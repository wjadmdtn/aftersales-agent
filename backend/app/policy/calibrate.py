"""T027: 阈值标定。

plan.md 要求：取使 FPR < 5% 且 recall@1 最大化的阈值，写入 calibration.md。

**FPR 的定义**必须说清楚，否则「FPR < 5%」是两个完全不同的数字：

  FPR = 负样本中被判为 HIT（会走到生成）的比例

不是「所有负样本里 top1 超过阈值的比例」——因为判为 AMBIGUOUS 的负样本
不会触发生成，它触发追问，用户被追问一句后要么补充、要么转人工，代价很小。
把 AMBIGUOUS 算进 FPR 会让阈值虚高，从而大量误杀正样本。

但 AMBIGUOUS 也不能白送：负样本被判 AMBIGUOUS 的用户体验是「客服没听懂装听懂」。
因此单独报 ambiguous_rate，作为阈值的第二道约束（默认 ≤ 15%）。

**margin 标定**：给定阈值后，正样本里 top1 与 top2 分差 < margin 的会被判 AMBIGUOUS。
margin 越大，越多正样本被追问。同时 margin 越大，误判 top1 的风险越低。
做法：在已选阈值下扫 margin，取「正样本 ambiguous_rate ≤ target」的最小 margin。

用法：
  python -m app.policy.calibrate --embedder=bge
  python -m app.policy.calibrate --embedder=bge --write-env
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.policy.embedder import BgeEmbedder, Embedder, StubEmbedder  # noqa: E402
from app.policy.models import RetrievalDecision  # noqa: E402
from app.policy.retriever import Retriever  # noqa: E402
from app.policy.seed import load_policies  # noqa: E402
from app.policy.store import InMemoryClauseStore  # noqa: E402

DEFAULT_SEED = ROOT / "backend" / "seed" / "policies" / "policies.json"
CALIBRATION_DATE = date(2026, 11, 10)
GRID = [round(x / 100, 2) for x in range(20, 96, 1)]  # 0.20 ~ 0.95
MARGIN_GRID = [round(x / 100, 2) for x in range(0, 26, 1)]  # 0.00 ~ 0.25

# 标定时的商品品类。留空会让 6 条限定品类的条款（换货/维修/以旧换新/完好标准/
# 不支持七天）全部不可见，召回率被系统性低估约 20 个点。
#
# 这里不传 None，而是**给每条样本一个合理品类**——标定的目标是测「正确品类下
# 阈值能否区分正负」，不是测「订单缺品类时系统行为」。品类缺失时的保守降级
# 由 test_policy_retriever 的单测覆盖，不该混进阈值分布。
DEFAULT_CALIBRATION_PRODUCT_TYPE = "apparel"

# 期望条款只在特定品类下可见的，必须用对应品类，否则该条正样本永远无法命中
CLAUSE_PRODUCT_TYPE = {
    "RETURN-GOOD-STANDARD-01": "apparel",
    "RETURN-EXCEPTION-01": "custom",
    "AFTERSALE-EXCHANGE-01": "apparel",
    "AFTERSALE-REPAIR-01": "electronics",
    "TRADE-IN-01": "electronics",
    "TRADE-IN-02": "electronics",
}


@dataclass
class Sample:
    query: str
    category: str
    expected_clause: str | None = None
    product_type: str | None = None
    top1: float | None = None
    top2: float | None = None
    top1_clause: str | None = None

    @property
    def gap(self) -> float:
        if self.top1 is None or self.top2 is None:
            return 1.0
        return self.top1 - self.top2


@dataclass
class Metrics:
    threshold: float
    margin: float
    need_answer: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    ambiguous_pos: int = 0
    ambiguous_neg: int = 0
    neg_correct_reject: int = 0
    wrong_top1: int = 0
    action_leak: int = 0
    action_ok: int = 0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        """已作答的正样本里答对的比例——刻意排除 ambiguous。

        被追问不是答错：用户补充一句就重试，体验损失远小于错答（纠纷）或漏答（转人工）。
        把 ambiguous 算进 recall 分母会让优化目标扭曲成「别追问」，
        从而选出「什么都答、答错的也多」的阈值。ambiguous 作为成本单独约束。
        """
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def answer_coverage(self) -> float:
        """正样本里最终真的给出了答案的比例（含答对和答错，不含追问）。

        与 recall 互补：recall 管「答了就要对」，coverage 管「别什么都不答」。
        单看 recall 会偏爱极高阈值（只答最有把握的几条），单看 coverage 会偏爱
        极低阈值（什么都答）。两者同时报告才能看出折中在哪。
        """
        d = self.need_answer
        return (self.tp + self.wrong_top1) / d if d else 0.0

    @property
    def fpr(self) -> float:
        """检索层该负责的负样本里，被判 HIT 的比例。

        分母只含检索层该拒绝的负样本（off_topic / near_miss）。
        `action` 类（我要退款/帮我改地址）由意图层拦下，走不到检索——把它们算进
        FPR 会逼着阈值往上调，用几十条正确样本去换一个本不归检索管的问题。
        意图层是否放行由 action_leak_rate 单独度量。

        只含负样本，不掺正样本的 fn —— 那是 recall 的事。
        """
        d = self.fp + self.ambiguous_neg + self.neg_correct_reject
        return self.fp / d if d else 0.0

    @property
    def neg_ambiguous_rate(self) -> float:
        """检索层负样本里被推去追问的比例。

        这是「用 margin 刷低 FPR」的代价闸门：调大 margin 能把误答变成追问，
        看起来 FPR 归零，实际是拿用户的无效追问换来的。
        """
        d = self.fp + self.ambiguous_neg + self.neg_correct_reject
        return self.ambiguous_neg / d if d else 0.0

    @property
    def action_leak_rate(self) -> float:
        """action 类负样本被判 HIT 的比例——意图层漏放，不是检索层的问题。"""
        d = self.action_leak + self.action_ok
        return self.action_leak / d if d else 0.0

    @property
    def ambiguous_rate_pos(self) -> float:
        d = self.tp + self.fn + self.ambiguous_pos
        return self.ambiguous_pos / d if d else 0.0


def _build_samples(
    *,
    retriever: Retriever,
    positives: list[tuple[str, str, str]],
    negatives: list[tuple[str, str, str]],
    top_k: int,
) -> tuple[list[Sample], list[Sample]]:
    pos_samples: list[Sample] = []
    for query, clause_id, _ in positives:
        product_type = CLAUSE_PRODUCT_TYPE.get(clause_id, DEFAULT_CALIBRATION_PRODUCT_TYPE)
        vec = retriever.embedder.embed([query])[0]
        hits = retriever.store.search(
            query_vector=vec,
            categories=None,
            product_type=product_type,
            order_date=CALIBRATION_DATE,
            limit=top_k,
        )
        s = Sample(
            query=query,
            category="positive",
            expected_clause=clause_id,
            product_type=product_type,
        )
        if hits:
            s.top1 = hits[0].similarity
            s.top1_clause = hits[0].clause.clause_id
            s.top2 = hits[1].similarity if len(hits) > 1 else None
        pos_samples.append(s)

    neg_samples: list[Sample] = []
    for query, category, _ in negatives:
        vec = retriever.embedder.embed([query])[0]
        hits = retriever.store.search(
            query_vector=vec,
            categories=None,
            product_type=DEFAULT_CALIBRATION_PRODUCT_TYPE,
            order_date=CALIBRATION_DATE,
            limit=top_k,
        )
        s = Sample(query=query, category=category)
        if hits:
            s.top1 = hits[0].similarity
            s.top1_clause = hits[0].clause.clause_id
            s.top2 = hits[1].similarity if len(hits) > 1 else None
        neg_samples.append(s)

    return pos_samples, neg_samples


def _classify(s: Sample, threshold: float, margin: float) -> RetrievalDecision:
    if s.top1 is None or s.top1 < threshold:
        return RetrievalDecision.MISS
    if s.top2 is not None and s.gap < margin:
        return RetrievalDecision.AMBIGUOUS
    return RetrievalDecision.HIT


def _evaluate(
    pos: list[Sample], neg: list[Sample], threshold: float, margin: float
) -> Metrics:
    m = Metrics(threshold=threshold, margin=margin)
    for s in pos:
        d = _classify(s, threshold, margin)
        m.need_answer += 1
        if d == RetrievalDecision.HIT:
            if s.top1_clause == s.expected_clause:
                m.tp += 1
            else:
                m.wrong_top1 += 1
                m.fn += 1  # 答错了也是没答对
        elif d == RetrievalDecision.AMBIGUOUS:
            m.ambiguous_pos += 1
        else:
            m.fn += 1
    for s in neg:
        d = _classify(s, threshold, margin)
        if s.category == "action":
            # 意图层该拦的，不计入检索层 FPR
            if d == RetrievalDecision.HIT:
                m.action_leak += 1
            else:
                m.action_ok += 1
            continue
        if d == RetrievalDecision.HIT:
            m.fp += 1
        elif d == RetrievalDecision.AMBIGUOUS:
            m.ambiguous_neg += 1
        else:
            m.neg_correct_reject += 1
    return m


def sweep(
    pos: list[Sample],
    neg: list[Sample],
    *,
    fpr_budget: float,
    amb_budget: float,
    neg_amb_budget: float = 0.30,
    fixed_margin: float | None = None,
) -> tuple[list[Metrics], Metrics | None]:
    """在 (threshold × margin) 上联合搜索。

    两者不能分开扫：margin 变大会把负样本从 HIT 推成 AMBIGUOUS，FPR 随之下降，
    但正样本也会被推成 AMBIGUOUS。固定一个再扫另一个，得到的是次优解。

    三道约束缺一不可：
      fpr_budget      负样本被误答的比例
      amb_budget      正样本被追问的比例（用户要多说一句）
      neg_amb_budget  负样本被追问的比例（无效追问——防「用 margin 刷 FPR」）
    """
    rows: list[Metrics] = []
    best: Metrics | None = None
    margins = [fixed_margin] if fixed_margin is not None else MARGIN_GRID
    for th in GRID:
        for mg in margins:
            m = _evaluate(pos, neg, th, mg)
            rows.append(m)
            if (
                m.fpr > fpr_budget
                or m.ambiguous_rate_pos > amb_budget
                or m.neg_ambiguous_rate > neg_amb_budget
            ):
                continue
            # 优化目标 = 答对数 tp（不是 recall —— 那是比率，会偏爱「只答极少数」的解）。
            # FPR 预算内，多答对一条就是实在的收益。
            # 同 tp 时偏好更低阈值：同样的收益，漏答更少，覆盖更广。
            if best is None or (m.tp, -m.threshold, m.margin) > (
                best.tp,
                -best.threshold,
                best.margin,
            ):
                best = m
    return rows, best


def sweep_margin(
    pos: list[Sample], neg: list[Sample], *, threshold: float, amb_budget: float
) -> tuple[list[Metrics], float]:
    rows = [_evaluate(pos, neg, threshold, mg) for mg in MARGIN_GRID]
    chosen = MARGIN_GRID[0]
    for m in rows:
        # margin 的代价是正样本被追问；约束满足时取能挡住最多歧义的那个
        if m.ambiguous_rate_pos <= amb_budget:
            chosen = m.margin
        else:
            break
    return rows, chosen


def _render_md(
    *,
    embedder_name: str,
    pos: list[Sample],
    neg: list[Sample],
    joint_rows: list[Metrics],
    final: Metrics,
    margin_rows: list[Metrics],
    fpr_budget: float,
    amb_budget: float,
    neg_amb_budget: float,
) -> str:
    def dist(vals: list[float | None]) -> str:
        xs = sorted(v for v in vals if v is not None)
        if not xs:
            return "n=0"
        k = lambda p: xs[min(len(xs) - 1, int(p * (len(xs) - 1)))]
        return (
            f"n={len(xs)}  min={xs[0]:.3f}  p10={k(.10):.3f}  p50={k(.50):.3f}  "
            f"p90={k(.90):.3f}  max={xs[-1]:.3f}"
        )

    retr_neg = [s for s in neg if s.category != "action"]
    action_neg = [s for s in neg if s.category == "action"]
    lines = [
        "# 政策 RAG 阈值标定",
        "",
        f"- Embedder：`{embedder_name}`",
        f"- 标定日期：{CALIBRATION_DATE}",
        f"- 正样本 {len(pos)} / 负样本 {len(neg)}"
        f"（检索层 {len(retr_neg)}，意图层 action {len(action_neg)}）",
        "- 标定订单日期：全部取 `2026-11-10`（所有版本在该日可见，只测语义不测版本）",
        f"- 商品品类：默认 `{DEFAULT_CALIBRATION_PRODUCT_TYPE}`；限定品类的条款用其对应品类",
        "  （品类为空的保守降级不在本标定范围内，见 test_policy_retriever）",
        "",
        "**口径说明**",
        "",
        "- `recall@1` = 已作答的正样本里答对的比例。**追问不算答错**，从分母剔除；"
        "追问是安全的中间态，单独作为成本约束。",
        "- `FPR` 的分母只含检索层该拒绝的负样本（off_topic / near_miss）。"
        "`action` 类（要退款/要投诉）由意图层拦下，不计入检索层 FPR，"
        "单独用 `action_leak_rate` 度量。",
        "",
        "## 相似度分布",
        "",
        "| 集合 | 分布 |",
        "|---|---|",
        f"| 正样本 top1 | {dist([s.top1 for s in pos])} |",
        f"| 正样本 top1-top2 分差 | {dist([s.gap for s in pos if s.top2 is not None])} |",
        f"| 检索层负样本 top1 | {dist([s.top1 for s in retr_neg])} |",
        f"| action 负样本 top1 | {dist([s.top1 for s in action_neg])} |",
        "",
        "## 联合搜索（threshold × margin）",
        "",
        f"约束：FPR ≤ {fpr_budget:.0%}，正样本追问率 ≤ {amb_budget:.0%}，"
        f"负样本追问率 ≤ {neg_amb_budget:.0%}（防用 margin 刷 FPR）。",
        "按「FPR 预算内答对数 tp 最大」取最优；同 tp 时取更低阈值与更小 margin。",
        "",
        "| threshold | margin | 答对/正样本 | coverage | recall | precision | FPR | 正追问率 | 负追问率 | action漏放 | 判定 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    # 先列全部可行解（按 tp 降序），再补几条被约束挡掉的对照，避免「看不到放弃了多少」
    feasible = sorted(
        [
            m
            for m in joint_rows
            if m.fpr <= fpr_budget
            and m.ambiguous_rate_pos <= amb_budget
            and m.neg_ambiguous_rate <= neg_amb_budget
        ],
        key=lambda m: (m.tp, -m.threshold, m.margin),
        reverse=True,
    )
    rejected = sorted(
        [
            m
            for m in joint_rows
            if m.fpr > fpr_budget
            or m.ambiguous_rate_pos > amb_budget
            or m.neg_ambiguous_rate > neg_amb_budget
        ],
        key=lambda m: m.tp,
        reverse=True,
    )[:10]
    for m in feasible + rejected:
        mark = " **← 选用**" if (m.threshold, m.margin) == (final.threshold, final.margin) else ""
        ok = (
            m.fpr <= fpr_budget
            and m.ambiguous_rate_pos <= amb_budget
            and m.neg_ambiguous_rate <= neg_amb_budget
        )
        n_pos = m.need_answer
        lines.append(
            f"| {m.threshold:.2f} | {m.margin:.2f} | {m.tp}/{n_pos} | "
            f"{m.answer_coverage:.1%} | {m.recall:.1%} | {m.precision:.1%} | "
            f"{m.fpr:.1%} | {m.ambiguous_rate_pos:.1%} | {m.neg_ambiguous_rate:.1%} | "
            f"{m.action_leak} | {'✓' if ok else '×'}{mark} |"
        )

    lines += [
        "",
        "## 选定阈值下的 margin 明细",
        "",
        f"在 threshold={final.threshold:.2f} 下扫 margin：",
        "",
        "| margin | 正样本追问率 | 负样本被追问 | FPR |",
        "|---|---|---|---|",
    ]
    for m in margin_rows:
        mark = " **←**" if m.margin == final.margin else ""
        lines.append(
            f"| {m.margin:.2f}{mark} | {m.ambiguous_rate_pos:.1%} | "
            f"{m.ambiguous_neg} | {m.fpr:.1%} |"
        )

    lines += [
        "",
        "## 结论",
        "",
        *_tradeoff_note(joint_rows, final, fpr_budget, amb_budget, neg_amb_budget),
        f"- `rag_similarity_threshold` = **{final.threshold:.2f}**",
        f"- `rag_ambiguity_margin` = **{final.margin:.2f}**",
        f"- 答对 = {final.tp}/{final.need_answer}"
        f"（coverage {final.answer_coverage:.1%}）",
        f"- recall@1 = {final.recall:.1%}（{final.tp}/{final.tp + final.fn}，已作答样本内）",
        f"- precision = {final.precision:.1%}",
        f"- FPR = {final.fpr:.1%}",
        f"- 正样本被追问率 = {final.ambiguous_rate_pos:.1%}",
        f"- 负样本被追问率 = {final.neg_ambiguous_rate:.1%}",
        f"- action 漏放 = {final.action_leak}"
        f"（交给意图层处理，阈值不为此继续上调）",
        "",
        "## 失败样本",
        "",
    ]

    misses = [s for s in pos if _classify(s, final.threshold, final.margin) == RetrievalDecision.MISS]
    if misses:
        lines.append("**正样本被判 miss（漏答）**")
        lines.append("")
        lines.append("| query | top1 | 期望 | 实际 top1 |")
        lines.append("|---|---|---|---|")
        for s in sorted(misses, key=lambda x: -(x.top1 or 0)):
            lines.append(
                f"| {s.query} | {s.top1 if s.top1 is None else f'{s.top1:.3f}'} | "
                f"{s.expected_clause} | {s.top1_clause or '-'} |"
            )
        lines.append("")

    false_hits = [
        s
        for s in neg
        if s.category != "action"
        and _classify(s, final.threshold, final.margin) == RetrievalDecision.HIT
    ]
    if false_hits:
        lines.append("**检索层负样本被判 hit（误答）**")
        lines.append("")
        lines.append("| query | 类别 | top1 | 命中条款 |")
        lines.append("|---|---|---|---|")
        for s in sorted(false_hits, key=lambda x: -(x.top1 or 0)):
            lines.append(
                f"| {s.query} | {s.category} | "
                f"{s.top1 if s.top1 is None else f'{s.top1:.3f}'} | {s.top1_clause or '-'} |"
            )
        lines.append("")

    action_hits = [
        s
        for s in neg
        if s.category == "action"
        and _classify(s, final.threshold, final.margin) == RetrievalDecision.HIT
    ]
    if action_hits:
        lines.append("**action 负样本被判 hit（意图层漏放，非检索层问题）**")
        lines.append("")
        lines.append("| query | top1 | 会命中条款 |")
        lines.append("|---|---|---|")
        for s in sorted(action_hits, key=lambda x: -(x.top1 or 0)):
            lines.append(
                f"| {s.query} | {s.top1 if s.top1 is None else f'{s.top1:.3f}'} | "
                f"{s.top1_clause or '-'} |"
            )
        lines.append("")

    ambig = [s for s in pos if _classify(s, final.threshold, final.margin) == RetrievalDecision.AMBIGUOUS]
    if ambig:
        lines.append(f"**正样本被判 ambiguous（追问）**：{len(ambig)} 条")
        lines.append("")

    return "\n".join(lines) + "\n"


def _tradeoff_note(
    rows: list[Metrics],
    final: Metrics,
    fpr_budget: float,
    amb_budget: float,
    neg_amb_budget: float,
) -> list[str]:
    """点明「为什么停在这个阈值」——只给数字，读者无法判断是否可接受。"""
    ok = [
        m
        for m in rows
        if m.fpr <= fpr_budget
        and m.ambiguous_rate_pos <= amb_budget
        and m.neg_ambiguous_rate <= neg_amb_budget
    ]
    if not ok:
        return []

    # 与「相邻的、更保守的可行点」比才有意义。
    # 直接挑 precision 最高的点会挑到「只答 1 条、100% 精确」的退化解，对比失真。
    more_conservative = [m for m in ok if m.threshold > final.threshold + 1e-9]
    lines: list[str] = []
    if more_conservative:
        nxt = min(more_conservative, key=lambda m: (m.threshold, m.margin))
        if nxt.fp < final.fp:
            lines += [
                f"**为什么不停在更保守的 {nxt.threshold:.2f}**："
                f"多挡掉 {final.fp - nxt.fp} 条误答的代价是少答对 {final.tp - nxt.tp} 条。"
                "漏答要转人工（有人工成本），误答只是争取到一次纠正机会——"
                "按这个比例该答，不该躲。",
                "",
            ]

    if final.margin == 0.0 and any(m.margin > 0 for m in ok):
        lines += [
            "**margin 为 0 不是没标定**：联合搜索显示任何非零 margin 都会减少答对数，"
            "本批样本的 top1-top2 分差分布不支持「靠追问消解歧义」这条路径。"
            "若政策条款增多或口径变细，歧义会变多，需要重标。",
            "",
        ]
    return lines


def _write_env_updates(path: Path, threshold: float, margin: float) -> None:
    text = path.read_text(encoding="utf-8")
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("MOCK_RAG_SIMILARITY_THRESHOLD="):
            out.append(f"MOCK_RAG_SIMILARITY_THRESHOLD={threshold:.2f}")
        elif line.startswith("MOCK_RAG_AMBIGUITY_MARGIN="):
            out.append(f"MOCK_RAG_AMBIGUITY_MARGIN={margin:.2f}")
        else:
            out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="标定政策 RAG 的相似度阈值与歧义 margin")
    ap.add_argument("--embedder", choices=["stub", "bge"], default="bge")
    ap.add_argument(
        "--bge-model",
        default="BAAI/bge-small-zh-v1.5",
        help="bge 模型名。换模型必须重标——不同模型的相似度尺度不可比",
    )
    ap.add_argument("--seed", default=str(DEFAULT_SEED))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--fpr-budget", type=float, default=0.05)
    ap.add_argument("--amb-budget", type=float, default=0.15)
    ap.add_argument(
        "--neg-amb-budget",
        type=float,
        default=0.30,
        help="负样本被追成 ambiguous 的比例上限，防止用大 margin 刷低 FPR",
    )
    ap.add_argument("--out", default=str(ROOT / "specs" / "005-policy-rag" / "calibration.md"))
    ap.add_argument(
        "--dump", default=None, help="把样本与相似度写成 JSON，便于换 embedder 后对照"
    )
    ap.add_argument(
        "--write-env",
        action="store_true",
        help="把标定结果写回 backend/.env.example（会覆盖阈值两行）",
    )
    args = ap.parse_args(argv)

    embedder: Embedder
    if args.embedder == "stub":
        print(
            "⚠ StubEmbedder 语义相似度是随机的，标定结果无意义。\n"
            "  仅用于验证标定脚本能跑通。",
            file=sys.stderr,
        )
        embedder = StubEmbedder(dim=1024)
    else:
        embedder = BgeEmbedder(model_name=args.bge_model)

    sys.path.insert(0, str(ROOT / "eval"))
    from eval.seeds.calibration_seed import NEGATIVES, POSITIVES  # noqa: E402

    clauses = load_policies(args.seed)
    store = InMemoryClauseStore(clauses, embedder)
    retriever = Retriever(
        store=store,
        embedder=embedder,
        similarity_threshold=0.0,
        ambiguity_margin=0.0,
        top_k=args.top_k,
    )

    print(f"加载 {len(clauses)} 条条款；正样本 {len(POSITIVES)} / 负样本 {len(NEGATIVES)}")
    pos, neg = _build_samples(
        retriever=retriever, positives=POSITIVES, negatives=NEGATIVES, top_k=args.top_k
    )

    joint_rows, best = sweep(
        pos,
        neg,
        fpr_budget=args.fpr_budget,
        amb_budget=args.amb_budget,
        neg_amb_budget=args.neg_amb_budget,
    )
    if best is None:
        print("✗ 没有任何阈值能同时满足 FPR 与追问率约束，请放宽预算或检查样本", file=sys.stderr)
        return 1

    margin_rows, _ = sweep_margin(pos, neg, threshold=best.threshold, amb_budget=args.amb_budget)
    final = _evaluate(pos, neg, best.threshold, best.margin)

    print(
        f"\n结论  threshold={final.threshold:.2f}  margin={final.margin:.2f}\n"
        f"      recall@1={final.recall:.1%}  precision={final.precision:.1%}  "
        f"FPR={final.fpr:.1%}  追问率={final.ambiguous_rate_pos:.1%}"
    )

    md = _render_md(
        embedder_name=args.bge_model if args.embedder == "bge" else "stub",
        pos=pos,
        neg=neg,
        joint_rows=joint_rows,
        final=final,
        margin_rows=margin_rows,
        fpr_budget=args.fpr_budget,
        amb_budget=args.amb_budget,
        neg_amb_budget=args.neg_amb_budget,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"报告 → {out}")

    if args.dump:
        payload = {
            "embedder": args.embedder,
            "threshold": final.threshold,
            "margin": final.margin,
            "positives": [
                {"query": s.query, "expected": s.expected_clause, "top1": s.top1,
                 "top2": s.top2, "top1_clause": s.top1_clause}
                for s in pos
            ],
            "negatives": [
                {"query": s.query, "category": s.category, "top1": s.top1,
                 "top2": s.top2, "top1_clause": s.top1_clause}
                for s in neg
            ],
        }
        Path(args.dump).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"样本 dump → {args.dump}")

    if args.write_env:
        env = ROOT / "backend" / ".env.example"
        _write_env_updates(env, final.threshold, final.margin)
        print(f"已写回 {env}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""检索增强方案对比实验。

对比四种检索方式在同一批标注样本上的 recall@1：
  A 基线      ：用户原问题直接向量检索
  B 查询改写  ：LLM 把口语问题改写成"文档式"检索词
  C HyDE      ：LLM 先写一个假设答案，用假设答案的向量检索
  D B+C       ：两者都做，取融合结果

判据：只看 recall@1 与 precision。不做阈值调优——用固定阈值 0.64，
避免"调阈值换召回"掩盖方案本身的差异。

注意：全量跑在 CPU 上很慢（每条样本一次 embedding + 可能的 LLM 调用），
默认只跑前 N 条，可用 --limit 调整。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.policy.embedder import BgeEmbedder, cosine  # noqa: E402
from app.policy.seed import load_policies  # noqa: E402
from app.policy.store import InMemoryClauseStore  # noqa: E402

QUERY_REWRITE_PROMPT = """把用户的口语问题改写成适合检索政策条款的查询词。
只输出关键词与短语，用空格分隔，不要解释，不要输出标点。
要求：补全可能相关的正式说法（如"吊牌剪了"→"商标标识 摘除 完好 标准"）。

用户问题：{q}
检索词："""

HYDE_PROMPT = """你是电商售后政策文档的编写者。请针对下面的问题，写一段政策文档风格的答案
（50 字以内，书面语气，不口语化）。不需要保证准确，只要求"像一份真实政策文件里的句子"。

问题：{q}
文档："""


class Recorder:
    def __init__(self, threshold: float):
        self.threshold = threshold
        self.rows: list[dict] = []

    def add(self, mode: str, q: str, expected: str | None, got: str | None, score: float):
        self.rows.append(
            {"mode": mode, "q": q, "expected": expected, "got": got, "score": score}
        )

    def summary(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for mode in {r["mode"] for r in self.rows}:
            rows = [r for r in self.rows if r["mode"] == mode]
            pos = [r for r in rows if r["expected"]]
            neg = [r for r in rows if not r["expected"]]
            hit = [r for r in pos if r["got"] and r["got"] == r["expected"] and r["score"] >= self.threshold]
            answered = [r for r in pos if r["got"] and r["score"] >= self.threshold]
            wrong = [r for r in answered if r["got"] != r["expected"]]
            neg_hit = [r for r in neg if r["got"] and r["score"] >= self.threshold]
            out[mode] = {
                "n_pos": len(pos),
                "n_neg": len(neg),
                "recall@1": len(hit) / len(pos) if pos else 0.0,
                "coverage": len(answered) / len(pos) if pos else 0.0,
                "wrong": len(wrong),
                "precision": len(hit) / len(answered) if answered else 0.0,
                "neg_false_hit": len(neg_hit) / len(neg) if neg else 0.0,
            }
        return out


async def llm_call(factory, prompt: str) -> str:
    try:
        return await factory(prompt)
    except Exception as e:
        print(f"  [LLM 失败] {type(e).__name__}: {str(e)[:80]}")
        return ""


CLASSIFY_PROMPT = """判断用户问题的意图，只输出一个词。
可选意图：policy（咨询政策规则，如"退款多久到账""吊牌剪了能退吗"）、
refund（要求退款）、logistics（查物流）、change_address（改地址）、
query_order（查订单）、complaint（投诉）、manual（要人工）、general（其他）。
用户问题：{q}
意图："""


async def is_policy_intent(llm, q: str) -> bool:
    """用真实模型做闸门，模拟 system 里"只有 policy 意图才进检索"的分支。

    注意：这是近似——生产里用的是 IntentClassifier + 结构化输出，
    这里只用它判断"该问题会不会走到政策检索"，不参与任何判定。
    """
    out = await llm_call(llm, CLASSIFY_PROMPT.format(q=q))
    return out.strip().lower().startswith("policy")


def make_llm():
    """复用项目的 openai_compat 客户端，走 .env 里的真实模型。"""
    import httpx

    from app.config import settings

    async def call(prompt: str) -> str:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                json={
                    "model": settings.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()

    return call


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--threshold", type=float, default=0.64)
    ap.add_argument("--skip-llm", action="store_true", help="只跑基线，不调模型")
    ap.add_argument("--out", default="/tmp/retrieval-compare.json")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT / "eval"))
    from eval.seeds.calibration_seed import NEGATIVES, POSITIVES

    clauses = load_policies(str(ROOT / "backend" / "seed" / "policies" / "policies.json"))
    emb = BgeEmbedder()
    store = InMemoryClauseStore(clauses, emb)

    pos = POSITIVES[: args.limit]
    neg = NEGATIVES[: max(10, args.limit // 2)]
    print(f"条款 {len(clauses)} 条；正样本 {len(pos)}，负样本 {len(neg)}")

    llm = make_llm()
    rec = Recorder(threshold=args.threshold)

    def best(vec: list[float], product_type: str | None = "apparel"):
        cands = store.search(
            query_vector=vec, categories=None, product_type=product_type,
            order_date=date(2026, 11, 10), limit=5,
        )
        if not cands:
            return None, 0.0
        return cands[0].clause.clause_id, cands[0].similarity

    for idx, (q, expected, _tag) in enumerate(pos, 1):
        print(f"[{idx}/{len(pos)}] {q}")
        v = emb.embed([q])[0]
        gid, sc = best(v)
        rec.add("A_baseline", q, expected, gid, sc)

        if args.skip_llm:
            continue

        # B 查询改写
        rewritten = await llm_call(llm, QUERY_REWRITE_PROMPT.format(q=q))
        if rewritten:
            gid, sc = best(emb.embed([rewritten])[0])
            rec.add("B_rewrite", q, expected, gid, sc)
            print(f"    改写 → {rewritten[:40]}  {gid} {sc:.3f}")

        # C HyDE
        hypo = await llm_call(llm, HYDE_PROMPT.format(q=q))
        if hypo:
            gid, sc = best(emb.embed([hypo])[0])
            rec.add("C_hyde", q, expected, gid, sc)
            print(f"    HyDE → {hypo[:40]}  {gid} {sc:.3f}")

            # D 两者取最优（按余弦分高者）
            if rewritten:
                va, vb = emb.embed([rewritten])[0], emb.embed([hypo])[0]
                ga, sa = best(va)
                gb, sb = best(vb)
                gid, sc = (ga, sa) if sa >= sb else (gb, sb)
                rec.add("D_hyde+rewrite", q, expected, gid, sc)

    for q, _tag, _ in neg:
        v = emb.embed([q])[0]
        gid, sc = best(v)
        rec.add("A_baseline", q, None, gid, sc)
        if not args.skip_llm:
            # 负例只有在被意图层判为 policy 时才会走到检索——
            # 直接对负例跑 HyDE 是在测一条系统根本走不到的路径。
            # 这里用轻量意图判定模拟闸门：明显无关的问句不进入改写/HyDE。
            if not await is_policy_intent(llm, q):
                continue
            hypo = await llm_call(llm, HYDE_PROMPT.format(q=q))
            if hypo:
                gid, sc = best(emb.embed([hypo])[0])
                rec.add("C_hyde", q, None, gid, sc)

    summary = rec.summary()
    print("\n" + "=" * 68)
    print(f"{'方案':<18}{'recall@1':>10}{'precision':>11}{'覆盖':>9}{'负例误判':>10}")
    for mode in sorted(summary):
        s = summary[mode]
        print(
            f"{mode:<18}{s['recall@1']:>9.1%}{s['precision']:>10.1%}"
            f"{s['coverage']:>9.1%}{s['neg_false_hit']:>10.1%}"
        )
    Path(args.out).write_text(
        json.dumps({"summary": summary, "rows": rec.rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n明细 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

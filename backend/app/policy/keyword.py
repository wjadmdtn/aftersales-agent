"""关键词召回 + RRF 融合。

为什么需要：纯向量召回对短问句、实体词（吊牌 / 送修 / 发票抬头）区分度不够，
标定显示 16/99 正例因分数不够被漏掉，而它们恰恰是关键词最擅长的。

为什么不用 jieba：宪法要求能不用三方库就不用。中文政策条款的强信号是
2~4 字实体词（退款、吊牌、运费、发票），用 n-gram 直接切即可，不需要分词。

为什么用 RRF 而不是加权求和：余弦相似度（0~1）与关键词命中数（0~N）量纲不同，
加权必须调权重且对分布敏感；RRF 只用排名，无需标定。
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from app.policy.models import PolicyClause, RetrievedClause

# 只保留中英文与数字，标点与空白作分隔
_TOKEN_RE = re.compile(r"[一-鿿]+|[a-zA-Z0-9]+")
MIN_N, MAX_N = 2, 4

# 高频虚词短语，出现即说明匹配无意义
_STOP_PHRASES = frozenset(
    {
        "的是", "是什", "什么", "么样", "怎么", "可以", "能不", "不能", "是否",
        "如何", "为什", "哪些", "多少", "请问", "一下", "我们", "你们", "这个",
        "那个", "的话", "时候", "还有", "以及", "或者", "但是", "因为", "所以",
    }
)


def _ngrams(text: str) -> set[str]:
    out: set[str] = set()
    for token in _TOKEN_RE.findall(text):
        if token.isascii():
            if len(token) >= MIN_N:
                out.add(token.lower())
            continue
        for n in range(MIN_N, MAX_N + 1):
            for i in range(0, len(token) - n + 1):
                gram = token[i : i + n]
                if gram in _STOP_PHRASES:
                    continue
                out.add(gram)
    return out


def build_terms(clause: PolicyClause) -> set[str]:
    """标题权重更高，但在 n-gram 集合里体现为"两条路径都命中"。

    这里只返回全集；加权在 KeywordIndex 里通过 term → clause 的计数完成。
    """
    return _ngrams(clause.title) | _ngrams(clause.body)


class KeywordIndex:
    """倒排索引：term → {clause_id: 命中次数}。

    条款数量在百级，内存足够；接 pgvector 时可迁到 Postgres 全文索引。

    同一 clause_id 可能有多个版本（v3 现在生效、v4 下月生效）。
    索引按 clause_id 建，因此**必须在检索时按生效期取版本**——
    否则会返回未来版本的正文，用户被按尚未生效的规则答复。
    """

    def __init__(self, clauses: list[PolicyClause]):
        # clause_id → [按版本排序的多个 clause]，检索时再挑生效的那条
        self._by_id: dict[str, list[PolicyClause]] = defaultdict(list)
        for c in clauses:
            self._by_id[c.clause_id].append(c)
        for group in self._by_id.values():
            group.sort(key=lambda c: c.version)

        self._title_terms: dict[str, set[str]] = {}
        self._body_terms: dict[str, set[str]] = {}
        self._exc_terms: dict[str, set[str]] = {}
        self._posting: dict[str, set[str]] = defaultdict(set)

        for c in clauses:
            tt = _ngrams(c.title)
            bt = _ngrams(c.body)
            # 例外条款必须进索引：像"吊牌被摘剪"这种**具体阻断条件**只写在 exceptions 里，
            # embedding 刻意排除了它（语义偏离正文），关键词是唯一能命中的路径。
            et = set()
            for exc in c.exceptions:
                et |= _ngrams(exc)
            et -= tt | bt
            # 多版本条款取词集**并集**：任一版本命中的词都算召回候选，
            # 具体用哪个版本在 search 里按 order_date 决定。
            self._title_terms.setdefault(c.clause_id, set()).update(tt)
            self._body_terms.setdefault(c.clause_id, set()).update(bt)
            self._exc_terms.setdefault(c.clause_id, set()).update(et)
            for term in tt | bt | et:
                self._posting[term].add(c.clause_id)

    def _pick_version(self, cid: str, order_date: date | None) -> PolicyClause | None:
        """取在 order_date 当天生效的版本；多个生效时取最高版本。"""
        group = self._by_id.get(cid)
        if not group:
            return None
        if order_date is None:
            return group[-1]
        live = [c for c in group if c.effective_at(order_date)]
        return live[-1] if live else None

    def search(
        self,
        query: str,
        *,
        limit: int,
        candidates: set[str] | None = None,
        order_date: date | None = None,
    ) -> list[RetrievedClause]:
        """candidates 用于复用向量检索的可见性过滤（品类/生效期）。

        order_date 用于在同一 clause_id 的多个版本里挑出当前生效的那条。
        不传则退化为取最高版本（仅脚本/标定场景使用）。
        """
        q_terms = _ngrams(query)
        if not q_terms:
            return []

        scores: dict[str, float] = defaultdict(float)
        for term in q_terms:
            ids = self._posting.get(term)
            if not ids:
                continue
            # 越长的 n-gram 越具体，权重越高
            weight = len(term) / MAX_N
            for cid in ids:
                if candidates is not None and cid not in candidates:
                    continue
                if term in self._title_terms[cid]:
                    scores[cid] += 2.0 * weight
                elif term in self._body_terms[cid]:
                    scores[cid] += 1.0 * weight
                elif term in self._exc_terms[cid]:
                    scores[cid] += 1.5 * weight

        if not scores:
            return []

        best = max(scores.values())
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

        out: list[RetrievedClause] = []
        for cid, score in ranked:
            # 命中词的条款若在 order_date 当天没有任何生效版本，跳过——
            # 不能拿未来版本的正文回答当前问题。
            clause = self._pick_version(cid, order_date)
            if clause is None:
                continue
            out.append(
                RetrievedClause(
                    clause=clause,
                    # 归一化到 0~1 只为可比较展示；阈值判断一律用 vector_similarity
                    similarity=score / best,
                    vector_similarity=None,
                    source="keyword",
                )
            )
            if len(out) >= limit:
                break
        return out


def rrf_fuse(
    rankings: list[list[RetrievedClause]],
    *,
    k: int = 60,
    order_date: date | None = None,
) -> list[RetrievedClause]:
    """Reciprocal Rank Fusion。

    融合后：similarity 保持向量余弦（阈值判断用），fusion_score 用于排序。
    只被关键词召回的条目 vector_similarity=None，retriever 不会把它当成高分。

    order_date：同一 clause_id 的两条路可能召回不同版本（向量路拿 v3、
    关键词路拿 v4）。不做版本裁决会让未来版本胜出——用户被按尚未生效的
    规则答复。因此多路命中同 id 时，取 order_date 当天生效的那条版本。
    """
    fused_score: dict[str, float] = defaultdict(float)
    best: dict[str, RetrievedClause] = {}

    def _version_ok(item: RetrievedClause) -> bool:
        return order_date is None or item.clause.effective_at(order_date)

    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            cid = item.clause.clause_id
            fused_score[cid] += 1.0 / (k + rank)
            prev = best.get(cid)
            if prev is None:
                best[cid] = item
                continue

            prev_ok, item_ok = _version_ok(prev), _version_ok(item)
            # 生效期优先于向量分：v3 生效 / v4 未生效时，无论分数谁高都留 v3
            if item_ok and not prev_ok:
                best[cid] = item
                continue
            if prev_ok and not item_ok:
                continue

            # 同版本（都在 order_date 生效，或都未生效）：保留向量分（若有），补齐来源标记
            if prev.vector_similarity is None and item.vector_similarity is not None:
                best[cid] = item
            elif prev.vector_similarity is not None and item.vector_similarity is not None:
                # 两路都有向量分时取更高者，语义上等价于"更确定"
                if item.vector_similarity > prev.vector_similarity:
                    best[cid] = item

    ordered = sorted(best.values(), key=lambda r: fused_score[r.clause.clause_id], reverse=True)
    out: list[RetrievedClause] = []
    for r in ordered:
        src = "both" if (r.vector_similarity is not None and _has_keyword(rankings, r.clause.clause_id)) else r.source
        out.append(
            r.model_copy(
                update={"fusion_score": fused_score[r.clause.clause_id], "source": src}
            )
        )
    return out


def _has_keyword(rankings: list[list[RetrievedClause]], cid: str) -> bool:
    return any(item.clause.clause_id == cid and item.source == "keyword" for r in rankings for item in r)

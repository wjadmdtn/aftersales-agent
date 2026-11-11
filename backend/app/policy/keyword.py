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
    """

    def __init__(self, clauses: list[PolicyClause]):
        self.clauses = {c.clause_id: c for c in clauses}
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
            self._title_terms[c.clause_id] = tt
            self._body_terms[c.clause_id] = bt
            self._exc_terms[c.clause_id] = et
            for term in tt | bt | et:
                self._posting[term].add(c.clause_id)

    def search(self, query: str, *, limit: int, candidates: set[str] | None = None) -> list[RetrievedClause]:
        """candidates 用于复用向量检索的可见性过滤（品类/生效期）。"""
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
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [
            RetrievedClause(
                clause=self.clauses[cid],
                # 归一化到 0~1 只为可比较展示；阈值判断一律用 vector_similarity
                similarity=score / best,
                vector_similarity=None,
                source="keyword",
            )
            for cid, score in ranked
        ]


def rrf_fuse(
    rankings: list[list[RetrievedClause]],
    *,
    k: int = 60,
) -> list[RetrievedClause]:
    """Reciprocal Rank Fusion。

    融合后：similarity 保持向量余弦（阈值判断用），fusion_score 用于排序。
    只被关键词召回的条目 vector_similarity=None，retriever 不会把它当成高分。
    """
    fused_score: dict[str, float] = defaultdict(float)
    best: dict[str, RetrievedClause] = {}

    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            cid = item.clause.clause_id
            fused_score[cid] += 1.0 / (k + rank)
            prev = best.get(cid)
            if prev is None:
                best[cid] = item
            else:
                # 同一条款被多路召回：保留向量分（若有），补齐来源标记
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

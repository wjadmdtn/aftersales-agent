"""关键词召回（BR25 风格）。

纯向量召回对"吊牌剪了还能退吗""怎么送修"这类**实体词**问句分数偏低：
短问句语义被稀释，而实体词恰恰是最强信号。这里补一路关键词召回，
与向量结果做 RRF 融合。

刻意约束：
1. 不做中文分词依赖（宪法：能不用三方库就不用）——用条款标题/正文里的
   2~4 字 n-gram 匹配
2. 关键词只做**召回**，不做判定：最终 decision 仍由 retriever 的阈值/分差决定
3. 只增不减：融合后的候选集是向量结果的超集
"""
from __future__ import annotations

from datetime import date

import pytest

from app.policy.keyword import KeywordIndex, build_terms, rrf_fuse
from app.policy.models import PolicyClause, RetrievedClause


def _clause(cid: str, title: str, body: str = "", category: str = "refund") -> PolicyClause:
    return PolicyClause(
        clause_id=cid,
        policy_id=cid.split("-")[0].lower(),
        version=1,
        effective_from=date(2020, 1, 1),
        category=category,
        title=title,
        body=body,
    )


def test_terms_extracted_from_title_and_body():
    c = _clause("R-01", "退款到账时间", "退款审核通过后原路退回")
    terms = build_terms(c)
    assert "退款" in terms
    assert "到账" in terms


def test_terms_include_two_to_four_char_ngrams():
    c = _clause("R-01", "吊牌剪了还能退吗")
    terms = build_terms(c)
    # 2 字与 3 字都应存在
    assert "吊牌" in terms
    assert "吊牌剪" in terms or "牌剪了" in terms


def test_keyword_search_finds_exact_entity():
    idx = KeywordIndex([
        _clause("R-01", "退款到账时间"),
        _clause("R-02", "吊牌与完好标准", "吊牌剪断视为影响二次销售"),
        _clause("R-03", "发票开具"),
    ])
    hits = idx.search("吊牌剪了还能退吗", limit=5)
    assert hits[0].clause.clause_id == "R-02"


def test_keyword_search_returns_empty_for_unrelated():
    idx = KeywordIndex([_clause("R-01", "退款到账时间")])
    assert idx.search("今天天气怎么样", limit=5) == []


def test_keyword_similarity_is_normalized():
    idx = KeywordIndex([_clause("R-02", "吊牌与完好标准", "吊牌剪断视为影响二次销售")])
    hits = idx.search("吊牌剪了还能退吗", limit=5)
    assert 0.0 <= hits[0].similarity <= 1.0


def test_rrf_fuse_merges_and_dedupes():
    a = RetrievedClause(clause=_clause("A", "a"), similarity=0.8)
    b = RetrievedClause(clause=_clause("B", "b"), similarity=0.7)
    c = RetrievedClause(clause=_clause("C", "c"), similarity=0.6)
    fused = rrf_fuse([[a, b], [c, a]], k=60)
    ids = [r.clause.clause_id for r in fused]
    assert set(ids) == {"A", "B", "C"}
    # A 在两路都出现，应排最前
    assert ids[0] == "A"


def test_rrf_preserves_ordering_within_single_list():
    a = RetrievedClause(clause=_clause("A", "a"), similarity=0.9)
    b = RetrievedClause(clause=_clause("B", "b"), similarity=0.5)
    fused = rrf_fuse([[a, b]], k=60)
    assert [r.clause.clause_id for r in fused] == ["A", "B"]


def test_rrf_keeps_vector_similarity_for_thresholding():
    """融合排序用 RRF 分，但阈值判断必须用原始向量相似度。

    两件事不能混：RRF 的数字只用于排序，量纲与余弦完全不同。
    """
    a = RetrievedClause(clause=_clause("A", "a"), similarity=0.8)
    fused = rrf_fuse([[a]], k=60)
    assert fused[0].similarity == 0.8
    assert fused[0].fusion_score > 0


def test_keyword_only_hit_has_no_vector_similarity():
    """只被关键词召回的条款没有向量分，阈值判断时不得当成高分。"""
    kw = RetrievedClause(clause=_clause("K", "k"), similarity=0.9)
    fused = rrf_fuse([[], [kw]], k=60)
    assert fused[0].vector_similarity is None

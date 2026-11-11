"""T010/T011: 检索层测试。

用 StubEmbedder 时相似度是伪随机的，因此涉及"具体哪条排第一"的用例
注入手工构造的 store，只验证判定逻辑；版本/品类过滤用真实种子 + 内存 store 验证。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.policy.embedder import StubEmbedder
from app.policy.models import PolicyClause, RetrievedClause, RetrievalDecision
from app.policy.retriever import Retriever, _resolve_date
from app.policy.seed import load_policies
from app.policy.store import InMemoryClauseStore, _visible

SEED = "seed/policies/policies.json"


@pytest.fixture(scope="module")
def clauses():
    return load_policies(SEED)


@pytest.fixture(scope="module")
def store(clauses):
    return InMemoryClauseStore(clauses, StubEmbedder(64))


def _retriever(store, threshold=0.0, margin=0.0, k=5):
    return Retriever(
        store=store,
        embedder=StubEmbedder(64),
        similarity_threshold=threshold,
        ambiguity_margin=margin,
        top_k=k,
    )


# ---------- 版本过滤 ----------

def test_version_filter_old_order_never_sees_v4(store):
    """直接查存储层：版本过滤发生在召回前，不依赖相似度排序。"""
    res = store.search(
        query_vector=[0.0] * 64,
        categories=None,
        product_type=None,
        order_date=date(2026, 11, 6),
        limit=999,
    )
    versions = {c.clause.version for c in res if c.clause.clause_id == "RETURN-SEVENDAY-02"}
    assert versions == {3}, "旧订单不得召回未来生效的 v4"


def test_version_filter_new_order_sees_v4(store):
    res = store.search(
        query_vector=[0.0] * 64,
        categories=None,
        product_type=None,
        order_date=date(2026, 12, 5),
        limit=999,
    )
    versions = {c.clause.version for c in res if c.clause.clause_id == "RETURN-SEVENDAY-02"}
    assert versions == {4}


def test_on_boundary_day_uses_new_version(clauses):
    v3 = next(c for c in clauses if c.clause_id == "RETURN-SEVENDAY-02" and c.version == 3)
    v4 = next(c for c in clauses if c.clause_id == "RETURN-SEVENDAY-02" and c.version == 4)
    assert v3.effective_to == date(2026, 11, 30)
    assert v4.effective_from == date(2026, 12, 1)
    assert v3.effective_at(date(2026, 11, 30)) is True
    assert v4.effective_at(date(2026, 12, 1)) is True
    assert v3.effective_at(date(2026, 12, 1)) is False


# ---------- 品类过滤 ----------

def test_fresh_order_does_not_see_apparel_only_clause():
    c = PolicyClause(
        clause_id="APPAREL-ONLY", policy_id="A", version=1,
        effective_from=date(2026, 1, 1), category="return", title="t", body="b",
        applies_to={"product_types": ["apparel"]},
    )
    assert _visible(c, product_type="fresh", order_date=date(2026, 11, 1)) is False
    assert _visible(c, product_type="apparel", order_date=date(2026, 11, 1)) is True


def test_wildcard_clause_visible_to_all():
    c = PolicyClause(
        clause_id="ALL", policy_id="A", version=1,
        effective_from=date(2026, 1, 1), category="refund", title="t", body="b",
        applies_to={"product_types": ["*"]},
    )
    for pt in (None, "apparel", "fresh"):
        assert _visible(c, product_type=pt, order_date=date(2026, 11, 1)) is True


def test_missing_product_type_excludes_typed_clause():
    c = PolicyClause(
        clause_id="CUSTOM-ONLY", policy_id="A", version=1,
        effective_from=date(2026, 1, 1), category="return", title="t", body="b",
        applies_to={"product_types": ["custom"]},
    )
    assert _visible(c, product_type=None, order_date=date(2026, 11, 1)) is False


# ---------- 判定 ----------

class _FixedStore:
    def __init__(self, sims):
        self.sims = sims

    def search(self, **kw):
        return [
            RetrievedClause(
                clause=PolicyClause(
                    clause_id=f"C{i}", policy_id="P", version=1,
                    effective_from=date(2026, 1, 1), category="refund",
                    title="t", body="b",
                ),
                similarity=s,
            )
            for i, s in enumerate(self.sims)
        ]


def _run(sims, threshold, margin):
    r = Retriever(
        store=_FixedStore(sims),
        embedder=StubEmbedder(64),
        similarity_threshold=threshold,
        ambiguity_margin=margin,
        top_k=5,
    )
    return r.retrieve(query="q", order_created_at=datetime(2026, 11, 6, tzinfo=timezone.utc))


def test_empty_candidates_is_miss():
    assert _run([], 0.6, 0.05).decision is RetrievalDecision.MISS


def test_below_threshold_is_miss():
    res = _run([0.55, 0.4], 0.6, 0.05)
    assert res.decision is RetrievalDecision.MISS
    assert "阈值" in res.reason


def test_close_scores_is_ambiguous():
    res = _run([0.91, 0.89], 0.6, 0.05)
    assert res.decision is RetrievalDecision.AMBIGUOUS


def test_clear_winner_is_hit():
    res = _run([0.91, 0.70], 0.6, 0.05)
    assert res.decision is RetrievalDecision.HIT


def test_single_candidate_above_threshold_is_hit():
    assert _run([0.95], 0.6, 0.05).decision is RetrievalDecision.HIT


def test_threshold_boundary_is_inclusive():
    assert _run([0.6, 0.4], 0.6, 0.05).decision is RetrievalDecision.HIT


class _KwBoostIndex:
    """把指定条款强行顶到关键词召回首位，制造融合重排。"""

    def __init__(self, clause):
        self.clause = clause

    def search(self, query, *, limit, candidates=None):
        return [
            RetrievedClause(
                clause=self.clause, similarity=1.0, vector_similarity=None, source="keyword"
            )
        ]


class _TwoClauseStore:
    """低分条款 + 高分条款的固定 store，clause_id 与关键词召回共用。"""

    def __init__(self, low, high):
        self.low, self.high = low, high

    def search(self, **kw):
        return [
            RetrievedClause(clause=self.low, similarity=0.615),
            RetrievedClause(clause=self.high, similarity=0.659),
        ]

    def visible_ids(self, **kw):
        return {self.low.clause_id, self.high.clause_id}


def test_fused_order_does_not_hide_high_vector_candidate():
    """置信判定必须看候选集里的**向量最高分**，不是融合排序的第一位。

    真实案例：「能合并订单发货吗」召回「修改收货地址」vec 0.659（过阈值），
    但关键词融合把 vec 0.615 的条款顶到第一位，早退检查直接判 miss——
    本该进 rerank、被拒答转人工的知识缺口案例被吞成了检索层 miss。
    """
    low = PolicyClause(
        clause_id="C-LOW", policy_id="P", version=1,
        effective_from=date(2026, 1, 1), category="refund", title="t", body="b",
    )
    high = PolicyClause(
        clause_id="C-HIGH", policy_id="P", version=1,
        effective_from=date(2026, 1, 1), category="refund", title="t2", body="b2",
    )
    r = Retriever(
        store=_TwoClauseStore(low, high),
        embedder=StubEmbedder(64),
        similarity_threshold=0.64,
        ambiguity_margin=0.0,
        top_k=5,
        keyword_index=_KwBoostIndex(low),
    )
    res = r.retrieve(query="能合并订单发货吗", order_created_at=datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert res.decision is RetrievalDecision.HIT, res.reason
    # 可信度最高的排第一，交给 rerank 复核
    assert res.candidates[0].clause.clause_id == "C-HIGH"


# ---------- 日期回落 ----------

def test_missing_order_date_defaults_to_today():
    d, used = _resolve_date(None)
    assert used is True
    assert d == datetime.now(timezone.utc).date()


def test_result_flags_default_date(store):
    res = _retriever(store, threshold=-1.0).retrieve(query="退款多久到账", order_created_at=None)
    assert res.used_default_date is True


# ---------- 存储一致性 ----------

def test_inmemory_store_respects_limit(store):
    r = _retriever(store, threshold=-1.0, k=3)
    res = r.retrieve(query="退款", order_created_at=datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert len(res.candidates) <= 3


def test_inmemory_store_category_filter(store):
    r = _retriever(store, threshold=-1.0, k=50)
    res = r.retrieve(
        query="退款",
        order_created_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
        categories=["logistics"],
    )
    assert res.candidates
    assert all(c.clause.category == "logistics" for c in res.candidates)

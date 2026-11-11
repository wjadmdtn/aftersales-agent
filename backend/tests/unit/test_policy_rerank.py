"""Rerank：检索后、生成前，用模型判断条款是否真能回答该问题。

为什么必须有这一步：
向量检索是双塔（问题与条款各自独立编码），只能判断"字面上像不像"，
判断不了"这条规则能不能回答这个问题"。实测出现过：
  问「能合并订单发货吗」→ 召回「修改收货地址」0.742 → 直接生成错误答案

rerank 把（问题 + 条款）一起给模型，是交叉编码，才判断得了语义相关性。

三条硬约束：
1. rerank 只能**降级**：模型判不相关 → 该条款被剔除；判相关也不能把
   miss 升级成 hit（与 answerer 的门禁同一原则）
2. rerank 失败（超时/报错）不得丢答案：退回按原向量分排序，记日志
3. 全部条款被判不相关 → miss，交回澄清链路，绝不硬答
"""
from __future__ import annotations

from datetime import date

import pytest

from app.agent.llm.base import LLMError
from app.policy.models import (
    PolicyClause,
    RetrievalDecision,
    RetrievalResult,
    RetrievedClause,
)
from app.policy.rerank import LLMReranker


def _clause(cid: str, title: str, body: str = "") -> PolicyClause:
    return PolicyClause(
        clause_id=cid,
        policy_id=cid.split("-")[0].lower(),
        version=1,
        effective_from=date(2020, 1, 1),
        category="refund",
        title=title,
        body=body,
    )


def _retrieval(*pairs) -> RetrievalResult:
    return RetrievalResult(
        query="q",
        order_date=date(2026, 11, 10),
        candidates=[RetrievedClause(clause=c, similarity=s) for c, s in pairs],
        decision=RetrievalDecision.HIT,
        reason="top1 ok",
    )


class ScriptedRerankLLM:
    """按 clause_id 返回预设相关性分数。"""

    def __init__(self, scores: dict[str, float], *, fail: str | None = None):
        self.scores = scores
        self.fail = fail
        self.calls = 0

    async def rerank_policy(self, *, query: str, clauses: list[PolicyClause]) -> dict[str, float]:
        self.calls += 1
        if self.fail == "error":
            raise LLMError("boom")
        if self.fail == "timeout":
            raise TimeoutError()
        return {c.clause_id: self.scores.get(c.clause_id, 0.0) for c in clauses}


async def test_irrelevant_clause_is_dropped():
    """核心用例：「合并订单发货」不该被「修改收货地址」回答。"""
    llm = ScriptedRerankLLM({"ORDER-ADDRESS-01": 0.05})
    rr = LLMReranker(llm, keep_threshold=0.5, top_n=2)

    r = await rr.apply(
        query="能合并订单发货吗",
        retrieval=_retrieval((_clause("ORDER-ADDRESS-01", "修改收货地址"), 0.742)),
    )
    assert r.decision is RetrievalDecision.MISS
    assert r.candidates == []
    assert "rerank" in r.reason


async def test_all_rejected_sets_explicit_flag():
    """主循环靠 rerank_rejected 区分「知识缺口」和「表述没对上」：
    前者直接转人工，后者走澄清。必须显式字段，不许靠 reason 字符串猜。"""
    llm = ScriptedRerankLLM({"ORDER-ADDRESS-01": 0.05})
    rr = LLMReranker(llm, keep_threshold=0.5, top_n=2)

    r = await rr.apply(
        query="能合并订单发货吗",
        retrieval=_retrieval((_clause("ORDER-ADDRESS-01", "修改收货地址"), 0.742)),
    )
    assert r.rerank_rejected is True


async def test_kept_candidates_do_not_set_rejected_flag():
    llm = ScriptedRerankLLM({"A-01": 0.9})
    rr = LLMReranker(llm, keep_threshold=0.5, top_n=2)

    r = await rr.apply(
        query="退款多久到账",
        retrieval=_retrieval((_clause("A-01", "退款到账时间"), 0.8)),
    )
    assert r.rerank_rejected is False


async def test_relevant_clause_is_kept_with_new_order():
    llm = ScriptedRerankLLM({"A-01": 0.9, "B-01": 0.3})
    rr = LLMReranker(llm, keep_threshold=0.5, top_n=2)

    r = await rr.apply(
        query="退款多久到账",
        retrieval=_retrieval((_clause("B-01", "退款金额"), 0.80), (_clause("A-01", "退款到账时间"), 0.70)),
    )
    assert r.decision is RetrievalDecision.HIT
    assert [c.clause.clause_id for c in r.candidates] == ["A-01"]
    # 排序应按 rerank 分，而不是原始向量分
    assert r.candidates[0].similarity == 0.9


async def test_rerank_cannot_upgrade_miss():
    """rerank 只能降级，不能把 miss 变成 hit。"""
    llm = ScriptedRerankLLM({"A-01": 0.99})
    rr = LLMReranker(llm, keep_threshold=0.5, top_n=2)

    miss = RetrievalResult(
        query="q", order_date=date(2026, 11, 10),
        candidates=[RetrievedClause(clause=_clause("A-01", "x"), similarity=0.3)],
        decision=RetrievalDecision.MISS, reason="below threshold",
    )
    r = await rr.apply(query="q", retrieval=miss)
    assert r.decision is RetrievalDecision.MISS
    assert llm.calls == 0, "已是 miss 不必再花一次调用"


async def test_llm_failure_falls_back_to_vector_order():
    llm = ScriptedRerankLLM({}, fail="error")
    rr = LLMReranker(llm, keep_threshold=0.5, top_n=2)

    r = await rr.apply(
        query="q", retrieval=_retrieval((_clause("A-01", "a"), 0.8), (_clause("B-01", "b"), 0.7))
    )
    assert r.decision is RetrievalDecision.HIT
    assert [c.clause.clause_id for c in r.candidates] == ["A-01", "B-01"]


async def test_timeout_falls_back_to_vector_order():
    llm = ScriptedRerankLLM({}, fail="timeout")
    rr = LLMReranker(llm, keep_threshold=0.5, top_n=2)
    r = await rr.apply(query="q", retrieval=_retrieval((_clause("A-01", "a"), 0.8)))
    assert r.decision is RetrievalDecision.HIT


async def test_top_n_limits_kept_clauses():
    llm = ScriptedRerankLLM({"A-01": 0.9, "B-01": 0.8, "C-01": 0.7})
    rr = LLMReranker(llm, keep_threshold=0.5, top_n=2)
    r = await rr.apply(
        query="q",
        retrieval=_retrieval(
            (_clause("A-01", "a"), 0.8), (_clause("B-01", "b"), 0.7), (_clause("C-01", "c"), 0.6)
        ),
    )
    assert len(r.candidates) == 2
    assert [c.clause.clause_id for c in r.candidates] == ["A-01", "B-01"]


async def test_ambiguous_stays_ambiguous_when_candidates_remain():
    llm = ScriptedRerankLLM({"A-01": 0.9, "B-01": 0.85})
    rr = LLMReranker(llm, keep_threshold=0.5, top_n=2)
    amb = RetrievalResult(
        query="q", order_date=date(2026, 11, 10),
        candidates=[
            RetrievedClause(clause=_clause("A-01", "a"), similarity=0.70),
            RetrievedClause(clause=_clause("B-01", "b"), similarity=0.699),
        ],
        decision=RetrievalDecision.AMBIGUOUS, reason="gap small",
    )
    r = await rr.apply(query="q", retrieval=amb)
    assert r.decision is RetrievalDecision.AMBIGUOUS


async def test_all_rejected_becomes_miss_not_ambiguous():
    llm = ScriptedRerankLLM({"A-01": 0.1, "B-01": 0.2})
    rr = LLMReranker(llm, keep_threshold=0.5, top_n=2)
    amb = RetrievalResult(
        query="q", order_date=date(2026, 11, 10),
        candidates=[
            RetrievedClause(clause=_clause("A-01", "a"), similarity=0.70),
            RetrievedClause(clause=_clause("B-01", "b"), similarity=0.699),
        ],
        decision=RetrievalDecision.AMBIGUOUS, reason="gap small",
    )
    r = await rr.apply(query="q", retrieval=amb)
    assert r.decision is RetrievalDecision.MISS


async def test_disabled_reranker_is_passthrough():
    from app.policy.rerank import NoopReranker

    orig = _retrieval((_clause("A-01", "a"), 0.8))
    r = await NoopReranker().apply(query="q", retrieval=orig)
    assert [c.clause.clause_id for c in r.candidates] == ["A-01"]

"""T017: 生成层门禁。

门禁是确定性检查，不是再问一次模型。凡不通过一律作废回答并降级为 miss。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.agent.llm.stub import StubLLMClient
from app.policy.answerer import PolicyAnswerer
from app.policy.models import (
    PolicyAnswer,
    PolicyClause,
    RetrievalDecision,
    RetrievalResult,
    RetrievedClause,
)


def _clause(cid="REFUND-COMMON-01", exceptions=()):
    return PolicyClause(
        clause_id=cid,
        policy_id="REFUND-COMMON",
        version=3,
        effective_from=date(2026, 1, 1),
        category="refund",
        title="退款到账时间",
        body="银行卡 3~7 个工作日",
        exceptions=list(exceptions),
    )


def _retrieval(decision=RetrievalDecision.HIT, *clauses):
    clauses = clauses or (_clause(),)
    return RetrievalResult(
        query="退款多久到账",
        order_date=date(2026, 11, 6),
        candidates=[RetrievedClause(clause=c, similarity=0.9 - 0.1 * i) for i, c in enumerate(clauses)],
        decision=decision,
    )


def _answerer(resp: PolicyAnswer | None = None, fail=False):
    llm = StubLLMClient(fail_mode="error" if fail else None, policy_default=resp)
    return PolicyAnswerer(llm)


async def test_valid_answer_passes():
    ans = PolicyAnswer(
        decision=RetrievalDecision.HIT,
        answer="银行卡 3~7 个工作日",
        cited_clause_ids=["REFUND-COMMON-01"],
    )
    out = await _answerer(ans).answer(user_input="q", retrieval=_retrieval())
    assert out.decision is RetrievalDecision.HIT
    assert out.rejection is None


async def test_hallucinated_clause_id_rejected():
    ans = PolicyAnswer(
        decision=RetrievalDecision.HIT,
        answer="根据政策……",
        cited_clause_ids=["RETURN-SEVENDAY-01"],  # 未召回
    )
    out = await _answerer(ans).answer(user_input="q", retrieval=_retrieval())
    assert out.decision is RetrievalDecision.MISS
    assert "未召回" in (out.rejection or "")


async def test_no_citation_rejected():
    ans = PolicyAnswer(decision=RetrievalDecision.HIT, answer="随便说说", cited_clause_ids=[])
    out = await _answerer(ans).answer(user_input="q", retrieval=_retrieval())
    assert out.decision is RetrievalDecision.MISS
    assert "未引用" in (out.rejection or "")


async def test_empty_answer_rejected():
    ans = PolicyAnswer(
        decision=RetrievalDecision.HIT, answer="   ", cited_clause_ids=["REFUND-COMMON-01"]
    )
    out = await _answerer(ans).answer(user_input="q", retrieval=_retrieval())
    assert out.decision is RetrievalDecision.MISS


async def test_exceptions_not_handled_rejected():
    c = _clause(exceptions=["遇法定节假日顺延"])
    ans = PolicyAnswer(
        decision=RetrievalDecision.HIT,
        answer="3~7 个工作日",
        cited_clause_ids=["REFUND-COMMON-01"],
        handled_exceptions=[],
    )
    out = await _answerer(ans).answer(user_input="q", retrieval=_retrieval(RetrievalDecision.HIT, c))
    assert out.decision is RetrievalDecision.MISS
    assert "例外" in (out.rejection or "")


async def test_exceptions_handled_passes():
    c = _clause(exceptions=["遇法定节假日顺延"])
    ans = PolicyAnswer(
        decision=RetrievalDecision.HIT,
        answer="3~7 个工作日，遇节假日顺延",
        cited_clause_ids=["REFUND-COMMON-01"],
        handled_exceptions=["遇法定节假日顺延"],
    )
    out = await _answerer(ans).answer(user_input="q", retrieval=_retrieval(RetrievalDecision.HIT, c))
    assert out.decision is RetrievalDecision.HIT


async def test_exception_clause_not_cited_does_not_force_exceptions():
    """召回了带例外的条款但没引用它，不要求 handled_exceptions。"""
    cited = _clause("A-01")
    other = _clause("B-01", exceptions=["例外x"])
    ans = PolicyAnswer(
        decision=RetrievalDecision.HIT, answer="ok", cited_clause_ids=["A-01"]
    )
    out = await _answerer(ans).answer(
        user_input="q", retrieval=_retrieval(RetrievalDecision.HIT, cited, other)
    )
    assert out.decision is RetrievalDecision.HIT


async def test_model_cannot_upgrade_miss():
    r = RetrievalResult(query="q", order_date=date(2026, 11, 6), decision=RetrievalDecision.MISS)
    ans = PolicyAnswer(decision=RetrievalDecision.HIT, answer="x", cited_clause_ids=["A"])
    out = await _answerer(ans).answer(user_input="q", retrieval=r)
    assert out.decision is RetrievalDecision.MISS


async def test_model_cannot_upgrade_ambiguous_to_hit():
    r = _retrieval(RetrievalDecision.AMBIGUOUS)
    ans = PolicyAnswer(
        decision=RetrievalDecision.HIT, answer="x", cited_clause_ids=["REFUND-COMMON-01"]
    )
    out = await _answerer(ans).answer(user_input="q", retrieval=r)
    assert out.decision is RetrievalDecision.AMBIGUOUS


async def test_model_downgrade_respected():
    ans = PolicyAnswer(decision=RetrievalDecision.MISS)
    out = await _answerer(ans).answer(user_input="q", retrieval=_retrieval())
    assert out.decision is RetrievalDecision.MISS


async def test_llm_error_never_produces_answer():
    out = await _answerer(fail=True).answer(user_input="q", retrieval=_retrieval())
    assert out.decision is RetrievalDecision.MISS
    assert out.answer == ""
    assert out.rejection == "llm_error"


async def test_stub_default_cites_top1_and_handles_exceptions():
    c = _clause(exceptions=["遇法定节假日顺延"])
    r = _retrieval(RetrievalDecision.HIT, c)
    out = await PolicyAnswerer(StubLLMClient()).answer(user_input="q", retrieval=r)
    assert out.cited_clause_ids == ["REFUND-COMMON-01"]
    assert out.handled_exceptions == ["遇法定节假日顺延"]

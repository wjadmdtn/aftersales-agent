"""PolicyService 编排：改写优先、miss 回退原文、rerank/生成用原话。

回退不花 LLM 调用——检索是本地向量计算，改写没救回来时用原话再查一次，
防止"改写丢细节反而变差"（实测「能合并订单发货吗」改写后 0.633 检索层
miss，原文 0.659 能进 rerank 被拒转人工）。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.policy.models import (
    PolicyAnswer,
    PolicyClause,
    RetrievalDecision,
    RetrievalResult,
    RetrievedClause,
)
from app.services.policy import PolicyService


def _clause(cid: str) -> PolicyClause:
    return PolicyClause(
        clause_id=cid,
        policy_id="P",
        version=1,
        effective_from=date(2020, 1, 1),
        category="refund",
        title="t",
        body="b",
    )


def _result(query: str, decision: RetrievalDecision) -> RetrievalResult:
    cands = []
    if decision is not RetrievalDecision.MISS:
        cands = [RetrievedClause(clause=_clause("C-1"), similarity=0.9)]
    return RetrievalResult(
        query=query,
        order_date=date(2026, 11, 10),
        candidates=cands,
        decision=decision,
        reason="scripted",
    )


class _ScriptedRetriever:
    """按 query 返回预设结果，记录调用顺序。"""

    def __init__(self, results: dict[str, RetrievalResult]):
        self.results = results
        self.queries: list[str] = []

    def retrieve(self, *, query, order_created_at, product_type=None, categories=None):
        self.queries.append(query)
        return self.results.get(
            query, _result(query, RetrievalDecision.MISS)
        )


class _NoopLLM:
    """answerer 的桩：引用 top1 条款直接答。"""

    async def answer_policy(self, *, user_input, retrieval):
        if not retrieval.candidates:
            return PolicyAnswer(decision=RetrievalDecision.MISS)
        top = retrieval.candidates[0].clause
        return PolicyAnswer(
            decision=retrieval.decision,
            answer=f"根据《{top.title}》",
            cited_clause_ids=[top.clause_id],
        )


def _service(retriever) -> PolicyService:
    return PolicyService(
        retriever=retriever, llm=_NoopLLM(), timeout_ms=5000
    )


async def test_rewrite_miss_falls_back_to_raw_query():
    """改写检索 miss 时必须用原话再查一次——不许让改写的方差吃掉基线能力。"""
    retriever = _ScriptedRetriever({
        "合并订单 发货 规则": _result("合并订单 发货 规则", RetrievalDecision.MISS),
        "能合并订单发货吗": _result("能合并订单发货吗", RetrievalDecision.HIT),
    })
    svc = _service(retriever)

    answer, retrieval = await svc.answer(
        question="能合并订单发货吗",
        order_created_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
        search_query="合并订单 发货 规则",
    )
    assert retriever.queries == ["合并订单 发货 规则", "能合并订单发货吗"]
    assert answer.decision is RetrievalDecision.HIT
    assert retrieval.query == "能合并订单发货吗"


async def test_rewrite_hit_skips_fallback():
    """改写直接命中就不再多查一次。"""
    retriever = _ScriptedRetriever({
        "吊牌 剪除 退货条件": _result("吊牌 剪除 退货条件", RetrievalDecision.HIT),
    })
    svc = _service(retriever)

    await svc.answer(
        question="吊牌被剪了还能退吗",
        order_created_at=None,
        search_query="吊牌 剪除 退货条件",
    )
    assert retriever.queries == ["吊牌 剪除 退货条件"]


async def test_no_search_query_single_retrieval():
    retriever = _ScriptedRetriever({})
    svc = _service(retriever)

    await svc.answer(
        question="退款多久到账", order_created_at=None
    )
    assert retriever.queries == ["退款多久到账"]


async def test_blank_search_query_treated_as_missing():
    """空串改写等于没改写，直接用原话查一次。"""
    retriever = _ScriptedRetriever({})
    svc = _service(retriever)

    await svc.answer(
        question="退款多久到账", order_created_at=None, search_query="   "
    )
    assert retriever.queries == ["退款多久到账"]

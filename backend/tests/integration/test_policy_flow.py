"""T023: 政策检索接入 Agent 主循环。

重点验证门控：只有 GENERAL 分支检索；读/写/投诉/人工一律不检索；
降级期不检索不生成。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.agent.intent import IntentClassifier
from app.agent.llm.stub import StubLLMClient
from app.agent.loop import AgentLoop
from app.models.agent import Intent, IntentMode, IntentResult
from app.policy.embedder import StubEmbedder
from app.policy.models import RetrievalDecision
from app.policy.retriever import Retriever
from app.policy.seed import load_policies
from app.policy.store import InMemoryClauseStore
from app.services.policy import PolicyService

from tests.conftest_agent import InProcessMockClient


class _SpyPolicy:
    """记录被调用次数；模拟命中。"""

    def __init__(self):
        self.calls = 0
        self.last_kwargs = {}

    async def answer(
        self, *, question, order_created_at, product_type=None, trace_id=None,
        search_query=None,
    ):
        self.calls += 1
        self.last_kwargs = {
            "question": question,
            "order_created_at": order_created_at,
            "product_type": product_type,
            "search_query": search_query,
        }
        from app.policy.models import PolicyAnswer

        return (
            PolicyAnswer(
                decision=RetrievalDecision.HIT,
                answer="银行卡 3~7 个工作日",
                cited_clause_ids=["REFUND-COMMON-01"],
            ),
            None,
        )


class _AmbiguousPolicy:
    def __init__(self):
        self.calls = 0

    async def answer(self, **kw):
        from app.policy.models import PolicyAnswer

        self.calls += 1
        return (
            PolicyAnswer(
                decision=RetrievalDecision.AMBIGUOUS,
                ambiguity_question="您是想退还是想换？",
            ),
            None,
        )


class _MissPolicy:
    def __init__(self):
        self.calls = 0

    async def answer(self, **kw):
        from app.policy.models import PolicyAnswer

        self.calls += 1
        return PolicyAnswer(decision=RetrievalDecision.MISS), None


class _RerankRejectedPolicy:
    """rerank 全剔除后的 miss：检索层进过 rerank，但模型判定没有条款能回答。
    这是「知识缺口」，与「检索层分数不够」是两种不同的 miss。"""

    def __init__(self):
        self.calls = 0

    async def answer(self, **kw):
        from datetime import date

        from app.policy.models import PolicyAnswer, RetrievalResult

        self.calls += 1
        return (
            PolicyAnswer(decision=RetrievalDecision.MISS, rejection="rerank 判为不相关"),
            RetrievalResult(
                query=kw["question"],
                order_date=date(2026, 11, 10),
                candidates=[],
                decision=RetrievalDecision.MISS,
                reason="rerank 判为不相关（阈值 0.5）：['RETURN-SEVENDAY-01']",
                rerank_rejected=True,
            ),
        )


class _LowScoreMissPolicy:
    """检索层就没过阈值：没进 rerank，可能只是表述问题，不算知识缺口。"""

    def __init__(self):
        self.calls = 0

    async def answer(self, **kw):
        from datetime import date

        from app.policy.models import (
            PolicyAnswer,
            PolicyClause,
            RetrievalResult,
            RetrievedClause,
        )

        self.calls += 1
        clause = PolicyClause(
            clause_id="RETURN-SEVENDAY-01",
            policy_id="return",
            version=1,
            effective_from=date(2020, 1, 1),
            category="return",
            title="七天无理由退货",
            body="签收后7天内可无理由退货。",
        )
        return (
            PolicyAnswer(decision=RetrievalDecision.MISS),
            RetrievalResult(
                query=kw["question"],
                order_date=date(2026, 11, 10),
                candidates=[RetrievedClause(clause=clause, similarity=0.51)],
                decision=RetrievalDecision.MISS,
                reason="top1 0.51 < threshold 0.64",
            ),
        )


@pytest.fixture
async def make_loop():
    import fakeredis.aioredis

    import app.api.routes as mock_routes
    from app.agent.session import SessionStore
    from app.main import create_app

    mock_routes.reset_store()
    mock_app = create_app()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sessions = SessionStore(redis, ttl_seconds=3600)
    tools = InProcessMockClient(mock_app)

    def _build(policy=None, *, responses=None, fail_mode=None, with_coordinator=False):
        stub = StubLLMClient(responses or {}, fail_mode=fail_mode)
        classifier = IntentClassifier(stub, keyword_map={})
        coordinator = None
        if with_coordinator:
            from app.handoff.coordinator import HandoffCoordinator
            from app.handoff.queue import HandoffQueue

            coordinator = HandoffCoordinator(HandoffQueue(redis))
        loop = AgentLoop(
            classifier=classifier,
            sessions=sessions,
            tools=tools,
            policy=policy,
            coordinator=coordinator,
        )
        return loop, stub

    yield _build
    await redis.aclose()


ALL_INTENTS = [
    ("我要退款", Intent.REFUND, IntentMode.EXECUTE),
    ("这个能退款吗", Intent.REFUND, IntentMode.INQUIRY),
    ("快递到哪了", Intent.LOGISTICS, IntentMode.QUERY),
    ("订单状态", Intent.QUERY_ORDER, IntentMode.QUERY),
    ("我要投诉", Intent.COMPLAINT, IntentMode.EXECUTE),
    ("转人工", Intent.MANUAL, IntentMode.EXECUTE),
]


@pytest.mark.parametrize("msg,intent,mode", ALL_INTENTS)
async def test_non_general_intents_never_retrieve(make_loop, msg, intent, mode):
    spy = _SpyPolicy()
    responses = {msg: IntentResult(intent=intent, mode=mode, confidence=0.9)}
    loop, _ = make_loop(spy, responses=responses)

    await loop.handle(
        session_id="s1", user_id="u1", message=msg, trace_id="t1", order_id="ORD1002"
    )
    assert spy.calls == 0, f"{intent} 不应触发政策检索"


async def test_general_intent_retrieves_and_answers(make_loop):
    spy = _SpyPolicy()
    responses = {"退款多久到账": IntentResult(intent=Intent.POLICY, confidence=0.8)}
    loop, _ = make_loop(spy, responses=responses)

    reply = await loop.handle(
        session_id="s1", user_id="u1", message="退款多久到账", trace_id="t1", order_id="ORD1002"
    )
    assert spy.calls == 1
    assert reply.state == "policy_answered"
    assert "3~7 个工作日" in reply.reply


async def test_general_passes_order_created_at_and_product_type(make_loop):
    spy = _SpyPolicy()
    responses = {"运费谁出": IntentResult(intent=Intent.POLICY, confidence=0.8)}
    loop, _ = make_loop(spy, responses=responses)

    await loop.handle(
        session_id="s1", user_id="u1", message="运费谁出", trace_id="t1", order_id="ORD1004"
    )
    assert spy.last_kwargs["product_type"] == "fresh"
    created = spy.last_kwargs["order_created_at"]
    assert created is not None
    assert created.date().isoformat() == "2026-10-26"


# ---------- 查询改写（意图识别顺带产出，不加调用次数） ----------

async def test_search_query_reaches_retrieval(make_loop):
    """意图识别顺带产出的改写 query 必须传到检索层；
    生成与 rerank 仍以用户原话为准（question 不变）。"""
    spy = _SpyPolicy()
    responses = {
        "吊牌剪了还能退吗": IntentResult(
            intent=Intent.POLICY,
            confidence=0.8,
            search_query="吊牌 商标标识 摘除 退货条件",
        )
    }
    loop, _ = make_loop(spy, responses=responses)

    await loop.handle(
        session_id="s1", user_id="u1", message="吊牌剪了还能退吗", trace_id="t1", order_id="ORD1002"
    )
    assert spy.calls == 1
    assert spy.last_kwargs["search_query"] == "吊牌 商标标识 摘除 退货条件"
    assert spy.last_kwargs["question"] == "吊牌剪了还能退吗"


async def test_missing_search_query_uses_raw_message(make_loop):
    """模型没给改写（非 policy 意图/字段缺失）时用原文检索，不猜。"""
    spy = _SpyPolicy()
    responses = {
        "退款多久到账": IntentResult(intent=Intent.POLICY, confidence=0.8)
    }
    loop, _ = make_loop(spy, responses=responses)

    await loop.handle(
        session_id="s1", user_id="u1", message="退款多久到账", trace_id="t1", order_id="ORD1002"
    )
    assert spy.last_kwargs["search_query"] is None
    assert spy.last_kwargs["question"] == "退款多久到账"


async def test_miss_falls_back_to_clarify(make_loop):
    miss = _MissPolicy()
    responses = {"你们老板是谁": IntentResult(intent=Intent.POLICY, confidence=0.8)}
    loop, _ = make_loop(miss, responses=responses)

    reply = await loop.handle(
        session_id="s1", user_id="u1", message="你们老板是谁", trace_id="t1", order_id="ORD1002"
    )
    assert miss.calls == 1
    assert reply.state == "clarifying"


async def test_ambiguous_asks_instead_of_answering(make_loop):
    amb = _AmbiguousPolicy()
    responses = {"能换吗": IntentResult(intent=Intent.POLICY, confidence=0.8)}
    loop, _ = make_loop(amb, responses=responses)

    reply = await loop.handle(
        session_id="s1", user_id="u1", message="能换吗", trace_id="t1", order_id="ORD1002"
    )
    assert reply.state == "clarifying"
    assert "退" in reply.reply


# ---------- rerank 判定不相关 → 直接转人工（不走追问） ----------

RERANK_MSG = "吊牌被剪了还能退吗"


async def test_rerank_rejected_goes_straight_to_handoff(make_loop):
    """rerank 判定没有条款能回答 = 知识缺口。换个说法问也不会有答案，
    追问无意义，直接转人工——不得先走 2 轮澄清。"""
    policy = _RerankRejectedPolicy()
    loop, _ = make_loop(policy, responses={
        RERANK_MSG: IntentResult(intent=Intent.POLICY, confidence=0.8)
    })

    reply = await loop.handle(
        session_id="s-b1", user_id="u1", message=RERANK_MSG, trace_id="t1", order_id="ORD1002"
    )
    assert policy.calls == 1
    assert reply.state == "handoff_pending"
    assert reply.intent is Intent.POLICY


async def test_rerank_rejected_handoff_carries_boundary_trigger(make_loop):
    """转接单必须标 BOUNDARY，人工端看到的是真实原因（rerank 判定不相关），
    而不是兜底的「用户主动要求」。"""
    from app.models.handoff import TriggerType

    from app.config import settings

    policy = _RerankRejectedPolicy()
    loop, _ = make_loop(policy, responses={
        RERANK_MSG: IntentResult(intent=Intent.POLICY, confidence=0.8)
    }, with_coordinator=True)

    reply = await loop.handle(
        session_id="s-b2", user_id="u1", message=RERANK_MSG, trace_id="t1", order_id="ORD1002"
    )
    assert reply.state == "handoff_queued"
    assert reply.reply == settings.handoff_scripts["boundary"]

    ctx = await loop.coordinator.queue.get_pack("s-b2")
    assert ctx is not None
    assert ctx.trigger_type is TriggerType.BOUNDARY
    assert "rerank" in ctx.trigger_detail


async def test_low_score_miss_still_clarifies(make_loop):
    """检索层分数不够（没进 rerank）≠ 知识缺口：可能只是表述问题，
    仍走澄清，不直接转人工。"""
    policy = _LowScoreMissPolicy()
    loop, _ = make_loop(policy, responses={
        RERANK_MSG: IntentResult(intent=Intent.POLICY, confidence=0.8)
    })

    reply = await loop.handle(
        session_id="s-b3", user_id="u1", message=RERANK_MSG, trace_id="t1", order_id="ORD1002"
    )
    assert policy.calls == 1
    assert reply.state == "clarifying"


async def test_low_score_miss_handoff_after_clarify_limit_uses_boundary(make_loop):
    """澄清 2 轮耗尽后转人工时，本轮检索分数低 → 触发 BOUNDARY，
    而不是兜底的 USER_REQUEST 话术。"""
    from app.models.handoff import TriggerType

    from app.config import settings

    policy = _LowScoreMissPolicy()
    msgs = [RERANK_MSG, "就是吊牌的事", "你们到底怎么处理"]
    loop, _ = make_loop(policy, responses={
        m: IntentResult(intent=Intent.POLICY, confidence=0.8) for m in msgs
    }, with_coordinator=True)

    for i, m in enumerate(msgs):
        reply = await loop.handle(
            session_id="s-b4", user_id="u1", message=m, trace_id=f"t{i}", order_id="ORD1002"
        )

    assert reply.state == "handoff_queued"
    assert reply.reply == settings.handoff_scripts["boundary"]
    ctx = await loop.coordinator.queue.get_pack("s-b4")
    assert ctx.trigger_type is TriggerType.BOUNDARY


async def test_degraded_never_retrieves(make_loop):
    spy = _SpyPolicy()
    loop, _ = make_loop(spy, responses={}, fail_mode="timeout")

    reply = await loop.handle(
        session_id="s1", user_id="u1", message="退款多久到账", trace_id="t1", order_id="ORD1002"
    )
    assert spy.calls == 0, "降级期不得触发政策检索"
    assert reply.state.startswith("degraded")


async def test_no_policy_service_still_works(make_loop):
    responses = {"退款多久到账": IntentResult(intent=Intent.POLICY, confidence=0.8)}
    loop, _ = make_loop(None, responses=responses)

    reply = await loop.handle(
        session_id="s1", user_id="u1", message="退款多久到账", trace_id="t1", order_id="ORD1002"
    )
    assert reply.state == "clarifying"


async def test_session_records_cited_clauses(make_loop):
    spy = _SpyPolicy()
    responses = {"退款多久到账": IntentResult(intent=Intent.POLICY, confidence=0.8)}
    loop, _ = make_loop(spy, responses=responses)

    await loop.handle(
        session_id="s1", user_id="u1", message="退款多久到账", trace_id="t1", order_id="ORD1002"
    )
    state = await loop.sessions.get_or_create("s1", user_id="u1")
    assert state.last_cited_clause_ids == ["REFUND-COMMON-01"]
    assert state.policy_turns == 1


# ---------- 真链路：retriever + answerer 全串起来 ----------

async def test_full_chain_with_real_retriever(make_loop):
    clauses = load_policies("seed/policies/policies.json")
    store = InMemoryClauseStore(clauses, StubEmbedder(64))
    retriever = Retriever(
        store=store,
        embedder=StubEmbedder(64),
        similarity_threshold=-1.0,  # 伪向量，阈值放开，只验证链路能通
        ambiguity_margin=0.0,
        top_k=3,
    )
    stub = StubLLMClient()
    service = PolicyService(retriever=retriever, llm=stub, timeout_ms=800)

    loop, _ = make_loop(service, responses={
        "退款多久到账": IntentResult(intent=Intent.POLICY, confidence=0.8)
    })
    reply = await loop.handle(
        session_id="s1", user_id="u1", message="退款多久到账", trace_id="t1", order_id="ORD1002"
    )
    assert stub.policy_call_count == 1
    assert reply.state == "policy_answered"


async def test_timeout_degrades_to_miss(make_loop):
    clauses = load_policies("seed/policies/policies.json")
    store = InMemoryClauseStore(clauses, StubEmbedder(64))
    retriever = Retriever(
        store=store, embedder=StubEmbedder(64),
        similarity_threshold=-1.0, ambiguity_margin=0.0, top_k=3,
    )
    stub = StubLLMClient(delay_ms=200)
    service = PolicyService(retriever=retriever, llm=stub, timeout_ms=20)

    loop, _ = make_loop(service, responses={
        "退款多久到账": IntentResult(intent=Intent.POLICY, confidence=0.8)
    })
    reply = await loop.handle(
        session_id="s1", user_id="u1", message="退款多久到账", trace_id="t1", order_id="ORD1002"
    )
    assert reply.state == "clarifying", "检索超时应回落澄清，而不是报错"


async def test_business_intent_does_not_retrieve(make_loop):
    """业务动作不该触发政策检索——既慢又答非所问。"""
    policy = _MissPolicy()
    responses = {"我要退款": IntentResult(intent=Intent.REFUND, confidence=0.95)}
    loop, _ = make_loop(policy, responses=responses)

    reply = await loop.handle(
        session_id="s1", user_id="u1", message="我要退款", trace_id="t1", order_id="ORD1002"
    )
    assert policy.calls == 0, "业务意图不得触发政策检索"
    assert reply.state == "awaiting_confirmation"


async def test_clarify_does_not_retrieve(make_loop):
    """说不清的问题走澄清，不查政策。"""
    policy = _MissPolicy()
    responses = {"这个东西不好用": IntentResult(intent=Intent.GENERAL, confidence=0.7)}
    loop, _ = make_loop(policy, responses=responses)

    reply = await loop.handle(
        session_id="s1", user_id="u1", message="这个东西不好用", trace_id="t1", order_id="ORD1002"
    )
    assert policy.calls == 0
    assert reply.state == "clarifying"

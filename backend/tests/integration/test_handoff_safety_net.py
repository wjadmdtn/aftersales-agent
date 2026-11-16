"""转人工"安全网"：AI 自己判断靠不住了要主动接人，不能只等用户点按钮。

此前 triggers.py 里 check_system_error / check_risk 的入参在生产链路上
永远是默认值（loop 只传了 user_message 和 rag_top_similarity），
这几条规则只活在单测里。本组用例从 AgentLoop 入口走真实链路，
断言"信号真的被累积并真的触发了转人工"。
"""
from __future__ import annotations

import pytest

from app.agent.intent import IntentClassifier
from app.agent.llm.stub import StubLLMClient
from app.agent.loop import AgentLoop
from app.models.agent import Intent, IntentMode, IntentResult
from app.models.handoff import HandoffState, TriggerType

from tests.conftest_agent import InProcessMockClient


class FlakyTools(InProcessMockClient):
    """按需让某几个动作失败，用于制造"工具连续失败"。"""

    def __init__(self, app, *, fail_actions: set[str]):
        super().__init__(app)
        self.fail_actions = fail_actions
        self.calls: list[str] = []

    async def get_order(self, order_id, *, trace_id):
        self.calls.append("order.get")
        if "order.get" in self.fail_actions:
            raise TimeoutError("mock timeout")
        return await super().get_order(order_id, trace_id=trace_id)


@pytest.fixture
async def make_env():
    import fakeredis.aioredis

    import app.api.routes as mock_routes
    from app.agent.session import SessionStore
    from app.handoff.coordinator import HandoffCoordinator
    from app.handoff.queue import HandoffQueue
    from app.main import create_app

    mock_routes.reset_store()
    mock_app = create_app()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sessions = SessionStore(redis, ttl_seconds=3600)
    coordinator = HandoffCoordinator(HandoffQueue(redis))

    def _build(*, fail_actions: set[str] = frozenset(), responses: dict | None = None):
        tools = FlakyTools(mock_app, fail_actions=set(fail_actions))
        # 聊天路径上默认订单/物流查询；未登记的句子仍回落 UNKNOWN
        stub = StubLLMClient(
            {"订单状态": IntentResult(intent=Intent.QUERY_ORDER, confidence=0.9)}
            | (responses or {})
        )
        loop = AgentLoop(
            classifier=IntentClassifier(stub, keyword_map={}),
            sessions=sessions,
            tools=tools,
            coordinator=coordinator,
        )
        return loop, tools

    yield _build
    await redis.aclose()


async def _say(loop, sid: str, msg: str, *, order: str | None = "ORD1002"):
    return await loop.handle(
        session_id=sid, user_id="u1", message=msg, trace_id="t", order_id=order
    )


async def test_tool_failure_streak_accumulates_and_triggers_handoff(make_env):
    """工具连续失败达到阈值 → 下一轮直接转人工，不再让用户听"系统繁忙"。"""
    loop, _ = make_env(
        fail_actions={"order.get"},
        responses={"订单状态": IntentResult(intent=Intent.QUERY_ORDER, confidence=0.9)},
    )
    # 每轮一次工具调用失败。第 3 轮那一句在 LLM 之后、dispatch 之前先看到
    # streak == 2——还不够；它在 dispatch 里失败后把 streak 推到 3。
    for _ in range(3):
        await _say(loop, "s-fail", "订单状态")
    state = await loop.sessions.get("s-fail")
    assert state is not None and state.tool_failure_streak == 3

    # 第 4 轮：检测在 dispatch 之前就满足阈值 → 不再让用户多等一次超时
    r4 = await _say(loop, "s-fail", "订单状态")
    assert r4.state == "handoff_queued"


async def test_tool_failure_streak_resets_on_success(make_env):
    """中途成功一次就清零——"连续"失败才是信号，累计失败不是。"""
    loop, _ = make_env(
        fail_actions=set(),
        responses={"订单状态": IntentResult(intent=Intent.QUERY_ORDER, confidence=0.9)},
    )
    state = await loop.sessions.get_or_create("s-reset", user_id="u1", order_id="ORD1002")
    state.tool_failure_streak = 2
    await loop.sessions.save(state)

    await _say(loop, "s-reset", "订单状态")
    state = await loop.sessions.get("s-reset")
    assert state is not None and state.tool_failure_streak == 0


async def test_business_rejection_does_not_count_as_tool_failure(make_env):
    """业务拒绝（已退款不能再退）是正常结论，不该被当成系统故障堆成转人工。"""
    loop, _ = make_env(
        responses={"我要退款": IntentResult(intent=Intent.REFUND, confidence=0.9)}
    )
    await _say(loop, "s-biz", "我要退款")
    await _say(loop, "s-biz", "确认")
    # ORD1002 此时已 REFUNDED，再来一次应被业务拒绝
    r = await _say(loop, "s-biz", "我要退款")

    assert r.state == "write_rejected"
    state = await loop.sessions.get("s-biz")
    assert state is not None and state.tool_failure_streak == 0


async def test_high_amount_handoff_is_high_priority(make_env):
    """大额退款转人工时，优先级必须是 high —— 判定依据写进 context pack 供主管追溯。"""
    loop, _ = make_env(
        responses={
            "我要退款": IntentResult(intent=Intent.REFUND, confidence=0.9),
            "转人工": IntentResult(intent=Intent.MANUAL, confidence=0.9),
        }
    )
    # ORD1002 = 259.90：弹出确认卡即记下待退金额
    await _say(loop, "s-big", "我要退款")
    state = await loop.sessions.get("s-big")
    assert state is not None
    assert state.pending_refund_amount is not None

    r = await _say(loop, "s-big", "转人工")
    assert r.state == "handoff_queued"

    pack = await loop.coordinator.queue.get_pack("s-big")
    assert pack is not None
    # 259.90 未达 500 阈值 → MEDIUM；断言的是"金额确实参与了判定"
    assert pack.priority.value == "medium"
    assert "用户主动要求转人工" in pack.priority_reason


async def test_pending_refund_amount_raises_priority(make_env):
    """待退金额超过阈值时，用户主动转人工也要升到 high。"""
    from decimal import Decimal

    loop, _ = make_env(
        responses={
            "我要退款": IntentResult(intent=Intent.REFUND, confidence=0.9),
            "转人工": IntentResult(intent=Intent.MANUAL, confidence=0.9),
        }
    )
    await _say(loop, "s-big2", "我要退款")
    state = await loop.sessions.get("s-big2")
    assert state is not None
    state.pending_refund_amount = Decimal("9999")
    await loop.sessions.save(state)

    await _say(loop, "s-big2", "转人工")
    pack = await loop.coordinator.queue.get_pack("s-big2")
    assert pack is not None
    assert pack.priority.value == "high"
    assert "9999" in pack.priority_reason or "金额" in pack.priority_reason


async def test_manual_request_during_pending_card_still_hands_off(make_env):
    """确认卡还没回，用户已经要先人工：不能被"请您确认一下"卡住。"""
    loop, _ = make_env(
        responses={
            "我要退款": IntentResult(intent=Intent.REFUND, confidence=0.9),
            # 真模型会把"转人工"判 MANUAL；不登记的话桩会落 UNKNOWN
            "转人工": IntentResult(intent=Intent.MANUAL, confidence=0.9),
        }
    )
    await _say(loop, "s-bypass", "我要退款")
    assert await loop.sessions.get_pending("s-bypass") is not None

    r = await _say(loop, "s-bypass", "转人工")
    assert r.state == "handoff_queued"
    assert await loop.sessions.get_pending("s-bypass") is None


async def test_policy_gap_handoff_carries_boundary_trigger(make_env):
    """知识库答不了 → BOUNDARY 触发（这条原本就通，防回归）。"""
    from datetime import date

    from app.policy.models import PolicyAnswer, RetrievalDecision, RetrievalResult

    class _GapPolicy:
        async def answer(self, **kw):
            return (
                PolicyAnswer(decision=RetrievalDecision.MISS, rejection="rerank 判为不相关"),
                RetrievalResult(
                    query=kw["question"],
                    order_date=date(2026, 11, 10),
                    candidates=[],
                    decision=RetrievalDecision.MISS,
                    reason="rerank 判为不相关",
                    rerank_rejected=True,
                ),
            )

    loop, _ = make_env(
        responses={"吊牌剪了能退吗": IntentResult(intent=Intent.POLICY, mode=IntentMode.INQUIRY, confidence=0.9)}
    )
    loop.policy = _GapPolicy()
    r = await _say(loop, "s-gap2", "吊牌剪了能退吗")
    assert r.state == "handoff_queued"

    pack = await loop.coordinator.queue.get_pack("s-gap2")
    assert pack is not None
    assert pack.trigger_type is TriggerType.BOUNDARY
    assert pack.priority is not None


async def test_handoff_context_records_attempted_failures(make_env):
    """转人工时 Context Pack 必须带上"已经失败了几次"，人工端据此判断要不要查系统。"""
    loop, _ = make_env(
        fail_actions={"order.get"},
        responses={"订单状态": IntentResult(intent=Intent.QUERY_ORDER, confidence=0.9)},
    )
    for _ in range(4):
        await _say(loop, "s-pack", "订单状态")

    pack = await loop.coordinator.queue.get_pack("s-pack")
    assert pack is not None
    assert pack.trigger_type is TriggerType.SYSTEM_ERROR
    assert "工具连续失败" in pack.trigger_detail

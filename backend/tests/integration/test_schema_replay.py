"""结构失败的重放与耗尽转人工（specs/007-llm-schema-replay）。

两个阶段分开验证：
- 重放在同一次请求内完成，失败即降级，不猜意图
- 耗尽标记进入会话，下一轮由 triggers 走 SYSTEM_ERROR 转人工
"""
from __future__ import annotations

import pytest

from app.agent.intent import IntentClassifier
from app.agent.llm.base import LLMError
from app.agent.llm.stub import StubLLMClient
from app.agent.loop import AgentLoop
from app.agent.resilience import RetryStats, with_retry
from app.models.agent import Intent, IntentResult

from tests.conftest_agent import InProcessMockClient


@pytest.fixture
async def make_loop():
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

    def _build(stub: StubLLMClient):
        return AgentLoop(
            classifier=IntentClassifier(stub, keyword_map={}),
            sessions=sessions,
            tools=InProcessMockClient(mock_app),
            coordinator=coordinator,
        )

    yield _build
    await redis.aclose()


async def _say(loop, sid: str, msg: str):
    return await loop.handle(
        session_id=sid, user_id="u1", message=msg, trace_id="t", order_id="ORD1002"
    )


# ---------- with_retry：结构失败不盲重试 ----------

async def test_schema_error_is_not_blindly_retried():
    """结构非法时客户端已带错误重放过，再盲重试只是拖长等待。"""
    calls = 0

    async def boom():
        nonlocal calls
        calls += 1
        raise LLMError("bad json", schema=True)

    with pytest.raises(LLMError) as exc:
        await with_retry(boom, label="test")
    assert exc.value.schema is True
    assert calls == 1, "结构失败不应再走三次盲重试"


async def test_network_error_still_retries():
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise LLMError("timeout")
        return "ok"

    assert await with_retry(flaky, label="test") == "ok"
    assert calls == 3


# ---------- 重放链路 ----------

async def test_replay_recovers_within_one_turn(make_loop):
    """首次非法 → 客户端带错误重放拿到合法 → 本轮正常走完。

    重放逻辑在 OpenAICompatLLMClient 内部，桩模型没有重放能力，
    因此不能靠桩验证这条（见 test_llm_openai_replay.py）。
    这里只验证"耗尽标记一旦生效并转人工，就不再被旧标记重复触发"。
    """
    stub = StubLLMClient(
        {"我要退款": IntentResult(intent=Intent.REFUND, confidence=0.95)},
        invalid_times=1,
    )
    loop = make_loop(stub)

    # 桩首次直接抛 schema 错（无重放能力）→ 降级 + 标记
    r1 = await _say(loop, "s-replay", "我要退款")
    assert r1.state == "degraded"

    # 下一轮：标记生效 → 转人工，标记随即清掉
    r2 = await _say(loop, "s-replay", "我要退款")
    assert r2.state == "handoff_queued"
    state = await loop.sessions.get("s-replay")
    assert state is not None and state.schema_retries_exhausted is False

    # 再一轮：模型已恢复（桩第 3 次返回合法），不应再被旧标记推人工
    stub.invalid_times = 0
    r3 = await _say(loop, "s-replay", "我要退款")
    assert r3.state == "awaiting_confirmation"


async def test_replay_exhausted_degrades_and_marks_session(make_loop):
    """重放也非法 → 本轮降级（不猜意图），并标记会话。"""
    stub = StubLLMClient({}, invalid_times=99)
    loop = make_loop(stub)

    r = await _say(loop, "s-exhaust", "我要退款")
    assert r.source.value == "degraded"
    assert r.state == "degraded"
    # 不得有任何退款动作
    order = await loop.tools.get_order("ORD1002", trace_id="verify")
    assert (order.get("data") or {}).get("status") == "PAID"

    state = await loop.sessions.get("s-exhaust")
    assert state is not None and state.schema_retries_exhausted is True


async def test_exhausted_marker_triggers_system_error_handoff(make_loop):
    """下一轮检测到标记 → SYSTEM_ERROR 转人工，Context Pack 写明原因。"""
    stub = StubLLMClient(
        {"我要退款": IntentResult(intent=Intent.REFUND, confidence=0.95)},
        invalid_times=99,
    )
    loop = make_loop(stub)

    await _say(loop, "s-next", "我要退款")  # 本轮降级
    r = await _say(loop, "s-next", "我要退款")  # 下一轮应先转人工
    # 注意力：这一轮仍会先调 LLM（FR-016b 不改），耗尽标记在 _exhausted 之前生效
    assert r.state == "handoff_queued"

    pack = await loop.coordinator.queue.get_pack("s-next")
    assert pack is not None
    assert "重放" in pack.trigger_detail or "校验" in pack.trigger_detail


async def test_exhausted_marker_is_one_shot(make_loop):
    """耗尽标记只能推一次人工。

    不清除的话，用户在人工队列里说的每一句话都会被这个旧标记
    重新推一遍人工，人工端会看到同一个会话反复入队。
    """
    stub = StubLLMClient({}, invalid_times=99)
    loop = make_loop(stub)
    await _say(loop, "s-clear", "订单状态")   # 降级 + 标记
    r2 = await _say(loop, "s-clear", "订单状态")  # 标记生效 → 转人工
    assert r2.state == "handoff_queued"

    state = await loop.sessions.get("s-clear")
    assert state is not None and state.schema_retries_exhausted is False


async def test_schema_failure_does_not_pollute_timeout_streak(make_loop):
    """结构失败不该累加 llm_timeout_streak —— 那是网络故障的计数。"""
    loop = make_loop(StubLLMClient({}, invalid_times=99))
    await _say(loop, "s-sep", "我要退款")
    state = await loop.sessions.get("s-sep")
    assert state is not None
    assert state.schema_retries_exhausted is True
    assert state.llm_timeout_streak == 0


async def test_timeout_failure_does_not_set_schema_flag(make_loop):
    """反过来，网络超时不该被当成结构失败。"""
    stub = StubLLMClient({}, fail_mode="timeout", fail_times=0)
    loop = make_loop(stub)
    await _say(loop, "s-sep2", "我要退款")
    state = await loop.sessions.get("s-sep2")
    assert state is not None
    assert state.schema_retries_exhausted is False
    assert state.llm_timeout_streak == 1


# ---------- 计数器 ----------

def test_stats_counters_exist_and_reset():
    s = RetryStats()
    s.record_schema_replay()
    s.record_schema_failure()
    assert s.schema_replays == 1 and s.schema_failures == 1
    s.reset()
    assert s.schema_replays == 0 and s.schema_failures == 0


async def test_health_endpoint_exposes_schema_counters():
    from app.api.agent_routes import llm_health

    body = await llm_health()
    assert "schema_replays" in body and "schema_failures" in body

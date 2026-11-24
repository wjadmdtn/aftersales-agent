"""多轮记忆：信息分散在多句话里时，前几轮的内容要真的进到下一轮请求里。

此前每轮只给模型 order_id + intent，用户分三句说清一件事（"我那单…上周买的…
保温杯"）时，前面说过的信息每轮都丢，只能反复追问。
"""
from __future__ import annotations

import pytest

from app.agent.intent import IntentClassifier
from app.agent.llm.stub import StubLLMClient
from app.agent.loop import AgentLoop
from app.models.agent import Intent, IntentMode, IntentResult

from tests.conftest_agent import InProcessMockClient


@pytest.fixture
async def make_env():
    import fakeredis.aioredis

    import app.api.routes as mock_routes
    from app.agent.session import SessionStore
    from app.main import create_app

    mock_routes.reset_store()
    mock_app = create_app()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sessions = SessionStore(redis, ttl_seconds=3600)

    def _build(responses: dict[str, IntentResult] | None = None):
        stub = StubLLMClient(responses or {})
        loop = AgentLoop(
            classifier=IntentClassifier(stub, keyword_map={}),
            sessions=sessions,
            tools=InProcessMockClient(mock_app),
        )
        return loop, stub

    yield _build
    await redis.aclose()


async def _say(loop, sid: str, msg: str, *, order: str | None = "ORD1002"):
    return await loop.handle(
        session_id=sid, user_id="u1", message=msg, trace_id="t", order_id=order
    )


async def test_second_turn_carries_history_to_model(make_env):
    """第二轮请求必须带上第一轮的原文，否则模型看不到上下文。"""
    loop, stub = make_env(
        {
            "查一下订单": IntentResult(intent=Intent.QUERY_ORDER, mode=IntentMode.EXECUTE, confidence=0.9),
        }
    )

    await _say(loop, "s1", "查一下订单")
    await _say(loop, "s1", "上周买的保温杯")

    # 第二轮调用时，历史里应有：用户第一句 + 助手第一轮回复
    assert [t.text for t in stub.last_turns][0] == "查一下订单"
    assert len(stub.last_turns) >= 2


async def test_facts_accumulate_across_turns(make_env):
    """订单号 / 时间 / 诉求要跨轮累积，不随窗口丢失。"""
    loop, stub = make_env(
        {
            "查一下订单": IntentResult(intent=Intent.QUERY_ORDER, mode=IntentMode.EXECUTE, confidence=0.9),
        }
    )

    await _say(loop, "s1", "查一下订单", order=None)
    await _say(loop, "s1", "上周买的,订单号 ORD1003", order=None)

    state = await loop.sessions.get("s1")
    assert state is not None
    assert state.facts["issue"] == "query_order"
    assert state.facts["time_ref"] == "上周"
    assert state.facts["order_id"] == "ORD1003"


async def test_low_info_message_not_recorded(make_env):
    loop, stub = make_env()
    await _say(loop, "s1", "嗯")
    state = await loop.sessions.get("s1")
    assert state is not None
    assert [t.text for t in state.recent_turns if t.role == "user"] == []


async def test_window_limited_by_config(make_env, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "memory_recent_turns", 2)
    loop, stub = make_env()

    for text in ("第一句", "第二句", "第三句"):
        await _say(loop, "s1", text)

    state = await loop.sessions.get("s1")
    assert state is not None
    assert len(state.recent_turns) == 2

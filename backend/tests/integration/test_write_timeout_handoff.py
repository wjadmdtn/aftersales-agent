"""写操作超时：先幂等重试一次，仍失败立即转人工（不等下一轮）。"""

from __future__ import annotations

import pytest

from app.agent.intent import IntentClassifier
from app.agent.llm.stub import StubLLMClient
from app.agent.loop import AgentLoop
from app.handoff.coordinator import HandoffCoordinator
from app.handoff.queue import HandoffQueue
from app.models.agent import Intent, IntentMode, IntentResult
from app.models.handoff import TriggerType

from tests.conftest_agent import InProcessMockClient


class TimeoutRefundTools(InProcessMockClient):
    """create_refund 永远超时，记录每次用的 request_id。"""

    def __init__(self, app):
        super().__init__(app)
        self.request_ids: list[str] = []

    async def create_refund(self, order_id, amount, *, request_id, trace_id):
        self.request_ids.append(request_id)
        raise TimeoutError("downstream timeout")


@pytest.fixture
async def env():
    import fakeredis.aioredis

    import app.api.routes as mock_routes
    from app.agent.session import SessionStore
    from app.main import create_app

    mock_routes.reset_store()
    mock_app = create_app()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sessions = SessionStore(redis, ttl_seconds=3600)
    coordinator = HandoffCoordinator(HandoffQueue(redis))
    tools = TimeoutRefundTools(mock_app)
    loop = AgentLoop(
        classifier=IntentClassifier(
            StubLLMClient(
                {"我要退款": IntentResult(intent=Intent.REFUND, mode=IntentMode.EXECUTE, confidence=0.9)}
            ),
            keyword_map={},
        ),
        sessions=sessions,
        tools=tools,
        coordinator=coordinator,
    )
    yield loop, tools
    await redis.aclose()


async def test_write_timeout_retries_once_then_hands_off(env):
    loop, tools = env

    first = await loop.handle(
        session_id="w1", user_id="u1", message="我要退款", trace_id="t", order_id="ORD1002"
    )
    assert first.state == "awaiting_confirmation"
    assert first.card is not None

    reply = await loop.confirm_pending(
        session_id="w1",
        user_id="u1",
        card_id=first.card["card_id"],
        decision="confirm",
        trace_id="t2",
    )

    # 幂等重试一次：两次用同一把 request_id
    assert len(tools.request_ids) == 2, "写超时应幂等重试一次"
    assert tools.request_ids[0] == tools.request_ids[1]

    # 仍失败 → 立即转人工
    assert reply.state == "handoff_queued"
    assert "转接人工" in reply.reply

    pack = await loop.coordinator.queue.get_pack("w1")
    assert pack is not None
    assert pack.trigger_type is TriggerType.SYSTEM_ERROR
    # 待退金额带进了上下文，供人工判风险
    assert "259.90" in (pack.priority_reason or "") or pack.priority is not None

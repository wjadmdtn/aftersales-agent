"""Agent 端到端测试夹具：起 mock 服务 + agent，用 fakeredis，LLM 用桩。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.routes as mock_routes
from app.agent.intent import IntentClassifier
from app.agent.llm.stub import StubLLMClient
from app.agent.session import SessionStore
from app.agent.tools.client import MockServiceClient
from app.api import agent_routes
from app.main import create_app
from app.models.agent import Intent, IntentResult


class InProcessMockClient(MockServiceClient):
    """用 ASGI 直连，不起真实端口；但 trace 透传与业务路径完全相同。"""

    def __init__(self, app):
        self._transport = ASGITransport(app=app)

    async def _get(self, path: str, *, trace_id: str) -> dict:
        async with AsyncClient(transport=self._transport, base_url="http://mock") as c:
            r = await c.get(path, headers={"X-Trace-Id": trace_id})
            r.raise_for_status()
            return r.json()

    async def create_refund(self, order_id, amount, *, request_id, trace_id):
        async with AsyncClient(transport=self._transport, base_url="http://mock") as c:
            r = await c.post(
                f"/orders/{order_id}/refund",
                json={"amount": str(amount)},
                headers={"X-Trace-Id": trace_id, "X-Request-Id": request_id},
            )
            r.raise_for_status()
            return r.json()

    async def update_address(self, order_id, address, *, trace_id, request_id=None):
        async with AsyncClient(transport=self._transport, base_url="http://mock") as c:
            headers = {"X-Trace-Id": trace_id}
            if request_id:
                headers["X-Request-Id"] = request_id
            r = await c.put(f"/orders/{order_id}/address", json=address, headers=headers)
            r.raise_for_status()
            return r.json()


@pytest.fixture
async def env():
    """返回 (agent_client, mock_client, stub_llm, store)。"""
    import fakeredis.aioredis

    mock_routes.reset_store()
    mock_app = create_app()

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = SessionStore(redis, ttl_seconds=3600)

    responses = {
        "我要退款": IntentResult(intent=Intent.REFUND, confidence=0.95),
        "快递到哪了": IntentResult(intent=Intent.LOGISTICS, confidence=0.92),
        "订单状态": IntentResult(intent=Intent.QUERY_ORDER, confidence=0.9),
        "转人工": IntentResult(intent=Intent.MANUAL, confidence=0.9),
        "我要投诉": IntentResult(intent=Intent.COMPLAINT, confidence=0.9),
    }
    stub = StubLLMClient(responses)
    classifier = IntentClassifier(stub, keyword_map={})
    tools = InProcessMockClient(mock_app)

    from app.handoff.coordinator import HandoffCoordinator
    from app.handoff.queue import HandoffQueue

    coordinator = HandoffCoordinator(HandoffQueue(redis))
    agent_routes.configure_agent(
        classifier=classifier, sessions=store, tools=tools, coordinator=coordinator
    )
    agent_app = create_app()

    async with AsyncClient(transport=ASGITransport(app=agent_app), base_url="http://agent") as ac:
        yield {
            "client": ac,
            "stub": stub,
            "store": store,
            "mock_client": AsyncClient(transport=ASGITransport(app=mock_app), base_url="http://mock"),
            "redis": redis,
        }

    await redis.aclose()


@pytest.fixture
def chat(env):
    async def _chat(message, *, session="s1", user="u1", order="ORD1002", trace=None, snapshot=None):
        headers = {"X-Trace-Id": trace} if trace else {}
        payload = {"session_id": session, "user_id": user, "message": message, "order_id": order}
        if snapshot:
            payload["snapshot"] = snapshot
        r = await env["client"].post("/agent/chat", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    return _chat

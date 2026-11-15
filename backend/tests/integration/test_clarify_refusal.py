"""抱怨类对话的转人工边界（口径 2026-11-12）。

真实会话暴露的问题：AI 问"要不要转人工"，用户答"不用"，系统却仍转了。
新口径把问题从根上解决——**AI 根本不问这句话了**：
抱怨、不满、说不清，一律由 AI 继续追问；只有明确投诉/要人工/差评才转。

本文件保留一条回归：抱怨多轮之后仍不得自动转人工。

（原「拒绝语识别」一组随 is_handoff_refusal / is_handoff_agreement 一并删除——
那套逻辑服务于已废弃的"AI 主动问要不要转人工"流程。）
"""
from __future__ import annotations

import pytest

from app.agent.intent import IntentClassifier
from app.agent.llm.stub import StubLLMClient
from app.agent.loop import AgentLoop
from app.models.agent import Intent, IntentMode, IntentResult

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
    tools = InProcessMockClient(mock_app)
    coordinator = HandoffCoordinator(HandoffQueue(redis))

    def _build(responses):
        return AgentLoop(
            classifier=IntentClassifier(StubLLMClient(responses), keyword_map={}),
            sessions=sessions,
            tools=tools,
            coordinator=coordinator,
        )

    yield _build
    await redis.aclose()


def _r(intent: Intent, mode: IntentMode = IntentMode.EXECUTE) -> IntentResult:
    return IntentResult(intent=intent, confidence=0.9, mode=mode)


async def _say(loop, sid: str, msg: str):
    return await loop.handle(
        session_id=sid, user_id="u1", message=msg, trace_id="t", order_id="ORD1002"
    )


async def test_complaint_never_offers_handoff_then_refusal_is_moot(make_loop):
    """真实复现序列：抱怨 → 系统追问（不再问转人工）→ 用户继续抱怨，前两轮不转。

    旧行为：第 2 轮问"要不要转人工"，用户答"不用"却被转。
    新行为：不问那句，也就不存在"拒绝了还转"。
    """
    loop = make_loop({
        "上次退款你们拖了半个月": _r(Intent.UNKNOWN),
        "之前退货就很麻烦": _r(Intent.UNKNOWN),
    })

    r1 = await _say(loop, "s-refuse", "上次退款你们拖了半个月")
    assert r1.state == "clarifying"
    assert "人工" not in r1.reply, f"第 1 轮不该提人工：{r1.reply}"

    r2 = await _say(loop, "s-refuse", "之前退货就很麻烦")
    assert r2.state == "clarifying"
    assert "人工" not in r2.reply, f"第 2 轮不该提人工：{r2.reply}"

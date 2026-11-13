"""抱怨类对话的转人工边界（口径 2026-11-12）。

真实会话暴露的问题：AI 问"要不要转人工"，用户答"不用"，系统却仍转了。
新口径把问题从根上解决——**AI 根本不问这句话了**：
抱怨、不满、说不清，一律由 AI 继续追问；只有明确投诉/要人工/差评才转。

本文件保留两条价值最高的回归：
1. 用户拒绝过转人工后，后续说话不得再被自动转
2. 拒绝语识别（句首匹配，不误伤"东西我不要了"这类诉求）
"""
from __future__ import annotations

import pytest

from app.agent.clarify import is_handoff_agreement, is_handoff_refusal
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


# ---------- 拒绝语识别（保留给未来可能的"确认式转人工"场景） ----------


@pytest.mark.parametrize(
    "text",
    ["不用", "不用了", "不用 之前退货就很麻烦", "不需要谢谢", "算了", "先不用", "不"],
)
def test_refusal_is_recognized(text):
    assert is_handoff_refusal(text), f"「{text}」应识别为拒绝"


@pytest.mark.parametrize(
    "text",
    ["东西我不要了", "我不想留了，退啦", "我要退款", "取消订单"],
)
def test_refusal_does_not_swallow_business_requests(text):
    """句中出现"不要/不"不等于拒绝转人工——那是正常业务诉求。"""
    assert not is_handoff_refusal(text), f"「{text}」是业务诉求，不该判为拒绝"


@pytest.mark.parametrize("text", ["好的", "可以", "行", "要", "转吧", "好的。"])
def test_agreement_is_recognized(text):
    assert is_handoff_agreement(text)

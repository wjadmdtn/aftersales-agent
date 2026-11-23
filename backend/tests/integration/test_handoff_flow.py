"""T026: 转人工端到端流程。"""

import pytest

from app.config import settings
from app.handoff.coordinator import HandoffCoordinator
from app.handoff.queue import HandoffQueue
from app.handoff.state import SilenceViolation, assert_ai_may_speak
from app.models.handoff import Emotion, HandoffState, Priority, TriggerType

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
async def coord():
    import fakeredis.aioredis

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield HandoffCoordinator(HandoffQueue(r))
    await r.aclose()


async def _init(coord, msg="转人工", **kw):
    trigger = coord.detect(user_message=msg, **kw)
    assert trigger.triggered
    return await coord.initiate(
        session_id=kw.pop("session_id", "s1"),
        user_id="u1",
        user_message=msg,
        primary_intent=kw.pop("primary_intent", "refund"),
        trigger=trigger,
        **kw,
    )


async def test_user_request_flow(coord):
    out = await _init(coord, "转人工")
    assert out.trigger_type is TriggerType.USER_REQUEST
    assert out.state is HandoffState.QUEUED
    assert "转接人工" in out.reply


async def test_user_request_has_no_retention_script(coord):
    """SC-002：禁止挽留话术。"""
    out = await _init(coord, "转人工")
    for banned in ("我还能帮您", "还有什么可以帮", "要不要再试试"):
        assert banned not in out.reply


async def test_risk_priority_is_high(coord):
    out = await _init(coord, "我要投诉你们")
    ctx = await coord.queue.get_pack("s1")
    assert ctx.priority is Priority.HIGH
    assert ctx.suggested_next_step is None, "风险触发不给 AI 建议"
    assert out.reply == settings.handoff_scripts["risk"]


async def test_risk_skips_self_service(coord):
    trigger = coord.detect(user_message="我要投诉")
    assert trigger.skip_self_service is True


async def test_duplicate_returns_queued_followup(coord):
    await _init(coord, "转人工")
    out = await _init(coord, "转人工")
    assert "正在为您接入" in out.reply
    assert await coord.queue.depth() == 1


async def test_human_accept(coord):
    await _init(coord, "转人工")
    out = await coord.human_accept("s1")
    assert out.state is HandoffState.HUMAN_SERVING
    assert await coord.queue.depth() == 0


async def test_ai_silent_after_accept(coord):
    """SC-003：人工接管后 AI 输出次数为 0。"""
    await _init(coord, "转人工")
    await coord.human_accept("s1")

    with pytest.raises(SilenceViolation):
        assert_ai_may_speak(HandoffState.HUMAN_SERVING)


async def test_ai_cannot_initiate_handoff_while_human_serving(coord):
    await _init(coord, "转人工")
    await coord.human_accept("s1")
    trigger = coord.detect(user_message="转人工")

    with pytest.raises(SilenceViolation):
        await coord.initiate(
            session_id="s1",
            user_id="u1",
            user_message="转人工",
            primary_intent="refund",
            trigger=trigger,
            current_state=HandoffState.HUMAN_SERVING,
        )


async def test_transfer_requeues_to_queue(coord):
    """人工不允许拒绝：转接后回到排队，等另一位客服接入。"""
    await _init(coord, "转人工")
    await coord.human_accept("s1")
    out = await coord.human_transfer("s1")

    assert out.state is HandoffState.QUEUED
    assert "transferred" in out.events


async def test_queued_followup_script(coord):
    text = await coord.queued_reply()
    assert "正在为您接入" in text


async def test_ask_time_gives_no_estimate(coord):
    text = await coord.queued_reply(asking_time=True)
    assert "接入" in text


def test_all_scripts_have_no_time_estimate():
    """SC-009：任何话术都不得出现时间数字。"""
    import re

    pattern = re.compile(r"\d+\s*(秒|分钟|小时|min|s\b|m\b)")
    for key, text in settings.handoff_scripts.items():
        assert not pattern.search(text), f"{key} 含时间承诺：{text}"


def test_all_scripts_non_empty():
    for key, text in settings.handoff_scripts.items():
        assert text.strip(), key

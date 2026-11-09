"""T018: 队列优先级与幂等。"""

from datetime import datetime, timedelta, timezone

import pytest

from app.handoff.queue import HandoffQueue
from app.models.handoff import Emotion, HandoffContext, Priority, TriggerType

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
async def queue():
    import fakeredis.aioredis

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield HandoffQueue(r)
    await r.aclose()


def _ctx(sid: str, priority: Priority, *, offset_ms: int = 0) -> HandoffContext:
    return HandoffContext(
        session_id=sid,
        user_id="u1",
        order_id="ORD1002",
        summary="测试摘要",
        primary_intent="refund",
        sentiment=Emotion.NEUTRAL,
        priority=priority,
        priority_reason="测试依据",
        trigger_type=TriggerType.USER_REQUEST,
        trigger_detail="测试",
        queued_at=datetime.now(timezone.utc) + timedelta(milliseconds=offset_ms),
    )


async def test_enqueue_and_peek(queue):
    await queue.enqueue(_ctx("s1", Priority.MEDIUM))
    items = await queue.peek()
    assert [c.session_id for c in items] == ["s1"]


async def test_high_priority_first(queue):
    await queue.enqueue(_ctx("low", Priority.LOW))
    await queue.enqueue(_ctx("high", Priority.HIGH, offset_ms=100))
    items = await queue.peek()
    assert items[0].session_id == "high", "高优先级必须排前面，即使后入队"


async def test_same_priority_fifo(queue):
    for i, sid in enumerate(["a", "b", "c"]):
        await queue.enqueue(_ctx(sid, Priority.MEDIUM, offset_ms=i * 10))
    items = await queue.peek()
    assert [c.session_id for c in items] == ["a", "b", "c"]


async def test_duplicate_trigger_takes_one_slot(queue):
    assert await queue.enqueue(_ctx("s1", Priority.MEDIUM)) is True
    assert await queue.enqueue(_ctx("s1", Priority.HIGH)) is False
    assert await queue.depth() == 1


async def test_pack_refreshed_on_requeue(queue):
    await queue.enqueue(_ctx("s1", Priority.MEDIUM))
    await queue.enqueue(_ctx("s1", Priority.HIGH))
    ctx = await queue.get_pack("s1")
    assert ctx.priority is Priority.HIGH, "上下文应刷新为最新"


async def test_accept_removes(queue):
    await queue.enqueue(_ctx("s1", Priority.MEDIUM))
    ctx = await queue.accept("s1")
    assert ctx is not None
    assert await queue.depth() == 0


async def test_decline_removes_and_does_not_requeue(queue):
    await queue.enqueue(_ctx("s1", Priority.MEDIUM))
    await queue.decline("s1")
    assert await queue.depth() == 0


async def test_depth_by_priority(queue):
    await queue.enqueue(_ctx("h", Priority.HIGH))
    await queue.enqueue(_ctx("m", Priority.MEDIUM))
    await queue.enqueue(_ctx("l", Priority.LOW))
    counts = await queue.depth_by_priority()
    assert counts == {"high": 1, "medium": 1, "low": 1}


async def test_accept_missing_returns_none(queue):
    assert await queue.accept("nope") is None

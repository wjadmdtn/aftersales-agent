import pytest

from app.config import HandoffRules
from app.handoff.context_pack import build_context
from app.handoff.queue import HandoffQueue, queue_depth_by_priority
from app.models import (
    HistoryTurn,
    Order,
    Priority,
    Sentiment,
    TriggerResult,
    TriggerType,
)

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
async def queue():
    import fakeredis.aioredis

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield HandoffQueue(r, HandoffRules())
    await r.aclose()


def _pack(session_id: str, trigger: TriggerType, *, amount: float | None = None):
    return build_context(
        session_id=session_id,
        user_id="u1",
        entry_point="order_list",
        order=Order(order_id="ORD1", status="SHIPPED", amount=299),
        turns=[HistoryTurn(role="user", content="退款")],
        attempted=[],
        primary_intent="refund",
        intent_confidence=0.9,
        sentiment=Sentiment.NEUTRAL,
        refund_amount=amount,
        trigger=TriggerResult.fire(trigger, "test"),
        rules=HandoffRules(),
    )


async def test_enqueue_then_peek(queue):
    assert await queue.enqueue(_pack("s1", TriggerType.USER_REQUEST)) is True
    packs = await queue.peek()
    assert [p.session_id for p in packs] == ["s1"]


async def test_enqueue_is_idempotent(queue):
    assert await queue.enqueue(_pack("s1", TriggerType.USER_REQUEST)) is True
    assert await queue.enqueue(_pack("s1", TriggerType.USER_REQUEST)) is False
    assert len(await queue.peek()) == 1


async def test_repeat_request_keeps_queue_position(queue):
    await queue.enqueue(_pack("s1", TriggerType.USER_REQUEST))
    await queue.enqueue(_pack("s2", TriggerType.USER_REQUEST))
    await queue.enqueue(_pack("s1", TriggerType.USER_REQUEST))
    packs = await queue.peek()
    assert [p.session_id for p in packs] == ["s1", "s2"]


async def test_high_priority_jumps_queue(queue):
    await queue.enqueue(_pack("low", TriggerType.SYSTEM_ERROR))
    await queue.enqueue(_pack("high", TriggerType.RISK, amount=800))
    packs = await queue.peek()
    assert packs[0].session_id == "high"
    assert packs[0].priority is Priority.HIGH


async def test_same_priority_is_fifo(queue):
    for sid in ("a", "b", "c"):
        await queue.enqueue(_pack(sid, TriggerType.USER_REQUEST))
    assert [p.session_id for p in await queue.peek()] == ["a", "b", "c"]


async def test_accept_removes_from_queue(queue):
    await queue.enqueue(_pack("s1", TriggerType.USER_REQUEST))
    ctx = await queue.accept("s1")
    assert ctx is not None and ctx.session_id == "s1"
    assert await queue.peek() == []


async def test_decline_does_not_requeue(queue):
    await queue.enqueue(_pack("s1", TriggerType.USER_REQUEST))
    await queue.decline("s1", reason="not my team")
    assert await queue.peek() == []


async def test_depth_by_priority(queue):
    await queue.enqueue(_pack("h", TriggerType.RISK, amount=800))
    await queue.enqueue(_pack("m", TriggerType.USER_REQUEST))
    await queue.enqueue(_pack("l", TriggerType.SYSTEM_ERROR))
    counts = queue_depth_by_priority(await queue.peek())
    assert counts == {"high": 1, "medium": 1, "low": 1}

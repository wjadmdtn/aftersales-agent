from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.agent.session import SessionStore, snapshot_mismatch
from app.config import settings
from app.models.agent import OrderSnapshot, PendingConfirmation, SessionState

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
async def store():
    import fakeredis.aioredis

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield SessionStore(r, ttl_seconds=3600)
    await r.aclose()


async def test_get_or_create(store):
    s = await store.get_or_create("s1", user_id="u1", order_id="ORD1002")
    assert s.session_id == "s1" and s.order_id == "ORD1002"
    again = await store.get_or_create("s1", user_id="u1")
    assert again.order_id == "ORD1002"


async def test_save_and_get(store):
    s = await store.get_or_create("s1", user_id="u1")
    s.intent = s.intent or None
    s.turn_count = 5
    await store.save(s)
    loaded = await store.get("s1")
    assert loaded.turn_count == 5


async def test_missing_returns_none(store):
    assert await store.get("nope") is None


async def test_pending_roundtrip(store):
    p = PendingConfirmation(
        action="refund",
        order_id="ORD1002",
        amount=Decimal("10.00"),
        summary="确认吗",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    await store.set_pending("s1", p)
    got = await store.get_pending("s1")
    assert got.action == "refund" and got.amount == Decimal("10.00")

    await store.clear_pending("s1")
    assert await store.get_pending("s1") is None


async def test_delete_clears_both(store):
    await store.get_or_create("s1", user_id="u1")
    await store.set_pending(
        "s1",
        PendingConfirmation(
            action="refund",
            order_id="ORD1002",
            summary="x",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        ),
    )
    await store.delete("s1")
    assert await store.get("s1") is None
    assert await store.get_pending("s1") is None


def test_snapshot_mismatch_detects_amount_diff():
    snap = OrderSnapshot(amount=Decimal("259.90"))
    real = type("O", (), {"amount": Decimal("299.00")})()
    assert snapshot_mismatch(snap, real) is True


def test_snapshot_mismatch_same_amount():
    snap = OrderSnapshot(amount=Decimal("259.90"))
    real = type("O", (), {"amount": Decimal("259.90")})()
    assert snapshot_mismatch(snap, real) is False


def test_snapshot_mismatch_no_snapshot():
    real = type("O", (), {"amount": Decimal("1.00")})()
    assert snapshot_mismatch(None, real) is False

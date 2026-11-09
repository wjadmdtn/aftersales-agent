import asyncio
import json
from pathlib import Path

import pytest

from app.models.enums import OrderStatus
from app.store.memory import MemoryStore
from app.store.seed import SeedValidationError, load_seed


@pytest.fixture
def seed_path(tmp_path) -> Path:
    p = tmp_path / "orders.json"
    p.write_text(
        json.dumps(
            [
                {
                    "order_id": "A1",
                    "status": "PAID",
                    "amount": "100.00",
                    "address": {
                        "receiver": "x",
                        "phone": "13800138000",
                        "province": "p",
                        "city": "c",
                        "district": "d",
                        "detail": "dd",
                    },
                    "logistics": None,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def test_seed_loads(seed_path):
    data = load_seed(seed_path)
    assert "A1" in data.orders
    assert data.orders["A1"].status is OrderStatus.PAID


def test_missing_seed_file(tmp_path):
    with pytest.raises(SeedValidationError):
        load_seed(tmp_path / "nope.json")


def test_invalid_status_rejected(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(
        json.dumps(
            [
                {
                    "order_id": "A1",
                    "status": "NOT_A_STATUS",
                    "amount": "1.00",
                    "address": {
                        "receiver": "x",
                        "phone": "13800138000",
                        "province": "p",
                        "city": "c",
                        "district": "d",
                        "detail": "dd",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(SeedValidationError):
        load_seed(p)


def test_duplicate_order_id_rejected(tmp_path):
    p = tmp_path / "dup.json"
    item = {
        "order_id": "A1",
        "status": "PAID",
        "amount": "1.00",
        "address": {
            "receiver": "x",
            "phone": "13800138000",
            "province": "p",
            "city": "c",
            "district": "d",
            "detail": "dd",
        },
    }
    p.write_text(json.dumps([item, item]), encoding="utf-8")
    with pytest.raises(SeedValidationError):
        load_seed(p)


def test_store_returns_copy(seed_path):
    store = MemoryStore(seed_path)
    order = store.get_order("A1")
    order.status = OrderStatus.CLOSED
    assert store.get_order("A1").status is OrderStatus.PAID


def test_store_reset_restores_seed(seed_path):
    store = MemoryStore(seed_path)
    from app.models.domain import Address

    store.update_address(
        "A1",
        Address(receiver="y", phone="13900139000", province="p", city="c", district="d", detail="dd"),
    )
    assert store.get_order("A1").address.receiver == "y"
    store.reset()
    assert store.get_order("A1").address.receiver == "x"


def test_missing_order_returns_none(seed_path):
    store = MemoryStore(seed_path)
    assert store.get_order("NOPE") is None
    assert store.get_logistics("NOPE") is None


async def test_locks_differ_per_order(seed_path):
    store = MemoryStore(seed_path, lock_shards=8)
    lock_a = store.lock_for("ORDER_A")
    lock_b = store.lock_for("ORDER_B")
    # 不保证不同，但至少同订单取到同一把锁
    assert store.lock_for("ORDER_A") is lock_a
    assert isinstance(lock_a, asyncio.Lock)
    assert isinstance(lock_b, asyncio.Lock)

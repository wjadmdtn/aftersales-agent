"""催发货业务逻辑（services/urge.py）。

与退款的区别决定了两条不同的测试重点：
- 退款怕重复扣款 → 幂等键是命门
- 催单怕催不该催的单 → 状态矩阵是命门；重复催反而无害，只防刷屏

另外锁死一条配置一致性：政策条款 LOGISTICS-01 里的 params 必须与
settings 的时效一致。两处不一致会让"客服话术说 48 小时"和"条款写 72 小时"
同时存在，这种分裂比数字本身错更糟。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.models.enums import OrderStatus, ResultCode
from app.models.results import ResultMeta
from app.services import urge as urge_service
from app.store.memory import MemoryStore

SEED = Path(__file__).resolve().parents[2] / "seed" / "orders.json"
POLICIES = Path(__file__).resolve().parents[2] / "seed" / "policies" / "policies.json"


def _meta() -> ResultMeta:
    return ResultMeta(trace_id="t", request_id="r")


@pytest.fixture
def store():
    return MemoryStore(SEED, lock_shards=4)


# ---------- 状态矩阵 ----------

@pytest.mark.parametrize(
    "order_id,status",
    [
        ("ORD1001", OrderStatus.PENDING_PAYMENT),
        ("ORD1003", OrderStatus.SHIPPED),
        ("ORD1004", OrderStatus.DELIVERED),
        ("ORD1005", OrderStatus.CLOSED),
        ("ORD1006", OrderStatus.REFUNDING),
        ("ORD1007", OrderStatus.REFUNDED),
    ],
)
async def test_only_paid_orders_can_be_urged(store, order_id, status):
    result = await urge_service.create_urge(store, order_id, "req-1", _meta())
    assert result.code is ResultCode.ORDER_STATUS_NOT_ALLOWED
    assert store.find_urge_by_request(urge_service.ACTION, "req-1") is None


async def test_paid_order_is_accepted(store):
    result = await urge_service.create_urge(store, "ORD1002", "req-ok", _meta())
    assert result.code is ResultCode.OK
    assert result.data.order_id == "ORD1002"
    assert result.data.urge_id.startswith("UG")


async def test_unknown_order(store):
    result = await urge_service.create_urge(store, "ORD9999", "req-1", _meta())
    assert result.code is ResultCode.ORDER_NOT_FOUND


async def test_missing_request_id_is_contract_error(store):
    result = await urge_service.create_urge(store, "ORD1002", "", _meta())
    assert result.code is ResultCode.INVALID_REQUEST


# ---------- 幂等 ----------

async def test_same_request_id_returns_first_result(store):
    first = await urge_service.create_urge(store, "ORD1002", "req-same", _meta())
    second = await urge_service.create_urge(store, "ORD1002", "req-same", _meta())
    assert first.code is ResultCode.OK
    assert second.code is ResultCode.DUPLICATE_REQUEST
    assert second.data.urge_id == first.data.urge_id


async def test_dedupe_window_blocks_repeat_with_new_request_id(store):
    """用户连点会生成新 request_id，幂等键挡不住，必须靠时间窗。"""
    first = await urge_service.create_urge(
        store, "ORD1002", "req-a", _meta(), dedupe_window_s=3600
    )
    second = await urge_service.create_urge(
        store, "ORD1002", "req-b", _meta(), dedupe_window_s=3600
    )
    assert first.code is ResultCode.OK
    assert second.code is ResultCode.DUPLICATE_REQUEST
    assert second.data.urge_id == first.data.urge_id


async def test_dedupe_window_expires(store):
    """窗口过后可以重新催——催单本来就该允许多次。"""
    base = datetime.now(timezone.utc)
    first = await urge_service.create_urge(
        store, "ORD1002", "req-1", _meta(), dedupe_window_s=60, now=base
    )
    later = base + timedelta(seconds=120)
    second = await urge_service.create_urge(
        store, "ORD1002", "req-2", _meta(), dedupe_window_s=60, now=later
    )
    assert first.code is ResultCode.OK
    assert second.code is ResultCode.OK
    assert second.data.urge_id != first.data.urge_id


async def test_zero_window_disables_dedupe(store):
    first = await urge_service.create_urge(
        store, "ORD1002", "req-1", _meta(), dedupe_window_s=0
    )
    second = await urge_service.create_urge(
        store, "ORD1002", "req-2", _meta(), dedupe_window_s=0
    )
    assert first.code is ResultCode.OK and second.code is ResultCode.OK


# ---------- 并发 ----------

async def test_concurrent_same_request_id_writes_once(store):
    results = await asyncio.gather(
        *(urge_service.create_urge(store, "ORD1002", "req-conc", _meta()) for _ in range(10))
    )
    ok = [r for r in results if r.code is ResultCode.OK]
    dup = [r for r in results if r.code is ResultCode.DUPLICATE_REQUEST]
    assert len(ok) == 1, "并发下只应产生一条催单"
    assert len(dup) == 9
    assert {r.data.urge_id for r in results} == {ok[0].data.urge_id}


async def test_concurrent_different_request_ids_dedupe_by_order(store):
    results = await asyncio.gather(
        *(
            urge_service.create_urge(
                store, "ORD1002", f"req-{i}", _meta(), dedupe_window_s=3600
            )
            for i in range(10)
        )
    )
    assert sum(1 for r in results if r.code is ResultCode.OK) == 1


# ---------- 时效 ----------

async def test_deadline_uses_configured_response_hours(store):
    result = await urge_service.create_urge(
        store, "ORD1002", "req-dl", _meta(), response_hours=24
    )
    delta = result.data.merchant_deadline_at - result.data.created_at
    assert delta == timedelta(hours=24)


async def test_urge_does_not_change_order_status(store):
    await urge_service.create_urge(store, "ORD1002", "req-1", _meta())
    assert store.get_order("ORD1002").status is OrderStatus.PAID


# ---------- 配置与条款同源 ----------

def test_clause_params_match_settings():
    """条款里写的时效必须和代码用的配置一致，否则话术与条款会互相矛盾。"""
    clauses = json.loads(POLICIES.read_text(encoding="utf-8"))
    clause = next(c for c in clauses if c["clause_id"] == "LOGISTICS-01")
    params = clause.get("params") or {}
    assert params.get("sla_hours") == settings.shipping_sla_hours
    assert params.get("urge_response_hours") == settings.urge_response_hours

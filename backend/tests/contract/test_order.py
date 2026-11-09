"""T021 [US1]: 订单查询契约测试。

覆盖 FR-001（查询订单）、FR-002（结果状态可区分）、FR-006（未找到 vs 失败）、
FR-006a（按类别分流）。
"""
from app.models.enums import OrderStatus, ResultCode


async def test_existing_order_returns_ok(client):
    r = await client.get("/orders/ORD1003")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == ResultCode.OK
    assert body["data"]["order_id"] == "ORD1003"
    assert body["data"]["status"] == OrderStatus.SHIPPED
    assert body["data"]["amount"] == "128.00"
    assert body["data"]["address"]["receiver"] == "王五"


async def test_every_seeded_status_is_queryable(client):
    expected = {
        "ORD1001": OrderStatus.PENDING_PAYMENT,
        "ORD1002": OrderStatus.PAID,
        "ORD1003": OrderStatus.SHIPPED,
        "ORD1004": OrderStatus.DELIVERED,
        "ORD1005": OrderStatus.CLOSED,
        "ORD1006": OrderStatus.REFUNDING,
        "ORD1007": OrderStatus.REFUNDED,
    }
    for order_id, status in expected.items():
        body = (await client.get(f"/orders/{order_id}")).json()
        assert body["code"] == ResultCode.OK, order_id
        assert body["data"]["status"] == status, order_id


async def test_missing_order_returns_not_found_not_error(client):
    """FR-006：没有这个订单 ≠ 没查成。必须落在 2xxx 而非 4xxx。"""
    r = await client.get("/orders/DOES_NOT_EXIST")
    assert r.status_code == 200, "业务失败不改 HTTP 状态"
    body = r.json()
    assert body["code"] == ResultCode.ORDER_NOT_FOUND
    assert body["data"] is None
    assert body["reason"] is None, "未找到不是拒绝，不需要 reason"


async def test_order_codes_fall_in_expected_ranges(client):
    """FR-006a：调用方按千位分流。"""
    ok_body = (await client.get("/orders/ORD1002")).json()
    nf_body = (await client.get("/orders/NOPE")).json()

    assert 1000 <= ok_body["code"] < 2000
    assert 2000 <= nf_body["code"] < 3000


async def test_response_carries_trace_meta(client):
    body = (await client.get("/orders/ORD1002")).json()
    assert body["meta"]["trace_id"]


async def test_trace_id_is_echoed_in_meta_and_header(client):
    r = await client.get("/orders/ORD1002", headers={"X-Trace-Id": "deadbeef" * 4})
    assert r.headers["x-trace-id"] == "deadbeef" * 4
    assert r.json()["meta"]["trace_id"] == "deadbeef" * 4


async def test_call_is_recorded(client):
    from app.api import routes

    trace = "t" * 32
    await client.get("/orders/ORD1002", headers={"X-Trace-Id": trace})
    # recorder 挂在 app 上，通过 routes 访问不方便，改为直接查 debug 端点
    debug = await client.get("/_debug/calls", params={"trace_id": trace})
    assert debug.status_code == 200
    records = debug.json()["records"]
    assert any(r["action"] == "order.get" for r in records)

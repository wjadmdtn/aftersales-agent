"""T032 [US2]: 退款契约测试。

覆盖 US2 场景 1、4、5 与金额边界。
"""
from app.models.enums import OrderStatus, ResultCode


def _refund(client, order_id, amount, rid="rid-1"):
    return client.post(
        f"/orders/{order_id}/refund",
        json={"amount": amount},
        headers={"X-Request-Id": rid},
    )


async def test_refund_paid_order_succeeds(client):
    body = (await _refund(client, "ORD1002", "50.00")).json()
    assert body["code"] == ResultCode.OK
    assert body["data"]["refund_id"].startswith("RF")
    assert body["data"]["amount"] == "50.00"

    order = (await client.get("/orders/ORD1002")).json()
    assert order["data"]["status"] == OrderStatus.REFUNDED


async def test_refund_whole_amount_succeeds(client):
    body = (await _refund(client, "ORD1008", "500.00")).json()
    assert body["code"] == ResultCode.OK


async def test_refund_exceeding_by_one_cent_rejected(client):
    body = (await _refund(client, "ORD1008", "500.01")).json()
    assert body["code"] == ResultCode.AMOUNT_EXCEEDS_ORDER
    assert 3000 <= body["code"] < 4000
    assert body["reason"]

    order = (await client.get("/orders/ORD1008")).json()
    assert order["data"]["status"] == OrderStatus.PAID, "拒绝后订单状态不得变化"


async def test_refund_already_refunded_order_rejected(client):
    body = (await _refund(client, "ORD1007", "10.00")).json()
    assert body["code"] == ResultCode.ORDER_STATUS_NOT_ALLOWED
    assert body["reason"]


async def test_refund_pending_payment_rejected(client):
    body = (await _refund(client, "ORD1001", "10.00")).json()
    assert body["code"] == ResultCode.ORDER_STATUS_NOT_ALLOWED


async def test_refund_closed_order_rejected(client):
    body = (await _refund(client, "ORD1005", "10.00")).json()
    assert body["code"] == ResultCode.ORDER_STATUS_NOT_ALLOWED


async def test_refund_unknown_order(client):
    body = (await _refund(client, "NOPE", "10.00")).json()
    assert body["code"] == ResultCode.ORDER_NOT_FOUND


async def test_refund_missing_request_id_rejected(client):
    body = (await client.post("/orders/ORD1002/refund", json={"amount": "10.00"})).json()
    assert body["code"] == ResultCode.INVALID_REQUEST


async def test_refund_zero_amount_rejected_by_schema(client):
    r = await client.post(
        "/orders/ORD1002/refund", json={"amount": "0"}, headers={"X-Request-Id": "z"}
    )
    assert r.status_code == 422, "金额 0 在入参层即被拒绝"


async def test_refund_negative_amount_rejected_by_schema(client):
    r = await client.post(
        "/orders/ORD1002/refund", json={"amount": "-1.00"}, headers={"X-Request-Id": "n"}
    )
    assert r.status_code == 422


async def test_refund_three_decimal_places_rejected_by_schema(client):
    r = await client.post(
        "/orders/ORD1002/refund", json={"amount": "1.001"}, headers={"X-Request-Id": "d"}
    )
    assert r.status_code == 422

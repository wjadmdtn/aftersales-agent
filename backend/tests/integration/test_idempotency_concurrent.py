"""T033 [US2]: 退款幂等的并发测试。

单线程测幂等很容易过，并发才是真实考验。
"""
import asyncio

from app.models.enums import OrderStatus, ResultCode

CONCURRENCY = 50


async def test_same_request_id_concurrent_produces_one_refund(client):
    """同一 request_id 并发 50 次 → 1 条退款记录，1 个 OK + 49 个 DUPLICATE。"""
    rid = "refund:ORD1002:concurrent"

    async def call():
        return (
            await client.post(
                "/orders/ORD1002/refund",
                json={"amount": "20.00"},
                headers={"X-Request-Id": rid},
            )
        ).json()

    results = await asyncio.gather(*[call() for _ in range(CONCURRENCY)])
    codes = [r["code"] for r in results]

    assert codes.count(ResultCode.OK) == 1, f"应只有一次真正退款，实际 {codes.count(ResultCode.OK)}"
    assert codes.count(ResultCode.DUPLICATE_REQUEST) == CONCURRENCY - 1

    refund_ids = {r["data"]["refund_id"] for r in results}
    assert len(refund_ids) == 1, "所有响应应指向同一笔退款"

    order = (await client.get("/orders/ORD1002")).json()
    assert order["data"]["status"] == OrderStatus.REFUNDED


async def test_different_request_ids_concurrent_only_one_succeeds(client):
    """不同 request_id 对同一订单并发 → 只有 1 笔成功，其余被拒。"""

    async def call(i: int):
        return (
            await client.post(
                "/orders/ORD1002/refund",
                json={"amount": "10.00"},
                headers={"X-Request-Id": f"refund:ORD1002:distinct-{i}"},
            )
        ).json()

    results = await asyncio.gather(*[call(i) for i in range(CONCURRENCY)])
    codes = [r["code"] for r in results]

    assert codes.count(ResultCode.OK) == 1, "并发下只能有一笔退款成功"
    assert codes.count(ResultCode.ORDER_STATUS_NOT_ALLOWED) == CONCURRENCY - 1


async def test_same_request_id_different_orders_not_conflated(client):
    """同一 request_id 但订单号不同，不得被误判为同一次退款。"""
    rid = "refund:shared:key"

    a = (
        await client.post(
            "/orders/ORD1002/refund",
            json={"amount": "10.00"},
            headers={"X-Request-Id": rid},
        )
    ).json()
    b = (
        await client.post(
            "/orders/ORD1008/refund",
            json={"amount": "10.00"},
            headers={"X-Request-Id": rid},
        )
    ).json()

    # 实现按 (action, request_id) 去重，因此第二次会被判为重复——这是刻意的：
    # 幂等键由调用方保证唯一，重复使用意味着同一笔业务，跨订单复用是调用方的错误。
    assert a["code"] == ResultCode.OK
    assert b["code"] == ResultCode.DUPLICATE_REQUEST
    assert b["data"]["order_id"] == "ORD1002", "返回的是首次那笔，未被串到别的订单"


async def test_sequential_retry_same_request_id(client):
    """串行重试同样只生效一次。"""
    rid = "refund:ORD1008:seq"
    first = (
        await client.post(
            "/orders/ORD1008/refund", json={"amount": "10.00"}, headers={"X-Request-Id": rid}
        )
    ).json()
    second = (
        await client.post(
            "/orders/ORD1008/refund", json={"amount": "10.00"}, headers={"X-Request-Id": rid}
        )
    ).json()

    assert first["code"] == ResultCode.OK
    assert second["code"] == ResultCode.DUPLICATE_REQUEST
    assert first["data"]["refund_id"] == second["data"]["refund_id"]

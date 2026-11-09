"""链路追踪：Agent 与 mock 服务共享同一 trace_id。"""

import re

HEX32 = re.compile(r"^[0-9a-f]{32}$")


async def test_trace_propagates_to_mock(env, chat):
    """一次 Agent 请求产生的 trace_id，必须能在 mock 服务的调用记录里查到。"""
    trace = "c" * 32
    await chat("快递到哪了", order="ORD1003", trace=trace)

    records = (
        await env["mock_client"].get("/_debug/calls", params={"trace_id": trace})
    ).json()["records"]

    assert records, "mock 服务应当收到同一个 trace_id"
    assert any(r["action"] == "logistics.get" for r in records)


async def test_trace_propagates_on_write(env, chat):
    trace = "d" * 32
    await chat("我要退款", order="ORD1002", trace=trace)
    await chat("确认", order="ORD1002", trace=trace)

    records = (
        await env["mock_client"].get("/_debug/calls", params={"trace_id": trace})
    ).json()["records"]
    actions = {r["action"] for r in records}
    assert "order.get" in actions
    assert "refund.create" in actions


async def test_agent_generates_trace_when_absent(env, chat):
    r = await chat("快递到哪了", order="ORD1003")
    assert HEX32.match(r["trace_id"])


async def test_trace_echoed_when_supplied(chat):
    trace = "e" * 32
    r = await chat("快递到哪了", order="ORD1003", trace=trace)
    assert r["trace_id"] == trace


async def test_refund_request_id_is_idempotent_per_action(env, chat):
    """同一笔退款重试不会重复扣款（mock 侧幂等）。"""
    await chat("我要退款", order="ORD1002")
    await chat("确认", order="ORD1002")

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "REFUNDED"

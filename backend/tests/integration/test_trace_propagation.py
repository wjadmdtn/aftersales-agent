"""T028 [US4]: 链路追踪传播测试。

覆盖 FR-017 / FR-018、SC-007 / SC-008：
同一笔业务重试 3 次 → 3 个不同 trace_id、同一 request_id、attempt 1/2/3，
但业务数据只被修改一次。
"""
import re

from app.models.enums import ResultCode

HEX32 = re.compile(r"^[0-9a-f]{32}$")


async def test_trace_id_generated_when_absent(client):
    r = await client.get("/orders/ORD1002")
    trace = r.headers["x-trace-id"]
    assert HEX32.match(trace), trace
    assert r.json()["meta"]["trace_id"] == trace


async def test_trace_id_echoed_when_supplied(client):
    supplied = "0123456789abcdef0123456789abcdef"
    r = await client.get("/orders/ORD1002", headers={"X-Trace-Id": supplied})
    assert r.headers["x-trace-id"] == supplied
    assert r.json()["meta"]["trace_id"] == supplied


async def test_retry_keeps_request_id_but_changes_trace(client):
    """SC-008：重试 3 次，trace 不同、request_id 相同、attempt 递增。"""
    rid = "refund:ORD1002:fixed-request"
    traces = []
    for _ in range(3):
        r = await client.post(
            "/orders/ORD1002/refund",
            json={"amount": "10.00"},
            headers={"X-Request-Id": rid},
        )
        traces.append(r.headers["x-trace-id"])

    assert len(set(traces)) == 3, "每次请求的 trace 应当不同"

    records = (
        await client.get("/_debug/calls", params={"request_id": rid})
    ).json()["records"]
    refund_records = [r for r in records if r["action"] == "refund.create"]
    assert len(refund_records) == 3
    assert {r["trace_id"] for r in refund_records} == set(traces)
    assert sorted(r["attempt"] for r in refund_records) == [1, 2, 3]


async def test_business_modified_once_across_retries(client):
    """重试 3 次，业务数据只改一次。"""
    rid = "refund:ORD1008:retry-once"
    codes = []
    for _ in range(3):
        body = (
            await client.post(
                "/orders/ORD1008/refund",
                json={"amount": "10.00"},
                headers={"X-Request-Id": rid},
            )
        ).json()
        codes.append(body["code"])

    assert codes[0] == ResultCode.OK
    assert all(c == ResultCode.DUPLICATE_REQUEST for c in codes[1:])

    records = (
        await client.get("/_debug/calls", params={"request_id": rid})
    ).json()["records"]
    refund_records = [r for r in records if r["action"] == "refund.create"]
    assert len(refund_records) == 3


async def test_debug_query_by_trace_returns_single_request(client):
    trace = "a" * 32
    await client.get("/orders/ORD1002", headers={"X-Trace-Id": trace})
    await client.get("/orders/ORD1002/logistics", headers={"X-Trace-Id": trace})
    await client.get("/orders/ORD1003", headers={"X-Trace-Id": "b" * 32})

    records = (
        await client.get("/_debug/calls", params={"trace_id": trace})
    ).json()["records"]
    assert len(records) == 2
    assert {r["action"] for r in records} == {"order.get", "logistics.get"}

    other = (
        await client.get("/_debug/calls", params={"trace_id": "b" * 32})
    ).json()["records"]
    assert len(other) == 1

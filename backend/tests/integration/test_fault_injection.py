"""T027 [US4]: 故障注入集成测试。

核心断言是**一致性**：timeout/error 时业务必须没执行，delay/dirty 时业务必须执行了。
顺序写反了表面行为看着一样，语义完全不同。
"""
import time

import pytest

from app.models.enums import OrderStatus, ResultCode


async def test_no_fault_header_means_normal_behaviour(client):
    body = (await client.get("/orders/ORD1002")).json()
    assert body["code"] == ResultCode.OK


async def test_delay_executes_business_then_slows(client):
    """delay：业务已执行，只是返回慢。"""
    started = time.perf_counter()
    r = await client.get(
        "/orders/ORD1002", headers={"X-Fault": '{"type":"delay","delay_ms":300}'}
    )
    elapsed = time.perf_counter() - started
    assert r.json()["code"] == ResultCode.OK, "业务应当执行成功"
    assert elapsed >= 0.3


async def test_timeout_does_not_execute_business(client):
    """timeout：业务不得执行。用改地址来验证——地址必须没变。"""
    r = await client.put(
        "/orders/ORD1002/address",
        json={
            "receiver": "新收件人",
            "phone": "13900139000",
            "province": "浙江省",
            "city": "杭州市",
            "district": "西湖区",
            "detail": "新地址1号",
        },
        headers={"X-Fault": '{"type":"timeout","delay_ms":5000}'},
    )
    # 调用方超时（这里直接拿到 504）
    assert r.status_code in (504, 408)

    after = (await client.get("/orders/ORD1002")).json()
    assert after["data"]["address"]["receiver"] == "李四", "超时不得留下副作用"


async def test_error_does_not_execute_business(client):
    """error：业务不得执行，返回 4xxx 系统异常码。"""
    r = await client.put(
        "/orders/ORD1002/address",
        json={
            "receiver": "另一个收件人",
            "phone": "13900139000",
            "province": "浙江省",
            "city": "杭州市",
            "district": "西湖区",
            "detail": "地址2号",
        },
        headers={"X-Fault": '{"type":"error","status":503}'},
    )
    assert r.status_code == 503
    assert r.json()["code"] == ResultCode.INTERNAL_ERROR

    after = (await client.get("/orders/ORD1002")).json()
    assert after["data"]["address"]["receiver"] == "李四"


async def test_dirty_mutates_response_after_business(client):
    """dirty：业务已执行，但响应字段被篡改。"""
    body = (
        await client.get(
            "/orders/ORD1003", headers={"X-Fault": '{"type":"dirty","field":"data.status"}'}
        )
    ).json()
    assert body["code"] == ResultCode.OK
    assert body["data"]["status"] is None, "字段应被置空"


async def test_invalid_fault_header_returns_invalid_request(client):
    body = (await client.get("/orders/ORD1002", headers={"X-Fault": "not json"})).json()
    assert body["code"] == ResultCode.INVALID_REQUEST


async def test_fault_appears_in_call_record(client):
    trace = "f" * 32
    await client.get(
        "/orders/ORD1002",
        headers={"X-Trace-Id": trace, "X-Fault": '{"type":"delay","delay_ms":10}'},
    )
    records = (
        await client.get("/_debug/calls", params={"trace_id": trace})
    ).json()["records"]
    assert records and records[0]["fault_injected"] == "delay"


async def test_refund_succeeds_normally_after_fault_window(client):
    """故障只作用于带头的请求，后续请求不受影响。"""
    await client.get("/orders/ORD1002", headers={"X-Fault": '{"type":"delay","delay_ms":10}'})
    body = (await client.get("/orders/ORD1002")).json()
    assert body["code"] == ResultCode.OK
    assert body["data"]["status"] == OrderStatus.PAID

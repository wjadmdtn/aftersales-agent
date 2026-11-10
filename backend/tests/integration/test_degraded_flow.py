"""降级端到端：重试耗尽后统一话术，零写操作。"""

from app.agent.degrade import DEGRADED_REPLY


async def test_degraded_returns_uniform_script(env, chat):
    env["stub"].fail_mode = "timeout"
    r = await chat("我要退款")
    assert r["source"] == "degraded"
    assert r["state"] == "degraded"
    assert r["reply"] == DEGRADED_REPLY


async def test_degraded_blocks_write(env, chat):
    env["stub"].fail_mode = "error"
    await chat("我要退款")
    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "PAID"


async def test_degraded_also_blocks_read(env, chat):
    """不再区分只读/写：区分本身就要求判断意图，而意图判断正不可用。"""
    env["stub"].fail_mode = "timeout"
    r = await chat("快递到哪了", order="ORD1003")
    assert r["reply"] == DEGRADED_REPLY
    assert r["tool_calls"] == []


async def test_retries_before_degrading(env, chat):
    """前两次失败、第三次成功：不该降级。"""
    env["stub"].fail_mode = "timeout"
    env["stub"].fail_times = 2
    r = await chat("我要退款")
    assert r["source"] != "degraded", "重试应救回这次请求"
    assert env["stub"].call_count == 3


async def test_every_request_retries_llm(env, chat):
    """SC-004a：上次失败不影响下次尝试。"""
    env["stub"].fail_mode = "timeout"
    env["stub"].fail_times = 3
    r1 = await chat("我要退款")
    assert r1["source"] == "degraded"

    env["stub"].fail_mode = None
    r2 = await chat("我要退款")
    assert r2["source"] == "llm"


async def test_degraded_script_does_not_promise(env, chat):
    env["stub"].fail_mode = "invalid"
    r = await chat("我要退款")
    for w in ("退款", "退换", "补偿", "分钟", "小时"):
        assert w not in r["reply"], w

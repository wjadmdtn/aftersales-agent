"""降级路径的端到端验证：LLM 不可用时零写操作。"""

from app.agent import degrade


async def test_degraded_blocks_refund(env, chat):
    env["stub"].fail_mode = "timeout"
    r = await chat("我要退款")
    assert r["source"] == "degraded"
    assert r["state"] == "degraded_write_blocked"
    assert r["tool_calls"] == []

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "PAID", "降级期不得动钱"


async def test_degraded_blocks_address_change(env, chat):
    env["stub"].fail_mode = "error"
    r = await chat("帮我改地址")
    assert r["state"] == "degraded_write_blocked"


async def test_degraded_allows_read(env, chat):
    env["stub"].fail_mode = "timeout"
    r = await chat("到哪了", order="ORD1003")
    assert r["source"] == "degraded"
    assert r["state"] == "degraded_read"
    assert "order.get" in r["tool_calls"]


async def test_degraded_tells_user_capability_limited(chat, env):
    env["stub"].fail_mode = "invalid"
    r = await chat("我要退款")
    assert "暂时无法处理" in r["reply"]


async def test_every_request_retries_llm(env, chat):
    """SC-004a：上次失败不影响下次调用 LLM。"""
    env["stub"].fail_mode = "timeout"
    env["stub"].fail_times = 1

    r1 = await chat("我要退款")
    assert r1["source"] == "degraded"

    r2 = await chat("我要退款")
    assert r2["source"] == "llm", "第二次请求必须仍然调用 LLM"
    assert env["stub"].call_count == 2


async def test_degraded_confirm_word_does_not_execute(env, chat):
    """降级期即使用户说'确认'也不得执行写操作。"""
    env["stub"].fail_mode = "timeout"
    await chat("我要退款")
    r = await chat("确认")
    assert r["state"] in ("degraded_write_blocked", "degraded_read")

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "PAID"

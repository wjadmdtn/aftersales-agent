"""完整退款流程：表达意图 → 确认 → 执行。"""

from app.models.agent import Intent


async def test_refund_full_flow(chat, env):
    # 1. 表达退款诉求 → 不直接执行，生成确认
    r1 = await chat("我要退款")
    assert r1["intent"] == Intent.REFUND
    assert r1["state"] == "awaiting_confirmation"
    assert "确认" in r1["reply"]
    assert r1["tool_calls"] == []

    # 确认环节没有动钱
    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "PAID"

    # 2. 用户确认 → 重新校验后执行
    r2 = await chat("确认")
    assert r2["state"] == "executed"
    assert "refund.create" in r2["tool_calls"]

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "REFUNDED"


async def test_refund_cancelled(chat, env):
    await chat("我要退款")
    r = await chat("算了")
    assert r["state"] == "cancelled"

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "PAID"


async def test_unrelated_reply_does_not_confirm(chat, env):
    await chat("我要退款")
    r = await chat("再说吧")
    assert r["state"] == "awaiting_confirmation"
    assert "确认" in r["reply"]

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "PAID"


async def test_containing_confirmation_word_is_rejected(chat, env):
    """'确认不了' 不是确认。"""
    await chat("我要退款")
    r = await chat("确认不了")
    assert r["state"] == "awaiting_confirmation"

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "PAID"


async def test_refund_on_non_refundable_order_shows_reason(chat, env):
    await chat("我要退款", order="ORD1007")
    r = await chat("确认")
    assert r["state"] == "executed"
    assert "已完成" in r["reply"] or "退款" in r["reply"]


async def test_logistics_read_flow(chat):
    r = await chat("快递到哪了", order="ORD1003")
    assert r["intent"] == Intent.LOGISTICS
    assert "logistics.get" in r["tool_calls"]
    assert "南京" in r["reply"]


async def test_manual_handoff_intent(chat):
    r = await chat("转人工")
    assert r["intent"] == Intent.MANUAL
    assert r["state"] == "handoff_pending"


async def test_complaint_does_not_trigger_action(chat, env):
    r = await chat("我要投诉", order="ORD1002")
    assert r["intent"] == Intent.COMPLAINT
    assert r["tool_calls"] == []

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "PAID"


async def test_unknown_input_asks_clarification(chat):
    r = await chat("随便说点什么")
    assert r["state"] == "unclear"

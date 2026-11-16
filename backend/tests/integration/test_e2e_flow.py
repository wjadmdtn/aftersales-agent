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
    """已退款订单不得再走确认流程。

    旧行为：弹卡片 → 用户确认 → 调退款接口才被服务端拒，用户白点一次。
    新行为：第一步就拒绝，不生成待确认操作。
    """
    r1 = await chat("我要退款", order="ORD1007")
    assert r1["state"] == "write_rejected"
    assert "退款完成" in r1["reply"]

    r2 = await chat("确认", order="ORD1007")
    assert r2["state"] != "executed"

    order = (await env["mock_client"].get("/orders/ORD1007")).json()
    assert order["data"]["status"] == "REFUNDED"


async def test_logistics_read_flow(chat):
    r = await chat("快递到哪了", order="ORD1003")
    assert r["intent"] == Intent.LOGISTICS
    assert "logistics.get" in r["tool_calls"]
    assert "南京" in r["reply"]


async def test_change_address_without_new_address_asks_first(chat, env):
    """没说新地址就不弹确认——否则用户确认的是个空地址，服务端必拒。"""
    r = await chat("我要改地址", order="ORD1002")
    assert r["intent"] == Intent.CHANGE_ADDRESS
    assert r["state"] == "need_address"
    assert "新的收货人" in r["reply"]

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["address"]["receiver"] != ""


async def test_change_address_with_address_shows_confirmation(chat, env):
    """带了地址信息才弹确认，且确认文案里能看到地址内容。"""
    r = await chat("我要改成别的地址", order="ORD1002")
    assert r["state"] == "need_address"

    r2 = await chat("改为北京市朝阳区建国路4号 13800138000", order="ORD1002")
    assert r2["state"] == "awaiting_confirmation"
    assert "确认" in r2["reply"]


async def test_exchange_gives_self_service_path(chat, env):
    """换货不由 AI 办理：告知条件 + 自助入口，不弹确认、不调工具、不改订单。"""
    r = await chat("我想换个货", order="ORD1002")
    assert r["intent"] == Intent.EXCHANGE
    assert r["tool_calls"] == []
    # ORD1002 未发货：不该说"可以换"，应告知还没到换货环节
    assert r["state"] == "exchange_not_applicable"
    assert "还没发货" in r["reply"] or "运输中" in r["reply"]

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "PAID"


async def test_exchange_delivered_within_window(chat, env):
    """已签收且在 7 天内：给出换货条件与自助入口。"""
    r = await chat("我想换个尺码", order="ORD1004")
    assert r["intent"] == Intent.EXCHANGE
    assert r["state"] in ("exchange_available", "exchange_expired")
    assert "我的订单" in r["reply"] or "超过 7 天" in r["reply"]


async def test_manual_handoff_intent(chat):
    """003 起：转人工进入队列，不再只是打个标记。"""
    r = await chat("转人工")
    assert r["intent"] == Intent.MANUAL
    assert r["state"] == "handoff_queued"
    assert "人工客服" in r["reply"]


async def test_complaint_does_not_trigger_action(chat, env):
    """投诉不转人工、不调工具、不动钱（2026-11-13 口径：只由用户主动要人工才转）。"""
    r = await chat("我要投诉", order="ORD1002")
    assert r["intent"] == Intent.UNKNOWN
    assert r["tool_calls"] == []
    assert r["state"] == "clarifying"
    assert "抱歉" in r["reply"]

    order = (await env["mock_client"].get("/orders/ORD1002")).json()
    assert order["data"]["status"] == "PAID"


async def test_unknown_input_introduces_capability(chat):
    """闲聊/问能力：介绍能做什么，不道歉、不转人工。"""
    r = await chat("随便说点什么")
    assert r["state"] == "clarifying"
    assert "售后客服助手" in r["reply"]
    assert "抱歉" not in r["reply"], "无关输入不道歉"


async def test_negative_input_gets_apology(chat):
    """用户表达不满时先道歉，而不是说"我没理解"。"""
    r = await chat("这个东西不好用")
    assert r["state"] == "clarifying"
    assert r["reply"].startswith("非常抱歉给您带来了不好的体验。")


async def test_clarify_never_promises_business_action(chat):
    """澄清话术不得出现退款、退换、补偿等业务承诺。"""
    banned = ["退款", "退货", "退换", "换货", "补发", "补偿", "赔付", "帮您申请"]
    r = await chat("这个东西不好用")
    for w in banned:
        assert w not in r["reply"], f"澄清话术出现业务承诺「{w}」：{r['reply']}"


async def test_second_clarify_keeps_asking_not_handoff(chat):
    """第 2 轮继续追问，**不再征求转人工**（口径 2026-11-12）。

    抱怨类不该被推向人工：只有明确投诉 / 要人工 / 差评才转。
    """
    await chat("这个东西不好用")
    r = await chat("嗯……")
    assert r["state"] == "clarifying"
    assert "人工" not in r["reply"], f"第 2 轮不该提示转人工：{r['reply']}"
    assert "1. 查询订单" not in r["reply"]


async def test_many_clarify_rounds_never_auto_handoff(env, chat):
    """澄清多少轮都不自动转人工——抱怨不是转人工的条件。"""
    await chat("随便说点什么")
    await chat("还是不懂")
    r = await chat("嗯")
    assert r["state"] == "clarifying", "抱怨/说不清不该被自动转人工"
    r2 = await chat("真的不知道")
    assert r2["state"] == "clarifying"


async def test_manual_request_skips_clarification(chat):
    """用户直接要人工时不澄清。"""
    r = await chat("转人工")
    assert r["state"] == "handoff_queued"

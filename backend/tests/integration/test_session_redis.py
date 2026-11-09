"""会话状态：Redis 存储、跨请求恢复、订单切换。"""

from app.models.agent import Intent


async def test_session_persists_across_requests(env, chat):
    await chat("我要退款", order="ORD1002")
    state = await env["store"].get("s1")
    assert state.order_id == "ORD1002"
    assert state.turn_count == 1


async def test_pending_survives_between_turns(env, chat):
    await chat("我要退款", order="ORD1002")
    pending = await env["store"].get_pending("s1")
    assert pending is not None and pending.action == "refund"


async def test_pending_cleared_after_confirm(env, chat):
    await chat("我要退款", order="ORD1002")
    await chat("确认", order="ORD1002")
    assert await env["store"].get_pending("s1") is None


async def test_order_switch(env, chat):
    await chat("快递到哪了", order="ORD1003")
    state = await env["store"].get("s1")
    assert state.order_id == "ORD1003"

    r = await chat("ORD1008 呢", order="ORD1003")
    state = await env["store"].get("s1")
    assert state.order_id == "ORD1008"


async def test_switch_to_missing_order_keeps_original(env, chat):
    await chat("快递到哪了", order="ORD1003")
    r = await chat("ORD999999 呢", order="ORD1003")
    state = await env["store"].get("s1")
    assert state.order_id == "ORD1003", "不存在的订单不能覆盖原上下文"
    assert "没有查到" in r["reply"] or "没查到" in r["reply"]


async def test_turn_count_increments(env, chat):
    await chat("快递到哪了", order="ORD1003")
    await chat("快递到哪了", order="ORD1003")
    state = await env["store"].get("s1")
    assert state.turn_count == 2


async def test_snapshot_mismatch_recorded(env, chat):
    """前端快照金额与库不一致时必须留痕，业务以库为准。"""
    r = await chat(
        "订单状态",
        order="ORD1002",
        snapshot={"name": "耳机", "amount": "199.00"},
    )
    # 库里是 259.90，快照是 199.00
    state = await env["store"].get("s1")
    assert state.snapshot.amount == 199
    assert "259.90" in r["reply"], "回复必须用库里的金额"

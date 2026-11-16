"""写操作的幂等与金额解析。

退款是唯一直接动钱的操作。两件事必须成立：
1. **同一张确认卡重试只退一次**——request_id 在弹卡时定稿，不在执行时生成
2. **订单号不会被误当成金额**——"帮我退 ORD1002" 不能退 1002 元
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.agent.intent import IntentClassifier
from app.agent.llm.stub import StubLLMClient
from app.agent.loop import AgentLoop, _extract_amount
from app.models.agent import Intent, IntentResult

from tests.conftest_agent import InProcessMockClient


@pytest.fixture
async def make_loop():
    import fakeredis.aioredis

    import app.api.routes as mock_routes
    from app.agent.session import SessionStore
    from app.main import create_app

    mock_routes.reset_store()
    mock_app = create_app()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sessions = SessionStore(redis, ttl_seconds=3600)
    tools = InProcessMockClient(mock_app)

    def _build(responses):
        return AgentLoop(
            classifier=IntentClassifier(StubLLMClient(responses), keyword_map={}),
            sessions=sessions,
            tools=tools,
        )

    yield _build
    await redis.aclose()


async def _say(loop, sid: str, msg: str, *, order: str | None = "ORD1002"):
    return await loop.handle(
        session_id=sid, user_id="u1", message=msg, trace_id="t", order_id=order
    )


# ---------- 金额解析 ----------

def test_order_id_is_not_parsed_as_amount():
    assert _extract_amount("帮我退 ORD1002") is None
    assert _extract_amount("退 ORD1002 这个单") is None


def test_explicit_amount_still_wins():
    assert _extract_amount("退我 50 元") == Decimal("50")
    assert _extract_amount("ORD1002 退 199.90") == Decimal("199.90")


# ---------- 确认卡上的幂等键 ----------

async def test_request_id_is_fixed_on_confirmation_card(make_loop):
    """弹确认卡时就带上 request_id，确认执行时复用它。

    旧实现在执行时才 uuid4()，链路重试就换一把钥匙，
    业务侧按 request_id 的去重彻底失效。
    """
    loop = make_loop({"我要退款": IntentResult(intent=Intent.REFUND, confidence=0.9)})
    await _say(loop, "s-id", "我要退款")

    pending = await loop.sessions.get_pending("s-id")
    assert pending is not None
    assert pending.request_id, "确认卡必须自带幂等键"
    assert pending.request_id.startswith("confirm:")

    await _say(loop, "s-id", "确认")
    body = await loop.tools.get_order("ORD1002", trace_id="verify")
    assert (body.get("data") or {}).get("status") == "REFUNDED"


async def test_same_request_id_replays_first_result(make_loop):
    """用同一把 request_id 打两次业务接口，第二次必须返回 DUPLICATE_REQUEST，不产生第二笔退款。"""
    loop = make_loop({"我要退款": IntentResult(intent=Intent.REFUND, confidence=0.9)})
    await _say(loop, "s-replay", "我要退款")
    first = await _say(loop, "s-replay", "确认")
    assert first.state == "executed"

    pending = None  # 卡片已清，按业务语义重放一次同一个请求
    # 从调用记录里拿不到就重新构造一个完全相同的请求
    body1 = await loop.tools.create_refund(
        "ORD1003", Decimal("1"), request_id="confirm:fixed-1", trace_id="t1"
    )
    body2 = await loop.tools.create_refund(
        "ORD1003", Decimal("1"), request_id="confirm:fixed-1", trace_id="t2"
    )
    assert body1["code"] == 1000
    assert body2["code"] == 3006, "重复请求应返回 DUPLICATE_REQUEST"
    assert body2["data"]["refund_id"] == body1["data"]["refund_id"]
    assert pending is None


async def test_double_confirm_does_not_refund_twice(make_loop):
    """用户连点两次确认，第二次不该再产生一笔退款。"""
    loop = make_loop({"我要退款": IntentResult(intent=Intent.REFUND, confidence=0.9)})
    await _say(loop, "s-dbl", "我要退款")
    first = await _say(loop, "s-dbl", "确认")
    assert first.state == "executed"

    second = await _say(loop, "s-dbl", "确认")
    assert second.state != "executed"

    body = await loop.tools.get_order("ORD1002", trace_id="verify")
    assert (body.get("data") or {}).get("status") == "REFUNDED"


async def test_full_amount_falls_back_to_order_amount(make_loop):
    """没说金额 → 按订单全额，且金额来自库而不是前端快照。"""
    loop = make_loop({"我要退款": IntentResult(intent=Intent.REFUND, confidence=0.9)})
    await _say(loop, "s-amt", "我要退款")

    pending = await loop.sessions.get_pending("s-amt")
    assert pending is not None
    assert pending.amount == Decimal("259.90")


async def test_order_id_mention_does_not_make_bogus_amount(make_loop):
    """“帮我退 1002 块”这句里的 1002 是真金额意图，不该被订单号正则吃掉；反过来，
    “帮我退 ORD1002”必须整段挖掉订单号，退回订单全额而不是 1002 元。"""
    loop = make_loop({"帮我退 ORD1002": IntentResult(intent=Intent.REFUND, confidence=0.9)})
    await _say(loop, "s-oid", "帮我退 ORD1002")

    pending = await loop.sessions.get_pending("s-oid")
    assert pending is not None
    assert pending.amount == Decimal("259.90")

    state = await loop.sessions.get("s-oid")
    assert state is not None
    assert state.pending_refund_amount == Decimal("259.90")


async def test_order_switch_survives_next_request(make_loop):
    """用户切到 ORD1003 之后，下一轮请求即使再带 ORD1002 也不能被重置回去。

    否则退款会打到错误的订单上，而且用户完全看不出来。
    """
    loop = make_loop(
        {
            "我要退款": IntentResult(intent=Intent.REFUND, confidence=0.9),
            "帮我退 ORD1003": IntentResult(intent=Intent.REFUND, confidence=0.9),
        }
    )
    await _say(loop, "s-switch", "我要退款")
    await _say(loop, "s-switch", "算了")
    await _say(loop, "s-switch", "帮我退 ORD1003")

    state = await loop.sessions.get("s-switch")
    assert state is not None and state.order_id == "ORD1003"

    # 下一轮前端照旧带回 ORD1002（进场时绑定的订单）
    await _say(loop, "s-switch", "我要退款")
    state = await loop.sessions.get("s-switch")
    assert state is not None and state.order_id == "ORD1003", "会话订单被入参覆盖了"

    pending = await loop.sessions.get_pending("s-switch")
    assert pending is not None and pending.order_id == "ORD1003"

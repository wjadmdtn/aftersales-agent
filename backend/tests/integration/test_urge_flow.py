"""催发货的 Agent 链路。

三条 mode 走三条完全不同的行为，任何一条走错都会出事：
- execute 拿不到执行结果却说成功 → 用户在等一个不存在的催单
- query 自动替用户催单 → 越权写操作
- 已发货还去催 → 用户拿着"已催单"来质疑
"""
from __future__ import annotations

import pytest

from app.agent.intent import IntentClassifier
from app.agent.llm.stub import StubLLMClient
from app.agent.loop import AgentLoop
from app.models.agent import Intent, IntentMode, IntentResult

from tests.conftest_agent import InProcessMockClient


class FlakyUrgeTools(InProcessMockClient):
    """让催单调用在传输层失败，验证"失败如实告知"而不是伪装成功。"""

    def __init__(self, app):
        super().__init__(app)
        self.urge_calls = 0

    async def create_urge(self, order_id, *, request_id, trace_id):
        self.urge_calls += 1
        raise TimeoutError("mock timeout")


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

    def _build(responses, *, tools=None):
        return AgentLoop(
            classifier=IntentClassifier(StubLLMClient(responses), keyword_map={}),
            sessions=sessions,
            tools=tools or InProcessMockClient(mock_app),
        )

    yield _build
    await redis.aclose()


def _urge(mode: IntentMode = IntentMode.EXECUTE) -> IntentResult:
    return IntentResult(intent=Intent.URGE_SHIPMENT, mode=mode, confidence=0.9)


async def _say(loop, sid: str, msg: str, *, order: str = "ORD1002"):
    return await loop.handle(
        session_id=sid, user_id="u1", message=msg, trace_id="t", order_id=order
    )


# ---------- execute：真催单 ----------

async def test_execute_calls_urge_api(make_loop):
    loop = make_loop({"帮我催一下": _urge()})
    r = await _say(loop, "s-urge", "帮我催一下")

    assert r.state == "urge_submitted"
    assert "urge.create" in r.tool_calls
    assert "已为您提交催单" in r.reply


async def test_execute_does_not_ask_for_confirmation(make_loop):
    """催单可重复、不涉钱，用户说"催一下"就是授权，再弹确认是啰嗦。"""
    loop = make_loop({"帮我催一下": _urge()})
    r = await _say(loop, "s-urge2", "帮我催一下")
    assert r.state != "awaiting_confirmation"


# ---------- query / inquiry：只解释，不写 ----------

async def test_statement_about_unshipped_order_advises_not_urges(make_loop):
    """「订单状态一直是待发货」是陈述+抱怨，不能替用户催单。"""
    loop = make_loop({"订单状态一直是待发货": _urge(IntentMode.QUERY)})
    r = await _say(loop, "s-advise", "订单状态一直是待发货")

    assert r.state == "urge_advice"
    assert "urge.create" not in r.tool_calls
    assert "需要我帮您催一下吗" in r.reply
    # 说明里必须带承诺时效，否则用户不知道还能等多久
    assert "48 小时" in r.reply


async def test_inquiry_explains_without_calling(make_loop):
    loop = make_loop({"能催发货吗": _urge(IntentMode.INQUIRY)})
    r = await _say(loop, "s-inq", "能催发货吗")
    assert r.state == "urge_advice"
    assert "urge.create" not in r.tool_calls


# ---------- 状态不允许 ----------

async def test_shipped_order_cannot_be_urged(make_loop):
    loop = make_loop({"帮我催一下": _urge()})
    r = await _say(loop, "s-shipped", "帮我催一下", order="ORD1003")
    assert r.state == "urge_rejected"
    assert "已经发货" in r.reply
    assert "urge.create" not in r.tool_calls


async def test_unpaid_order_cannot_be_urged(make_loop):
    loop = make_loop({"帮我催一下": _urge()})
    r = await _say(loop, "s-unpaid", "帮我催一下", order="ORD1001")
    assert r.state == "urge_rejected"
    assert "还没付款" in r.reply


# ---------- 系统失败：如实告知 ----------

async def test_transport_failure_tells_the_truth(make_loop):
    """接口挂了不能回"已提交催单"——用户会一直等着商家的回应。"""
    import fakeredis.aioredis

    import app.api.routes as mock_routes
    from app.main import create_app

    tools = FlakyUrgeTools(create_app())
    loop = make_loop({"帮我催一下": _urge()}, tools=tools)
    r = await _say(loop, "s-fail", "帮我催一下")

    assert r.state == "urge_failed"
    assert "没提交成功" in r.reply
    assert "已为您提交" not in r.reply


# ---------- 幂等 ----------

async def test_repeat_urge_in_same_session_returns_same_result(make_loop):
    """同一会话催两次：第二次命中时间窗，返回同一条催单，用户看到同样的话。"""
    loop = make_loop({"帮我催一下": _urge()})
    first = await _say(loop, "s-dup", "帮我催一下")
    second = await _say(loop, "s-dup", "帮我催一下")
    assert first.state == "urge_submitted"
    assert second.state == "urge_submitted"
    assert first.reply == second.reply

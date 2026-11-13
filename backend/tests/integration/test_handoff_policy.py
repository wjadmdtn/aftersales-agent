"""转人工口径：只有明确投诉 / 要人工 / 差评才转，抱怨类一律不转。

用户定的产品口径（2026-11-12）：
- 抱怨、不满、情绪化表达 → AI 自己处理（安抚 + 问清诉求），**不提示转人工**
- 明确要人工（"转人工""找真人"）→ 转
- 明确投诉（"我要投诉""12315""消协""曝光"）→ 转
- 差评（"我要给差评""不行就差评"）→ 转

此前系统会：
  1. 澄清第 2 轮主动问"需要我帮您转接到人工客服吗"
  2. 澄清 3 轮仍说不清 → 自动转人工
两者都会把只想吐槽的用户推进人工队列，已按新口径删除。
"""
from __future__ import annotations

import pytest

from app.agent.intent import IntentClassifier
from app.agent.llm.stub import StubLLMClient
from app.agent.loop import AgentLoop
from app.models.agent import Intent, IntentMode, IntentResult

from tests.conftest_agent import InProcessMockClient


@pytest.fixture
async def make_loop():
    import fakeredis.aioredis

    import app.api.routes as mock_routes
    from app.agent.session import SessionStore
    from app.handoff.coordinator import HandoffCoordinator
    from app.handoff.queue import HandoffQueue
    from app.main import create_app

    mock_routes.reset_store()
    mock_app = create_app()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sessions = SessionStore(redis, ttl_seconds=3600)
    tools = InProcessMockClient(mock_app)
    coordinator = HandoffCoordinator(HandoffQueue(redis))

    def _build(responses):
        loop = AgentLoop(
            classifier=IntentClassifier(StubLLMClient(responses), keyword_map={}),
            sessions=sessions,
            tools=tools,
            coordinator=coordinator,
        )
        return loop

    yield _build
    await redis.aclose()


def _r(intent: Intent, mode: IntentMode = IntentMode.EXECUTE) -> IntentResult:
    return IntentResult(intent=intent, confidence=0.9, mode=mode)


async def _say(loop, sid: str, msg: str):
    return await loop.handle(
        session_id=sid, user_id="u1", message=msg, trace_id="t", order_id="ORD1002"
    )


# ---------- 抱怨类：一律不转 ----------


async def test_complaint_words_are_never_offered_handoff(make_loop):
    """抱怨类连续多轮，任何一轮都不得出现"转人工"的提示。"""
    msgs = ["太慢了", "还是很慢", "这也太慢了", "烦死了"]
    loop = make_loop({m: _r(Intent.UNKNOWN) for m in msgs + ["嗯"]})

    for i, m in enumerate(msgs):
        r = await _say(loop, "s-grumpy", m)
        assert r.state != "handoff_queued", f"第 {i + 1} 轮「{m}」不该转人工"
        assert "人工" not in r.reply, f"第 {i + 1} 轮不该提示转人工：{r.reply}"


async def test_grumpy_after_many_rounds_still_not_handoff(make_loop):
    """10 轮抱怨也不转——抱怨不是转人工的条件。"""
    loop = make_loop({f"抱怨{i}": _r(Intent.UNKNOWN) for i in range(10)})
    for i in range(10):
        r = await _say(loop, "s-long", f"抱怨{i}")
        assert r.state != "handoff_queued", f"第 {i + 1} 轮被转人工：{r.reply}"


async def test_second_clarify_does_not_offer_handoff(make_loop):
    """第 2 轮澄清不再问"要不要转人工"，改为继续追问。"""
    loop = make_loop({"说不清": _r(Intent.UNKNOWN), "还是说不清": _r(Intent.UNKNOWN)})
    await _say(loop, "s-2nd", "说不清")
    r = await _say(loop, "s-2nd", "还是说不清")
    assert r.state == "clarifying"
    assert "人工" not in r.reply, f"第 2 轮不该提示转人工：{r.reply}"


# ---------- 明确投诉 / 差评：**不再转人工**（2026-11-13 口径） ----------
# 转人工只由用户主动要（manual）+ 系统异常/能力边界触发。
# 投诉词、差评、扬言监管——都是 AI 安抚的范围，不进人工队列。


@pytest.mark.parametrize(
    "msg",
    ["我要投诉你们", "我要去消协投诉", "再不处理我就打12315", "我要曝光你们", "我要给差评"],
)
async def test_complaint_words_no_longer_handoff(make_loop, msg):
    loop = make_loop({msg: _r(Intent.UNKNOWN)})
    r = await _say(loop, f"s-{hash(msg) % 1000}", msg)
    assert r.state == "clarifying", f"「{msg}」不该再自动转人工"
    assert "人工" not in r.reply


async def test_manual_request_still_handoffs(make_loop):
    loop = make_loop({"转人工": _r(Intent.MANUAL)})
    r = await _say(loop, "s-manual", "转人工")
    assert r.state == "handoff_queued"


async def test_policy_knowledge_gap_still_handoffs(make_loop):
    """能力边界（政策库答不了）仍要转——这不是抱怨，是 AI 确实处理不了。"""
    from datetime import date

    from app.policy.models import PolicyAnswer, RetrievalDecision, RetrievalResult

    class _GapPolicy:
        async def answer(self, **kw):
            return (
                PolicyAnswer(decision=RetrievalDecision.MISS, rejection="rerank 判为不相关"),
                RetrievalResult(
                    query=kw["question"],
                    order_date=date(2026, 11, 10),
                    candidates=[],
                    decision=RetrievalDecision.MISS,
                    reason="rerank 判为不相关",
                    rerank_rejected=True,
                ),
            )

    loop = make_loop({"吊牌剪了能退吗": _r(Intent.POLICY, IntentMode.INQUIRY)})
    loop.policy = _GapPolicy()
    r = await _say(loop, "s-gap", "吊牌剪了能退吗")
    assert r.state == "handoff_queued"

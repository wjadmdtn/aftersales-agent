"""T046: Context Pack 对外契约（SC-004 字段完整率 100%）。

工作台不重算优先级、不推断触发器，只按后端给的值展示。
所以字段必须齐全且语义明确。
"""
from __future__ import annotations

from tests.conftest_app import *  # noqa: F401,F403

REQUIRED = {
    "session_id",
    "summary",
    "attempted_actions",
    "priority",
    "priority_reason",
    "trigger_type",
    "trigger_detail",
    "suggested_next_step",
    "primary_intent",
    "sentiment",
    "order_id",
    "queued_at",
}


def _queued(http, ws_user, text="转人工", sid="s1"):
    ws = ws_user(session_id=sid, user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.send({"type": "user.message", "payload": {"text": text}})
    ws.recv_until("assistant.done")
    return ws


def test_queue_item_has_all_required_fields(http, ws_user):
    _queued(http, ws_user)
    items = http.get("/handoff/queue").json()["items"]
    assert items, "转人工应入队"
    assert REQUIRED <= set(items[0])


def test_summary_within_120_chars(http, ws_user):
    _queued(http, ws_user)
    item = http.get("/handoff/queue").json()["items"][0]
    assert 0 < len(item["summary"]) <= 120


def test_risk_trigger_has_no_ai_suggestion():
    """FR-012：风险场景不给 AI 建议，由后端置空。

    2026-11-13 口径下投诉不再自动转人工，risk 触发从对话路径不可达，
    但 context_pack 的这条契约仍需守住（金额超阈值等触发仍会用到）。
    直接构造 TriggerResult 验证，不依赖对话链路。
    """
    from decimal import Decimal

    from app.handoff.context_pack import build_context
    from app.handoff.triggers import evaluate
    from app.models.handoff import Emotion, TurnSignals

    trigger = evaluate(TurnSignals(user_message="占位", refund_amount=Decimal("9999")))
    assert trigger.trigger_type.value == "risk"
    assert trigger.skip_self_service is True

    ctx = build_context(
        session_id="s-risk",
        user_id="u1",
        order_id="ORD1002",
        primary_intent="refund",
        last_user_message="占位",
        emotion=Emotion.NEUTRAL,
        trigger=trigger,
    )
    assert ctx.suggested_next_step is None
    assert ctx.priority.value == "high"


def test_user_request_trigger_may_have_suggestion(http, ws_user):
    ws = ws_user(session_id="s2", user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")
    item = http.get("/handoff/queue").json()["items"][0]
    assert item["trigger_type"] == "user_request"
    # 有值或为空都合法，但字段必须在
    assert "suggested_next_step" in item


def test_priority_reason_non_empty(http, ws_user):
    _queued(http, ws_user)
    item = http.get("/handoff/queue").json()["items"][0]
    assert item["priority"] in ("high", "medium", "low")
    assert item["priority_reason"]


def test_high_risk_is_high_priority():
    """风险触发（金额超阈值）必须是 high，走单元层验证。"""
    from decimal import Decimal

    from app.handoff.context_pack import build_context
    from app.handoff.triggers import evaluate
    from app.models.handoff import Emotion, TurnSignals

    trigger = evaluate(TurnSignals(user_message="占位", refund_amount=Decimal("9999")))
    ctx = build_context(
        session_id="s-high",
        user_id="u1",
        order_id="ORD1002",
        primary_intent="refund",
        last_user_message="占位",
        emotion=Emotion.NEUTRAL,
        trigger=trigger,
    )
    assert ctx.priority.value == "high"


def test_queue_sorted_high_before_low(http, ws_user):
    """FR-010：高优先级在前，同级按入队时间。

    2026-11-13 口径下投诉不再自动转人工，high 只能由风险金额/系统异常产生。
    这里用两条手动转人工验证同级稳定排序。
    """
    for sid in ("s-med-a", "s-med-b"):
        ws = ws_user(session_id=sid, user_id="u1", order_id="ORD1002")
        ws.recv()
        ws.send({"type": "user.handoff", "payload": {}})
        ws.recv_until("session.state")

    items = http.get("/handoff/queue").json()["items"]
    prios = [i["priority"] for i in items]
    rank = {"high": 0, "medium": 1, "low": 2}
    assert prios == sorted(prios, key=lambda p: rank[p])
    assert [i["session_id"] for i in items][:2] == ["s-med-a", "s-med-b"]

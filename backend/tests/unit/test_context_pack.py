"""T011: Context Pack。"""

from decimal import Decimal

import pytest

from app.config import Settings
from app.handoff.context_pack import SUMMARY_MAX_CHARS, build_context, rule_based_summary, truncate
from app.models.handoff import AttemptedAction, Emotion, ToolStatus, TriggerResult, TriggerType

CFG = Settings()


def _trigger(t: TriggerType, *, skip=False) -> TriggerResult:
    return TriggerResult.fire(t, "test", skip_self_service=skip)


def _ctx(trigger, **kw):
    defaults = dict(
        session_id="s1",
        user_id="u1",
        order_id="ORD1002",
        primary_intent="refund",
        last_user_message="我要退款",
        emotion=Emotion.NEUTRAL,
        trigger=trigger,
        cfg=CFG,
    )
    defaults.update(kw)
    return build_context(**defaults)


def test_pack_has_required_fields():
    ctx = _ctx(_trigger(TriggerType.USER_REQUEST))
    assert ctx.order_id == "ORD1002"
    assert ctx.summary
    assert ctx.priority_reason
    assert ctx.trigger_type is TriggerType.USER_REQUEST


def test_summary_within_limit():
    ctx = _ctx(_trigger(TriggerType.BOUNDARY), last_user_message="我要退款" * 100)
    assert len(ctx.summary) <= SUMMARY_MAX_CHARS


def test_truncate_adds_ellipsis():
    out = truncate("x" * 500)
    assert len(out) == SUMMARY_MAX_CHARS and out.endswith("…")


def test_risk_has_no_ai_suggestion():
    ctx = _ctx(_trigger(TriggerType.RISK, skip=True))
    assert ctx.suggested_next_step is None


def test_boundary_has_suggestion():
    ctx = _ctx(_trigger(TriggerType.BOUNDARY))
    assert ctx.suggested_next_step is not None


def test_empty_actions_is_list_not_missing():
    ctx = _ctx(_trigger(TriggerType.USER_REQUEST))
    assert ctx.attempted_actions == []


def test_actions_recorded():
    ctx = _ctx(
        _trigger(TriggerType.BOUNDARY),
        attempted=[AttemptedAction(tool="order.get", result_status=ToolStatus.OK)],
    )
    assert len(ctx.attempted_actions) == 1
    assert "order.get" in ctx.summary


def test_requires_trigger():
    with pytest.raises(ValueError):
        _ctx(TriggerResult.none())


def test_summary_makes_no_io():
    """摘要必须是纯函数：同样的输入永远同样的输出。"""
    t = _trigger(TriggerType.RISK)
    a = rule_based_summary(
        primary_intent="refund", last_user_message="我要退款", trigger=t, attempted=[]
    )
    b = rule_based_summary(
        primary_intent="refund", last_user_message="我要退款", trigger=t, attempted=[]
    )
    assert a == b

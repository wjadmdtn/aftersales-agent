from app.config import HandoffRules
from app.handoff.context_pack import (
    SUMMARY_MAX_CHARS,
    build_context,
    pack_completeness,
    rule_based_summary,
    truncate_summary,
)
from app.models import (
    AttemptedAction,
    HistoryTurn,
    Order,
    Priority,
    Sentiment,
    ToolStatus,
    TriggerResult,
    TriggerType,
)

RULES = HandoffRules()


def _ctx(trigger: TriggerResult, **kw):
    defaults = dict(
        session_id="s1",
        user_id="u1",
        entry_point="order_list",
        order=Order(order_id="ORD1", status="SHIPPED", amount=299),
        turns=[HistoryTurn(role="user", content="我要退款"), HistoryTurn(role="assistant", content="好的")],
        attempted=[AttemptedAction(tool="get_order", result_status=ToolStatus.OK)],
        primary_intent="refund",
        intent_confidence=0.92,
        sentiment=Sentiment.NEUTRAL,
        trigger=trigger,
        rules=RULES,
    )
    defaults.update(kw)
    return build_context(**defaults)


def test_pack_carries_order_id():
    ctx = _ctx(TriggerResult.fire(TriggerType.USER_REQUEST, "转人工"))
    assert ctx.order_id == "ORD1"
    ok, missing = pack_completeness(ctx)
    assert ok, missing


def test_summary_respects_length_limit():
    turns = [HistoryTurn(role="user", content="我要退款" * 100)]
    ctx = _ctx(TriggerResult.fire(TriggerType.BOUNDARY, "x"), turns=turns)
    assert len(ctx.summary) <= SUMMARY_MAX_CHARS


def test_truncate_summary_is_stable():
    assert truncate_summary("abc") == "abc"
    out = truncate_summary("x" * 500)
    assert len(out) == SUMMARY_MAX_CHARS and out.endswith("…")


def test_risk_trigger_drops_suggestion():
    ctx = _ctx(TriggerResult.fire(TriggerType.RISK, "amount", skip_self_service=True))
    assert ctx.suggested_next_step is None
    assert ctx.priority is Priority.HIGH


def test_boundary_trigger_keeps_suggestion():
    ctx = _ctx(TriggerResult.fire(TriggerType.BOUNDARY, "tool empty"))
    assert ctx.suggested_next_step is not None


def test_priority_reason_is_always_present():
    ctx = _ctx(TriggerResult.fire(TriggerType.SYSTEM_ERROR, "tool streak"))
    assert ctx.priority_reason


def test_completeness_detects_missing_order():
    ctx = _ctx(TriggerResult.fire(TriggerType.USER_REQUEST, "手动"), order=None)
    ok, missing = pack_completeness(ctx)
    assert not ok and "order_id" in missing


def test_rule_based_summary_mentions_intent_and_trigger():
    trigger = TriggerResult.fire(TriggerType.USER_REQUEST, "转人工")
    s = rule_based_summary(
        [HistoryTurn(role="user", content="我要退款")], intent="refund", trigger=trigger
    )
    assert "退款" in s
    assert "转人工" in s


def test_build_requires_trigger():
    import pytest

    with pytest.raises(ValueError):
        _ctx(TriggerResult.none())

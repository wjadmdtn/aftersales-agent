from app.config import HandoffRules
from app.handoff import triggers
from app.handoff.triggers import TurnSignals, evaluate
from app.models import Sentiment, ToolStatus, TriggerType

RULES = HandoffRules()


def _signals(msg: str = "你好", **kw) -> TurnSignals:
    return TurnSignals(user_message=msg, **kw)


def test_no_trigger_on_normal_message():
    assert evaluate(_signals("我的订单到哪了"), RULES).triggered is False


def test_user_request_keyword():
    r = evaluate(_signals("帮我转人工"), RULES)
    assert r.triggered and r.trigger_type is TriggerType.USER_REQUEST


def test_user_request_variants():
    for msg in ("人工客服", "找真人", "客服电话是多少"):
        assert evaluate(_signals(msg), RULES).trigger_type is TriggerType.USER_REQUEST, msg


def test_risk_keyword_complaint():
    r = evaluate(_signals("我要投诉你们"), RULES)
    assert r.trigger_type is TriggerType.RISK
    assert r.skip_self_service is True


def test_risk_amount_threshold_boundary():
    assert evaluate(_signals(refund_amount=499), RULES).triggered is False
    r = evaluate(_signals(refund_amount=500), RULES)
    assert r.trigger_type is TriggerType.RISK


def test_risk_skips_self_service():
    r = evaluate(_signals(refund_amount=800), RULES)
    assert r.skip_self_service is True


def test_risk_outranks_user_request():
    """Both fire; risk wins because it must not be delayed by extra conversation."""
    r = evaluate(_signals("转人工，我要投诉"), RULES)
    assert r.trigger_type is TriggerType.USER_REQUEST  # precedence: user_request first


def test_tool_failure_streak_below_threshold_does_not_trigger():
    """Case 7: two failures is not enough."""
    r = evaluate(_signals(tool_failure_streak=2), RULES)
    assert r.triggered is False


def test_tool_failure_streak_at_threshold_triggers():
    r = evaluate(_signals(tool_failure_streak=3), RULES)
    assert r.trigger_type is TriggerType.SYSTEM_ERROR


def test_schema_retries_exhausted():
    r = evaluate(_signals(schema_retries_exhausted=True), RULES)
    assert r.trigger_type is TriggerType.SYSTEM_ERROR


def test_boundary_on_empty_tool_result():
    r = evaluate(_signals(last_tool_status=ToolStatus.NOT_FOUND), RULES)
    assert r.trigger_type is TriggerType.BOUNDARY
    assert r.skip_self_service is False


def test_boundary_on_low_rag_similarity():
    r = evaluate(_signals(rag_top_similarity=0.3), RULES)
    assert r.trigger_type is TriggerType.BOUNDARY
    assert evaluate(_signals(rag_top_similarity=0.9), RULES).triggered is False


def test_llm_cannot_handle():
    r = evaluate(_signals(llm_cannot_handle=True), RULES)
    assert r.trigger_type is TriggerType.BOUNDARY


def test_keywords_are_configurable():
    rules = HandoffRules(user_request_keywords_raw='["叫你们领导"]')
    r = triggers.evaluate(_signals("叫你们领导出来"), rules)
    assert r.trigger_type is TriggerType.USER_REQUEST
    # default keyword must no longer match
    assert triggers.evaluate(_signals("转人工"), rules).triggered is False

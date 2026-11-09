from app.config import HandoffRules
from app.models import Priority, Sentiment, TriggerType
from app.handoff.priority import calc_priority


def test_risk_trigger_is_always_high():
    p, reason = calc_priority(trigger_type=TriggerType.RISK, sentiment=Sentiment.NEUTRAL)
    assert p is Priority.HIGH
    assert "risk" in reason


def test_angry_sentiment_outranks_low_amount():
    """Case 8 from the spec: angry + 100 yuan is still high priority."""
    p, reason = calc_priority(
        trigger_type=TriggerType.BOUNDARY, sentiment=Sentiment.ANGRY, refund_amount=100
    )
    assert p is Priority.HIGH
    assert reason == "angry sentiment"


def test_amount_threshold_boundary():
    rules = HandoffRules(risk_amount_threshold=500)
    low, _ = calc_priority(
        trigger_type=TriggerType.BOUNDARY, sentiment=Sentiment.NEUTRAL, refund_amount=499, rules=rules
    )
    high, _ = calc_priority(
        trigger_type=TriggerType.BOUNDARY, sentiment=Sentiment.NEUTRAL, refund_amount=500, rules=rules
    )
    assert low is Priority.LOW
    assert high is Priority.HIGH


def test_user_request_is_medium():
    p, _ = calc_priority(trigger_type=TriggerType.USER_REQUEST, sentiment=Sentiment.NEUTRAL)
    assert p is Priority.MEDIUM


def test_default_is_low():
    p, _ = calc_priority(trigger_type=TriggerType.SYSTEM_ERROR, sentiment=Sentiment.NEUTRAL)
    assert p is Priority.LOW


def test_threshold_is_configurable():
    rules = HandoffRules(risk_amount_threshold=100)
    p, _ = calc_priority(
        trigger_type=TriggerType.BOUNDARY, sentiment=Sentiment.NEUTRAL, refund_amount=150, rules=rules
    )
    assert p is Priority.HIGH

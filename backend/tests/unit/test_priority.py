"""T008: 优先级。纯函数，必带依据。"""

from decimal import Decimal

from app.config import Settings
from app.handoff.priority import calc_priority
from app.models.handoff import Emotion, Priority, TriggerType

CFG = Settings()


def test_risk_is_always_high():
    p, reason = calc_priority(trigger_type=TriggerType.RISK, emotion=Emotion.NEUTRAL, cfg=CFG)
    assert p is Priority.HIGH
    assert reason


def test_angry_outranks_small_amount():
    """Case：情绪激烈但金额很小，仍是高优先级。"""
    p, reason = calc_priority(
        trigger_type=TriggerType.BOUNDARY,
        emotion=Emotion.ANGRY,
        refund_amount=Decimal("100"),
        cfg=CFG,
    )
    assert p is Priority.HIGH
    assert reason == "情绪激烈"


def test_amount_threshold_boundary():
    low, _ = calc_priority(
        trigger_type=TriggerType.BOUNDARY, emotion=Emotion.NEUTRAL, refund_amount=Decimal("499"), cfg=CFG
    )
    high, _ = calc_priority(
        trigger_type=TriggerType.BOUNDARY, emotion=Emotion.NEUTRAL, refund_amount=Decimal("500"), cfg=CFG
    )
    assert low is Priority.LOW
    assert high is Priority.HIGH


def test_user_request_is_medium():
    p, reason = calc_priority(trigger_type=TriggerType.USER_REQUEST, emotion=Emotion.NEUTRAL, cfg=CFG)
    assert p is Priority.MEDIUM
    assert "主动要求" in reason


def test_angry_outranks_user_request():
    p, _ = calc_priority(trigger_type=TriggerType.USER_REQUEST, emotion=Emotion.ANGRY, cfg=CFG)
    assert p is Priority.HIGH


def test_default_is_low():
    p, reason = calc_priority(trigger_type=TriggerType.SYSTEM_ERROR, emotion=Emotion.NEUTRAL, cfg=CFG)
    assert p is Priority.LOW
    assert reason


def test_reason_never_empty():
    """优先级必须可解释给主管，依据不能为空。"""
    for t in TriggerType:
        for e in Emotion:
            _, reason = calc_priority(trigger_type=t, emotion=e, cfg=CFG)
            assert reason, f"{t}/{e} 缺少依据"

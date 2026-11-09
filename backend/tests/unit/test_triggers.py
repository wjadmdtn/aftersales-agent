"""T004: 四类触发源与互斥顺序。"""

from decimal import Decimal

from app.config import Settings
from app.handoff import triggers
from app.handoff.triggers import TurnSignals, evaluate
from app.models.handoff import ToolStatus, TriggerType

CFG = Settings()


def _s(msg: str = "你好", **kw) -> TurnSignals:
    return TurnSignals(user_message=msg, **kw)


def test_normal_message_does_not_trigger():
    assert evaluate(_s("我的订单到哪了"), CFG).triggered is False


def test_user_request():
    r = evaluate(_s("帮我转人工"), CFG)
    assert r.trigger_type is TriggerType.USER_REQUEST


def test_user_request_variants():
    for m in ("人工客服", "找真人", "客服在吗 我要找客服"):
        assert evaluate(_s(m), CFG).trigger_type is TriggerType.USER_REQUEST, m


def test_risk_keyword():
    r = evaluate(_s("我要投诉你们"), CFG)
    assert r.trigger_type is TriggerType.RISK
    assert r.skip_self_service is True


def test_risk_amount_threshold():
    assert evaluate(_s(refund_amount=Decimal("499")), CFG).triggered is False
    assert evaluate(_s(refund_amount=Decimal("500")), CFG).trigger_type is TriggerType.RISK


def test_user_request_outranks_risk():
    """'我要投诉，转人工' 按用户主动要求处理。

    用户要求的是"要人"，不是"要 AI 道歉"，因此不必走风险那套话术。
    """
    r = evaluate(_s("我要投诉，转人工"), CFG)
    assert r.trigger_type is TriggerType.USER_REQUEST


def test_system_error_threshold():
    assert evaluate(_s(tool_failure_streak=2), CFG).triggered is False
    assert evaluate(_s(tool_failure_streak=3), CFG).trigger_type is TriggerType.SYSTEM_ERROR


def test_boundary_on_not_found():
    r = evaluate(_s(last_tool_status=ToolStatus.NOT_FOUND), CFG)
    assert r.trigger_type is TriggerType.BOUNDARY
    assert r.skip_self_service is False


def test_risk_outranks_system_error():
    r = evaluate(_s("投诉", tool_failure_streak=5), CFG)
    assert r.trigger_type is TriggerType.RISK


def test_keywords_configurable():
    cfg = Settings(user_request_keywords_raw='["叫你们领导"]')
    assert triggers.evaluate(_s("叫你们领导出来"), cfg).trigger_type is TriggerType.USER_REQUEST
    assert triggers.evaluate(_s("转人工"), cfg).triggered is False

"""T010: 优先级。纯函数，必须带可解释的依据。

优先级决定真人的工作分配，出错了没法向主管解释，也没法复现。
因此这里不碰任何外部状态。
"""
from __future__ import annotations

from decimal import Decimal

from app.config import Settings, settings
from app.models.handoff import Emotion, Priority, TriggerType


def calc_priority(
    *,
    trigger_type: TriggerType,
    emotion: Emotion,
    refund_amount: Decimal | None = None,
    cfg: Settings | None = None,
) -> tuple[Priority, str]:
    c = cfg or settings

    if trigger_type is TriggerType.RISK:
        return Priority.HIGH, f"风险触发：{_describe_risk(refund_amount, emotion, c)}"

    if emotion is Emotion.ANGRY:
        return Priority.HIGH, "情绪激烈"

    if refund_amount is not None and float(refund_amount) >= c.risk_amount_threshold:
        return Priority.HIGH, f"金额 {refund_amount} ≥ {c.risk_amount_threshold}"

    if trigger_type is TriggerType.USER_REQUEST:
        return Priority.MEDIUM, "用户主动要求转人工"

    return Priority.LOW, "系统自动转接"


def _describe_risk(amount: Decimal | None, emotion: Emotion, cfg: Settings) -> str:
    bits: list[str] = []
    if amount is not None and float(amount) >= cfg.risk_amount_threshold:
        bits.append(f"金额 {amount}")
    if emotion is Emotion.ANGRY:
        bits.append("情绪激烈")
    return "、".join(bits) or "命中投诉关键词"

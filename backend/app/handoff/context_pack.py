"""T012: Context Pack。

摘要用纯字符串拼接，**没有网络、没有 IO、没有模型**。
转人工是最不能挂的路径——模型挂了还要能转，所以摘要不可能失败。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

from app.config import Settings, settings
from app.handoff.priority import calc_priority
from app.models.handoff import (
    AttemptedAction,
    Emotion,
    HandoffContext,
    TriggerResult,
    TriggerType,
)

SUMMARY_MAX_CHARS = 120

_INTENT_LABEL = {
    "refund": "退款",
    "change_address": "修改收货地址",
    "logistics": "查询物流",
    "query_order": "查询订单",
    "complaint": "投诉",
    "manual": "要求人工",
    "general": "未识别诉求",
}

_TRIGGER_LABEL = {
    TriggerType.USER_REQUEST: "用户主动要求转人工",
    TriggerType.RISK: "风险拦截",
    TriggerType.SYSTEM_ERROR: "系统异常",
    TriggerType.BOUNDARY: "AI 能力范围外",
}

_SUGGESTION = {
    "refund": "核对订单状态后按退款流程处理",
    "change_address": "确认未发货后修改地址",
    "logistics": "查询物流轨迹并同步给用户",
    "query_order": "核对订单信息",
    "complaint": "记录投诉并升级处理",
}


def truncate(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def rule_based_summary(
    *,
    primary_intent: str,
    last_user_message: str,
    trigger: TriggerResult,
    attempted: list[AttemptedAction],
) -> str:
    parts = [
        f"用户诉求：{_INTENT_LABEL.get(primary_intent, primary_intent)}",
        f"用户表述：{last_user_message}",
        f"转人工原因：{_TRIGGER_LABEL[trigger.trigger_type]}",
    ]
    if attempted:
        parts.append("已尝试：" + "、".join(a.tool for a in attempted))
    return truncate("；".join(parts))


def build_context(
    *,
    session_id: str,
    user_id: str,
    order_id: str | None,
    primary_intent: str,
    last_user_message: str,
    emotion: Emotion,
    trigger: TriggerResult,
    attempted: list[AttemptedAction] | None = None,
    refund_amount: Decimal | None = None,
    cfg: Settings | None = None,
) -> HandoffContext:
    c = cfg or settings
    if trigger.trigger_type is None:
        raise ValueError("无法在未触发的情况下构建转接上下文")

    actions = attempted or []
    summary = rule_based_summary(
        primary_intent=primary_intent,
        last_user_message=last_user_message,
        trigger=trigger,
        attempted=actions,
    )
    priority, reason = calc_priority(
        trigger_type=trigger.trigger_type,
        emotion=emotion,
        refund_amount=refund_amount,
        cfg=c,
    )

    # 风险场景不给 AI 建议：风险判定下 AI 不该再输出方案
    suggested = None if trigger.skip_self_service else _SUGGESTION.get(primary_intent)

    return HandoffContext(
        session_id=session_id,
        user_id=user_id,
        order_id=order_id,
        summary=summary,
        attempted_actions=actions,
        primary_intent=primary_intent,
        sentiment=emotion,
        priority=priority,
        priority_reason=reason,
        trigger_type=trigger.trigger_type,
        trigger_detail=trigger.detail,
        suggested_next_step=suggested,
        queued_at=datetime.now(timezone.utc),
    )

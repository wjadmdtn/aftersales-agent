"""Builds the payload a human agent reads before picking up the session.

A human handles several conversations at once; without a summary they have to re-read
the transcript from the top. That is why the pack carries structured facts instead of
just forwarding the chat log.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Protocol

from app.config import HandoffRules, settings
from app.handoff.priority import calc_priority
from app.models import (
    AttemptedAction,
    HandoffContext,
    HistoryTurn,
    Order,
    Sentiment,
    TriggerResult,
)
SUMMARY_MAX_CHARS = 120

_INTENT_LABEL = {
    "refund": "退款",
    "change_address": "修改收货地址",
    "logistics": "查询物流",
    "urge_shipping": "催发货",
    "complaint": "投诉",
    "unknown": "未识别诉求",
}

_TRIGGER_LABEL = {
    "boundary": "AI 能力范围外",
    "risk": "风险拦截",
    "user_request": "用户主动要求转人工",
    "system_error": "系统异常",
}

_SUGGESTION = {
    "refund": "核对订单状态后按退款流程处理",
    "change_address": "确认未发货后修改地址",
    "logistics": "查询物流轨迹并同步给用户",
    "urge_shipping": "联系仓库催单",
    "complaint": "记录投诉并升级处理",
    "unknown": "先确认用户诉求",
}


class Summarizer(Protocol):
    def __call__(self, turns: list[HistoryTurn], *, intent: str, trigger: TriggerResult) -> str: ...


def rule_based_summary(turns: list[HistoryTurn], *, intent: str, trigger: TriggerResult) -> str:
    """Deterministic fallback so the pack never depends on an LLM being available."""
    user_turns = [t.content.strip() for t in turns if t.role == "user" and t.content.strip()]
    last_request = user_turns[-1] if user_turns else ""
    label = _INTENT_LABEL.get(intent, "未识别诉求")
    parts = [f"用户诉求：{label}", f"用户表述：{last_request}"]
    if trigger.trigger_type is not None:
        parts.append(f"转人工原因：{_TRIGGER_LABEL[trigger.trigger_type.value]}")
    return truncate_summary("；".join(parts))


def truncate_summary(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def build_context(
    *,
    session_id: str,
    user_id: str,
    entry_point: str,
    order: Order | None,
    turns: list[HistoryTurn],
    attempted: list[AttemptedAction],
    primary_intent: str,
    intent_confidence: float,
    sentiment: Sentiment,
    trigger: TriggerResult,
    refund_amount: float | None = None,
    summarizer: Summarizer | None = None,
    rules: HandoffRules | None = None,
) -> HandoffContext:
    r = rules or settings.handoff
    if trigger.trigger_type is None:
        raise ValueError("cannot build handoff context without a trigger")

    summarizer = summarizer or rule_based_summary
    summary = truncate_summary(
        summarizer(turns, intent=primary_intent, trigger=trigger)
    )

    priority, reason = calc_priority(
        trigger_type=trigger.trigger_type,
        sentiment=sentiment,
        refund_amount=refund_amount,
        rules=r,
    )

    suggested = None if trigger.skip_self_service else _SUGGESTION.get(primary_intent)

    return HandoffContext(
        session_id=session_id,
        user_id=user_id,
        entry_point=entry_point,  # type: ignore[arg-type]
        order_id=order.order_id if order else None,
        summary=summary,
        attempted_actions=attempted,
        primary_intent=primary_intent,
        intent_confidence=intent_confidence,
        sentiment=sentiment,
        priority=priority,
        priority_reason=reason,
        trigger_type=trigger.trigger_type,
        trigger_detail=trigger.detail,
        suggested_next_step=suggested,
        queued_at=datetime.now(timezone.utc),
        expected_wait_seconds=r.expected_wait_seconds,
    )


def pack_completeness(ctx: HandoffContext) -> tuple[bool, list[str]]:
    """Missing fields for the 'context pack complete' rubric."""
    missing: list[str] = []
    for name in ("session_id", "user_id", "order_id", "summary", "primary_intent", "priority_reason"):
        if not getattr(ctx, name):
            missing.append(name)
    if not ctx.attempted_actions:
        # an empty list is legitimate when no tool ran, but the field must still be present
        pass
    if len(ctx.summary) > SUMMARY_MAX_CHARS:
        missing.append("summary_too_long")
    return (not missing, missing)


def priority_sort_key(ctx: HandoffContext) -> tuple[int, float]:
    from app.models import PRIORITY_SCORE

    return (-PRIORITY_SCORE[ctx.priority], ctx.queued_at.timestamp())


__all__ = [
    "SummaryMaxChars",
    "build_context",
    "pack_completeness",
    "priority_sort_key",
    "rule_based_summary",
    "truncate_summary",
]

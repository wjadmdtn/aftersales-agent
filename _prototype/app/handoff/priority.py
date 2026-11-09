"""Priority is rule-based on purpose: it drives real people's work order,
so it must be reproducible and explainable to a team lead. Never let the LLM decide it."""
from __future__ import annotations

from app.config import HandoffRules, settings
from app.models import Priority, Sentiment, TriggerType


def calc_priority(
    *,
    trigger_type: TriggerType,
    sentiment: Sentiment,
    refund_amount: float | None = None,
    rules: HandoffRules | None = None,
) -> tuple[Priority, str]:
    r = rules or settings.handoff

    if trigger_type is TriggerType.RISK:
        return Priority.HIGH, f"risk trigger: amount={refund_amount}, sentiment={sentiment.value}"

    if sentiment is Sentiment.ANGRY:
        return Priority.HIGH, "angry sentiment"

    if refund_amount is not None and refund_amount >= r.risk_amount_threshold:
        return Priority.HIGH, f"amount {refund_amount} >= threshold {r.risk_amount_threshold}"

    if trigger_type is TriggerType.USER_REQUEST:
        return Priority.MEDIUM, "user requested human handoff"

    return Priority.LOW, "system auto-handoff"

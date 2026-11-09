"""Four handoff trigger sources.

Order matters: the evaluate() entry point applies them in a fixed precedence so the
outcome is deterministic and testable. Risk outranks everything -- in a risk scenario
one extra round of self-service is what escalates the user further.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import HandoffRules, settings
from app.models import Sentiment, ToolStatus, TriggerResult, TriggerType


@dataclass
class TurnSignals:
    """Everything the trigger layer needs to know about the current turn."""

    user_message: str
    refund_amount: float | None = None
    sentiment: Sentiment = Sentiment.NEUTRAL

    # ① boundary
    last_tool_status: ToolStatus | None = None
    rag_top_similarity: float | None = None
    llm_cannot_handle: bool = False

    # ④ system error
    tool_failure_streak: int = 0
    schema_retries_exhausted: bool = False
    llm_timeout_streak: int = 0

    # whether a real request was already in flight (e.g. a tool is executing)
    pending_tool_calls: int = 0


def _match_any(message: str, keywords: tuple[str, ...]) -> str | None:
    normalized = message.lower()
    for kw in keywords:
        if kw in normalized:
            return kw
    return None


def check_user_request(s: TurnSignals, rules: HandoffRules) -> TriggerResult:
    hit = _match_any(s.user_message, rules.user_request_keywords)
    if hit:
        return TriggerResult.fire(TriggerType.USER_REQUEST, f"matched keyword: {hit}")
    return TriggerResult.none()


def check_risk(s: TurnSignals, rules: HandoffRules) -> TriggerResult:
    hits: list[str] = []

    if s.refund_amount is not None and s.refund_amount >= rules.risk_amount_threshold:
        hits.append(f"amount {s.refund_amount} >= {rules.risk_amount_threshold}")

    kw = _match_any(s.user_message, rules.risk_keywords)
    if kw:
        hits.append(f"risk keyword: {kw}")

    angry_kw = _match_any(s.user_message, rules.angry_keywords)
    if angry_kw or s.sentiment is Sentiment.ANGRY:
        hits.append(f"angry signals: {angry_kw or 'classifier'}")

    if hits:
        return TriggerResult.fire(TriggerType.RISK, "; ".join(hits), skip_self_service=True)
    return TriggerResult.none()


def check_system_error(s: TurnSignals, rules: HandoffRules) -> TriggerResult:
    if s.tool_failure_streak >= rules.tool_failure_threshold:
        return TriggerResult.fire(
            TriggerType.SYSTEM_ERROR,
            f"tool failure streak {s.tool_failure_streak} >= {rules.tool_failure_threshold}",
        )
    if s.schema_retries_exhausted:
        return TriggerResult.fire(TriggerType.SYSTEM_ERROR, "schema retries exhausted")
    if s.llm_timeout_streak >= rules.llm_timeout_threshold:
        return TriggerResult.fire(
            TriggerType.SYSTEM_ERROR,
            f"llm timeout streak {s.llm_timeout_streak} >= {rules.llm_timeout_threshold}",
        )
    return TriggerResult.none()


def check_boundary(s: TurnSignals, rules: HandoffRules) -> TriggerResult:
    if s.last_tool_status in (ToolStatus.NOT_FOUND, ToolStatus.EMPTY):
        return TriggerResult.fire(TriggerType.BOUNDARY, f"tool returned {s.last_tool_status.value}")
    if s.rag_top_similarity is not None and s.rag_top_similarity < rules.rag_similarity_threshold:
        return TriggerResult.fire(
            TriggerType.BOUNDARY,
            f"rag similarity {s.rag_top_similarity} < {rules.rag_similarity_threshold}",
        )
    if s.llm_cannot_handle:
        return TriggerResult.fire(TriggerType.BOUNDARY, "llm declared cannot_handle")
    return TriggerResult.none()


# precedence: user_request > risk > system_error > boundary
_CHECKS = (check_user_request, check_risk, check_system_error, check_boundary)


def evaluate(s: TurnSignals, rules: HandoffRules | None = None) -> TriggerResult:
    r = rules or settings.handoff
    for check in _CHECKS:
        result = check(s, r)
        if result.triggered:
            return result
    return TriggerResult.none()

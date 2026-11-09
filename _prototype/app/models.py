"""Domain types shared by the agent loop and the handoff subsystem."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SessionState(str, Enum):
    INTENT = "INTENT"
    COLLECTING = "COLLECTING"
    VALIDATING = "VALIDATING"
    DECIDING = "DECIDING"
    EXECUTING = "EXECUTING"
    RISK_BLOCK = "RISK_BLOCK"
    HANDOFF_REQUESTED = "HANDOFF_REQUESTED"
    QUEUED = "QUEUED"
    HUMAN_SERVING = "HUMAN_SERVING"
    DONE = "DONE"
    RESOLVED = "RESOLVED"


TERMINAL_STATES = {SessionState.DONE, SessionState.RESOLVED}


class TriggerType(str, Enum):
    BOUNDARY = "boundary"
    RISK = "risk"
    USER_REQUEST = "user_request"
    SYSTEM_ERROR = "system_error"


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


PRIORITY_SCORE = {Priority.HIGH: 3, Priority.MEDIUM: 2, Priority.LOW: 1}


class Sentiment(str, Enum):
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    ANGRY = "angry"


class ToolStatus(str, Enum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    EMPTY = "EMPTY"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class Order(BaseModel):
    order_id: str
    status: Literal["PAID", "SHIPPED", "DELIVERED", "CLOSED", "REFUNDING", "REFUNDED"]
    amount: float = Field(ge=0)
    address: str | None = None


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AttemptedAction(BaseModel):
    tool: str
    params: dict = Field(default_factory=dict)
    result_status: ToolStatus
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HandoffContext(BaseModel):
    session_id: str
    user_id: str
    entry_point: Literal["order_list", "chat_widget", "h5_order_page"] = "order_list"
    order_id: str | None = None

    summary: str = Field(max_length=120)
    attempted_actions: list[AttemptedAction] = Field(default_factory=list)

    primary_intent: str
    intent_confidence: float = Field(ge=0, le=1)
    sentiment: Sentiment = Sentiment.NEUTRAL

    priority: Priority
    priority_reason: str

    trigger_type: TriggerType
    trigger_detail: str

    suggested_next_step: str | None = None

    queued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expected_wait_seconds: int = 120


class TriggerResult(BaseModel):
    triggered: bool
    trigger_type: TriggerType | None = None
    detail: str = ""
    # risk blocks skip the self-service suggestion stage
    skip_self_service: bool = False

    @classmethod
    def none(cls) -> TriggerResult:
        return cls(triggered=False)

    @classmethod
    def fire(cls, trigger_type: TriggerType, detail: str, *, skip_self_service: bool = False) -> TriggerResult:
        return cls(
            triggered=True,
            trigger_type=trigger_type,
            detail=detail,
            skip_self_service=skip_self_service,
        )

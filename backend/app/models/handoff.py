"""转人工领域模型。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum, IntEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class TriggerType(StrEnum):
    USER_REQUEST = "user_request"
    RISK = "risk"
    SYSTEM_ERROR = "system_error"
    BOUNDARY = "boundary"


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


PRIORITY_SCORE: dict[Priority, int] = {Priority.HIGH: 3, Priority.MEDIUM: 2, Priority.LOW: 1}


class Emotion(StrEnum):
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    ANGRY = "angry"


class HandoffState(StrEnum):
    NORMAL = "normal"
    HANDOFF_REQUESTED = "handoff_requested"
    QUEUED = "queued"
    HUMAN_SERVING = "human_serving"
    RESOLVED = "resolved"


class ToolStatus(StrEnum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    EMPTY = "EMPTY"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class AttemptedAction(BaseModel):
    tool: str
    result_status: ToolStatus
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TurnSignals(BaseModel):
    """触发判定需要的全部信号。纯数据，判定函数是纯函数。"""

    user_message: str
    refund_amount: Decimal | None = None

    last_tool_status: ToolStatus | None = None
    rag_top_similarity: float | None = None
    llm_cannot_handle: bool = False

    tool_failure_streak: int = 0
    schema_retries_exhausted: bool = False
    llm_timeout_streak: int = 0


class TriggerResult(BaseModel):
    triggered: bool
    trigger_type: TriggerType | None = None
    detail: str = ""
    skip_self_service: bool = False

    @classmethod
    def none(cls) -> TriggerResult:
        return cls(triggered=False)

    @classmethod
    def fire(cls, t: TriggerType, detail: str, *, skip_self_service: bool = False) -> TriggerResult:
        return cls(triggered=True, trigger_type=t, detail=detail, skip_self_service=skip_self_service)


class HandoffContext(BaseModel):
    session_id: str
    user_id: str
    order_id: str | None = None

    summary: str = Field(max_length=120)
    attempted_actions: list[AttemptedAction] = Field(default_factory=list)

    primary_intent: str
    sentiment: Emotion

    priority: Priority
    priority_reason: str = Field(min_length=1)

    trigger_type: TriggerType
    trigger_detail: str
    suggested_next_step: str | None = None

    queued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QueueItem(BaseModel):
    session_id: str
    priority: Priority
    queued_at: datetime


ScriptKey = Literal[
    "user_request", "risk", "system_error", "boundary", "queued_followup", "queued_ask_time", "declined"
]


class HandoffEvent(BaseModel):
    session_id: str
    event: Literal["triggered", "queued", "accepted", "declined", "resolved"]
    detail: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

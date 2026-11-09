"""Agent 领域模型：意图、会话、待确认操作、对话轮次。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Intent(StrEnum):
    REFUND = "refund"
    LOGISTICS = "logistics"
    CHANGE_ADDRESS = "change_address"
    QUERY_ORDER = "query_order"
    COMPLAINT = "complaint"
    MANUAL = "manual"
    GENERAL = "general"


# 只有这四个意图会映射到 mock 服务的业务能力
ACTIONABLE_INTENTS = {
    Intent.REFUND,
    Intent.LOGISTICS,
    Intent.CHANGE_ADDRESS,
    Intent.QUERY_ORDER,
}

# 会改变状态、必须确认的意图
WRITE_INTENTS = {Intent.REFUND, Intent.CHANGE_ADDRESS}

READ_INTENTS = {Intent.LOGISTICS, Intent.QUERY_ORDER}


class IntentSource(StrEnum):
    LLM = "llm"
    DEGRADED = "degraded"


class IntentResult(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    source: IntentSource = IntentSource.LLM
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class OrderSnapshot(BaseModel):
    """前端随会话传入的商品快照，仅用于快速展示。业务判断一律以查库为准。"""

    name: str | None = None
    amount: Decimal | None = None
    status: str | None = None


class PendingConfirmation(BaseModel):
    action: Literal["refund", "change_address"]
    order_id: str
    amount: Decimal | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime


class SessionState(BaseModel):
    session_id: str
    user_id: str
    order_id: str | None = None
    order_owner_id: str | None = None
    snapshot: OrderSnapshot | None = None
    intent: Intent | None = None
    turn_count: int = 0
    clarify_round: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TurnRecord(BaseModel):
    turn: int
    user_input: str
    intent: Intent | None
    source: IntentSource
    confidence: float | None = None
    tool_calls: list[str] = Field(default_factory=list)
    reply: str = ""
    snapshot_mismatch: bool = False


def now() -> datetime:
    return datetime.now(timezone.utc)

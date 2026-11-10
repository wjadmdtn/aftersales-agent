"""WS 协议：信封、消息类型、ChatMessage、ChatState。

字段定义以 specs/006-web-ui/data-model.md 为准，改这里必须同步改那份文档。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.handoff import HandoffState


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MessageType(StrEnum):
    # 流程
    HELLO = "hello"
    PING = "ping"
    PONG = "pong"
    ERROR = "error"
    # 用户端
    USER_MESSAGE = "user.message"
    USER_MESSAGE_ACK = "user.message.ack"
    USER_HANDOFF = "user.handoff"
    ASSISTANT_DELTA = "assistant.delta"
    ASSISTANT_DONE = "assistant.done"
    # 人工端
    AGENT_MESSAGE = "agent.message"
    QUEUE_UPDATE = "queue.update"
    # 双向
    HUMAN_MESSAGE = "human.message"
    SESSION_STATE = "session.state"


CLIENT_TO_SERVER = frozenset(
    {
        MessageType.PONG,
        MessageType.USER_MESSAGE,
        MessageType.USER_HANDOFF,
        MessageType.AGENT_MESSAGE,
        # 人工消息理论上不经客户端发，但协议声明它双向以保持对称
        MessageType.HUMAN_MESSAGE,
    }
)

SERVER_TO_CLIENT = frozenset(MessageType) - {MessageType.USER_MESSAGE, MessageType.USER_HANDOFF,
                                             MessageType.AGENT_MESSAGE}


class Envelope(BaseModel):
    type: MessageType
    session_id: str | None = None
    seq: int = 0
    ts: datetime = Field(default_factory=utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)


def envelope(
    type_: MessageType,
    *,
    session_id: str | None = None,
    seq: int = 0,
    payload: dict[str, Any] | None = None,
) -> Envelope:
    return Envelope(type=type_, session_id=session_id, seq=seq, payload=payload or {})


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    HUMAN = "human"
    SYSTEM = "system"


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str
    seq: int = Field(ge=1)
    role: Role
    sender_id: str | None = None
    text: str
    # 仅 assistant 有意义：True=LLM 真流式，False=完整回复按块推送
    streaming: bool = False
    final: bool = True
    ts: datetime = Field(default_factory=utcnow)
    trace_id: str | None = None


class ChatState(BaseModel):
    session_id: str
    user_id: str
    state: HandoffState = HandoffState.NORMAL
    agent_id: str | None = None
    agent_name: str | None = None
    order_id: str | None = None
    updated_at: datetime = Field(default_factory=utcnow)
    last_seq: int = Field(default=0, ge=0)


# 界面文案键与 HandoffState 的映射在 data-model §2 固定为四类：
# handoff_requested 在同一请求内即迁移为 queued，界面不可观测，并入 ai_serving。
UI_STATE_OF: dict[HandoffState, Literal["ai_serving", "queued", "human_serving", "resolved"]] = {
    HandoffState.NORMAL: "ai_serving",
    HandoffState.HANDOFF_REQUESTED: "ai_serving",
    HandoffState.QUEUED: "queued",
    HandoffState.HUMAN_SERVING: "human_serving",
    HandoffState.RESOLVED: "resolved",
}

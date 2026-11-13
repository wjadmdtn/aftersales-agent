"""Agent 领域模型：意图、会话、待确认操作、对话轮次。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Intent(StrEnum):
    """六个业务意图 + 一个兜底。

    2026-11-13 口径：删除 complaint / general 两个语义桶。
    - 抱怨、不满、情绪化表达不再是独立意图，统一落 UNKNOWN，
      由 AI 安抚即可，不转人工、不做业务动作。
    - 转人工只由用户主动触发（manual）+ 系统异常/能力边界两条例外。
    """

    REFUND = "refund"
    LOGISTICS = "logistics"
    CHANGE_ADDRESS = "change_address"
    QUERY_ORDER = "query_order"
    MANUAL = "manual"
    POLICY = "policy"      # 政策咨询：规则是什么，不是"我这一单怎样"
    # 诉求不明（含抱怨、闲聊、无法归类）。行为 == 安抚/澄清，绝不调工具、绝不转人工。
    UNKNOWN = "unknown"


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


class IntentMode(StrEnum):
    """同一句话，模式不同，行为完全不同。

    "我要退款"      → EXECUTE  弹确认后执行
    "这个能退款吗"   → INQUIRY  回答能不能，不执行
    "退款到哪了"     → QUERY    查进度，不执行
    "退了吧"（无上下文）→ NEED_CONTEXT  追问缺失信息
    """

    EXECUTE = "execute"
    INQUIRY = "inquiry"
    QUERY = "query"
    NEED_CONTEXT = "need_context"


# 含这些词倾向 INQUIRY（疑问/假设）
INQUIRY_MARKERS = ("吗", "能不能", "可不可以", "行不行", "如何", "怎么", "是否", "可以吗", "吗？")
# 含这些词倾向 EXECUTE（祈使）
EXECUTE_MARKERS = ("我要", "帮我", "麻烦", "请", "给我", "退了吧", "退了吧", "改成", "退了")


class IntentSource(StrEnum):
    LLM = "llm"
    DEGRADED = "degraded"


class IntentResult(BaseModel):
    intent: Intent
    mode: IntentMode = IntentMode.EXECUTE
    confidence: float = Field(ge=0, le=1)
    source: IntentSource = IntentSource.LLM
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    # 意图识别顺带产出的检索改写：仅 policy 意图时有值，供政策检索用。
    # 生成与 rerank 仍以用户原话为准；None = 不改写（行为与从前一致）。
    search_query: str | None = None


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
    # 最近一次政策检索的 top 相似度。跨轮保留，转人工时用它判断是否知识缺口。
    last_rag_top_similarity: float | None = None
    # 政策检索：最近一次引用的条款（多轮追问用）与发生检索的轮次数（评测用）
    last_cited_clause_ids: list[str] = Field(default_factory=list)
    policy_turns: int = 0
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

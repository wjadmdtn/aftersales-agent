"""Agent 对话接口。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.agent.intent import IntentClassifier
from app.agent.loop import AgentLoop
from app.agent.session import SessionStore
from app.agent.tools.client import MockServiceClient
from app.models.agent import OrderSnapshot
from app.observability.trace import current_trace_id

router = APIRouter(prefix="/agent")


class SnapshotIn(BaseModel):
    name: str | None = None
    amount: Decimal | None = None
    status: str | None = None


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    order_id: str | None = None
    snapshot: SnapshotIn | None = None


class ChatResponse(BaseModel):
    reply: str
    intent: str | None = None
    mode: str | None = None
    source: str
    tool_calls: list[str] = Field(default_factory=list)
    state: str
    trace_id: str


_loop: AgentLoop | None = None


def configure_agent(
    *,
    classifier: IntentClassifier,
    sessions: SessionStore,
    tools: MockServiceClient,
    coordinator=None,
    policy=None,
    on_handoff=None,
) -> None:
    global _loop
    _loop = AgentLoop(
        classifier=classifier,
        sessions=sessions,
        tools=tools,
        coordinator=coordinator,
        policy=policy,
        on_handoff=on_handoff,
    )


def get_loop() -> AgentLoop:
    if _loop is None:
        raise RuntimeError("agent loop is not configured")
    return _loop


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    trace_id = current_trace_id()
    snapshot = (
        OrderSnapshot(
            name=req.snapshot.name, amount=req.snapshot.amount, status=req.snapshot.status
        )
        if req.snapshot
        else None
    )
    result = await get_loop().handle(
        session_id=req.session_id,
        user_id=req.user_id,
        message=req.message,
        trace_id=trace_id,
        order_id=req.order_id,
        snapshot=snapshot,
    )
    return ChatResponse(
        reply=result.reply,
        intent=result.intent.value if result.intent else None,
        mode=result.mode,
        source=result.source.value,
        tool_calls=result.tool_calls,
        state=result.state,
        trace_id=trace_id,
    )


@router.get("/health/llm")
async def llm_health() -> dict:
    """LLM 调用的健康指标，供告警系统拉取。

    failure_rate 持续高于阈值、或 recent_failures 在短时间内激增，
    说明 LLM 链路出问题，应触发告警。
    """
    from app.agent.resilience import stats

    return {
        "total_calls": stats.total_calls,
        "total_attempts": stats.total_attempts,
        "successes": stats.successes,
        "failures": stats.failures,
        "failure_rate": round(stats.failure_rate, 4),
        "retry_rate": round(stats.retry_rate, 4),
        "failures_last_10min": len(stats.recent_failures),
        "degraded": stats.total_calls > 0 and stats.failure_rate > 0.2,
    }

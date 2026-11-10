"""人工侧接口。

三个动作都要"先落业务、再广播"：业务失败不得让界面显示已接入。
接入还要抢锁——现有 HandoffQueue.accept 是 get_pack + zrem 三步，
两人同时接入会都成功，违反 FR-015。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.handoff.coordinator import HandoffCoordinator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/handoff")

_coordinator: HandoffCoordinator | None = None


def configure_handoff(coordinator: HandoffCoordinator) -> None:
    global _coordinator
    _coordinator = coordinator


def get_coordinator() -> HandoffCoordinator:
    if _coordinator is None:
        raise RuntimeError("handoff coordinator is not configured")
    return _coordinator


class SessionIn(BaseModel):
    session_id: str
    agent_id: str | None = None
    agent_name: str | None = None


def _agent(body: SessionIn) -> tuple[str, str]:
    return (
        body.agent_id or settings.demo_agent_id,
        body.agent_name or settings.demo_agent_name,
    )


@router.get("/queue")
async def get_queue() -> dict:
    queue = get_coordinator().queue
    items = await queue.peek()
    return {
        "depth": await queue.depth(),
        "by_priority": await queue.depth_by_priority(),
        "items": [ctx.model_dump(mode="json") for ctx in items],
    }


@router.post("/accept")
async def accept(body: SessionIn) -> dict:
    from app.chat.broadcast import on_queue_accepted
    from app.chat.runtime import get_chat

    agent_id, agent_name = _agent(body)
    rt = get_chat()

    claimed = await rt.registry.try_claim(body.session_id, agent_id)
    if not claimed:
        owner = await rt.registry.claim_owner(body.session_id)
        raise HTTPException(
            status_code=409,
            detail={"code": "already_claimed", "owner_agent_id": owner},
        )

    outcome = await get_coordinator().human_accept(body.session_id)
    if outcome is None:
        # 队列里没有：可能是已接入但状态未同步，也可能根本不存在
        await rt.registry.release_claim(body.session_id)
        raise HTTPException(status_code=404, detail="没有该会话的排队记录")

    await on_queue_accepted(body.session_id, agent_id, agent_name)
    return {
        "state": outcome.state.value,
        "events": outcome.events,
        "agent_id": agent_id,
        "agent_name": agent_name,
    }


@router.post("/decline")
async def decline(body: SessionIn) -> dict:
    from app.chat.broadcast import on_queue_declined

    outcome = await get_coordinator().human_decline(body.session_id)
    if outcome is None:
        raise HTTPException(status_code=404, detail="没有该会话的排队记录")
    await on_queue_declined(body.session_id)
    return {
        "state": outcome.state.value,
        "events": outcome.events,
        "reply": outcome.reply,
    }


@router.post("/resolve")
async def resolve(body: SessionIn) -> dict:
    from app.chat.broadcast import on_resolved
    from app.chat.runtime import get_chat

    agent_id, _ = _agent(body)
    rt = get_chat()
    state = await rt.registry.get(body.session_id)

    if state is not None and state.agent_id and state.agent_id != agent_id:
        raise HTTPException(
            status_code=403, detail={"code": "not_owner", "owner_agent_id": state.agent_id}
        )

    await get_coordinator().resolve(body.session_id)

    if state is not None:
        try:
            await on_resolved(body.session_id)
        except ValueError:
            pass  # 已经是终态
    return {"state": "resolved"}

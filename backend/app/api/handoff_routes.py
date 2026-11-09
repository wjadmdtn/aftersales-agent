"""人工侧接口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.handoff.coordinator import HandoffCoordinator
from app.handoff.queue import HandoffQueue

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
    outcome = await get_coordinator().human_accept(body.session_id)
    if outcome is None:
        raise HTTPException(status_code=404, detail="没有该会话的排队记录")
    return {"state": outcome.state.value, "events": outcome.events}


@router.post("/decline")
async def decline(body: SessionIn) -> dict:
    outcome = await get_coordinator().human_decline(body.session_id)
    if outcome is None:
        raise HTTPException(status_code=404, detail="没有该会话的排队记录")
    return {
        "state": outcome.state.value,
        "events": outcome.events,
        "reply": outcome.reply,
    }


@router.post("/resolve")
async def resolve(body: SessionIn) -> dict:
    await get_coordinator().resolve(body.session_id)
    return {"state": "resolved"}

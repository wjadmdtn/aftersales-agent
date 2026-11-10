"""会话查询面（FR-029/030）。

只做读取，不产生状态变更——状态变更的入口只有 WS 与 handoff 三个动作。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.chat.protocol import UI_STATE_OF
from app.chat.runtime import get_chat

router = APIRouter(prefix="/chat")


@router.get("/session/{session_id}")
async def get_session(
    session_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1),
) -> dict:
    rt = get_chat()
    state = await rt.registry.get(session_id)
    if state is None:
        raise HTTPException(
            status_code=404, detail={"code": "session_not_found", "message": "会话不存在"}
        )
    page = await rt.messages.backfill(
        session_id, after_seq=after_seq, limit=limit or 200
    )
    state = await rt.registry.touch_seq(session_id, page.last_seq)
    state_dump = state.model_dump(mode="json")
    state_dump["ui_state"] = UI_STATE_OF[state.state]
    return {
        "state": state_dump,
        "messages": [m.model_dump(mode="json") for m in page.messages],
        "last_seq": page.last_seq,
        "truncated": page.truncated,
    }


@router.get("/sessions")
async def list_sessions(
    agent_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    rt = get_chat()
    if bool(agent_id) == bool(user_id):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_query", "message": "agent_id 与 user_id 必须且只能提供一个"},
        )
    states = (
        await rt.registry.list_for_agent(agent_id)  # type: ignore[arg-type]
        if agent_id
        else await rt.registry.list_for_user(user_id)  # type: ignore[arg-type]
    )
    items = []
    for st in states:
        msgs = await rt.messages.list(st.session_id)
        last = msgs[-1] if msgs else None
        items.append(
            {
                "session_id": st.session_id,
                "state": st.state.value,
                "agent_id": st.agent_id,
                "agent_name": st.agent_name,
                "order_id": st.order_id,
                "last_message": last.text if last else None,
                "last_ts": last.ts.isoformat() if last else st.updated_at.isoformat(),
            }
        )
    return {"items": items}

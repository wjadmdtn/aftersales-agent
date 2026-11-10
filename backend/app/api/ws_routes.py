"""WebSocket 端点：用户端 /ws/chat，人工端 /ws/agent。

协议见 specs/006-web-ui/contracts/websocket.md。
这里只做"接线"：校验 → 分发到 chat/streamer.py 与 chat/broadcast.py。
业务规则（能否 AI 说话、优先级）不在这里判断。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.chat import streamer
from app.chat.hub import (
    CLOSE_INVALID_SESSION,
    CLOSE_NOT_OWNED,
    CLOSE_ORDER_NOT_FOUND,
    CLOSE_IDLE_TIMEOUT,
    Connection,
    Hub,
)
from app.chat.protocol import Envelope, MessageType, envelope
from app.chat.runtime import get_chat
from app.config import settings
from app.models.enums import ResultCode

logger = logging.getLogger(__name__)

router = APIRouter()

# 握手时校验订单要查 mock 服务；这里延迟导入避免循环
async def _order_exists(order_id: str) -> bool:
    from app.api.agent_routes import get_loop

    loop = get_loop()
    body = await loop.tools.get_order(order_id, trace_id="ws-handshake")
    return body.get("code") == ResultCode.OK


async def _hello(conn: Connection) -> None:
    conn.send(
        envelope(
            MessageType.HELLO,
            session_id=conn.session_id,
            payload={
                "heartbeat_interval_s": settings.ws_heartbeat_interval_s,
                "idle_timeout_s": settings.ws_idle_timeout_s,
                "reconnect": {
                    "base_s": settings.ws_reconnect_base_s,
                    "max_s": settings.ws_reconnect_max_s,
                },
                "server_time": datetime.now(timezone.utc).isoformat(),
            },
        )
    )


async def _serve(conn: Connection, hub: Hub) -> None:
    while True:
        try:
            raw = await asyncio.wait_for(
                conn.ws.receive_text(), timeout=settings.ws_idle_timeout_s
            )
        except asyncio.TimeoutError:
            await conn.ws.close(code=CLOSE_IDLE_TIMEOUT, reason="idle timeout")
            return
        except WebSocketDisconnect:
            return

        try:
            frame = Envelope.model_validate_json(raw)
        except ValidationError:
            conn.send(
                envelope(
                    MessageType.ERROR,
                    session_id=conn.session_id,
                    payload={"code": "bad_frame", "message": "无法解析的消息", "retryable": False},
                )
            )
            continue

        if frame.type is MessageType.PONG:
            continue
        if frame.type is MessageType.USER_MESSAGE:
            await streamer.handle_user_message(conn, frame)
        elif frame.type is MessageType.USER_HANDOFF:
            await streamer.handle_user_handoff(conn, frame)
        elif frame.type is MessageType.AGENT_MESSAGE:
            await streamer.handle_agent_message(conn, frame)
        else:
            conn.send(
                envelope(
                    MessageType.ERROR,
                    session_id=conn.session_id,
                    payload={
                        "code": "bad_frame",
                        "message": f"不支持的消息类型 {frame.type}",
                        "retryable": False,
                    },
                )
            )


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket, session_id: str, user_id: str,
                  order_id: str | None = None) -> None:
    if not session_id or not user_id:
        await websocket.close(code=CLOSE_INVALID_SESSION, reason="invalid session")
        return

    rt = get_chat()
    existing = await rt.registry.get(session_id)
    if existing is not None and existing.user_id != user_id:
        await websocket.close(code=CLOSE_NOT_OWNED, reason="session not owned")
        return

    # 只有首次建会话才接受握手带入的订单，避免握手悄悄改写已有上下文
    if existing is None and order_id and not await _order_exists(order_id):
        await websocket.close(code=CLOSE_ORDER_NOT_FOUND, reason="order not found")
        return

    await websocket.accept()
    state = await rt.registry.ensure(session_id, user_id=user_id, order_id=order_id)
    if existing is None:
        from app.agent.session import SessionStore

        sessions: SessionStore = _sessions()
        await sessions.get_or_create(session_id, user_id=user_id, order_id=state.order_id)

    conn = Connection(ws=websocket, role="user", user_id=user_id, session_id=session_id)
    rt.hub.register(conn)
    await _hello(conn)
    try:
        await _serve(conn, rt.hub)
    finally:
        rt.hub.unregister(conn)


@router.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket, agent_id: str | None = None) -> None:
    if not agent_id:
        await websocket.close(code=CLOSE_INVALID_SESSION, reason="invalid session")
        return

    await websocket.accept()
    rt = get_chat()
    conn = Connection(ws=websocket, role="agent", agent_id=agent_id)
    rt.hub.register(conn)
    await _hello(conn)
    # 连上先给一次完整队列，避免依赖增量事件
    from app.api.handoff_routes import get_coordinator

    try:
        queue = await get_coordinator().queue.peek()
        conn.send(
            envelope(
                MessageType.QUEUE_UPDATE,
                session_id=None,
                payload={
                    "added": [c.session_id for c in queue],
                    "removed": [],
                    "depth": len(queue),
                },
            )
        )
    except Exception:
        logger.exception("agent hello queue snapshot failed")
    try:
        await _serve(conn, rt.hub)
    finally:
        rt.hub.unregister(conn)


def _sessions():
    from app.api.agent_routes import get_loop

    return get_loop().sessions

"""WS 连接管理器。

每个连接一个独立发送队列 + 发送协程：慢客户端只会堆积自己的队列，
不会拖住广播方（否则一个人卡住全站卡住）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.chat.protocol import Envelope, MessageType, envelope

logger = logging.getLogger(__name__)

# close codes（contracts/websocket.md §6）
CLOSE_INVALID_SESSION = 4401
CLOSE_NOT_OWNED = 4403
CLOSE_ORDER_NOT_FOUND = 4404
CLOSE_IDLE_TIMEOUT = 4408


@dataclass(eq=False)
class Connection:
    ws: WebSocket
    role: str                      # "user" | "agent"
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None  # 用户端固定；人工端为 None（关心多会话）
    queue: asyncio.Queue[Envelope] = field(default_factory=asyncio.Queue)
    _closed: bool = False

    def matches_session(self, session_id: str | None) -> bool:
        if self.role == "agent":
            return True
        return session_id is not None and self.session_id == session_id

    def send(self, env: Envelope) -> None:
        if not self._closed:
            self.queue.put_nowait(env)


class Hub:
    def __init__(self) -> None:
        self.connections: set[Connection] = set()
        self._tasks: set[asyncio.Task] = set()

    def register(self, conn: Connection) -> None:
        self.connections.add(conn)
        task = asyncio.create_task(self._sender(conn))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def unregister(self, conn: Connection) -> None:
        conn._closed = True
        self.connections.discard(conn)

    async def _sender(self, conn: Connection) -> None:
        try:
            while True:
                env = await conn.queue.get()
                if conn.ws.client_state is not WebSocketState.CONNECTED:
                    return
                await conn.ws.send_text(env.model_dump_json())
        except (asyncio.CancelledError, RuntimeError):
            return
        except Exception:
            logger.exception("ws sender crashed role=%s", conn.role)

    async def publish(
        self,
        env: Envelope,
        *,
        to_session: str | None = None,
        to_agents: bool = False,
        to_user: str | None = None,
    ) -> int:
        """把帧投递给匹配的连接。返回投递数（便于测试断言）。

        人工端：整队广播（to_agents）或该会话相关的帧（to_session）
        用户端：只收自己会话的帧；to_user 用于按用户定向
        """
        n = 0
        for conn in list(self.connections):
            if conn.role == "agent":
                if to_agents or (to_session and conn.matches_session(to_session)):
                    conn.send(env)
                    n += 1
            else:
                if to_session is not None and conn.session_id != to_session:
                    continue
                if to_user is not None and conn.user_id != to_user:
                    continue
                if to_session is None and to_user is None:
                    continue
                conn.send(env)
                n += 1
        return n

    async def publish_state(self, session_id: str, state_payload: dict[str, Any], *, seq: int = 0) -> None:
        await self.publish(
            envelope(MessageType.SESSION_STATE, session_id=session_id, seq=seq, payload=state_payload),
            to_session=session_id,
            to_agents=True,
        )

    async def publish_queue_update(self, payload: dict[str, Any]) -> None:
        await self.publish(
            envelope(MessageType.QUEUE_UPDATE, session_id=None, payload=payload),
            to_agents=True,
        )

    async def heartbeat_loop(self, interval_s: int, idle_timeout_s: int) -> None:
        """周期发 ping；调用方的 receive 循环负责在超时后断开。"""
        try:
            while True:
                await asyncio.sleep(interval_s)
                for conn in list(self.connections):
                    conn.send(envelope(MessageType.PING, session_id=conn.session_id, payload={}))
        except asyncio.CancelledError:
            return

    async def close_all(self) -> None:
        for conn in list(self.connections):
            conn._closed = True
            try:
                await conn.ws.close()
            except Exception:
                pass
        for t in list(self._tasks):
            t.cancel()
        self.connections.clear()

"""chat 模块的运行时单例。

装配在 main.py lifespan 里做，路由通过 get_chat() 取用。
reset_chat() 供测试在用例间清场。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.chat.hub import Hub
from app.chat.messages import MessageStore
from app.chat.session_registry import SessionRegistry


@dataclass
class ChatRuntime:
    hub: Hub
    messages: MessageStore
    registry: SessionRegistry
    # 每个会话一把异步锁：同一会话一次只处理一条消息（含地址提交）。
    # 单连接读帧本就串行，这里防的是多连接（重连 / 多标签页）并发改同一份会话状态。
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict, repr=False)

    def session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def close(self) -> None:
        await self.hub.close_all()


_runtime: ChatRuntime | None = None


def configure_chat(runtime: ChatRuntime) -> None:
    global _runtime
    _runtime = runtime


def get_chat() -> ChatRuntime:
    if _runtime is None:
        raise RuntimeError("chat runtime is not configured")
    return _runtime


def reset_chat() -> None:
    global _runtime
    _runtime = None

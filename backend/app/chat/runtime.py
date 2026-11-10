"""chat 模块的运行时单例。

装配在 main.py lifespan 里做，路由通过 get_chat() 取用。
reset_chat() 供测试在用例间清场。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.chat.hub import Hub
from app.chat.messages import MessageStore
from app.chat.session_registry import SessionRegistry


@dataclass
class ChatRuntime:
    hub: Hub
    messages: MessageStore
    registry: SessionRegistry

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

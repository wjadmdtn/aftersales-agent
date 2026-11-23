"""会话锁：同一会话串行处理，防止多连接并发改会话状态。"""

import asyncio

from app.chat.runtime import ChatRuntime


def _rt() -> ChatRuntime:
    # 只测锁原语，hub/messages/registry 不参与
    return ChatRuntime(hub=None, messages=None, registry=None)  # type: ignore[arg-type]


def test_same_session_shares_one_lock():
    rt = _rt()
    assert rt.session_lock("s1") is rt.session_lock("s1")
    assert rt.session_lock("s1") is not rt.session_lock("s2")


async def test_lock_serializes_critical_sections():
    rt = _rt()
    lock = rt.session_lock("s1")
    order: list[str] = []

    async def worker(name: str) -> None:
        async with lock:
            order.append(f"{name}:enter")
            await asyncio.sleep(0.01)
            order.append(f"{name}:exit")

    await asyncio.gather(worker("a"), worker("b"))

    # 不允许交错：一个人 exit 之前另一个人不能 enter
    assert order in (
        ["a:enter", "a:exit", "b:enter", "b:exit"],
        ["b:enter", "b:exit", "a:enter", "a:exit"],
    )

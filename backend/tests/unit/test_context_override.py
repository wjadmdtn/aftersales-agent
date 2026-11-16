"""全局单例的 ContextVar 覆盖：评测并发靠它隔离每个 worker 的状态。"""
from __future__ import annotations

import asyncio

from app.api import agent_routes
from app.api import routes as mock_routes
from app.store.memory import MemoryStore


def test_store_override_takes_precedence_then_restores():
    global_store = mock_routes.get_store()
    other = MemoryStore(mock_routes.settings.seed_path, 4)

    with mock_routes.use_store(other):
        assert mock_routes.get_store() is other
        assert mock_routes.get_store() is not global_store

    assert mock_routes.get_store() is not other


def test_store_override_isolated_between_tasks():
    """并发 worker 各自覆盖，互不可见——这是评测不串数据的根据。"""
    a = MemoryStore(mock_routes.settings.seed_path, 4)
    b = MemoryStore(mock_routes.settings.seed_path, 4)
    seen: dict[str, int] = {}

    async def boot(name, store):
        with mock_routes.use_store(store):
            await asyncio.sleep(0)
            seen[name] = id(mock_routes.get_store())

    async def main():
        await asyncio.gather(boot("a", a), boot("b", b))

    asyncio.run(main())
    assert seen["a"] == id(a)
    assert seen["b"] == id(b)


def test_loop_override_takes_precedence_and_restores():
    original = agent_routes._loop
    fake = object()

    with agent_routes.use_loop(fake):
        assert agent_routes.get_loop() is fake

    assert agent_routes._loop is original
    if original is not None:
        assert agent_routes.get_loop() is original

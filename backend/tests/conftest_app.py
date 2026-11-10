"""006: 走真实生产装配的 app 夹具。

与 conftest_agent.env 的区别：这里不手工 configure_*，而是让 create_app() 的
lifespan 自己装配——这样"生产启动能不能跑"本身也是被测对象（FR-031）。

WebSocket 用同步 TestClient：事件循环跑在后台线程，async 用例里通过
anyio.to_thread 调用，避免两种循环互相污染。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import anyio
import pytest
from starlette.testclient import TestClient

import app.api.routes as mock_routes
from app.api import agent_routes, chat_routes, handoff_routes
from app.chat import redis as chat_redis
from app.main import create_app


@pytest.fixture
def fake_redis_for_prod(monkeypatch):
    """把 lifespan 里的 Redis.from_url 换成 fakeredis。

    只替换连接构造，不替换被测代码路径——装配逻辑仍原样执行。
    """
    import fakeredis.aioredis

    created: list[Any] = []

    def _factory(*_a, **_kw):
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        created.append(r)
        return r

    monkeypatch.setattr(chat_redis, "make_redis", _factory)
    return created


@pytest.fixture
def prod_app(monkeypatch, fake_redis_for_prod):
    """重置 mock store 并返回未启动的 app。"""
    mock_routes.reset_store()
    agent_routes._loop = None
    handoff_routes._coordinator = None
    chat_routes.reset_chat()
    app = create_app()
    yield app
    agent_routes._loop = None
    handoff_routes._coordinator = None
    chat_routes.reset_chat()


@pytest.fixture
def http(prod_app):
    with TestClient(prod_app) as client:
        yield client


class WsClient:
    """同步测试客户端的 WebSocket 包装，提供 collect_until 便于断言序列。"""

    def __init__(self, ws):
        self.ws = ws

    def send(self, payload: dict) -> None:
        self.ws.send_text(json.dumps(payload, ensure_ascii=False))

    def recv(self) -> dict:
        return json.loads(self.ws.receive_text())

    def recv_until(self, type_: str, *, skip_system: bool = True, limit: int = 50) -> dict:
        for _ in range(limit):
            frame = self.recv()
            if frame["type"] == type_:
                return frame
            if skip_system and frame["type"] in ("hello", "ping", "pong"):
                continue
            if not skip_system:
                continue
        raise AssertionError(f"在 {limit} 帧内没有收到 {type_}")

    def drain(self, *, limit: int = 50) -> list[dict]:
        out = []
        for _ in range(limit):
            try:
                out.append(self.recv())
            except Exception:
                break
        return out


@pytest.fixture
def ws_user(http):
    """用户端连接。用法：ws_user(session_id="s1", user_id="u1", order_id="ORD1002")"""

    def _open(session_id: str = "s1", user_id: str = "u1", order_id: str | None = None):
        params = {"session_id": session_id, "user_id": user_id}
        if order_id:
            params["order_id"] = order_id
        ws = http.websocket_connect("/ws/chat?" + "&".join(f"{k}={v}" for k, v in params.items()))
        ctx = ws.__enter__()
        return WsClient(ctx)

    return _open


@pytest.fixture
def ws_agent(http):
    def _open(agent_id: str = "demo-agent"):
        ws = http.websocket_connect(f"/ws/agent?agent_id={agent_id}")
        ctx = ws.__enter__()
        return WsClient(ctx)

    return _open


async def in_thread(fn, *args, **kwargs):
    """在 worker 线程里执行同步 TestClient 调用，避免阻塞事件循环。"""
    return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))


@pytest.fixture(autouse=True)
def _reset_chat_state():
    chat_routes.reset_chat()
    yield
    chat_routes.reset_chat()


def run_sync(coro):
    """给需要在同步测试里跑协程的场景用。"""
    return asyncio.get_event_loop().run_until_complete(coro)

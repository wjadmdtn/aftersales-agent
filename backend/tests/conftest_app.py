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
from app.api import agent_routes, handoff_routes
from app.chat import redis as chat_redis
from app.chat.runtime import reset_chat
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
    """重置 mock store 并返回未启动的 app。

    强制走 stub：本地 .env 可能指向真实内网 LLM，测试绝不能打到它。
    业务接口走 in-process：Agent 与 mock 服务在同一个 app 里。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "llm_base_url", "", raising=False)
    monkeypatch.setattr(settings, "policy_enabled", False, raising=False)
    mock_routes.reset_store()
    agent_routes._loop = None
    handoff_routes._coordinator = None
    reset_chat()
    app = create_app()
    yield app
    agent_routes._loop = None
    handoff_routes._coordinator = None
    reset_chat()


@pytest.fixture
def http(prod_app):
    with TestClient(prod_app) as client:
        # 业务接口 in-process：TestClient 不监听网络端口，tools 必须换掉
        from app.api.agent_routes import _loop
        from tests.conftest_agent import InProcessMockClient

        if _loop is not None:
            _loop.tools = InProcessMockClient(prod_app)
        yield client


class _WsRegistry:
    """收集测试里开的 WS，用例结束统一关闭，否则 TestClient 退出会挂死。"""

    def __init__(self):
        self.open = []

    def track(self, ws):
        self.open.append(ws)

    def close_all(self):
        while self.open:
            ws = self.open.pop()
            try:
                ws.__exit__(None, None, None)
            except Exception:
                pass


@pytest.fixture
def ws_registry():
    reg = _WsRegistry()
    yield reg
    reg.close_all()


@pytest.fixture
def ws_user(http, ws_registry):
    """用户端连接。用法：ws_user(session_id="s1", user_id="u1", order_id="ORD1002")"""

    def _open(session_id: str = "s1", user_id: str = "u1", order_id: str | None = None):
        params = {"session_id": session_id, "user_id": user_id}
        if order_id:
            params["order_id"] = order_id
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        ctx = http.websocket_connect("/ws/chat?" + qs)
        ws_registry.track(ctx)
        return WsClient(ctx.__enter__())

    return _open


@pytest.fixture
def ws_agent(http, ws_registry):
    def _open(agent_id: str = "demo-agent"):
        ctx = http.websocket_connect(f"/ws/agent?agent_id={agent_id}")
        ws_registry.track(ctx)
        return WsClient(ctx.__enter__())

    return _open


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


async def in_thread(fn, *args, **kwargs):
    """在 worker 线程里执行同步 TestClient 调用，避免阻塞事件循环。"""
    return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))


@pytest.fixture(autouse=True)
def _reset_chat_state():
    reset_chat()
    yield
    reset_chat()


def run_sync(coro):
    """给需要在同步测试里跑协程的场景用。"""
    return asyncio.get_event_loop().run_until_complete(coro)

"""T010: 连接分发与心跳。

这里测的是纯粹的路由逻辑：一帧该送给谁。
真正通过 WebSocket 收发的集成测试在 tests/integration/。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.chat.hub import Connection, Hub
from app.chat.protocol import MessageType, envelope


from starlette.websockets import WebSocketState


class FakeWs:
    def __init__(self):
        self.sent: list[str] = []
        self.client_state = WebSocketState.CONNECTED
        self.closed = False

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self) -> None:
        self.closed = True


def _conn(role, **kw) -> Connection:
    return Connection(ws=FakeWs(), role=role, **kw)  # type: ignore[arg-type]


async def _flush():
    # 让发送协程跑一轮
    for _ in range(3):
        await asyncio.sleep(0)


async def test_session_frame_reaches_only_that_user():
    hub = Hub()
    a = _conn("user", user_id="u1", session_id="s1")
    b = _conn("user", user_id="u2", session_id="s2")
    hub.register(a)
    hub.register(b)

    env = envelope(MessageType.ASSISTANT_DELTA, session_id="s1", seq=1, payload={"text_delta": "hi"})
    sent = await hub.publish(env, to_session="s1")
    await _flush()

    assert sent == 1
    assert len(a.ws.sent) == 1
    assert b.ws.sent == []


async def test_agent_receives_session_frames():
    hub = Hub()
    agent = _conn("agent", agent_id="demo-agent")
    hub.register(agent)

    env = envelope(MessageType.SESSION_STATE, session_id="s1", payload={"state": "queued"})
    await hub.publish(env, to_session="s1")
    await _flush()
    assert len(agent.ws.sent) == 1


async def test_queue_update_goes_to_all_agents_only():
    hub = Hub()
    agent = _conn("agent", agent_id="a1")
    user = _conn("user", user_id="u1", session_id="s1")
    hub.register(agent)
    hub.register(user)

    await hub.publish_queue_update({"added": ["s1"], "removed": [], "depth": 1})
    await _flush()

    assert len(agent.ws.sent) == 1
    assert user.ws.sent == []


async def test_unregistered_connection_gets_nothing():
    hub = Hub()
    a = _conn("user", user_id="u1", session_id="s1")
    hub.register(a)
    hub.unregister(a)
    await hub.publish(envelope(MessageType.PING, session_id="s1"), to_session="s1")
    await _flush()
    assert a.ws.sent == []


async def test_slow_connection_does_not_block_others():
    hub = Hub()
    slow, fast = _conn("user", user_id="u1", session_id="s1"), _conn("user", user_id="u2", session_id="s1")
    hub.register(slow)
    hub.register(fast)

    for i in range(50):
        await hub.publish(envelope(MessageType.ASSISTANT_DELTA, session_id="s1", seq=i, payload={}),
                          to_session="s1")
    await _flush()
    assert len(slow.ws.sent) == 50
    assert len(fast.ws.sent) == 50


async def test_close_all_clears_registry():
    hub = Hub()
    a = _conn("user", user_id="u1", session_id="s1")
    hub.register(a)
    await hub.close_all()
    assert hub.connections == set()
    assert a.ws.closed is True


async def test_heartbeat_sends_ping_periodically():
    hub = Hub()
    a = _conn("user", user_id="u1", session_id="s1")
    hub.register(a)
    task = asyncio.create_task(hub.heartbeat_loop(interval_s=0, idle_timeout_s=1))
    await asyncio.sleep(0.05)
    task.cancel()
    await _flush()
    import json

    frames = [json.loads(s) for s in a.ws.sent]
    assert any(f["type"] == "ping" for f in frames)

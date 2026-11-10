"""T049: 人工回复与结束（US6）。

US6-2 最关键：人工服务期间用户发消息，AI 不得出现在中间。
这与宪法 III「影响真人的决策不交给模型」是同一件事的两面。
"""
from __future__ import annotations

import pytest

from tests.conftest_app import *  # noqa: F401,F403


def _in_service(http, ws_user, sid="s1"):
    ws = ws_user(session_id=sid, user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")
    http.post("/handoff/accept", json={"session_id": sid, "agent_id": "demo-agent"})
    ws.recv_until("session.state")
    return ws


def test_agent_message_reaches_user(http, ws_user, ws_agent):
    ws = _in_service(http, ws_user)
    agent = ws_agent("demo-agent")
    agent.recv_until("queue.update")

    agent.send({"type": "agent.message", "payload": {"session_id": "s1", "text": "您好，我来处理"}})
    frame = ws.recv_until("human.message")
    assert frame["payload"]["text"] == "您好，我来处理"
    assert frame["payload"]["role"] == "human"


def test_agent_message_persisted(http, ws_user, ws_agent):
    _in_service(http, ws_user)
    agent = ws_agent("demo-agent")
    agent.recv_until("queue.update")
    agent.send({"type": "agent.message", "payload": {"session_id": "s1", "text": "已受理"}})

    body = http.get("/chat/session/s1").json()
    assert any(m["role"] == "human" and m["text"] == "已受理" for m in body["messages"])


def test_user_message_goes_to_human_not_ai(http, ws_user, ws_agent, monkeypatch):
    """US6-2：人工服务中用户发消息，AI 不出现在中间。"""
    from app.api.agent_routes import _loop

    calls = {"n": 0}
    original = _loop.classifier.llm.classify

    async def _spy(*a, **kw):
        calls["n"] += 1
        return await original(*a, **kw)

    monkeypatch.setattr(_loop.classifier.llm, "classify", _spy)

    ws = _in_service(http, ws_user)
    before = calls["n"]

    agent = ws_agent("demo-agent")
    agent.recv_until("queue.update")

    ws.send({"type": "user.message", "payload": {"text": "好的谢谢"}})
    frame = agent.recv_until("human.message")
    assert frame["payload"]["text"] == "好的谢谢"
    assert frame["payload"]["role"] == "user"
    assert calls["n"] == before, "人工服务期间不得调用 LLM"


def test_agent_message_rejected_from_non_owner(http, ws_user, ws_agent):
    _in_service(http, ws_user)
    other = ws_agent("agent-x")
    other.recv_until("queue.update")
    other.send({"type": "agent.message", "payload": {"session_id": "s1", "text": "我来插一脚"}})
    err = other.recv_until("error")
    assert err["payload"]["code"] == "not_owner"


def test_agent_message_rejected_when_not_serving(http, ws_user, ws_agent):
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    agent = ws_agent("demo-agent")
    agent.recv_until("queue.update")
    agent.send({"type": "agent.message", "payload": {"session_id": "s1", "text": "hi"}})
    err = agent.recv_until("error")
    assert err["payload"]["code"] == "not_owner"


def test_resolve_notifies_both_sides(http, ws_user, ws_agent):
    ws = _in_service(http, ws_user)
    agent = ws_agent("demo-agent")
    agent.recv_until("queue.update")

    http.post("/handoff/resolve", json={"session_id": "s1", "agent_id": "demo-agent"})

    user_state = ws.recv_until("session.state")
    assert user_state["payload"]["ui_state"] == "resolved"
    agent_state = agent.recv_until("session.state")
    assert agent_state["payload"]["ui_state"] == "resolved"


def test_non_owner_cannot_resolve(http, ws_user):
    _in_service(http, ws_user)
    r = http.post("/handoff/resolve", json={"session_id": "s1", "agent_id": "agent-x"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "not_owner"

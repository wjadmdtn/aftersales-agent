"""T026: 状态广播（US2，FR-004/016）。

四类界面状态：ai_serving / queued / human_serving / resolved。
handoff_requested 在同一请求内即迁移，对外仍映射为 ai_serving。
"""
from __future__ import annotations

from tests.conftest_app import *  # noqa: F401,F403


def _open(ws_user, sid="s1"):
    ws = ws_user(session_id=sid, user_id="u1", order_id="ORD1002")
    ws.recv()
    return ws


def test_session_starts_in_ai_serving(http, ws_user):
    _open(ws_user)
    body = http.get("/chat/session/s1").json()
    assert body["state"]["state"] == "normal"
    assert body["state"]["ui_state"] == "ai_serving"


def test_handoff_requested_maps_to_ai_serving():
    """FR-004：机器态 handoff_requested 并入 ai_serving，不单列。"""
    from app.chat.protocol import UI_STATE_OF
    from app.models.handoff import HandoffState

    assert UI_STATE_OF[HandoffState.HANDOFF_REQUESTED] == "ai_serving"
    assert UI_STATE_OF[HandoffState.NORMAL] == "ai_serving"
    assert UI_STATE_OF[HandoffState.QUEUED] == "queued"
    assert UI_STATE_OF[HandoffState.HUMAN_SERVING] == "human_serving"
    assert UI_STATE_OF[HandoffState.RESOLVED] == "resolved"


def test_quick_handoff_broadcasts_queued_to_user(http, ws_user):
    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    frame = ws.recv_until("session.state")
    assert frame["payload"]["ui_state"] == "queued"
    assert frame["payload"]["state"] == "queued"


def test_accept_broadcasts_human_serving_with_agent_name(http, ws_user):
    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")

    http.post("/handoff/accept", json={"session_id": "s1", "agent_id": "demo-agent"})
    frame = ws.recv_until("session.state")
    assert frame["payload"]["ui_state"] == "human_serving"
    assert frame["payload"]["agent_name"] == "小王"


def test_resolve_broadcasts_resolved(http, ws_user):
    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")
    http.post("/handoff/accept", json={"session_id": "s1", "agent_id": "demo-agent"})
    ws.recv_until("session.state")

    http.post("/handoff/resolve", json={"session_id": "s1", "agent_id": "demo-agent"})
    frame = ws.recv_until("session.state")
    assert frame["payload"]["ui_state"] == "resolved"


def test_resolved_rejects_further_messages(http, ws_user):
    ws = _open(ws_user)
    http.post("/handoff/resolve", json={"session_id": "s1", "agent_id": "demo-agent"})
    ws.recv_until("session.state")
    ws.send({"type": "user.message", "payload": {"text": "还要问一句"}})
    err = ws.recv_until("error")
    assert err["payload"]["code"] in ("session_resolved", "not_human_serving")

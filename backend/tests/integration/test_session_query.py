"""T024: 会话状态与历史查询（FR-029）。

用户端刷新恢复与断线补齐都靠这一个端点。
"""
from __future__ import annotations

from tests.conftest_app import *  # noqa: F401,F403


def _seed(http, ws_user, session_id="s1", order_id="ORD1002"):
    ws = ws_user(session_id=session_id, user_id="u1", order_id=order_id)
    ws.recv()
    ws.send({"type": "user.message", "payload": {"text": "我要退款"}})
    ws.recv_until("assistant.done")
    return ws


def test_returns_state_and_messages(http, ws_user):
    _seed(http, ws_user)
    body = http.get("/chat/session/s1").json()
    assert body["state"]["session_id"] == "s1"
    assert body["state"]["state"] == "normal"
    assert body["state"]["ui_state"] == "ai_serving"
    assert len(body["messages"]) == 2
    assert body["truncated"] is False


def test_after_seq_filters(http, ws_user):
    _seed(http, ws_user)
    body = http.get("/chat/session/s1?after_seq=1").json()
    assert [m["seq"] for m in body["messages"]] == [2]
    assert body["last_seq"] == 2


def test_unknown_session_404(http):
    r = http.get("/chat/session/nope")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "session_not_found"


def test_limit_marks_truncated(http, ws_user):
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    for i in range(4):
        ws.send({"type": "user.message", "payload": {"text": f"订单状态 {i}"}})
        ws.recv_until("assistant.done")

    body = http.get("/chat/session/s1?limit=3").json()
    assert len(body["messages"]) == 3
    assert body["truncated"] is True


def test_list_sessions_requires_single_selector(http):
    assert http.get("/chat/sessions").status_code == 400
    assert http.get("/chat/sessions?agent_id=a&user_id=u").status_code == 400


def test_list_sessions_by_user(http, ws_user):
    _seed(http, ws_user, session_id="s1")
    body = http.get("/chat/sessions?user_id=u1").json()
    assert [i["session_id"] for i in body["items"]] == ["s1"]
    assert body["items"][0]["last_message"].startswith("已为您提交退款") or \
        body["items"][0]["last_message"].startswith("即将为订单")

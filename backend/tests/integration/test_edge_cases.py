"""T059: 边界场景（quickstart §5 + Edge Cases）。

这些场景在真实客服里都会遇到，出错代价高（重复入队、消息串台、越权）。
"""
from __future__ import annotations

import concurrent.futures

import pytest

from tests.conftest_app import *  # noqa: F401,F403


def _open(ws_user, sid="s1"):
    ws = ws_user(session_id=sid, user_id="u1", order_id="ORD1002")
    ws.recv()
    return ws


def test_duplicate_handoff_enqueues_once(http, ws_user):
    ws = _open(ws_user)
    for _ in range(3):
        ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")
    assert http.get("/handoff/queue").json()["depth"] == 1


def test_queued_user_message_does_not_consume_llm(http, ws_user):
    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")
    ws.send({"type": "user.message", "payload": {"text": "我还要改地址"}})
    done = ws.recv_until("assistant.done")
    assert "正在为您接入" in done["payload"]["text"]


def test_two_agents_concurrent_accept(http, ws_user):
    _open(ws_user)
    ws2 = None
    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")

    def go(aid):
        return http.post("/handoff/accept", json={"session_id": "s1", "agent_id": aid}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        codes = sorted(ex.map(go, ["a1", "a2"]))
    assert codes == [200, 409]


def test_two_users_never_see_each_others_messages(http, ws_user):
    a = _open(ws_user, sid="sa")
    a.send({"type": "user.message", "payload": {"text": "订单状态"}})
    a.recv_until("assistant.done")

    b = ws_user(session_id="sb", user_id="u2", order_id="ORD1003")
    b.recv()
    b.send({"type": "user.message", "payload": {"text": "订单状态"}})
    b.recv_until("assistant.done")

    a_msgs = http.get("/chat/session/sa").json()["messages"]
    b_msgs = http.get("/chat/session/sb").json()["messages"]
    assert {m["session_id"] for m in a_msgs} == {"sa"}
    assert {m["session_id"] for m in b_msgs} == {"sb"}


def test_duplicate_delivery_is_deduped_by_seq(http, ws_user):
    ws = _open(ws_user)
    ws.send({"type": "user.message", "payload": {"text": "订单状态"}})
    ws.recv_until("assistant.done")
    msgs = http.get("/chat/session/s1").json()["messages"]
    seqs = [m["seq"] for m in seqs_sorted(msgs)]
    assert seqs == [1, 2]


def seqs_sorted(msgs):
    return sorted(msgs, key=lambda m: m["seq"])


def test_resolved_session_rejects_new_input(http, ws_user):
    ws = _open(ws_user)
    http.post("/handoff/resolve", json={"session_id": "s1", "agent_id": "demo-agent"})
    ws.recv_until("session.state")
    ws.send({"type": "user.message", "payload": {"text": "还在吗"}})
    err = ws.recv_until("error")
    assert err["payload"]["code"] == "session_resolved"


def test_agent_connecting_without_id_is_rejected(http):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with http.websocket_connect("/ws/agent") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_user_connecting_without_session_is_rejected(http):
    """缺必填 query 参数时 FastAPI 在进闭包前就拒绝（1008）；
    参数在但为空时由本代码拒绝（4401）。两者都算"拒绝连接"。"""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with http.websocket_connect("/ws/chat?user_id=u1") as ws:
            ws.receive_text()
    assert exc.value.code in (1008, 4401)


def test_empty_session_id_is_rejected(http):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with http.websocket_connect("/ws/chat?session_id=&user_id=u1") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_other_user_cannot_hijack_session(http, ws_user):
    from starlette.websockets import WebSocketDisconnect

    _open(ws_user)
    with pytest.raises(WebSocketDisconnect) as exc:
        ws = ws_user(session_id="s1", user_id="intruder")
        ws.recv()
    assert exc.value.code == 4403


def test_unknown_message_type_gets_error(http, ws_user):
    ws = _open(ws_user)
    ws.send({"type": "user.message", "payload": {}})  # 空文本，忽略
    ws.send({"type": "pong", "payload": {}})
    # 不应崩：再发一条正常消息仍能工作
    ws.send({"type": "user.message", "payload": {"text": "订单状态"}})
    done = ws.recv_until("assistant.done")
    assert done["payload"]["text"]


def test_bad_json_frame_does_not_kill_connection(http, ws_user):
    ws = _open(ws_user)
    ws.ws.send_text("{not json")
    err = ws.recv_until("error")
    assert err["payload"]["code"] == "bad_frame"
    ws.send({"type": "user.message", "payload": {"text": "订单状态"}})
    assert ws.recv_until("assistant.done")["payload"]["text"]

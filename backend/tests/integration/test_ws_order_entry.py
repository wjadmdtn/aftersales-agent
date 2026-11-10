"""T063: 订单上下文随会话带入（FR-034/035）。

业务入口是"订单页点客服"，不是让用户背订单号。
握手带入的订单只在首次建会话时生效，之后以会话已绑定的为准。
"""
from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest_app import *  # noqa: F401,F403


def test_order_bound_on_first_handshake(http, ws_user):
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    body = http.get("/chat/session/s1").json()
    assert body["state"]["order_id"] == "ORD1002"


def test_bound_order_removes_need_to_mention_it(http, ws_user):
    """演示路径第①步：只说要退款，AI 直接知道是哪一单。"""
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.send({"type": "user.message", "payload": {"text": "我要退款"}})
    done = ws.recv_until("assistant.done")
    assert "ORD1002" in done["payload"]["text"]
    assert "259.90" in done["payload"]["text"]


def test_unknown_order_is_rejected_at_handshake(http, ws_user):
    with pytest.raises(WebSocketDisconnect) as exc:
        ws = ws_user(session_id="sx", user_id="u1", order_id="ORD9999")
        ws.recv()
    assert exc.value.code == 4404


def test_existing_session_ignores_new_query_order(http, ws_user):
    """会话已存在时，握手参数不能悄悄改写上下文。"""
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()

    ws2 = ws_user(session_id="s1", user_id="u1", order_id="ORD1003")
    ws2.recv()
    body = http.get("/chat/session/s1").json()
    assert body["state"]["order_id"] == "ORD1002"


def test_order_survives_reconnect(http, ws_user):
    """FR-035：刷新/重连后绑定订单仍生效。"""
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.ws.close()

    ws2 = ws_user(session_id="s1", user_id="u1")  # 不带 order_id 重连
    ws2.recv()
    ws2.send({"type": "user.message", "payload": {"text": "我要退款"}})
    done = ws2.recv_until("assistant.done")
    assert "ORD1002" in done["payload"]["text"]


def test_switching_order_inside_conversation_updates_state(http, ws_user):
    """FR-036：消息里出现新订单号即切换，切换后对外状态同步。"""
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.send({"type": "user.message", "payload": {"text": "订单 ORD1003 的物流到哪了"}})
    ws.recv_until("assistant.done")
    body = http.get("/chat/session/s1").json()
    assert body["state"]["order_id"] == "ORD1003"

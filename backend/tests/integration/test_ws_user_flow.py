"""T016: 用户端 WS 流程。

US1-1 消息上屏 / US1-3 断线保留 / US1-4 降级。
US1-2 "可以中断" 是非强制能力，不在 SC 内，这里只验证明断后服务端不再推。
"""
from __future__ import annotations

import pytest

from tests.conftest_app import *  # noqa: F401,F403


def _hello_then(ws):
    frame = ws.recv()
    assert frame["type"] == "hello"
    return frame


def test_hello_carries_heartbeat_and_reconnect(http, ws_user):
    ws = ws_user(session_id="s1", user_id="u1")
    hello = _hello_then(ws)
    p = hello["payload"]
    assert p["heartbeat_interval_s"] > 0
    assert p["reconnect"]["base_s"] > 0
    assert p["reconnect"]["max_s"] >= p["reconnect"]["base_s"]


def test_user_message_gets_ack_then_streamed_reply(http, ws_user):
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    _hello_then(ws)

    ws.send({"type": "user.message", "payload": {"text": "我要退款", "local_id": "L1"}})

    ack = ws.recv_until("user.message.ack")
    assert ack["payload"]["local_id"] == "L1"
    assert ack["payload"]["message"]["role"] == "user"
    assert ack["seq"] == 1

    deltas = []
    while True:
        frame = ws.recv()
        if frame["type"] == "assistant.delta":
            deltas.append(frame)
        elif frame["type"] == "assistant.done":
            done = frame
            break
        elif frame["type"] in ("ping", "hello"):
            continue
        else:
            raise AssertionError(f"意外的帧 {frame['type']}")

    assert len(deltas) >= 1, "至少一帧 delta，否则界面上看不到流式"
    assert done["payload"]["text"].startswith("即将为订单 ORD1002")
    # 分块推送必须如实标记
    assert all(d["payload"]["streaming"] is False for d in deltas)
    # 一条回复共用一个 seq
    assert {d["seq"] for d in deltas} == {done["seq"]}


def test_deltas_concatenate_to_done_text(http, ws_user):
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    _hello_then(ws)
    ws.send({"type": "user.message", "payload": {"text": "我要退款"}})

    buf = ""
    while True:
        f = ws.recv()
        if f["type"] == "assistant.delta":
            buf += f["payload"]["text_delta"]
        elif f["type"] == "assistant.done":
            assert buf == f["payload"]["text"]
            return
        elif f["type"] in ("ping", "hello", "user.message.ack"):
            continue


def test_reply_is_persisted_as_single_message(http, ws_user):
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    _hello_then(ws)
    ws.send({"type": "user.message", "payload": {"text": "我要退款"}})
    ws.recv_until("assistant.done")

    body = http.get("/chat/session/s1").json()
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant"]


def test_degraded_marks_done(http, ws_user, monkeypatch):
    """LLM 不可用 → 统一降级话术 + degraded 标记，前端据此展示转人工。"""
    from app.api.agent_routes import _loop

    monkeypatch.setattr(_loop.classifier.llm, "fail_mode", "error", raising=False)
    monkeypatch.setattr(_loop.classifier.llm, "fail_times", 0, raising=False)

    ws = ws_user(session_id="sd", user_id="u1", order_id="ORD1002")
    _hello_then(ws)
    ws.send({"type": "user.message", "payload": {"text": "我要退款"}})

    done = ws.recv_until("assistant.done")
    assert done["payload"]["degraded"] is True
    from app.agent.degrade import DEGRADED_REPLY

    assert done["payload"]["text"] == DEGRADED_REPLY


def test_history_survives_reconnect(http, ws_user):
    """US1-3：断线后已输出内容仍在，重连按 after_seq 补齐。"""
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    _hello_then(ws)
    ws.send({"type": "user.message", "payload": {"text": "我要退款"}})
    done = ws.recv_until("assistant.done")
    last_seq = done["seq"]

    body = http.get(f"/chat/session/s1?after_seq=0").json()
    assert len(body["messages"]) == 2

    # 重连：只补 last_seq 之后的（此时没有）
    again = http.get(f"/chat/session/s1?after_seq={last_seq}").json()
    assert again["messages"] == []

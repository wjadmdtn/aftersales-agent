"""T055: 断线补齐（FR-018/025/027）。

不轮询、不丢消息：断线期间产生的消息在重连后按 after_seq 补齐，
重复投递靠 seq 去重。
"""
from __future__ import annotations

from tests.conftest_app import *  # noqa: F401,F403


def test_messages_accumulate_while_disconnected(http, ws_user, ws_agent):
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")
    http.post("/handoff/accept", json={"session_id": "s1", "agent_id": "demo-agent"})

    # 用户断开
    ws.ws.close()

    agent = ws_agent("demo-agent")
    agent.recv_until("queue.update")
    agent.send({"type": "agent.message", "payload": {"session_id": "s1", "text": "您还在吗"}})

    body = http.get("/chat/session/s1").json()
    assert any(m["text"] == "您还在吗" for m in body["messages"])


def test_backfill_returns_only_newer_messages(http, ws_user):
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.send({"type": "user.message", "payload": {"text": "我要退款"}})
    done = ws.recv_until("assistant.done")
    last = done["seq"]

    ws.send({"type": "user.message", "payload": {"text": "确认"}})
    ws.recv_until("assistant.done")

    snap = http.get(f"/chat/session/s1?after_seq={last}").json()
    assert len(snap["messages"]) == 2
    assert all(m["seq"] > last for m in snap["messages"])


def test_seq_is_continuous_and_unique(http, ws_user):
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    for text in ("我要退款", "确认"):
        ws.send({"type": "user.message", "payload": {"text": text}})
        ws.recv_until("assistant.done")

    msgs = http.get("/chat/session/s1").json()["messages"]
    seqs = [m["seq"] for m in msgs]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
    # 连续无缺口
    assert seqs == list(range(1, len(seqs) + 1))


def test_truncation_flag_over_limit(http, ws_user):
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    for i in range(4):
        ws.send({"type": "user.message", "payload": {"text": f"订单状态 {i}"}})
        ws.recv_until("assistant.done")

    snap = http.get("/chat/session/s1?limit=3").json()
    assert snap["truncated"] is True
    assert len(snap["messages"]) == 3


def test_reconnect_from_rest_is_idempotent(http, ws_user):
    """补齐多次调用结果一致，不会产生重复。"""
    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.send({"type": "user.message", "payload": {"text": "订单状态"}})
    ws.recv_until("assistant.done")

    a = http.get("/chat/session/s1?after_seq=0").json()
    b = http.get("/chat/session/s1?after_seq=0").json()
    assert [m["id"] for m in a["messages"]] == [m["id"] for m in b["messages"]]

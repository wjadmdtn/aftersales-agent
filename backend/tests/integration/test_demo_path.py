"""T056: 演示路径全链路（SC-001，一票否决）。

⓪ 订单页 → ① 点客服+我要退款 → ② 确认话术 → ③ 确认+已提交
④ 我要投诉 → ⑤ 工作台出现高风险 → ⑥ 看到上下文包
⑦ 接入 → ⑧ 人工发消息 → ⑨ 结束

同时断言 SC-002 首字延迟 < 2s 与 SC-003 状态同步 < 1s。
"""
from __future__ import annotations

import time

import pytest

from tests.conftest_app import *  # noqa: F401,F403


def test_demo_path_end_to_end(http, ws_user, ws_agent):
    t_start = time.monotonic()

    # ---- ⓪ 订单页数据来自真实接口 ----
    order = http.get("/orders/ORD1002").json()
    assert order["code"] == 1000
    assert order["data"]["amount"] == "259.90"
    assert order["data"]["status"] in ("PAID", "SHIPPED", "DELIVERED")

    # ---- ① 点客服进入，订单随握手带入 ----
    user = ws_user(session_id="demo", user_id="u1", order_id="ORD1002")
    assert user.recv()["type"] == "hello"

    agent = ws_agent("demo-agent")
    agent.recv_until("queue.update")

    t_send = time.monotonic()
    user.send({"type": "user.message", "payload": {"text": "我要退款"}})

    # ---- ② AI 复述订单与金额，不追问订单号 ----
    first_delta_at = None
    buf = ""
    while True:
        f = user.recv()
        if f["type"] == "assistant.delta":
            if first_delta_at is None:
                first_delta_at = time.monotonic()
            buf += f["payload"]["text_delta"]
        elif f["type"] == "assistant.done":
            break
        elif f["type"] in ("ping", "hello", "user.message.ack", "session.state"):
            continue

    # SC-002：首字 < 2s
    assert first_delta_at is not None
    assert first_delta_at - t_send < 2.0, f"首字延迟 {first_delta_at - t_send:.2f}s"

    assert "ORD1002" in buf
    assert "259.90" in buf
    assert "确认" in buf
    assert "哪一笔订单" not in buf, "不应追问订单号——订单已由入口带入"

    # ---- ③ 确认 → 已提交 ----
    user.send({"type": "user.message", "payload": {"text": "确认"}})
    done = user.recv_until("assistant.done")
    assert "已为您提交退款" in done["payload"]["text"]

    # ---- ④ 投诉 → 转人工 ----
    user.send({"type": "user.message", "payload": {"text": "我要投诉"}})
    t_queue = time.monotonic()

    # 转人工同时产生 assistant.done 与 session.state，顺序不保证，
    # 因此一次读到两帧都齐了为止
    got_done = got_state = None
    for _ in range(30):
        f = user.recv()
        if f["type"] == "assistant.done" and got_done is None:
            got_done = f
        elif f["type"] == "session.state" and f["payload"].get("ui_state") == "queued":
            got_state = f
        if got_done and got_state:
            break
        if f["type"] in ("ping", "hello", "user.message.ack"):
            continue

    assert got_done is not None, "没有收到转人工回复"
    assert "人工客服" in got_done["payload"]["text"]
    assert got_state is not None, "没有收到排队状态广播"
    # SC-003：状态同步 < 1s
    assert time.monotonic() - t_queue < 1.0

    upd = agent.recv_until("queue.update")
    assert "demo" in upd["payload"]["added"]

    queue = http.get("/handoff/queue").json()["items"]
    assert queue[0]["session_id"] == "demo"
    assert queue[0]["priority"] == "high"
    assert queue[0]["trigger_type"] == "risk"
    assert queue[0]["suggested_next_step"] is None, "风险场景不得给 AI 建议"

    # ---- ⑥ 上下文包字段齐全（SC-004）----
    ctx = queue[0]
    for key in ("summary", "attempted_actions", "priority_reason", "trigger_type"):
        assert key in ctx
    assert ctx["priority_reason"]

    # ---- ⑦ 接入 → 用户端看到人工客服 ----
    r = http.post("/handoff/accept", json={"session_id": "demo", "agent_id": "demo-agent"})
    assert r.status_code == 200
    st = user.recv_until("session.state")
    assert st["payload"]["ui_state"] == "human_serving"
    assert st["payload"]["agent_name"] == "小王"

    # ---- ⑧ 人工发消息 → 用户端收到 ----
    agent.send({"type": "agent.message", "payload": {"session_id": "demo", "text": "您好，我来处理"}})
    hm = user.recv_until("human.message")
    assert hm["payload"]["text"] == "您好，我来处理"

    # 用户回复 → 人工端收到，且 AI 不插话
    user.send({"type": "user.message", "payload": {"text": "好的，谢谢"}})
    # 人工端会先收到自己刚发消息的回显，跳过它
    back = None
    for _ in range(10):
        f = agent.recv()
        if f["type"] == "human.message" and f["payload"]["role"] == "user":
            back = f
            break
    assert back is not None and back["payload"]["text"] == "好的，谢谢"

    # ---- ⑨ 结束 → 双方已解决 ----
    http.post("/handoff/resolve", json={"session_id": "demo", "agent_id": "demo-agent"})
    final = user.recv_until("session.state")
    assert final["payload"]["ui_state"] == "resolved"

    body = http.get("/chat/session/demo").json()
    assert body["state"]["ui_state"] == "resolved"
    # 全程无重复消息、seq 连续
    seqs = [m["seq"] for m in body["messages"]]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))

    total = time.monotonic() - t_start
    assert total < 30, f"整条路径耗时 {total:.1f}s"


def test_demo_path_never_requires_refresh(http, ws_user):
    """SC-001：单连接走完全程，不重连。"""
    user = ws_user(session_id="d2", user_id="u1", order_id="ORD1002")
    user.recv()
    for msg in ("我要退款", "确认", "我要投诉"):
        user.send({"type": "user.message", "payload": {"text": msg}})
        user.recv_until("assistant.done")
    # hello 只出现一次
    body = http.get("/chat/session/d2").json()
    assert body["messages"]

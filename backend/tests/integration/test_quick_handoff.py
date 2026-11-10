"""T032: 快捷转人工（US3, FR-005）。

关键点：不经过意图识别。用 spy 断言 classify 一次都没被调用——
"绕开分类器"不能只靠代码看起来没调，要有断言。
"""
from __future__ import annotations

import pytest

from tests.conftest_app import *  # noqa: F401,F403


@pytest.fixture
def classify_spy(monkeypatch):
    from app.api.agent_routes import _loop

    calls = {"n": 0}
    original = _loop.classifier.llm.classify

    async def _spy(*a, **kw):
        calls["n"] += 1
        return await original(*a, **kw)

    monkeypatch.setattr(_loop.classifier.llm, "classify", _spy)
    return calls


def _open(ws_user, sid="s1"):
    ws = ws_user(session_id=sid, user_id="u1", order_id="ORD1002")
    ws.recv()
    return ws


def test_handoff_does_not_call_classifier(http, ws_user, classify_spy):
    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")
    assert classify_spy["n"] == 0


def test_handoff_enters_queue_immediately(http, ws_user):
    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    state = ws.recv_until("session.state")
    assert state["payload"]["ui_state"] == "queued"

    queue = http.get("/handoff/queue").json()
    assert [i["session_id"] for i in queue["items"]] == ["s1"]


def test_repeated_handoff_does_not_enqueue_twice(http, ws_user):
    """US3-2：已转人工后再点，只重放状态，不产生第二条队列项。"""
    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")
    ws.send({"type": "user.handoff", "payload": {}})
    replay = ws.recv_until("session.state")
    assert replay["payload"]["ui_state"] == "queued"

    assert http.get("/handoff/queue").json()["depth"] == 1


def test_message_while_queued_gets_waiting_script(http, ws_user):
    """US3-3：排队中发消息，提示正在接入，不调 Agent。"""
    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")

    ws.send({"type": "user.message", "payload": {"text": "我要退款"}})
    done = ws.recv_until("assistant.done")
    assert "正在为您接入" in done["payload"]["text"]
    # 不应出现"即将为订单…退款"这类业务应答
    assert "259.90" not in done["payload"]["text"]


def test_handoff_pushes_queue_update_to_agents(http, ws_user, ws_agent):
    agent = ws_agent("demo-agent")
    agent.recv_until("queue.update")

    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    upd = agent.recv_until("queue.update")
    assert "s1" in upd["payload"]["added"]
    assert upd["payload"]["depth"] == 1


def test_handoff_on_resolved_session_is_rejected(http, ws_user):
    ws = _open(ws_user)
    http.post("/handoff/resolve", json={"session_id": "s1", "agent_id": "demo-agent"})
    ws.recv_until("session.state")
    ws.send({"type": "user.handoff", "payload": {}})
    err = ws.recv_until("error")
    assert err["payload"]["code"] == "session_resolved"


def test_handoff_records_visible_message(http, ws_user):
    """对话流里要留下"已为您接入"的痕迹，否则用户端看不到任何反馈。"""
    ws = _open(ws_user)
    ws.send({"type": "user.handoff", "payload": {}})
    done = ws.recv_until("assistant.done")
    assert "人工客服" in done["payload"]["text"]

"""T037: 人工端 WS 流程（US4，一票否决：并发接入唯一性）。

两人同时接入同一会话，必须只有一个成功——现有队列 accept 是三步非原子，
不做抢锁会两人都"成功"，那是线上事故级缺陷。
"""
from __future__ import annotations

import concurrent.futures

import pytest

from tests.conftest_app import *  # noqa: F401,F403


def _queued(http, ws_user, sid="s1"):
    ws = ws_user(session_id=sid, user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")
    return ws


def test_agent_receives_queue_snapshot_on_connect(http, ws_user, ws_agent):
    _queued(http, ws_user)
    agent = ws_agent("demo-agent")
    upd = agent.recv_until("queue.update")
    assert "s1" in upd["payload"]["added"]


def test_accept_moves_session_out_of_queue(http, ws_user):
    _queued(http, ws_user)
    r = http.post("/handoff/accept", json={"session_id": "s1", "agent_id": "demo-agent"})
    assert r.status_code == 200
    assert r.json()["state"] == "human_serving"
    assert http.get("/handoff/queue").json()["depth"] == 0


def test_accept_broadcasts_state_and_queue_update(http, ws_user, ws_agent):
    _queued(http, ws_user)
    agent = ws_agent("demo-agent")
    agent.recv_until("queue.update")

    http.post("/handoff/accept", json={"session_id": "s1", "agent_id": "demo-agent"})

    upd = agent.recv_until("queue.update")
    assert "s1" in upd["payload"]["removed"]


def test_concurrent_accept_only_one_wins(http, ws_user):
    """一票否决：两人同时接入，只能一个 200，一个 409。"""
    _queued(http, ws_user)

    def attempt(agent_id: str):
        return http.post(
            "/handoff/accept", json={"session_id": "s1", "agent_id": agent_id}
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(attempt, "agent-a"), ex.submit(attempt, "agent-b")]
        codes = sorted(f.result().status_code for f in futs)

    assert codes == [200, 409], f"应当一成一败，实际 {codes}"


def test_losing_agent_gets_owner_info(http, ws_user):
    _queued(http, ws_user)
    http.post("/handoff/accept", json={"session_id": "s1", "agent_id": "agent-a"})
    r = http.post("/handoff/accept", json={"session_id": "s1", "agent_id": "agent-b"})
    assert r.status_code == 409
    assert r.json()["detail"]["owner_agent_id"] == "agent-a"


def test_state_recorded_with_agent_identity(http, ws_user):
    _queued(http, ws_user)
    http.post("/handoff/accept", json={"session_id": "s1", "agent_id": "agent-a"})
    body = http.get("/chat/session/s1").json()
    assert body["state"]["agent_id"] == "agent-a"
    assert body["state"]["ui_state"] == "human_serving"


def test_accepted_session_appears_in_my_sessions(http, ws_user):
    """FR-030：已接入会话能被自己查到。"""
    _queued(http, ws_user)
    http.post("/handoff/accept", json={"session_id": "s1", "agent_id": "agent-a"})
    mine = http.get("/chat/sessions?agent_id=agent-a").json()["items"]
    assert [i["session_id"] for i in mine] == ["s1"]
    others = http.get("/chat/sessions?agent_id=agent-b").json()["items"]
    assert others == []


def test_decline_removes_from_queue_and_keeps_human_state(http, ws_user):
    _queued(http, ws_user)
    http.post("/handoff/decline", json={"session_id": "s1", "agent_id": "demo-agent"})
    assert http.get("/handoff/queue").json()["depth"] == 0
    body = http.get("/chat/session/s1").json()
    # 拒绝不回流 AI（状态机刻意没有 HUMAN_SERVING -> NORMAL）
    assert body["state"]["state"] == "human_serving"

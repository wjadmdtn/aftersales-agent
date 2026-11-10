"""T050: 人工服务期间 AI 绝对静默（US6-2 / 宪法 III）。

不用"等一会儿看有没有 AI 帧"——同步 WebSocket 客户端没超时，等不到就挂死。
改为断言可确定的事实：消息记录里不得新增 assistant 条目，且 LLM 未被调用。
"""
from __future__ import annotations

from tests.conftest_app import *  # noqa: F401,F403


def _in_service(http, ws_user, monkeypatch):
    from app.api.agent_routes import _loop

    calls = {"n": 0}
    original = _loop.classifier.llm.classify

    async def _spy(*a, **kw):
        calls["n"] += 1
        return await original(*a, **kw)

    monkeypatch.setattr(_loop.classifier.llm, "classify", _spy)

    ws = ws_user(session_id="s1", user_id="u1", order_id="ORD1002")
    ws.recv()
    ws.send({"type": "user.handoff", "payload": {}})
    ws.recv_until("session.state")
    http.post("/handoff/accept", json={"session_id": "s1", "agent_id": "demo-agent"})
    ws.recv_until("session.state")
    return ws, calls


def _assistant_count(http, sid="s1") -> int:
    body = http.get(f"/chat/session/{sid}").json()
    return sum(1 for m in body["messages"] if m["role"] == "assistant")


def test_no_assistant_message_while_human_serving(http, ws_user, monkeypatch):
    ws, calls = _in_service(http, ws_user, monkeypatch)
    before_assistant = _assistant_count(http)
    before_calls = calls["n"]

    ws.send({"type": "user.message", "payload": {"text": "我要退款 ORD1002"}})
    ws.recv_until("user.message.ack")

    assert _assistant_count(http) == before_assistant, "人工服务期间新增了 AI 消息"
    assert calls["n"] == before_calls, "人工服务期间调用了 LLM"


def test_silence_holds_for_multiple_turns(http, ws_user, monkeypatch):
    ws, calls = _in_service(http, ws_user, monkeypatch)
    before = _assistant_count(http)

    for i in range(3):
        ws.send({"type": "user.message", "payload": {"text": f"第{i}句"}})
        ws.recv_until("user.message.ack")

    assert _assistant_count(http) == before


def test_state_machine_blocks_ai_reentry():
    """HUMAN_SERVING -> NORMAL 这条边刻意不存在。"""
    from app.handoff.state import IllegalTransition, assert_transition
    from app.models.handoff import HandoffState

    try:
        assert_transition(HandoffState.HUMAN_SERVING, HandoffState.NORMAL, actor="agent")
    except IllegalTransition:
        return
    raise AssertionError("HUMAN_SERVING 不应能回到 NORMAL")


def test_ai_can_speak_again_only_after_resolved_not_before():
    from app.handoff.state import assert_ai_may_speak
    from app.models.handoff import HandoffState

    for allowed in (HandoffState.NORMAL, HandoffState.HANDOFF_REQUESTED, HandoffState.QUEUED):
        assert_ai_may_speak(allowed)
    for blocked in (HandoffState.HUMAN_SERVING, HandoffState.RESOLVED):
        try:
            assert_ai_may_speak(blocked)
        except Exception:
            continue
        raise AssertionError(f"{blocked} 状态下 AI 不应能说话")

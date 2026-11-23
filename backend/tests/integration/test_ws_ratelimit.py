"""入站限流的端到端行为：超长与超频都不该进入 AgentLoop。

这两条是防刷单的底线——被拒的消息一分钱都不该花，所以判定必须在
落库和调模型之前。
"""
from __future__ import annotations

from app.config import settings
from tests.conftest_app import *  # noqa: F401,F403


def _open(ws_user, *, session, user):
    ws = ws_user(session_id=session, user_id=user)
    ws.recv_until("hello")
    return ws


def test_too_long_message_rejected(http, ws_user, monkeypatch):
    monkeypatch.setattr(settings, "ws_message_max_chars", 10, raising=False)
    ws = _open(ws_user, session="rl-long", user="u-long")

    ws.send({"type": "user.message", "payload": {"text": "字" * 50}})

    err = ws.recv_until("error")
    assert err["payload"]["code"] == "message_too_long"
    assert err["payload"]["retryable"] is True


def test_burst_exceeding_limit_is_rate_limited(http, ws_user, monkeypatch):
    monkeypatch.setattr(settings, "ws_inbound_rate_limit", 2, raising=False)
    monkeypatch.setattr(settings, "ws_sustained_rate_limit", 100, raising=False)
    ws = _open(ws_user, session="rl-burst", user="u-burst")

    for _ in range(2):
        ws.send({"type": "user.message", "payload": {"text": "你好"}})
        ws.recv_until("assistant.done")

    ws.send({"type": "user.message", "payload": {"text": "你好"}})
    err = ws.recv_until("error")
    assert err["payload"]["code"] == "rate_limited"
    # 必须给出等待秒数，前端要展示"请 N 秒后再试"
    assert "秒" in err["payload"]["message"]


def test_rejected_message_not_persisted(http, ws_user, monkeypatch):
    """被限流的消息不得落库：落库了就会出现在会话历史里，用户以为发出去了。"""
    monkeypatch.setattr(settings, "ws_inbound_rate_limit", 1, raising=False)
    monkeypatch.setattr(settings, "ws_sustained_rate_limit", 100, raising=False)
    ws = _open(ws_user, session="rl-nopersist", user="u-nopersist")

    ws.send({"type": "user.message", "payload": {"text": "第一条"}})
    ws.recv_until("assistant.done")

    ws.send({"type": "user.message", "payload": {"text": "被限的那条"}})
    assert ws.recv_until("error")["payload"]["code"] == "rate_limited"

    snap = http.get("/chat/session/rl-nopersist").json()
    texts = [m["text"] for m in snap["messages"]]
    assert "被限的那条" not in texts

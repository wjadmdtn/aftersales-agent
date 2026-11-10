"""T005: WS 信封与消息类型。

信封是两端唯一的共同语言，字段错了前端全部解析失败。
所以这里逐字段断言，不靠"能跑就行"。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.chat.protocol import (
    CLIENT_TO_SERVER,
    SERVER_TO_CLIENT,
    Envelope,
    MessageType,
    envelope,
)


def test_all_message_types_declared():
    """spec 消息类型表 + 流程消息，一个都不能少。"""
    expected = {
        # spec 表
        "user.message",
        "user.handoff",
        "assistant.delta",
        "assistant.done",
        "human.message",
        "session.state",
        "queue.update",
        "agent.message",
        # 流程消息
        "hello",
        "ping",
        "pong",
        "error",
        "user.message.ack",
    }
    assert {t.value for t in MessageType} == expected


def test_every_type_has_a_direction():
    for t in MessageType:
        assert t in CLIENT_TO_SERVER or t in SERVER_TO_CLIENT, f"{t} 没有方向定义"


def test_directions_are_sane():
    assert MessageType.USER_MESSAGE in CLIENT_TO_SERVER
    assert MessageType.USER_HANDOFF in CLIENT_TO_SERVER
    assert MessageType.AGENT_MESSAGE in CLIENT_TO_SERVER
    assert MessageType.ASSISTANT_DELTA in SERVER_TO_CLIENT
    assert MessageType.ASSISTANT_DONE in SERVER_TO_CLIENT
    assert MessageType.SESSION_STATE in SERVER_TO_CLIENT
    # human.message 双向
    assert MessageType.HUMAN_MESSAGE in CLIENT_TO_SERVER
    assert MessageType.HUMAN_MESSAGE in SERVER_TO_CLIENT


def test_envelope_carries_required_fields():
    e = envelope(MessageType.SESSION_STATE, session_id="s1", seq=3, payload={"state": "queued"})
    data = e.model_dump()
    assert set(data) == {"type", "session_id", "seq", "ts", "payload"}
    assert data["type"] == "session.state"
    assert data["session_id"] == "s1"
    assert data["seq"] == 3
    assert data["payload"] == {"state": "queued"}
    assert data["ts"] is not None


def test_global_frames_use_seq_zero():
    e = envelope(MessageType.PING, session_id=None, payload={})
    assert e.seq == 0
    assert e.session_id is None


def test_queue_update_is_global():
    e = envelope(MessageType.QUEUE_UPDATE, session_id=None, payload={"added": [], "removed": []})
    assert e.seq == 0


def test_envelope_round_trips_json():
    e = envelope(MessageType.ASSISTANT_DELTA, session_id="s1", seq=7,
                 payload={"msg_id": "m1", "text_delta": "你好", "streaming": False})
    again = Envelope.model_validate_json(e.model_dump_json())
    assert again == e
    assert again.payload["text_delta"] == "你好"


def test_client_frame_parsing_rejects_unknown_type():
    with pytest.raises(ValidationError):
        Envelope.model_validate({"type": "not.a.type", "session_id": "s1", "payload": {}})


def test_client_frame_may_omit_seq_and_ts():
    """客户端入站帧可以不带 seq/ts，服务端补齐。"""
    e = Envelope.model_validate({"type": "user.message", "session_id": "s1",
                                 "payload": {"text": "hi"}})
    assert e.seq == 0
    assert e.ts is not None

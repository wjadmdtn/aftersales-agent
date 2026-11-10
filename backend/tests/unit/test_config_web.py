"""T003: 006 新增配置项。

宪法 IV：每个配置项都要有测试证明"改了就生效"，非法值在启动阶段失败。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _make(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_ws_defaults_are_usable():
    s = _make()
    assert s.ws_heartbeat_interval_s == 20
    assert s.ws_idle_timeout_s == 60
    assert s.ws_reconnect_base_s == 1.0
    assert s.ws_reconnect_max_s == 30.0
    assert s.chat_backfill_max_messages == 200
    assert s.demo_agent_id == "demo-agent"
    assert s.demo_agent_name == "小王"


@pytest.mark.parametrize(
    "field,value",
    [
        ("ws_heartbeat_interval_s", 0),
        ("ws_idle_timeout_s", 0),
        ("ws_reconnect_base_s", 0),
        ("chat_message_ttl_seconds", 0),
        ("chat_backfill_max_messages", 0),
    ],
)
def test_non_positive_values_rejected(field, value):
    with pytest.raises(ValidationError):
        _make(**{field: value})


def test_reconnect_max_must_be_at_least_base():
    with pytest.raises(ValidationError):
        _make(ws_reconnect_base_s=10.0, ws_reconnect_max_s=5.0)


def test_overrides_take_effect():
    s = _make(ws_heartbeat_interval_s=5, demo_agent_name="小李")
    assert s.ws_heartbeat_interval_s == 5
    assert s.demo_agent_name == "小李"


def test_demo_stub_responses_parse_to_intent_results():
    s = _make()
    table = s.demo_stub_responses
    assert "我要退款" in table
    assert table["我要退款"]["intent"] == "refund"
    # 每条都必须能构成合法 IntentResult，否则装配时才炸
    from app.models.agent import Intent, IntentMode, IntentResult

    for phrase, spec in table.items():
        IntentResult(
            intent=Intent(spec["intent"]),
            mode=IntentMode(spec["mode"]),
            confidence=1.0,
        ), phrase


def test_invalid_stub_json_rejected_at_startup():
    with pytest.raises(ValidationError):
        _make(demo_stub_responses_raw="not json")

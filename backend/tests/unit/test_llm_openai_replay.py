"""OpenAICompatLLMClient 的带错误重放（specs/007-llm-schema-replay）。

重放逻辑在客户端内部，桩模型没有这个能力，必须针对真实客户端验证。
这里替换 `_complete`，不联网。
"""
from __future__ import annotations

import pytest

from app.agent.llm.base import LLMError
from app.agent.llm.openai_compat import OpenAICompatLLMClient
from app.agent.resilience import stats
from app.models.agent import Intent, IntentMode


def _client(scripted: list[str]) -> tuple[OpenAICompatLLMClient, list[list[dict]]]:
    """按顺序返回 scripted 里的内容，并记录每次收到的 messages。"""
    sent: list[list[dict]] = []

    async def fake_complete(messages):
        sent.append([dict(m) for m in messages])
        return scripted[len(sent) - 1]

    c = OpenAICompatLLMClient(base_url="http://x", api_key="k", model="m")
    c._complete = fake_complete  # type: ignore[assignment]
    return c, sent


async def test_replay_sends_error_back_to_model():
    """首次非法 → 第二次请求里必须带上"原文 + 哪错了 + 要求重写"。"""
    c, sent = _client([
        '{"intent":"退款","mode":"execute"}',
        '{"intent":"refund","mode":"execute","confidence":0.9}',
    ])

    result = await c.classify(user_input="我要退款", hint_keywords=[], context={})

    assert result.intent is Intent.REFUND
    assert len(sent) == 2

    # 第二次请求的 messages 结构：原 system/user + 上一轮 assistant 原文 + 纠错 user
    replay = sent[1]
    roles = [m["role"] for m in replay]
    assert roles == ["system", "user", "assistant", "user"]
    assert replay[2]["content"].startswith('{"intent":"退款"')
    correction = replay[3]["content"]
    assert "不合法" in correction
    # 错误信息可执行：必须报出合法取值，否则模型只是再猜一次
    assert "refund" in correction and "logistics" in correction


async def test_exhausted_raises_schema_error():
    """重放次数用尽 → LLMError 带 schema=True，上层据此标记会话。"""
    c, sent = _client([
        '{"intent":"退款","mode":"execute"}',
        '{"intent":"退款","mode":"execute"}',
    ])

    with pytest.raises(LLMError) as exc:
        await c.classify(user_input="我要退款", hint_keywords=[], context={})

    assert exc.value.schema is True
    # 首次 + 1 次重放（默认 schema_replay_attempts=1）
    assert len(sent) == 2


async def test_valid_first_time_makes_single_call():
    c, sent = _client(['{"intent":"refund","mode":"execute","confidence":0.8}'])
    result = await c.classify(user_input="我要退款", hint_keywords=[], context={})
    assert result.intent is Intent.REFUND
    assert len(sent) == 1


async def test_mode_null_is_accepted_without_replay():
    """mode:null 是模型的合理表达，不该触发重放。"""
    c, sent = _client(['{"intent":"unknown","mode":null,"confidence":0.5}'])
    result = await c.classify(user_input="你好", hint_keywords=[], context={})
    assert result.mode is IntentMode.EXECUTE
    assert len(sent) == 1


async def test_missing_mode_triggers_replay():
    """缺 mode 现在算结构失败——不再静默按 EXECUTE 兜底。"""
    c, sent = _client([
        '{"intent":"refund","confidence":0.9}',
        '{"intent":"refund","mode":"execute","confidence":0.9}',
    ])
    result = await c.classify(user_input="我要退款", hint_keywords=[], context={})
    assert result.mode is IntentMode.EXECUTE
    assert len(sent) == 2
    assert "mode" in sent[1][3]["content"]


async def test_stats_track_replays_and_failures():
    before = stats.schema_replays
    fail_before = stats.schema_failures

    c, _ = _client([
        '{"intent":"退款","mode":"execute"}',
        '{"intent":"refund","mode":"execute","confidence":0.9}',
    ])
    await c.classify(user_input="我要退款", hint_keywords=[], context={})
    assert stats.schema_replays == before + 1

    c2, _ = _client(['{"intent":"退款","mode":"execute"}', '{"intent":"退款","mode":"execute"}'])
    with pytest.raises(LLMError):
        await c2.classify(user_input="我要退款", hint_keywords=[], context={})
    assert stats.schema_failures == fail_before + 1

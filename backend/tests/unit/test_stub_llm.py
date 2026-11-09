import asyncio

import pytest

from app.agent.llm.stub import StubLLMClient
from app.models.agent import Intent, IntentResult


async def test_returns_mapped_response():
    stub = StubLLMClient({"我要退款": IntentResult(intent=Intent.REFUND, confidence=0.95)})
    r = await stub.classify(user_input="我要退款", hint_keywords=[], context={})
    assert r.intent is Intent.REFUND


async def test_default_when_unmapped():
    stub = StubLLMClient(default=IntentResult(intent=Intent.GENERAL, confidence=0.3))
    r = await stub.classify(user_input="随便说点什么", hint_keywords=[], context={})
    assert r.intent is Intent.GENERAL


async def test_fail_mode_timeout():
    from app.agent.llm.base import LLMError

    stub = StubLLMClient(fail_mode="timeout")
    with pytest.raises(LLMError):
        await stub.classify(user_input="x", hint_keywords=[], context={})


async def test_fail_times_only_first_n():
    """SC-004a：前两次失败，第三次应当正常（证明没有被跳过）。"""
    from app.agent.llm.base import LLMError

    stub = StubLLMClient(
        {"x": IntentResult(intent=Intent.REFUND, confidence=0.9)},
        fail_mode="timeout",
        fail_times=2,
    )
    for _ in range(2):
        with pytest.raises(LLMError):
            await stub.classify(user_input="x", hint_keywords=[], context={})
    r = await stub.classify(user_input="x", hint_keywords=[], context={})
    assert r.intent is Intent.REFUND
    assert stub.call_count == 3


async def test_records_hints():
    stub = StubLLMClient()
    await stub.classify(user_input="x", hint_keywords=["退款", "投诉"], context={})
    assert stub.last_hints == ["退款", "投诉"]


async def test_classifier_hook():
    stub = StubLLMClient(classifier=lambda s: IntentResult(intent=Intent.LOGISTICS, confidence=0.8))
    r = await stub.classify(user_input="任意", hint_keywords=[], context={})
    assert r.intent is Intent.LOGISTICS

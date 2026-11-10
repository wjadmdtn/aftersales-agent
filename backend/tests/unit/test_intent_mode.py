"""mode 兜底校验：LLM 判错时往保守方向纠偏。"""

import pytest

from app.agent.intent_mode import normalize_mode, requires_order_context
from app.models.agent import Intent, IntentMode


def test_execute_with_question_marker_downgraded():
    """「我要退款吗」这类自相矛盾的输入，宁可多问一句。"""
    mode, note = normalize_mode(Intent.REFUND, IntentMode.EXECUTE, "这个能退款吗")
    assert mode is IntentMode.INQUIRY
    assert note


def test_execute_clean_stays_execute():
    mode, note = normalize_mode(Intent.REFUND, IntentMode.EXECUTE, "我要退款")
    assert mode is IntentMode.EXECUTE
    assert note is None


def test_inquiry_with_imperative_upgraded():
    mode, note = normalize_mode(Intent.REFUND, IntentMode.INQUIRY, "麻烦帮我退款")
    assert mode is IntentMode.EXECUTE


def test_read_intent_not_downgraded():
    """查物流没有写操作风险，不做降级。"""
    mode, _ = normalize_mode(Intent.LOGISTICS, IntentMode.EXECUTE, "物流到哪了")
    assert mode is IntentMode.EXECUTE


def test_change_address_with_question():
    mode, _ = normalize_mode(Intent.CHANGE_ADDRESS, IntentMode.EXECUTE, "能改地址吗")
    assert mode is IntentMode.INQUIRY


@pytest.mark.parametrize("text", ["能不能退", "可不可以退", "退款行不行", "如何退款", "可以吗"])
def test_various_question_markers(text):
    mode, _ = normalize_mode(Intent.REFUND, IntentMode.EXECUTE, text)
    assert mode is IntentMode.INQUIRY


@pytest.mark.parametrize("text", ["我要退款", "帮我退", "麻烦退一下", "给我退了"])
def test_various_imperatives(text):
    mode, _ = normalize_mode(Intent.REFUND, IntentMode.INQUIRY, text)
    assert mode is IntentMode.EXECUTE


def test_requires_order_context():
    assert requires_order_context(Intent.REFUND, IntentMode.EXECUTE, has_order=False)
    assert not requires_order_context(Intent.REFUND, IntentMode.EXECUTE, has_order=True)
    assert not requires_order_context(Intent.REFUND, IntentMode.INQUIRY, has_order=False)
    assert not requires_order_context(Intent.LOGISTICS, IntentMode.EXECUTE, has_order=False)

"""澄清话术：不得含业务承诺。

AI 说出去的承诺与人工客服同等效力。一旦说"可以帮您申请退换"，
用户就会拿这句话来要求退换，而这些条件本系统无权判断。
"""

import pytest

from app.agent.clarify import (
    ALL_SCRIPTS,
    APOLOGY,
    FORBIDDEN_PROMISES,
    clarify_round_one,
    clarify_round_two,
)


@pytest.mark.parametrize("word", FORBIDDEN_PROMISES)
def test_no_business_promise(word):
    for text in ALL_SCRIPTS:
        assert word not in text, f"澄清话术含业务承诺「{word}」：{text}"


def test_negative_gets_apology():
    text = clarify_round_one(negative=True)
    assert text.startswith(APOLOGY)
    assert "具体情况" in text


def test_neutral_does_not_apologize():
    text = clarify_round_one(negative=False)
    assert APOLOGY not in text, "用户没有表达不满时不该道歉"
    assert "具体情况" in text


def test_apology_is_uniform():
    """道歉不区分程度，统一一句，避免分级措辞出错。"""
    assert APOLOGY == "非常抱歉给您带来了不好的体验。"


def test_round_two_offers_handoff_not_options():
    """第 2 轮不再列业务选项——列选项仍然是在引导用户走某条路。"""
    text = clarify_round_two()
    assert "转接" in text or "人工" in text
    for opt in ("查询订单", "修改收货地址", "申请退款", "其他问题"):
        assert opt not in text


def test_no_promise_in_any_script_including_apology():
    for text in ALL_SCRIPTS:
        for word in FORBIDDEN_PROMISES:
            assert word not in text

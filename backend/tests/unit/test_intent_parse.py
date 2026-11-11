"""T013: 关键词只能作为提示，不能影响意图判定。

这组测试的作用是防止以后有人"顺手优化"成规则优先。
"""

import pytest

from app.agent.intent import IntentClassifier, collect_hints
from app.agent.llm.stub import StubLLMClient
from app.config import settings
from app.models.agent import Intent, IntentResult

KEYWORDS = {
    "refund": ["退款", "退货", "退钱"],
    "logistics": ["物流", "快递", "到哪"],
    "change_address": ["改地址", "修改地址"],
    "complaint": ["投诉", "差评"],
}


def test_collect_hints_finds_words():
    assert set(collect_hints("我要退款", KEYWORDS)) == {"退款"}


def test_collect_hints_dedups():
    hits = collect_hints("退款退款退货", KEYWORDS)
    assert hits.count("退款") == 1


@pytest.mark.parametrize(
    "user_input,hint_present",
    [
        ("我要退款", True),
        ("钱什么时候能回来", False),
        ("这个不能用", False),
    ],
)
async def test_hints_do_not_change_outcome(user_input, hint_present):
    """同一条输入，LLM 返回什么就是什么；hints 存在与否不影响结果。"""
    stub = StubLLMClient(classifier=lambda s: IntentResult(intent=Intent.REFUND, confidence=0.9))
    classifier = IntentClassifier(stub, KEYWORDS)

    result, error = await classifier.classify(user_input)
    assert error is None
    assert result.intent is Intent.REFUND
    # hints 确实被收集并传给了 LLM，但没参与判定
    assert bool(stub.last_hints) is hint_present


async def test_keyword_hit_does_not_force_intent():
    """含"退款"二字但当 LLM 判定为投诉时，结果必须是投诉。"""
    text = "再不处理我就投诉你们不肯退款"
    stub = StubLLMClient(classifier=lambda s: IntentResult(intent=Intent.COMPLAINT, confidence=0.88))
    classifier = IntentClassifier(stub, KEYWORDS)

    result, _ = await classifier.classify(text)
    assert result.intent is Intent.COMPLAINT
    assert "退款" in stub.last_hints, "词确实命中了"


async def test_returns_error_on_llm_failure():
    stub = StubLLMClient(fail_mode="timeout")
    classifier = IntentClassifier(stub, KEYWORDS)
    result, error = await classifier.classify("我要退款")
    assert result is None and error is not None


async def test_result_source_is_llm():
    from app.models.agent import IntentSource

    stub = StubLLMClient(default=IntentResult(intent=Intent.GENERAL, confidence=0.4))
    classifier = IntentClassifier(stub, KEYWORDS)
    result, _ = await classifier.classify("随便")
    assert result.source is IntentSource.LLM


# ---------- 查询改写字段解析 ----------

def test_parse_search_query_strips():
    from app.agent.llm.openai_compat import _parse_search_query

    assert _parse_search_query(" 吊牌 商标标识 退货条件 ") == "吊牌 商标标识 退货条件"


def test_parse_search_query_blank_is_none():
    from app.agent.llm.openai_compat import _parse_search_query

    assert _parse_search_query("   ") is None


def test_parse_search_query_non_string_is_none():
    """模型把该字段给成 null/数字/列表时一律视为没改写，不许抛错。"""
    from app.agent.llm.openai_compat import _parse_search_query

    assert _parse_search_query(None) is None
    assert _parse_search_query(123) is None
    assert _parse_search_query(["吊牌"]) is None

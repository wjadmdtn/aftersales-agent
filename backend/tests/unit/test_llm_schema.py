"""LLM 输出结构校验器（specs/007-llm-schema-replay）。

校验器的两个不可妥协点：
1. 整体校验，不部分采纳——截断输出里"看起来合法"的 intent 也不能用
2. 错误信息能被模型照着改——必须报字段名、期望取值、实际收到什么
"""
from __future__ import annotations

import pytest

from app.agent.llm.schema import SchemaError, SchemaErrorKind, validate_intent_result
from app.models.agent import Intent, IntentMode


def _err(raw: str) -> SchemaError:
    with pytest.raises(SchemaError) as exc:
        validate_intent_result(raw)
    return exc.value


# ---------- 正常路径 ----------

def test_valid_output():
    out = validate_intent_result(
        '{"intent":"refund","mode":"execute","confidence":0.9,"reason":"用户要退"}'
    )
    assert out.result.intent is Intent.REFUND
    assert out.result.mode is IntentMode.EXECUTE
    assert out.result.confidence == 0.9
    assert out.warnings == []


def test_mode_null_takes_default_and_warns():
    """模型对 unknown 类输入常回 mode:null —— 这是合理表达，不是结构失败。"""
    out = validate_intent_result('{"intent":"unknown","mode":null,"confidence":0.5}')
    assert out.result.mode is IntentMode.EXECUTE
    assert any("mode 为 null" in w for w in out.warnings)


def test_extra_fields_are_ignored():
    out = validate_intent_result(
        '{"intent":"refund","mode":"execute","confidence":0.8,"extra":{"x":1}}'
    )
    assert out.result.intent is Intent.REFUND


def test_confidence_out_of_range_is_clamped_not_failed():
    out = validate_intent_result('{"intent":"refund","mode":"execute","confidence":1.7}')
    assert out.result.confidence == 1.0
    assert any("越界" in w for w in out.warnings)


def test_confidence_missing_defaults():
    out = validate_intent_result('{"intent":"refund","mode":"execute"}')
    assert out.result.confidence == 0.5


def test_confidence_as_numeric_string_is_accepted():
    out = validate_intent_result('{"intent":"refund","mode":"execute","confidence":"0.7"}')
    assert out.result.confidence == 0.7


# ---------- 失败：缺字段 ----------

def test_missing_mode_is_an_error():
    """关键口径（specs/007 待确认项 1 已实测：真实模型漏 mode 比例 0%）。

    缺 mode 就按 EXECUTE 兜底，等于模型少写一个字段就能退钱。
    """
    e = _err('{"intent":"refund","confidence":0.9}')
    assert e.kind is SchemaErrorKind.MISSING_FIELD
    assert "mode" in e.message


def test_missing_intent_is_an_error():
    e = _err('{"mode":"execute"}')
    assert e.kind is SchemaErrorKind.MISSING_FIELD
    assert "intent" in e.message


# ---------- 失败：非法取值 ----------

def test_chinese_intent_reports_options():
    e = _err('{"intent":"退款","mode":"execute"}')
    assert e.kind is SchemaErrorKind.INVALID_VALUE
    # 错误信息必须可执行：能让模型照着重写
    assert "refund" in e.message and "logistics" in e.message


def test_abbreviated_mode_reports_options():
    e = _err('{"intent":"refund","mode":"exec"}')
    assert e.kind is SchemaErrorKind.INVALID_VALUE
    assert "execute" in e.message and "inquiry" in e.message


def test_intent_wrong_type():
    e = _err('{"intent":123,"mode":"execute"}')
    assert e.kind is SchemaErrorKind.INVALID_TYPE


# ---------- 失败：JSON 语法 ----------

def test_truncated_json_says_truncated():
    e = _err('{"intent":"refund","mode":"exe')
    assert e.kind is SchemaErrorKind.JSON_SYNTAX
    assert "截断" in e.message


def test_empty_output():
    e = _err("   ")
    assert e.kind is SchemaErrorKind.JSON_SYNTAX


def test_top_level_not_object():
    e = _err('["refund", "execute"]')
    assert e.kind is SchemaErrorKind.INVALID_TYPE


def test_prose_wrapped_json_is_extracted():
    out = validate_intent_result(
        '好的，我的判断是：{"intent":"logistics","mode":"query","confidence":0.8}。以上。'
    )
    assert out.result.intent is Intent.LOGISTICS


# ---------- 安全：不得部分采纳 ----------

def test_truncated_output_never_yields_intent():
    """截断的输出里即便包含完整 intent，也不得被提取出来当业务参数。

    这是 specs/007 FR-019：一旦允许"取能解析的那部分"，
    一次模型抽风就能变成一次真实退款。
    """
    raw = '{"intent":"refund","mode":"exe'
    e = _err(raw)
    assert not hasattr(e, "result")


def test_search_query_blank_becomes_none():
    out = validate_intent_result(
        '{"intent":"policy","mode":"inquiry","confidence":0.7,"search_query":"   "}'
    )
    assert out.result.search_query is None


def test_search_query_kept():
    out = validate_intent_result(
        '{"intent":"policy","mode":"inquiry","confidence":0.7,"search_query":"吊牌 商标 摘除"}'
    )
    assert out.result.search_query == "吊牌 商标 摘除"

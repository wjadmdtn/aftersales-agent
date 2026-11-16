"""LLM 输出的严格结构校验。

模型是概率系统，`response_format: json_object` 只保证"是 JSON"，不保证
"字段对"。这里做一件事：把模型的原始输出校验成 `IntentResult`，
校验失败时给出**可执行**的错误说明，供上层带错误重放。

两条硬约束：

1. **整体校验，不部分采纳。** 任一字段非法即整体失败。截断的输出里可能
   恰好包含看起来合法的 `{"intent": "refund", "mode": "exe`，此时若"只取
   能解析的部分"，就会把一次模型抽风变成一次真实退款。
2. **错误信息要能被模型照着改。** 只说"格式错误"等于让模型原地再猜一次；
   必须报出字段名、期望的取值集合、实际收到什么。
"""
from __future__ import annotations

import json
import re
from enum import StrEnum

from pydantic import BaseModel

from app.models.agent import Intent, IntentMode, IntentResult

# 夹带说明文字时，定位第一个 JSON 对象（"好的，我的判断是：{...}"）
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

# json 库对"输出被截断"给出的这几类报错
_TRUNCATION_MESSAGES = (
    "Unterminated string",
    "Expecting ',' delimiter",
    "Expecting property name",
    "Expecting value",
)

_INTENT_OPTIONS = "/".join(i.value for i in Intent)
_MODE_OPTIONS = "/".join(m.value for m in IntentMode)


class SchemaErrorKind(StrEnum):
    JSON_SYNTAX = "json_syntax"
    MISSING_FIELD = "missing_field"
    INVALID_VALUE = "invalid_value"
    INVALID_TYPE = "invalid_type"


class SchemaError(ValueError):
    """结构校验失败。message 会被原样回喂给模型。"""

    def __init__(self, kind: SchemaErrorKind, message: str):
        self.kind = kind
        self.message = message
        super().__init__(message)


class ValidationOutcome(BaseModel):
    result: IntentResult
    warnings: list[str] = []


def _extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise SchemaError(SchemaErrorKind.JSON_SYNTAX, "输出为空")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as first:
        # 允许模型在 JSON 前后夹一句话，但结构本身必须完整
        match = _JSON_OBJECT.search(text)
        if match is None:
            hint = ""
            # 常见截断特征：花括号没闭合、字符串没闭合。
            # 告诉模型"你输出被截断了"比"语法错误"更能让它改对。
            if first.msg in _TRUNCATION_MESSAGES or text.count("{") > text.count("}"):
                hint = "，可能是输出被截断（JSON 结构不完整）"
            raise SchemaError(
                SchemaErrorKind.JSON_SYNTAX,
                f"不是合法 JSON（第 {first.pos} 个字符处：{first.msg}{hint}）。"
                f"原始输出片段：{_clip(text)}",
            ) from first
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as again:
            raise SchemaError(
                SchemaErrorKind.JSON_SYNTAX,
                f"JSON 不完整（第 {again.pos} 个字符处：{again.msg}），"
                f"可能是输出被截断。原始输出片段：{_clip(text)}",
            ) from again

    if not isinstance(data, dict):
        raise SchemaError(
            SchemaErrorKind.INVALID_TYPE,
            f"顶层必须是 JSON 对象，实际是 {type(data).__name__}",
        )
    return data


def _clip(text: str, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _require(data: dict, field: str, *, allow_null: bool = False):
    if field not in data:
        raise SchemaError(
            SchemaErrorKind.MISSING_FIELD,
            f"缺少必填字段 {field}",
        )
    value = data[field]
    if value is None and not allow_null:
        raise SchemaError(
            SchemaErrorKind.MISSING_FIELD,
            f"字段 {field} 不能为 null",
        )
    return value


def validate_intent_result(raw: str) -> ValidationOutcome:
    """把模型原始输出校验成 IntentResult。

    校验口径（与 specs/007-llm-schema-replay 一致）：
    - intent、mode 必填，且必须是枚举值
    - mode 允许显式 null（模型对 unknown 类输入常这么回），由调用方按意图定默认
    - confidence 是数值型，越界截断并记 warning，不算结构失败
    - 多余字段忽略（向前兼容）
    """
    data = _extract_json(raw)
    warnings: list[str] = []

    raw_intent = _require(data, "intent")
    if not isinstance(raw_intent, str):
        raise SchemaError(
            SchemaErrorKind.INVALID_TYPE,
            f"intent 必须是字符串，实际是 {type(raw_intent).__name__}",
        )
    try:
        intent = Intent(raw_intent)
    except ValueError:
        raise SchemaError(
            SchemaErrorKind.INVALID_VALUE,
            f"intent={raw_intent!r} 不是合法值。必须是以下之一：{_INTENT_OPTIONS}",
        ) from None

    # mode 必须在场。缺了字段就按 EXECUTE 兜底，等于模型少写一个字段就能退钱。
    raw_mode = _require(data, "mode", allow_null=True)
    if raw_mode is None:
        mode = _default_mode(intent)
        warnings.append(f"mode 为 null，按意图 {intent.value} 取默认 {mode.value}")
    else:
        try:
            mode = IntentMode(raw_mode)
        except ValueError:
            raise SchemaError(
                SchemaErrorKind.INVALID_VALUE,
                f"mode={raw_mode!r} 不是合法值。必须是以下之一：{_MODE_OPTIONS}",
            ) from None

    confidence = _confidence(data.get("confidence"), warnings)
    reason = data.get("reason")
    if reason is not None and not isinstance(reason, str):
        warnings.append("reason 不是字符串，已忽略")
        reason = None

    search_query = data.get("search_query")
    if not isinstance(search_query, str) or not search_query.strip():
        search_query = None
    else:
        search_query = search_query.strip()

    return ValidationOutcome(
        result=IntentResult(
            intent=intent,
            mode=mode,
            confidence=confidence,
            reason=reason,
            search_query=search_query,
        ),
        warnings=warnings,
    )


def _default_mode(intent: Intent) -> IntentMode:
    """mode 显式为 null 时按意图取默认（不是"缺字段"的兜底）。

    模型对 unknown 类输入常认为"没有可执行的模式"而回 null，这是合理表达：
    unknown 不调任何工具，给 EXECUTE 即可。其余意图同样取 EXECUTE，与改动前一致。
    """
    return IntentMode.EXECUTE


def _confidence(value, warnings: list[str]) -> float:
    if value is None:
        warnings.append("confidence 缺失，按 0.5 处理")
        return 0.5
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            warnings.append(f"confidence={value!r} 不是数值，按 0.5 处理")
            return 0.5
    value = float(value)
    if value < 0.0 or value > 1.0:
        clamped = max(0.0, min(1.0, value))
        warnings.append(f"confidence={value} 越界，已截断为 {clamped}")
        return clamped
    return value

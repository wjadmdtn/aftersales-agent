"""T015: 故障规则解析。

通过 X-Fault 请求头按请求维度注入故障，不改变全局状态。
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.models.enums import FaultType

FAULT_HEADER = "X-Fault"


class FaultRuleError(ValueError):
    pass


class FaultRule(BaseModel):
    type: FaultType
    delay_ms: int = Field(default=0, ge=0)
    status: int = Field(default=500, ge=400, le=599)
    mutate: str | None = None
    field: str | None = None

    @model_validator(mode="after")
    def _check_required(self):
        if self.type is FaultType.DIRTY and not self.field:
            raise ValueError("dirty fault requires 'field'")
        if self.type in (FaultType.DELAY, FaultType.TIMEOUT) and self.delay_ms <= 0:
            raise ValueError(f"{self.type.value} fault requires positive delay_ms")
        return self


def parse_fault_header(raw: str | None) -> FaultRule | None:
    if not raw:
        return None
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        raise FaultRuleError(f"X-Fault is not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise FaultRuleError("X-Fault must be a JSON object")
    try:
        return FaultRule.model_validate(payload)
    except ValidationError as e:
        raise FaultRuleError(str(e)) from e

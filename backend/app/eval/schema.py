"""评测用例与结果的模型。"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class EvalPath(StrEnum):
    """预期处理路径。评测口径按路径分类，而非只分对错。"""

    BUSINESS_ACTION = "business_action"
    CLARIFY = "clarify"
    HANDOFF = "handoff"
    IRRELEVANT = "irrelevant"
    E2E = "e2e"
    POLICY = "policy"


class EvalCase(BaseModel):
    id: str = Field(min_length=1)
    category: str
    path: EvalPath
    input: str | None = None
    turns: list[str] | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    expect: dict[str, Any]
    notes: str | None = None

    @model_validator(mode="after")
    def _input_or_turns(self):
        if self.path is EvalPath.E2E:
            if not self.turns:
                raise ValueError(f"{self.id}: e2e 用例必须有 turns")
        elif not self.input:
            raise ValueError(f"{self.id}: 非 e2e 用例必须有 input")
        return self

    @model_validator(mode="after")
    def _expect_shape(self):
        required = {
            EvalPath.BUSINESS_ACTION: {"intent"},
            EvalPath.CLARIFY: {"state"},
            EvalPath.HANDOFF: {"triggered"},
            EvalPath.IRRELEVANT: {"state"},
            EvalPath.E2E: {"order_status"},
            EvalPath.POLICY: {"intent"},
        }[self.path]
        missing = required - set(self.expect)
        if missing:
            raise ValueError(f"{self.id}: expect 缺少字段 {missing}")
        return self


class GradeResult(BaseModel):
    case_id: str
    path: EvalPath
    category: str
    passed: bool
    failures: list[str] = Field(default_factory=list)
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)


class MetricLine(BaseModel):
    name: str
    value: float
    passed: int | None = None
    total: int | None = None
    baseline: float | None = None
    delta: float | None = None


class EvalReport(BaseModel):
    backend: Literal["stub", "real"]
    model: str | None = None
    case_count: int
    elapsed_seconds: float
    metrics: list[MetricLine]
    results: list[GradeResult]
    blocked: bool = False
    block_reasons: list[str] = Field(default_factory=list)

    @property
    def failures(self) -> list[GradeResult]:
        return [r for r in self.results if not r.passed]

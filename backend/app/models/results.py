"""T006: 统一响应信封。

业务失败一律 HTTP 200，调用方读 code 分流，不碰 HTTP 层。
"""
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, model_validator

from app.models.enums import ResultCode, REJECTED_RANGE

T = TypeVar("T")


class ResultMeta(BaseModel):
    trace_id: str
    request_id: str | None = None


class BizResult(BaseModel, Generic[T]):
    code: ResultCode
    data: T | None = None
    reason: str | None = None
    meta: ResultMeta

    @model_validator(mode="after")
    def _reason_required_for_rejection(self):
        # 3xxx 必须给出原因，调用方要直接展示给用户
        if self.code in REJECTED_RANGE and not self.reason:
            raise ValueError(f"{self.code.name} requires a reason")
        return self


def ok(data: T, meta: ResultMeta) -> BizResult[T]:
    return BizResult(code=ResultCode.OK, data=data, meta=meta)


def not_found(code: ResultCode, meta: ResultMeta, *, data: T | None = None) -> BizResult[T]:
    if code not in range(2000, 3000):
        raise ValueError(f"{code.name} is not a NOT_FOUND code")
    return BizResult(code=code, data=data, meta=meta)


def rejected(code: ResultCode, reason: str, meta: ResultMeta) -> BizResult[Any]:
    if code not in REJECTED_RANGE:
        raise ValueError(f"{code.name} is not a REJECTED code")
    return BizResult(code=code, reason=reason, meta=meta)


def error(code: ResultCode, reason: str, meta: ResultMeta) -> BizResult[Any]:
    if code not in range(4000, 5000):
        raise ValueError(f"{code.name} is not an ERROR code")
    return BizResult(code=code, reason=reason, meta=meta)


__all__ = ["BizResult", "ResultMeta", "ok", "not_found", "rejected", "error"]

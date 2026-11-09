"""T012: 调用记录。环形缓冲，避免无界增长。"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.models.enums import ResultCode


class CallRecord(BaseModel):
    trace_id: str
    request_id: str | None = None
    action: str
    input: dict = Field(default_factory=dict)
    output: dict | None = None
    code: ResultCode
    elapsed_ms: int
    fault_injected: str | None = None
    attempt: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CallRecorder:
    def __init__(self, capacity: int | None = None):
        from app.config import settings

        self._records: deque[CallRecord] = deque(
            maxlen=capacity if capacity is not None else settings.call_record_capacity
        )
        self._attempts: dict[tuple[str, str], int] = {}

    def append(self, record: CallRecord) -> None:
        self._records.append(record)

    def next_attempt(self, action: str, request_id: str | None) -> int:
        """该 (action, request_id) 的第几次尝试。首次返回 1。"""
        if request_id is None:
            return 1
        key = (action, request_id)
        n = self._attempts.get(key, 0) + 1
        self._attempts[key] = n
        return n

    def find_by_trace_id(self, trace_id: str) -> list[CallRecord]:
        return [r for r in self._records if r.trace_id == trace_id]

    def find_by_request_id(self, request_id: str) -> list[CallRecord]:
        """跨 trace_id：该笔业务的所有重试尝试。"""
        return [r for r in self._records if r.request_id == request_id]

    def all(self) -> list[CallRecord]:
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()
        self._attempts.clear()

    def __len__(self) -> int:
        return len(self._records)

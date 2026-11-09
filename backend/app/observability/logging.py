"""T014: 日志格式器，自动从 contextvars 取 trace_id。"""
from __future__ import annotations

import logging

from app.observability.trace import current_trace_id


class TraceFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.trace_id = current_trace_id() or "-"
        return super().format(record)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        TraceFormatter("%(asctime)s %(levelname)s [trace=%(trace_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

"""T013: 链路追踪中间件。

trace_id 是横切关注点，用 contextvars 传递而不是层层传参。
mock 服务是叶子节点，无嵌套调用，因此不需要 span_id。
"""
from __future__ import annotations

import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

TRACE_ID_HEADER = "X-Trace-Id"

trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
fault_ctx: ContextVar[str | None] = ContextVar("fault_injected", default=None)

_SHORT_CIRCUIT_PATHS = ("/docs", "/openapi.json", "/redoc")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_trace_id() -> str:
    return trace_id_ctx.get()


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get(TRACE_ID_HEADER) or new_trace_id()
        token = trace_id_ctx.set(trace_id)
        request.state.trace_id = trace_id
        request.state.started_at = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[TRACE_ID_HEADER] = trace_id
            return response
        finally:
            trace_id_ctx.reset(token)


def elapsed_ms(request: Request) -> int:
    started = getattr(request.state, "started_at", None)
    if started is None:
        return 0
    return int((time.perf_counter() - started) * 1000)

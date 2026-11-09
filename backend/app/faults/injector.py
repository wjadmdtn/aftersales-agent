"""T016: 故障注入中间件。

关键一致性保证：timeout 与 error 必须在业务执行**之前**拦截，
否则会出现"钱退了但调用方以为失败"——这正是要避免的真实现网事故。

    delay   业务已执行，返回前 sleep    → 测"调用方等太久"
    timeout 业务未执行，直接断开        → 测"不确定成功与否"
    error   业务未执行，直接返回 5xx    → 测"明确的失败"
    dirty   业务已执行，返回前篡改响应  → 测"返回了坏数据"
"""
from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.faults.rules import FAULT_HEADER, FaultRule, FaultRuleError, parse_fault_header
from app.models.enums import FaultType, ResultCode

fault_applied_ctx: ContextVar[str | None] = ContextVar("fault_applied", default=None)


class FaultMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.fault_enabled:
            return await call_next(request)

        raw = request.headers.get(FAULT_HEADER)
        if not raw:
            return await call_next(request)

        try:
            rule = parse_fault_header(raw)
        except FaultRuleError as e:
            return JSONResponse(
                status_code=200,
                content={
                    "code": ResultCode.INVALID_REQUEST.value,
                    "data": None,
                    "reason": f"invalid X-Fault header: {e}",
                    "meta": {"trace_id": getattr(request.state, "trace_id", "")},
                },
            )

        if rule is None:
            return await call_next(request)

        token = fault_applied_ctx.set(rule.type.value)
        try:
            # ---- 执行前拦截：业务不得产生任何副作用 ----
            if rule.type is FaultType.TIMEOUT:
                # 睡到超过调用方超时阈值，让调用方先放弃
                await asyncio.sleep(settings.caller_timeout_ms / 1000 + 0.5)
                return Response(status_code=504)

            if rule.type is FaultType.ERROR:
                return JSONResponse(
                    status_code=rule.status,
                    content={
                        "code": ResultCode.INTERNAL_ERROR.value,
                        "data": None,
                        "reason": "injected error",
                        "meta": {"trace_id": getattr(request.state, "trace_id", "")},
                    },
                )

            # ---- 正常执行 ----
            response = await call_next(request)

            # ---- 执行后干预：业务已生效 ----
            if rule.type is FaultType.DELAY:
                await asyncio.sleep(rule.delay_ms / 1000)
                return response

            if rule.type is FaultType.DIRTY:
                return await _mutate_response(response, rule)

            return response
        finally:
            fault_applied_ctx.reset(token)


async def _mutate_response(response: Response, rule: FaultRule) -> Response:
    body = b"".join([chunk async for chunk in response.body_iterator])
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    target = rule.field or ""
    if target.startswith("data."):
        target = target[len("data.") :]
        container = payload.get("data")
    else:
        container = payload

    if isinstance(container, dict):
        if target in container:
            container[target] = None
        else:
            container[target] = None

    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    return JSONResponse(status_code=response.status_code, content=payload, headers=headers)


def current_fault() -> str | None:
    return fault_applied_ctx.get()

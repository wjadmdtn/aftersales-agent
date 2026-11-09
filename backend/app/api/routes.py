"""API 路由。业务失败一律 HTTP 200，调用方读 code 分流。"""
from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.models.domain import Address
from app.models.enums import ResultCode
from app.models.results import BizResult, ResultMeta
from app.observability.recorder import CallRecord, CallRecorder
from app.observability.trace import current_trace_id, elapsed_ms
from app.services import address as address_service
from app.services import logistics as logistics_service
from app.services import order as order_service
from app.services import refund as refund_service
from app.store.memory import MemoryStore

logger = logging.getLogger(__name__)

router = APIRouter()

_store: MemoryStore | None = None
_recorder = CallRecorder(capacity=settings.call_record_capacity)


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore(settings.seed_path, settings.refund_lock_shards)
    return _store


def get_recorder() -> CallRecorder:
    return _recorder


def reset_store() -> None:
    """回到种子状态，供测试使用。"""
    global _store
    _store = MemoryStore(settings.seed_path, settings.refund_lock_shards)
    _recorder.clear()


def meta_of(request: Request) -> ResultMeta:
    return ResultMeta(
        trace_id=current_trace_id(),
        request_id=request.headers.get("X-Request-Id"),
    )


def _record(
    response: BizResult,
    *,
    action: str,
    request: Request,
    payload: dict,
    request_id: str | None = None,
) -> None:
    from app.faults.injector import current_fault

    _recorder.append(
        CallRecord(
            trace_id=current_trace_id(),
            request_id=request_id or request.headers.get("X-Request-Id"),
            action=action,
            input=payload,
            output=response.model_dump(mode="json").get("data"),
            code=response.code,
            elapsed_ms=elapsed_ms(request),
            fault_injected=current_fault(),
            attempt=_recorder.next_attempt(action, request_id or request.headers.get("X-Request-Id")),
        )
    )


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/orders/{order_id}", response_model=BizResult)
async def get_order(order_id: str, request: Request) -> BizResult:
    meta = meta_of(request)
    result = order_service.get_order(get_store(), order_id, meta)
    _record(result, action="order.get", request=request, payload={"order_id": order_id})
    logger.info("order.get order_id=%s code=%s", order_id, result.code.name)
    return result


@router.get("/orders/{order_id}/logistics", response_model=BizResult)
async def get_logistics(order_id: str, request: Request) -> BizResult:
    meta = meta_of(request)
    result = logistics_service.get_logistics(get_store(), order_id, meta)
    _record(result, action="logistics.get", request=request, payload={"order_id": order_id})
    logger.info("logistics.get order_id=%s code=%s", order_id, result.code.name)
    return result


class RefundRequest(BaseModel):
    amount: Decimal = Field(gt=0, decimal_places=2)


@router.post("/orders/{order_id}/refund", response_model=BizResult)
async def create_refund(
    order_id: str, payload: RefundRequest, request: Request
) -> BizResult:
    meta = meta_of(request)
    request_id = request.headers.get("X-Request-Id", "")
    result = await refund_service.create_refund(
        get_store(), order_id, payload.amount, request_id, meta
    )
    _record(
        result,
        action=refund_service.ACTION,
        request=request,
        payload={"order_id": order_id, "amount": str(payload.amount)},
        request_id=request_id or None,
    )
    logger.info("refund.create order_id=%s code=%s", order_id, result.code.name)
    return result


@router.put("/orders/{order_id}/address", response_model=BizResult)
async def update_address(
    order_id: str, address: Address, request: Request
) -> BizResult:
    meta = meta_of(request)
    result = await address_service.update_address(get_store(), order_id, address, meta)
    _record(
        result,
        action="address.update",
        request=request,
        payload={"order_id": order_id},
    )
    logger.info("address.update order_id=%s code=%s", order_id, result.code.name)
    return result


@router.get("/_debug/calls")
async def debug_calls(
    trace_id: str | None = None, request_id: str | None = None
) -> dict:
    if not settings.debug_endpoints_enabled:
        from fastapi import HTTPException

        raise HTTPException(status_code=404)

    if trace_id:
        records = _recorder.find_by_trace_id(trace_id)
    elif request_id:
        records = _recorder.find_by_request_id(request_id)
    else:
        records = _recorder.all()

    return {"records": [r.model_dump(mode="json") for r in records]}


__all__ = ["router", "get_store", "get_recorder", "reset_store", "ResultCode"]

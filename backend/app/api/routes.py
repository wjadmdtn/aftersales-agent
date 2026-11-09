"""API 路由。业务失败一律 HTTP 200，调用方读 code 分流。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import settings
from app.models.results import BizResult, ResultMeta
from app.observability.trace import current_trace_id
from app.store.memory import MemoryStore

router = APIRouter()

_store: MemoryStore | None = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore(settings.seed_path, settings.refund_lock_shards)
    return _store


def reset_store() -> None:
    global _store
    _store = MemoryStore(settings.seed_path, settings.refund_lock_shards)


def meta_of(request: Request) -> ResultMeta:
    return ResultMeta(
        trace_id=current_trace_id(),
        request_id=request.headers.get("X-Request-Id"),
    )


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}

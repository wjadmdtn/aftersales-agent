"""T009: mock 服务客户端。

trace_id 必须透传给下游，否则一次用户输入会被打散成互不相干的链路。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from app.config import settings


class MockServiceClient:
    def __init__(self, base_url: str | None = None, timeout_ms: int | None = None):
        self.base_url = (base_url or settings.mock_service_base_url).rstrip("/")
        self.timeout_ms = timeout_ms or settings.caller_timeout_ms

    def _headers(self, *, trace_id: str, request_id: str | None = None) -> dict[str, str]:
        headers = {"X-Trace-Id": trace_id}
        if request_id:
            headers["X-Request-Id"] = request_id
        return headers

    async def _get(self, path: str, *, trace_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as c:
            r = await c.get(f"{self.base_url}{path}", headers=self._headers(trace_id=trace_id))
            r.raise_for_status()
            return r.json()

    async def get_order(self, order_id: str, *, trace_id: str) -> dict[str, Any]:
        return await self._get(f"/orders/{order_id}", trace_id=trace_id)

    async def get_logistics(self, order_id: str, *, trace_id: str) -> dict[str, Any]:
        return await self._get(f"/orders/{order_id}/logistics", trace_id=trace_id)

    async def create_refund(
        self,
        order_id: str,
        amount: Decimal,
        *,
        request_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as c:
            r = await c.post(
                f"{self.base_url}/orders/{order_id}/refund",
                json={"amount": str(amount)},
                headers=self._headers(trace_id=trace_id, request_id=request_id),
            )
            r.raise_for_status()
            return r.json()

    async def update_address(
        self, order_id: str, address: dict[str, Any], *, trace_id: str, request_id: str | None = None
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as c:
            r = await c.put(
                f"{self.base_url}/orders/{order_id}/address",
                json=address,
                headers=self._headers(trace_id=trace_id, request_id=request_id),
            )
            r.raise_for_status()
            return r.json()

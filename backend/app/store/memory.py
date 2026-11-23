"""T011: 内存存储 + 按订单分段的并发控制。

业务数据是内存态的：启动时从种子加载，运行中只改内存副本。
重启回到种子状态，保证评测可复现。
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path

from app.models.domain import Address, Logistics, Order, Refund, ShipmentUrge
from app.models.enums import OrderStatus
from app.store.seed import SeedData, load_seed


class MemoryStore:
    def __init__(self, seed_path: str | Path, lock_shards: int | None = None):
        from app.config import settings

        if lock_shards is None:
            lock_shards = settings.refund_lock_shards
        self._seed_path = seed_path
        self._data: SeedData = load_seed(seed_path)
        self._refunds: dict[str, Refund] = {}
        # (action, request_id) -> refund_id
        self._refund_by_request: dict[tuple[str, str], str] = {}
        self._urges: dict[str, ShipmentUrge] = {}
        # (action, request_id) -> urge_id
        self._urge_by_request: dict[tuple[str, str], str] = {}
        self._locks = [asyncio.Lock() for _ in range(lock_shards)]

    def reset(self) -> None:
        """回到种子状态，供测试使用。"""
        self._data = load_seed(self._seed_path)
        self._refunds.clear()
        self._refund_by_request.clear()
        self._urges.clear()
        self._urge_by_request.clear()
        self._locks = [asyncio.Lock() for _ in self._locks]

    # ---------- 读 ----------

    def get_order(self, order_id: str) -> Order | None:
        order = self._data.orders.get(order_id)
        return copy.deepcopy(order) if order else None

    def get_logistics(self, order_id: str) -> Logistics | None:
        logistics = self._data.logistics.get(order_id)
        return copy.deepcopy(logistics) if logistics else None

    def has_order(self, order_id: str) -> bool:
        return order_id in self._data.orders

    def orders(self) -> list[Order]:
        return [copy.deepcopy(o) for o in self._data.orders.values()]

    # ---------- 写 ----------

    def update_address(self, order_id: str, address: Address) -> Order:
        order = self._data.orders[order_id]
        order.address = address
        return copy.deepcopy(order)

    def lock_for(self, order_id: str) -> asyncio.Lock:
        """按订单取分段锁，避免不同订单互相阻塞。"""
        return self._locks[hash(order_id) % len(self._locks)]

    def find_refund_by_request(self, action: str, request_id: str) -> Refund | None:
        refund_id = self._refund_by_request.get((action, request_id))
        if refund_id is None:
            return None
        return copy.deepcopy(self._refunds[refund_id])

    def save_refund(self, refund: Refund, action: str) -> None:
        self._refunds[refund.refund_id] = refund
        self._refund_by_request[(action, refund.request_id)] = refund.refund_id
        order = self._data.orders.get(refund.order_id)
        if order is not None:
            order.status = OrderStatus.REFUNDED

    # ---------- 催发货 ----------

    def find_urge_by_request(self, action: str, request_id: str) -> ShipmentUrge | None:
        urge_id = self._urge_by_request.get((action, request_id))
        if urge_id is None:
            return None
        return copy.deepcopy(self._urges[urge_id])

    def find_recent_urge(
        self, order_id: str, *, within_s: int, now: datetime | None = None
    ) -> ShipmentUrge | None:
        """该订单在 within_s 秒内是否已经催过。

        用户连点、网络重试都会产生新的 request_id，光靠幂等键挡不住刷屏，
        所以再加一道按订单的时间窗去重。
        """
        current = now or datetime.now(timezone.utc)
        recent = [
            u for u in self._urges.values()
            if u.order_id == order_id
            and (current - u.created_at).total_seconds() < within_s
        ]
        if not recent:
            return None
        return copy.deepcopy(max(recent, key=lambda u: u.created_at))

    def save_urge(self, urge: ShipmentUrge, action: str) -> None:
        """催单不改变订单状态——它只是通知商家，不是订单流转。"""
        self._urges[urge.urge_id] = urge
        self._urge_by_request[(action, urge.request_id)] = urge.urge_id

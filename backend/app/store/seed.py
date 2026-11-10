"""T010: 种子数据加载。非法种子在启动阶段失败。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.models.domain import Address, Logistics, LogisticsTrack, Order
from app.models.enums import OrderStatus


class SeedValidationError(RuntimeError):
    pass


class _SeedLogistics(BaseModel):
    current_node: str
    tracks: list[dict]


class _SeedOrder(BaseModel):
    order_id: str
    status: OrderStatus
    amount: Decimal
    address: Address
    logistics: _SeedLogistics | None = None
    # 缺失时回落到启动时刻：政策检索按 created_at 选版本，不填就无法做版本过滤
    created_at: datetime | None = None
    product_type: str | None = None


@dataclass
class SeedData:
    orders: dict[str, Order] = field(default_factory=dict)
    logistics: dict[str, Logistics] = field(default_factory=dict)


def _parse_ts(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def load_seed(path: str | Path) -> SeedData:
    p = Path(path)
    if not p.exists():
        raise SeedValidationError(f"seed file not found: {p}")

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SeedValidationError(f"seed file is not valid JSON: {e}") from e

    if not isinstance(raw, list):
        raise SeedValidationError("seed file must contain a JSON array of orders")

    data = SeedData()
    for idx, item in enumerate(raw):
        try:
            seed_order = _SeedOrder.model_validate(item)
        except ValidationError as e:
            raise SeedValidationError(f"invalid seed order at index {idx}: {e}") from e

        if seed_order.order_id in data.orders:
            raise SeedValidationError(f"duplicate order_id in seed: {seed_order.order_id}")

        data.orders[seed_order.order_id] = Order(
            order_id=seed_order.order_id,
            status=seed_order.status,
            amount=seed_order.amount,
            address=seed_order.address,
            product_type=seed_order.product_type,
            created_at=seed_order.created_at or datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        if seed_order.logistics is not None:
            data.logistics[seed_order.order_id] = Logistics(
                order_id=seed_order.order_id,
                current_node=seed_order.logistics.current_node,
                tracks=[
                    LogisticsTrack(node=t["node"], at=_parse_ts(t["at"]))
                    for t in seed_order.logistics.tracks
                ],
            )

    return data

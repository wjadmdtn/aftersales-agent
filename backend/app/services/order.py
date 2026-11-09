"""T023 [US1]: 订单查询。"""
from __future__ import annotations

from app.models.domain import Order
from app.models.enums import ResultCode
from app.models.results import BizResult, ResultMeta, not_found, ok
from app.store.memory import MemoryStore


def get_order(store: MemoryStore, order_id: str, meta: ResultMeta) -> BizResult[Order]:
    order = store.get_order(order_id)
    if order is None:
        return not_found(ResultCode.ORDER_NOT_FOUND, meta)
    return ok(order, meta)

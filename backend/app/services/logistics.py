"""T024 [US1]: 物流查询。

订单存在但无物流数据（如未发货）返回 2002，与订单不存在（2001）区分。
不根据订单状态推断物流，一律如实反映数据。
"""
from __future__ import annotations

from app.models.domain import Logistics
from app.models.enums import ResultCode
from app.models.results import BizResult, ResultMeta, not_found, ok
from app.store.memory import MemoryStore


def get_logistics(
    store: MemoryStore, order_id: str, meta: ResultMeta
) -> BizResult[Logistics]:
    if not store.has_order(order_id):
        return not_found(ResultCode.ORDER_NOT_FOUND, meta)

    logistics = store.get_logistics(order_id)
    if logistics is None:
        return not_found(ResultCode.LOGISTICS_NOT_FOUND, meta)

    return ok(logistics, meta)

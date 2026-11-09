"""T037 [US3]: 修改收货地址。

已发货不可修改。改为完全相同的内容返回成功但不写库（已确认决策 3）。
"""
from __future__ import annotations

from app.models.domain import Address, Order
from app.models.enums import OrderStatus, ResultCode
from app.models.results import BizResult, ResultMeta, not_found, ok, rejected
from app.store.memory import MemoryStore

EDITABLE_STATES = {
    OrderStatus.PENDING_PAYMENT,
    OrderStatus.PAID,
}

_EMPTY_HINT = {
    "receiver": "收件人",
    "phone": "联系电话",
    "province": "省",
    "city": "市",
    "district": "区县",
    "detail": "详细地址",
}


async def update_address(
    store: MemoryStore,
    order_id: str,
    address: Address,
    meta: ResultMeta,
) -> BizResult[Order]:
    if not store.has_order(order_id):
        return not_found(ResultCode.ORDER_NOT_FOUND, meta)

    async with store.lock_for(order_id):
        order = store.get_order(order_id)

        if order.status not in EDITABLE_STATES:
            return rejected(
                ResultCode.ADDRESS_NOT_EDITABLE,
                f"订单状态 {order.status.value} 不允许修改地址",
                meta,
            )

        if order.address == address:
            # 内容完全相同：幂等成功，不产生写操作
            return ok(order, meta)

        updated = store.update_address(order_id, address)
    return ok(updated, meta)


def incomplete_address_hint(field: str) -> str:
    return f"地址缺少{_EMPTY_HINT.get(field, field)}"

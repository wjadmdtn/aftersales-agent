"""T034 [US2]: 发起退款。

唯一直接动钱的不可逆操作。幂等与前置校验必须同时成立：

    1. 按 (action, request_id) 查已存在 → 返回首次结果 + DUPLICATE_REQUEST
    2. 校验订单状态与金额
    3. 分段锁内**二次**检查 request_id 再写入

第 3 步的二次检查是必须的：第 1 步到拿到锁之间，并发请求可能已经写入。
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.domain import Refund
from app.models.enums import OrderStatus, RefundStatus, ResultCode
from app.models.results import BizResult, ResultMeta, error, not_found, ok, rejected
from app.store.memory import MemoryStore

ACTION = "refund.create"

# 可发起退款的订单状态。已在退款中/已退款的不允许，避免重复扣款。
REFUNDABLE_STATES = {OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED}


async def create_refund(
    store: MemoryStore,
    order_id: str,
    amount: Decimal,
    request_id: str,
    meta: ResultMeta,
) -> BizResult[Refund]:
    if not request_id:
        # 缺幂等标识是调用方契约问题，归 4xxx，不是业务拒绝
        return error(ResultCode.INVALID_REQUEST, "缺少幂等标识 request_id", meta)

    # 1. 幂等命中：直接返回首次结果
    existing = store.find_refund_by_request(ACTION, request_id)
    if existing is not None:
        return _duplicate(existing, meta)

    if not store.has_order(order_id):
        return not_found(ResultCode.ORDER_NOT_FOUND, meta)

    order = store.get_order(order_id)

    if order.status not in REFUNDABLE_STATES:
        return rejected(
            ResultCode.ORDER_STATUS_NOT_ALLOWED,
            f"订单状态 {order.status.value} 不允许退款",
            meta,
        )

    if amount <= 0:
        return rejected(ResultCode.AMOUNT_INVALID, "退款金额必须大于 0", meta)
    if amount.as_tuple().exponent < -2:
        return rejected(ResultCode.AMOUNT_INVALID, "退款金额最多两位小数", meta)
    if amount > order.amount:
        return rejected(
            ResultCode.AMOUNT_EXCEEDS_ORDER,
            f"退款金额 {amount} 超过订单金额 {order.amount}",
            meta,
        )

    # 3. 分段锁内二次检查，防止并发下重复写入
    async with store.lock_for(order_id):
        again = store.find_refund_by_request(ACTION, request_id)
        if again is not None:
            return _duplicate(again, meta)

        current = store.get_order(order_id)
        if current.status not in REFUNDABLE_STATES:
            return rejected(
                ResultCode.ORDER_STATUS_NOT_ALLOWED,
                f"订单状态 {current.status.value} 不允许退款",
                meta,
            )

        refund = Refund(
            refund_id=f"RF{uuid.uuid4().hex[:12].upper()}",
            order_id=order_id,
            amount=amount,
            status=RefundStatus.SUCCESS,
            request_id=request_id,
        )
        store.save_refund(refund, ACTION)

    return ok(refund, meta)


def _duplicate(refund: Refund, meta: ResultMeta) -> BizResult[Refund]:
    result = BizResult(
        code=ResultCode.DUPLICATE_REQUEST,
        data=refund,
        reason="重复请求，返回首次退款结果",
        meta=meta,
    )
    return result

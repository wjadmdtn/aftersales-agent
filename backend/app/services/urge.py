"""催发货。

一个写操作，但和退款不是一类：
- 可重复：同一订单催两次不会造成任何损失，所以没有金额和状态机
- 不改变订单：催单只是通知商家，订单仍停在待发货
- 风险在"催不该催的单"（已发货还去催），所以状态校验是唯一硬门禁

幂等两层：
1. (action, request_id) —— 同一次请求重放不产生第二条
2. 同订单时间窗去重 —— 用户连点 / 换了个 request_id 重试也不会刷屏

第 2 层是必须的：客户端每次重试都会生成新 request_id，只靠第 1 层挡不住。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models.domain import ShipmentUrge
from app.models.enums import OrderStatus, ResultCode
from app.models.results import BizResult, ResultMeta, error, not_found, ok, rejected
from app.store.memory import MemoryStore

ACTION = "urge.create"

# 只有"已付款未发货"可以催。待支付催不了（商家还没收到钱），
# 已发货/已签收催了没意义，已退款更没有。
URGEABLE_STATES = {OrderStatus.PAID}

_STATE_HINT = {
    OrderStatus.PENDING_PAYMENT: "这笔订单还没付款，付款后商家才会安排发货。",
    OrderStatus.SHIPPED: "这笔订单已经发货了，不需要催单哦。",
    OrderStatus.DELIVERED: "这笔订单已经签收了，不需要催单哦。",
    OrderStatus.CLOSED: "这笔订单已经关闭了。",
    OrderStatus.REFUNDING: "这笔订单正在退款处理中，不需要催发货。",
    OrderStatus.REFUNDED: "这笔订单已经退款完成了，不需要催发货。",
}


async def create_urge(
    store: MemoryStore,
    order_id: str,
    request_id: str,
    meta: ResultMeta,
    *,
    dedupe_window_s: int | None = None,
    response_hours: int | None = None,
    now: datetime | None = None,
) -> BizResult[ShipmentUrge]:
    if not request_id:
        return error(ResultCode.INVALID_REQUEST, "缺少幂等标识 request_id", meta)

    if not store.has_order(order_id):
        return not_found(ResultCode.ORDER_NOT_FOUND, meta)

    order = store.get_order(order_id)
    if order.status not in URGEABLE_STATES:
        return rejected(
            ResultCode.ORDER_STATUS_NOT_ALLOWED,
            _STATE_HINT.get(order.status, f"订单状态 {order.status.value} 不支持催发货"),
            meta,
        )

    # 1. 幂等命中：同一请求重放，返回首次结果
    existing = store.find_urge_by_request(ACTION, request_id)
    if existing is not None:
        return _duplicate(existing, meta, "重复请求，返回首次催单结果")

    window = settings.urge_dedupe_window_s if dedupe_window_s is None else dedupe_window_s
    hours = settings.urge_response_hours if response_hours is None else response_hours
    current = now or datetime.now(timezone.utc)

    # 2. 时间窗去重：同订单刚催过，直接复用首次结果
    recent = store.find_recent_urge(order_id, within_s=window, now=current)
    if recent is not None:
        return _duplicate(recent, meta, "该订单刚刚已催过，返回首次催单结果")

    # 锁内二次检查：拿到锁之前可能已有并发请求落库
    async with store.lock_for(order_id):
        again = store.find_urge_by_request(ACTION, request_id)
        if again is not None:
            return _duplicate(again, meta, "重复请求，返回首次催单结果")
        recent = store.find_recent_urge(order_id, within_s=window, now=current)
        if recent is not None:
            return _duplicate(recent, meta, "该订单刚刚已催过，返回首次催单结果")

        current_order = store.get_order(order_id)
        if current_order.status not in URGEABLE_STATES:
            return rejected(
                ResultCode.ORDER_STATUS_NOT_ALLOWED,
                _STATE_HINT.get(
                    current_order.status,
                    f"订单状态 {current_order.status.value} 不支持催发货",
                ),
                meta,
            )

        urge = ShipmentUrge(
            urge_id=f"UG{uuid.uuid4().hex[:12].upper()}",
            order_id=order_id,
            request_id=request_id,
            created_at=current,
            merchant_deadline_at=current + timedelta(hours=hours),
        )
        store.save_urge(urge, ACTION)

    return ok(urge, meta)


def _duplicate(urge: ShipmentUrge, meta: ResultMeta, reason: str) -> BizResult[ShipmentUrge]:
    return BizResult(
        code=ResultCode.DUPLICATE_REQUEST,
        data=urge,
        reason=reason,
        meta=meta,
    )

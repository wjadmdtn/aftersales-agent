"""T023: 写操作确认。

确认词**精确匹配**，不能用包含匹配——否则"确认不了"会被当成确认。
执行前始终重新校验订单状态（FR-020），不管 pending 是否过期。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from decimal import Decimal
from typing import Any

from app.config import settings
from app.models.agent import Intent, PendingConfirmation

CANCEL_WORDS = frozenset({"取消", "算了", "不用了", "不要了", "先不", "不退了", "不改了"})


class Action(StrEnum):
    REFUND = "refund"
    CHANGE_ADDRESS = "change_address"


def is_confirmation(reply: str, confirm_words: frozenset[str] | None = None) -> bool:
    words = confirm_words if confirm_words is not None else settings.confirm_words
    return reply.strip() in words


def is_cancellation(reply: str) -> bool:
    return reply.strip() in CANCEL_WORDS


def build_confirmation(
    *,
    intent: Intent,
    order_id: str,
    amount: Decimal | None,
    payload: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> PendingConfirmation:
    ttl = ttl_seconds or settings.confirm_ttl_seconds
    created = datetime.now(timezone.utc)

    if intent is Intent.REFUND:
        action = "refund"
        summary = f"即将为订单 {order_id} 提交退款 {amount} 元，确认吗？"
    else:
        action = "change_address"
        addr = (payload or {}).get("address", {})
        target = " ".join(
            str(addr.get(k, "")) for k in ("province", "city", "district", "detail")
        ).strip()
        summary = f"即将把订单 {order_id} 的收货地址修改为「{target}」，确认吗？"

    return PendingConfirmation(
        action=action,  # type: ignore[arg-type]
        order_id=order_id,
        amount=amount,
        payload=payload or {},
        summary=summary,
        created_at=created,
        expires_at=created + timedelta(seconds=ttl),
    )


def is_expired(pending: PendingConfirmation, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    return current >= pending.expires_at

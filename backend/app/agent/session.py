"""T008: Redis 会话存储。

会话与待确认操作分开存：pending 有独立 TTL，避免确认过期后会话也被清掉。
每次写入刷新会话 TTL，避免活跃会话被误删。
"""
from __future__ import annotations

from decimal import Decimal

from redis.asyncio import Redis

from app.config import settings
from app.models.agent import PendingConfirmation, SessionState

SESSION_KEY = "agent:session:{sid}"
PENDING_KEY = "agent:pending:{sid}"


class SessionStore:
    def __init__(self, redis: Redis, ttl_seconds: int | None = None):
        self.redis = redis
        self.ttl = ttl_seconds or settings.session_ttl_seconds

    async def get(self, session_id: str) -> SessionState | None:
        raw = await self.redis.get(SESSION_KEY.format(sid=session_id))
        if raw is None:
            return None
        return SessionState.model_validate_json(raw)

    async def save(self, state: SessionState) -> None:
        key = SESSION_KEY.format(sid=state.session_id)
        await self.redis.set(key, state.model_dump_json(), ex=self.ttl)

    async def get_or_create(
        self,
        session_id: str,
        *,
        user_id: str,
        order_id: str | None = None,
        order_owner_id: str | None = None,
        snapshot=None,
    ) -> SessionState:
        existing = await self.get(session_id)
        if existing is not None:
            return existing
        state = SessionState(
            session_id=session_id,
            user_id=user_id,
            order_id=order_id,
            order_owner_id=order_owner_id,
            snapshot=snapshot,
        )
        await self.save(state)
        return state

    async def delete(self, session_id: str) -> None:
        await self.redis.delete(
            SESSION_KEY.format(sid=session_id),
            PENDING_KEY.format(sid=session_id),
        )

    # ---------- 待确认操作 ----------

    async def set_pending(self, session_id: str, pending: PendingConfirmation) -> None:
        await self.redis.set(
            PENDING_KEY.format(sid=session_id),
            pending.model_dump_json(),
            ex=settings.confirm_ttl_seconds,
        )

    async def get_pending(self, session_id: str) -> PendingConfirmation | None:
        raw = await self.redis.get(PENDING_KEY.format(sid=session_id))
        if raw is None:
            return None
        return PendingConfirmation.model_validate_json(raw)

    async def clear_pending(self, session_id: str) -> None:
        await self.redis.delete(PENDING_KEY.format(sid=session_id))


def snapshot_mismatch(snapshot, real) -> bool:
    """快照与库不一致时返回 True。业务一律用库的值，但必须留痕。"""
    if snapshot is None or snapshot.amount is None:
        return False
    real_amount: Decimal | None = getattr(real, "amount", None)
    if real_amount is None:
        return False
    return Decimal(snapshot.amount) != Decimal(real_amount)

"""T019: 队列。

ZSET score：高优先级 score 更小，ZRANGE 自然把高优先级排前面；
同优先级用入队时间戳区分先后。
"""
from __future__ import annotations

from datetime import datetime, timezone

from redis.asyncio import Redis

from app.models.handoff import (
    PRIORITY_SCORE,
    HandoffContext,
    Priority,
    QueueItem,
)

QUEUE_KEY = "handoff:queue"
PACK_KEY = "handoff:pack:{sid}"
REQUESTED_KEY = "handoff:requested:{sid}"
STATE_KEY = "handoff:state:{sid}"


class HandoffQueue:
    def __init__(self, redis: Redis):
        self.redis = redis

    def _score(self, ctx: HandoffContext) -> float:
        return -PRIORITY_SCORE[ctx.priority] * 1e6 + ctx.queued_at.timestamp()

    async def enqueue(self, ctx: HandoffContext) -> bool:
        """返回 True 表示新占了一个队列位；False 表示该会话已在队列中。"""
        await self.redis.set(PACK_KEY.format(sid=ctx.session_id), ctx.model_dump_json())

        is_new = await self.redis.set(
            REQUESTED_KEY.format(sid=ctx.session_id), "1", nx=True
        )
        if not is_new:
            return False

        await self.redis.zadd(QUEUE_KEY, {ctx.session_id: self._score(ctx)})
        return True

    async def peek(self, limit: int = 20) -> list[HandoffContext]:
        ids = await self.redis.zrange(QUEUE_KEY, 0, limit - 1)
        out: list[HandoffContext] = []
        for sid in ids:
            ctx = await self.get_pack(sid)
            if ctx is not None:
                out.append(ctx)
        return out

    async def get_pack(self, session_id: str) -> HandoffContext | None:
        raw = await self.redis.get(PACK_KEY.format(sid=session_id))
        if raw is None:
            return None
        return HandoffContext.model_validate_json(raw)

    async def accept(self, session_id: str) -> HandoffContext | None:
        ctx = await self.get_pack(session_id)
        if ctx is None:
            return None
        await self.redis.zrem(QUEUE_KEY, session_id)
        await self.redis.delete(REQUESTED_KEY.format(sid=session_id))
        return ctx

    async def decline(self, session_id: str) -> HandoffContext | None:
        """人工拒绝：出队，但**不回流给 AI**。"""
        ctx = await self.get_pack(session_id)
        await self.redis.zrem(QUEUE_KEY, session_id)
        await self.redis.delete(REQUESTED_KEY.format(sid=session_id))
        return ctx

    async def depth(self) -> int:
        return int(await self.redis.zcard(QUEUE_KEY))

    async def depth_by_priority(self) -> dict[str, int]:
        counts = {p.value: 0 for p in Priority}
        for ctx in await self.peek(limit=1000):
            counts[ctx.priority.value] += 1
        return counts

    async def clear(self, session_id: str) -> None:
        await self.redis.zrem(QUEUE_KEY, session_id)
        await self.redis.delete(
            REQUESTED_KEY.format(sid=session_id),
            PACK_KEY.format(sid=session_id),
        )

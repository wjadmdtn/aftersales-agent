"""Queue + handoff bookkeeping backed by Redis.

Idempotency is the point of this module: one session may ask for a human many times,
but it must only ever occupy one slot in the queue.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from redis import asyncio as aioredis

from app.config import HandoffRules, settings
from app.handoff.context_pack import priority_sort_key
from app.models import HandoffContext, PRIORITY_SCORE, Priority

QUEUE_KEY = "handoff:queue"
PACK_KEY = "handoff:pack:{session_id}"
REQUESTED_KEY = "handoff:requested:{session_id}"


class HandoffQueue:
    def __init__(self, redis: aioredis.Redis, rules: HandoffRules | None = None):
        self.redis = redis
        self.rules = rules or settings.handoff

    async def enqueue(self, ctx: HandoffContext) -> bool:
        """Returns True if a new queue slot was taken, False if the session was already queued."""
        already = await self.redis.set(REQUESTED_KEY.format(session_id=ctx.session_id), "1", nx=True)
        await self.redis.set(PACK_KEY.format(session_id=ctx.session_id), ctx.model_dump_json())

        if not already:
            # idempotent: refresh the pack (newer context) but keep the original queue position
            return False

        score = self._score(ctx)
        await self.redis.zadd(QUEUE_KEY, {ctx.session_id: score})
        return True

    def _score(self, ctx: HandoffContext) -> float:
        # higher priority first, then earlier queue time
        rank, ts = priority_sort_key(ctx)
        return rank * 1e6 + ts

    async def peek(self, limit: int = 10) -> list[HandoffContext]:
        ids = await self.redis.zrange(QUEUE_KEY, 0, limit - 1)
        return [ctx for ctx in [await self.get_pack(i) for i in ids] if ctx is not None]

    async def get_pack(self, session_id: str) -> HandoffContext | None:
        raw = await self.redis.get(PACK_KEY.format(session_id=session_id))
        if raw is None:
            return None
        return HandoffContext.model_validate_json(raw)

    async def accept(self, session_id: str) -> HandoffContext | None:
        """Human side claims the session. Removes it from the queue."""
        ctx = await self.get_pack(session_id)
        if ctx is None:
            return None
        await self.redis.zrem(QUEUE_KEY, session_id)
        return ctx

    async def decline(self, session_id: str, *, reason: str) -> None:
        """Human side refuses. Per design the session does NOT flow back to the AI."""
        await self.redis.set(
            f"handoff:declined:{session_id}",
            json.dumps({"reason": reason, "at": datetime.now(timezone.utc).isoformat()}),
        )
        await self.redis.zrem(QUEUE_KEY, session_id)
        await self.redis.delete(REQUESTED_KEY.format(session_id=session_id))

    async def clear(self, session_id: str) -> None:
        await self.redis.zrem(QUEUE_KEY, session_id)
        await self.redis.delete(REQUESTED_KEY.format(session_id=session_id))
        await self.redis.delete(PACK_KEY.format(session_id=session_id))

    async def overdue(self) -> list[HandoffContext]:
        """Sessions that waited past their expected window; used for the fallback message."""
        now = datetime.now(timezone.utc).timestamp()
        overdue: list[HandoffContext] = []
        for ctx in await self.peek(limit=1000):
            waited = now - ctx.queued_at.timestamp()
            if waited > ctx.expected_wait_seconds * self.rules.queue_timeout_multiplier:
                overdue.append(ctx)
        return overdue


def queue_depth_by_priority(packs: list[HandoffContext]) -> dict[str, int]:
    counts = {p.value: 0 for p in Priority}
    for ctx in packs:
        counts[ctx.priority.value] += 1
    return counts


__all__ = ["HandoffQueue", "QUEUE_KEY", "PRIORITY_SCORE", "queue_depth_by_priority"]

"""消息存储：Redis List + 单调序号。

两条刻意约束（见 data-model §3）：
1. 流式片段不落库，done 时落一条完整消息，复用首个 delta 的 seq
2. 重连补齐超过上限时返回最近 N 条并标 truncated，不静默少给
"""
from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.chat.protocol import ChatMessage, Role

MSG_KEY = "chat:msg:{sid}"
SEQ_KEY = "chat:seq:{sid}"


@dataclass
class Backfill:
    messages: list[ChatMessage]
    last_seq: int
    truncated: bool


class MessageStore:
    def __init__(self, redis: Redis, ttl_seconds: int):
        self.redis = redis
        self.ttl = ttl_seconds

    async def next_seq(self, session_id: str) -> int:
        key = SEQ_KEY.format(sid=session_id)
        seq = await self.redis.incr(key)
        await self.redis.expire(key, self.ttl)
        return int(seq)

    async def append(
        self,
        session_id: str,
        *,
        role: Role | str,
        text: str,
        seq: int | None = None,
        sender_id: str | None = None,
        streaming: bool = False,
        final: bool = True,
        trace_id: str | None = None,
    ) -> ChatMessage:
        seq = seq if seq is not None else await self.next_seq(session_id)
        msg = ChatMessage(
            session_id=session_id,
            seq=seq,
            role=Role(role),
            sender_id=sender_id,
            text=text,
            streaming=streaming,
            final=final,
            trace_id=trace_id,
        )
        key = MSG_KEY.format(sid=session_id)
        await self.redis.rpush(key, msg.model_dump_json())
        await self.redis.expire(key, self.ttl)
        return msg

    async def list(self, session_id: str, *, after_seq: int = 0) -> list[ChatMessage]:
        raw = await self.redis.lrange(MSG_KEY.format(sid=session_id), 0, -1)
        out = [ChatMessage.model_validate_json(r) for r in raw]
        return [m for m in out if m.seq > after_seq]

    async def last_seq(self, session_id: str) -> int:
        raw = await self.redis.get(SEQ_KEY.format(sid=session_id))
        return int(raw) if raw else 0

    async def backfill(self, session_id: str, *, after_seq: int, limit: int) -> Backfill:
        items = await self.list(session_id, after_seq=after_seq)
        last = await self.last_seq(session_id)
        if len(items) > limit:
            return Backfill(messages=items[-limit:], last_seq=last, truncated=True)
        return Backfill(messages=items, last_seq=last, truncated=False)

    async def clear(self, session_id: str) -> None:
        await self.redis.delete(MSG_KEY.format(sid=session_id), SEQ_KEY.format(sid=session_id))

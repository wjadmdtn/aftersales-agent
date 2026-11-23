"""入站限流。

放在调模型之前：被限的消息一分钱都不该花。计在 Redis 里而不是进程内，
多副本部署时配额才是全局的。

固定窗口而非滑动窗口：客服对话不需要毫秒级精度，INCR + EXPIRE 足够，
代价是窗口边界可能放过约两倍的请求——对"防刷单"这个目标可以接受。
"""
from __future__ import annotations

from redis.asyncio import Redis


async def allow(
    redis: Redis, key: str, *, limit: int, window_s: int
) -> tuple[bool, int]:
    """固定窗口计数。返回 (是否放行, 建议等待秒数)。

    超限时返回窗口剩余时间，让前端能告诉用户"等几秒"，而不是笼统的系统繁忙。
    """
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, window_s)
    if n > limit:
        ttl = await redis.ttl(key)
        return False, max(int(ttl), 1)
    return True, 0


async def check_message_rates(
    redis: Redis, *, user_key: str, ip_key: str | None
) -> tuple[bool, int]:
    """两层用户限流 + IP 兜底。

    user_id 是客户端自报的，换个 user_id 就绕过用户层——所以有 IP 层。
    IP 阈值比用户层宽，因为同一出口后面可能有多个真实用户。
    """
    from app.config import settings

    ok, wait = await allow(
        redis,
        user_key,
        limit=settings.ws_inbound_rate_limit,
        window_s=settings.ws_inbound_rate_window_s,
    )
    if not ok:
        return False, wait

    ok, wait = await allow(
        redis,
        user_key + ":sustained",
        limit=settings.ws_sustained_rate_limit,
        window_s=settings.ws_sustained_rate_window_s,
    )
    if not ok:
        return False, wait

    if ip_key:
        ok, wait = await allow(
            redis,
            ip_key,
            limit=settings.ws_inbound_rate_limit * 5,
            window_s=settings.ws_inbound_rate_window_s,
        )
        if not ok:
            return False, wait

    return True, 0

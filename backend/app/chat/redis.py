"""Redis 连接构造的唯一入口。

生产走 Redis.from_url，测试 monkeypatch 本函数换成 fakeredis——
被测代码路径不变，只是连接对象不同。
"""
from __future__ import annotations

from redis.asyncio import Redis

from app.config import settings


def make_redis(url: str | None = None) -> Redis:
    return Redis.from_url(url or settings.redis_url, decode_responses=True)

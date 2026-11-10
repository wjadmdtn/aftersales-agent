"""本地演示/验证用启动器：用 fakeredis 顶替 Redis，让没装 Redis 的机器也能跑。

生产用 uvicorn app.main:app（走真 Redis）。这里只用于本地联调与 E2E。
"""
from __future__ import annotations

import fakeredis.aioredis

from app.chat import redis as chat_redis

_shared = fakeredis.aioredis.FakeRedis(decode_responses=True)


def _make_redis(url: str | None = None):
    return _shared


chat_redis.make_redis = _make_redis  # type: ignore[assignment]

from app.main import create_app  # noqa: E402

app = create_app()

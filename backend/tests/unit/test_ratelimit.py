"""固定窗口限流。"""
from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.chat.ratelimit import allow


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def test_allows_up_to_limit(redis):
    for _ in range(3):
        ok, wait = await allow(redis, "k", limit=3, window_s=5)
        assert ok
        assert wait == 0


async def test_rejects_over_limit_and_reports_wait(redis):
    for _ in range(3):
        await allow(redis, "k", limit=3, window_s=5)

    ok, wait = await allow(redis, "k", limit=3, window_s=5)
    assert not ok
    # 必须给一个正的等待时间，前端要据此告诉用户等几秒
    assert 1 <= wait <= 5


async def test_key_isolated_between_users(redis):
    for _ in range(3):
        await allow(redis, "u:a", limit=3, window_s=5)

    ok, _ = await allow(redis, "u:b", limit=3, window_s=5)
    assert ok, "一个用户超限不能影响另一个用户"


async def test_window_resets_after_expiry(redis):
    for _ in range(2):
        await allow(redis, "k", limit=2, window_s=1)
    ok, _ = await allow(redis, "k", limit=2, window_s=1)
    assert not ok

    # 直接删掉等价于窗口过期，避免测试真的等 1 秒
    await redis.delete("k")
    ok, wait = await allow(redis, "k", limit=2, window_s=1)
    assert ok and wait == 0


async def test_ttl_set_only_on_first_hit(redis):
    """首次写入才设过期，否则持续请求会把窗口不断延后，永远不过期。"""
    await allow(redis, "k", limit=10, window_s=5)
    ttl1 = await redis.ttl("k")
    await allow(redis, "k", limit=10, window_s=5)
    ttl2 = await redis.ttl("k")
    assert ttl2 <= ttl1

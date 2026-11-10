"""T006: 消息存储。

seq 的语义是"同一会话的时间线位置"——两端靠它去重和补齐。
这里锁死几个容易做错的点：单调、补号、流式共号、截断标记。
"""
from __future__ import annotations

import pytest

from app.chat.messages import MessageStore
from app.chat.protocol import Role


@pytest.fixture
async def store():
    import fakeredis.aioredis

    from app.chat.messages import MessageStore as MS

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield MS(r, ttl_seconds=3600)
    await r.aclose()


async def test_seq_starts_at_one_and_increments(store):
    assert await store.next_seq("s1") == 1
    assert await store.next_seq("s1") == 2
    # 不同会话各自独立
    assert await store.next_seq("s2") == 1


async def test_append_and_list_roundtrip(store):
    m = await store.append("s1", role=Role.USER, text="你好", sender_id="u1")
    assert m.seq == 1
    assert m.id
    items = await store.list("s1")
    assert len(items) == 1
    assert items[0].text == "你好"
    assert items[0].role == "user"


async def test_after_seq_filters(store):
    for i in range(5):
        await store.append("s1", role=Role.USER, text=f"m{i}")
    got = await store.list("s1", after_seq=3)
    assert [m.seq for m in got] == [4, 5]


async def test_append_with_reserved_seq_keeps_single_number(store):
    """流式回复：首个 delta 占号，done 落库复用同号，不产生两条。"""
    seq = await store.next_seq("s1")
    await store.append("s1", role=Role.ASSISTANT, text="完整回复", seq=seq)
    seq2 = await store.next_seq("s1")
    assert seq2 == seq + 1
    items = await store.list("s1")
    assert len(items) == 1
    assert items[0].seq == seq


async def test_truncated_flag_when_over_limit(store):
    for i in range(10):
        await store.append("s1", role=Role.USER, text=f"m{i}")
    page = await store.backfill("s1", after_seq=0, limit=4)
    assert len(page.messages) == 4
    assert page.truncated is True
    assert page.last_seq == 10
    # 截断时给的是最近 N 条
    assert [m.seq for m in page.messages] == [7, 8, 9, 10]


async def test_not_truncated_when_under_limit(store):
    await store.append("s1", role=Role.USER, text="only")
    page = await store.backfill("s1", after_seq=0, limit=200)
    assert page.truncated is False
    assert page.last_seq == 1


async def test_last_seq_zero_for_unknown_session(store):
    assert await store.last_seq("nope") == 0


async def test_ttl_is_applied(store):
    import fakeredis.aioredis

    await store.append("s1", role=Role.USER, text="hi")
    ttl = await store.redis.ttl(f"chat:msg:s1")
    assert 0 < ttl <= 3600

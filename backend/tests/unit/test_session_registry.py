"""T007: 会话对外状态（ChatState）。

这是界面上"当前谁在服务"的唯一权威来源。
写入口只能来自状态机迁移，终态之后不可再改。
"""
from __future__ import annotations

import pytest

from app.chat.session_registry import SessionRegistry
from app.models.handoff import HandoffState


@pytest.fixture
async def reg():
    import fakeredis.aioredis

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield SessionRegistry(r, ttl_seconds=3600)
    await r.aclose()


async def test_ensure_creates_normal_state(reg):
    st = await reg.ensure("s1", user_id="u1", order_id="ORD1002")
    assert st.state is HandoffState.NORMAL
    assert st.user_id == "u1"
    assert st.order_id == "ORD1002"
    assert st.agent_id is None
    assert st.last_seq == 0


async def test_ensure_is_idempotent_and_keeps_existing(reg):
    await reg.ensure("s1", user_id="u1")
    again = await reg.ensure("s1", user_id="someone-else", order_id="ORD1002")
    assert again.user_id == "u1"        # 不覆盖
    assert again.order_id is None       # 也不补写


async def test_get_returns_none_for_unknown(reg):
    assert await reg.get("nope") is None


async def test_set_state_writes_agent_fields(reg):
    await reg.ensure("s1", user_id="u1")
    st = await reg.set_state("s1", HandoffState.HUMAN_SERVING,
                             agent_id="demo-agent", agent_name="小王")
    assert st.state is HandoffState.HUMAN_SERVING
    assert st.agent_id == "demo-agent"
    assert st.agent_name == "小王"


async def test_resolved_is_terminal(reg):
    await reg.ensure("s1", user_id="u1")
    await reg.set_state("s1", HandoffState.QUEUED)
    await reg.set_state("s1", HandoffState.HUMAN_SERVING, agent_id="a", agent_name="甲")
    await reg.set_state("s1", HandoffState.RESOLVED)
    with pytest.raises(ValueError, match="已解决"):
        await reg.set_state("s1", HandoffState.QUEUED)


async def test_set_state_requires_existing_session(reg):
    with pytest.raises(KeyError):
        await reg.set_state("nope", HandoffState.QUEUED)


async def test_last_seq_is_monotonic(reg):
    await reg.ensure("s1", user_id="u1")
    await reg.touch_seq("s1", 5)
    assert (await reg.get("s1")).last_seq == 5
    await reg.touch_seq("s1", 3)   # 旧号不回退
    assert (await reg.get("s1")).last_seq == 5
    await reg.touch_seq("s1", 9)
    assert (await reg.get("s1")).last_seq == 9


async def test_list_by_user_and_agent(reg):
    await reg.ensure("s1", user_id="u1")
    await reg.ensure("s2", user_id="u1")
    await reg.set_state("s2", HandoffState.QUEUED)
    await reg.set_state("s2", HandoffState.HUMAN_SERVING, agent_id="demo-agent", agent_name="小王")

    mine = await reg.list_for_agent("demo-agent")
    assert [s.session_id for s in mine] == ["s2"]

    users = await reg.list_for_user("u1")
    assert {s.session_id for s in users} == {"s1", "s2"}


async def test_order_id_can_be_updated_on_switch(reg):
    await reg.ensure("s1", user_id="u1", order_id="ORD1002")
    st = await reg.set_order("s1", "ORD1003")
    assert st.order_id == "ORD1003"

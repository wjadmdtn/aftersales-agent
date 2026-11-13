"""会话空闲超时：5 分钟没有继续问答即结束本次会话。

产品口径（2026-11-12）：
- 用户每次进入对话页，AI 先发一条问候
- 5 分钟没有继续问答 → 本次会话结束（人工状态一并关掉，不再保留）
- 下次进入 = 全新会话，重新由 AI 问候

为什么按"打开时判定"而不是定时器：定时器需要后台轮询、会占用资源，
且判定时机本质上只在用户回来那一刻才有意义。读时判定即可。
"""
from __future__ import annotations

import pytest

from app.config import settings


@pytest.fixture
async def registry():
    import fakeredis.aioredis

    from app.chat.session_registry import SessionRegistry

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield SessionRegistry(r, ttl_seconds=3600)
    await r.aclose()


async def test_new_session_is_not_expired(registry):
    st = await registry.ensure("s1", user_id="u1")
    assert registry.is_expired(st) is False


async def test_session_expires_after_timeout(registry, monkeypatch):
    from datetime import datetime, timedelta, timezone

    st = await registry.ensure("s2", user_id="u1")
    # 假装已过 5 分钟
    st.updated_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.chat_inactivity_timeout_s + 1
    )
    await registry._save(st)
    loaded = await registry.get("s2")
    assert registry.is_expired(loaded) is True


async def test_touch_postpones_expiry(registry):
    """有新的问答就刷新时间，不会在对话中途被判定超时。"""
    from datetime import datetime, timedelta, timezone

    st = await registry.ensure("s3", user_id="u1")
    st.updated_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.chat_inactivity_timeout_s + 1
    )
    await registry._save(st)
    await registry.touch("s3")
    loaded = await registry.get("s3")
    assert registry.is_expired(loaded) is False


async def test_expired_human_session_returns_to_normal(registry):
    """超时后人工状态一并关掉：下次进入是全新会话，不残留 human_serving。"""
    from datetime import datetime, timedelta, timezone

    from app.models.handoff import HandoffState

    st = await registry.ensure("s4", user_id="u1")
    st.state = HandoffState.HUMAN_SERVING
    st.agent_id = "demo-agent"
    st.updated_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.chat_inactivity_timeout_s + 1
    )
    await registry._save(st)

    fresh = await registry.reset_if_expired("s4", user_id="u1")
    assert fresh.state is HandoffState.NORMAL
    assert fresh.agent_id is None

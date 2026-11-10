"""会话对外状态：chat:state:{sid}。

唯一写入口是 HandoffCoordinator 的合法迁移之后（见 chat/broadcast.py）。
这里只提供存取，不判断业务合法性——唯一的例外是 RESOLVED 终态，
因为那是状态机层面的硬约束，不该指望调用方记得。
"""
from __future__ import annotations

from redis.asyncio import Redis

from app.chat.protocol import ChatState, utcnow
from app.models.handoff import HandoffState

STATE_KEY = "chat:state:{sid}"
CLAIM_KEY = "chat:claim:{sid}"
# 会话索引：避免每次 list 都 SCAN 全库
USER_INDEX = "chat:index:user:{uid}"
AGENT_INDEX = "chat:index:agent:{aid}"


class SessionRegistry:
    def __init__(self, redis: Redis, ttl_seconds: int):
        self.redis = redis
        self.ttl = ttl_seconds

    async def get(self, session_id: str) -> ChatState | None:
        raw = await self.redis.get(STATE_KEY.format(sid=session_id))
        if raw is None:
            return None
        return ChatState.model_validate_json(raw)

    async def _save(self, st: ChatState) -> ChatState:
        key = STATE_KEY.format(sid=st.session_id)
        await self.redis.set(key, st.model_dump_json(), ex=self.ttl)
        await self.redis.sadd(USER_INDEX.format(uid=st.user_id), st.session_id)
        await self.redis.expire(USER_INDEX.format(uid=st.user_id), self.ttl)
        if st.agent_id:
            await self.redis.sadd(AGENT_INDEX.format(aid=st.agent_id), st.session_id)
            await self.redis.expire(AGENT_INDEX.format(aid=st.agent_id), self.ttl)
        return st

    async def ensure(
        self, session_id: str, *, user_id: str, order_id: str | None = None
    ) -> ChatState:
        existing = await self.get(session_id)
        if existing is not None:
            return existing
        return await self._save(
            ChatState(session_id=session_id, user_id=user_id, order_id=order_id)
        )

    async def set_state(
        self,
        session_id: str,
        state: HandoffState,
        *,
        agent_id: str | None = None,
        agent_name: str | None = None,
    ) -> ChatState:
        st = await self.get(session_id)
        if st is None:
            raise KeyError(session_id)
        if st.state is HandoffState.RESOLVED:
            raise ValueError(f"会话已解决，不能再迁移：{session_id}")
        st.state = HandoffState(state)
        if agent_id is not None:
            st.agent_id = agent_id
        if agent_name is not None:
            st.agent_name = agent_name
        st.updated_at = utcnow()
        return await self._save(st)

    async def set_order(self, session_id: str, order_id: str) -> ChatState:
        st = await self.get(session_id)
        if st is None:
            raise KeyError(session_id)
        st.order_id = order_id
        st.updated_at = utcnow()
        return await self._save(st)

    async def touch_seq(self, session_id: str, seq: int) -> ChatState:
        st = await self.get(session_id)
        if st is None:
            raise KeyError(session_id)
        if seq > st.last_seq:
            st.last_seq = seq
            await self._save(st)
        return st

    async def _collect(self, index_key: str) -> list[ChatState]:
        ids = await self.redis.smembers(index_key)
        out: list[ChatState] = []
        for sid in ids:
            st = await self.get(sid)
            if st is not None:
                out.append(st)
        out.sort(key=lambda s: s.updated_at)
        return out

    async def list_for_agent(self, agent_id: str) -> list[ChatState]:
        return await self._collect(AGENT_INDEX.format(aid=agent_id))

    async def list_for_user(self, user_id: str) -> list[ChatState]:
        return await self._collect(USER_INDEX.format(uid=user_id))

    async def clear(self, session_id: str) -> None:
        await self.redis.delete(
            STATE_KEY.format(sid=session_id), CLAIM_KEY.format(sid=session_id)
        )

    # ---------- 接入抢锁（FR-015 / FR-032）----------

    async def try_claim(self, session_id: str, agent_id: str) -> bool:
        """SET NX：同一会话只能被一个客服接入。"""
        ok = await self.redis.set(
            CLAIM_KEY.format(sid=session_id), agent_id, nx=True, ex=self.ttl
        )
        return bool(ok)

    async def claim_owner(self, session_id: str) -> str | None:
        return await self.redis.get(CLAIM_KEY.format(sid=session_id))

    async def release_claim(self, session_id: str) -> None:
        await self.redis.delete(CLAIM_KEY.format(sid=session_id))

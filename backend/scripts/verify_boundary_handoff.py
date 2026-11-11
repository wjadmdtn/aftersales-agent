"""真机验证：rerank 判定不相关 → 直接转人工（BOUNDARY），不走追问。

三个场景各代表一种边界，预期行为不同：
1. 检索过阈值但 rerank 全剔除 = 知识缺口 → 直接转人工，trigger=BOUNDARY
2. 正常命中 → 政策回答，不转人工（防误伤对照）
3. 检索层分数不够（没进 rerank）→ 仍走澄清，不转人工

跑法：cd backend && PYTHONIOENCODING=utf-8 HF_HUB_OFFLINE=1 uv run python scripts/verify_boundary_handoff.py
"""
import asyncio
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import fakeredis.aioredis  # noqa: E402

from app.agent.intent import IntentClassifier  # noqa: E402
from app.agent.llm.openai_compat import OpenAICompatLLMClient  # noqa: E402
from app.agent.loop import AgentLoop  # noqa: E402
from app.agent.session import SessionStore  # noqa: E402
from app.config import settings  # noqa: E402
from app.handoff.coordinator import HandoffCoordinator  # noqa: E402
from app.handoff.queue import HandoffQueue  # noqa: E402
from app.services.policy import build_policy_service  # noqa: E402

CASES = [
    ("能合并订单发货吗", "rerank 拒答场景：预期直接转人工（BOUNDARY）"),
    ("退款多久到账", "正常命中：预期政策回答，不转人工"),
    ("吊牌被剪了还能退吗", "检索层 miss：预期澄清而非转人工"),
]


async def main():
    llm = OpenAICompatLLMClient()
    policy = build_policy_service(llm)
    print(f"embedder={settings.rag_embedder} rerank={policy.reranker is not None}")

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sessions = SessionStore(redis, ttl_seconds=3600)
    coordinator = HandoffCoordinator(HandoffQueue(redis))
    loop = AgentLoop(
        classifier=IntentClassifier(llm, settings.keyword_hints),
        sessions=sessions,
        tools=None,
        policy=policy,
        coordinator=coordinator,
    )

    for i, (msg, expect) in enumerate(CASES):
        sid = f"verify-{i}"
        reply = await loop.handle(
            session_id=sid, user_id="u1", message=msg, trace_id=f"verify-{i}"
        )
        ctx = await coordinator.queue.get_pack(sid)
        trig = ctx.trigger_type.value if ctx else "-"
        detail = ctx.trigger_detail[:80] if ctx else ""
        print(f"\n=== {msg} ===")
        print(f"预期: {expect}")
        print(f"intent={reply.intent} state={reply.state}")
        print(f"reply: {reply.reply[:150]}")
        print(f"trigger={trig} detail={detail}")

    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())

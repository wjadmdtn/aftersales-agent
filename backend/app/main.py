"""FastAPI 应用装配。

lifespan 里完成 Agent / 转人工 / 会话实时层的生产装配（FR-031）：
此前这些只在测试夹具里 configure，线上起来 /agent/chat 直接 500。
装配失败即启动失败——配置非法不该等到第一个请求才发现。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.faults.injector import FaultMiddleware
from app.observability.logging import configure_logging
from app.observability.trace import TraceMiddleware

logger = logging.getLogger(__name__)


def _build_llm():
    """无 LLM 配置时用带演示响应表的桩。

    StubLLMClient 默认返回 GENERAL，未登记的句子会全部走澄清，
    本地演示与 E2E 必须先登记（plan Q7）。
    """
    from app.agent.llm.stub import StubLLMClient
    from app.models.agent import Intent, IntentMode, IntentResult

    if not settings.llm_base_url:
        table = {
            phrase: IntentResult(
                intent=Intent(spec["intent"]),
                mode=IntentMode(spec["mode"]),
                confidence=1.0,
            )
            for phrase, spec in settings.demo_stub_responses.items()
        }
        return StubLLMClient(table)

    from app.agent.llm.openai_compat import OpenAICompatLLMClient

    return OpenAICompatLLMClient()


def _build_policy(redis):
    """政策问答装配。任何环节不可用都不阻塞启动——它只是附加能力。"""
    if not settings.policy_enabled:
        return None
    try:
        from app.policy.embedder import build_embedder
        from app.policy.retriever import Retriever
        from app.policy.seed import load_policies
        from app.policy.store import InMemoryClauseStore
        from app.services.policy import PolicyService

        embedder = build_embedder(settings.rag_embedder, settings.rag_embedding_dim)
        store = InMemoryClauseStore(load_policies(settings.policy_seed_path), embedder)
        retriever = Retriever(
            store=store,
            embedder=embedder,
            similarity_threshold=settings.rag_similarity_threshold,
            ambiguity_margin=settings.rag_ambiguity_margin,
            top_k=settings.rag_recall_top_k,
        )
        return PolicyService(
            retriever=retriever,
            llm=_policy_llm(),
            timeout_ms=settings.rag_timeout_ms,
            enabled=True,
        )
    except Exception:
        logger.warning("policy module unavailable, continuing without it", exc_info=True)
        return None


def _policy_llm():
    from app.agent.llm.stub import StubLLMClient

    if not settings.llm_base_url:
        return StubLLMClient()
    from app.agent.llm.openai_compat import OpenAICompatLLMClient

    return OpenAICompatLLMClient()


async def _on_handoff(session_id: str, outcome_state) -> None:
    """Agent 内部触发转人工后，把状态同步到 ChatState 并广播。

    此前只有"快捷转人工"走广播，走 AgentLoop 的意图转人工（投诉等）
    用户端看不到状态变化——这是真实缺陷，不只是测试问题。
    """
    from app.chat.broadcast import broadcast_queue_update, broadcast_state
    from app.chat.runtime import get_chat

    rt = get_chat()
    try:
        await rt.registry.set_state(session_id, outcome_state)
    except KeyError:
        await rt.registry.ensure(session_id, user_id="unknown")
        await rt.registry.set_state(session_id, outcome_state)
    await broadcast_state(session_id)
    await broadcast_queue_update(added=[session_id])


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.agent.intent import IntentClassifier
    from app.agent.session import SessionStore
    from app.agent.tools.client import MockServiceClient
    from app.api import agent_routes, chat_routes, handoff_routes, ws_routes
    from app.chat.hub import Hub
    from app.chat.messages import MessageStore
    from app.chat.redis import make_redis
    from app.chat.runtime import ChatRuntime, configure_chat, reset_chat
    from app.chat.session_registry import SessionRegistry
    from app.handoff.coordinator import HandoffCoordinator
    from app.handoff.queue import HandoffQueue

    redis = make_redis()
    sessions = SessionStore(redis)
    coordinator = HandoffCoordinator(HandoffQueue(redis))
    llm = _build_llm()

    agent_routes.configure_agent(
        classifier=IntentClassifier(llm, settings.keyword_hints),
        sessions=sessions,
        tools=MockServiceClient(settings.mock_service_base_url),
        coordinator=coordinator,
        policy=_build_policy(redis),
        on_handoff=_on_handoff,
    )
    handoff_routes.configure_handoff(coordinator)

    ttl = settings.chat_message_ttl_seconds
    configure_chat(
        ChatRuntime(
            hub=Hub(),
            messages=MessageStore(redis, ttl_seconds=ttl),
            registry=SessionRegistry(redis, ttl_seconds=ttl),
        )
    )
    logger.info("chat runtime ready (ws endpoint + session query)")

    try:
        yield
    finally:
        from app.chat.runtime import get_chat

        try:
            await get_chat().close()
        except RuntimeError:
            pass
        await redis.aclose()
        reset_chat()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)

    app = FastAPI(title="Aftersales Mock Service", version="0.1.0", lifespan=lifespan)

    # 中间件顺序：后注册的先执行，因此 Trace 必须在 Fault 之后注册
    app.add_middleware(FaultMiddleware)
    app.add_middleware(TraceMiddleware)

    from app.api.routes import router

    app.include_router(router)

    from app.api.agent_routes import router as agent_router

    app.include_router(agent_router)

    from app.api.handoff_routes import router as handoff_router

    app.include_router(handoff_router)

    from app.api.chat_routes import router as chat_router

    app.include_router(chat_router)

    from app.api.ws_routes import router as ws_router

    app.include_router(ws_router)

    _mount_static(app)
    return app


def _mount_static(app: FastAPI) -> None:
    """前端构建产物存在才挂载（FR-022：后端不依赖前端构建即可启动）。

    默认配置 `app/static` 是相对 backend 根目录的，因此按 app 包父目录解析；
    绝对路径原样使用。找不到就只是不挂载，不猜别的位置。
    """
    configured = Path(settings.frontend_static_dir)
    dist = configured if configured.is_absolute() else (
        Path(__file__).resolve().parent.parent / configured
    )
    if dist.is_dir() and (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")
        logger.info("static ui mounted from %s", dist)


app = create_app()

"""T018: 政策问答编排。

对 Agent 暴露一个入口，内部串起 retriever + answerer，并保证：
- 任何异常（PG 不可用、embedder 报错、超时）一律视为 miss，不抛出到用户侧
- 降级期不得调用本模块（生成阶段最容易产生幻觉，由调用方把关）
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.agent.llm.base import PolicyLLM
from app.policy.answerer import PolicyAnswerer
from app.policy.models import PolicyAnswer, RetrievalDecision, RetrievalResult
from app.policy.retriever import Retriever

logger = logging.getLogger(__name__)


class PolicyService:
    def __init__(
        self,
        *,
        retriever: Retriever,
        llm: PolicyLLM,
        timeout_ms: int,
        enabled: bool = True,
    ):
        self.retriever = retriever
        self.answerer = PolicyAnswerer(llm)
        self.timeout_ms = timeout_ms
        self.enabled = enabled

    async def answer(
        self,
        *,
        question: str,
        order_created_at: datetime | None,
        product_type: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[PolicyAnswer, RetrievalResult | None]:
        if not self.enabled:
            return _miss("policy disabled"), None

        try:
            return await asyncio.wait_for(
                self._answer(question, order_created_at, product_type),
                timeout=self.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            logger.warning("policy retrieval timeout trace_id=%s", trace_id)
            return _miss("timeout"), None
        except Exception as e:  # 检索/存储异常不得阻塞主链路
            logger.warning("policy retrieval failed: %s trace_id=%s", e, trace_id)
            return _miss(f"error: {type(e).__name__}"), None

    async def _answer(
        self, question: str, order_created_at: datetime | None, product_type: str | None
    ) -> tuple[PolicyAnswer, RetrievalResult]:
        retrieval = self.retriever.retrieve(
            query=question,
            order_created_at=order_created_at,
            product_type=product_type,
        )
        if retrieval.decision is RetrievalDecision.MISS:
            return _miss(retrieval.reason), retrieval

        answer = await self.answerer.answer(user_input=question, retrieval=retrieval)
        return answer, retrieval


def _miss(reason: str) -> PolicyAnswer:
    return PolicyAnswer(decision=RetrievalDecision.MISS, answer="", rejection=reason)


def build_policy_service(llm: PolicyLLM, cfg=None) -> PolicyService:
    """按配置装配：无 PG 时用内存存储（CI / 本地无库），有 PG 时走 pgvector。"""
    from app.config import settings as _settings
    from app.policy.embedder import build_embedder
    from app.policy.seed import load_policies
    from app.policy.store import ClauseStore, InMemoryClauseStore, PgClauseStore

    cfg = cfg or _settings
    embedder = build_embedder(cfg.rag_embedder, cfg.rag_embedding_dim)
    clauses = load_policies(cfg.policy_seed_path)

    store: ClauseStore
    pg_dsn = getattr(cfg, "pg_dsn", "")
    if pg_dsn:
        pg_store = PgClauseStore(pg_dsn, cfg.rag_embedding_dim)
        pg_store.ensure_schema()
        pg_store.upsert(clauses, embedder)
        store = pg_store
    else:
        logger.info("pg_dsn not configured, using in-memory clause store")
        store = InMemoryClauseStore(clauses, embedder)

    retriever = Retriever(
        store=store,
        embedder=embedder,
        similarity_threshold=cfg.rag_similarity_threshold,
        ambiguity_margin=cfg.rag_ambiguity_margin,
        top_k=cfg.rag_recall_top_k,
    )
    return PolicyService(
        retriever=retriever,
        llm=llm,
        timeout_ms=cfg.rag_timeout_ms,
        enabled=cfg.policy_enabled,
    )

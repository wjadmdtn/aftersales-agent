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
        reranker=None,
    ):
        self.retriever = retriever
        self.answerer = PolicyAnswerer(llm)
        self.timeout_ms = timeout_ms
        self.enabled = enabled
        # 两步：检索算出"像不像"，rerank 判断"能不能回答"。
        # 不开时传 None，行为与 005 一致。
        self.reranker = reranker

    async def answer(
        self,
        *,
        question: str,
        order_created_at: datetime | None,
        product_type: str | None = None,
        trace_id: str | None = None,
        search_query: str | None = None,
    ) -> tuple[PolicyAnswer, RetrievalResult | None]:
        """search_query：意图识别顺带产出的改写查询，只喂给检索层；
        rerank 与生成仍以 question（用户原话）为准——改写可能丢口语细节。"""
        if not self.enabled:
            return _miss("policy disabled"), None

        try:
            return await asyncio.wait_for(
                self._answer(question, order_created_at, product_type, search_query),
                timeout=self.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            logger.warning("policy retrieval timeout trace_id=%s", trace_id)
            return _miss("timeout"), None
        except Exception as e:  # 检索/存储异常不得阻塞主链路
            logger.warning("policy retrieval failed: %s trace_id=%s", e, trace_id)
            return _miss(f"error: {type(e).__name__}"), None

    async def _answer(
        self,
        question: str,
        order_created_at: datetime | None,
        product_type: str | None,
        search_query: str | None = None,
    ) -> tuple[PolicyAnswer, RetrievalResult]:
        retrieval = self.retriever.retrieve(
            query=(search_query or "").strip() or question,
            order_created_at=order_created_at,
            product_type=product_type,
        )
        # 改写检索 miss 且原话不同时，用原话再查一次（纯本地计算，不花 LLM 调用）。
        # 改写是增强不是替换：它可能丢口语细节（实测改写后 0.633 检索层 miss，
        # 原话 0.659 能进 rerank），miss 回退保住基线召回。
        rewritten = (search_query or "").strip()
        if (
            retrieval.decision is RetrievalDecision.MISS
            and rewritten
            and rewritten != question
        ):
            retrieval = self.retriever.retrieve(
                query=question,
                order_created_at=order_created_at,
                product_type=product_type,
            )
        if retrieval.decision is RetrievalDecision.MISS:
            return _miss(retrieval.reason), retrieval

        if self.reranker is not None:
            retrieval = await self.reranker.apply(query=question, retrieval=retrieval)
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
    from app.policy.keyword import KeywordIndex
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
        # 关键词召回：例外条款（如「吊牌被摘剪」）只写在这，embedding 刻意排除了它
        keyword_index=(
            KeywordIndex(clauses) if getattr(cfg, "rag_keyword_enabled", False) else None
        ),
    )
    reranker = None
    if getattr(cfg, "rag_rerank_enabled", False):
        from app.policy.rerank import LLMReranker

        # rerank 需要模型具备 rerank_policy 能力；桩模型不实现，直接跳过
        if hasattr(llm, "rerank_policy"):
            reranker = LLMReranker(
                llm,
                keep_threshold=cfg.rag_rerank_keep_threshold,
                top_n=cfg.rag_rerank_top_n,
            )
        else:
            logger.info("llm has no rerank_policy, rerank disabled")

    return PolicyService(
        retriever=retriever,
        llm=llm,
        timeout_ms=cfg.rag_timeout_ms,
        enabled=cfg.policy_enabled,
        reranker=reranker,
    )

"""T009: 检索编排。召回 → 置信判定 → 歧义判定。纯逻辑，便于单测。

三条判定，顺序不可换：
1. 无候选           → miss（不生成）
2. top1 < 阈值      → miss（不生成，由 check_boundary 触发转人工）
3. top1-top2 < margin → ambiguous（追问，不硬答）
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from app.policy.embedder import Embedder
from app.policy.models import RetrievalDecision, RetrievalResult
from app.policy.store import ClauseStore

logger = logging.getLogger(__name__)


class Retriever:
    def __init__(
        self,
        *,
        store: ClauseStore,
        embedder: Embedder,
        similarity_threshold: float,
        ambiguity_margin: float,
        top_k: int,
    ):
        self.store = store
        self.embedder = embedder
        self.threshold = similarity_threshold
        self.margin = ambiguity_margin
        self.top_k = top_k

    def retrieve(
        self,
        *,
        query: str,
        order_created_at: datetime | date | None,
        product_type: str | None = None,
        categories: list[str] | None = None,
    ) -> RetrievalResult:
        order_date, used_default = _resolve_date(order_created_at)
        if used_default:
            logger.info("policy retrieval: order date missing, defaulting to today")

        vector = self.embedder.embed([query])[0]
        candidates = self.store.search(
            query_vector=vector,
            categories=categories,
            product_type=product_type,
            order_date=order_date,
            limit=self.top_k,
        )

        result = RetrievalResult(
            query=query,
            order_date=order_date,
            product_type=product_type,
            candidates=candidates,
            used_default_date=used_default,
        )

        if not candidates:
            result.decision = RetrievalDecision.MISS
            result.reason = "无候选条款（版本/品类过滤后为空）"
            return result

        top1 = candidates[0].similarity
        if top1 < self.threshold:
            result.decision = RetrievalDecision.MISS
            result.reason = f"top1 {top1:.3f} < 阈值 {self.threshold}"
            return result

        if len(candidates) >= 2:
            gap = top1 - candidates[1].similarity
            if gap < self.margin:
                result.decision = RetrievalDecision.AMBIGUOUS
                result.reason = f"top1-top2 分差 {gap:.3f} < margin {self.margin}"
                return result

        result.decision = RetrievalDecision.HIT
        result.reason = f"top1 {top1:.3f} >= 阈值 {self.threshold}"
        return result


def _resolve_date(order_created_at: datetime | date | None) -> tuple[date, bool]:
    if order_created_at is None:
        return datetime.now(timezone.utc).date(), True
    if isinstance(order_created_at, datetime):
        return order_created_at.date(), False
    return order_created_at, False

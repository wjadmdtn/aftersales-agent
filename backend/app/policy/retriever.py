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
        keyword_index=None,
        keyword_top_k: int = 5,
    ):
        self.store = store
        self.embedder = embedder
        self.threshold = similarity_threshold
        self.margin = ambiguity_margin
        self.top_k = top_k
        # 关键词召回是可选的：关掉即退回纯向量，行为与 005 完全一致
        self.keyword_index = keyword_index
        self.keyword_top_k = keyword_top_k

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
        # 向量召回的结果带上 vector_similarity，供阈值判断（不受融合影响）
        for c in candidates:
            c.vector_similarity = c.similarity
            c.source = "vector"

        if self.keyword_index is not None:
            # 关键词必须能在“向量没召回到”的条款里找——否则救不了 miss。
            # 但品类/生效期的硬过滤不能绕过，所以只在可见条款内搜。
            visible = self.store.visible_ids(
                categories=categories, product_type=product_type, order_date=order_date
            )
            kw = self.keyword_index.search(
                query, limit=self.keyword_top_k, candidates=visible
            )
            if kw:
                from app.policy.keyword import rrf_fuse

                candidate_ids = {c.clause.clause_id for c in candidates}
                missing = [k for k in kw if k.clause.clause_id not in candidate_ids]
                if missing:
                    # 补算向量分：阈值/歧义判定一律看余弦，不能因为"关键词命中了"
                    # 就当成高置信——那等于绕开置信控制。
                    from app.policy.embedder import cosine

                    for k in missing:
                        vec = self.embedder.embed([k.clause.embed_text])[0]
                        k.vector_similarity = cosine(vector, vec)
                        k.similarity = k.vector_similarity
                candidates = rrf_fuse([candidates, kw])

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

        # 置信判定看候选集里的"向量最高分"，而排序可以因关键词命中而调整——
        # 两件事必须分开：排序只决定"谁先进 rerank"，置信只看向量分本身。
        # 早退检查若锚在融合排序的第一位，会把过阈值的候选挡在门外
        # （实测「能合并订单发货吗」：融合 top1 0.615 < 0.64 判死，
        # 但候选里有 0.659 过阈值，本该进 rerank 拒答转人工）。
        best_vec_item = max(
            (c for c in candidates if c.vector_similarity is not None),
            key=lambda c: c.vector_similarity or 0.0,
            default=None,
        )
        if best_vec_item is None:
            result.decision = RetrievalDecision.MISS
            result.reason = "仅关键词召回，无向量置信度"
            return result
        top1_vec = best_vec_item.vector_similarity or 0.0
        if top1_vec < self.threshold:
            result.decision = RetrievalDecision.MISS
            result.reason = f"最高向量分 {top1_vec:.3f} < 阈值 {self.threshold}"
            return result

        # 排序上把可信度最高的放前面：融合只影响候选集的"是否考虑"，
        # 第一位必须是向量分最高的条款，否则会答非所问。
        if candidates[0] is not best_vec_item:
            rest = [c for c in candidates if c is not best_vec_item]
            candidates = [best_vec_item] + rest

        if len(candidates) >= 2:
            second = self._vector_score(candidates[1])
            if second is not None:
                gap = top1_vec - second
                if gap < self.margin:
                    result.decision = RetrievalDecision.AMBIGUOUS
                    result.reason = f"top1-top2 分差 {gap:.3f} < margin {self.margin}"
                    result.candidates = candidates
                    return result

        result.candidates = candidates
        result.decision = RetrievalDecision.HIT
        result.reason = f"top1 {top1_vec:.3f} >= 阈值 {self.threshold}"
        return result

    @staticmethod
    def _vector_score(item) -> float | None:
        return item.vector_similarity if item.vector_similarity is not None else None


def _resolve_date(order_created_at: datetime | date | None) -> tuple[date, bool]:
    if order_created_at is None:
        return datetime.now(timezone.utc).date(), True
    if isinstance(order_created_at, datetime):
        return order_created_at.date(), False
    return order_created_at, False

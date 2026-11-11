"""Rerank：检索后用模型复核"这条条款真的能回答这个问题吗"。

为什么必须有：
向量检索是双塔结构——问题和条款各自独立编码，只能判断"字面上像不像"。
它分不出「修改收货地址」能不能回答「能合并订单发货吗」，因为两者都含
"订单"这个词。实测该问题拿到 0.742 高分并被直接生成答案，属于答非所问。

rerank 把（问题 + 条款）**一起**给模型判断，是交叉编码，才能判断语义相关性。

与宪法一致的三条约束：
1. 只能降级：模型判不相关 → 剔除该条款；判相关也不把 miss 升级为 hit
2. 失败不丢答案：超时/报错退回原向量排序，只记日志
3. 全被剔除 → miss，交回澄清，绝不硬答
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.policy.models import PolicyClause, RetrievalDecision, RetrievalResult, RetrievedClause

logger = logging.getLogger(__name__)


@runtime_checkable
class RerankLLM(Protocol):
    async def rerank_policy(
        self, *, query: str, clauses: list[PolicyClause]
    ) -> dict[str, float]:
        """返回 clause_id → 相关性分（0~1）。实现方不得自行剔除条款。"""
        ...


class Reranker(Protocol):
    async def apply(self, *, query: str, retrieval: RetrievalResult) -> RetrievalResult: ...


class NoopReranker:
    """不开 rerank 时的直通实现，行为与 005 完全一致。"""

    async def apply(self, *, query: str, retrieval: RetrievalResult) -> RetrievalResult:
        return retrieval


class LLMReranker:
    def __init__(self, llm: RerankLLM, *, keep_threshold: float, top_n: int):
        self.llm = llm
        self.keep_threshold = keep_threshold
        self.top_n = top_n

    async def apply(self, *, query: str, retrieval: RetrievalResult) -> RetrievalResult:
        # 已经是 miss：没有后续步骤会用这些候选，不必再花一次模型调用
        if retrieval.decision is RetrievalDecision.MISS or not retrieval.candidates:
            return retrieval

        clauses = [c.clause for c in retrieval.candidates]
        try:
            scores = await self.llm.rerank_policy(query=query, clauses=clauses)
        except Exception as e:
            # rerank 只是增强，不能因为它挂了就不答——退回向量排序
            logger.warning(
                "rerank failed, fallback to vector order: %s: %s",
                type(e).__name__, str(e)[:120],
            )
            return retrieval

        kept = [
            (c, scores.get(c.clause.clause_id, 0.0))
            for c in retrieval.candidates
            if scores.get(c.clause.clause_id, 0.0) >= self.keep_threshold
        ]
        if not kept:
            return retrieval.model_copy(
                update={
                    "candidates": [],
                    "decision": RetrievalDecision.MISS,
                    # 知识缺口的显式标志：主循环据此直接转人工，不走追问
                    "rerank_rejected": True,
                    "reason": (
                        f"rerank 判为不相关（阈值 {self.keep_threshold}）："
                        f"{[c.clause.clause_id for c in retrieval.candidates]}"
                    ),
                }
            )

        kept.sort(key=lambda kv: kv[1], reverse=True)
        kept = kept[: self.top_n]
        new_candidates = [
            c.model_copy(update={"similarity": s, "fusion_score": s, "source": "rerank"})
            for c, s in kept
        ]

        # AMBIGUOUS 保留原判定：追问是更保守的行为，rerank 分高不代表不歧义
        decision = retrieval.decision
        reason = f"rerank 保留 {len(new_candidates)} 条（原 {len(retrieval.candidates)} 条）"
        return retrieval.model_copy(
            update={"candidates": new_candidates, "decision": decision, "reason": reason}
        )

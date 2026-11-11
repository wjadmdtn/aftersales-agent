"""T002: 政策条款领域模型。

条款是知识的最小单元——不做文档切片，一条一个完整意思。
exceptions 单独成字段：不参与 embedding（语义偏离正文），但必须参与生成。
"""
from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class RetrievalDecision(StrEnum):
    HIT = "hit"
    AMBIGUOUS = "ambiguous"
    MISS = "miss"


class PolicyClause(BaseModel):
    clause_id: str
    policy_id: str
    version: int
    effective_from: date
    effective_to: date | None = None
    category: str
    title: str
    body: str
    exceptions: list[str] = Field(default_factory=list)
    applies_to: dict = Field(default_factory=dict)

    @property
    def embed_text(self) -> str:
        """只对 title + body 做向量化。例外语义偏离正文，拉进去会降低召回精度。"""
        return f"{self.title}\n{self.body}"

    @property
    def product_types(self) -> list[str]:
        return list(self.applies_to.get("product_types") or ["*"])

    def effective_at(self, on: date) -> bool:
        return self.effective_from <= on and (self.effective_to is None or on <= self.effective_to)


class RetrievedClause(BaseModel):
    clause: PolicyClause
    similarity: float
    # 融合召回：similarity 保持为向量余弦（阈值判断用），fusion_score 仅用于排序。
    # 只被关键词召回时 vector_similarity 为 None——不能拿 RRF 分冒充余弦分。
    vector_similarity: float | None = None
    fusion_score: float = 0.0
    source: str = "vector"


class RetrievalResult(BaseModel):
    query: str
    order_date: date
    product_type: str | None = None
    candidates: list[RetrievedClause] = Field(default_factory=list)
    decision: RetrievalDecision = RetrievalDecision.MISS
    reason: str = ""
    used_default_date: bool = False
    # rerank 全剔除时置 True：这是「模型确认库里没有能回答的条款」（知识缺口），
    # 与「检索层分数不够」（可能只是表述问题）是两种不同的 miss。
    # 主循环只对前者直接转人工，后者仍走澄清。
    rerank_rejected: bool = False

    @property
    def top_similarity(self) -> float | None:
        return self.candidates[0].similarity if self.candidates else None

    @property
    def clause_ids(self) -> set[str]:
        return {c.clause.clause_id for c in self.candidates}


class PolicyAnswer(BaseModel):
    decision: RetrievalDecision
    answer: str = ""
    cited_clause_ids: list[str] = Field(default_factory=list)
    handled_exceptions: list[str] = Field(default_factory=list)
    ambiguity_question: str | None = None
    rejection: str | None = Field(
        default=None, description="非空表示回答被门禁拒绝，调用方应降级为 miss"
    )

"""T013/T014: 基于召回条款生成结构化回答，并施加硬门禁。

门禁不向模型"重试"——重试会让模型学会编一个看起来合理的 clause_id。
任一门禁不通过，直接作废回答并降级为 miss。
"""
from __future__ import annotations

from app.agent.llm.base import LLMError, PolicyLLM
from app.policy.models import PolicyAnswer, RetrievalDecision, RetrievalResult


class PolicyAnswerer:
    def __init__(self, llm: PolicyLLM):
        self.llm = llm

    async def answer(
        self, *, user_input: str, retrieval: RetrievalResult
    ) -> PolicyAnswer:
        if retrieval.decision is RetrievalDecision.MISS:
            return PolicyAnswer(decision=RetrievalDecision.MISS, answer="")

        try:
            raw = await self.llm.answer_policy(user_input=user_input, retrieval=retrieval)
        except LLMError:
            # 生成不可用时不得用模板拼政策回答——那等于绕过幻觉防线
            return PolicyAnswer(decision=RetrievalDecision.MISS, answer="", rejection="llm_error")

        return self._gate(raw, retrieval)

    # ---------- 门禁 ----------

    def _gate(self, ans: PolicyAnswer, retrieval: RetrievalResult) -> PolicyAnswer:
        # 模型只能降级，不能升级
        if ans.decision is RetrievalDecision.MISS:
            return PolicyAnswer(decision=RetrievalDecision.MISS, answer="")
        if retrieval.decision is RetrievalDecision.AMBIGUOUS and ans.decision is RetrievalDecision.HIT:
            ans = ans.model_copy(update={"decision": RetrievalDecision.AMBIGUOUS})

        if ans.decision is RetrievalDecision.AMBIGUOUS:
            return ans

        rejected = self._validate_hit(ans, retrieval)
        if rejected is not None:
            return PolicyAnswer(
                decision=RetrievalDecision.MISS, answer="", rejection=rejected
            )
        return ans

    def _validate_hit(self, ans: PolicyAnswer, retrieval: RetrievalResult) -> str | None:
        recalled = retrieval.clause_ids

        if not ans.cited_clause_ids:
            return "回答未引用任何条款"

        unknown = set(ans.cited_clause_ids) - recalled
        if unknown:
            # 引用了本次没有召回的条款 = 幻觉
            return f"引用了未召回的条款: {sorted(unknown)}"

        if not ans.answer.strip():
            return "回答为空"

        recalled_with_exceptions = {
            c.clause.clause_id for c in retrieval.candidates if c.clause.exceptions
        }
        cited_with_exceptions = recalled_with_exceptions & set(ans.cited_clause_ids)
        if cited_with_exceptions and not ans.handled_exceptions:
            return f"引用条款含例外但未处理: {sorted(cited_with_exceptions)}"

        return None

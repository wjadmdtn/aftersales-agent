"""T006: 测试桩。

fail_times 用来验证 FR-016b：上次失败后下次请求仍会调用 LLM，不会被跳过。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Literal

from app.agent.llm.base import LLMError
from app.models.agent import Intent, IntentResult
from app.policy.models import PolicyAnswer, RetrievalDecision, RetrievalResult

FailMode = Literal["timeout", "error", "invalid"] | None


class StubLLMClient:
    def __init__(
        self,
        responses: dict[str, IntentResult] | None = None,
        *,
        default: IntentResult | None = None,
        fail_mode: FailMode = None,
        fail_times: int = 0,
        delay_ms: int = 0,
        classifier: Callable[[str], IntentResult] | None = None,
        policy_responses: dict[str, PolicyAnswer] | None = None,
        policy_default: PolicyAnswer | None = None,
    ):
        self.responses = responses or {}
        self.default = default or IntentResult(intent=Intent.GENERAL, confidence=0.5)
        self.fail_mode = fail_mode
        self.fail_times = fail_times
        self.delay_ms = delay_ms
        self.classifier = classifier
        self.policy_responses = policy_responses or {}
        self.policy_default = policy_default
        self.call_count = 0
        self.policy_call_count = 0
        self.last_hints: list[str] = []

    async def classify(
        self,
        *,
        user_input: str,
        hint_keywords: list[str],
        context: dict[str, Any],
    ) -> IntentResult:
        self.call_count += 1
        self.last_hints = hint_keywords

        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)

        should_fail = self.fail_mode is not None and (
            self.fail_times == 0 or self.call_count <= self.fail_times
        )
        if should_fail:
            if self.fail_mode == "timeout":
                raise LLMError("llm timeout (stub)")
            if self.fail_mode == "error":
                raise LLMError("llm error (stub)")
            raise LLMError("llm returned invalid structure (stub)")

        if self.classifier is not None:
            return self.classifier(user_input)
        return self.responses.get(user_input, self.default)

    async def answer_policy(
        self, *, user_input: str, retrieval: RetrievalResult
    ) -> PolicyAnswer:
        self.policy_call_count += 1

        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)

        if self.fail_mode is not None:
            raise LLMError("llm unavailable (stub, policy)")

        if user_input in self.policy_responses:
            return self.policy_responses[user_input]

        if self.policy_default is not None:
            return self.policy_default

        # 默认行为：引用 top1、把它的例外全部当作已处理
        top = retrieval.candidates[0].clause if retrieval.candidates else None
        if top is None:
            return PolicyAnswer(decision=RetrievalDecision.MISS)
        return PolicyAnswer(
            decision=retrieval.decision,
            answer=f"根据《{top.title}》：{top.body}",
            cited_clause_ids=[top.clause_id],
            handled_exceptions=list(top.exceptions),
        )

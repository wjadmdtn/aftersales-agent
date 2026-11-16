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
        invalid_times: int = 0,
    ):
        self.responses = responses or {}
        # 每条消息的前 N 次调用返回结构非法（供重放链路测试）
        self.invalid_times = invalid_times
        self._invalid_seen: dict[str, int] = {}
        self.default = default or IntentResult(intent=Intent.UNKNOWN, confidence=0.5)
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
            # 结构非法必须带 schema 标记：上层据此走"标记耗尽→转人工"而不是
            # "盲重试三次→超时计数"，两者是不同故障
            raise LLMError("llm returned invalid structure (stub)", schema=True)

        # invalid_times 按"用户消息"计，而不是按调用次数计：
        # 同一条消息的第 1 次返回非法（触发重放），第 2 次起返回合法。
        # 连续 N 条消息都非法，用它验证耗尽路径。
        key = user_input
        seen = self._invalid_seen.get(key, 0) + 1
        self._invalid_seen[key] = seen
        if self.invalid_times and seen <= self.invalid_times:
            raise LLMError("llm returned invalid structure (stub, replayable)", schema=True)

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

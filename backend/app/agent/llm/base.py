"""T005: LLM 客户端协议。

意图判断必须由实现方（模型）完成，关键词只作为提示传入。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.models.agent import IntentResult


class LLMError(RuntimeError):
    """LLM 调用失败：超时、报错、返回不合法。触发本次请求降级。"""


@runtime_checkable
class LLMClient(Protocol):
    async def classify(
        self,
        *,
        user_input: str,
        hint_keywords: list[str],
        context: dict[str, Any],
    ) -> IntentResult:
        """判断用户真实诉求。

        hint_keywords 仅作提示，实现方不得据此直接决定意图——
        同样的词在不同语境下含义相反（威胁 / 咨询 / 历史陈述 / 诉求）。
        """
        ...

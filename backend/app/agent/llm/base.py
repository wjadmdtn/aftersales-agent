"""T005: LLM 客户端协议。

意图判断必须由实现方（模型）完成，关键词只作为提示传入。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.models.agent import IntentResult
from app.policy.models import PolicyAnswer, RetrievalResult


class LLMError(RuntimeError):
    """LLM 调用失败：超时、报错、返回不合法。触发本次请求降级。

    schema=True 表示失败原因是"输出结构不合法"而不是网络/超时。
    上层据此区分：结构失败要标记 schema_retries_exhausted（走 SYSTEM_ERROR 转人工），
    网络失败要累加 llm_timeout_streak。两者处置不同，混在一起会把模型抽风
    算成系统故障。
    """

    def __init__(
        self, message: str = "", *, schema: bool = False, status: int | None = None
    ):
        super().__init__(message)
        self.schema = schema
        # HTTP 状态码：429 要按限流退避，不能和普通网络抖动用同一套短重试
        self.status = status


@runtime_checkable
class IntentLLM(Protocol):
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


@runtime_checkable
class PolicyLLM(Protocol):
    async def answer_policy(
        self,
        *,
        user_input: str,
        retrieval: RetrievalResult,
    ) -> PolicyAnswer:
        """基于召回条款生成结构化回答。

        实现方只能把决策降级（hit → ambiguous/miss），不得把 miss 升级为 hit：
        否则"检索不到就不答"的边界会被模型的必答倾向吃掉。
        """
        ...


# 兼容既有导入
LLMClient = IntentLLM

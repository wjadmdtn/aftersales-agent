"""T012: 意图识别。

关键词只作为提示传给 LLM，**没有任何分支**据此决定意图。
同样的词在不同语境下含义相反：

    我要退款                    → 诉求
    再不处理我就投诉你们不肯退款  → 威胁
    这个能退款吗                → 咨询
    上次退款拖了半个月           → 历史陈述
    别再发退款短信了             → 反向表达

判断必须由模型完成。这个函数在结构上就不留规则决策的位置。
"""
from __future__ import annotations

from typing import Any

from app.agent.llm.base import LLMClient, LLMError
from app.config import settings
from app.models.agent import Intent, IntentResult, IntentSource


def collect_hints(user_input: str, keyword_map: dict[str, list[str]] | None = None) -> list[str]:
    """只收集命中的词，作为提示交给 LLM。返回值不参与任何意图判定。"""
    km = keyword_map if keyword_map is not None else settings.keyword_hints
    hits: list[str] = []
    for words in km.values():
        for w in words:
            if w in user_input:
                hits.append(w)
    return list(dict.fromkeys(hits))


class IntentClassifier:
    def __init__(self, llm: LLMClient, keyword_map: dict[str, list[str]] | None = None):
        self.llm = llm
        self.keyword_map = keyword_map if keyword_map is not None else settings.keyword_hints

    async def classify(
        self, user_input: str, *, context: dict[str, Any] | None = None
    ) -> tuple[IntentResult | None, LLMError | None]:
        """返回 (结果, 错误)。错误非空表示 LLM 不可用，调用方须走降级。"""
        hints = collect_hints(user_input, self.keyword_map)
        try:
            result = await self.llm.classify(
                user_input=user_input,
                hint_keywords=hints,
                context=context or {},
            )
        except LLMError as e:
            return None, e
        return result.model_copy(update={"source": IntentSource.LLM}), None

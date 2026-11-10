"""T007: 真实 LLM 客户端（OpenAI 兼容接口）。

关键词以提示形式随请求发出，但最终判断由模型完成。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.agent.llm.base import LLMError
from app.config import settings
from app.models.agent import Intent, IntentMode, IntentResult
from app.policy.models import PolicyAnswer, RetrievalDecision, RetrievalResult

_POLICY_SYSTEM_PROMPT = """你是客服政策问答助手。只能依据下面提供的【条款】回答，不得使用常识、
行业惯例或任何未列出的信息。只输出 JSON。

规则：
1. cited_clause_ids 只能取自条款列表里的 clause_id，**不得编造**。没有能回答的条款就给 miss。
2. decision 判定：
   - hit：某一条款明确回答了问题
   - ambiguous：多条条款都相关且分不出主次（如"能换吗"——退货与换货都相关）
   - miss：没有条款能回答，或只沾边
3. 引用的条款若有 exceptions，必须在回答中说明例外，并逐条写入 handled_exceptions。
   没有写进 handled_exceptions 的例外，视为没有处理。
4. 不得给出条款之外的承诺、金额、时效。
5. 回答要简短、口语化、直接回应用户问题。

输出格式：
{"decision":"hit|ambiguous|miss","answer":"...","cited_clause_ids":["..."],
 "handled_exceptions":["..."],"ambiguity_question":"..."}"""


def _format_clauses(retrieval: RetrievalResult) -> str:
    lines: list[str] = []
    for r in retrieval.candidates:
        c = r.clause
        block = f"[{c.clause_id}] {c.title}\n{c.body}"
        if c.exceptions:
            block += "\n例外：" + "；".join(c.exceptions)
        lines.append(block)
    return "\n\n".join(lines)

_SYSTEM_PROMPT = """你是客服意图识别器。判断用户**真实诉求**，只输出 JSON。

意图只能是：refund(发起退款) / logistics(查物流) / change_address(改地址) /
query_order(查订单) / complaint(投诉) / manual(要人工) / general(其他)

**关键**：同样的词在不同语境下含义相反。必须区分：
- 诉求：用户希望系统为他执行某个操作
- 威胁："再不处理我就投诉你们不肯退款" —— 是投诉施压，不是要退款
- 咨询："这个能退款吗" —— 是问可行性，不是要退
- 历史："上次退款拖了半个月" —— 是抱怨过去，不是新诉求
- 反向："别再发退款短信了" —— 不是要退款
- 转述："我朋友说能退货" —— 不是他自己要退

**mode 必须同时判断**，它决定系统是执行还是只回答：

- execute：用户明确要求执行。特征含「我要/帮我/麻烦/请/给我/退了/退了吧」
  例："我要退款"、"帮我改地址"
- inquiry：用户在问能不能做。特征含「吗/能不能/可不可以/行不行/如何/是否」
  例："这个能退款吗"、"可以改地址吗"
- query：用户在查进度/状态，不是要执行
  例："退款到哪了"、"钱什么时候能回来"、"物流到哪了"
- need_context：意图明确是写操作，但缺少必要信息（如没说哪笔订单）
  例："退了吧"（会话里没有订单）

注意：
- 写操作（退款/改地址）判 execute 要格外谨慎，用户只是在问就判 inquiry
- 陈述问题但未提诉求（"东西摔碎了"）不属于任何业务意图，intent 给 general

输出格式：{"intent": "...", "mode": "...", "confidence": 0.0-1.0, "reason": "简短依据"}"""


class OpenAICompatLLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_ms: int | None = None,
    ):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout_ms = timeout_ms or settings.llm_timeout_ms
        if not self.base_url or not self.model:
            raise ValueError("LLM base_url and model must be configured")

    async def classify(
        self,
        *,
        user_input: str,
        hint_keywords: list[str],
        context: dict[str, Any],
    ) -> IntentResult:
        hint_block = ""
        if hint_keywords:
            hint_block = (
                f"\n\n（辅助提示：文本中出现这些词：{', '.join(hint_keywords)}。"
                f"注意它们可能出现在威胁、否定、咨询、历史陈述或转述中，"
                f"不能据此直接判定诉求。）"
            )
        ctx_block = ""
        if context:
            ctx_block = f"\n\n会话上下文：{json.dumps(context, ensure_ascii=False)}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_input + hint_block + ctx_block},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError) as e:
            raise LLMError(f"llm call failed: {e}") from e

        try:
            data = json.loads(content)
            intent = Intent(data["intent"])
            mode = IntentMode(data.get("mode", "execute"))
            confidence = float(data.get("confidence", 0.5))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise LLMError(f"llm returned invalid structure: {e}") from e

        return IntentResult(
            intent=intent,
            mode=mode,
            confidence=max(0.0, min(1.0, confidence)),
            reason=data.get("reason"),
        )

    async def answer_policy(
        self, *, user_input: str, retrieval: RetrievalResult
    ) -> PolicyAnswer:
        if not retrieval.candidates:
            return PolicyAnswer(decision=RetrievalDecision.MISS)

        user_block = (
            f"用户问题：{user_input}\n\n【条款】\n{_format_clauses(retrieval)}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _POLICY_SYSTEM_PROMPT},
                {"role": "user", "content": user_block},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_ms / 1000) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError) as e:
            raise LLMError(f"llm policy call failed: {e}") from e

        try:
            data = json.loads(content)
            decision = RetrievalDecision(data.get("decision", "miss"))
            return PolicyAnswer(
                decision=decision,
                answer=str(data.get("answer") or ""),
                cited_clause_ids=[str(x) for x in (data.get("cited_clause_ids") or [])],
                handled_exceptions=[str(x) for x in (data.get("handled_exceptions") or [])],
                ambiguity_question=data.get("ambiguity_question"),
            )
        except (json.JSONDecodeError, ValueError) as e:
            raise LLMError(f"llm policy returned invalid structure: {e}") from e

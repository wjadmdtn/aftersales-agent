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

def _parse_search_query(value) -> str | None:
    """模型返回的改写查询清洗：非字符串/空白一律视为没改写，绝不抛错。"""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


_SYSTEM_PROMPT = """你是客服意图识别器。判断用户真实诉求，只输出 JSON。

## 一、意图定义

| 意图 | 含义 | 例子 |
|---|---|---|
| refund | 发起退款 | 我要退款、帮我退一下款 |
| logistics | 查物流 | 快递到哪了、包裹在哪 |
| change_address | 改收货地址 | 我要改地址、地址填错了 |
| query_order | 查订单 | 查下订单、订单状态是什么 |
| exchange | 提出换货申请 | 我想换个货、帮我换个尺码 |
| manual | 要人工 | 转人工、找真人、要客服电话 |
| policy | 问规则或功能入口 | 能开发票吗、换货什么条件 |
| unknown | 诉求不明 | 太差了、你好、我要投诉你们 |

## 二、四条边界（最容易混）

### 1. 提申请 vs 问规则
- 祈使句（我要/帮我/我想换成）→ 对应业务意图：refund / change_address / exchange
- 疑问句（能…吗/可以…吗/什么条件）→ policy

  我要退款 → refund　｜　这个能退款吗 → policy
  帮我改地址 → change_address　｜　还没发货能改地址吗 → policy
  我想换个尺码 → exchange　｜　能换个尺码吗 → policy
  退款记录在哪看 → policy

### 2. 有诉求 vs 诉求不明
- 有明确可执行诉求 → 对应业务意图
- 以下一律 unknown：抱怨（太差了/很失望）、发泄、闲聊、转述（我朋友说…）、
  否定（别发了/不想…）、历史陈述（上次退款拖了半个月）、诉求不明（东西摔碎了）

### 3. 要人工 vs 投诉抱怨
- manual 只认一件事：用户明确要人（转人工/找真人/人工客服/叫个人来）
- 抱怨、扬言投诉、说要打 12315、差评——全部 unknown，不是 manual。
  系统不判断用户该不该转人工，用户想转自己会说。

### 4. change_address vs exchange
换地址 = change_address；换商品（尺码/颜色/型号）= exchange。

## 三、mode

| mode | 含义 | 例子 |
|---|---|---|
| execute | 要求执行 | 我要退款、帮我改地址 |
| inquiry | 问能不能做 | 这个能退款吗、可以改地址吗 |
| query | 查进度或状态 | 退款到哪了、物流到哪了、钱什么时候能回来、付了钱没收到货 |
| need_context | 要执行但缺信息 | 退了吧（会话里没有订单） |

- 写操作（refund / change_address）**判 execute 要格外谨慎**：用户只是在问就判 inquiry。
  判错会把"能不能退"变成"真的退"，代价是钱。
- intent 是 unknown 时 mode 不重要，给 execute 即可。

## 四、search_query

仅当 intent=policy 时输出：把用户口语改写成适合检索政策条款的查询词。
补全正式说法（"吊牌剪了"→"吊牌 商标标识 摘除 退货条件"），保留实体词，
空格分隔，不加标点。不得添加用户没提的信息；问题已规范则原样输出。
intent 不是 policy 时给 null。改写与意图判定同一次完成。

## 五、输出

{"intent": "...", "mode": "...", "confidence": 0.0-1.0, "reason": "简短依据", "search_query": "...|null"}
"""


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
            # 模型对 unknown 类输入常返回 mode: null（它认为没有可执行的模式）。
            # get(..., default) 对显式 null 不生效，必须真判空。
            raw_mode = data.get("mode")
            mode = IntentMode(raw_mode) if raw_mode else IntentMode.EXECUTE
            confidence = float(data.get("confidence", 0.5))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise LLMError(f"llm returned invalid structure: {e}") from e

        return IntentResult(
            intent=intent,
            mode=mode,
            confidence=max(0.0, min(1.0, confidence)),
            reason=data.get("reason"),
            search_query=_parse_search_query(data.get("search_query")),
        )

    async def rerank_policy(
        self, *, query: str, clauses: list
    ) -> dict[str, float]:
        """逐条判断条款能否回答该问题，输出 0~1 相关性分。

        与 answer_policy 的区别：这里只判相关性，不生成答案。
        之所以要一次判完全部候选，是为了让模型在**同一批**里做相对比较，
        分多次调用会让模型对每条都给出偏高的分。
        """
        if not clauses:
            return {}

        block = "\n\n".join(
            f"[{c.clause_id}] {c.title}\n{c.body[:200]}" for c in clauses
        )
        prompt = f"""判断下面每条政策条款能否真正回答用户的问题。

用户问题：{query}

【候选条款】
{block}

规则：
- 只填分数，不要解释
- 1.0 = 这条条款直接回答了问题
- 0.5 = 部分相关但没正面回答
- 0.0 = 讲的是另一件事（哪怕用词相近，如"修改地址"不能回答"合并发货"）
- 话题相近但核心问题不同的，给 0.2 以下

只输出 JSON：{{"clause_id": 分数, ...}}"""

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
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
            raise LLMError(f"llm rerank call failed: {e}") from e

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"llm rerank returned invalid json: {e}") from e

        out: dict[str, float] = {}
        for cid, v in (data or {}).items():
            try:
                out[str(cid)] = max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                continue
        return out

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

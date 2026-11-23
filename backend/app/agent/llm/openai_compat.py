"""T007: 真实 LLM 客户端（OpenAI 兼容接口）。

关键词以提示形式随请求发出，但最终判断由模型完成。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.agent.llm.base import LLMError
from app.agent.llm.schema import SchemaError, validate_intent_result
from app.agent.resilience import stats
from app.config import settings
from app.models.agent import IntentResult, IntentSource
from app.policy.models import PolicyAnswer, RetrievalDecision, RetrievalResult

logger = logging.getLogger(__name__)

_POLICY_SYSTEM_PROMPT = """你是客服政策问答助手。只能依据下面提供的【条款】回答，不得使用常识、
行业惯例或任何未列出的信息。只输出 JSON。

规则：
1. cited_clause_ids 只能取自条款列表里的 clause_id，**不得编造**。没有能回答的条款就给 miss。
2. decision 判定：
   - hit：某一条款明确回答了问题
   - ambiguous：多条条款都相关且分不出主次（如"能换吗"——退货与换货都相关）
   - miss：没有条款能回答，或只沾边

   **ambiguous 要慎用**：如果分歧只在于"普通商品 / 预售商品"这类用户能自行对号入座的条件，
   就把两种情况都讲清楚（先讲普通商品，再补一句预售/特殊类的差异），不要反问用户。
   只有缺的信息**只有用户知道**（比如"是否已联系过快递""是否已拆封"）才用 ambiguous 反问。
3. 引用的条款若有 exceptions，必须在回答中说明例外，并逐条写入 handled_exceptions。
   没有写进 handled_exceptions 的例外，视为没有处理。
4. 不得给出条款之外的承诺、金额、时效。
5. 语气：像真人客服在说话，不是念条款。
   - 通篇称「您」。
   - 先回应对方的处境（一句理解或歉意），再说做法；不要一开口就是结论或命令。
   - 用请求式说法：「建议您…」「麻烦您…」「您可以…」；
     避免「需要您…」「必须…」「由…发起」「进行核查」这类公文腔。
   - 结尾给一句可以依靠的收束，如「有问题您随时找我」。
     但不得借语气添加条款之外的处理方式、金额、时效承诺。
   - 两三句讲完，不编号、不分点、不列「其一其二」。

   同一事实的两种说法：
   ✗「这种情况需要您联系快递公司核实签收情况，由快递公司发起未收到货核查。」
   ✓「您别着急，建议您先联系快递公司核实一下签收情况，让他们帮您查一下这单送到哪儿了。
      有进展或者还有疑问，您随时找我。」

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
    """模型返回的改写查询清洗：非字符串/空白一律视为没改写，绝不抛错。

    校验器内部已做同样处理；保留此函数是因为它被单测直接引用。
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


_SYSTEM_PROMPT = """你是客服意图识别器。判断用户真实诉求，只输出 JSON。

## 一、意图定义

| 意图 | 含义 | 例子 |
|---|---|---|
| refund | 发起退款 | 我要退款、帮我退一下款 |
| logistics | 发货与物流进度 | 发货了吗、快递到哪了、包裹在哪 |
| change_address | 改收货地址 | 我要改地址、地址填错了 |
| query_order | 查订单内容与付款状态 | 订单多少钱、买的是什么、付款成功了吗、订单详情 |
| exchange | 提出换货申请 | 我想换个货、帮我换个尺码 |
| urge_shipment | 催发货（要求商家尽快发） | 帮我催一下、催催我的订单 |
| manual | 要人工 | 转人工、找真人、要客服电话 |
| policy | 问商家规则 | 能开发票吗、退货运费谁承担 |
| unknown | 诉求不明、问能力或功能入口 | 太差了、你好、你能干什么 |

**logistics 与 query_order 的边界**：
- logistics 管**"东西走到哪一步了"**：发货了吗、快递到哪了、包裹在哪、签收了吗
- query_order 管**"这单本身的信息"**：多少钱、买了什么、付款了没、订单号是多少
- "发货了吗" → logistics（问的是物流环节有没有动），不是 query_order

**催发货既不是查物流，也不是问规则，是 urge_shipment**：
"帮我催一下""催催我的订单""催一下发货""订单状态一直是待发货""怎么还不发货"
——诉求是**让商家尽快发货**，系统有专门的动作可执行，必须判 urge_shipment。
判 logistics 只会回一句"查不到物流信息"，答非所问。

**urge_shipment 与 policy 的边界**：
- 要求、抱怨、催促（"帮我催""怎么还不发"）→ urge_shipment
- 问规则、问赔偿（"超时没发货有赔偿吗""平台怎么规定的"）→ policy

**logistics 只覆盖查进度**。以下是问**处理规则**，判 policy（政策库有对应条款）：
"显示签收但没收到怎么办""包裹丢了怎么处理""运输中破损了怎么办"
——问的是"遇到这种情况该怎么办"，不是"现在到哪了"。

## 二、四条边界（最容易混）

### 1. 提申请 vs 问规则
判据不是句式，而是**问的对象**：
- 问**我这一单**能不能 → 对应业务意图 + mode=inquiry
- 问**商家规则条款**（时效/费用/条件/标准）→ policy
- 问**助手会什么、功能在哪点** → unknown（政策库只有商家规则，没有产品功能说明）

  能退吗 / 这个能退款吗 / 我这个情况可以退货吗 → refund + inquiry
  能改地址吗 / 还没发货能改地址吗 → change_address + inquiry
  能换个尺码吗 → exchange + inquiry
  能开发票吗 / 七天无理由怎么算 / 退款多久能到账 → policy
  退货运费谁承担 / 哪些商品不支持七天无理由 → policy
  跨境订单能退吗 / 企业采购能不能退 / 生鲜类能退吗 → policy
  你能干什么 / 你有什么功能 → unknown
  退款记录在哪看 / 发票入口在哪 → unknown
  我要退款 → refund + execute　｜　帮我改地址 → change_address + execute

关键区分：
- **"能X吗"要看问的是哪一类**：
  · 问**泛指的一类订单/商品**（跨境订单、企业采购、生鲜）→ policy，问的是这类规则
  · 问**眼前这一单**（"我这个""这个"）→ 对应业务意图 + inquiry
- policy **只收商家规则条款**（多久/谁承担/什么条件/怎么算/哪些范围/某类订单的规则）。
  产品功能、UI 入口、助手能力都不属于 policy——政策库没有这些内容，判 policy 必然查不到。

### 2. 有诉求 vs 诉求不明
- 有明确可执行诉求 → 对应业务意图
- 以下一律 unknown：抱怨（太差了/很失望）、发泄、闲聊、转述（我朋友说…）、
  否定（别发了/不想…）、历史陈述（上次退款拖了半个月）、诉求不明（东西摔碎了）
- **询问 AI 能力、问功能入口，都归 unknown**："你能干什么""你有什么功能"
  "退款记录在哪看""发票入口在哪"。这类问题问的是**助手会什么、页面在哪点**，
  不是商家规则，政策库没有这些内容——判 policy 必然查不到。

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
- urge_shipment 同样分 mode，但风险低：execute = 真的去催单；query = 只在陈述没发货、
  问进度，此时不应替用户催单，判 query；inquiry = 问能不能催 / 催了有用吗。
- intent 是 unknown 时 mode 不重要，给 execute 即可。

## 四、search_query

仅当 intent=policy 时输出：把用户口语改写成适合检索政策条款的查询词。
补全正式说法（"吊牌剪了"→"吊牌 商标标识 摘除 退货条件"），保留实体词，
空格分隔，不加标点。不得添加用户没提的信息；问题已规范则原样输出。
intent 不是 policy 时给 null。改写与意图判定同一次完成。

问发货时效/超时赔偿规则时，要改写成条款里的说法，否则检索不到：
"超时没发货有赔偿吗"→"超时未发货 赔付 比例 上限"
"平台对发货时间怎么规定"→"承诺发货时间 48小时 催发货 仅退款"

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
        self._http_client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        """复用连接池。

        每次调用新建 client 会在并发场景下把连接数顶到网关上限，实测 12 并发时
        出现 "All connection attempts failed"。复用后连接数稳定在池内。
        AsyncClient 本身并发安全，可直接被多个协程共用。
        """
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=self.timeout_ms / 1000,
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=64),
            )
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

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

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_input + hint_block + ctx_block},
        ]

        last_error: SchemaError | None = None
        for attempt in range(settings.schema_replay_attempts + 1):
            content = await self._complete(messages)
            try:
                outcome = validate_intent_result(content)
            except SchemaError as e:
                last_error = e
                logger.warning(
                    "llm schema invalid attempt=%d kind=%s err=%s raw=%s",
                    attempt + 1, e.kind, e.message[:160], content[:200],
                )
                if attempt >= settings.schema_replay_attempts:
                    break
                # 带错误重放：把上一轮的原文与可执行的纠错要求一起喂回去。
                # 温度 0 下重问同一句 prompt 几乎必然复现同一个错，必须告诉它错在哪。
                stats.record_schema_replay()
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            f"你上一次的输出不合法：{e.message}\n"
                            "请重新输出，只输出一个符合格式的 JSON 对象，"
                            "不要包含任何解释文字或 Markdown 代码块。"
                        ),
                    },
                ]
                continue

            for w in outcome.warnings:
                logger.info("llm schema warning: %s", w)
            result = outcome.result
            result.source = IntentSource.LLM
            return result

        stats.record_schema_failure()
        raise LLMError(
            f"llm returned invalid structure: {last_error.message if last_error else 'unknown'}",
            schema=True,
        )

    async def _complete(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            client = self._http()
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"llm call failed: {e}") from e

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
            client = self._http()
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
            client = self._http()
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

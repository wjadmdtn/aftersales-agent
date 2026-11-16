"""T014/T018/T024/T027/T030: Agent 主循环。

    输入 → 调 LLM（每次都调）→ 意图
                ├─ 失败 → 降级：宽写词表判断读写，写一律拒
                └─ 成功 → 按意图编排工具
                            ├─ 读：直接查并呈现
                            └─ 写：生成 pending → 复述 → 等确认
                                      └─ 确认 → **重新校验状态** → 执行

几条刻意的设计：
- 每次输入都先调 LLM，不做跨请求的"降级中"标记
- 关键词只作 hint，不参与意图判定
- 写操作执行前始终重新查一次订单，不管 pending 是否过期
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.agent import degrade, present
from app.agent.confirm import Action, build_confirmation, is_cancellation, is_confirmation
from app.agent.intent import IntentClassifier
from app.agent.session import SessionStore, snapshot_mismatch
from app.agent.tools.client import MockServiceClient
from app.models.agent import (
    READ_INTENTS,
    IntentMode,
    WRITE_INTENTS,
    Intent,
    IntentSource,
    PendingConfirmation,
    SessionState,
)
from app.models.enums import ResultCode
from app.models.handoff import ToolStatus, TriggerType
from app.policy.models import RetrievalDecision

logger = logging.getLogger(__name__)

# 与种子数据一致：ORD + 至少 4 位数字
ORDER_ID_PATTERN = re.compile(r"ORD\d{4,}")
AMOUNT_PATTERN = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱)?")
# 手机号：改地址必须带收件人联系方式，用它作为"用户确实给了新地址"的判据
PHONE_PATTERN = re.compile(r"1\d{10}")
# 行政区划关键词：出现即认为消息里有地址内容
ADDRESS_HINTS = ("省", "市", "区", "县", "路", "街", "号", "栋", "室", "小区", "镇", "村")

# 业务码 → 工具状态。2xxx（没这个对象）是"查不到"，不是"失败了"：
# 前者该转人工说明情况，后者要累计失败次数，两者处理完全不同。
_STATUS_OF_CODE = {2001: "NOT_FOUND", 2002: "NOT_FOUND", 2003: "NOT_FOUND", 4002: "TIMEOUT"}


def tool_status_of(body: dict) -> ToolStatus:
    code = int(body.get("code") or 0)
    if code == ResultCode.OK:
        return ToolStatus.OK
    name = _STATUS_OF_CODE.get(code)
    return ToolStatus(name) if name else ToolStatus.ERROR


def tool_failed(body: dict) -> bool:
    """只有系统级失败才计入连续失败次数。

    业务拒绝（状态不支持退款、金额超限）是正常的业务结论，
    把它算成"工具失败"会让用户听见几句正常拒绝就被转人工。
    """
    return int(body.get("code") or 0) >= 4000 or body.get("_transport_error") is True


@dataclass
class AgentReply:
    reply: str
    intent: Intent | None = None
    mode: str | None = None
    source: IntentSource = IntentSource.LLM
    tool_calls: list[str] = field(default_factory=list)
    state: str = "done"
    snapshot_mismatch: bool = False


class AgentLoop:
    def __init__(
        self,
        *,
        classifier: IntentClassifier,
        sessions: SessionStore,
        tools: MockServiceClient,
        coordinator=None,
        policy=None,
        on_handoff=None,
    ):
        self.classifier = classifier
        self.sessions = sessions
        self.tools = tools
        self.coordinator = coordinator
        self.policy = policy
        # 转人工成功后的旁路通知（写 ChatState + 广播），不改变Agent 的返回值语义
        self.on_handoff = on_handoff

    async def handle(
        self,
        *,
        session_id: str,
        user_id: str,
        message: str,
        trace_id: str,
        order_id: str | None = None,
        snapshot=None,
    ) -> AgentReply:
        state = await self.sessions.get_or_create(
            session_id,
            user_id=user_id,
            order_id=order_id,
            snapshot=snapshot,
        )
        state.turn_count += 1

        # 入参订单号是"进会话那一刻的上下文"，只在会话还没有订单时采纳。
        # 会话已有订单就该由会话自己维护（用户中途提到别的订单会切换）——
        # 否则"帮我退 ORD1003"切走了，下一轮又被前端带回的 ORD1002 覆盖回去，
        # 用户会在毫无提示的情况下对错误的订单发起退款（FR-022）。
        # 不复用 get_or_create 来做这件事：它的语义是"取或建"，不是"建时绑定"。
        if not state.order_id and order_id:
            state.order_id = order_id
            if snapshot is not None:
                state.snapshot = snapshot

        # ---- 1. 有待确认操作时，先处理确认回复 ----
        pending = await self.sessions.get_pending(session_id)
        if pending is not None:
            if self._pending_bypassed(pending, message):
                # 用户没在回答确认，而是提了新诉求。旧卡作废，本轮按新消息正常处理。
                # 这笔待退金额保留：转人工时要拿它判风险等级（大额单不能被当成普通排队）。
                await self.sessions.clear_pending(session_id)
            else:
                reply = await self._handle_confirmation(
                    state, pending, message, trace_id=trace_id
                )
                await self.sessions.save(state)
                return reply

        # ---- 2. 上一轮结构重放已耗尽：这一次直接接人，别再让模型试 ----
        # 只有这一项必须放在调 LLM 之前：模型写不出合法结构时，每轮都会
        # "先失败再降级"，标记永远等不到生效的那一轮。
        # 标记是一次性的——生效后立即清除。不清除的话，用户在人工队列里说的
        # 每一句话都会被这个旧标记重新推一遍人工。
        if state.schema_retries_exhausted:
            state.schema_retries_exhausted = False
            reply = await self._handoff(
                state,
                message,
                primary_intent=(state.intent.value if state.intent else "unknown"),
                trace_id=trace_id,
                forced_detail="输出校验重放耗尽",
            )
            reply.intent = state.intent
            await self.sessions.save(state)
            return reply

        # ---- 3. 每次都先调 LLM ----
        outcome = await self.classifier.classify_detailed(
            message,
            context={"order_id": state.order_id, "intent": state.intent},
        )
        result, error = outcome.result, outcome.error

        if result is None:
            # 结构失败与网络失败分开记账：前者下一轮走 SYSTEM_ERROR 转人工，
            # 后者累加 llm_timeout_streak。混在一起会把"模型不会写 JSON"
            # 当成"网络在抖"，重试一百次也不会好。
            if outcome.schema_exhausted:
                state.schema_retries_exhausted = True
            else:
                state.llm_timeout_streak += 1
            reply = await self._handle_degraded(state, message, error, trace_id=trace_id)
            await self.sessions.save(state)
            return reply

        # 这次识别成功：上一轮的抽风不该继续影响本会话的转人工判定
        state.schema_retries_exhausted = False
        state.llm_timeout_streak = 0

        # mode 兜底校验：LLM 判错时往保守方向纠偏
        from app.agent.intent_mode import normalize_mode

        fixed_mode, fix_note = normalize_mode(result.intent, result.mode, message)
        if fix_note:
            result = result.model_copy(update={"mode": fixed_mode, "reason": fix_note})

        # ---- 4. 订单号切换 ----
        mentioned = ORDER_ID_PATTERN.search(message)
        if mentioned and mentioned.group(0) != state.order_id:
            switched = await self._switch_order(state, mentioned.group(0), trace_id=trace_id)
            if switched is not None:
                await self.sessions.save(state)
                return switched

        state.intent = result.intent

        # 连续系统级失败已达阈值：用户已经在一遍遍等、一遍遍重试，
        # 这一轮不该再让他听一遍"系统繁忙"。放在这里而不是调 LLM 之前，
        # 是因为需要 result.intent 才能给出准确的 primary_intent——
        # 提前到 LLM 之前只能拿到上一轮的意图，新会话里就是 unknown。
        if self._exhausted(state, message):
            reply = await self._handoff(
                state, message, primary_intent=result.intent.value, trace_id=trace_id
            )
            reply.intent = result.intent
            reply.mode = result.mode.value
            await self.sessions.save(state)
            return reply

        reply = await self._dispatch(state, result, message, trace_id=trace_id)
        # 所有意图都要回传 mode：查询类的 mode 同样是判分依据
        reply.mode = result.mode.value
        await self.sessions.save(state)
        return reply

    def _pending_bypassed(self, pending: PendingConfirmation, message: str) -> bool:
        """用户这句是不是在"另起炉灶"，还是在回答确认卡。

        整体转人工 / 风险词 → 让路：最该接人的时候不该被"请您确认一下"卡住。
        其他情况一律留在确认流程里（含"我要退 9999 块"这种改金额的说法）——
        确认卡本就应该先确认再执行，用户说了新金额也该重弹一次卡片，
        不能一句话就把待确认操作丢掉。
        """
        text = message.strip()
        if not text or is_confirmation(text) or is_cancellation(text):
            return False
        from app.config import settings

        if any(w in text for w in settings.user_request_keywords):
            return True
        return any(w in text for w in settings.risk_keywords)

    # ---------- 工具调用 ----------

    def _note_tool(self, state: SessionState, body: dict) -> dict:
        """把一次工具调用的结果记进会话状态。

        转人工的 SYSTEM_ERROR / BOUNDARY 判定读的就是这里累积的计数，
        不记的话 triggers 里那几条规则永远拿默认值（Feature 003 遗留缺陷）。
        """
        status = tool_status_of(body)
        state.last_tool_status = status
        state.tool_failure_streak = state.tool_failure_streak + 1 if tool_failed(body) else 0
        return body

    async def _call(self, coro_factory, state: SessionState) -> dict:
        """工具调用统一出口：传输层异常转成结构化结果，不让它冒泡成 500。

        httpx 超时/连接失败在这里被吃掉并标记为 4002（DEPENDENCY_TIMEOUT），
        这样上层既会计入失败次数，也能按正常业务结果渲染给用户。
        """
        try:
            body = await coro_factory()
        except Exception as e:  # noqa: BLE001 - 网络类异常一律视为依赖失败
            logger.warning("tool call failed: %s: %s", type(e).__name__, str(e)[:120])
            body = {
                "code": ResultCode.DEPENDENCY_TIMEOUT,
                "data": None,
                "reason": "业务服务暂时不可用",
                "_transport_error": True,
            }
        return self._note_tool(state, body)

    def _exhausted(self, state: SessionState, message: str) -> bool:
        """是否已进入"该转人工"的异常状态。

        只判系统级异常（失败次数/超时/structure），不判风险与边界——
        那两类各有自己的判定入口，混进来会让投诉词多走一遍兜底。
        """
        if self.coordinator is None:
            return False
        trigger = self.coordinator.detect(
            user_message=message,
            last_tool_status=state.last_tool_status,
            tool_failure_streak=state.tool_failure_streak,
            llm_timeout_streak=state.llm_timeout_streak,
            schema_retries_exhausted=state.schema_retries_exhausted,
        )
        return trigger.triggered and trigger.trigger_type is TriggerType.SYSTEM_ERROR

    # ---------- 降级 ----------

    async def _handle_degraded(
        self, state: SessionState, message: str, error: Exception | None, *, trace_id: str
    ) -> AgentReply:
        """重试耗尽后的降级：统一话术，不猜意图。

        不做读写区分——区分本身就要求判断意图，而意图判断正是此刻不可用的能力。
        """
        logger.error(
            "llm unavailable, degraded trace_id=%s err=%s", trace_id, type(error).__name__ if error else "?"
        )
        return AgentReply(
            reply=degrade.degraded_reply(),
            source=IntentSource.DEGRADED,
            state="degraded",
        )

    # ---------- 订单切换 ----------

    async def _switch_order(
        self, state: SessionState, order_id: str, *, trace_id: str
    ) -> AgentReply | None:
        body = await self._call(lambda: self.tools.get_order(order_id, trace_id=trace_id), state)
        if body.get("code") != ResultCode.OK:
            # 订单不存在：保持原上下文（FR-023）
            return AgentReply(
                reply=present.render(body),
                state="switch_failed",
            )
        state.order_id = order_id
        state.snapshot = None  # 切到新订单，旧快照失效
        return None  # 继续走正常流程

    # ---------- 分发 ----------

    async def _dispatch(
        self, state: SessionState, result, message: str, *, trace_id: str
    ) -> AgentReply:
        intent: Intent = result.intent

        mode = getattr(result, "mode", IntentMode.EXECUTE)

        if intent in READ_INTENTS:
            return await self._handle_read(state, intent, trace_id=trace_id)

        if intent in WRITE_INTENTS:
            if mode is IntentMode.INQUIRY:
                # 咨询可行性：回答能不能，不执行
                return await self._handle_inquiry(state, intent, trace_id=trace_id)
            if mode is IntentMode.QUERY:
                return await self._handle_refund_progress(state, trace_id=trace_id)
            if mode is IntentMode.NEED_CONTEXT:
                # 只有真的没有订单上下文时才追问。前端带货进场的场景下
                # state.order_id 一定存在，此时再问订单号是多余的。
                if not state.order_id:
                    return AgentReply(
                        reply="请问是哪一笔订单呢？麻烦提供一下订单号。",
                        intent=intent,
                        state="need_order",
                    )
            return await self._handle_write(state, intent, message, trace_id=trace_id)

        if intent is Intent.EXCHANGE:
            # 换货不由 AI 办理：按订单状态给出条件判断 + 自助入口指引。
            # 不弹确认、不调工具、不改订单——换货申请由用户在订单页自己提交。
            return await self._handle_exchange(state, trace_id=trace_id)

        if intent is Intent.MANUAL:
            return await self._handoff(
                state, message, primary_intent="manual", trace_id=trace_id
            )

        if intent is Intent.POLICY:
            # 只有 policy 意图才走检索。业务动作不必查政策——既慢又答非所问。
            # search_query：意图识别同一次调用顺带产出的改写查询，不额外花调用
            policy_reply, retrieval = await self._try_policy(
                state, message, trace_id=trace_id, search_query=result.search_query
            )
            if policy_reply is not None:
                return policy_reply
            # 知识库答不了 → 转人工（规范 005 FR：无条款不得编造，转人工）。
            # 检索层 miss 与 rerank 判死都算"答不了"：
            # 前者是没找到候选，后者是候选全被判无关，对用户而言都是没有答案。
            # 不再走反问引导——用户问的是规则，换个说法也还是答不出。
            from app.models.handoff import TriggerResult, TriggerType

            return await self._handoff(
                state,
                message,
                primary_intent="policy",
                trace_id=trace_id,
                forced_trigger=TriggerResult.fire(
                    TriggerType.BOUNDARY,
                    retrieval.reason if retrieval else "政策检索未命中",
                ),
                reply_intent=Intent.POLICY,
            )

        # UNKNOWN（诉求不明，含抱怨/闲聊/无法归类）：只安抚，不追问、不转人工、不调工具。
        # 情绪类不该被 AI 反复盘问，也不该把人工成本吃光。
        return await self._clarify(state, message, trace_id=trace_id, comfort_only=True)

    async def _try_policy(
        self,
        state: SessionState,
        message: str,
        *,
        trace_id: str,
        search_query: str | None = None,
    ) -> tuple[AgentReply | None, Any]:
        """检索命中则直接回答。

        返回 (回复, 检索结果)。回复为 None 表示未命中，交回澄清链路；
        检索结果供调用方取相似度信号（澄清耗尽转人工时判 BOUNDARY 用）。
        search_query：意图识别顺带产出的改写查询，仅喂检索层。
        """
        if self.policy is None:
            return None, None

        order_created_at = None
        product_type = None
        if state.order_id:
            body = await self._call(
            lambda: self.tools.get_order(state.order_id, trace_id=trace_id), state
        )
            if body.get("code") == ResultCode.OK:
                order = body.get("data") or {}
                order_created_at = _parse_dt(order.get("created_at"))
                product_type = order.get("product_type")

        answer, retrieval = await self.policy.answer(
            question=message,
            order_created_at=order_created_at,
            product_type=product_type,
            trace_id=trace_id,
            search_query=search_query,
        )

        if answer.decision is RetrievalDecision.AMBIGUOUS:
            state.clarify_round += 1
            return (
                AgentReply(
                    reply=answer.ambiguity_question or "您能再具体说一下是想问哪方面吗？",
                    intent=Intent.POLICY,
                    state="clarifying",
                ),
                retrieval,
            )

        if answer.decision is RetrievalDecision.MISS or not answer.answer:
            # 答不了：不论是 rerank 全剔除（条款存在但都不能回答）
            # 还是检索层没找到候选，都由调用方统一转人工。
            # 不在这一层转接，避免与调用方重复处理。
            return None, retrieval

        state.last_cited_clause_ids = list(answer.cited_clause_ids)
        state.policy_turns += 1
        return (
            AgentReply(
                reply=answer.answer,
                intent=Intent.POLICY,
                tool_calls=["policy.search"],
                state="policy_answered",
            ),
            retrieval,
        )

    CLARIFY_ROUND_LIMIT = 2

    async def _clarify(
        self,
        state: SessionState,
        message: str,
        *,
        trace_id: str,
        rag_top_similarity: float | None = None,
        comfort_only: bool = False,
    ) -> AgentReply:
        """诉求不明时的回应。

        产品口径（2026-11-15）：
        - comfort_only=True（UNKNOWN）：按情绪细分
            · 有负面情绪（抱怨/投诉）→ 只安抚道歉，不追问、不转人工
            · 无情绪（闲聊/问能力/误触）→ 介绍能做什么，把话头交回用户。
              这类输入没有抱怨成分，回"您遇到什么情况"是答非所问。
        - comfort_only=False（POLICY 检索无结果）：走反问引导，把诉求问出来。

        都不主动提"要不要转人工"——转人工只有在用户主动要（manual）
        或系统异常/能力边界时才发生。
        """
        state.clarify_round += 1

        from app.agent.clarify import (
            CAPABILITY_INTRO,
            clarify_round_one,
            clarify_round_two,
            comfort_reply,
        )
        from app.handoff.emotion import detect_emotion
        from app.models.handoff import Emotion

        # 跨轮保留检索信号：后续若因别的条件转人工，还要用它判断是否知识缺口
        if rag_top_similarity is not None:
            state.last_rag_top_similarity = rag_top_similarity

        negative = detect_emotion(message) is not Emotion.NEUTRAL

        if comfort_only and negative:
            # 抱怨/投诉：只道歉，不追问
            reply = comfort_reply()
        elif comfort_only:
            # 闲聊/问能力/误触：介绍能力，问用户想做什么
            reply = CAPABILITY_INTRO
        elif state.clarify_round == 1:
            reply = clarify_round_one(negative=negative)
        else:
            reply = clarify_round_two()

        # 意图必须回传：评测与前端都靠它区分"抱怨"与"问政策但没检索到"
        return AgentReply(reply=reply, intent=state.intent, state="clarifying")

    async def _handoff(
        self,
        state: SessionState,
        message: str,
        *,
        primary_intent: str,
        trace_id: str,
        forced_trigger=None,
        reply_intent: Intent | None = None,
        rag_top_similarity: float | None = None,
        forced_detail: str = "",
    ) -> AgentReply:
        if self.coordinator is None:
            return AgentReply(
                reply="正在为您接入人工客服，请稍候。", intent=reply_intent, state="handoff_pending"
            )

        if forced_trigger is not None:
            trigger = forced_trigger
            refund_amount = None
        else:
            # 6 个信号全部传：少传任何一个，triggers 里对应那条规则就是死代码。
            # refund_amount 取待确认操作的金额——用户刚说了"退 9999 块"又要求转人工，
            # 那笔钱就是风险本身。没有待确认操作时不要拿订单全额凑数：
            # "查一下订单，转人工" 不是大额退款风险，按 do-nothing 原则不升级。
            refund_amount = state.pending_refund_amount
            trigger = self.coordinator.detect(
                user_message=message,
                refund_amount=refund_amount,
                last_tool_status=state.last_tool_status,
                tool_failure_streak=state.tool_failure_streak,
                llm_timeout_streak=state.llm_timeout_streak,
                schema_retries_exhausted=state.schema_retries_exhausted,
                rag_top_similarity=rag_top_similarity,
            )

        if forced_detail:
            # 调用方已知原因（如"输出校验重放耗尽"），必须原样落进 Context Pack——
            # 人工端要看得出是"模型输出问题"还是"业务问题"，两者处置完全不同。
            # 用 SYSTEM_ERROR 触发，不受下面"未触发就归 USER_REQUEST"的兜底影响。
            from app.models.handoff import TriggerResult

            trigger = TriggerResult.fire(TriggerType.SYSTEM_ERROR, forced_detail)
        elif not trigger.triggered:
            # 走到这里只剩"意图是 manual 但没命中人工关键词"的情形。
            # 投诉/差评不再自动转人工（2026-11-13 口径），全部由 AI 安抚。
            from app.models.handoff import TriggerResult

            trigger = TriggerResult.fire(
                TriggerType.USER_REQUEST,
                f"意图识别为 {primary_intent}",
            )

        outcome = await self.coordinator.initiate(
            session_id=state.session_id,
            user_id=state.user_id,
            user_message=message,
            primary_intent=primary_intent,
            trigger=trigger,
            order_id=state.order_id,
            refund_amount=refund_amount,
        )
        # 状态变化必须送到界面，否则用户端会一直显示"AI 助手"而实际已在排队。
        # 默认 no-op，避免 003~005 的既有测试被牵连。
        if self.on_handoff is not None:
            await self.on_handoff(state.session_id, outcome.state)
        if reply_intent is None:
            reply_intent = Intent.MANUAL
        return AgentReply(
            reply=outcome.reply,
            intent=reply_intent,
            state="handoff_queued",
        )

    async def _handle_exchange(self, state: SessionState, *, trace_id: str) -> AgentReply:
        """换货指引：判断条件是否满足，告诉用户去哪自助申请。不调任何写工具。

        依据 AFTERSALE-EXCHANGE-01：
        - 普通换货：签收次日起 7 日内，仅限同款不同规格
        - 未签收（未发货/已发货未签收）→ 还没到换货环节
        - 已签收超 7 日 → 不再受理无理由换货
        """
        if not state.order_id:
            return AgentReply(
                reply="请问是哪一笔订单呢？我帮您确认一下换货条件。",
                intent=Intent.EXCHANGE,
                state="need_order",
            )

        body = await self._call(
            lambda: self.tools.get_order(state.order_id, trace_id=trace_id), state
        )
        if body.get("code") != ResultCode.OK:
            return AgentReply(reply=present.render(body), intent=Intent.EXCHANGE, state="exchange_failed")

        order = body.get("data") or {}
        status = order.get("status")

        if status in ("PENDING_PAYMENT", "PAID"):
            reply = (
                f"这笔订单还没发货，暂时不需要换货哦。"
                f"如果商品信息不对，可以取消订单重新下单。"
            )
            return AgentReply(reply=reply, intent=Intent.EXCHANGE, state="exchange_not_applicable")

        if status == "SHIPPED":
            return AgentReply(
                reply="这笔订单还在运输中，签收后 7 日内可以申请换货。收到货后如有需要，"
                      "可以在「我的订单 → 申请售后 → 换货」里提交。",
                intent=Intent.EXCHANGE,
                state="exchange_not_applicable",
            )

        if status in ("REFUNDED", "REFUNDING"):
            return AgentReply(
                reply="这笔订单已经在退款流程中，无法再申请换货。",
                intent=Intent.EXCHANGE,
                state="exchange_not_applicable",
            )

        # 已签收：算是否在 7 天内。签收时间在物流轨迹里，订单接口不带，
        # 必须单独查一次物流——拿不到就按"期限内"处理，不因为查不到就拒绝用户。
        days_left = None
        try:
            lg = await self._call(
                lambda: self.tools.get_logistics(state.order_id, trace_id=trace_id), state
            )
            days_left = _exchange_days_left(lg.get("data") or {})
        except Exception:  # noqa: BLE001 - 物流查不到不影响给指引
            logger.warning("exchange: logistics lookup failed order=%s", state.order_id)
        if days_left is not None and days_left < 0:
            return AgentReply(
                reply="抱歉，这笔订单签收已超过 7 天，不再支持无理由换货。"
                      "如果是质量问题，您可以提供照片，我帮您转人工核实。",
                intent=Intent.EXCHANGE,
                state="exchange_expired",
            )

        when = f"还剩 {days_left} 天" if days_left is not None else "在签收次日起 7 日内"
        return AgentReply(
            reply=f"这笔订单{when}可以申请换货，仅限同款不同规格（如尺码、颜色）。"
                  f"您可以在「我的订单 → 申请售后 → 换货」里自助提交，"
                  f"我这边也可以帮您转人工处理。",
            intent=Intent.EXCHANGE,
            state="exchange_available",
        )

    async def _handle_inquiry(
        self, state: SessionState, intent: Intent, *, trace_id: str
    ) -> AgentReply:
        """回答"能做吗"，不执行任何写操作。"""
        if not state.order_id:
            return AgentReply(
                reply="请问是哪一笔订单呢？我帮您确认一下。",
                intent=intent,
                state="need_order",
            )

        body = await self._call(
            lambda: self.tools.get_order(state.order_id, trace_id=trace_id), state
        )
        if body.get("code") != ResultCode.OK:
            return AgentReply(
                reply=present.render(body),
                intent=intent,
                tool_calls=["order.get"],
                state="inquiry_failed",
            )

        order = body.get("data") or {}
        status = order.get("status")

        if intent is Intent.REFUND:
            if status in ("PAID", "SHIPPED", "DELIVERED"):
                reply = (
                    f"这笔订单（¥{order.get('amount')}）目前符合退款条件，"
                    "如果您确定要退，告诉我就行，我帮您提交申请。"
                )
            elif status == "REFUNDED":
                reply = "这笔订单已经退款完成了，不需要重复申请哦。"
            elif status == "REFUNDING":
                reply = "这笔订单正在退款处理中，请留意到账通知。"
            else:
                reply = "这笔订单目前的状态暂时不支持退款。"
        else:
            if status in ("PENDING_PAYMENT", "PAID"):
                reply = "这笔订单还没发货，可以修改收货地址。需要我帮您改吗？"
            else:
                reply = "订单已经发货了，收货地址无法修改。"

        return AgentReply(
            reply=reply,
            intent=intent,
            tool_calls=["order.get"],
            state="inquiry_answered",
        )

    async def _handle_refund_progress(self, state: SessionState, *, trace_id: str) -> AgentReply:
        """查退款进度。固定话术，不调 LLM。"""
        if not state.order_id:
            return AgentReply(
                reply="请问是哪一笔订单呢？我帮您查一下退款进度。",
                intent=Intent.REFUND,
                state="need_order",
            )

        body = await self._call(
            lambda: self.tools.get_order(state.order_id, trace_id=trace_id), state
        )
        if body.get("code") != ResultCode.OK:
            return AgentReply(reply=present.render(body), state="query_failed")

        status = (body.get("data") or {}).get("status")
        if status == "REFUNDING":
            reply = "您的退款申请已经提交，预计 1-3 个工作日原路退回，请留意账户到账。"
        elif status == "REFUNDED":
            reply = "这笔订单的退款已经完成，请留意账户到账，如未收到可联系发卡行。"
        else:
            reply = "这笔订单目前没有退款记录哦。是遇到什么问题了吗？"

        return AgentReply(reply=reply, intent=Intent.REFUND, state="refund_progress")

    async def _handle_read(
        self, state: SessionState, intent: Intent, *, trace_id: str
    ) -> AgentReply:
        if not state.order_id:
            return AgentReply(reply="请问是哪一笔订单呢？麻烦提供一下订单号。", state="need_order")

        if intent is Intent.LOGISTICS:
            body = await self._call(
                lambda: self.tools.get_logistics(state.order_id, trace_id=trace_id), state
            )
            calls = ["logistics.get"]
        else:
            body = await self._call(
            lambda: self.tools.get_order(state.order_id, trace_id=trace_id), state
        )
            calls = ["order.get"]

        mismatch = snapshot_mismatch(state.snapshot, type("O", (), body.get("data") or {})())
        return AgentReply(
            reply=present.render(body),
            intent=intent,
            tool_calls=calls,
            snapshot_mismatch=mismatch,
        )

    async def _handle_write(
        self, state: SessionState, intent: Intent, message: str, *, trace_id: str
    ) -> AgentReply:
        if not state.order_id:
            return AgentReply(reply="请问是哪一笔订单呢？麻烦提供一下订单号。", state="need_order")

        # 写操作在弹确认前就要查订单：不能改的订单不该走到"确认"这一步，
        # 否则用户点了确认才发现不行，是拿无效确认骗用户。
        body = await self._call(
            lambda: self.tools.get_order(state.order_id, trace_id=trace_id), state
        )
        if body.get("code") != ResultCode.OK:
            return AgentReply(reply=present.render(body), state="execute_failed")
        order = body.get("data") or {}
        status = order.get("status")

        if intent is Intent.CHANGE_ADDRESS:
            # 只有未发货（待支付 / 已支付）的订单可以改地址。
            if status not in ("PENDING_PAYMENT", "PAID"):
                return AgentReply(
                    reply="订单已经发货了，收货地址无法修改。",
                    intent=intent,
                    state="write_rejected",
                )
            # 没说新地址就弹确认，等于让用户确认一个空地址——
            # 服务端会拒，用户白点一次。先问清楚要改成哪里。
            if not _extract_address(message):
                return AgentReply(
                    reply="好的，请把新的收货人、手机号、地址发给我，我帮您修改。",
                    intent=intent,
                    state="need_address",
                )
            amount = None
        else:
            # 用户未说金额时按订单全额退款。金额取自库，不用前端快照。
            if status == "REFUNDED":
                return AgentReply(
                    reply="这笔订单已经退款完成了，不需要重复申请哦。",
                    intent=intent,
                    state="write_rejected",
                )
            if status == "REFUNDING":
                return AgentReply(
                    reply="这笔订单正在退款处理中，请留意到账通知。",
                    intent=intent,
                    state="write_rejected",
                )
            matched = _extract_amount(message)
            amount = matched if matched is not None else Decimal(str(order.get("amount", "0")))

        pending = build_confirmation(
            intent=intent,
            order_id=state.order_id,
            amount=amount,
            payload={"address": _extract_address(message) or {}} if intent is Intent.CHANGE_ADDRESS else {},
        )
        await self.sessions.set_pending(state.session_id, pending)
        state.pending_refund_amount = pending.amount
        return AgentReply(reply=pending.summary, intent=intent, state="awaiting_confirmation")

    # ---------- 确认处理 ----------

    async def _handle_confirmation(
        self, state: SessionState, pending: PendingConfirmation, message: str, *, trace_id: str
    ) -> AgentReply:
        if is_cancellation(message):
            await self.sessions.clear_pending(state.session_id)
            state.pending_refund_amount = None
            return AgentReply(reply="好的，已为您取消。", state="cancelled")

        if not is_confirmation(message):
            return AgentReply(
                reply=f"请您确认一下：{pending.summary}",
                state="awaiting_confirmation",
            )

        # 确认通过。执行前**始终**重新校验订单状态（FR-020）
        await self.sessions.clear_pending(state.session_id)
        state.pending_refund_amount = None

        if pending.action == Action.REFUND.value:
            check = await self._call(
                lambda: self.tools.get_order(pending.order_id, trace_id=trace_id), state
            )
            if check.get("code") != ResultCode.OK:
                return AgentReply(reply=present.render(check), state="execute_failed")
            status = (check.get("data") or {}).get("status")
            # 从弹卡片到点确认之间订单可能已被退过，重复退款必须挡住
            if status in ("REFUNDED", "REFUNDING"):
                return AgentReply(
                    reply="这笔订单已经退款完成或正在退款中，无需重复申请。",
                    state="write_rejected",
                )
            if status in ("PENDING_PAYMENT", "CLOSED"):
                return AgentReply(
                    reply="这笔订单当前状态不支持退款。",
                    state="write_rejected",
                )

            body = await self._call(
                lambda: self.tools.create_refund(
                    pending.order_id,
                    pending.amount or Decimal("0"),
                    # 幂等键用确认卡上定稿的那把：同一张卡不管重试多少次都是同一个请求，
                    # 业务侧按 (action, request_id) 的去重才真正生效。
                    request_id=pending.request_id or f"refund:{pending.order_id}",
                    trace_id=trace_id,
                ),
                state,
            )
            if body.get("_transport_error"):
                # 结果未知不能说"已退款"，也不能说"没退"——两句话都可能骗用户
                return AgentReply(
                    reply="退款请求已发出，但暂时没拿到结果。请稍后在订单里确认，"
                          "或点「转人工」让客服帮您核实。",
                    tool_calls=["order.get", "refund.create"],
                    state="execute_uncertain",
                )
            return AgentReply(
                reply=present.render(body),
                tool_calls=["order.get", "refund.create"],
                state="executed",
            )

        # 改地址同样要在执行前复核：弹确认到用户点确认之间可能已经发货
        check = await self._call(
            lambda: self.tools.get_order(pending.order_id, trace_id=trace_id), state
        )
        if check.get("code") != ResultCode.OK:
            return AgentReply(reply=present.render(check), state="execute_failed")
        if (check.get("data") or {}).get("status") not in ("PENDING_PAYMENT", "PAID"):
            return AgentReply(
                reply="这笔订单刚刚已经发货了，收货地址无法修改。",
                state="write_rejected",
            )

        body = await self._call(
            lambda: self.tools.update_address(
                pending.order_id,
                pending.payload.get("address", {}),
                trace_id=trace_id,
                request_id=pending.request_id,
            ),
            state,
        )
        if body.get("_transport_error"):
            return AgentReply(
                reply="地址修改请求已发出，但暂时没拿到结果。请稍后在订单里确认，"
                      "或点「转人工」让客服帮您核实。",
                tool_calls=["order.get", "address.update"],
                state="execute_uncertain",
            )
        return AgentReply(
            reply=present.render(body),
            tool_calls=["order.get", "address.update"],
            state="executed",
        )


EXCHANGE_WINDOW_DAYS = 7


def _extract_amount(message: str) -> Decimal | None:
    """从消息里抽出用户指定的退款金额。

    先摘掉订单号再匹配：旧实现直接全文搜数字，"帮我退 ORD1002" 里的
    「1002」会被当成 1002 元。订单号不是金额，必须整段挖掉。
    """
    text = ORDER_ID_PATTERN.sub(" ", message)
    matched = AMOUNT_PATTERN.search(text)
    return Decimal(matched.group(1)) if matched else None


def _extract_address(message: str) -> dict | None:
    """判断消息里是否包含可用的新地址信息。

    只做"够不够"的粗判，不做精细解析——完整地址结构由前端表单提供。
    判据：出现手机号，或出现两个以上行政区划关键词。
    宁严勿松：判宽了会弹一个内容残缺的确认框，用户点了照样失败。
    """
    if PHONE_PATTERN.search(message):
        return {"raw": message}
    hits = sum(1 for h in ADDRESS_HINTS if h in message)
    if hits >= 2:
        return {"raw": message}
    return None


def _exchange_days_left(order: dict) -> int | None:
    """按签收日算换货剩余天数。签收次日计第 1 天。

    找不到签收时间返回 None（按"在期限内"处理并提示用户自行确认）。
    """
    signed_at = _signed_at(order)
    if signed_at is None:
        return None
    if signed_at.tzinfo is None:
        signed_at = signed_at.replace(tzinfo=timezone.utc)
    deadline = signed_at + timedelta(days=EXCHANGE_WINDOW_DAYS)
    return (deadline - datetime.now(timezone.utc)).days


def _signed_at(payload: dict) -> datetime | None:
    """取签收时间。兼容两种结构：订单内的 logistics 子对象、物流接口的 data。"""
    tracks = (payload.get("logistics") or {}).get("tracks") or payload.get("tracks") or []
    for t in tracks:
        if t.get("node") == "已签收":
            return _parse_dt(t.get("at"))
    return None


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value


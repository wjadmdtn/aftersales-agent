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
import uuid
from dataclasses import dataclass, field
from datetime import datetime
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
    TurnRecord,
)
from app.models.enums import ResultCode
from app.policy.models import RetrievalDecision

logger = logging.getLogger(__name__)

# 与种子数据一致：ORD + 至少 4 位数字
ORDER_ID_PATTERN = re.compile(r"ORD\d{4,}")
AMOUNT_PATTERN = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱)?")


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
    ):
        self.classifier = classifier
        self.sessions = sessions
        self.tools = tools
        self.coordinator = coordinator
        self.policy = policy

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

        # ---- 1. 有待确认操作时，先处理确认回复 ----
        pending = await self.sessions.get_pending(session_id)
        if pending is not None:
            reply = await self._handle_confirmation(
                state, pending, message, trace_id=trace_id
            )
            await self.sessions.save(state)
            return reply

        # ---- 2. 每次都先调 LLM ----
        result, error = await self.classifier.classify(
            message,
            context={"order_id": state.order_id, "intent": state.intent},
        )

        if result is None:
            reply = await self._handle_degraded(state, message, error, trace_id=trace_id)
            await self.sessions.save(state)
            return reply

        # mode 兜底校验：LLM 判错时往保守方向纠偏
        from app.agent.intent_mode import normalize_mode

        fixed_mode, fix_note = normalize_mode(result.intent, result.mode, message)
        if fix_note:
            result = result.model_copy(update={"mode": fixed_mode, "reason": fix_note})

        # ---- 3. 订单号切换 ----
        mentioned = ORDER_ID_PATTERN.search(message)
        if mentioned and mentioned.group(0) != state.order_id:
            switched = await self._switch_order(state, mentioned.group(0), trace_id=trace_id)
            if switched is not None:
                await self.sessions.save(state)
                return switched

        state.intent = result.intent
        reply = await self._dispatch(state, result, message, trace_id=trace_id)
        # 所有意图都要回传 mode：查询类的 mode 同样是判分依据
        reply.mode = result.mode.value
        await self.sessions.save(state)
        return reply

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
        body = await self.tools.get_order(order_id, trace_id=trace_id)
        if body.get("code") != ResultCode.OK:
            # 订单不存在：保持原上下文（FR-023）
            return AgentReply(
                reply=present.render(body),
                state="switch_failed",
            )
        order = body.get("data") or {}
        if order.get("user_id") and order["user_id"] != state.user_id:
            return AgentReply(reply="这笔订单不属于当前账号，无法为您操作。", state="forbidden")

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
                return AgentReply(
                    reply="请问是哪一笔订单呢？麻烦提供一下订单号。",
                    intent=intent,
                    state="need_order",
                )
            return await self._handle_write(state, intent, message, trace_id=trace_id)

        if intent is Intent.MANUAL:
            return await self._handoff(
                state, message, primary_intent="manual", trace_id=trace_id
            )

        if intent is Intent.COMPLAINT:
            # 投诉一律走人工：风险触发，跳过自助建议，优先级高
            return await self._handoff(
                state, message, primary_intent="complaint", trace_id=trace_id
            )

        if intent is Intent.POLICY:
            # 只有 policy 意图才走检索。业务动作不必查政策——既慢又答非所问。
            policy_reply = await self._try_policy(state, message, trace_id=trace_id)
            if policy_reply is not None:
                return policy_reply
            # 检索不到：不猜、不编，交回澄清或转人工
            return await self._clarify(state, message, trace_id=trace_id)

        # 无法识别可调用的工具：澄清优先，转人工是最后手段。
        # "AI 一时没理解"不等于"用户需要人工"——直接转会把人工成本吃光。
        return await self._clarify(state, message, trace_id=trace_id)

    async def _try_policy(
        self, state: SessionState, message: str, *, trace_id: str
    ) -> AgentReply | None:
        """检索命中则直接回答；未命中返回 None，交回澄清链路。"""
        if self.policy is None:
            return None

        order_created_at = None
        product_type = None
        if state.order_id:
            body = await self.tools.get_order(state.order_id, trace_id=trace_id)
            if body.get("code") == ResultCode.OK:
                order = body.get("data") or {}
                order_created_at = _parse_dt(order.get("created_at"))
                product_type = order.get("product_type")

        answer, _retrieval = await self.policy.answer(
            question=message,
            order_created_at=order_created_at,
            product_type=product_type,
            trace_id=trace_id,
        )

        if answer.decision is RetrievalDecision.AMBIGUOUS:
            state.clarify_round += 1
            return AgentReply(
                reply=answer.ambiguity_question or "您能再具体说一下是想问哪方面吗？",
                intent=None,
                state="clarifying",
            )

        if answer.decision is RetrievalDecision.MISS or not answer.answer:
            return None

        state.last_cited_clause_ids = list(answer.cited_clause_ids)
        state.policy_turns += 1
        return AgentReply(
            reply=answer.answer,
            intent=Intent.POLICY,
            tool_calls=["policy.search"],
            state="policy_answered",
        )

    CLARIFY_ROUND_LIMIT = 2

    async def _clarify(
        self, state: SessionState, message: str, *, trace_id: str
    ) -> AgentReply:
        state.clarify_round += 1

        if state.clarify_round > self.CLARIFY_ROUND_LIMIT:
            state.clarify_round = 0
            return await self._handoff(
                state, message, primary_intent="general", trace_id=trace_id
            )

        from app.agent.clarify import clarify_round_one, clarify_round_two
        from app.handoff.emotion import detect_emotion
        from app.models.handoff import Emotion

        if state.clarify_round == 1:
            negative = detect_emotion(message) is not Emotion.NEUTRAL
            reply = clarify_round_one(negative=negative)
        else:
            reply = clarify_round_two()

        return AgentReply(reply=reply, intent=None, state="clarifying")

    async def _handoff(
        self, state: SessionState, message: str, *, primary_intent: str, trace_id: str
    ) -> AgentReply:
        if self.coordinator is None:
            return AgentReply(reply="正在为您接入人工客服，请稍候。", state="handoff_pending")

        trigger = self.coordinator.detect(user_message=message)
        if not trigger.triggered:
            # 意图是 manual/complaint 但关键词没命中时，仍按该意图转接
            from app.models.handoff import TriggerResult, TriggerType

            trigger = TriggerResult.fire(
                TriggerType.RISK if primary_intent == "complaint" else TriggerType.USER_REQUEST,
                f"意图识别为 {primary_intent}",
                skip_self_service=(primary_intent == "complaint"),
            )

        outcome = await self.coordinator.initiate(
            session_id=state.session_id,
            user_id=state.user_id,
            user_message=message,
            primary_intent=primary_intent,
            trigger=trigger,
            order_id=state.order_id,
        )
        return AgentReply(
            reply=outcome.reply,
            intent=Intent.MANUAL if primary_intent == "manual" else Intent.COMPLAINT,
            state="handoff_queued",
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

        body = await self.tools.get_order(state.order_id, trace_id=trace_id)
        if body.get("code") != ResultCode.OK:
            return AgentReply(reply=present.render(body), intent=intent, state="inquiry_failed")

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

        return AgentReply(reply=reply, intent=intent, state="inquiry_answered")

    async def _handle_refund_progress(self, state: SessionState, *, trace_id: str) -> AgentReply:
        """查退款进度。固定话术，不调 LLM。"""
        if not state.order_id:
            return AgentReply(
                reply="请问是哪一笔订单呢？我帮您查一下退款进度。",
                intent=Intent.REFUND,
                state="need_order",
            )

        body = await self.tools.get_order(state.order_id, trace_id=trace_id)
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
            body = await self.tools.get_logistics(state.order_id, trace_id=trace_id)
            calls = ["logistics.get"]
        else:
            body = await self.tools.get_order(state.order_id, trace_id=trace_id)
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

        # 用户未说金额时按订单全额退款。金额取自库，不用前端快照。
        if intent is Intent.REFUND:
            body = await self.tools.get_order(state.order_id, trace_id=trace_id)
            if body.get("code") != ResultCode.OK:
                return AgentReply(reply=present.render(body), state="execute_failed")
            order = body.get("data") or {}
            matched = AMOUNT_PATTERN.search(message)
            amount = Decimal(matched.group(1)) if matched else Decimal(str(order.get("amount", "0")))
        else:
            amount = None

        pending = build_confirmation(
            intent=intent,
            order_id=state.order_id,
            amount=amount,
            payload={"address": {}} if intent is Intent.CHANGE_ADDRESS else {},
        )
        await self.sessions.set_pending(state.session_id, pending)
        return AgentReply(reply=pending.summary, intent=intent, state="awaiting_confirmation")

    # ---------- 确认处理 ----------

    async def _handle_confirmation(
        self, state: SessionState, pending: PendingConfirmation, message: str, *, trace_id: str
    ) -> AgentReply:
        if is_cancellation(message):
            await self.sessions.clear_pending(state.session_id)
            return AgentReply(reply="好的，已为您取消。", state="cancelled")

        if not is_confirmation(message):
            return AgentReply(
                reply=f"请您确认一下：{pending.summary}",
                state="awaiting_confirmation",
            )

        # 确认通过。执行前**始终**重新校验订单状态（FR-020）
        await self.sessions.clear_pending(state.session_id)

        if pending.action == Action.REFUND.value:
            check = await self.tools.get_order(pending.order_id, trace_id=trace_id)
            if check.get("code") != ResultCode.OK:
                return AgentReply(reply=present.render(check), state="execute_failed")

            req_id = f"refund:{pending.order_id}:{uuid.uuid4().hex}"
            body = await self.tools.create_refund(
                pending.order_id,
                pending.amount or Decimal("0"),
                request_id=req_id,
                trace_id=trace_id,
            )
            return AgentReply(
                reply=present.render(body),
                tool_calls=["order.get", "refund.create"],
                state="executed",
            )

        body = await self.tools.update_address(
            pending.order_id,
            pending.payload.get("address", {}),
            trace_id=trace_id,
        )
        return AgentReply(
            reply=present.render(body),
            tool_calls=["address.update"],
            state="executed",
        )


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value


def to_turn(turn: int, message: str, reply: AgentReply) -> TurnRecord:
    return TurnRecord(
        turn=turn,
        user_input=message,
        intent=reply.intent,
        source=reply.source,
        tool_calls=reply.tool_calls,
        reply=reply.reply,
        snapshot_mismatch=reply.snapshot_mismatch,
    )

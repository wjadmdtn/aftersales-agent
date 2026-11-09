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

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.agent import degrade, present
from app.agent.confirm import Action, build_confirmation, is_cancellation, is_confirmation
from app.agent.intent import IntentClassifier
from app.agent.session import SessionStore, snapshot_mismatch
from app.agent.tools.client import MockServiceClient
from app.models.agent import (
    READ_INTENTS,
    WRITE_INTENTS,
    Intent,
    IntentSource,
    PendingConfirmation,
    SessionState,
    TurnRecord,
)
from app.models.enums import ResultCode

# 与种子数据一致：ORD + 至少 4 位数字
ORDER_ID_PATTERN = re.compile(r"ORD\d{4,}")
AMOUNT_PATTERN = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱)?")


@dataclass
class AgentReply:
    reply: str
    intent: Intent | None = None
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
    ):
        self.classifier = classifier
        self.sessions = sessions
        self.tools = tools

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

        # ---- 3. 订单号切换 ----
        mentioned = ORDER_ID_PATTERN.search(message)
        if mentioned and mentioned.group(0) != state.order_id:
            switched = await self._switch_order(state, mentioned.group(0), trace_id=trace_id)
            if switched is not None:
                await self.sessions.save(state)
                return switched

        state.intent = result.intent
        reply = await self._dispatch(state, result, message, trace_id=trace_id)
        await self.sessions.save(state)
        return reply

    # ---------- 降级 ----------

    async def _handle_degraded(
        self, state: SessionState, message: str, error: Exception | None, *, trace_id: str
    ) -> AgentReply:
        kind = degrade.classify_degraded(message)
        if kind == degrade.WRITE_SUSPECTED:
            return AgentReply(
                reply=degrade.DEGRADED_WRITE_REPLY,
                source=IntentSource.DEGRADED,
                state="degraded_write_blocked",
            )

        # 只读放行：有绑定订单就帮他查一下，没有就给通用话术
        if state.order_id:
            body = await self.tools.get_order(state.order_id, trace_id=trace_id)
            rendered = present.render(body)
            return AgentReply(
                reply=f"{degrade.degraded_read_reply(True)}\n{rendered}",
                source=IntentSource.DEGRADED,
                tool_calls=["order.get"],
                state="degraded_read",
            )
        return AgentReply(
            reply=degrade.degraded_read_reply(False),
            source=IntentSource.DEGRADED,
            state="degraded_read",
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

        if intent in READ_INTENTS:
            return await self._handle_read(state, intent, trace_id=trace_id)

        if intent in WRITE_INTENTS:
            return await self._handle_write(state, intent, message, trace_id=trace_id)

        if intent is Intent.MANUAL:
            return AgentReply(
                reply="好的，正在为您转接人工客服，请稍候。",
                intent=intent,
                state="handoff_pending",
            )

        if intent is Intent.COMPLAINT:
            return AgentReply(
                reply="非常抱歉给您带来了不好的体验，我这边帮你记录下来并反馈处理。",
                intent=intent,
                state="complaint_logged",
            )

        return AgentReply(
            reply="抱歉，我没太理解您的意思，可以说得具体一些吗？",
            intent=intent,
            state="unclear",
        )

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

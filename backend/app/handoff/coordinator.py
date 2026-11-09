"""T025: 把转人工接入 Agent 主循环。

流程：意图识别后 → 判断是否触发 → 打包上下文 → 入队 → 回复话术。
静默约束在输出前检查，人工服务期间任何输出都会被拦截。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.config import Settings, settings
from app.handoff import triggers
from app.handoff.context_pack import build_context
from app.handoff.emotion import detect_emotion
from app.handoff.queue import HandoffQueue
from app.handoff.state import assert_ai_may_speak
from app.models.handoff import (
    AttemptedAction,
    HandoffState,
    ToolStatus,
    TriggerResult,
    TriggerType,
    TurnSignals,
)


@dataclass
class HandoffOutcome:
    reply: str
    state: HandoffState
    trigger_type: TriggerType
    events: list[str] = field(default_factory=list)


class HandoffCoordinator:
    def __init__(self, queue: HandoffQueue, cfg: Settings | None = None):
        self.queue = queue
        self.cfg = cfg or settings

    def detect(
        self,
        *,
        user_message: str,
        refund_amount: Decimal | None = None,
        last_tool_status: ToolStatus | None = None,
        tool_failure_streak: int = 0,
        llm_cannot_handle: bool = False,
    ) -> TriggerResult:
        signals = TurnSignals(
            user_message=user_message,
            refund_amount=refund_amount,
            last_tool_status=last_tool_status,
            tool_failure_streak=tool_failure_streak,
            llm_cannot_handle=llm_cannot_handle,
        )
        return triggers.evaluate(signals, self.cfg)

    async def initiate(
        self,
        *,
        session_id: str,
        user_id: str,
        user_message: str,
        primary_intent: str,
        trigger: TriggerResult,
        order_id: str | None = None,
        attempted: list[AttemptedAction] | None = None,
        refund_amount: Decimal | None = None,
        current_state: HandoffState = HandoffState.NORMAL,
    ) -> HandoffOutcome:
        # 人工服务期间不得有任何 AI 输出
        assert_ai_may_speak(current_state)

        emotion = detect_emotion(user_message, self.cfg)
        ctx = build_context(
            session_id=session_id,
            user_id=user_id,
            order_id=order_id,
            primary_intent=primary_intent,
            last_user_message=user_message,
            emotion=emotion,
            trigger=trigger,
            attempted=attempted,
            refund_amount=refund_amount,
            cfg=self.cfg,
        )
        is_new = await self.queue.enqueue(ctx)

        reply = self._pick_script(trigger.trigger_type)
        if not is_new:
            reply = self.cfg.handoff_scripts["queued_followup"]

        return HandoffOutcome(
            reply=reply,
            state=HandoffState.QUEUED,
            trigger_type=trigger.trigger_type,
            events=["triggered", "queued" if is_new else "requeued"],
        )

    def _pick_script(self, t: TriggerType) -> str:
        key = {
            TriggerType.USER_REQUEST: "user_request",
            TriggerType.RISK: "risk",
            TriggerType.SYSTEM_ERROR: "system_error",
            TriggerType.BOUNDARY: "boundary",
        }[t]
        return self.cfg.handoff_scripts[key]

    async def queued_reply(self, *, asking_time: bool = False) -> str:
        key = "queued_ask_time" if asking_time else "queued_followup"
        return self.cfg.handoff_scripts[key]

    async def human_accept(self, session_id: str) -> HandoffOutcome | None:
        ctx = await self.queue.accept(session_id)
        if ctx is None:
            return None
        return HandoffOutcome(
            reply="",
            state=HandoffState.HUMAN_SERVING,
            trigger_type=ctx.trigger_type,
            events=["accepted"],
        )

    async def human_decline(self, session_id: str) -> HandoffOutcome | None:
        ctx = await self.queue.decline(session_id)
        if ctx is None:
            return None
        # 拒绝后状态停在 HUMAN_SERVING，由上级处理；不回流给 AI
        return HandoffOutcome(
            reply=self.cfg.handoff_scripts["declined"],
            state=HandoffState.HUMAN_SERVING,
            trigger_type=ctx.trigger_type,
            events=["declined", "escalated"],
        )

    async def resolve(self, session_id: str) -> None:
        await self.queue.clear(session_id)

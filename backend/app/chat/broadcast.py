"""状态变更 → 广播。

两条铁律（宪法 III / plan D3）：
1. 状态只在 HandoffCoordinator 迁移成功之后写入并广播，没有第二个写入口
2. 转人工的业务判定（触发类型、优先级）仍由 handoff/ 决定，这里只搬运
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from app.chat.protocol import MessageType, Role, UI_STATE_OF, envelope
from app.chat.runtime import get_chat
from app.config import settings
from app.models.handoff import HandoffState, TriggerResult, TriggerType

logger = logging.getLogger(__name__)

Sender = Callable[[Any], None]


def state_payload(state) -> dict[str, Any]:
    """对外状态帧的载荷。四类界面状态由 UI_STATE_OF 固定映射。"""
    return {
        "session_id": state.session_id,
        "state": state.state.value,
        "ui_state": UI_STATE_OF[state.state],
        "agent_id": state.agent_id,
        "agent_name": state.agent_name,
        "order_id": state.order_id,
        "last_seq": state.last_seq,
    }


async def broadcast_state(session_id: str) -> None:
    rt = get_chat()
    state = await rt.registry.get(session_id)
    if state is None:
        return
    await rt.hub.publish_state(session_id, state_payload(state))


async def broadcast_queue_update(*, added: list[str] | None = None,
                                removed: list[str] | None = None) -> None:
    from app.api.handoff_routes import get_coordinator

    rt = get_chat()
    depth = await get_coordinator().queue.depth()
    await rt.hub.publish_queue_update(
        {"added": added or [], "removed": removed or [], "depth": depth}
    )


async def initiate_handoff(
    *,
    session_id: str,
    user_id: str,
    reason: str,
    send_frames: Sender,
) -> None:
    """快捷转人工的唯一入口（FR-005）。

    不经过 IntentClassifier——刻意不走 loop._handoff，
    那里的语义是"意图识别后转接"，而这里恰恰要跳过识别。
    """
    from app.api.agent_routes import get_loop

    rt = get_chat()
    state = await rt.registry.get(session_id)
    if state is None:
        raise KeyError(session_id)

    if state.state is HandoffState.RESOLVED:
        send_frames(
            envelope(
                MessageType.ERROR,
                session_id=session_id,
                payload={"code": "session_resolved", "message": "会话已结束", "retryable": False},
            )
        )
        return

    if state.state in (HandoffState.QUEUED, HandoffState.HUMAN_SERVING):
        # 幂等：只重放当前状态，不重复入队
        send_frames(
            envelope(MessageType.SESSION_STATE, session_id=session_id,
                     payload=state_payload(state))
        )
        return

    loop = get_loop()
    trigger = TriggerResult.fire(TriggerType.USER_REQUEST, reason)
    outcome = await loop.coordinator.initiate(
        session_id=session_id,
        user_id=user_id,
        user_message="转人工",
        primary_intent="manual",
        trigger=trigger,
        order_id=state.order_id,
    )
    await rt.registry.set_state(session_id, outcome.state)
    await broadcast_state(session_id)
    await broadcast_queue_update(added=[session_id])

    # 在对话流里留下一条记录，用户端才有"已为您接入"的可见痕迹
    if outcome.reply:
        seq = await rt.messages.next_seq(session_id)
        msg_id = uuid.uuid4().hex
        await rt.messages.append(session_id, role=Role.SYSTEM, text=outcome.reply, seq=seq)
        await rt.registry.touch_seq(session_id, seq)
        send_frames(
            envelope(
                MessageType.ASSISTANT_DELTA,
                session_id=session_id,
                seq=seq,
                payload={"msg_id": msg_id, "text_delta": outcome.reply, "streaming": False},
            )
        )
        send_frames(
            envelope(
                MessageType.ASSISTANT_DONE,
                session_id=session_id,
                seq=seq,
                payload={
                    "msg_id": msg_id,
                    "seq": seq,
                    "text": outcome.reply,
                    "intent": "manual",
                    "state": "handoff_queued",
                    "tool_calls": [],
                    "trace_id": None,
                    "degraded": False,
                },
            )
        )


async def on_queue_accepted(session_id: str, agent_id: str, agent_name: str) -> None:
    rt = get_chat()
    await rt.registry.set_state(
        session_id, HandoffState.HUMAN_SERVING, agent_id=agent_id, agent_name=agent_name
    )
    await broadcast_state(session_id)
    await broadcast_queue_update(removed=[session_id])


async def on_queue_declined(session_id: str) -> None:
    """拒绝后状态停在 HUMAN_SERVING（由上级处理），不回流给 AI。"""
    rt = get_chat()
    state = await rt.registry.get(session_id)
    if state is None:
        return
    if state.state is HandoffState.QUEUED:
        await rt.registry.set_state(session_id, HandoffState.HUMAN_SERVING)
    await broadcast_state(session_id)
    await broadcast_queue_update(removed=[session_id])


async def on_resolved(session_id: str) -> None:
    rt = get_chat()
    await rt.registry.set_state(session_id, HandoffState.RESOLVED)
    await broadcast_state(session_id)


async def ensure_session_state(session_id: str, *, user_id: str,
                               order_id: str | None) -> None:
    """建会话时写入 NORMAL，让 AI 服务期间也有可读状态。"""
    rt = get_chat()
    await rt.registry.ensure(session_id, user_id=user_id, order_id=order_id)


DEFAULT_AGENT_ID = settings.demo_agent_id
DEFAULT_AGENT_NAME = settings.demo_agent_name

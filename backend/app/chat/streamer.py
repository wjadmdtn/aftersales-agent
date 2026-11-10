"""用户消息入口：按会话状态分流。

    NORMAL         → AgentLoop.handle，结果按块推送
    QUEUED         → 固定话术，不调 Agent（用户还没排上，AI 不该再答业务问题）
    HUMAN_SERVING  → 只转发给 owner 人工，AI 静默
    RESOLVED       → 拒绝

"AI 人工服务期间不得输出"这条约束在 handoff/state.py 已有硬拦截，
这里不再重复判断，而是用分流结构让它根本走不到 AgentLoop。
"""
from __future__ import annotations

import logging
import re

from app.chat.hub import Connection
from app.chat.protocol import Envelope, MessageType, Role, envelope
from app.chat.runtime import get_chat
from app.config import settings
from app.models.handoff import HandoffState

logger = logging.getLogger(__name__)

# 按句末标点切块；保留标点，拼接后与原文一致
_SENTENCE_END = re.compile(r"(?<=[。！？；\n])")


def split_chunks(text: str) -> list[str]:
    if not text:
        return []
    parts = [p for p in _SENTENCE_END.split(text) if p]
    # 超长单句再按长度切，避免一次推一大坨
    out: list[str] = []
    for p in parts:
        if len(p) <= 60:
            out.append(p)
        else:
            out.extend(p[i : i + 60] for i in range(0, len(p), 60))
    return out


async def stream_reply(conn: Connection, session_id: str, text: str, *, meta: dict) -> None:
    """把一段已完成的回复按语义块推给用户。

    宪法 V：这是"分块推送"而不是真流式，必须如实标记 streaming=False。
    不 sleep、不伪造节奏——块与块之间只是让出事件循环。
    """
    rt = get_chat()
    # 一条回复共用一个 seq：这里先占号，done 与落库都复用
    seq = await rt.messages.next_seq(session_id)
    import uuid

    msg_id = uuid.uuid4().hex

    for chunk in split_chunks(text):
        conn.send(
            envelope(
                MessageType.ASSISTANT_DELTA,
                session_id=session_id,
                seq=seq,
                payload={"msg_id": msg_id, "text_delta": chunk, "streaming": False},
            )
        )
        await _yield_once()

    await _finish(conn, session_id, text, msg_id=msg_id, seq=seq, meta=meta)


async def _finish(conn: Connection, session_id: str, text: str, *, msg_id: str, seq: int,
                  meta: dict) -> None:
    rt = get_chat()
    await rt.messages.append(
        session_id,
        role=Role.ASSISTANT,
        text=text,
        seq=seq,
        streaming=False,
        trace_id=meta.get("trace_id"),
    )
    await rt.registry.touch_seq(session_id, seq)
    conn.send(
        envelope(
            MessageType.ASSISTANT_DONE,
            session_id=session_id,
            seq=seq,
            payload={
                "msg_id": msg_id,
                "seq": seq,
                "text": text,
                "intent": meta.get("intent"),
                "state": meta.get("state"),
                "tool_calls": meta.get("tool_calls", []),
                "trace_id": meta.get("trace_id"),
                "degraded": bool(meta.get("degraded")),
            },
        )
    )


async def _yield_once() -> None:
    import asyncio

    await asyncio.sleep(0)


async def handle_user_message(conn: Connection, frame: Envelope) -> None:
    session_id = conn.session_id
    assert session_id is not None
    rt = get_chat()
    state = await rt.registry.get(session_id)
    text = str((frame.payload or {}).get("text", "")).strip()
    local_id = (frame.payload or {}).get("local_id")

    if not text:
        return

    if state is not None and state.state is HandoffState.RESOLVED:
        conn.send(
            envelope(
                MessageType.ERROR,
                session_id=session_id,
                payload={"code": "session_resolved", "message": "会话已结束", "retryable": False},
            )
        )
        return

    user_msg = await rt.messages.append(
        session_id, role=Role.USER, text=text, sender_id=conn.user_id
    )
    await rt.registry.touch_seq(session_id, user_msg.seq)
    conn.send(
        envelope(
            MessageType.USER_MESSAGE_ACK,
            session_id=session_id,
            seq=user_msg.seq,
            payload={
                "local_id": local_id,
                "message": user_msg.model_dump(mode="json"),
            },
        )
    )

    current = state.state if state else HandoffState.NORMAL

    if current is HandoffState.QUEUED:
        await stream_reply(conn, session_id, settings.handoff_scripts["queued_followup"],
                           meta={"state": "queued"})
        return

    if current is HandoffState.HUMAN_SERVING:
        await _forward_to_human(conn, session_id, user_msg)
        return

    await _run_agent(conn, session_id, text)


async def _forward_to_human(conn: Connection, session_id: str, user_msg) -> None:
    """人工服务期间，用户消息只给 owner，不产生任何 assistant 帧。"""
    rt = get_chat()
    state = await rt.registry.get(session_id)
    payload = user_msg.model_dump(mode="json")
    env = envelope(MessageType.HUMAN_MESSAGE, session_id=session_id, seq=user_msg.seq,
                   payload=payload)
    for c in list(rt.hub.connections):
        if c.role == "agent" and state is not None and c.agent_id == state.agent_id:
            c.send(env)


async def _run_agent(conn: Connection, session_id: str, text: str) -> None:
    from app.api.agent_routes import get_loop
    from app.observability.trace import new_trace_id

    rt = get_chat()
    state = await rt.registry.get(session_id)
    trace_id = new_trace_id()
    try:
        reply = await get_loop().handle(
            session_id=session_id,
            user_id=conn.user_id or "u",
            message=text,
            trace_id=trace_id,
            order_id=state.order_id if state else None,
        )
    except Exception:
        logger.exception("agent loop failed session=%s", session_id)
        conn.send(
            envelope(
                MessageType.ERROR,
                session_id=session_id,
                payload={"code": "server_busy", "message": "系统繁忙，请重试", "retryable": True},
            )
        )
        return

    # AgentReply.state 是 agent 内部阶段名；对外状态仍由 registry 决定
    await stream_reply(
        conn,
        session_id,
        reply.reply,
        meta={
            "intent": reply.intent.value if reply.intent else None,
            "state": reply.state,
            "tool_calls": reply.tool_calls,
            "trace_id": trace_id,
            "degraded": reply.source.value == "degraded",
        },
    )

    # 会话过程中可能需要把新绑定的订单同步到 ChatState（用户消息里出现新订单号）
    if state is not None:
        from app.api.agent_routes import get_loop

        sess = await get_loop().sessions.get(session_id)
        if sess is not None and sess.order_id and sess.order_id != state.order_id:
            await rt.registry.set_order(session_id, sess.order_id)


async def handle_user_handoff(conn: Connection, frame: Envelope) -> None:
    """快捷转人工：不经过意图识别，不消耗 LLM。"""
    from app.chat.broadcast import initiate_handoff

    session_id = conn.session_id
    assert session_id is not None
    try:
        await initiate_handoff(
            session_id=session_id,
            user_id=conn.user_id or "u",
            reason="用户点击快捷转人工",
            send_frames=lambda env: conn.send(env),
        )
    except Exception:
        logger.exception("quick handoff failed session=%s", session_id)
        conn.send(
            envelope(
                MessageType.ERROR,
                session_id=session_id,
                payload={"code": "server_busy", "message": "转接失败，请重试", "retryable": True},
            )
        )


async def handle_agent_message(conn: Connection, frame: Envelope) -> None:
    """人工发消息：必须是自己接入的会话且处于人工服务中。"""
    rt = get_chat()
    payload = frame.payload or {}
    session_id = payload.get("session_id")
    text = str(payload.get("text", "")).strip()
    if not session_id or not text:
        return

    state = await rt.registry.get(session_id)
    if state is None:
        _err(conn, session_id, "invalid_session", "会话不存在")
        return
    if state.agent_id != conn.agent_id:
        _err(conn, session_id, "not_owner", "不是您接入的会话")
        return
    if state.state is not HandoffState.HUMAN_SERVING:
        _err(conn, session_id, "not_human_serving", "会话不在人工服务中")
        return

    msg = await rt.messages.append(
        session_id, role=Role.HUMAN, text=text, sender_id=conn.agent_id
    )
    await rt.registry.touch_seq(session_id, msg.seq)
    await rt.hub.publish(
        envelope(MessageType.HUMAN_MESSAGE, session_id=session_id, seq=msg.seq,
                 payload=msg.model_dump(mode="json")),
        to_session=session_id,
        to_agents=True,
    )


def _err(conn: Connection, session_id: str | None, code: str, message: str) -> None:
    conn.send(
        envelope(
            MessageType.ERROR,
            session_id=session_id,
            payload={"code": code, "message": message, "retryable": False},
        )
    )

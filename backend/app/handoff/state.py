"""T015: 状态机 + AI 静默硬拦截。

关键设计：`HUMAN_SERVING -> NORMAL` 这条边**不存在**。
与其在代码里写"拒绝时不要回 AI"，不如让这条路根本写不出来。

静默约束抛异常而不是返回 False——返回值容易被调用方忽略，
抛异常会直接让测试失败。
"""
from __future__ import annotations

from app.models.handoff import HandoffState

AI_SPEAKABLE = frozenset(
    {
        HandoffState.NORMAL,
        HandoffState.HANDOFF_REQUESTED,
        HandoffState.QUEUED,
    }
)

_ALLOWED: dict[HandoffState, frozenset[HandoffState]] = {
    HandoffState.NORMAL: frozenset(
        {HandoffState.NORMAL, HandoffState.HANDOFF_REQUESTED}
    ),
    HandoffState.HANDOFF_REQUESTED: frozenset(
        {HandoffState.HANDOFF_REQUESTED, HandoffState.QUEUED, HandoffState.NORMAL}
    ),
    HandoffState.QUEUED: frozenset({HandoffState.QUEUED, HandoffState.HUMAN_SERVING}),
    # 刻意没有 NORMAL：人工服务中不能回流给 AI
    HandoffState.HUMAN_SERVING: frozenset({HandoffState.RESOLVED}),
    HandoffState.RESOLVED: frozenset(),
}

HUMAN_ONLY = frozenset(
    {
        (HandoffState.QUEUED, HandoffState.HUMAN_SERVING),
        (HandoffState.HUMAN_SERVING, HandoffState.RESOLVED),
    }
)


class IllegalTransition(RuntimeError):
    def __init__(self, src: HandoffState, dst: HandoffState, reason: str):
        self.src, self.dst = src, dst
        super().__init__(f"{reason}：{src.value} -> {dst.value}")


class SilenceViolation(RuntimeError):
    """AI 在人工服务期间输出，属于事故。"""

    def __init__(self, state: HandoffState):
        self.state = state
        super().__init__(f"人工服务期间 AI 不得输出（当前状态 {state.value}）")


def assert_transition(src: HandoffState, dst: HandoffState, *, actor: str = "agent") -> None:
    if src is HandoffState.RESOLVED:
        raise IllegalTransition(src, dst, "已解决的会话不能再迁移")
    if dst not in _ALLOWED.get(src, frozenset()):
        raise IllegalTransition(src, dst, "不允许的迁移")
    if actor == "agent" and (src, dst) in HUMAN_ONLY:
        raise IllegalTransition(src, dst, "该迁移只能由人工侧驱动")


def assert_ai_may_speak(state: HandoffState) -> None:
    if state not in AI_SPEAKABLE:
        raise SilenceViolation(state)


def can_ai_speak(state: HandoffState) -> bool:
    return state in AI_SPEAKABLE

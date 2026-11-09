"""T014: 状态机与 AI 静默。"""

import pytest

from app.handoff.state import (
    IllegalTransition,
    SilenceViolation,
    assert_ai_may_speak,
    assert_transition,
    can_ai_speak,
)
from app.models.handoff import HandoffState as S


def test_normal_path():
    assert_transition(S.NORMAL, S.HANDOFF_REQUESTED)
    assert_transition(S.HANDOFF_REQUESTED, S.QUEUED)


def test_human_accepts():
    assert_transition(S.QUEUED, S.HUMAN_SERVING, actor="human")
    assert_transition(S.HUMAN_SERVING, S.RESOLVED, actor="human")


def test_agent_cannot_push_into_human_serving():
    with pytest.raises(IllegalTransition):
        assert_transition(S.QUEUED, S.HUMAN_SERVING, actor="agent")


def test_no_return_to_ai_edge():
    """HUMAN_SERVING -> NORMAL 这条边不存在：人工拒绝不得回流给 AI。"""
    with pytest.raises(IllegalTransition):
        assert_transition(S.HUMAN_SERVING, S.NORMAL, actor="human")
    with pytest.raises(IllegalTransition):
        assert_transition(S.HUMAN_SERVING, S.NORMAL, actor="agent")


def test_resolved_is_terminal():
    with pytest.raises(IllegalTransition):
        assert_transition(S.RESOLVED, S.NORMAL)


def test_cannot_skip_queue():
    with pytest.raises(IllegalTransition):
        assert_transition(S.NORMAL, S.HUMAN_SERVING, actor="human")


def test_ai_speakable_states():
    assert can_ai_speak(S.NORMAL)
    assert can_ai_speak(S.QUEUED)
    assert not can_ai_speak(S.HUMAN_SERVING)
    assert not can_ai_speak(S.RESOLVED)


def test_silence_raises():
    """静默约束必须抛异常，不能返回 False 被调用方忽略。"""
    with pytest.raises(SilenceViolation):
        assert_ai_may_speak(S.HUMAN_SERVING)


def test_silence_ok_in_normal():
    assert_ai_may_speak(S.NORMAL)

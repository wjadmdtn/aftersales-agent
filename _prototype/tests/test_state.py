import pytest

from app.handoff.state import (
    IllegalTransition,
    assert_transition,
    can_ai_speak,
    check_silence,
    is_queue_overdue,
)
from app.models import SessionState as S


def test_normal_happy_path():
    for src, dst in [
        (S.INTENT, S.COLLECTING),
        (S.COLLECTING, S.VALIDATING),
        (S.VALIDATING, S.DECIDING),
        (S.DECIDING, S.EXECUTING),
        (S.EXECUTING, S.DONE),
    ]:
        assert_transition(src, dst)


def test_terminal_state_is_frozen():
    with pytest.raises(IllegalTransition):
        assert_transition(S.DONE, S.INTENT)


def test_agent_cannot_push_session_into_human_serving():
    """Only the human side may accept a queued session."""
    assert_transition(S.QUEUED, S.HUMAN_SERVING, actor="human")
    with pytest.raises(IllegalTransition):
        assert_transition(S.QUEUED, S.HUMAN_SERVING, actor="agent")


def test_agent_cannot_close_human_session():
    with pytest.raises(IllegalTransition):
        assert_transition(S.HUMAN_SERVING, S.RESOLVED, actor="agent")


def test_risk_block_goes_to_queue():
    assert_transition(S.RISK_BLOCK, S.QUEUED, actor="human")


def test_ai_silence_zone():
    assert can_ai_speak(S.DECIDING) is True
    assert can_ai_speak(S.HUMAN_SERVING) is False
    assert can_ai_speak(S.QUEUED) is False


def test_ai_speaking_during_human_session_is_an_incident():
    check_silence(S.HUMAN_SERVING, agent_emitted=False)
    with pytest.raises(IllegalTransition):
        check_silence(S.HUMAN_SERVING, agent_emitted=True)


def test_cannot_skip_collecting():
    with pytest.raises(IllegalTransition):
        assert_transition(S.INTENT, S.EXECUTING)


def test_declined_human_does_not_return_to_ai():
    """Design decision: a declining human escalates upward, never back into an AI loop."""
    with pytest.raises(IllegalTransition):
        assert_transition(S.HUMAN_SERVING, S.COLLECTING)


def test_queue_overdue_uses_multiplier():
    assert is_queue_overdue(queued_seconds=100, expected_wait_seconds=120, multiplier=2) is False
    assert is_queue_overdue(queued_seconds=300, expected_wait_seconds=120, multiplier=2) is True

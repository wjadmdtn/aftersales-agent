"""Session state machine.

Two rules carry most of the weight:
  1. HUMAN_SERVING is a silence zone -- the agent must not emit anything once a human owns it.
  2. QUEUED -> HUMAN_SERVING only happens on an explicit human-side accept event, never from the agent.
"""
from __future__ import annotations

from app.models import SessionState, TERMINAL_STATES

AI_MODE_STATES = {
    SessionState.INTENT,
    SessionState.COLLECTING,
    SessionState.VALIDATING,
    SessionState.DECIDING,
    SessionState.EXECUTING,
}

# states in which the AI is allowed to speak to the user
AI_SPEAKABLE_STATES = AI_MODE_STATES

_ALLOWED: dict[SessionState, set[SessionState]] = {
    SessionState.INTENT: {
        SessionState.COLLECTING,
        SessionState.RISK_BLOCK,
        SessionState.HANDOFF_REQUESTED,
        SessionState.DONE,
    },
    SessionState.COLLECTING: {
        SessionState.COLLECTING,
        SessionState.VALIDATING,
        SessionState.RISK_BLOCK,
        SessionState.HANDOFF_REQUESTED,
    },
    SessionState.VALIDATING: {
        SessionState.COLLECTING,
        SessionState.DECIDING,
        SessionState.RISK_BLOCK,
        SessionState.HANDOFF_REQUESTED,
    },
    SessionState.DECIDING: {
        SessionState.EXECUTING,
        SessionState.HANDOFF_REQUESTED,
        SessionState.DONE,
    },
    SessionState.EXECUTING: {
        SessionState.EXECUTING,
        SessionState.DONE,
        SessionState.HANDOFF_REQUESTED,
    },
    SessionState.RISK_BLOCK: {SessionState.QUEUED},
    SessionState.HANDOFF_REQUESTED: {
        SessionState.QUEUED,
        # handoff collapsed before reaching a human (e.g. user withdrew); stay in AI mode
        SessionState.COLLECTING,
    },
    SessionState.QUEUED: {SessionState.HUMAN_SERVING},
    SessionState.HUMAN_SERVING: {SessionState.RESOLVED, SessionState.QUEUED},
    SessionState.DONE: set(),
    SessionState.RESOLVED: set(),
}

HUMAN_ONLY_TRANSITIONS = {
    (SessionState.QUEUED, SessionState.HUMAN_SERVING),
    (SessionState.HUMAN_SERVING, SessionState.RESOLVED),
    (SessionState.HUMAN_SERVING, SessionState.QUEUED),
    (SessionState.RISK_BLOCK, SessionState.QUEUED),
}


class IllegalTransition(Exception):
    def __init__(self, src: SessionState, dst: SessionState, reason: str):
        self.src, self.dst = src, dst
        super().__init__(f"{reason}: {src.value} -> {dst.value}")


def assert_transition(src: SessionState, dst: SessionState, *, actor: str = "agent") -> None:
    if src in TERMINAL_STATES:
        raise IllegalTransition(src, dst, "terminal state cannot transition")
    if dst not in _ALLOWED.get(src, set()):
        raise IllegalTransition(src, dst, "transition not allowed")
    if actor == "agent" and (src, dst) in HUMAN_ONLY_TRANSITIONS:
        raise IllegalTransition(src, dst, "transition must be driven by the human side")


def can_ai_speak(state: SessionState) -> bool:
    return state in AI_SPEAKABLE_STATES


def check_silence(state: SessionState, *, agent_emitted: bool) -> None:
    """Call after any agent-side output. Raising here is the audit hook for 'AI talked over a human'."""
    if agent_emitted and not can_ai_speak(state):
        raise IllegalTransition(state, state, "AI emitted output while human owns the session")


def is_queue_overdue(*, queued_seconds: float, expected_wait_seconds: int, multiplier: float) -> bool:
    return queued_seconds > expected_wait_seconds * multiplier

"""用例构造 helper。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.eval.schema import EvalCase, EvalPath  # noqa: E402


def business(
    cid: str,
    *,
    category: str,
    text: str,
    intent: str,
    mode: str = "execute",
    order_id: str = "ORD1002",
    notes: str | None = None,
) -> EvalCase:
    return EvalCase(
        id=cid,
        category=category,
        path=EvalPath.BUSINESS_ACTION,
        input=text,
        context={"order_id": order_id},
        expect={"intent": intent, "mode": mode},
        notes=notes,
    )


def clarify(
    cid: str, *, category: str, text: str, apology: bool, order_id: str | None = "ORD1002",
    intent: str = "unknown", notes: str | None = None
) -> EvalCase:
    return EvalCase(
        id=cid,
        category=category,
        path=EvalPath.CLARIFY,
        input=text,
        context={"order_id": order_id} if order_id else {},
        expect={"intent": intent, "state": "clarifying", "starts_with_apology": apology},
        notes=notes,
    )


def irrelevant(cid: str, *, text: str, intent: str = "unknown", notes: str | None = None) -> EvalCase:
    return EvalCase(
        id=cid,
        category="irrelevant",
        path=EvalPath.IRRELEVANT,
        input=text,
        context={"order_id": "ORD1002"},
        expect={"intent": intent, "state": "clarifying", "starts_with_apology": False},
        notes=notes,
    )


def handoff(
    cid: str,
    *,
    text: str,
    trigger_type: str,
    priority: str,
    category: str = "handoff",
    notes: str | None = None,
) -> EvalCase:
    return EvalCase(
        id=cid,
        category=category,
        path=EvalPath.HANDOFF,
        input=text,
        context={"order_id": "ORD1002"},
        expect={"triggered": True, "trigger_type": trigger_type, "priority": priority},
        notes=notes,
    )


def e2e(
    cid: str, *, turns: list[str], order_status: str, refund_count: int | None = None,
    order_id: str = "ORD1002", notes: str | None = None, **extra_expect
) -> EvalCase:
    """extra_expect 支持逐轮断言：
    first_turn_state / last_turn_state / any_turn_state / never_state
    / must_say / must_not_say / must_call
    """
    expect = {"order_status": order_status}
    if refund_count is not None:
        expect["refund_count"] = refund_count
    expect.update(extra_expect)
    return EvalCase(
        id=cid,
        category="e2e",
        path=EvalPath.E2E,
        turns=turns,
        context={"order_id": order_id},
        expect=expect,
        notes=notes,
    )

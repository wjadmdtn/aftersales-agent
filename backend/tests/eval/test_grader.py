"""判分逻辑测试。判分本身错了，评测结果全不可信。"""

import pytest

from app.agent.clarify import APOLOGY
from app.eval.grader import grade
from app.eval.schema import EvalCase, EvalPath


def _case(**kw) -> EvalCase:
    base = dict(id="t1", category="std", path=EvalPath.BUSINESS_ACTION, input="x", expect={"intent": "refund"})
    base.update(kw)
    return EvalCase(**base)


def test_business_pass():
    r = grade(_case(), {"intent": "refund"})
    assert r.passed


def test_business_fail_shows_both_values():
    r = grade(_case(), {"intent": "logistics"})
    assert not r.passed
    assert "退款" not in r.failures[0]  # 报告里是英文枚举值
    assert "refund" in r.failures[0] and "logistics" in r.failures[0]


def test_clarify_pass():
    c = _case(path=EvalPath.CLARIFY, expect={"state": "clarifying", "starts_with_apology": True})
    r = grade(c, {"state": "clarifying", "reply": APOLOGY + "方便说下具体情况吗？"})
    assert r.passed


def test_clarify_promise_always_fails():
    """硬约束：不管用例怎么写，话术出现业务承诺就失败。"""
    c = _case(path=EvalPath.CLARIFY, expect={"state": "clarifying"})
    r = grade(c, {"state": "clarifying", "reply": "可以帮您申请退款哦"})
    assert not r.passed
    assert any("业务承诺" in f for f in r.failures)


def test_clarify_apology_mismatch():
    c = _case(path=EvalPath.CLARIFY, expect={"state": "clarifying", "starts_with_apology": True})
    r = grade(c, {"state": "clarifying", "reply": "方便说下具体情况吗？"})
    assert not r.passed
    assert any("apology" in f for f in r.failures)


def test_clarify_should_not_apologize():
    c = _case(path=EvalPath.CLARIFY, expect={"state": "clarifying", "starts_with_apology": False})
    r = grade(c, {"state": "clarifying", "reply": "方便说下具体情况吗？"})
    assert r.passed


def test_handoff_reports_which_field():
    c = _case(
        path=EvalPath.HANDOFF,
        expect={"triggered": True, "trigger_type": "risk", "priority": "high"},
    )
    r = grade(c, {"triggered": True, "trigger_type": "user_request", "priority": "high"})
    assert not r.passed
    assert len(r.failures) == 1
    assert "trigger_type" in r.failures[0]


def test_handoff_all_match():
    c = _case(
        path=EvalPath.HANDOFF,
        expect={"triggered": True, "trigger_type": "risk", "priority": "high"},
    )
    assert grade(c, {"triggered": True, "trigger_type": "risk", "priority": "high"}).passed


def test_irrelevant_no_apology():
    c = _case(path=EvalPath.IRRELEVANT, input="今天天气不错", expect={"state": "clarifying", "starts_with_apology": False})
    assert grade(c, {"state": "clarifying", "reply": "方便说下具体情况吗？"}).passed
    r = grade(c, {"state": "clarifying", "reply": APOLOGY + "怎么了"})
    assert not r.passed


def test_e2e_checks_status_not_text():
    c = _case(path=EvalPath.E2E, input=None, turns=["我要退款", "确认"], expect={"order_status": "REFUNDED"})
    assert grade(c, {"order_status": "REFUNDED"}).passed
    assert not grade(c, {"order_status": "PAID"}).passed


def test_e2e_refund_count():
    c = _case(path=EvalPath.E2E, input=None, turns=["x"], expect={"order_status": "REFUNDED", "refund_count": 1})
    r = grade(c, {"order_status": "REFUNDED", "refund_count": 2})
    assert not r.passed
    assert any("refund_count" in f for f in r.failures)

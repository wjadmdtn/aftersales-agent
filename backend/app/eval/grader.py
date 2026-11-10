"""判分。

四套逻辑，不做通用 DSL——三类判分本质不同，万能断言语言只会让用例
更难写、报错更难读。

澄清类有两条**无视用例配置**的硬约束：
  1. 话术不得含业务承诺词（AI 无权替商家承诺）
  2. 道歉与否必须符合情绪判定
"""
from __future__ import annotations

from typing import Any

from app.agent.clarify import APOLOGY, FORBIDDEN_PROMISES
from app.eval.schema import EvalCase, EvalPath, GradeResult

CLARIFYING_STATE = "clarifying"
HANDOFF_STATE = "handoff_queued"


def grade(case: EvalCase, actual: dict[str, Any]) -> GradeResult:
    grader = {
        EvalPath.BUSINESS_ACTION: _grade_business,
        EvalPath.CLARIFY: _grade_clarify,
        EvalPath.HANDOFF: _grade_handoff,
        EvalPath.IRRELEVANT: _grade_irrelevant,
        EvalPath.E2E: _grade_e2e,
    }[case.path]
    failures = grader(case, actual)
    return GradeResult(
        case_id=case.id,
        path=case.path,
        category=case.category,
        passed=not failures,
        failures=failures,
        expected=case.expect,
        actual=actual,
    )


def _grade_business(case: EvalCase, actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected = case.expect["intent"]
    got = actual.get("intent")
    if got != expected:
        failures.append(f"intent：期望 {expected}，实际 {got}")

    # mode：EXECUTE / INQUIRY / QUERY 决定系统是执行还是只回答
    if "mode" in case.expect:
        want = case.expect["mode"]
        has = actual.get("mode")
        if has != want:
            failures.append(f"mode：期望 {want}，实际 {has}")
    return failures


def _grade_clarify(case: EvalCase, actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    if actual.get("state") != CLARIFYING_STATE:
        failures.append(f"state：期望 {CLARIFYING_STATE}，实际 {actual.get('state')}")

    reply = actual.get("reply") or ""

    # 硬约束 1：话术不得含业务承诺（无视用例配置）
    hit = next((w for w in FORBIDDEN_PROMISES if w in reply), None)
    if hit:
        failures.append(f"话术含业务承诺「{hit}」")

    # 硬约束 2：道歉与否必须符合预期
    if "starts_with_apology" in case.expect:
        want = bool(case.expect["starts_with_apology"])
        has = reply.startswith(APOLOGY)
        if has != want:
            failures.append(
                f"apology：期望{'道歉' if want else '不道歉'}，实际{'道歉' if has else '未道歉'}"
            )

    return failures


def _grade_handoff(case: EvalCase, actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected = case.expect

    got_triggered = actual.get("triggered", actual.get("state") == HANDOFF_STATE)
    if "triggered" in expected and got_triggered != expected["triggered"]:
        failures.append(f"triggered：期望 {expected['triggered']}，实际 {got_triggered}")

    if "trigger_type" in expected:
        got = actual.get("trigger_type")
        if got != expected["trigger_type"]:
            failures.append(f"trigger_type：期望 {expected['trigger_type']}，实际 {got}")

    if "priority" in expected:
        got = actual.get("priority")
        if got != expected["priority"]:
            failures.append(f"priority：期望 {expected['priority']}，实际 {got}")

    return failures


def _grade_irrelevant(case: EvalCase, actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected = case.expect

    if actual.get("state") != expected["state"]:
        failures.append(f"state：期望 {expected['state']}，实际 {actual.get('state')}")

    reply = actual.get("reply") or ""
    hit = next((w for w in FORBIDDEN_PROMISES if w in reply), None)
    if hit:
        failures.append(f"话术含业务承诺「{hit}」")

    if expected.get("starts_with_apology") is False and reply.startswith(APOLOGY):
        failures.append("apology：无关输入不应道歉")

    return failures


def _grade_e2e(case: EvalCase, actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected = case.expect

    if "order_status" in expected:
        got = actual.get("order_status")
        if got != expected["order_status"]:
            failures.append(f"order_status：期望 {expected['order_status']}，实际 {got}")

    if "refund_count" in expected:
        got = actual.get("refund_count")
        if got != expected["refund_count"]:
            failures.append(f"refund_count：期望 {expected['refund_count']}，实际 {got}")

    return failures

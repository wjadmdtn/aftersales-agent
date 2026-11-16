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
        EvalPath.POLICY: _grade_policy,
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
        input=case.input,
        turns=case.turns,
        notes=case.notes,
    )


def _grade_business(case: EvalCase, actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected = case.expect["intent"]
    got = actual.get("intent")
    if got != expected:
        failures.append(f"intent：期望 {expected}，实际 {got}")

    # mode：EXECUTE / INQUIRY / QUERY 决定系统是执行还是只回答
    # mode 只在有可执行诉求时才有意义。unknown = 诉求不明，
    # 标个 execute 本身就自相矛盾（要求执行一个不存在的诉求），因此不比对。
    if "mode" in case.expect and expected != "unknown":
        want = case.expect["mode"]
        has = actual.get("mode")
        if has != want:
            failures.append(f"mode：期望 {want}，实际 {has}")
    return failures


def _grade_clarify(case: EvalCase, actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    # 意图必须真的被判成 unknown。只断言 state=clarifying 不够——
    # 模型把"太差了"误判成 refund，只要没弹确认、走了澄清，旧断言照样通过。
    want_intent = case.expect.get("intent", "unknown")
    got_intent = actual.get("intent")
    if got_intent != want_intent:
        failures.append(f"intent：期望 {want_intent}，实际 {got_intent}")

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

    # 不该转人工的用例（投诉/差评）：意图必须判成 unknown，
    # 且系统要给出安抚，不能只是"没转人工"就算对。
    if expected.get("intent"):
        got_intent = actual.get("intent")
        if got_intent != expected["intent"]:
            failures.append(f"intent：期望 {expected['intent']}，实际 {got_intent}")

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

    want_intent = expected.get("intent", "unknown")
    got_intent = actual.get("intent")
    if got_intent != want_intent:
        failures.append(f"intent：期望 {want_intent}，实际 {got_intent}")

    if actual.get("state") != expected["state"]:
        failures.append(f"state：期望 {expected['state']}，实际 {actual.get('state')}")

    reply = actual.get("reply") or ""
    hit = next((w for w in FORBIDDEN_PROMISES if w in reply), None)
    if hit:
        failures.append(f"话术含业务承诺「{hit}」")

    if expected.get("starts_with_apology") is False and reply.startswith(APOLOGY):
        failures.append("apology：无关输入不应道歉")

    return failures


def _grade_policy(case: EvalCase, actual: dict[str, Any]) -> list[str]:
    """政策问答：意图必须是 policy；给了期望条款则要求命中。

    期望 state=handoff_queued 的用例（知识库确实没有答案）要求转人工，
    不得编造回答——这是规范 005 FR-011 的硬约束。
    """
    failures: list[str] = []

    if actual.get("intent") != "policy":
        failures.append(f"intent：期望 policy，实际 {actual.get('intent')}")

    want_state = case.expect.get("state")
    if want_state and actual.get("state") != want_state:
        failures.append(f"state：期望 {want_state}，实际 {actual.get('state')}")

    want = case.expect.get("cited_clause")
    if want:
        cited = actual.get("cited_clause_ids") or []
        if want not in cited:
            failures.append(f"cited_clause：期望 {want}，实际 {cited or '未引用'}")

    return failures


def _grade_e2e(case: EvalCase, actual: dict[str, Any]) -> list[str]:
    """端到端：最终业务状态必须是真，关键轮次的交互约束也要满足。

    只判最终状态测不出"有没有让用户确认"——AI 直接退款也能变 REFUNDED，
    但那违反了写操作必须二次确认的设计。
    """
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

    turns = actual.get("turns") or []

    # 首轮必须让用户确认，不能直接执行
    if expected.get("first_turn_state"):
        want = expected["first_turn_state"]
        got = turns[0]["state"] if turns else None
        if got != want:
            failures.append(f"第 1 轮 state：期望 {want}，实际 {got}")

    # 最后轮的状态（转人工 / 已执行 / 澄清…）
    if expected.get("last_turn_state"):
        want = expected["last_turn_state"]
        got = turns[-1]["state"] if turns else None
        if got != want:
            failures.append(f"末轮 state：期望 {want}，实际 {got}")

    # 全程任一轮出现过某状态即算命中（如"曾弹过确认"）
    if expected.get("any_turn_state"):
        want = expected["any_turn_state"]
        if not any(t["state"] == want for t in turns):
            got = [t["state"] for t in turns]
            failures.append(f"全程未出现 state={want}，实际 {got}")

    # 全程都不得出现某状态（如投诉类不该转人工）
    if expected.get("never_state"):
        want = expected["never_state"]
        hit = [i + 1 for i, t in enumerate(turns) if t["state"] == want]
        if hit:
            failures.append(f"不应出现 state={want}，第 {hit} 轮出现了")

    # 回复中必须出现/不得出现的词（承诺、道歉、转人工提示）
    if expected.get("must_say"):
        for w in expected["must_say"]:
            if not any(w in (t.get("reply") or "") for t in turns):
                failures.append(f"全程回复未出现「{w}」")
    if expected.get("must_not_say"):
        for w in expected["must_not_say"]:
            hit = [i + 1 for i, t in enumerate(turns) if w in (t.get("reply") or "")]
            if hit:
                failures.append(f"回复不应出现「{w}」，第 {hit} 轮出现了")

    # 只约束首轮：确认前不得先宣布已执行
    if expected.get("first_reply_must_not_say"):
        first = (turns[0].get("reply") or "") if turns else ""
        for w in expected["first_reply_must_not_say"]:
            if w in first:
                failures.append(f"首轮回复不应出现「{w}」")

    # 必须调用过某工具
    if expected.get("must_call"):
        called = {c for t in turns for c in (t.get("tool_calls") or [])}
        for tool in expected["must_call"]:
            if tool not in called:
                failures.append(f"未调用工具 {tool}，实际调用 {sorted(called) or '无'}")

    return failures

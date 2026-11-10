"""指标聚合。

注意「过度转接率」单独成项：它衡量的是人工成本损耗，
与识别准确率不是一回事——系统可能"确实没识别出意图"却把用户
直接推给人工，准确率看不出来，这个指标能。
"""
from __future__ import annotations

from app.eval.schema import EvalPath, GradeResult, MetricLine


def _rate(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 0.0


def compute_metrics(results: list[GradeResult]) -> list[MetricLine]:
    metrics: list[MetricLine] = []

    def add(name: str, subset: list[GradeResult]) -> None:
        if not subset:
            return
        passed = sum(1 for r in subset if r.passed)
        metrics.append(
            MetricLine(
                name=name,
                value=_rate(passed, len(subset)),
                passed=passed,
                total=len(subset),
            )
        )

    by_path = {p: [r for r in results if r.path is p] for p in EvalPath}

    add("业务动作准确率", by_path[EvalPath.BUSINESS_ACTION])
    for cat in _categories(by_path[EvalPath.BUSINESS_ACTION]):
        add(f"业务动作-{cat}", [r for r in by_path[EvalPath.BUSINESS_ACTION] if r.category == cat])

    add("澄清正确率", by_path[EvalPath.CLARIFY])
    add("无关输入正确率", by_path[EvalPath.IRRELEVANT])
    add("转人工正确率", by_path[EvalPath.HANDOFF])
    add("端到端成功率", by_path[EvalPath.E2E])
    add("政策问答准确率", by_path[EvalPath.POLICY])
    # 政策路由：业务意图被误判为 policy 的比例
    not_policy = [r for r in results if r.category == "not_policy"]
    if not_policy:
        wrong = sum(1 for r in not_policy if r.actual.get("intent") == "policy")
        metrics.append(
            MetricLine(
                name="政策误路由率",
                value=_rate(wrong, len(not_policy)),
                passed=wrong,
                total=len(not_policy),
            )
        )

    # 总体：所有非 e2e 用例的通过率
    general = [r for r in results if r.path is not EvalPath.E2E]
    add("意图识别总体", general)

    # 过度转接率：澄清类里实际被转人工的比例
    clarify = by_path[EvalPath.CLARIFY]
    if clarify:
        over = sum(1 for r in clarify if r.actual.get("state") == "handoff_queued")
        metrics.append(
            MetricLine(
                name="过度转接率",
                value=_rate(over, len(clarify)),
                passed=over,
                total=len(clarify),
            )
        )

    # 转人工误转 / 漏转分开算
    handoff = by_path[EvalPath.HANDOFF]
    should_handoff = [r for r in handoff if r.expected.get("triggered") is True]
    should_not = [r for r in handoff if r.expected.get("triggered") is False]

    if should_handoff:
        missed = sum(1 for r in should_handoff if r.actual.get("state") != "handoff_queued")
        metrics.append(
            MetricLine(
                name="转人工漏转率",
                value=_rate(missed, len(should_handoff)),
                passed=missed,
                total=len(should_handoff),
            )
        )
    if should_not:
        wrong = sum(1 for r in should_not if r.actual.get("state") == "handoff_queued")
        metrics.append(
            MetricLine(
                name="转人工误转率",
                value=_rate(wrong, len(should_not)),
                passed=wrong,
                total=len(should_not),
            )
        )

    # 硬性项：出现一次即为 1.0
    promise_leak = sum(1 for r in results if any("业务承诺" in f for f in r.failures))
    metrics.append(MetricLine(name="话术承诺泄露", value=float(promise_leak), total=len(results)))

    return metrics


def _categories(results: list[GradeResult]) -> list[str]:
    seen: list[str] = []
    for r in results:
        if r.category not in seen:
            seen.append(r.category)
    return seen

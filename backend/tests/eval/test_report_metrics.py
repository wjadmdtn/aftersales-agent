"""报告与指标聚合。"""

from app.eval.metrics import compute_metrics
from app.eval.report import render_markdown
from app.eval.schema import EvalPath, EvalReport, GradeResult


def _r(cid, path, category="c", passed=True, actual=None, expected=None, failures=None):
    return GradeResult(
        case_id=cid,
        path=path,
        category=category,
        passed=passed,
        failures=failures or [],
        expected=expected or {},
        actual=actual or {},
    )


def test_over_handoff_is_separate_metric():
    """过度转接率单独成项，不混进准确率。"""
    results = [
        _r("c1", EvalPath.CLARIFY, passed=True, actual={"state": "clarifying"}),
        _r("c2", EvalPath.CLARIFY, passed=False, actual={"state": "handoff_queued"}),
        _r("c3", EvalPath.CLARIFY, passed=True, actual={"state": "clarifying"}),
        _r("c4", EvalPath.CLARIFY, passed=True, actual={"state": "clarifying"}),
    ]
    metrics = {m.name: m for m in compute_metrics(results)}
    assert metrics["过度转接率"].value == 0.25
    assert metrics["澄清正确率"].value == 0.75


def test_false_negative_and_false_positive_separate():
    results = [
        _r("h1", EvalPath.HANDOFF, expected={"triggered": True}, actual={"state": "clarifying"}, passed=False),
        _r("h2", EvalPath.HANDOFF, expected={"triggered": True}, actual={"state": "handoff_queued"}),
        _r("h3", EvalPath.HANDOFF, expected={"triggered": False}, actual={"state": "handoff_queued"}, passed=False),
        _r("h4", EvalPath.HANDOFF, expected={"triggered": False}, actual={"state": "clarifying"}),
    ]
    metrics = {m.name: m for m in compute_metrics(results)}
    assert metrics["转人工漏转率"].value == 0.5
    assert metrics["转人工误转率"].value == 0.5


def test_promise_leak_counts():
    results = [
        _r("c1", EvalPath.CLARIFY, passed=False, failures=["话术含业务承诺「退款」"]),
        _r("c2", EvalPath.CLARIFY, passed=True),
    ]
    metrics = {m.name: m for m in compute_metrics(results)}
    assert metrics["话术承诺泄露"].value == 1.0


def test_report_has_stub_warning():
    report = EvalReport(
        backend="stub",
        case_count=1,
        elapsed_seconds=1.0,
        metrics=[],
        results=[],
    )
    md = render_markdown(report)
    assert "不可对外引用" in md


def test_report_marks_backend():
    real = EvalReport(backend="real", model="qwen-plus", case_count=1, elapsed_seconds=1.0, metrics=[], results=[])
    md = render_markdown(real)
    assert "**real**" in md
    assert "qwen-plus" in md
    assert "不可对外引用" not in md

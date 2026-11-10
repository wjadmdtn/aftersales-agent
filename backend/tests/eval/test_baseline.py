"""基线比较与分类阈值。

重点：噪声级波动不能触发阻断，硬性项一次就阻断。
"""

import json

import pytest

from app.eval.baseline import THRESHOLDS, compare, load_baseline, save_baseline
from app.eval.schema import EvalPath, EvalReport, GradeResult, MetricLine


def _report(**metrics) -> EvalReport:
    return EvalReport(
        backend="real",
        case_count=10,
        elapsed_seconds=1.0,
        metrics=[MetricLine(name=k, value=v) for k, v in metrics.items()],
        results=[],
    )


def test_one_point_noise_not_blocked():
    """总体指标只降 1 个百分点，不该阻断。"""
    r = _report(意图识别总体=0.86)
    reasons, _ = compare(r, {"意图识别总体": 0.87})
    assert reasons == []


def test_three_point_drop_blocked():
    r = _report(意图识别总体=0.84)
    reasons, _ = compare(r, {"意图识别总体": 0.87})
    assert len(reasons) == 1
    assert "意图识别总体" in reasons[0]


def test_single_metric_tighter_threshold():
    """单项阈值 5 点：降 4 点不阻断，降 6 点阻断。"""
    r = _report(澄清正确率=0.88)
    assert compare(r, {"澄清正确率": 0.92})[0] == []

    r2 = _report(澄清正确率=0.86)
    assert len(compare(r2, {"澄清正确率": 0.92})[0]) == 1


def test_over_handoff_increase_blocked():
    """过度转接率上升超阈值要阻断。"""
    r = _report(过度转接率=0.12)
    reasons, _ = compare(r, {"过度转接率": 0.05})
    assert len(reasons) == 1


def test_hard_metric_zero_tolerance():
    """硬性项只要非零就阻断，不管基线多少。"""
    r = _report(话术承诺泄露=1.0)
    reasons, _ = compare(r, {"话术承诺泄露": 0.0})
    assert any("硬性项" in x for x in reasons)


def test_hard_metric_blocks_even_if_baseline_also_nonzero():
    r = _report(话术承诺泄露=1.0)
    reasons, _ = compare(r, {"话术承诺泄露": 1.0})
    assert reasons, "硬性项不该因为基线也是坏的就不报"


def test_block_message_has_values():
    r = _report(意图识别总体=0.80)
    reasons, _ = compare(r, {"意图识别总体": 0.90})
    msg = reasons[0]
    assert "90.0%" in msg and "80.0%" in msg and "3%" in msg


def test_unknown_metric_ignored():
    r = _report(某个新指标=0.5)
    assert compare(r, {"某个新指标": 0.9})[0] == []


def test_baseline_roundtrip(tmp_path):
    r = _report(意图识别总体=0.87)
    p = tmp_path / "b.json"
    save_baseline(p, r)
    loaded = load_baseline(p)
    assert loaded["意图识别总体"] == 0.87


def test_baseline_missing(tmp_path):
    from app.eval.baseline import BaselineError

    with pytest.raises(BaselineError):
        load_baseline(tmp_path / "nope.json")


def test_baseline_bad_format(tmp_path):
    from app.eval.baseline import BaselineError

    p = tmp_path / "b.json"
    p.write_text("{bad", encoding="utf-8")
    with pytest.raises(BaselineError):
        load_baseline(p)


def test_thresholds_cover_hard_items():
    for name in ("话术承诺泄露", "静默违规", "降级期写操作"):
        assert name in THRESHOLDS
        assert THRESHOLDS[name].amount == 0.0

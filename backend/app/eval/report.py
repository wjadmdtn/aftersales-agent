"""报告生成：Markdown（给人看，可直接贴 README）+ JSON（给机器比对）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.eval.schema import EvalReport, MetricLine

_PERCENT_METRICS = {
    "意图识别总体",
    "业务动作准确率",
    "澄清正确率",
    "无关输入正确率",
    "转人工正确率",
    "端到端成功率",
    "过度转接率",
    "转人工漏转率",
    "转人工误转率",
}


def _is_percent(name: str) -> bool:
    if name in _PERCENT_METRICS:
        return True
    # 业务动作-standard / adversarial 这类分项也是百分比
    return any(name.startswith(p) for p in ("业务动作-",))


def _fmt(metric: MetricLine) -> str:
    if _is_percent(metric.name):
        return f"{metric.value:.1%}"
    return f"{metric.value:g} 次"


def render_markdown(report: EvalReport) -> str:
    lines: list[str] = []
    backend_label = "**real**" if report.backend == "real" else "**stub（桩）**"

    lines.append("## 评测结果")
    lines.append("")
    header = f"后端：{backend_label}"
    if report.model:
        header += f" ({report.model})"
    header += f" | 用例 {report.case_count} 条 | 耗时 {report.elapsed_seconds:.1f}s"
    lines.append(header)
    lines.append("")

    if report.backend == "stub":
        lines.append("> ⚠ 本次使用**桩实现**，准确率不代表真实效果，**不可对外引用**。")
        lines.append("> 对外数字请用 `--llm=real` 重新运行。")
        lines.append("")

    lines.append("| 指标 | 结果 | 基线 | 变化 |")
    lines.append("|---|---|---|---|")
    for m in report.metrics:
        passed_part = f" ({m.passed}/{m.total})" if m.passed is not None and m.total is not None else ""
        base = _fmt(MetricLine(name=m.name, value=m.baseline)) if m.baseline is not None else "—"
        delta = f"{m.delta:+.2f}" if m.delta is not None else "—"
        lines.append(f"| {m.name} | {_fmt(m)}{passed_part} | {base} | {delta} |")
    lines.append("")

    if report.blocked:
        lines.append("### ❌ 回归阻断")
        lines.append("")
        for r in report.block_reasons:
            lines.append(f"- {r}")
        lines.append("")

    failures = report.failures
    if failures:
        lines.append(f"### 失败样本（{len(failures)} 条）")
        lines.append("")
        lines.append("| ID | 类别 | 失败项 |")
        lines.append("|---|---|---|")
        for f in failures:
            joined = "；".join(f.failures).replace("|", "\\|")
            lines.append(f"| `{f.case_id}` | {f.category} | {joined} |")
    else:
        lines.append("### 全部通过")
    lines.append("")

    return "\n".join(lines)


def render_json(report: EvalReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)


def write_report(report: EvalReport, out_dir: str | Path) -> tuple[Path, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    md = d / f"report-{stamp}.md"
    js = d / f"report-{stamp}.json"
    md.write_text(render_markdown(report), encoding="utf-8")
    js.write_text(render_json(report), encoding="utf-8")
    return md, js

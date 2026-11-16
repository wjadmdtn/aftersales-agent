"""评测结果可视化：生成单文件 HTML，跑完自动打开浏览器。

为什么单独成页而不是塞进 Markdown：
失败样本要一眼看出"问了什么、期望什么、实际做了什么"，表格里三列并排
比 Markdown 的横排好读得多；再加上筛选和搜索，定位问题快很多。

零依赖：内联 CSS/JS，不引 CDN——内网环境打不开外链会把页面卡死。
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from app.eval.schema import EvalReport, GradeResult

_PATH_LABEL = {
    "business_action": "业务动作",
    "clarify": "反问引导",
    "irrelevant": "无关输入",
    "handoff": "转人工",
    "policy": "政策问答",
    "e2e": "端到端",
}

_INTENT_LABEL = {
    "refund": "退款",
    "logistics": "物流",
    "change_address": "改地址",
    "query_order": "查订单",
    "exchange": "换货",
    "manual": "要人工",
    "policy": "政策",
    "unknown": "诉求不明",
}

_MODE_LABEL = {
    "execute": "执行",
    "inquiry": "咨询",
    "query": "查询",
    "need_context": "缺信息",
}


def _e(v) -> str:
    return html.escape(str(v)) if v is not None else ""


def _input_text(r: GradeResult) -> str:
    if r.turns:
        return " → ".join(r.turns)
    return r.input or ""


def _expected_text(r: GradeResult) -> str:
    parts = []
    if "intent" in r.expected:
        parts.append(_INTENT_LABEL.get(r.expected["intent"], r.expected["intent"]))
    if "mode" in r.expected:
        parts.append(_MODE_LABEL.get(r.expected["mode"], r.expected["mode"]))
    if "cited_clause" in r.expected:
        parts.append(f"条款 {r.expected['cited_clause']}")
    if "state" in r.expected:
        parts.append(str(r.expected["state"]))
    if "triggered" in r.expected:
        parts.append("转人工" if r.expected["triggered"] else "不转人工")
    if "order_status" in r.expected:
        parts.append(f"订单 {r.expected['order_status']}")
    return " / ".join(parts) or json.dumps(r.expected, ensure_ascii=False)


def _actual_text(r: GradeResult) -> str:
    a = r.actual or {}
    parts = []
    if a.get("intent"):
        parts.append(_INTENT_LABEL.get(a["intent"], a["intent"]))
    if a.get("mode"):
        parts.append(_MODE_LABEL.get(a["mode"], a["mode"]))
    if a.get("state"):
        parts.append(str(a["state"]))
    if a.get("cited_clause_ids"):
        parts.append("条款 " + ",".join(a["cited_clause_ids"]))
    if a.get("order_status"):
        parts.append(f"订单 {a['order_status']}")
    if a.get("error"):
        parts.append("ERROR")
    return " / ".join(parts) or "—"


def _metric_cards(report: EvalReport) -> str:
    cards = []
    is_stub = report.backend != "real"
    for m in report.metrics:
        if is_stub:
            # 桩不展示百分比：它按关键词匹配，准确率没有意义，
            # 展示只会误导人。只给通过条数，看链路跑通没有。
            val = f"{m.passed if m.passed is not None else '—'}"
            sub = f"/ {m.total}" if m.total else ""
            cards.append(
                f'<div class="card stub"><div class="k">{_e(m.name)}</div>'
                f'<div class="v">{val}</div><div class="s">通过 {sub}</div></div>'
            )
            continue

        pct = any(k in m.name for k in ("率", "准确率", "总体")) or m.name.startswith("业务动作-")
        val = f"{m.value:.1%}" if pct else f"{m.value:g}"
        bad = pct and m.value < 0.9 and "泄露" not in m.name
        if m.name == "话术承诺泄露" and m.value > 0:
            bad = True
        cls = "bad" if bad else ""
        sub = f"{m.passed}/{m.total}" if m.passed is not None and m.total else ""
        cards.append(
            f'<div class="card {cls}"><div class="k">{_e(m.name)}</div>'
            f'<div class="v">{val}</div><div class="s">{sub}</div></div>'
        )
    return "".join(cards)


def _row(r: GradeResult) -> str:
    status = "pass" if r.passed else "fail"
    badge = "通过" if r.passed else "失败"
    fails = "；".join(r.failures)
    reply = (r.actual or {}).get("reply") or ""
    note = f'<div class="note">{_e(r.notes)}</div>' if r.notes else ""
    return f"""<tr class="{status}" data-path="{_e(r.path.value)}" data-status="{status}"
     data-text="{_e(_input_text(r))} {_e(r.case_id)}">
  <td class="id">{_e(r.case_id)}</td>
  <td class="inp">{_e(_input_text(r))}{note}</td>
  <td class="exp">{_e(_expected_text(r))}</td>
  <td class="act">{_e(_actual_text(r))}</td>
  <td><span class="badge {status}">{badge}</span></td>
  <td class="why">{_e(fails)}</td>
</tr>
<tr class="detail {status}"><td colspan="6">
  <div class="reply"><b>实际回复：</b>{_e(reply) or '（空）'}</div>
  <div class="raw"><b>expect</b> {_e(json.dumps(r.expected, ensure_ascii=False))}<br>
  <b>actual</b> {_e(json.dumps(r.actual, ensure_ascii=False))}</div>
</td></tr>"""


def render_html(report: EvalReport) -> str:
    rows = "".join(_row(r) for r in report.results)
    fails = sum(1 for r in report.results if not r.passed)
    backend = "真实模型" if report.backend == "real" else "桩（不可对外引用）"
    paths = sorted({r.path.value for r in report.results})
    tabs = '<button class="tab on" data-p="all">全部 %d</button>' % len(report.results)
    for p in paths:
        n = sum(1 for r in report.results if r.path.value == p)
        f = sum(1 for r in report.results if r.path.value == p and not r.passed)
        tabs += f'<button class="tab" data-p="{p}">{_PATH_LABEL.get(p, p)} {n}<i>{f}</i></button>'

    blocked = ""
    if report.blocked:
        blocked = '<div class="blocked">⛔ 回归阻断：' + "<br>".join(
            _e(x) for x in report.block_reasons
        ) + "</div>"

    return f"""<!doctype html><html lang="zh"><meta charset="utf-8">
<title>评测报告 {_e(report.model or report.backend)}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
background:#f5f6f8;color:#1f2328}}
header{{background:#fff;padding:20px 28px;border-bottom:1px solid #e5e7eb}}
h1{{margin:0 0 4px;font-size:20px}}
.meta{{color:#656d76;font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
gap:10px;padding:18px 28px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px}}
.card .k{{font-size:12px;color:#656d76;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.card .v{{font-size:20px;font-weight:600;margin-top:2px}}
.card .s{{font-size:11px;color:#8c959f}}
.card.bad .v{{color:#cf222e}}
.blocked{{margin:0 28px 12px;padding:12px;background:#ffebe9;border:1px solid #ff8182;
border-radius:8px;color:#82071e}}
.bar{{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid #e5e7eb;
padding:10px 28px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.tab{{border:1px solid #d0d7de;background:#fff;border-radius:16px;padding:5px 12px;
cursor:pointer;font-size:13px}}
.tab.on{{background:#1f2328;color:#fff;border-color:#1f2328}}
.tab i{{font-style:normal;margin-left:5px;color:#cf222e;font-weight:600}}
.tab.on i{{color:#ffb4bc}}
.tab.failonly{{margin-left:4px;border-color:#ff8182;color:#cf222e}}
.tab.failonly.on{{background:#cf222e;border-color:#cf222e;color:#fff}}
.tab.failonly.on i{{color:#ffd7db}}
input[type=search]{{margin-left:auto;padding:6px 12px;border:1px solid #d0d7de;
border-radius:6px;width:260px;font-size:13px}}
table{{width:100%;border-collapse:collapse;background:#fff}}
th{{text-align:left;font-size:12px;color:#656d76;font-weight:500;padding:8px 12px;
border-bottom:1px solid #e5e7eb;background:#fafbfc;position:sticky;top:53px}}
td{{padding:9px 12px;border-bottom:1px solid #f0f1f3;vertical-align:top;font-size:13px}}
tr.fail td{{background:#fff8f8}}
td.id{{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#57606a;white-space:nowrap}}
td.inp{{font-weight:500;max-width:200px}}
td.exp,td.act{{color:#57606a;white-space:nowrap}}
td.why{{color:#cf222e;font-size:12px;max-width:340px}}
.note{{font-size:11px;color:#8c959f;font-weight:400;margin-top:2px}}
.badge{{font-size:11px;padding:2px 8px;border-radius:10px;white-space:nowrap}}
.badge.pass{{background:#dafbe1;color:#1a7f37}}
.badge.fail{{background:#ffebe9;color:#cf222e}}
tr.detail td{{background:#fafbfc;padding:0}}
tr.detail.open td{{padding:12px}}
.detail .reply,.detail .raw{{display:none;font-size:12px;color:#57606a}}
.detail.open .reply,.detail.open .raw{{display:block}}
.detail .raw{{margin-top:6px;font-family:ui-monospace,Consolas,monospace;
word-break:break-all;color:#8c959f}}
</style>
<header>
  <h1>评测报告 <span class="meta">· {_e(backend)} · {_e(report.model or '')}</span></h1>
  <div class="meta">用例 {report.case_count} 条 · 失败 {fails} 条 · 耗时 {report.elapsed_seconds:.0f}s</div>
</header>
{blocked}
<div class="cards">{_metric_cards(report)}</div>
<div class="bar">{tabs}
<button class="tab failonly" id="fo" data-n="{fails}">只看失败 <i>{fails}</i></button>
<input type="search" placeholder="搜索用例 ID 或输入…" id=q></div>
<table>
<thead><tr><th>用例</th><th>输入</th><th>期望</th><th>实际</th><th>结果</th><th>失败原因</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<script>
const tb=document.querySelector('tbody');
let curPath='all', q='', onlyFail=false;
function apply(){{
  let shown=0;
  tb.querySelectorAll('tr').forEach(tr=>{{
    if(!tr.dataset.path) return;
    const okP = curPath==='all'||tr.dataset.path===curPath;
    const okF = !onlyFail || tr.dataset.status==='fail';
    const okQ = !q || tr.dataset.text.toLowerCase().includes(q);
    const show = okP && okQ && okF;
    tr.style.display = show?'':'none';
    const d = tr.nextElementSibling;
    if(d && d.classList.contains('detail')) d.style.display = show?'':'none';
    if(show) shown++;
  }});
  document.getElementById('cnt').textContent = shown;
}}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  t.classList.add('on'); curPath=t.dataset.p; apply();
}});
document.getElementById('q').oninput=e=>{{ q=e.target.value.trim().toLowerCase(); apply(); }};
document.getElementById('fo').onclick=function(){{
  onlyFail=!onlyFail;
  this.classList.toggle('on', onlyFail);
  apply();
}};
tb.addEventListener('click',e=>{{
  const tr=e.target.closest('tr:not(.detail)');
  if(!tr) return;
  const d=tr.nextElementSibling;
  if(d&&d.classList.contains('detail')) d.classList.toggle('open');
}});
apply();
</script>
<span id="cnt" style="display:none"></span>
</html>"""


def write_html(report: EvalReport, out_path: str | Path) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html(report), encoding="utf-8")
    return p

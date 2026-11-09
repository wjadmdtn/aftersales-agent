# Implementation Plan: 评测集与自动回归

**Branch**: `004-evaluation` | **Date**: 2026-11-09 | **Spec**: [spec.md](./spec.md)

## Summary

220 条用例、四类判分口径、两档 LLM 后端、分类阈值的基线回归。

三个技术要点：

1. **用例用生成脚本产出 JSONL**——220 条手写必然出错，且 diff 不可读
2. **判分按处理路径分四套**——业务动作 / 澄清 / 转人工 / 无关，不用统一 DSL
3. **stub 与 real 共用同一套用例和判分**——否则两边的数字不可比

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: 复用现有；新增无（评测脚本用标准库 + pytest）

**Storage**: 用例 JSONL 存 `eval/cases/`；基线存 `eval/baseline/*.json`

**Testing**: 评测脚本自身也要有测试（判分逻辑、阈值比较、报告生成）

**Target Platform**: Linux / Windows / CI

**Constraints**: stub 模式结果必须完全确定；real 模式必须记录耗时与成本

**Scale/Scope**: 220 条用例；real 模式单次预计 10-20 分钟

## Constitution Check

| 原则 | 满足方式 | 结论 |
|---|---|---|
| **I. 规范先行** | 派生自已定稿 spec | ✅ |
| **II. 测试即验收标准** | 判分逻辑本身有单元测试 | ✅ |
| **III. 规则可解释可复现** | 阈值与判分是确定性逻辑 | ✅ |
| **IV. 配置外置** | 阈值、后端、路径全部可配 | ✅ |
| **V. 边界诚实** | stub 结果的准确率显式标注不得对外引用 | ✅ |

无新增依赖。

## Project Structure

```text
eval/                                 # 新增，与 backend/ 平级
├── cases/                            # 220 条用例（JSONL，一行一条）
│   ├── intent.jsonl                  # 105 条：业务动作 + 无关
│   ├── clarify.jsonl                 # 60 条：应澄清
│   ├── handoff.jsonl                 # 30 条：应转人工
│   └── e2e.jsonl                     # 25 条：端到端
├── seeds/                            # 生成用例的 Python 脚本
│   ├── intent_seed.py
│   ├── clarify_seed.py
│   ├── handoff_seed.py
│   └── e2e_seed.py
├── baseline/
│   └── v1.json                       # 基线快照
└── runner.py                         # 主程序：加载 → 跑 → 判分 → 报告

backend/
└── tests/eval/
    ├── test_case_schema.py           # 用例格式校验
    └── test_grader.py                # 判分逻辑
```

**Structure Decision**: `eval/` 独立于 `backend/`。理由：
- 用例是**数据资产**，不该和代码混在一起
- 将来可能由非开发人员维护用例
- CI 里可以只跑 `backend/tests`（快）或同时跑 `eval`（慢）

## 设计要点

### 1. 用例格式：JSONL + 生成脚本

**为什么用 JSONL**：

| 格式 | 220 条手写的实际后果 |
|---|---|
| YAML | 缩进错一处，整块崩 |
| JSON 数组 | 末尾少个逗号，全文件加载失败 |
| **JSONL** | **一条一行，崩了只影响那一条；git diff 一行一处改动** |

**但 220 条不手写——用脚本生成**：

```python
# eval/seeds/intent_seed.py
STANDARD_REFUND = ["我要退款", "申请退款", "这个能退吗", ...]

def build():
    for i, phrase in enumerate(STANDARD_REFUND):
        yield {
            "id": f"intent-std-{i:03d}",
            "category": "intent",
            "path": "business_action",       # 预期处理路径
            "input": phrase,
            "expect": {"intent": "refund"},
        }
```

改一句措辞就改一个字符串，不用动结构。

### 2. 用例 schema（FR-004a：必须声明预期处理路径）

```json
{
  "id": "clarify-neg-007",
  "category": "clarify",
  "path": "clarify",
  "input": "这个东西不好用",
  "context": {"order_id": "ORD1002"},
  "expect": {
    "state": "clarifying",
    "starts_with_apology": true
  },
  "notes": "有负面情绪，应道歉后询问"
}
```

`path` 取值：`business_action` / `clarify` / `handoff` / `irrelevant`。

四类 `expect` 结构不同：

| path | expect 字段 |
|---|---|
| business_action | `intent` |
| clarify | `state`, `starts_with_apology` |
| handoff | `triggered`, `trigger_type`, `priority` |
| irrelevant | `state`（clarifying）, `starts_with_apology`: false |

### 3. 判分：四套简单逻辑，不做通用 DSL（FR-001）

```python
def grade(case, actual) -> GradeResult:
    if case["path"] == "business_action":
        ok = actual.intent.value == case["expect"]["intent"]
        return GradeResult(ok, detail=f"期望 {case['expect']['intent']}，实际 {actual.intent}")

    if case["path"] == "clarify":
        ok = actual.state == "clarifying"
        if ok and "starts_with_apology" in case["expect"]:
            has_apology = actual.reply.startswith(APOLOGY)
            ok = has_apology == case["expect"]["starts_with_apology"]
        # 附加硬约束：话术不得含业务承诺
        promise_hit = next((w for w in FORBIDDEN_PROMISES if w in actual.reply), None)
        if promise_hit:
            ok = False
            detail = f"话术含业务承诺「{promise_hit}」"
        ...
```

**不做 DSL 的理由**：三类判分逻辑完全不同（比对意图 / 比对状态+前缀 / 比对触发的三个字段）。一套万能断言语言会让用例难写、报错难读，而它省下的代码量还不到 200 行。

### 4. 过度转接率：单独的指标（FR-006c）

```python
over_handoff = sum(
    1 for c in clarify_cases
    if grade_result(c).actual_state == "handoff_queued"
) / len(clarify_cases)
```

**它不是识别准确率的一部分**，必须单独出现在报告里。理由：这个数字直接衡量人工成本损耗。

### 5. 两档后端（FR-021）

```python
python -m eval.runner --llm=stub    # CI，确定性
python -m eval.runner --llm=real    # 出效果数字
```

- 用例、判分、报告**完全一致**，只有 LLM 客户端不同
- 报告头部必须标注后端
- stub 报告额外加一行警告：
  ```
  ⚠ 本次使用桩实现，准确率不代表真实效果，不可对外引用
  ```

### 6. 基线比较与分类阈值（FR-019a）

```python
THRESHOLDS = {
    # 总体：容忍 LLM 噪声（同版本重跑差 1-2 点很正常）
    "intent_overall":        ("decrease", 0.03),
    "e2e_success":           ("decrease", 0.03),
    # 关键单项：样本少波动大，但退步更危险
    "intent_adversarial":    ("decrease", 0.05),
    "handoff_false_neg":     ("increase", 0.05),
    "over_handoff_rate":     ("increase", 0.05),
    # 硬性项：出现一次即阻断，不设容忍
    "silence_violation":     ("increase", 0.0),
    "degraded_write":        ("increase", 0.0),
    "promise_leak":          ("increase", 0.0),
}
```

阻断时输出（FR-019c）：

```
❌ 回归阻断
  intent_adversarial: 82.0% → 74.0%（-8.0pt，阈值 -5.0pt）
  promise_leak: 0 → 1（阈值 0）
```

**不是只给一个退出码**——要能一眼看出是哪项、差多少。

### 7. 报告格式

`eval/report/report-{timestamp}.md`：给人看，可直接贴 README。

```markdown
## 评测结果

后端：**real (gpt-4o-mini)** | 耗时 12m04s | 成本 ¥2.31 | 用例 220 条

| 指标 | 结果 | 基线 | 变化 |
|---|---|---|---|
| 意图识别总体 | 87.5% | 86.0% | +1.5 |
| ├ 标准表述 | 97.5% | — | |
| ├ 对抗表述 | 73.3% | — | |
| └ 无关键词 | 80.0% | — | |
| 澄清正确率 | 91.7% | — | |
| **过度转接率** | **5.0%** | — | |
| 转人工漏转率 | 7.5% | — | |
| 转人工误转率 | 3.3% | — | |
| 端到端成功率 | 82.5% | — | |

### 失败样本（18 条）
| ID | 输入 | 预期 | 实际 |
|---|---|---|---|
| adv-007 | 再不处理我就投诉你们不肯退款 | complaint | refund |
```

`eval/report/report-{timestamp}.json`：机器比对基线用。

### 8. 用例的初始状态隔离（FR-022）

每条用例跑之前重置：
- mock 服务 store（`reset_store()`）
- Redis 会话（用独立 key 前缀或 flush）
- 队列

**否则一条用例的退款会影响后面的用例。**

## Complexity Tracking

| 决策 | 为什么不做更简单的方案 |
|---|---|
| 用例独立于 backend | 用例是数据资产，且可能由非开发维护 |
| 生成脚本而非手写 | 220 条手写必然出错，且改措辞要动结构 |
| 四套判分而非通用 DSL | 三类判分逻辑本质不同，通用 DSL 只会让用例更难写、报错更难读 |

**否决的方案**：

| 否决 | 原因 |
|---|---|
| 用 YAML 写用例 | 缩进错一处整块崩，220 条手写必错 |
| 用 LLM 当裁判打分 | 判分本身变成不确定的，回归失去意义 |
| 只跑 stub 出数字 | 桩按预期返回，准确率虚高，不能对外引用 |
| 一个统一阈值 | 硬性项不能用"容忍阈值"，总体指标也不能零容忍 |

## 待确认项

1. **real 模式的模型选型**：需要确认用哪个模型跑（影响准确率与成本）。
2. **220 条用例的编写**：生成脚本里那批措辞需要人工确认是否贴近真实用户表达。建议先写 20 条样例过一遍，确认风格后再铺开。

## 下一步

由 `/speckit-tasks` 生成 `tasks.md`。

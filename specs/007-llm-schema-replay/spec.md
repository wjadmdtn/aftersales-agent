# Feature Specification: LLM 输出校验重放

**Feature Branch**: `007-llm-schema-replay`

**Created**: 2026-11-16

**Status**: 已实现（2026-11-16）

**Input**: LLM 返回结构不合法时的自纠重放；耗尽后进入 SYSTEM_ERROR 转人工

## 背景与目标

### 现状

Feature 002 对 LLM 输出的非法结构是这样处理的（`agent/llm/openai_compat.py`）：

```python
except (json.JSONDecodeError, KeyError, ValueError) as e:
    raise LLMError(f"llm returned invalid structure: {e}")   # 直接抛
```

`raise` 之后由 `with_retry` 接住，**用完全相同的 prompt 重试 3 次**（`temperature=0`）。

而 `handoff/triggers.py` 里早就写好了对应的转人工触发条件：

```python
if s.schema_retries_exhausted:
    return TriggerResult.fire(TriggerType.SYSTEM_ERROR, "输出校验重放耗尽")
```

**但这个字段从来没有被赋过值**——它永远是 `False`（本次 Feature 006 之后修复时也确认了这点）。所以：

- 结构不合法 → 盲重试 3 次 → 每次大概率同样的错 → 降级说"系统繁忙"
- 用户感觉是"系统挂了"，实际是**模型不按格式回**
- `schema_retries_exhausted` 这条转人工路径**从未生效**

### 为什么要改

LLM 是概率系统，`response_format: json_object` 只保证"是 JSON"，不保证"字段对"。实际会遇到的失败形态：

| 失败形态 | 例子 | 现状 |
|---|---|---|
| 枚举值错 | `{"intent": "退款"}`（应为 `refund`） | 抛出 → 盲重试 |
| 少了必填字段 | `{"intent": "refund"}` 缺 `mode` | 现在默认 EXECUTE，**静默猜** |
| 多了包装层 | `{"result": {"intent": "refund"}}` | KeyError → 盲重试 |
| 截断 | `{"intent": "refund", "mode": "exe` | JSONDecodeError → 盲重试 |
| 夹带说明文字 | `好的，我的判断是：{...}` | JSONDecodeError → 盲重试 |
| 类型错 | `{"confidence": "高"}` | ValueError → 盲重试 |

**温度 0 下重试同一句 prompt，模型大概率重复同一个错** —— 这是"盲重试"，等于浪费调用次数。

### 目标

1. 校验失败时把**具体错在哪**告诉模型，让它自己改（error-feedback replay）
2. 重放次数用尽时**明确标记** `schema_retries_exhausted=True`，走既有的 SYSTEM_ERROR 转人工
3. 降级路径保持不变：**绝不因为重放而冒险猜测意图**，尤其不得把结构不合法升级成写操作

### 非目标

- 不引入 Function Calling / tool_calls（当前架构仍为意图路由，见 002）
- 不改写操作确认流程
- 不对政策问答（`answer_policy`）做重放——那是 `PolicyAnswerer` 门禁的职责，门禁失败一律 miss，不重试（005 的既定决策）
- 不做"用另一个模型重试"这类多模型路由

## 核心设计约束

### 1. 重放必须带错误信息，否则等于盲重试

```python
# 现在（盲重试）
messages = [system, user]

# 改后（带错误重放）
messages = [
    system,
    user,
    {"role": "assistant", "content": 上次返回的原文},
    {"role": "user", "content": "你上次的输出不合法：<具体错误>。请只输出符合格式的 JSON，不要解释。"},
]
```

**关键**：错误信息要**可执行**，不能只说"格式错误"：

| 不好的错误信息 | 好的错误信息 |
|---|---|
| "格式错误" | "'退款' 不是合法 intent，必须是 refund/logistics/change_address/query_order/exchange/manual/policy/unknown 之一" |
| "缺少字段" | "缺少必填字段 mode，必须是 execute/inquiry/query/need_context 之一" |
| "JSON 解析失败" | "JSON 在第 28 个字符处截断（字符串未闭合）。请确保输出完整" |

### 2. 重放次数必须可配置，默认不高于 1 次

原因：每次重放 = 一次完整的模型调用。内网慢模型单次 20~40 秒，重放 2 次就是 +80 秒。用户已经在等了。

默认 `schema_replay_attempts = 1`（首次 + 1 次带错误重放）。配置可覆盖。

### 3. 重放耗尽 ≠ 立即降级

必须**先转人工，再降级**？不是。判定顺序：

```
结构非法
  → 带错误重放（≤ N 次）
    → 仍非法
      → 标记 schema_retries_exhausted = True
      → 本次请求仍走降级（统一话术，不猜意图）
      → 下一轮请求检测到该标记 → SYSTEM_ERROR 转人工（复用 003 的 triggers）
```

**为什么不立即转人工**：单次结构失败可能是一次性的（模型被限流返回了半截）。用户可能只是问了一句话，不该因为模型抽风一次就被推到人工队列。留一轮观察窗口，符合 002 FR-016c「转人工是最后手段」的精神。

### 4. 非法输出不得用作任何参数来源

**这是安全红线**：结构非法时，**颗粒度更细的字段**（如 `confidence`、`reason`）一律丢弃，不得"部分采纳"。

反例（禁止）：
```python
# 即使 intent 解析成功，只要整体校验失败，就不得用其中的字段
intent = data.get("intent")   # ← 非法数据里的字段，不可信
```

正例：
```python
try:
    result = _validate(raw)
except SchemaError as e:
    # 整体作废，连 intent 都不提取
    ...
```

理由：模型输出截断时可能恰好包含看起来合法的 `{"intent": "refund"}`，后面跟着被截断的 `"mode": "exec`。此时如果"只取能解析的部分"，就会把"execute" 误当成默认值 → **误执行退款**。

## User Scenarios

### 场景 A：模型输出枚举值用了中文（最常见）

1. **Given** 用户说"我要退款"，LLM 返回 `{"intent":"退款","mode":"exec"}`（两处都非法）
2. **When** 首次校验失败
3. **Then** 严格校验器给出："intent='退款' 不是合法值（可选：refund/logistics/...）；mode='exec' 不是合法值（可选：execute/inquiry/query/need_context）"
4. **When** 带这段错误信息重放
5. **Then** 模型返回 `{"intent":"refund","mode":"execute",...}` → 正常流程

### 场景 B：重放后仍然非法（模型能力不足或持续出错）

1. **Given** 用户说"我要退款"
2. **When** 首次 + 1 次重放都返回非法结构
3. **Then** 标记 `schema_retries_exhausted=True`，本次返回统一降级话术（不猜意图、不退款）
4. **When** 用户再发一条消息
5. **Then** `_exhausted()` 检测到该标记 → SYSTEM_ERROR 转人工，Context Pack 里 `trigger_detail` 写明"输出校验重放耗尽"

### 场景 C：截断输出恰好包含完整 intent

1. **Given** 模型返回 `{"intent": "refund", "mode": "exe`（被截断）
2. **When** JSON 解析失败
3. **Then** **整体作废**，不得从截断文本里提取 `intent=refund`
4. **When** 重放
5. **Then** 正常返回合法 JSON → 流程继续
6. **When** 重放仍然非法
7. **Then** 降级，**绝不生成退款确认卡**

### 场景 D：模型返回合法 JSON 但语义不合理

1. **Given** 用户说"今天天气不错"，模型返回 `{"intent":"refund","mode":"execute","confidence":1.0}`
2. **When** 通过结构校验
3. **Then** 结构合法 → 不触发重放，进入 002 的 `normalize_mode` 兜底校验（既有能力）
4. **补充**：这是**语义**问题，不是**结构**问题。本 Feature 只管结构，语义由评测集（Feature 004）衡量

### 场景 E：重放本身超时

1. **Given** 首次调用返回非法结构
2. **When** 重放调用超时
3. **Then** 按"重放失败"处理：标记 `schema_retries_exhausted=True`，走降级路径
4. **不得**把超时和结构非法混为一谈：超时时 `llm_timeout_streak` 也要累加

### 场景 F：测试桩的可控性

1. **Given** 测试需要验证"首次非法，重放后合法"
2. **When** `StubLLMClient` 配置 `invalid_times=1` 外加一份合法响应
3. **Then** 首次抛结构非法，重放返回合法结果
4. **When** `invalid_times=99`
5. **Then** 每次都非法，用于验证耗尽路径

### 场景 G：结构合法但缺必填字段 `mode`

1. **Given** 模型返回 `{"intent": "refund"}`（缺 mode）
2. **当前行为**：静默默认为 `EXECUTE` → 弹退款确认卡
3. **新行为**：`mode` 是必填字段 → 校验失败 → 重放
4. **理由**：写意图缺 mode 时静默按 execute 处理，等于让模型少写一个字段就退钱。宁可重放一次或降级，也不能猜。

> **这是本次 Feature 唯一会改变现有语义的点。** 需要在 tasks 里单独列出与回归验证。

## Requirements

### Functional Requirements

**校验器**

- **FR-001**: 必须有一个独立的**严格结构校验器**，输入模型原始输出文本，输出 `IntentResult` 或 `SchemaError`
- **FR-002**: 校验器必须**整体校验**，任一字段非法即整体失败，不得部分采纳
- **FR-003**: `mode` 必须为必填字段（允许显式 `null` 由业务层按意图决定默认，但**不得缺字段**）
- **FR-004**: `intent` 必须是 `Intent` 枚举之一，非法值必须报出全部可选值
- **FR-005**: `confidence` 必须是 `[0, 1]` 的数值；越界必须截断并警告，**不视为结构失败**（数值型字段宽松处理）
- **FR-006**: 多余字段必须忽略，不算失败（向前兼容）
- **FR-007**: 校验器必须容忍模型输出**前后夹带说明文字**：能定位到第一个 `{...}` 结构则解析，否则报 JSON 解析失败
- **FR-008**: `SchemaError` 必须包含：错误类别（`json_syntax` / `missing_field` / `invalid_value` / `invalid_type`）、具体字段、期望值

**重放**

- **FR-009**: 重放必须在**同一次业务请求内**完成，不增加用户轮次
- **FR-010**: 重放必须带上前一次模型的原文输出与可执行的错误说明（见设计约束 §1）
- **FR-011**: 重放次数必须可配置（`schema_replay_attempts`），默认 1
- **FR-012**: 重放必须继承第一次调用的超时预算与 trace_id
- **FR-013**: 重放失败后，`LLMError` 必须携带 `schema=True` 标记，便于上层区分"网络失败"与"结构失败"

**信号接线**

- **FR-014**: 重放耗尽后，`SessionState.schema_retries_exhausted` 必须置 `True`
- **FR-015**: 该标记在**下一次成功的意图识别**时清除（否则会话内一次抽风会永久污染）
- **FR-016**: `_exhausted()` 检测到该标记时触发 `TriggerType.SYSTEM_ERROR` 转人工（复用 003）
- **FR-017**: Context Pack 的 `trigger_detail` 必须写明"输出校验重放耗尽"，人工端能看出是模型问题不是业务问题

**安全**

- **FR-018**: 结构校验失败时**禁止**执行任何写操作（与 FR-013 of 002 一致）
- **FR-019**: 结构校验失败时**禁止**从原始输出里提取任何字段用作业务参数
- **FR-020**: 重放过程中**禁止**修改用户消息或伪造上下文（重放只追加 assistant + user 纠错消息，原 user 消息不变）

**可观测**

- **FR-021**: `/agent/health/llm` 必须新增 `schema_failures` 与 `schema_replays` 两个计数
- **FR-022**: 每次重放必须打日志：原始输出（截断到 200 字符）、错误类别、重放次数

### Key Entities

- **SchemaError**：错误类别、字段名、期望值、原始片段
- **IntentResult**（既有）：意图、模式、置信度、理由、改写查询
- **SessionState.schema_retries_exhausted**（既有字段，本 Feature 首次生效）

## 兼容性分析

| 改动点 | 是否影响现有行为 | 需要回归 |
|---|---|---|
| `mode` 改为必填 | **会**：缺 mode 从"默认 EXECUTE"变成"校验失败" | 需评估影响范围（见下） |
| 重放追加对话轮次 | 不影响业务结果，只多一次模型调用 | 慢，但内网模型限流可能受影响 |
| `LLMError.schema` 标记 | 纯新增属性 | 无 |
| `schema_retries_exhausted` 接线 | **会**：新增一条转人工路径 | 需验证不会误转（只在重放耗尽时触发） |

**`mode` 必填的影响面 —— 已验证（2026-11-16）**：对 `deepseek-v4-flash` 的 292 条真实评测结果统计
（`eval/report/report-20261116-074356.json`），真正走了意图识别的样本共 172 条
（business_action 112 / clarify 40 / irrelevant 20），**漏 `mode` 比例 0%**。
报告中另外 100 条 `mode=None` 是 e2e 终态记录与 policy/handoff 的 STATE 记录，
并未经过 classify，属统计假阳性。**结论：改必填安全。**

## 发布时序

1. 先上**校验器 + 重放**（不影响语义，mode 仍宽松）
2. 观察一周 `schema_failures` / `schema_replays` 指标
3. 如果 `missing_mode` 占比高 → 改 prompt 加强要求
4. 如果占比低 → 收紧为必填
5. 最后接线 `schema_retries_exhausted` → SYSTEM_ERROR

**不建议一次全上**，尤其第 5 步会改变转人工率。

## 测试要点

### 单元（校验器）

- 6 种失败形态各自产出正确的 `SchemaError` 类别与字段
- 合法输出 → 正确 `IntentResult`
- 夹带说明文字 → 能提取 JSON
- 多余字段 → 忽略不报错
- confidence 越界 → 截断不报错

### 集成（重放链路）

- 首次非法 + 重放合法 → 正常流程走完，`schema_replays == 1`
- 重放耗尽 → `schema_retries_exhausted == True`，本次降级
- 下一次请求 → 检测到标记，SYSTEM_ERROR 转人工
- 截断输出 → 不提取 intent，不生成退款确认
- 重放超时 → 计入 `llm_timeout_streak` 且标记耗尽

### 回归

- 现有 584 条测试全绿
- 真实模型（`--llm=real`）重跑，转人工率、降级率不显著变化

## 变更清单

| 文件 | 改动 |
|---|---|
| `app/agent/llm/schema.py` | 新增：严格校验器 + `SchemaError` |
| `app/agent/llm/openai_compat.py` | 改为调用校验器，支持重放 |
| `app/agent/llm/base.py` | `LLMError` 增加 `schema` 属性 |
| `app/agent/llm/stub.py` | 支持 `invalid_times` 模拟结构非法 |
| `app/agent/resilience.py` | `with_retry` 支持"带错误重放"回调 |
| `app/agent/intent.py` | 结构失败时标记 `schema_retries_exhausted` |
| `app/api/agent_routes.py` | `/agent/health/llm` 增加两个计数 |
| `app/config.py` | 新增 `schema_replay_attempts`（默认 1） |
| `tests/unit/test_llm_schema.py` | 新增 |
| `tests/integration/test_schema_replay.py` | 新增 |

## 实测记录（2026-11-16，deepseek-v4-flash + 真实模型）

| 验证项 | 做法 | 结果 |
|---|---|---|
| 意图识别准确率 | `eval/intent_regression.py` 跑评测集 202 条意图用例（8 并发） | **94.6%**（174/184 可判定），**结构失败 0 条** |
| 结构合规率 | 40 条多样化输入走完整 `classify` | **39/39 通过**，`schema_replays=0`、`schema_failures=0` |
| `mode` 字段缺失率 | 绕过校验器直接看模型原始返回，16 条样本 | **0%** |
| 重放生效性 | 强制首次返回 `{"intent":"退款","mode":"执行"}`，后接真实网络 | 重放 1 次，模型照纠错提示改对，`schema_replays=1` |

**新旧解析器等价性证明**：对同一段合法 JSON，改动前的解析逻辑与 `validate_intent_result`
在 5 组样本（含 `mode:null`、多余字段、`search_query` 空白）上输出**完全相同**。
结合"结构失败 0 条"，可确定本次改动对识别结果**零影响**。

### 一个必须知道的事实：temperature=0 并不保证可复现

排查 2 条疑似新引入的判错时发现，模型对同一条输入多次调用结果不一致：

```
'再不处理我就投诉你们不肯退款'  5 次 → refund×4, unknown×1
'我这单现在是啥情况'            5 次 → logistics×3, query_order×2
```

这两条在改动前就存在同样的不确定性（前一份报告里其中 1 次恰好判对）。
**结论：`temperature=0` 只降低随机性，不消除随机性。**
单次评测的准确率有噪声，比较两个版本时应看同一批用例的多次运行均值，
不能拿一次跑分直接判定"回归/提升"。

## 风险与待确认项

1. **`mode` 是否改为必填**：需先离线统计真实模型漏 mode 的比例。**这是本 Feature 唯一可能改变现网行为的点。**

2. **重放会拖慢政策链路**：政策问答本身就是 3 次模型调用（检索 + rerank + 生成），如果意图识别也重放，整体变 4 次。内网慢模型下单次问答可能从 40 秒变成 80 秒。是否需要为政策意图单独关闭重放？

3. **重放是否应只对读意图生效**：写意图（退款/改地址）结构失败时，重放本身是安全的（不执行任何写操作）。但如果重放后拿到的恰好是 `execute` 模式，是否会让一个本该降级的请求变成真执行？——按 FR-019 不提取任何字段，所以不会。但需要测试锁死。

4. **`schema_retries_exhausted` 一轮窗口是否够**：现在的设计是"这次降级，下次转人工"。如果用户看到"系统繁忙"就关掉了页面，人工队列里就什么都没有。是否应该**当场就转人工**？——见设计约束 §3 的取舍说明，倾向保留一轮窗口。

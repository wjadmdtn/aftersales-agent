# Implementation Plan: 转人工链路

**Branch**: `003-handoff` | **Date**: 2026-11-09 | **Spec**: [spec.md](./spec.md)

## Summary

在 Feature 002 的 Agent 上增加转人工能力：四类触发源、Context Pack、规则优先级排队、人工接管后的 AI 静默。

四个实现要点：

1. **触发判定是纯函数**——输入信号，输出触发结果，完全可测
2. **Context Pack 的摘要不能依赖外部服务**——转人工是最不能挂的路径
3. **静默约束用状态机硬拦截**——不能只靠注释约定
4. **优先级是纯函数 + 可解释**——必须能向主管说明为什么这个用户排前面

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: 复用 Feature 001/002 的 FastAPI、Pydantic、Redis、httpx

**Storage**: Redis（队列用 ZSET，score = 优先级权重 + 入队时间）

**Testing**: pytest + fakeredis

**Target Platform**: Linux / Windows

**Performance Goals**: 触发判定 < 5ms；Context Pack 生成 < 20ms（纯本地，不调外部）

**Constraints**: 情绪判定零 LLM 调用；摘要零外部依赖；禁止提示等待时长

**Scale/Scope**: 单实例队列；人工侧通过接口模拟

## Constitution Check

| 原则 | 满足方式 | 结论 |
|---|---|---|
| **I. 规范先行** | 派生自已定稿 spec | ✅ |
| **II. 测试即验收标准** | FR-001~030、SC-001~009 逐条对应 | ✅ |
| **III. 规则可解释可复现** | 优先级、情绪、触发判定全部纯规则 | ✅ |
| **IV. 配置外置** | 阈值、词表、文案全部可配 | ✅ |
| **V. 边界诚实** | AI 静默硬拦截；不承诺等待时长 | ✅ |

无新增第三方依赖。

## Project Structure

```text
backend/
├── app/
│   ├── handoff/                       # 本 feature 新增
│   │   ├── triggers.py                # 四类触发源 + 互斥判定顺序
│   │   ├── emotion.py                 # 规则情绪判定（零 LLM）
│   │   ├── priority.py                # 优先级纯函数
│   │   ├── context_pack.py            # 打包 + 规则摘要
│   │   ├── queue.py                   # Redis ZSET 队列
│   │   ├── state.py                   # 会话状态机 + 静默拦截
│   │   └── scripts.py                 # 七条转接话术
│   ├── models/
│   │   └── handoff.py                 # TriggerType / Priority / HandoffContext
│   └── api/
│       └── handoff_routes.py          # 人工侧接口
└── tests/
    ├── unit/
    │   ├── test_triggers.py
    │   ├── test_emotion.py
    │   ├── test_priority.py
    │   ├── test_context_pack.py
    │   └── test_state_machine.py
    └── integration/
        ├── test_handoff_flow.py
        └── test_queue_priority.py
```

**Structure Decision**: 独立子包 `app/handoff/`，与 `app/agent/` 平级。转人工是与对话编排并列的能力，不是它的子模块。

## 设计要点

### 1. 触发源：互斥的判定顺序（FR-002）

```python
# 固定顺序，命中即返回。顺序本身是设计决策：
# 用户明确要求必须最先响应，风险必须优先于其他一切
CHECKS = (check_user_request, check_risk, check_system_error, check_boundary)
```

| 顺序 | 触发源 | 为什么排这个位置 |
|---|---|---|
| 1 | 用户主动要求 | 用户说了要真人，任何其他判断都是多余的 |
| 2 | 风险 | 投诉/情绪激烈，不能让 AI 再多说一轮 |
| 3 | 系统异常 | 工具连续失败，用户已经等很久了 |
| 4 | 能力边界外 | 可以先给自助建议，最不紧急 |

**"我要投诉，转人工"同时命中 1 和 2**：按用户主动要求处理（不挽留、不道歉铺垫），因为用户在要求的是"要人"，而不是"要 AI 道歉"。

### 2. 情绪判定：规则，零 LLM（FR-007a）

```python
STRONG_NEGATIVE = ["垃圾", "骗子", "太差了", "恶心", "差评", "坑人", "废物", "滚"]
# 再加：连续感叹号 ≥2 / 全大写英文 / 重复辱骂

def detect_emotion(text) -> Emotion:
    if 强负面词命中: return ANGRY
    if "!!" in text 或 "！！" in text: return ANGRY
    if 一般负面词命中: return NEGATIVE
    return NEUTRAL
```

**不要 LLM 的理由**（与 Feature 002 对比写清）：

| | 意图识别（002） | 情绪判定（003） |
|---|---|---|
| 判错的后果 | 动错钱 | 优先级排序偏一点 |
| 用 LLM | 必须 | 不需要 |
| 一致性 | 可容忍随机 | 必须确定（同一用户同一句话每次都该同优先级） |

### 3. 优先级：纯函数 + 强制带依据（FR-014/015）

```python
def calc_priority(trigger, emotion, amount, threshold) -> tuple[Priority, str]:
    if trigger is RISK:        return HIGH,   f"风险触发：{detail}"
    if emotion is ANGRY:       return HIGH,   "情绪激烈"
    if amount >= threshold:    return HIGH,   f"金额 {amount} ≥ {threshold}"
    if trigger is USER_REQUEST: return MEDIUM, "用户主动要求转人工"
    return LOW, "系统自动转接"
```

返回 `(优先级, 依据)` 二元组，**依据是强制的**——`HandoffContext.priority_reason` 不允许为空。

### 4. Context Pack：摘要必须零外部依赖（FR-010）

```python
def rule_based_summary(turns, intent, trigger) -> str:
    # 纯字符串拼接，不调 LLM、不查库
    # 用户诉求：退款；用户表述：我要退款；转人工原因：用户主动要求转人工
```

摘要模板：

```
用户诉求：{意图中文}
用户表述：{最后一轮用户原话}
转人工原因：{触发类型中文}
已尝试：{工具调用序列}
```

超过 120 字时截断加省略号。**关键是这个函数不可能失败**——没有网络、没有 IO、没有模型。

### 5. 静默拦截：代码级硬约束（FR-021）

```python
AI_SPEAKABLE_STATES = {INTENT, COLLECTING, VALIDATING, DECIDING, EXECUTING}

def assert_ai_may_speak(state):
    if state not in AI_SPEAKABLE_STATES:
        raise SilenceViolation(state)
```

在 `AgentLoop` 输出回复前调用。**抛异常而不是返回 False**——返回 False 容易被调用方忽略，抛异常会直接让测试失败。

### 6. 队列：ZSET + 幂等（FR-017/018）

```python
score = -PRIORITY_SCORE[priority] * 1e6 + queued_at.timestamp()
redis.zadd("handoff:queue", {session_id: score})
```

- 高优先级 score 更小（负号），ZRANGE 自然排前面
- 同优先级按 `queued_at` 升序
- 幂等：`SET handoff:requested:{sid} NX` 判断是否已入队

### 7. 状态机（FR-019~023）

```
INTENT/... ──触发──▶ HANDOFF_REQUESTED ──▶ QUEUED ──人工接入──▶ HUMAN_SERVING ──▶ RESOLVED
                                            ▲                       │
                                            └───人工拒绝：不回流────┘
```

关键约束：
- `QUEUED → HUMAN_SERVING` 只能由**人工侧接口**驱动（`actor="human"`）
- `HUMAN_SERVING → COLLECTING`（回流 AI）**不存在这条边**
- 人工拒绝 → 状态停在 `HUMAN_SERVING` 并标记 `escalated`，等待上级处理

### 8. 话术：配置化（FR-024~027）

七条话术写在配置里，代码只按场景取键。**测试要断言"任何话术里都不含时间数字"**（SC-009）：

```python
for script in all_scripts():
    assert not re.search(r"\d+\s*(秒|分钟|小时)", script)
```

## Complexity Tracking

| 决策 | 为什么不做更简单的方案 |
|---|---|
| 摘要规则实现 | 调 LLM 生成摘要，模型不可用时转人工就挂了——这是最不该挂的路径 |
| 静默用异常而非返回值 | 返回值容易被调用方 `if not ok: continue` 忽略掉 |
| 队列用 ZSET 而非 LIST | LIST 无法按优先级排序，只能先来后到 |

**否决的方案**：

| 否决 | 原因 |
|---|---|
| 情绪用 LLM 判定 | 判错代价小，规则足够；LLM 还会引入同一句话不同优先级的问题 |
| 记录"预计等待时长" | spec 已确认不提示时长 |
| 人工拒绝后回流 AI | spec 已确认不回流（会形成踢皮球循环） |
| 用现成的任务队列库 | 单实例 Redis ZSET 足够，引入 Celery/RQ 属过度设计 |

## 下一步

由 `/speckit-tasks` 生成 `tasks.md`。

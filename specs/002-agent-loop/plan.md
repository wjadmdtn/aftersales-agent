# Implementation Plan: Agent 主循环与状态机

**Branch**: `002-agent-loop` | **Date**: 2026-11-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-agent-loop/spec.md`

## Summary

实现一个以 LLM 为主导的对话 Agent：识别用户真实诉求、编排 Feature 001 的四个业务能力、管理多轮会话状态、执行写操作前强制确认。

四个技术难点：

1. **LLM 必须可替换**——生产用真实模型，测试用确定性桩，否则测试无法回归
2. **LLM 失败时必须安全降级**——单次调用失败才降级，下次请求仍先尝试 LLM；关键词保守筛选，禁止任何写操作
3. **写操作前的确认必须有有效期与重新校验**——状态可能在确认间隙被改变
4. **trace_id 必须贯穿 Agent 与 mock 服务**——一次用户输入产生一条完整链路

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastAPI、Pydantic v2、Redis（`redis[hiredis]`）、httpx（调 mock 服务）、无 LLM SDK 硬依赖（通过适配层）

**Storage**: Redis（会话状态，TTL 24h）。无跨请求的降级状态——每次请求独立判断

**Testing**: pytest + httpx.AsyncClient；LLM 用具确定性桩；Redis 用 `fakeredis`

**Target Platform**: Linux / Windows

**Project Type**: web-service（Agent 服务，与 mock 服务并列）

**Performance Goals**: 单轮决策 P99 < 8s（含 LLM 调用）；降级态单轮 < 200ms（不调 LLM）

**Constraints**: 降级期零写操作；写操作执行前必须重新校验；关键词不做意图决策

**Scale/Scope**: 单会话多轮；会话数不限（受 Redis 容量约束）

## Constitution Check

| 原则 | 本 feature 如何满足 | 结论 |
|---|---|---|
| **I. 规范先行** | 本 plan 派生自已定稿 spec，待确认项已清零 | ✅ |
| **II. 测试即验收标准** | FR-001~032、SC-001~008 逐条对应测试，见 tasks | ✅ |
| **III. 规则可解释可复现** | 确认环节与执行前校验是确定性规则；LLM 只负责理解，不负责裁决写操作 | ✅ |
| **IV. 配置外置** | LLM 超时、会话 TTL、关键词表（含降级宽词表）全部走配置 | ✅ |
| **V. 边界诚实** | 降级时明确告知能力受限（FR-016）；不编造结论（FR-030） | ✅ |

**新增依赖说明**（宪法要求）：
- `redis[hiredis]`：spec 已确认会话状态入 Redis。hiredis 是官方推荐的 C 加速解析器，不改变语义，仅提升吞吐。替代方案 `redis`（纯 Python）也可用，但解析开销更大。**如不希望引入 hiredis，可只用 `redis`。**
- `fakeredis`：仅测试依赖，用于免起真实 Redis

## Project Structure

### Documentation (this feature)

```text
specs/002-agent-loop/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
└── tasks.md
```

### Source Code

```text
backend/
├── app/
│   ├── agent/                        # 本 feature 新增
│   │   ├── loop.py                   # 主循环：输入 → 识别 → 编排 → 回复
│   │   ├── intent.py                 # 意图类型、识别结果模型
│   │   ├── llm/
│   │   │   ├── base.py               # LLMClient 协议
│   │   │   ├── openai_compat.py      # 真实实现（OpenAI 兼容接口）
│   │   │   ├── stub.py               # 测试桩：按预设返回
│   │   ├── degrade.py                # 降级路径：宽写词表判断
│   │   ├── session.py                # 会话状态（Redis）
│   │   ├── confirm.py                # 待确认操作的创建与核销
│   │   ├── present.py                # 结果码 → 自然语言
│   │   └── tools/
│   │       └── client.py             # 调 mock 服务的 HTTP 客户端（透传 trace）
│   ├── models/
│   │   └── agent.py                  # 会话、轮次、待确认操作
│   ├── config.py                     # 增加 Agent 相关配置
│   └── api/
│       └── agent_routes.py           # Agent 对话接口
└── tests/
    ├── unit/
    │   ├── test_intent_parse.py
    │   ├── test_degrade.py
    │   ├── test_present.py
    │   └── test_confirm.py
    ├── contract/
    │   ├── test_agent_intent.py
    │   └── test_agent_confirm.py
    └── integration/
        ├── test_session_redis.py
        ├── test_e2e_flow.py
        └── test_trace_through_services.py
```

**Structure Decision**: 复用 `backend/`，Agent 作为独立子包 `app/agent/`。理由：Agent 与 mock 服务同属一个后端工程，共享模型与配置；但通过 HTTP 调用 mock 服务（不是进程内调用），保持服务边界清晰，也为将来拆成两个服务留出空间。

## 设计要点

### 1. LLM 适配层（FR-005、FR-031）

```python
class LLMClient(Protocol):
    async def classify(self, *, user_input: str, hint_keywords: list[str], context: dict) -> IntentResult: ...
```

只有两个实现：

| 实现 | 用途 |
|---|---|
| `OpenAICompatClient` | 生产：调用 OpenAI 兼容接口 |
| `StubLLMClient` | 测试：按输入返回预设结果，可配置超时/报错/非法返回 |

**没有熔断包装层**——spec 已确认每次输入都先走 LLM，单次失败才降级，不做跨请求的状态记忆。

**StubLLMClient 的设计很关键**——它必须能模拟：

```python
StubLLMClient(
    responses={"我要退款": IntentResult(intent="refund", confidence=0.95)},
    fail_mode=None,          # None | "timeout" | "error" | "invalid"
    fail_times=0,            # 仅前 N 次失败，用于验证"下次请求仍调用 LLM"
    delay_ms=0,
)
```

`fail_times` 用来验证 FR-016b：上次失败后，下次请求**仍然先调 LLM**，不会因上次失败而跳过。

### 2. 意图识别：关键词只作提示（FR-007、FR-008）

```python
async def classify(user_input, context, llm):
    # 关键词只用于收集提示，不参与判定
    hints = match_keywords(user_input)          # 仅提示
    result = await llm.classify(
        user_input=user_input,
        hint_keywords=hints,                    # 传给 LLM
        context=context,
    )
    return result
```

**没有任何分支依赖 `hints` 决定意图。** 这一点在代码里要明显，并写测试断言：给 Stub 返回任意意图时，hints 不影响最终结果。

### 3. 单次失败即降级（FR-016b）

没有跨请求的熔断状态。每次请求的处理：

```
请求进入
  → 先调 LLM（不管上次成功与否）
      ├─ 成功 → 正常路径
      └─ 超时 / 报错 / 返回不合法 → 本次请求走降级路径
  → 本次请求结束，不记录任何"降级中"状态
```

**为什么不做熔断**：熔断会在冷却期内跳过 LLM，等于用关键词替代意图判断——正是本 feature 要避免的。代价是 LLM 持续故障时每次请求都要等一次超时，用**较短的超时配置**（如 3s）控制影响。

**测试要点**（对应 SC-004a）：
```python
# 前 2 次失败，第 3 次应当仍然调用 LLM
stub = StubLLMClient(fail_times=2)
await agent.handle(...)   # 降级
await agent.handle(...)   # 降级
await agent.handle(...)   # 正常，说明没有被跳过
assert stub.call_count == 3
```

### 4. 降级路径（FR-013~016a）

```python
DEGRADE_WRITE_KEYWORDS = {
    # 比正常关键词表更宽：只要沾边写操作就拒
    "退", "退款", "退货", "退钱", "退换", "申请退",
    "改", "改地址", "修改", "换地址", "更换",
    "取消", "撤销", "修改订单",
}

def classify_degraded(user_input) -> "read" | "write_suspected":
    if any(kw in user_input for kw in DEGRADE_WRITE_KEYWORDS):
        return "write_suspected"
    return "read"
```

- `write_suspected` → 拒绝，回复固定话术
- `read` → 允许查订单 / 查物流（若上下文有订单号）
- **关键词表可配置**（FR-014a 要求比正常路径更宽，测试要断言这一点）

### 5. 写操作确认（FR-017~021）

```python
class PendingConfirmation(BaseModel):
    action: Literal["refund", "change_address"]
    order_id: str
    amount: Decimal | None
    payload: dict
    created_at: datetime
    expires_at: datetime      # created_at + confirm_ttl_seconds
```

流程：

```
用户表达写意图
  → 生成 PendingConfirmation，存入会话
  → 回复复述内容，询问确认
  → 用户回复
      确认 → 重新查订单状态 → 校验通过 → 执行 → 清除 pending
      取消 → 清除 pending
      其他 → 重新询问确认（不默认同意）
  → 执行前始终重新校验（FR-020），不只看是否超时
```

**确认词表**也要可配置，且必须**精确匹配**（"确认"、"是的"、"对"），不能用"包含"——否则"确认不了"会被当成确认。

### 6. 会话状态（FR-025~027）

Redis 结构：

```
会话主记录   session:{session_id}      → JSON，TTL 24h
待确认操作   session:{session_id}:pending → JSON，TTL = confirm_ttl
```

会话 JSON：

```json
{
  "session_id": "...",
  "user_id": "...",
  "order_id": "ORD1002",
  "snapshot": {"name": "...", "amount": "259.90"},
  "intent": "refund",
  "turn_count": 3,
  "degraded": false,
  "updated_at": "..."
}
```

**每次写入刷新 TTL**，避免活跃会话被误删。

### 7. trace 贯穿（FR-031）

```
用户请求 → Agent 生成/接收 X-Trace-Id
         → 调 mock 服务时**原样透传**
         → mock 服务的调用记录里也有同一个 trace_id
```

测试断言：一次 Agent 请求后，`GET {mock}/_debug/calls?trace_id=X` 能查到对应记录。

### 8. 快照与库不一致（FR-002、FR-003）

```python
async def load_order(session):
    real = await tools.get_order(session.order_id)      # 以库为准
    if session.snapshot and real.amount != session.snapshot.amount:
        logger.warning("snapshot mismatch session=%s order=%s", ...)
        record.mismatch = True                          # 留痕，FR-003
    return real
```

**不一致时用库的值，但必须留痕**，且可在调用记录里查到。

## Complexity Tracking

| 决策 | 为什么不做更简单的方案 |
|---|---|
| LLM 适配层 + 协议 | 直接用 SDK 会让测试必须联网，无法回归。协议化是唯一可测的方式 |
| 独立降级词表 | 复用正常词表会导致"退款"等词被漏判，而写操作漏判代价是钱 |
| 待确认操作存 Redis | 存内存会导致重启后确认丢失，用户确认了却没执行 |

**否决的方案**：

| 否决 | 原因 |
|---|---|
| 熔断器 | 熔断会在冷却期跳过 LLM，等于用关键词替代意图判断。代价是 LLM 持续故障时每次都要等一次超时，改用较短超时配置控制 |
| 降级期缓存"已降级"标记 | 同上，跨请求状态会让后续请求绕过 LLM |
| 用 LangChain / LangGraph 做编排 | 本 feature 流程线性（识别→确认→执行），引入框架收益小于理解成本；且宪法要求新增依赖说明理由 |
| 意图识别用规则 + LLM 双路投票 | spec 已确认 LLM 主导；双路投票在规则错时会拉低准确率 |
| 会话状态存内存 | spec 已确认 Redis（FR-025） |

## 下一步

本 plan 确认后，由 `/speckit-tasks` 生成 `tasks.md`。

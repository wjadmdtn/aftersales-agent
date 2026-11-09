# Implementation Plan: 工具层与 mock 业务服务

**Branch**: `001-tool-layer-mock` | **Date**: 2026-11-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-tool-layer-mock/spec.md`

## Summary

实现四个业务能力（查订单、查物流、改地址、退款）与一个按请求维度生效的故障注入机制，以独立的 HTTP 服务形式提供，业务数据为内存态 + 种子文件。上层 Agent 通过真实网络请求调用，从而使超时、脏数据、重复提交等失败路径可被真实覆盖。

技术要点：
- **结果状态用结构化枚举表达**，而非 HTTP 状态码或错误文案，保证调用方可编程分流
- **退款幂等靠请求标识 + 进程内并发控制**，并发场景下有测试覆盖
- **故障注入是横切的中间件**，通过请求头触发，不侵入业务代码

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastAPI、Pydantic v2、uvicorn、pytest、httpx（测试用客户端）

**Storage**: 内存字典 + JSON 种子文件（`seed/` 目录），进程启动时加载，运行期只读种子、写入内存副本

**Testing**: pytest + httpx.AsyncClient（直连 ASGI app，不起真实端口）；并发幂等用 `asyncio.gather`

**Target Platform**: Linux / Windows，本地与容器

**Project Type**: 独立 web-service（后续作为 Agent 的依赖服务）

**Performance Goals**: 单次调用 P99 < 50ms（排除主动注入的延迟）；并发退款 50 路无重复

**Constraints**: 故障注入不改变业务数据一致性；不引入 Redis/数据库（本 feature 阶段）

**Scale/Scope**: 4 个业务能力 + 1 个故障注入机制；种子数据 20 条订单量级

## Constitution Check

*GATE: 必须在 Phase 0 之前通过，Phase 1 后复检。*

| 原则 | 本 feature 如何满足 | 结论 |
|---|---|---|
| **I. 规范先行** | 本 plan 由已确认的 spec 派生，spec 的 4 个待确认项已闭环 | ✅ |
| **II. 测试即验收标准** | spec 的 FR-001~016、SC-001~006 逐条映射为测试用例，见 tasks | ✅ |
| **III. 规则可解释可复现** | 本层不涉及"影响真人的决策"；退款状态校验是确定性规则 | ✅ |
| **IV. 配置外置** | 种子文件路径、故障注入开关、超时阈值全部走配置 | ✅ |
| **V. 边界诚实** | 未找到 / 业务拒绝 / 系统异常三者结构上可区分（FR-005/006） | ✅ |

**依赖**：新增 FastAPI、uvicorn、httpx——均已在宪法技术栈内或为测试必备，无额外引入。

## Project Structure

### Documentation (this feature)

```text
specs/001-tool-layer-mock/
├── plan.md              # 本文件
├── research.md          # Phase 0 输出
├── data-model.md        # Phase 1 输出
├── quickstart.md        # Phase 1 输出
├── contracts/           # Phase 1 输出（OpenAPI / 接口契约）
└── tasks.md             # Phase 2 输出（由 /speckit-tasks 生成）
```

### Source Code (repository root)

```text
backend/
├── pyproject.toml
├── .env.example
├── app/
│   ├── main.py                     # FastAPI 应用装配
│   ├── config.py                   # 配置（种子路径、故障开关、超时）
│   ├── models/
│   │   ├── enums.py                # OrderStatus / RefundStatus / ResultCode / FaultType
│   │   ├── domain.py               # Order / Logistics / Address / Refund
│   │   ├── results.py              # 统一的业务结果包装
│   │   └── call_record.py          # 调用记录（trace_id / request_id / attempt）
│   ├── observability/
│   │   ├── trace.py                # TraceMiddleware + contextvar + trace_id 生成
│   │   ├── recorder.py             # CallRecorder（环形缓冲 + 按 trace/request 查询）
│   │   └── logging.py              # 日志格式器，自动注入 trace_id
│   ├── store/
│   │   ├── seed.py                 # 种子加载
│   │   └── memory.py               # 内存存储 + 并发控制
│   ├── faults/
│   │   ├── injector.py             # 故障注入中间件（按请求头触发）
│   │   └── rules.py                # 故障类型定义与延迟/错误/脏数据的实现
│   ├── services/
│   │   ├── order.py
│   │   ├── logistics.py
│   │   ├── address.py
│   │   └── refund.py
│   └── api/
│       ├── routes.py               # 四个业务端点
│       └── debug.py                # /_debug/calls 只读查询端点
├── seed/
│   └── orders.json                 # 种子数据
└── tests/
    ├── conftest.py                 # app fixture + 内存 store 重置
    ├── unit/
    │   ├── test_store.py
    │   ├── test_fault_rules.py
    │   └── test_trace_recorder.py
    ├── contract/
    │   ├── test_order.py
    │   ├── test_logistics.py
    │   ├── test_address.py
    │   └── test_refund.py
    └── integration/
        ├── test_idempotency_concurrent.py
        ├── test_fault_injection.py
        └── test_trace_propagation.py

frontend/                            # 本 feature 不涉及，Feature 005 再建
```

**Structure Decision**: 采用 `backend/` + `frontend/` 双目录（宪法已确认前端独立工程、后端不依赖前端构建）。本 feature 只建 `backend/`。选择独立 web-service 而非单文件，是因为后续 Agent 主循环、转人工、评测集都要调用它，需要清晰的模块边界。

**关于 `_prototype/`**：原型中的 `state.py`、`priority.py` 属于转人工 feature（003），不在此次实现范围内，且不直接复用。

## 设计要点

### 0. 状态用枚举，协议传输用数字码

**协议层（HTTP 响应体）的 `code` 是数字**，语义由枚举承载；代码内部禁止裸字符串和裸数字比较。

**编码规则：4 位数字，前 2 位表示类别，后 2 位表示该类下的具体原因。**

```python
class ResultCode(IntEnum):
    # ---- 1xxx 成功 ----
    OK = 1000                       # 成功

    # ---- 2xxx 未找到（不是错误，是"没有这个对象"）----
    ORDER_NOT_FOUND = 2001          # 订单不存在
    LOGISTICS_NOT_FOUND = 2002      # 该订单暂无物流信息（如未发货）
    REFUND_NOT_FOUND = 2003         # 退款单不存在

    # ---- 3xxx 业务拒绝（有明确原因，绪告知用户）----
    ORDER_STATUS_NOT_ALLOWED = 3001 # 订单当前状态不允许该操作
    AMOUNT_EXCEEDS_ORDER = 3002     # 退款金额超过订单金额
    AMOUNT_INVALID = 3003           # 金额非法（<=0 或超过两位小数）
    ADDRESS_INCOMPLETE = 3004       # 地址字段缺失
    ADDRESS_NOT_EDITABLE = 3005     # 已发货，不可修改地址
    DUPLICATE_REQUEST = 3006        # 幂等命中：重复请求返回首次结果（非错误）

    # ---- 4xxx 系统异常（调用方通常触发降级/转人工）----
    INTERNAL_ERROR = 4001           # 服务内部错误
    DEPENDENCY_TIMEOUT = 4002       # 下游超时（故障注入）
    INVALID_REQUEST = 4003          # 入参无法解析
```

**为什么分四个区间**：调用方只需要看千位就能决定走哪条分支，不需要维护 13 个值的映射表：

```python
if 1000 <= code < 2000:     处理成功
elif 2000 <= code < 3000:   资源不存在 → 转人工或引导用户
elif 3000 <= code < 4000:   业务拒绝 → 把 reason 展示给用户
else:                       系统异常 → 重试或降级
```

**关键细节**：

- **`DUPLICATE_REQUEST` 归在 3xxx 但语义上是"成功"**——幂等命中时返回首次结果，业务上是对的。单独给它一个码，是为了让调用方能区分"首次成功"和"重试命中"，同时避免把重试误判为故障。
- **未找到（2xxx）与系统异常（4xxx）严格分开**：前者是"确实没有这个订单"，后者是"没查成"。这是 FR-006 的核心要求。
- **数字码一旦发布不可复用**：删除某个码时留空位并标注 `RESERVED`，不能把 3007 挪去表示别的东西，否则老客户端的解释会错。

**其他状态字段仍用字符串枚举**——订单状态、退款状态、故障类型不是协议层分支判断的依据，用可读字符串更利于日志排查和种子文件维护。

```python
class OrderStatus(str, Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"   # 待支付
    PAID = "PAID"                          # 已支付
    SHIPPED = "SHIPPED"                    # 已发货
    DELIVERED = "DELIVERED"                # 已完成
    CLOSED = "CLOSED"                      # 已关闭
    REFUNDING = "REFUNDING"                # 退款中
    REFUNDED = "REFUNDED"                  # 已退款

class RefundStatus(str, Enum):
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

class FaultType(str, Enum):
    DELAY = "delay"
    TIMEOUT = "timeout"
    ERROR = "error"
    DIRTY = "dirty"
```

**序列化边界必须强类型校验**：

- 响应模型的 `code` 字段声明为 `ResultCode`（`IntEnum`），Pydantic 序列化为整数
- 调用方（Agent 工具层）用同一个 `ResultCode` 反序列化；**未知数字直接报错**，不允许静默降级为 `None` 或默认值——否则服务端新增了一个码，老客户端会静默走错分支
- 种子文件里的状态字符串加载时经 Pydantic 校验，非法值在启动阶段失败

**理由**：字符串字面量在多处使用时无法被编译期校验。`"NOT_FOUDN"` 这类拼写错误在 Python 里不会报错，会静默走错分支；大小写不一致（`"Rejected"` vs `"REJECTED"`）同样无法发现。枚举让错误在第一次使用时就以 `AttributeError` / `ValidationError` 暴露。

**禁止**：任何地方出现 `if resp.code == 3005` 或 `if resp.code == "REJECTED"` 这样的裸数字/裸字符串比较。业务代码一律比较枚举成员（`resp.code is ResultCode.ADDRESS_NOT_EDITABLE`）。

### 1. 结果模型（对应 FR-005 / FR-006）

所有业务端点返回统一信封，HTTP 状态码只表达传输层结果：

```
{
  "code": 3005,                                  // 数字码，含义见 0 节枚举
  "data": {...} | null,
  "reason": "订单已发货，不可修改地址" | null,     // 3xxx 时必有
  "meta": {
    "trace_id": "a1b2c3...",                     // 回显，便于排障
    "request_id": "refund:ORD123:..." | null     // 仅写操作回显
  }
}
```

- `2xxx`（没有这个对象）与 `4xxx`（没查成）严格分离
- `3xxx` 必须带 `reason`，调用方可以直接展示给用户
- 业务失败一律 HTTP 200，只有传输层问题才用 4xx/5xx——避免调用方把"业务拒绝"误当"系统故障"

**为什么业务失败也用 HTTP 200**：如果用 HTTP 404 表示"订单不存在"，Agent 的工具层就得从异常里区分业务与传输，容易出错。有了数字码，调用方读一次 `code` 就能分流，不用碰 HTTP 层。

### 2. 幂等键与链路追踪：两个不同的东西

这是两个**用途完全不同、生命周期不同**的标识，不能混用。

| 标识 | 谁生成 | 生命周期 | 用途 |
|---|---|---|---|
| **`request_id`**（幂等键） | **调用方**（Agent 工具层） | **跨重试保持不变** | 保证同一笔退款重复提交只生效一次 |
| **`trace_id`**（链路 ID） | 入口第一个服务生成 | **每次请求唯一**，重试会变 | 把一次请求在多个服务间的日志串起来，用于排障 |

**为什么必须分开**：

- `request_id` 如果每次请求都变，幂等就失效了——重试时服务端认不出这是同一笔业务
- `trace_id` 如果跨重试不变，排障时会把三次重试的日志混成一条，看不出到底失败了几次

**具体谁生成 `request_id`**：

Agent 工具层在**发起业务动作之前**生成，并存进会话状态：

```
用户说"我要退款"
  → Agent 决定调用退款工具
  → 生成 request_id = "refund:ORD123:<uuid>"，写入会话状态
  → 调用退款接口，带 request_id
  → 超时了
  → 重试，**复用同一个 request_id**
  → 服务端识别出重复，返回第一次的结果
```

**为什么由调用方生成而不是服务端生成**：服务端生成的话，客户端超时后重试拿不到上次的 `request_id`，就会生成新的，幂等失效。这是幂等键设计的基本原则——**谁能重试，谁负责生成**。

**格式约定**：`{动作}:{业务主键}:{UUID}`，例如 `refund:ORD202611090001:550e8400-e29b-41d4-a716-446655440000`。带上前缀便于在日志里一眼看出这是什么请求。

**`trace_id` 在本 feature 的处理**：mock 服务接收调用方传来的 `X-Trace-Id` 请求头，若没有则自己生成一个；所有日志与调用记录都带上它，并原样回显在响应里。它**不参与幂等判断**。

### 2.1 链路追踪的实现（对应 FR-017 / FR-018、SC-007 / SC-008）

**标识生成规则**

| 标识 | 来源 | 格式 |
|---|---|---|
| `trace_id` | 请求头 `X-Trace-Id`，缺失时服务端生成 | 32 位小写 hex（UUID4 去横线） |
| `request_id` | 请求体/请求头，由调用方生成 | `{action}:{业务主键}:{uuid}` |

**中间件链路**（`app/observability/trace.py`）：

```
请求进入
  → TraceMiddleware
     1. 读 X-Trace-Id，没有则生成 trace_id
     3. 绑定到 contextvar（供日志与调用记录读取）
     4. 开始计时
  → FaultMiddleware（见 4 节）
  → 业务处理
     每个 service 调用结束时向 CallRecorder 追加一条记录
  → 响应返回前
     1. 写入 elapsed_ms
     2. 把 trace_id 注入响应信封的 meta 字段
     3. 响应头也带 X-Trace-Id（便于调用方直接从 header 取）
  → 记录落库（CallRecorder）
```

**为什么用 contextvar 而不是层层传参**：`trace_id` 是横切关注点，如果每个函数都加一个 `trace_id` 参数，业务签名会被污染，且容易漏传。Python 的 `contextvars` 在 asyncio 下是协程隔离的，天然适合。日志格式器也从 contextvar 取，**任何日志自动带 trace_id，不需要显式传**。

**调用记录（CallRecord）**

```
CallRecord:
  trace_id: str          # 本次请求
  request_id: str | None # 幂等键，仅写操作有
  action: str            # order.get / logistics.get / address.update / refund.create
  input: dict
  output: dict | None
  code: ResultCode
  elapsed_ms: int
  fault_injected: str | None   # 注入了哪类故障，无则 null
  attempt: int           # 这是该 request_id 的第几次尝试
  created_at: datetime
```

**`attempt` 字段的由来**：收到请求时按 `request_id` 统计该键已出现的次数，+1 即为当前尝试序号。首次是 1，第一次重试是 2。这样排障时能直接看出"这笔退款被重试了 3 次"。

**存储与查询**

- 进程内环形缓冲（默认保留最近 10000 条），避免无界增长
- 提供两个只读查询端点：
  - `GET /_debug/calls?trace_id=xxx` → 该次请求的全部记录
  - `GET /_debug/calls?request_id=xxx` → 该笔业务的所有重试尝试（跨 trace_id）
- 这两个端点**只读**，不提供写入或删除能力

> 注意：`/_debug/*` 是调试端点，与宪法"页面上能走通的路径就是系统真实支持的路径"不冲突——它们不参与业务逻辑，只是观测窗口。生产可通过配置关闭。

**故障注入与 trace 的关系**：被注入故障的请求，`trace_id` 照常生成、记录照常写入，`fault_injected` 字段标记故障类型。这使得"哪次请求因为什么故障失败了"可以直接查出来，而不需要去翻日志文本。

### 3. 幂等实现（对应 FR-007 / SC-002）

```
处理顺序：
1. 按 (action, request_id) 查是否已存在记录 → 存在则直接返回首次结果（含原 refund_id）
2. 校验订单状态、金额
3. 在 asyncio.Lock（按 order_id 分段）内：再次检查 request_id，然后写入
```

- 用**按订单分段锁**而非全局锁，避免不同订单互相阻塞
- 并发测试：同一 `request_id` 并发 50 次，断言只产生 1 条退款记录、50 个响应内容一致
- 另一个测试：**不同** `request_id` 对同一订单并发退款，断言只有一笔成功、其余被拒（订单状态已变）
- 又一个测试：同一 `request_id` 但**订单号不同**，断言不会误判为同一次退款（防止幂等键跨业务串用）

### 4. 故障注入（对应 FR-012~015、SC-003/004）

**触发方式**：请求头 `X-Fault`，值为 JSON：

```
X-Fault: {"type": "timeout", "delay_ms": 5000}
X-Fault: {"type": "error", "status": 500}
X-Fault: {"type": "dirty", "mutate": "null_field", "field": "refund_id"}
X-Fault: {"type": "delay", "delay_ms": 800}
```

**实现**：FastAPI 中间件，在业务处理**之前或之后**介入：

| 类型 | 行为 |
|---|---|
| `delay` | 业务正常执行，返回前 sleep |
| `timeout` | sleep 到超过调用方超时阈值后直接断开（模拟真实超时） |
| `error` | 不执行业务，直接返回 5xx |
| `dirty` | 业务正常执行，返回前篡改响应体（字段置空/改类型/截断） |

**一致性保证（FR-015）**：`timeout` 与 `error` **必须在业务执行前拦截**，不能"业务做完了然后返回失败"——否则会出现"钱退了但调用方以为失败"，这正是要避免的真实现网事故。

> 注意区分：`delay`（业务执行 + 慢返回）与 `timeout`（业务不执行 + 超时）语义不同，前者测"调用方等太久怎么办"，后者测"不确定成功与否怎么办"。

### 5. 数据模型（对 spec 的 Key Entities 落地）

```
Order:      order_id, status, amount, address, created_at, updated_at
Logistics:  order_id, current_node, estimated_delivery, tracks[]
Refund:     refund_id, order_id, amount, status, request_id, created_at
Address:    receiver, phone, province, city, district, detail
FaultRule:  type, delay_ms, status, mutate, field
```

金额统一用 `Decimal`（避免浮点误差，`0.1 + 0.2 != 0.3` 那类问题在金额上是事故）。

### 6. 配置项

| 配置 | 默认 | 说明 |
|---|---|---|
| `MOCK_SEED_PATH` | `seed/orders.json` | 种子数据路径 |
| `MOCK_FAULT_ENABLED` | `true` | 故障注入总开关，生产可关 |
| `MOCK_CALLER_TIMEOUT_MS` | `3000` | 上层调用超时，决定 `timeout` 注入的睡眠时长 |
| `MOCK_REFUND_LOCK_SHARDS` | `16` | 分段锁数量 |
| `MOCK_CALL_RECORD_CAPACITY` | `10000` | 调用记录环形缓冲容量 |
| `MOCK_DEBUG_ENDPOINTS_ENABLED` | `true` | `/_debug/*` 查询端点开关，生产可关 |
| `MOCK_LOG_LEVEL` | `INFO` | 日志级别 |

## Complexity Tracking

无违规。本 feature 未引入超出宪法范围的复杂度。

刻意避免的复杂方案：

| 被否决的方案 | 原因 |
|---|---|
| 引入 OpenTelemetry / Jaeger 等完整链路追踪体系 | 本 feature 只有单个服务，用不上跨服务上报；contextvar + 环形缓冲足够，且不引入新依赖 |
| trace_id 通过函数参数层层传递 | 横切关注点，污染业务签名且易漏传 |
| 调用记录写文件或数据库 | 内存环形缓冲已满足排障需要，落盘属于后续可观测 feature 的范围 |
| 用真实数据库存业务数据 | spec 已确认"内存 + 种子"，加 DB 是过度设计 |
| 用 Redis 实现分布式锁 | 单进程场景，`asyncio.Lock` 足够；多进程需求尚未出现 |
| 故障注入做成独立控制接口（`POST /fault/configure`） | 状态会跨请求泄漏，违反"按请求维度"的已确认结论 |
| 用 HTTP 状态码表达业务失败 | 迫使调用方解析异常，与 FR-005 冲突 |

## 下一步

本 plan 确认后，由 `/speckit-tasks` 生成 `tasks.md`，再进入实现。

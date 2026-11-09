# Aftersales Mock Service

售后 Agent 依赖的 mock 业务服务。提供订单、物流、改地址、退款四个业务能力，以及**按请求维度生效的故障注入**——这是这个服务存在的意义：没有可控的故障，上层的降级与转人工能力无法被验证。

## 快速开始

```bash
# 安装
uv venv && uv pip install -e ".[dev]"

# 运行
uvicorn app.main:app --reload

# 测试
python -m pytest -q
```

Windows 下跑测试需带 `PYTHONIOENCODING=utf-8`，否则控制台 GBK 编码会让中文断言失败。

## 接口

所有业务接口**业务失败也返回 HTTP 200**，调用方读 `code` 分流。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/orders/{order_id}` | 查订单 |
| GET | `/orders/{order_id}/logistics` | 查物流 |
| POST | `/orders/{order_id}/refund` | 发起退款（需 `X-Request-Id`） |
| PUT | `/orders/{order_id}/address` | 改收货地址 |
| GET | `/_debug/calls` | 查调用记录（`?trace_id=` 或 `?request_id=`） |

### 响应格式

```json
{
  "code": 1000,
  "data": { ... },
  "reason": null,
  "meta": { "trace_id": "a1b2...", "request_id": null }
}
```

### 结果码

4 位分段，按千位分流：

| 区间 | 含义 | 调用方应该 |
|---|---|---|
| 1xxx | 成功 | 继续 |
| 2xxx | 未找到 | 引导用户，或转人工 |
| 3xxx | 业务拒绝 | 把 `reason` 展示给用户 |
| 4xxx | 系统异常 | 重试或降级 |

| 码 | 常量 | 含义 |
|---|---|---|
| 1000 | OK | 成功 |
| 2001 | ORDER_NOT_FOUND | 订单不存在 |
| 2002 | LOGISTICS_NOT_FOUND | 该订单暂无物流信息 |
| 2003 | REFUND_NOT_FOUND | 退款单不存在（预留） |
| 3001 | ORDER_STATUS_NOT_ALLOWED | 订单状态不允许该操作 |
| 3002 | AMOUNT_EXCEEDS_ORDER | 退款金额超过订单金额 |
| 3003 | AMOUNT_INVALID | 金额非法 |
| 3004 | ADDRESS_INCOMPLETE | 地址字段缺失 |
| 3005 | ADDRESS_NOT_EDITABLE | 已发货不可改地址 |
| 3006 | DUPLICATE_REQUEST | 幂等命中，返回首次结果（**业务上是成功的**） |
| 4001 | INTERNAL_ERROR | 服务内部错误 |
| 4002 | DEPENDENCY_TIMEOUT | 下游超时 |
| 4003 | INVALID_REQUEST | 入参无法解析 |

**结果码一经发布不可复用**。未占用的编号是预留位，不得挪用。

## 幂等

退款接口必须带 `X-Request-Id`。同一标识重复提交只产生一笔退款，后续请求返回 `3006` 和首次的 `refund_id`。

```
用户说"我要退款"
  → Agent 生成 request_id = "refund:ORD123:<uuid>"
  → 调用退款，超时
  → 重试，复用同一个 request_id
  → 服务端返回首次结果，不重复执行
```

**谁负责重试，谁负责生成 request_id**——服务端生成的话，客户端超时后拿不到上次的值，幂等就失效了。

## 链路追踪

`X-Trace-Id` 请求头，不传则服务端生成 32 位 hex。会回显在响应头和 `meta.trace_id` 里，并自动出现在所有日志中。

**`trace_id` 与 `request_id` 不是一回事**：

| | `trace_id` | `request_id` |
|---|---|---|
| 谁生成 | 入口服务 | 调用方 |
| 重试时 | **变** | **不变** |
| 用途 | 串联一次请求的日志 | 保证重复提交只生效一次 |

## 故障注入

通过 `X-Fault` 头按请求注入，不改变全局状态，后续请求不受影响。

```bash
# 延迟（业务执行了，只是慢）
curl "localhost:8000/orders/ORD1002" -H 'X-Fault: {"type":"delay","delay_ms":800}'

# 超时（业务不执行，直接超时）
curl "localhost:8000/orders/ORD1002/refund" -X POST -H 'X-Request-Id: x' \
  -H 'Content-Type: application/json' -H 'X-Fault: {"type":"timeout","delay_ms":5000}' \
  -d '{"amount":"10.00"}'

# 报错（业务不执行）
curl "localhost:8000/orders/ORD1002" -H 'X-Fault: {"type":"error","status":503}'

# 脏数据（业务执行了，响应被篡改）
curl "localhost:8000/orders/ORD1003" -H 'X-Fault: {"type":"dirty","field":"data.status"}'
```

### delay 和 timeout 的区别

| 类型 | 业务是否执行 | 测什么 |
|---|---|---|
| `delay` | **执行了** | 调用方等太久怎么办 |
| `timeout` | **不执行** | 不确定成功没成功怎么办 |

**`timeout` 必须在业务执行前拦截**。如果先退款再超时，就会出现"钱退了但调用方以为失败"——这是要避免的真实事故。测试里有专门断言这一点。

## 配置

全部走环境变量，见 `.env.example`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `MOCK_SEED_PATH` | `seed/orders.json` | 种子数据 |
| `MOCK_FAULT_ENABLED` | `true` | 故障注入总开关 |
| `MOCK_CALLER_TIMEOUT_MS` | `3000` | 上层超时，决定 timeout 注入的睡眠时长 |
| `MOCK_REFUND_LOCK_SHARDS` | `16` | 分段锁数量 |
| `MOCK_CALL_RECORD_CAPACITY` | `10000` | 调用记录环形缓冲 |
| `MOCK_DEBUG_ENDPOINTS_ENABLED` | `true` | `/_debug/*` 开关 |
| `MOCK_LOG_LEVEL` | `INFO` | 日志级别 |

## 数据

内存态 + 种子文件。启动时从 `seed/` 加载，运行中只改内存副本，**重启回到种子状态**。这是刻意的选择——保证评测每次从干净状态开始，结果可复现。

种子包含 8 条订单，覆盖 7 种订单状态。

## 结构

```
backend/
├── app/
│   ├── models/        枚举、领域模型、响应信封
│   ├── observability/ 链路追踪、调用记录、日志
│   ├── store/         种子加载、内存存储
│   ├── faults/        故障规则、故障注入中间件
│   ├── services/      四个业务能力
│   └── api/           路由
├── seed/orders.json
└── tests/             88 个用例
```

## 测试

```
tests/unit/         枚举、信封、故障规则、调用记录、存储
tests/contract/     四个业务能力的接口契约
tests/integration/  并发幂等、故障注入、链路传播
```

重点用例：
- `test_timeout_does_not_execute_business` — 超时不得留下副作用
- `test_same_request_id_concurrent_produces_one_refund` — 50 并发只退一次
- `test_different_request_ids_concurrent_only_one_succeeds` — 不同键并发只成功一笔
- `test_retry_keeps_request_id_but_changes_trace` — attempt 1/2/3，trace 各不相同

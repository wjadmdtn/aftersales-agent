# Tasks: 工具层与 mock 业务服务

**Input**: `/specs/001-tool-layer-mock/` — spec.md、plan.md

**Prerequisites**: spec.md（已确认）、plan.md（已确认）

**Tests**: 必须。本 feature 的宪法检查要求 spec 的每条 FR/SC 都有对应断言（原则 II）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 所属用户故事（US1~US4）

## Path Conventions

Web app 结构：`backend/` 与 `frontend/` 分离。本 feature 只涉及 `backend/`。

---

## Phase 1: Setup

**Purpose**: 建立 backend 工程骨架

- [ ] T001 创建 `backend/` 目录结构：`app/{models,observability,store,faults,services,api}`、`seed/`、`tests/{unit,contract,integration}`
- [ ] T002 编写 `backend/pyproject.toml`：Python >=3.11，依赖 fastapi / uvicorn / pydantic / pydantic-settings；开发依赖 pytest / pytest-asyncio / httpx；`[tool.pytest.ini_options]` 配 testpaths、pythonpath、asyncio_mode=auto
- [ ] T003 [P] 编写 `backend/.env.example`，列出 plan 第 6 节的 7 个配置项及其默认值
- [ ] T004 [P] 编写 `backend/tests/conftest.py`：ASGI app fixture、`httpx.AsyncClient`、每个用例前重置内存 store

---

## Phase 2: Foundational（阻塞所有用户故事）

**Purpose**: 所有故事共享的类型、存储、追踪与故障基础设施

**⚠️ 完成本阶段前不能开始任何用户故事**

- [ ] T005 在 `backend/app/models/enums.py` 定义 `ResultCode`（IntEnum，4 位分段编码，含 1000/2001-2003/3001-3006/4001-4003）、`OrderStatus`、`RefundStatus`、`FaultType`
- [ ] T006 在 `backend/app/models/results.py` 定义统一响应信封 `BizResult`：`code: ResultCode`、`data`、`reason`、`meta{trace_id, request_id}`；提供 `ok()` / `not_found()` / `rejected()` / `error()` 构造方法，`reason` 在 3xxx 时必填（用 `model_validator` 强制）
- [ ] T007 在 `backend/app/models/domain.py` 定义 `Order`、`Logistics`、`Address`、`Refund`；金额字段用 `Decimal` 并限制两位小数
- [ ] T008 在 `backend/app/config.py` 定义 `Settings`（pydantic-settings）：种子路径、故障开关、调用方超时、锁分片数、记录容量、debug 端点开关、日志级别；非法值启动即失败
- [ ] T009 在 `backend/seed/orders.json` 编写种子数据：至少 8 条订单覆盖 7 种 `OrderStatus`，其中 1 条不存在于数据中以测 NOT_FOUND
- [ ] T010 [P] 在 `backend/app/store/seed.py` 实现种子加载：读 JSON → Pydantic 校验 → 非法值启动失败
- [ ] T011 [P] 在 `backend/app/store/memory.py` 实现内存 store：按订单索引、退款记录（按 `(action, request_id)` 索引）、按 `order_id` 分段的 `asyncio.Lock`；提供 `reset()` 供测试使用
- [ ] T012 [P] 在 `backend/app/observability/recorder.py` 实现 `CallRecorder`：环形缓冲（容量可配）、`append()`、`find_by_trace_id()`、`find_by_request_id()`、`attempt_count(request_id)`
- [ ] T013 [P] 在 `backend/app/observability/trace.py` 实现 `TraceMiddleware`：读 `X-Trace-Id` 或生成 32 位 hex、绑定 `contextvars`、计时、把 `trace_id` 注入响应 `meta` 与响应头
- [ ] T014 [P] 在 `backend/app/observability/logging.py` 配置日志格式器，自动从 contextvars 取 `trace_id`
- [ ] T015 [P] 在 `backend/app/faults/rules.py` 定义故障规则解析：从 `X-Fault` 头解析 JSON → `FaultRule` 对象，非法配置返回 4003
- [ ] T016 [P] 在 `backend/app/faults/injector.py` 实现 `FaultMiddleware`：`timeout`/`error` 在业务执行**前**拦截；`delay`/`dirty` 在业务执行后、返回前介入；记录 `fault_injected`
- [ ] T017 在 `backend/app/main.py` 装配 FastAPI：注册中间件（顺序 Trace → Fault）、挂载路由、初始化 store 与 recorder
- [ ] T018 [P] 单元测试 `backend/tests/unit/test_trace_recorder.py`：环形缓冲淘汰、按 trace_id / request_id 查询、attempt 计数
- [ ] T019 [P] 单元测试 `backend/tests/unit/test_fault_rules.py`：四类故障解析、非法配置报错
- [ ] T020 [P] 单元测试 `backend/tests/unit/test_store.py`：种子加载、非法种子启动失败、分段锁不跨订单阻塞

**Checkpoint**: 基础设施就绪，可开始用户故事

---

## Phase 3: User Story 1 - 查询订单与物流 (Priority: P1) 🎯 MVP

**Goal**: 订单与物流查询，返回可区分的结果码

**Independent Test**: 给定种子数据，查询不同状态的订单得到正确状态；查询不存在的订单得到 `2001`；未发货订单查物流得到 `2002` 而非失败

### Tests for User Story 1

> 先写测试并确认失败，再实现

- [ ] T021 [P] [US1] 契约测试 `backend/tests/contract/test_order.py`：覆盖 FR-001、FR-002、FR-005、FR-006、FR-006a
  - 存在订单 → `1000` 且返回状态/金额/地址
  - 不存在订单 → `2001`（非 4xxx，非空对象）
  - 每类 code 落在正确的千位区间
- [ ] T022 [P] [US1] 契约测试 `backend/tests/contract/test_logistics.py`：覆盖 US1 场景 3、4
  - 已发货 → 返回节点与预计送达
  - 未发货 → `2002`（与"查询失败"严格区分）
  - 物流数据与订单状态不一致时 → 明确的结果码而非静默返回空

### Implementation for User Story 1

- [ ] T023 [P] [US1] `backend/app/services/order.py`：`get_order(order_id)` → `BizResult[Order]`
- [ ] T024 [P] [US1] `backend/app/services/logistics.py`：`get_logistics(order_id)` → `BizResult[Logistics]`
- [ ] T025 [US1] `backend/app/api/routes.py` 增加 `GET /orders/{order_id}` 与 `GET /orders/{order_id}/logistics`，接入 `CallRecorder`
- [ ] T026 [US1] 两个端点均遵守"业务失败仍返回 HTTP 200"，仅传输层异常用 4xx/5xx

**Checkpoint**: US1 可独立验证

---

## Phase 4: User Story 4 - 故障注入与容错验证 (Priority: P1)

> 放在 US2/US3 之前：退款与改地址的"失败路径"测试依赖故障注入可用

**Goal**: 四类故障可仅通过请求头触发，且不破坏数据一致性

**Independent Test**: 不修改任何业务代码，仅通过 `X-Fault` 头让接口超时/报错/变慢/返回脏数据，业务数据不被污染

### Tests for User Story 4

- [ ] T027 [P] [US4] 集成测试 `backend/tests/integration/test_fault_injection.py`：覆盖 FR-012~015、SC-003、SC-004
  - `delay`：业务已执行，耗时符合配置，数据有变化
  - `timeout`：**业务未执行**，数据无变化（FR-015 关键）
  - `error`：**业务未执行**，返回 4xxx 系统异常码
  - `dirty`：业务已执行，但响应字段被篡改
  - 无 `X-Fault` 头：行为完全正常
  - 故障请求的调用记录中 `fault_injected` 有值（SC-006）
- [ ] T028 [P] [US4] 集成测试 `backend/tests/integration/test_trace_propagation.py`：覆盖 FR-017、FR-018、SC-007、SC-008
  - 传入 `X-Trace-Id` → 响应 meta 与响应头原样回显
  - 不传 → 服务端生成 32 位 hex
  - 同一 request_id 重试 3 次 → 3 条记录 trace_id 不同、request_id 相同、attempt 为 1/2/3
  - `GET /_debug/calls?trace_id=` 能查到该次请求的记录
  - `GET /_debug/calls?request_id=` 能查到全部重试

### Implementation for User Story 4

- [ ] T029 [US4] 完善 `FaultMiddleware` 的执行时序（T016 已搭骨架，此处补齐一致性保证）
- [ ] T030 [US4] `backend/app/api/debug.py`：`GET /_debug/calls` 支持 `trace_id` / `request_id` 查询，受配置开关控制
- [ ] T031 [US4] 各 service 在返回前向 `CallRecorder` 追加记录，含 `attempt` 与 `fault_injected`

**Checkpoint**: 故障与追踪可独立验证

---

## Phase 5: User Story 2 - 发起退款 (Priority: P1)

**Goal**: 不可逆资金操作，幂等与校验必须严格

**Independent Test**: 同一 request_id 并发 50 次只产生 1 笔退款；不允许退款的订单被拒且无资金变动

### Tests for User Story 2

- [ ] T032 [P] [US2] 契约测试 `backend/tests/contract/test_refund.py`：覆盖 US2 场景 1、4、5
  - 已支付未退款 → `1000` + refund_id
  - 已退款 → `3001` 且不产生第二笔
  - 未支付 → `3001`
  - 金额 > 订单金额 → `3002`
  - 金额 <= 0 → `3003`
  - 金额恰好等于订单金额 → 成功；金额为订单金额 + 0.01 → `3002`
- [ ] T033 [US2] 集成测试 `backend/tests/integration/test_idempotency_concurrent.py`：覆盖 FR-007、SC-002
  - 同一 request_id 并发 50 次 → 1 条退款记录，50 个响应内容一致，其中 1 个 `1000`、49 个 `3006`
  - **不同** request_id 对同一订单并发 → 只有 1 笔成功，其余 `3001`
  - 同一 request_id 但订单号不同 → 不被误判为同一次退款

### Implementation for User Story 2

- [ ] T034 [US2] `backend/app/services/refund.py`：`create_refund(order_id, amount, request_id)`
  - 第一步：查 `(action, request_id)` 已存在 → 返回首次结果 + `DUPLICATE_REQUEST`
  - 第二步：校验状态与金额
  - 第三步：分段锁内二次检查 request_id 后写入
- [ ] T035 [US2] `POST /orders/{order_id}/refund` 端点，接入 recorder

**Checkpoint**: US1 + US4 + US2 独立可用

---

## Phase 6: User Story 3 - 修改收货地址 (Priority: P2)

**Goal**: 有前置条件约束的不可逆操作

**Independent Test**: 未发货订单改地址成功且可查回；已发货被拒且原地址不变；改为完全相同内容返回成功且无写操作

### Tests for User Story 3

- [ ] T036 [P] [US3] 契约测试 `backend/tests/contract/test_address.py`：覆盖 US3 场景 1-3 与边界
  - 未发货改合法地址 → `1000`，再查订单可见新地址
  - 已发货 → `3005`，原地址不变
  - 缺字段 → `3004` 且指出缺什么
  - 改为**完全相同**的内容 → `1000`，不产生写操作（已确认决策 3）
  - 订单不存在 → `2001`

### Implementation for User Story 3

- [ ] T037 [US3] `backend/app/services/address.py`：`update_address(order_id, address)`，内容相同时直接返回成功不写库
- [ ] T038 [US3] `PUT /orders/{order_id}/address` 端点

**Checkpoint**: 四个故事全部可独立验证

---

## Phase 7: Polish

- [ ] T039 全量测试通过，统计覆盖率；确认 spec 的 FR-001~018、SC-001~008 每条都有对应断言
- [ ] T040 编写 `backend/README.md`：如何启动、如何用 `X-Fault` 手动制造故障、如何查 `/_debug/calls`
- [ ] T041 生成 `backend/openapi.json` 并从 `specs/001-tool-layer-mock/contracts/` 归档接口契约
- [ ] T042 编写 `backend/Dockerfile` 与 `docker-compose.yml`（仅本服务）
- [ ] T043 逐条核对 Constitution Check：枚举无裸比较、配置全部外置、错误码不复用

---

## Dependencies & Execution Order

```
T001-T004 (Setup)
    ↓
T005-T020 (Foundational) ← 阻塞全部
    ↓
    ├─→ US1 (T021-T026) ─┐
    ├─→ US4 (T027-T031) ─┤ 可并行
    │                    ↓
    │              US2 (T032-T035) 需 US4 的故障注入与 recorder
    │                    ↓
    └────────────→ US3 (T036-T038)
                         ↓
                   Polish (T039-T043)
```

**关键依赖**：
- US2 的并发幂等测试被试依赖 recorder 的 attempt 统计（T012）
- US2/US3 的失败路径测试依赖 US4 的故障注入
- US3 不依赖 US2，可与 US2 并行

**任务数**：43 条，其中 [P] 标记 18 条

---

## Notes

- 每个 task 完成后跑定向测试，Phase 结束跑全量
- 测试先写并确认失败，再写实现（宪法原则 II）
- 提交粒度：按 Phase 提交，提交信息说明"为什么"
- T005 的 `ResultCode` 一旦实现，`2007`、`3007` 等未占用编号即为预留位，不得挪用

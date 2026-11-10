# Tasks: 订单详情页 + 客服前端（用户对话页 + 人工工作台）

**Input**: Design documents from `/specs/006-web-ui/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: 宪法 II 为 NON-NEGOTIABLE——每条验收条件必须有对应断言，故**每个 user story 均含测试任务**，且测试先于实现（Red → Green）。

**Prerequisites 状态**: spec 已于 2026-11-10 由负责人确认为 Approved，可进入实现。

**Organization**: 按 user story 分组，每个 story 可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属 user story（US1~US6）
- 所有路径相对仓库根目录

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 建骨架，不写业务逻辑

- [ ] T001 初始化 `frontend/` 工程：`package.json`（vue@3 / vue-router@4 / pinia / element-plus / vite / typescript / vitest / @vue/test-utils / @playwright/test，均锁版本并提交 `package-lock.json`）、`tsconfig.json`、`vite.config.ts`（`server.proxy` 代理 `/agent` `/handoff` `/chat` `/ws` → `:8000`）、`vitest.config.ts`、`index.html`、`src/main.ts`、`src/App.vue`
- [ ] T002 [P] 建立 `src/router.ts`：`#/order/:orderId` → `views/OrderDetail.vue`，`#/chat` → `views/UserChat.vue`，`#/console` → `views/AgentConsole.vue`
- [ ] T003 [P] 在 `backend/app/config.py` 增加配置项并校验（宪法 IV）：`ws_heartbeat_interval_s`（默认 20，gt 0）、`ws_idle_timeout_s`（默认 60，gt 0）、`ws_reconnect_base_s`（默认 1）、`ws_reconnect_max_s`（默认 30）、`chat_message_ttl_seconds`（默认 = session_ttl_seconds）、`chat_backfill_max_messages`（默认 200，ge 1）、`frontend_static_dir`（默认 `app/static`）、`demo_agent_id`（默认 `demo-agent`）、`demo_agent_name`（默认 `小王`）
- [ ] T004 [P] 创建 `backend/app/chat/__init__.py` 与 `backend/tests/conftest_app.py`：用 `starlette.testclient.TestClient` 起真实 `create_app()`（走生产装配，见 T009），产出 `ws_user(session_id,user_id)` / `ws_agent(agent_id)` / `http` 夹具；Redis 用 `fakeredis`，mock store 用 `routes.reset_store()`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有 story 共用的实时层核心。**未完成前任何 story 都不能开始实现**

- [ ] T005 [P] 在 `backend/tests/unit/test_ws_protocol.py` 写信封与消息类型断言（先红）：类型常量全集、信封字段 `type/session_id/seq/ts/payload`、无会话帧 `seq=0`
- [ ] T006 [P] 在 `backend/tests/unit/test_messages.py` 写消息存储断言（先红）：`seq` 从 1 单调递增、`after_seq` 过滤、TTL 生效、同一条流式回复 delta/done 共用同一 `seq`
- [ ] T007 [P] 在 `backend/tests/unit/test_session_registry.py` 写 `ChatState` 断言（先红）：字段齐全、`NORMAL` 在建会话时写入、`RESOLVED` 后拒绝写入、`last_seq` 单调
- [ ] T008 实现 `backend/app/chat/protocol.py`：`Envelope`/`ChatMessage`/`ChatState` Pydantic 模型 + 消息类型常量（字段与约束**逐字照抄** `data-model.md` §1/§3/§4）
- [ ] T009 实现 `backend/app/chat/messages.py`（`RPUSH chat:msg:{sid}`，`INCR chat:seq:{sid}`，`after_seq` 查询，`truncated` 判定）与 `backend/app/chat/session_registry.py`（`chat:state:{sid}` CRUD）
- [ ] T010 实现 `backend/app/chat/hub.py`：连接表（`session:{sid}` / `agents` 两类目标）、每连接独立 `asyncio.Queue` + 发送协程、`publish()`、`hello` 首帧、`ping` 心跳、`idle_timeout` 断开（close 4408）
- [ ] T011 实现 `backend/app/api/ws_routes.py`：`GET /ws/chat`（握手校验 `session_id`/`user_id`，失败 4401/4403；`order_id` 处理见 T064）与 `GET /ws/agent`（`agent_id`）
- [ ] T012 实现 `backend/app/main.py` 的 lifespan 生产装配（plan D9）：Redis 连接、`SessionStore`、`HandoffQueue`、`HandoffCoordinator`、LLM（`llm_base_url` 为空则 `StubLLMClient`）、`IntentClassifier`、`MockServiceClient`、policy（`policy_enabled` 时）、`agent_routes.configure_agent`、`handoff_routes.configure_handoff`、`chat` 模块装配；shutdown 关闭 hub 与 redis
- [ ] T013 在 `backend/tests/integration/test_production_boot.py` 写启动冒烟（先红，对应 FR-031）：`GET /health` 200、`/agent/health/llm` 200、`/handoff/queue` 200、`POST /agent/chat` 200、`GET /` 为 404（`frontend_static_dir` 不存在时不挂载）。**本任务须在 T014 之前完成**，否则 404 断言会与静态挂载冲突
- [ ] T014 在 `backend/app/main.py` 增加静态目录挂载：`frontend_static_dir` 存在则 `StaticFiles(html=True)` 挂 `/`，不存在则不挂（FR-022）

**Checkpoint**: 后端能生产启动、WS 能握手、消息与状态能落盘 —— story 可并行开工

---

## Phase 3: User Story 1 - 用户对话与流式回复 (Priority: P1) 🎯 MVP

**Goal**: 用户在对话页发消息，AI 流式回复，保留中断/重试/降级态

**Independent Test**: 发送「我的订单到哪了」，回复以多帧 `assistant.delta` 逐步出现，最后收到 `assistant.done`

### Tests

- [ ] T015 [P] [US1] `backend/tests/unit/test_streamer.py`（先红）：语义块切分（按 `。！？；\n`）、`streaming:false` 标记、LLM 未实现 `stream_reply` 时走分块、实现时走真流式
- [ ] T016 [P] [US1] `backend/tests/integration/test_ws_user_flow.py`（先红）：US1-1 消息上屏与 delta 序列、US1-2 中断（客户端断开后服务端不再推）、US1-3 断线保留已输出、US1-4 降级时 `assistant.done{degraded:true}`
- [ ] T017 [P] [US1] `frontend/src/components/__tests__/MessageStream.spec.ts`（先红）：delta 拼接、`done` 后定格、`degraded` 显示统一话术 + 转人工按钮、中断后保留文本

### Implementation

- [ ] T018 [US1] 实现 `backend/app/chat/streamer.py`：`user.message` → `assert_ai_may_speak` → `AgentLoop.handle`；优先 `getattr(llm,"stream_reply",None)` 真流式，否则完整回复按语义块推送（**不得 sleep 固定时长**，边界见 plan D5 与 research R3）
- [ ] T019 [US1] 在 `backend/app/api/ws_routes.py` 的 `/ws/chat` 接上 `user.message`：先发 `user.message.ack`（含 `local_id` 回填），再推 `assistant.delta*` + `assistant.done`；落库一条 `role=assistant` 消息，复用首个 delta 的 `seq`（data-model §3）
- [ ] T020 [P] [US1] 实现 `frontend/src/ws/socket.ts`：连接、`hello` 解析、心跳、指数退避重连（以 `hello.reconnect` 为准，本地常量兜底）
- [ ] T021 [P] [US1] 实现 `frontend/src/api/rest.ts`：`getOrder`（`GET /orders/{id}`）、`getLogistics`、`getSession`、`listSessions`、`getQueue`、`accept`、`decline`、`resolve`

### US1 补充：订单详情页与会话入口（FR-033~036）

- [ ] T063 [P] [US1] `backend/tests/integration/test_ws_order_entry.py`（先红）：握手带 `order_id` 建会话后 `state.order_id` 已写入；会话已存在时忽略 query 的 `order_id`；订单不存在 close 4404；此后发「我要退款」不再被追问订单号
- [ ] T064 [US1] 在 `backend/app/api/ws_routes.py` 的 `/ws/chat` 握手实现 `order_id` 处理（plan D10）：首次建会话校验订单存在并写入 `SessionState`，已存在则忽略
- [ ] T065 [P] [US1] `frontend/src/views/__tests__/OrderDetail.spec.ts`（先红）：渲染订单号/状态/金额/收货地址/下单时间；`2002` 时显示「暂无物流信息」；`code!=1000` 显示错误态；**不出现商品名**（数据里没有）
- [ ] T066 [US1] 实现 `frontend/src/views/OrderDetail.vue` 与 `frontend/src/components/OrderSummaryCard.vue`：数据全部来自 `GET /orders/{id}`，底部「联系客服」图标 → `router.push({name:'chat', query:{order_id}})`
- [ ] T067 [US1] `UserChat.vue` 接收 `order_id` query 并拼进 WS 握手；顶部显示「正在咨询：ORD1002」；刷新后从 `getSession` 恢复绑定订单（FR-035）
- [ ] T022 [US1] 实现 `frontend/src/stores/userSession.ts`：`sessionId` 持久于 `localStorage`（FR-007）、`userId` 固定演示账号、按 `(session_id,seq)` 去重、`seq` 跳号 200ms 缓冲后触发补齐
- [ ] T023 [US1] 实现 `frontend/src/components/MessageStream.vue` 与 `frontend/src/views/UserChat.vue`：消息列表、乐观上屏（`local_id`）、流式渲染、失败重试
- [ ] T024 [US1] `GET /chat/session/{session_id}` 后端实现（contracts/rest.md §4）+ `backend/tests/integration/test_session_query.py`（先红）：`after_seq` 过滤、404、`truncated`
- [ ] T025 [US1] 用户端刷新恢复：`UserChat.vue` 挂载时调 `getSession` 渲染历史与状态（FR-007、FR-029）

**Checkpoint**: 用户端可独立演示流式对话与刷新恢复

---

## Phase 4: User Story 2 - 状态可视化 (Priority: P1)

**Goal**: 界面准确反映当前服务方（四类：AI / 排队 / 人工 / 已结束）

**Independent Test**: 依次触发四种状态，状态条文案与输入框可用性正确

### Tests

- [ ] T026 [P] [US2] `backend/tests/integration/test_session_state_broadcast.py`（先红）：`NORMAL` 建会话时写入；`QUEUED`/`HUMAN_SERVING`/`RESOLVED` 迁移后推 `session.state`；`handoff_requested` 对外仍映射为「AI 接手中」（spec FR-004）
- [ ] T027 [P] [US2] `frontend/src/components/__tests__/HandoffBar.spec.ts`（先红）：四种状态的文案、`queued` 时输入框禁用、`resolved` 时禁用

### Implementation

- [ ] T028 [US2] 实现 `backend/app/chat/broadcast.py`：状态迁移 → 写 `ChatState` → 推 `session.state`；在 `HandoffCoordinator` 注入 `broadcaster`（默认 no-op，避免影响 003~005 既有测试）
- [ ] T029 [US2] 在 `backend/app/agent/loop.py` 的会话创建路径写入 `ChatState{state:NORMAL}`（data-model §1）
- [ ] T030 [US2] 实现 `frontend/src/components/HandoffBar.vue` + `ConnectionBanner.vue`：状态文案映射（`ai_serving`/`queued`/`human_serving`/`resolved`）、断线提示
- [ ] T031 [US2] 在 `frontend/src/views/UserChat.vue` 接入 `HandoffBar` 与输入框可用性联动（FR-006）

**Checkpoint**: 状态条在真实状态迁移下正确变化

---

## Phase 5: User Story 3 - 快捷转人工 (Priority: P1)

**Goal**: 一键转人工，不经意图识别，不重复入队

**Independent Test**: 点「转人工」直接进入排队；再点无第二个队列项

### Tests

- [ ] T032 [P] [US3] `backend/tests/integration/test_quick_handoff.py`（先红）：`user.handoff` 不调用 `IntentClassifier`（spy 挂在 `agent_routes.get_loop().classifier.llm` 的 `classify` 上，断言调用次数为 0）、`QUEUED` 重复点仅重放 `session.state`、`RESOLVED` 返回 `session_resolved`、同时推 `queue.update{added}`
- [ ] T033 [P] [US3] `frontend/src/components/__tests__/QuickHandoff.spec.ts`（先红）：点击只发一次 `user.handoff`、排队中按钮禁用、排队中发送提示「正在为您接入，请稍候」

### Implementation

- [ ] T034 [US3] 在 `backend/app/api/ws_routes.py` 实现 `user.handoff` 入站处理（contracts/websocket.md §4）：`TriggerResult.fire(USER_REQUEST,...)` → `coordinator.initiate` → 写状态 → 广播
- [ ] T035 [US3] 在 `frontend/src/views/UserChat.vue` 加「转人工」按钮，调用 `socket.send({type:'user.handoff'})`；`queued`/`human_serving`/`resolved` 时禁用
- [ ] T036 [US3] 排队态下发消息：`frontend/src/views/UserChat.vue` 拦截发送并提示 `queued_ask_time` 话术；`backend/app/chat/streamer.py` 在 `QUEUED` 下不调 `AgentLoop`，回 `handoff_scripts.queued_followup`（US3-3）

**Checkpoint**: 转人工路径全通，无 LLM 消耗

---

## Phase 6: User Story 4 - 人工工作台：队列与接入 (Priority: P1)

**Goal**: 队列按优先级排序、高风险标红、接入后移出、多会话切换

**Independent Test**: 多个会话排队，高优先级在前；接入后从队列消失并出现在「我的会话」

### Tests

- [ ] T037 [P] [US4] `backend/tests/integration/test_ws_agent_flow.py`（先红，**一票否决**）：`queue.update` 推送、两客户并发 `accept` 只有一个 200 另一个 409（FR-015/FR-032）、`accept` 后广播 `session.state`
- [ ] T038 [P] [US4] `backend/tests/integration/test_session_query.py` 增补：`GET /chat/sessions?agent_id=` 只返回该客服已接入会话（FR-030）
- [ ] T039 [P] [US4] `frontend/src/components/__tests__/QueueSidebar.spec.ts`（先红）：按优先级排序（同级按 `queued_at`）、`high` 红色、`queue.update` 增量刷新

### Implementation

- [ ] T040 [US4] `backend/app/chat/session_registry.py` 加抢锁：`SET chat:claim:{sid} <agent_id> NX EX <ttl>`；修改 `backend/app/api/handoff_routes.py` 的 `accept`/`decline`/`resolve` 接入抢锁与广播，并发失败返回 409 `{"detail":{"code":"already_claimed","owner_agent_id":...}}`（contracts/rest.md §2/§3）
- [ ] T041 [US4] 实现 `GET /chat/sessions`（contracts/rest.md §5）：按 `agent_id` 查 `chat:state`；实现时选定"索引 SET"或"SCAN"二选一并写入代码注释说明理由
- [ ] T042 [US4] 实现 `frontend/src/stores/agentConsole.ts`：`queue` / `mySessions` / `activeSessionId` / `messagesBySession`（各会话独立，US4-4）
- [ ] T043 [US4] 实现 `frontend/src/views/AgentConsole.vue` 与 `frontend/src/components/QueueSidebar.vue`：队列首拉 `GET /handoff/queue`、`queue.update` 增量、接入按钮调 `accept`、409 时提示「已被他人接入」
- [ ] T044 [US4] 在 `frontend/src/stores/agentConsole.ts` 订阅 `queue.update` 与 `session.state`；重连时重拉队列

**Checkpoint**: 工作台可接单，并发接入安全

---

## Phase 7: User Story 5 - 上下文包展示 (Priority: P1)

**Goal**: 接入前看清 AI 做了什么

**Independent Test**: 打开排队会话，摘要/已尝试动作/优先级依据/触发类型齐全；风险触发时无 AI 建议

### Tests

- [ ] T045 [P] [US5] `frontend/src/components/__tests__/ContextPackPanel.spec.ts`（先红）：四个字段渲染、`suggested_next_step === null` 时不渲染 AI 建议区块（FR-012）、摘要超 120 字截断展示
- [ ] T046 [P] [US5] `backend/tests/integration/test_context_pack_contract.py`（先红）：`GET /handoff/queue` 的每项含 `summary`(≤120)/`attempted_actions`/`priority_reason`/`trigger_type`/`suggested_next_step`（SC-004）

### Implementation

- [ ] T047 [US5] 实现 `frontend/src/components/ContextPackPanel.vue`：字段一律**原样展示后端值**，前端不重算优先级、不推断触发器（宪法 III）
- [ ] T048 [US5] 在 `frontend/src/views/AgentConsole.vue` 接入 `ContextPackPanel`，选中队列项即展示

**Checkpoint**: 工作台信息足以决定接或不接

---

## Phase 8: User Story 6 - 人工回复与结束 (Priority: P2)

**Goal**: 人工发消息用户端实时收到；结束双方同步；人工期间 AI 不插话

**Independent Test**: 人工发消息用户端实时看到；人工结束后两端显示已结束

### Tests

- [ ] T049 [P] [US6] `backend/tests/integration/test_human_reply.py`（先红）：`agent.message` 仅 owner 可发、`HUMAN_SERVING` 下 `human.message` 双向送达、用户消息**不触发 AgentLoop**（断言 `classify` 调用次数为 0，US6-2）、`resolve` 后双方收到 `session.state{resolved}`
- [ ] T050 [P] [US6] `backend/tests/integration/test_human_silence.py`（先红）：`HUMAN_SERVING` 下服务端不产生任何 `assistant.*` 帧（US6-2 / 宪法 III）

### Implementation

- [ ] T051 [US6] 在 `backend/app/api/ws_routes.py` 实现 `agent.message`：校验 `owner_agent_id == agent_id` 与状态 `HUMAN_SERVING`，落库并双向推 `human.message`
- [ ] T052 [US6] `HUMAN_SERVING` 下 `user.message` 只转发给 owner，不调 `AgentLoop`（US6-2）：在 `backend/app/chat/streamer.py` 的入口按 `ChatState` 分流，人工服务期间直接走转发分支
- [ ] T053 [US6] 在 `frontend/src/views/AgentConsole.vue` 加人工输入框与「结束会话」按钮（确认弹层），接 `agent.message` 与 `resolve`
- [ ] T054 [US6] 用户端 `UserChat.vue` 渲染 `human.message{role:'human'}`，样式区别于 AI 消息

**Checkpoint**: 完整人工接管闭环可用

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T055 [P] 断线补齐端到端：`backend/tests/integration/test_reconnect_backfill.py`（FR-018/025/027）：断线期间消息不丢（含**用户端断开时人工发的消息**）、重连 `after_seq` 补齐、超 200 条 `truncated:true`、不重复
- [ ] T056 [P] 演示路径全链路 `backend/tests/integration/test_demo_path.py`（**一票否决**，SC-001）：第⓪步建带订单的会话 + 九步走完，断言两端状态与消息序列，且第②步**不被追问订单号**；同时断言 **SC-002 首字延迟 < 2s**（记录首帧 `assistant.delta` 时间戳减请求时刻）与 **SC-003 状态同步 < 1s**（记录 `session.state` 广播到对端收到的时间差）
- [ ] T057 [P] Playwright `frontend/e2e/demo-path.spec.ts`：从 `/#/order/ORD1002` 起，点客服图标进入对话，真实后端跑完九步；断言文案用 `contains`（Q2：后端 `handoff_scripts.risk` 与 spec 文本不完全一致）
- [ ] T058 [P] 分辨率验收：在 `frontend/e2e/responsive.spec.ts` 以 1280×720 与 1920×1080 打开两个页面并截图（SC-005）
- [ ] T059 [P] 边界场景测试覆盖 quickstart §5 的七项（重复转人工、排队发消息、两人接入、断线重连、去重、降级、已结束后再发），写入 `backend/tests/integration/test_edge_cases.py`；另含人工端点缺 `agent_id` 握手返回 4401
- [ ] T060 更新 `README.md`：新增进度、启动方式（含 `frontend/`）、`npx playwright install chromium` 步骤、演示前重启后端、**以及必须写明的演示级限制**（无登录、sessionId 即凭证、人工端固定账号、**无订单归属校验**）
- [ ] T061 更新 `backend/Dockerfile`：多阶段构建，`frontend` 阶段 `npm ci && npm run build`，产物复制到 `app/static`（FR-021 的唯一自动校验点）
- [ ] T062 跑 `quickstart.md` 全流程并修正文档与实际不符处；`npm run typecheck && npm run build` 通过（SC-006）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001~T004)**: 无依赖
- **Foundational (T005~T014)**: 依赖 Setup —— **阻塞所有 story**
- **User Stories**: 均依赖 Foundational
- **Polish**: 依赖所需 story 完成

### User Story Dependencies

| Story | 依赖 | 说明 |
|---|---|---|
| US1 (P1) | Foundational | 需要 `streamer`、`socket.ts`、`userSession` |
| US2 (P1) | US1（弱） | `HandoffBar` 需要 US1 的 `socket.ts` 与 `userSession` |
| US3 (P1) | US1、US2 | 需要状态条展示排队态 |
| US4 (P1) | Foundational | 后端独立；前端另起 `agentConsole` |
| US5 (P1) | US4 | 需要在工作台选中共话 |
| US6 (P2) | US4 | 需要已接入会话 |

### Within Each Story

测试先写并确认红 → 模型/服务 → 端点/组件 → 集成。

### Parallel Opportunities

- T005/T006/T007 三个后端单测可并行；T020/T021 两个前端基础件可并行
- US4 后端（T037~T041）与 US1 前端（T020~T023）可跨 story 并行
- T055/T056/T057/T058/T059 五个验收任务互不冲突

---

## Parallel Example: User Story 4

```text
Task: "test_ws_agent_flow.py —— 并发接入唯一性"      (T037)
Task: "test_session_query.py —— /chat/sessions"       (T038)
Task: "QueueSidebar.spec.ts —— 排序与标色"            (T039)
```

---

## Implementation Strategy

### MVP First

1. Phase 1 + Phase 2（后端实时层骨架 + 生产装配）
2. **US1** → 独立验证：一条流式回复能跑通
3. 此时后端已能生产启动，可先行演示"对话"能力

### Incremental

- US1 → US2 → US3：用户端闭环
- US4 → US5 → US6：工作台闭环
- 最后跑 T056 九步演示路径作为整体验收

### 注意

- **不要**为让测试变绿而改断言（宪法 II）；断言与 spec 冲突时先停下来报告
- `HandoffCoordinator` 的 `broadcaster` 注入必须默认 no-op，否则 003~005 的既有测试会一起挂
- 流式分块**不得加 sleep 固定时长**（宪法 V）

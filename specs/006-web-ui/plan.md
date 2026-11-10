# Implementation Plan: 客服前端（用户对话页 + 人工工作台）

**Branch**: `006-web-ui` | **Date**: 2026-11-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-web-ui/spec.md`

## Summary

给已经跑通的 Agent（意图识别、工具编排、转人工、政策问答）补上**可被人操作的界面**，并补齐支撑界面所必需的**会话实时层**。

后端现状与 spec 之间存在一个必须正视的落差，本 plan 的第一部分就是把它补平：

| spec 要求 | 后端现状 | 本 plan 的处理 |
|---|---|---|
| AI 流式逐字输出 | `POST /agent/chat` 一次性返回 `reply` | 新增流式通道：LLM 支持 stream 走真流式，否则按**语义块**（句/段）推送，协议统一为 `assistant.delta` |
| 两端实时同步、不轮询 | 无任何推送；grep `websocket/StreamingResponse/sse` 零命中 | 新增 WebSocket 通道，用户端与人工端共用一套协议 |
| 用户端收人工消息 | 无通道、无消息存储 | 新增会话消息存储（Redis）与 `human.message` 双向推送 |
| 订单上下文由入口带入 | `order_id` 只能塞在 `POST /agent/chat` 的请求体里，WS 没有入口 | `/ws/chat` 握手增加 `order_id`，首次建会话时写入（plan D10） |
| 五类状态可视化 | `HandoffState.NORMAL` 从不落 Redis；`/queue` 只看得见排队会话 | 新增会话状态存储，`session.state` 从状态机唯一出口广播 |
| 刷新不丢上下文 | 会话只有 Agent 内部 `SessionState`，无对外可读接口 | 新增 `GET /chat/session/{id}` 状态与历史补齐接口（FR-007/FR-018 共用） |
| 后端可独立启动 | `_loop` / `_coordinator` 仅测试装配，`main.py` 起服务后 `/agent/chat` 直接 500 | 新增生产装配（lifespan 内建 Redis / 队列 / Agent / 政策），前端不构建也能启动 |

前端为独立工程 `frontend/`，Vue 3 + TypeScript + Vite + Element Plus（spec 已定），构建产物打进后端镜像 `app/static`，后端启动时挂载。

**关于"不开后门"的界定**：WebSocket 通道与既有 REST 端点的复用符合 spec 的 FR-019/FR-020。下列三项原不在 spec 文字内，已按负责人 2026-11-10 确认增补进 spec（见 spec 变更记录）：

| 新增项 | 服务于哪些 spec 要求 | 为何不能"只用已有接口" |
|---|---|---|
| WS 入站消息 `user.handoff` | FR-005（一键转人工，不经意图识别） | 现有唯一入口是 `POST /agent/chat`，它必经 `IntentClassifier`，不满足 FR-005 |
| `GET /chat/session/{id}` | FR-007（刷新不丢）、FR-018 / FR-025（重连补齐） | 现有无任何接口能读出会话历史与当前服务方 |
| `GET /chat/sessions` | FR-014（多会话并切换）/ FR-030 | `/handoff/queue` 只返回排队会话，不含已接入会话 |

这三项是"系统真实能力的查询/事件面"，服务任何调用者，不是为界面特设的旁路。按宪法 Governance 须同步增补 spec，已于 2026-11-10 写入（`user.handoff` 进消息类型表，查询面落为 FR-029~030，另补 FR-031/032 覆盖生产装配与并发接入）。

## Technical Context

**Language/Version**: Python 3.11+（后端）、TypeScript 5.6 / Node 20（前端）

**Primary Dependencies**:
- 后端新增：**无**。WebSocket 用 FastAPI/Starlette 自带实现；流式 LLM 复用已有 `httpx`
- 前端新增：`vue@3`、`vue-router@4`、`pinia`、`element-plus`、`vite`、`typescript`、`vitest`、`@vue/test-utils`、`@playwright/test`
  - 用途：路由 / 状态管理 / 组件库 / 构建 / 单测 / E2E
  - 替代方案：路由与状态管理可用 Vue 原生 `defineComponent` + `provide/inject` 手写，但本 feature 有"多会话切换 + 断线重连补齐"两个强状态场景，手写反而更多代码，故引入 pinia；组件库 spec 已定为 Element Plus，不另选

**Storage**:
- 复用 Redis（`settings.redis_url`）。新增键族：`chat:msg:{sid}`（消息列表）、`chat:state:{sid}`（会话对外状态）、`chat:seq:{sid}`（单调序号）
- 消息与状态均设 TTL，与 `session_ttl_seconds` 一致

**Testing**:
- 后端：pytest（现有夹具风格）+ `fakeredis`；WebSocket 用 `httpx` 的 `ASGITransport` 无法覆盖，改 `starlette.testclient.TestClient`（同步）或 `httpx_ws`——**为避免新增依赖，用 `TestClient`**
- 前端：vitest（组合式函数与 store）、Playwright（演示路径 E2E，对接真实后端）

**Target Platform**: 桌面浏览器（Chrome / Edge 最新两个大版本），分辨率 ≥ 1280×720

**Project Type**: web application（`backend/` 已有，新增 `frontend/`）

**Performance Goals**:
- 流式首字 < 2s（SC-002）
- 状态变更两端同步 < 1s（SC-003）
- 队列刷新（`queue.update`）< 500ms

**Constraints**:
- 前端构建失败阻断合并（SC-006、FR-021）
- 后端不依赖前端构建即可启动与测试（FR-022）
- 不引入真实电商/物流平台，不做登录，不做移动端
- 消息带单调序号，前端按 `(session_id, seq)` 去重（FR-027）

**Scale/Scope**: 单人演示规模；并发接入上限由现有单实例 Redis 队列决定，本 feature 不扩容

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 本 plan 的满足方式 | 结论 |
|---|---|---|
| **I. 规范先行** | 本 plan 派生自 `spec.md`；spec 已于 2026-11-10 按 plan 反馈增补（FR-029~032、`user.handoff`、FR-004/021 修订），实现前 plan 仍待负责人最终确认 | ✅（待确认） |
| **II. 测试即验收标准** | US1~US6 每个 Acceptance Scenario 映射到后端契约测试 / 前端 vitest / Playwright E2E；资金与状态机路径（确认退款、`accept`/`resolve`）标为一票否决项 | ✅ |
| **III. 规则可解释、可复现** | 前端**不重算**优先级、触发器与 AI 建议可见性，全部读后端 `HandoffContext`；`suggested_next_step` 是否为空由 `context_pack.py` 决定，前端只按"有没有值"渲染 | ✅ |
| **IV. 配置外置** | 新增配置项：`ws_heartbeat_interval_s`、`ws_idle_timeout_s`、`ws_reconnect_backoff_*`、`chat_message_ttl_seconds`、`frontend_static_dir`；全部进 `Settings`，启动校验非法值即失败 | ✅ |
| **V. 边界诚实** | ① 流式降级为**按语义块**推送时，`assistant.delta` 携带 `streaming: false`，前端不得伪造打字节奏；② 断线重连补齐失败时明确提示"部分消息可能缺失"，不静默；③ 非排队会话在 `/handoff/queue` 不可见是现状，本 plan 补 `my sessions` 查询而非让前端拼凑 | ✅ |
| 结构约定：状态机不渗透 HTTP | `session.state` 只在 `HandoffCoordinator` 迁移成功后广播，WS 层是消费者 | ✅ |
| 结构约定：外部依赖定义接口 | 流式能力通过 `IntentLLM.stream_reply()` 可选协议方法暴露，未实现即走分块降级 | ✅ |
| 禁止：spec 外扩范围 | 新增后端能力仅限 spec 的 FR-016~018、023~036 与消息类型表；`GET /chat/session/{id}` 服务于 FR-007/FR-018 | ✅ |
| 前端：调真实接口、不开后门 | 无 mock 专用路由、无伪状态接口；E2E 走真实后端 | ✅ |
| 依赖管理 | 后端零新增依赖；前端依赖见 Technical Context，均锁版本并提交 lock（`package-lock.json`） | ✅ |
| 前端工程约束：构建阻断合并（FR-021） | **不满足**。负责人 2026-11-10 决定不做 CI，无自动阻断机制，构建仅在 Dockerfile 与人工执行时校验（D8） | ⚠️ 已知缺口 |

**复杂度说明**：前端新增 8 个直接依赖，均非"凭习惯引入"——`pinia` 用于多会话切换与断线补齐，`vue-router` 用于两个页面的路由隔离，其余为 spec 指定或构建必需。Gate 无违反项，`Complexity Tracking` 留空。

## Project Structure

### Documentation (this feature)

```text
specs/006-web-ui/
├── plan.md          # 本文件
├── research.md      # Phase 0：实时层与流式的取舍记录
├── data-model.md    # Phase 1：会话状态、消息、WS 信封、Context Pack 对外结构
├── quickstart.md    # Phase 1：演示路径的可执行验证步骤
└── contracts/
    ├── websocket.md # WS 协议：握手、信封、消息类型、心跳、重连补齐
    └── rest.md      # 新增/复用 REST 端点与响应结构
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── chat/                      # 新增：会话实时层
│   │   ├── __init__.py
│   │   ├── hub.py                 # WS 连接管理器（按 role / session 分发，心跳，扇出）
│   │   ├── protocol.py            # WS 信封模型 + 消息类型常量 + 校验
│   │   ├── messages.py            # 消息存储（Redis list + 单调 seq）
│   │   ├── session_registry.py    # 会话对外状态读写（chat:state:{sid}）
│   │   ├── streamer.py            # LLM stream → assistant.delta 分块/真流式 + done/degraded
│   │   └── broadcast.py           # 状态机迁移 → session.state / queue.update 广播
│   ├── api/
│   │   ├── ws_routes.py           # 新增：/ws/chat（用户端）、/ws/agent（人工端）
│   │   └── chat_routes.py         # 新增：GET /chat/session/{id}、GET /chat/sessions
│   ├── handoff/
│   │   └── coordinator.py         # 修改：迁移成功后调用 broadcast（注入，默认 no-op）
│   ├── main.py                    # 修改：lifespan 生产装配 + 静态目录挂载
│   └── config.py                  # 修改：新增 WS/chat 配置项
├── tests/
│   ├── unit/
│   │   ├── test_ws_protocol.py
│   │   ├── test_streamer.py
│   │   └── test_messages.py
│   ├── integration/
│   │   ├── test_ws_user_flow.py
│   │   ├── test_ws_agent_flow.py
│   │   ├── test_reconnect_backfill.py
│   │   └── test_demo_path.py      # 演示路径 9 步（端到端，一票否决）
│   └── conftest_app.py            # 新增：走生产装配的 app 夹具（TestClient）
└── Dockerfile                     # 修改：复制 frontend 构建产物到 app/static

frontend/
├── index.html
├── package.json / package-lock.json / tsconfig.json / vite.config.ts / vitest.config.ts
├── playwright.config.ts
├── src/
│   ├── main.ts / App.vue / router.ts
│   ├── ws/
│   │   ├── socket.ts              # 连接、心跳、指数退避重连，暴露事件流
│   │   └── store.ts               # 按 (session_id, seq) 去重、乱序缓冲、补齐
│   ├── api/
│   │   └── rest.ts                # 会话状态/历史/队列/接入/拒绝/结束
│   ├── stores/
│   │   ├── userSession.ts
│   │   └── agentConsole.ts
│   ├── views/
│   │   ├── OrderDetail.vue        # 订单详情页（数据来自 GET /orders/{id}）
│   │   ├── UserChat.vue           # 用户对话页
│   │   └── AgentConsole.vue       # 人工工作台
│   └── components/
│       ├── OrderSummaryCard.vue   # 订单摘要卡（详情页 / 对话页顶部复用）
│       ├── MessageStream.vue      # 流式消息渲染（含中断/重试/降级态）
│       ├── HandoffBar.vue         # 五类状态条 + 快捷转人工按钮
│       ├── QueueSidebar.vue       # 队列列表（优先级排序、高风险标红）
│       ├── ContextPackPanel.vue   # 摘要/已尝试动作/优先级依据/触发类型/AI 建议
│       └── ConnectionBanner.vue   # 断线/重连/补齐提示
└── e2e/
    └── demo-path.spec.ts          # 演示路径 Playwright
```

**Structure Decision**: 沿用仓库现有的 `backend/` 单工程，新增平级 `frontend/`。后端实时层独立为 `app/chat/`，与 `app/handoff/` 并列——转人工的业务规则仍在 `handoff/`，`chat/` 只负责"把状态变化送到人眼前"，避免状态机与传输层互相渗透。

## 详细设计

### D1. WebSocket 连接与分发（`chat/hub.py`, `api/ws_routes.py`）

两个端点，**同一套协议**：

- `GET /ws/chat?session_id=<sid>&user_id=<uid>&order_id=<oid>`（`order_id` 可选，见 D10）：用户端。一个连接只关心自己的 `sid`
- `GET /ws/agent?agent_id=<aid>`：人工端。一个连接关心所有排队会话 + 自己已接入的会话

`Hub` 维护两张表：`role → set[Connection]`，`Connection` 持有 `send_queue: asyncio.Queue`，每个连接一个**独立发送协程**（慢客户端不阻塞他人）。`Hub.publish(target, envelope)` 支持三种目标：`session:{sid}`、`user:{uid}`、`agents`（全部人工端）。

**心跳**：服务端每 `ws_heartbeat_interval_s` 发 `{"type":"ping","seq":0}`，客户端回 `{"type":"pong"}`；超过 `ws_idle_timeout_s` 未收到任意帧则断开。

### D2. WS 信封（`chat/protocol.py`）

```json
{ "type": "assistant.delta",
  "session_id": "s-abc",
  "seq": 42,
  "ts": "2026-11-10T08:00:00.000Z",
  "payload": { "...": "见 contracts/websocket.md" } }
```

- `seq` 由 `chat/seq`（Redis `INCR chat:seq:{sid}`）分配，**同一会话内单调递增**，用户端与人工端看到的序号一致
- `ping/pong` 不带 seq
- 前端按 `(session_id, seq)` 去重；`seq` 跳号时触发补齐

### D3. 会话对外状态与五类可见性

spec 的"五类"对应现有机器的五态，**一一映射，不新增状态**：

| 界面状态 | `HandoffState` | 用户端展示 | 输入框 |
|---|---|---|---|
| AI 接手中 | `NORMAL` / `HANDOFF_REQUESTED` | 「AI 助手」 | 可用 |
| 排队等待 | `QUEUED` | 「正在接入人工客服」 | 禁用，点击发送提示"正在为您接入，请稍候" |
| 人工接手中 | `HUMAN_SERVING` | 「人工客服 xxx 正在为您服务」 | 可用（消息走 `user.message`） |
| 已解决 | `RESOLVED` | 「会话已结束」 | 禁用 |
| 已转接待定 | spec 未单列 | `HANDOFF_REQUESTED` 不单独展示，合并进 AI 接手中（转人工动作发出后立即进入 `QUEUED`） | 可用 |

- `NORMAL` 状态在会话首次创建时写入 `chat:state:{sid}`，解决"AI 期间无状态可读"的问题
- 状态只在 `HandoffCoordinator` 迁移成功后写入并广播 `session.state`；`RESOLVED` 后不再接受 `user.message`/`agent.message`（复用 `assert_transition` 的终态约束）

### D4. 快捷转人工（FR-005）

新增用户端 WS 入站消息 `user.handoff`（`payload: {}`）：

- 服务端**直接**调 `coordinator.detect` → 若未命中关键词，按 `TriggerType.USER_REQUEST` 强制触发（与 `loop._handoff(primary_intent="manual")` 同一语义）
- **完全不经过 `IntentClassifier`**，不消耗 LLM
- 幂等：已在 `QUEUED`/`HUMAN_SERVING` 时返回 `session.state` 重放，不重复入队（`queue.enqueue` 已有 `is_new` 语义）

> `user.handoff` 已增补进 spec 消息类型表（2026-11-10）。

### D5. 用户消息与 AI 流式（`chat/streamer.py`）

用户端发 `user.message` 后服务端：

1. 立即回 `user.message.ack`（带上服务端分配的 `seq` 与规范化后的消息 ID）
2. 状态为 `QUEUED` 时不调 Agent，回固定话术 `queued_followup`（`assistant.delta` + `done`）
3. 状态为 `HUMAN_SERVING` 时不调 Agent（`assert_ai_may_speak` 会抛 `SilenceViolation`），消息只转发给人工端
4. `NORMAL` 时调 `AgentLoop.handle`，按流式推送：
   - **真流式**：若 `IntentLLM` 实现了可选方法 `stream_reply(plan) -> AsyncIterator[str]`，逐 token 发 `assistant.delta{pending:true}`
   - **降级分块**：否则在 `handle()` 返回完整 `AgentReply` 后，按句末标点（`。！？；\n`）切块，逐块发送，首块**立即**发出、后续块仅做 `await asyncio.sleep(0)` 让出事件循环（**不 sleep 固定时长，不伪造节奏**），并在每个 delta 里标 `streaming:false`
   - 结束发 `assistant.done{reply_hash, tool_calls, intent, state, trace_id}`
   - 失败/降级发 `assistant.done{degraded:true}`，前端据此渲染统一降级话术 + 转人工按钮

> 当前 `openai_compat.py` 与 `stub.py` 均非流式。本 plan 给 `IntentLLM` Protocol 增加**可选**能力探测（`getattr(llm, "stream_reply", None)`），不破坏现有实现与测试。

### D6. 人工接入与消息（`chat/broadcast.py`, `api/handoff_routes.py`）

- 接入/拒绝/结束仍走现有 `POST /handoff/{accept,decline,resolve}`，但：
  - 接入前**先做并发抢锁**（`chat:claim:{sid}` `SET NX`），两人同时接入只有一个成功，另一个 409（FR-015）
  - 接入成功后记录 `owner_agent_id` 到 `chat:state:{sid}`
  - 三个动作成功后广播 `session.state`；`accept`/`decline` 同时广播 `queue.update{removed:[sid]}` 给全体人工端
- 人工发消息：WS `agent.message{session_id, text}` → 服务端校验 `owner_agent_id == agent_id` 且状态 `HUMAN_SERVING` → 持久化并发 `human.message` 给该 session 的用户连接与所有人工端（人工端多窗口可见）
- 用户发消息：`user.message` → 持久化并发 `human.message` 给 owner 人工端
- `queue.update` 的完整重发：新连上的客户端、或任何连接收到 `seq` 跳号，主动调 `GET /handoff/queue` 重拉

### D7. 断线重连与补齐（FR-018、FR-024、FR-025）

前端 `ws/socket.ts`：

- 指数退避：`1s, 2s, 4s, 8s, ..., 30s` 封顶，抖动 ±20%（参数以后端 `hello` 帧下发为准，前端常量仅兜底，见 Q4）
- 重连成功后调 `GET /chat/session/{sid}?after_seq=<本地最大 seq>` 补齐：
  - 返回 `{state, messages:[...], last_seq}`，前端合并去重
  - 若 `last_seq` 与本地差距超过 `chat_backfill_max_messages`（如 200），只补最近 N 条并在界面提示"部分早期消息已省略"

### D8. 前端工程约束

- `frontend/` 与 `backend/` 完全解耦：`npm run build` 产出 `frontend/dist`，Dockerfile 构建镜像时复制到 `backend/app/static`；`create_app()` 检测目录存在则 `StaticFiles` 挂载 `/`，不存在则返回 404（**后端启动不依赖该目录**，FR-022）
- 开发模式：Vite `server.proxy` 把 `/orders`、`/agent`、`/handoff`、`/chat`、`/ws` 代理到 `localhost:8000`
- **不做 CI**（负责人 2026-11-10 确认）。仓库无 CI 配置，本 feature **不新增**流水线。构建产物由 `frontend/package.json` 的 `build` 脚本产出，进镜像的一步由 Dockerfile 的 `RUN npm ci && npm run build` 承担——镜像构建失败即失败，这是目前唯一的自动阻断点。

  **因此 FR-021「构建失败阻断合并」在本仓库没有强制机制**：合并前是否通过 `npm run build` 只能靠人工纪律。此缺口如实记录，不假装已有 CI。若日后需要，补一条流水线执行 `npm ci && npm run typecheck && npm run test && npm run build` 即可，plan 的其他部分不受影响。

### D9. 生产装配（`main.py` lifespan）

```text
startup:
  redis = Redis.from_url(settings.redis_url)
  sessions = SessionStore(redis)
  queue = HandoffQueue(redis)
  coordinator = HandoffCoordinator(queue, settings, broadcaster=hub_broadcast)
  llm = build_llm(settings)              # llm_base_url 为空则 StubLLMClient
  classifier = IntentClassifier(llm, settings.keyword_hints)
  tools = MockServiceClient(settings.mock_service_base_url)
  policy = build_policy(settings) if settings.policy_enabled else None
  agent_routes.configure_agent(...)
  handoff_routes.configure_handoff(coordinator)
  chat_routes.configure_chat(messages, registry)
shutdown: hub.close_all(); await redis.aclose()
```

装配失败（Redis 连不上、配置非法）启动即失败——符合宪法 IV。

### D10. 订单详情页与会话入口（FR-033~036）

**业务入口**：用户在订单详情页点底部客服图标 → 打开对话页 → 订单上下文随会话带入。

```
订单详情页  #/order/:orderId
  GET /orders/{id}       → 订单号/状态/金额/收货地址/下单时间
  GET /orders/{id}/logistics → 物流（2002 时显示"暂无物流信息"）
  底部「联系客服」→ router.push({name:'chat', query:{order_id}})
        ↓
对话页  #/chat?order_id=ORD1002
  new WebSocket(`/ws/chat?session_id=..&user_id=..&order_id=ORD1002`)
  顶部显示「正在咨询：ORD1002」
```

**后端处理**（`/ws/chat` 握手）：

1. 会话不存在 → 校验订单存在（`tools.get_order`，`code==1000`），失败 close 4404；成功则 `SessionState(order_id=...)` 写入
2. 会话已存在 → **忽略 query 的 `order_id`**，以会话已绑定的为准，防止握手悄悄改写上下文
3. 不做订单归属校验：订单表无 `user_id`，本项目无登录（README 必须写明）

**订单恢复优先级**（A4）：页面 URL 的 `order_id` query 优先；无 query 时从 `GET /chat/session` 读会话已绑定订单；会话发生切换后以切换结果为准，刷新恢复最新绑定（FR-035/036）。

**前端数据真实性**：订单页数据一律来自 `GET /orders/{id}`，不写死。订单模型里**没有商品名/商品图字段**，详情页商品区只显示 `product_type` 品类标签，不编造商品名（宪法 V）。

**订单页不新增接口**：全部复用已有 mock 服务端点，无 UI 专用后门（FR-020）。

## 关键取舍

| 取舍点 | 选择 | 放弃的方案 | 理由 |
|---|---|---|---|
| AI 流式 | LLM 真流式 + 语义块降级 | 固定延时逐字（假打字） | 宪法 V 边界诚实：不伪造节奏；且 SC-002 可被真实测量 |
| 实时传输 | 单一 WebSocket 双向 | SSE + 第二条推送通道 | spec 决策 #1 已定；人工消息是服务端主动推送，SSE 收不到 |
| WS 实现 | FastAPI/Starlette 原生 + 自研 Hub | socket.io / broadcaster 库 | 宪法不引新依赖；本场景只是按 session 扇出，Hub 约 150 行 |
| 会话状态存储 | 新 Redis 键 `chat:state:{sid}` | 复用 `SessionState` | `SessionState` 是 Agent 内部状态，含 pending/clarify 等，不含"当前服务方"；混在一起会把传输层耦合进 Agent |
| 两人同时接入 | `SET NX` 抢锁 + 409 | 仅靠 Redis ZREM 结果 | `accept` 用的是 `get_pack` + `zrem`，两人都拿到 pack 不为空，会**都成功**——这是现存 bug，本 feature 必须修 |
| 对话历史存储 | Redis list，TTL 同 session | 不存、只靠前端 | FR-007（刷新不丢）+ FR-018/025（补齐）都需要服务端权威历史 |

## 风险与待确认项

| # | 项 | 影响 | 建议 |
|---|---|---|---|
| Q1 | ~~spec 消息类型表无 `user.handoff`~~ | — | **已定**（负责人 2026-11-10）：spec 已增补 `user.handoff` 与 FR-029~032，见 spec 变更记录 |
| Q2 | 演示路径 ④「我要投诉」→ AI 回复文本 | 现有 `handoff_scripts.risk` 是「非常抱歉……已为您优先接入人工客服」而非 spec 写的「已为您优先接入人工客服」，文案不同 | 前端不硬编码，直接展示后端话术；**E2E 断言用 `contains("优先接入人工客服")` 而非全等** |
| Q3 | ~~CI 平台~~ | — | **已定：不做 CI**（负责人 2026-11-10）。FR-021 无强制机制，靠人工纪律，见 D8 |
| Q4 | 重连退避参数由前端常量还是后端下发 | 宪法 IV 要求配置外置，但前端构建期无法读后端配置 | 方案：`/ws/chat` 首帧 `hello` 携带 `{heartbeat_interval_s, reconnect:{base, max}}`；前端以 `hello` 为准，本地常量仅作兜底 |
| Q5 | 演示路径第⑦步「人工客服 小王」 | 演示账号固定，展示名需可配 | 固定演示账号 `小 王`（`agent_id=demo-agent`，`display_name=小王`），进 `Settings` |
| Q6 | ~~`HANDOFF_REQUESTED` 是否单独呈现~~ | — | **已定**（负责人 2026-11-10）：界面按四类状态呈现，`handoff_requested` 并入「AI 接手中」，spec FR-004 已注明 |

| Q7 | stub LLM 默认返回 `Intent.GENERAL`，`keyword_hints` 为 `{}` | 本地/无 LLM 环境下除登记过的句子外**全部走澄清**，演示与 E2E 会卡住 | 生产装配时若 `llm_base_url` 为空，使用带演示响应表的 stub（`我要退款`/`确认`/`我要投诉`/`转人工` 等），并进配置项；quickstart 已写明 |
| Q8 | 退款成功后订单变 `REFUNDED`，不可重复演示 | 同一进程内第二次演示退款会失败 | quickstart 写明"每次完整演示前重启后端"；不为此改动业务状态机 |
| Q9 | 订单没有 `user_id`，无法校验归属 | 从订单页带 `order_id` 进入会话时后端无法验证这是不是该用户的订单 | 本项目不做登录，归属校验属于用户侧系统职责；README 必须写明"演示级，无权限控制"，不得对外宣称有此能力 |
| Q10 | 订单数据无商品名/商品图 | 订单详情页商品区显示不出商品 | 只显示 `product_type` 品类标签，不编造商品名（宪法 V）；如需商品信息须先扩 mock 数据，不在本 feature 范围 |

## Complexity Tracking

无宪法违反项，本节留空。

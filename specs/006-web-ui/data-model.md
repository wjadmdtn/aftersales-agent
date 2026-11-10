# Data Model：006 客服前端

本 feature 引入的实体分两类：

- **传输/呈现模型**（前端与 WS 直接消费）——新增，定义在本文件
- **领域模型**（已有）——`HandoffState`、`HandoffContext`、`HandoffEvent`、`Intent`、`SessionState`、`AgentReply`

原则：**新增实体只承载"送到人眼前的形状"，不复制业务规则。** 优先级、触发类型、AI 建议可见性一律以 `HandoffContext` 原值为准。

---

## 1. ChatState（会话对外状态）

存储：Redis key `chat:state:{session_id}`（JSON），TTL = `session_ttl_seconds`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `session_id` | str | 非空 | 主键 |
| `user_id` | str | 非空 | 会话所属用户 |
| `state` | `HandoffState` | 枚举五值 | 唯一权威状态；映射见 §2 |
| `agent_id` | str \| null | 仅 `HUMAN_SERVING` 时非空 | 当前服务的人工 ID |
| `agent_name` | str \| null | 与 `agent_id` 同存 | 展示名，如「小王」 |
| `order_id` | str \| null | 与 `SessionState.order_id` 同步 | 便于工作台展示 |
| `updated_at` | datetime | UTC | 最后迁移时间 |
| `last_seq` | int | ≥ 0 | 该会话已分配的最大消息序号 |

**写入规则**：只能由 `HandoffCoordinator` 在一次合法迁移之后写入（`assert_transition` 通过后）。不存在其他写入口。

**Redis 键族总表**

| 键 | 类型 | 内容 | TTL |
|---|---|---|---|
| `chat:state:{sid}` | String(JSON) | `ChatState` | `chat_message_ttl_seconds` |
| `chat:msg:{sid}` | List | `ChatMessage` 序列 | 同上 |
| `chat:seq:{sid}` | String(int) | 单调序号 | 同上 |
| `chat:claim:{sid}` | String | 接入抢锁，值为 `agent_id` | 同上（成功接入后不删除，表示 owner） |

**创建时机**：`SessionState` 首次创建时，同事务写入 `state=NORMAL`，解决"AI 服务期间无状态可读"。

**终态**：`RESOLVED` 后 `state` 不再变化；允许读取，拒绝 `user.message` / `agent.message` / `user.handoff`。

---

## 2. 状态映射（界面 vs 机器）

| `HandoffState` | 界面文案键 | 用户端输入框 | 人工端可操作 |
|---|---|---|---|
| `NORMAL` | `ai_serving` →「AI 助手」 | 可用 | 只读观察 |
| `HANDOFF_REQUESTED` | `ai_serving`（不单独展示） | 可用 | 只读观察 |
| `QUEUED` | `queued` →「正在接入人工客服」 | 禁用 | 接入 / 拒绝 |
| `HUMAN_SERVING` | `human_serving` →「人工客服 {agent_name} 正在为您服务」 | 可用 | 发消息 / 结束 |
| `RESOLVED` | `resolved` →「会话已结束」 | 禁用 | 只读 |

**为什么 `HANDOFF_REQUESTED` 不单独展示**：现有状态机里 `HANDOFF_REQUESTED → QUEUED` 在同一请求内完成（`coordinator.initiate` 直接返回 `QUEUED`），界面上永远看不到停留态。为它单开一个 UI 状态等于承诺了一个不可观测的状态，违反宪法 V。此点需负责人确认（plan 待确认项 Q6）。

---

## 3. ChatMessage（消息）

存储：Redis List `chat:msg:{session_id}`，`RPUSH`，TTL = `session_ttl_seconds`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | str | 全局唯一（uuid4 hex） | 消息 ID |
| `session_id` | str | 非空 | |
| `seq` | int | 同会话单调递增，从 1 开始 | `INCR chat:seq:{sid}` |
| `role` | enum | `user` \| `assistant` \| `human` \| `system` | 发送方 |
| `sender_id` | str \| null | `user`→user_id；`human`→agent_id | |
| `text` | str | 非空（`system` 也非空） | 正文 |
| `streaming` | bool | 仅 `assistant` 有意义 | `true`=LLM 真流式；`false`=完整回复按块推送 |
| `final` | bool | 默认 `true` | `assistant.delta` 的中间块为 `false`，`done` 之后该消息落库为 `true` |
| `ts` | datetime | UTC | 服务端时间 |
| `trace_id` | str \| null | 仅 `assistant` | 便于工作台跳转观察视图 |

**关键约束**：

- **流式片段不落库**。`assistant.delta` 只是推送片段，不单独分配序号、不写 Redis List。`assistant.done` 时把拼接完成的完整文本作为**一条** `role=assistant` 消息落库。理由：否则一次长回复会吃掉几十个 seq，且重连补齐会把半截回复当成历史。
- **一条流式回复共用一个 `seq`**：收到该轮首个 `assistant.delta` 时 `INCR chat:seq:{sid}` 一次，后续 delta 与 `done` 沿用。落库消息复用同一 `seq`。客户端与历史因此对同一条回复看到同一个序号，补齐不会产生重复。
- **稳定排序的取舍**：当流式回复仍在进行、人工又发来消息时，人工消息会取到更大的 seq（`INCR` 已发生），因此可能先于流式回复落库。界面按 seq 渲染时，流式回复会显示在人工消息之后，与实际到达顺序不同。这是"一条回复一个 seq"的已知代价；演示路径中两者不会并发，故接受，**不在前端插队伪造顺序**（FR-028）。

---

## 4. Envelope（WS 信封）

所有 WS 帧统一外层结构（详见 `contracts/websocket.md`）：

| 字段 | 类型 | 约束 |
|---|---|---|
| `type` | str | 见消息类型表 |
| `session_id` | str \| null | 人工端的全局事件（如 `queue.update`）为 null |
| `seq` | int | 会话内消息序号；无会话上下文的帧（`ping`/`pong`/`hello`/`queue.update`）= 0。**与 `ChatState.last_seq` 同源**：`ChatState.last_seq` 恒等于该会话已分配的最大 `seq` |
| `ts` | datetime | UTC |
| `payload` | object | 按 type 定义 |

---

## 5. QueueView（队列项，前端消费形状）

来源：`GET /handoff/queue`，**字段与 `HandoffContext` 一一对应，不增减业务字段**

| 字段 | 来源 | 界面用途 |
|---|---|---|
| `session_id` | `HandoffContext.session_id` | 点击进入 |
| `summary` | `.summary`（≤120 字） | 侧边栏一行摘要 |
| `priority` | `.priority` | 排序、标色（`high`→红） |
| `priority_reason` | `.priority_reason` | 上下文包"优先���依据" |
| `trigger_type` | `.trigger_type` | 上下文包"触发类型" |
| `trigger_detail` | `.trigger_detail` | 触发类型下的补充 |
| `attempted_actions` | `.attempted_actions` | 「已查过：订单、物流」 |
| `suggested_next_step` | `.suggested_next_step` | **值为 null 时不渲染该区块**（风险场景后端即置空） |
| `primary_intent` | `.primary_intent` | |
| `sentiment` | `.sentiment` | |
| `order_id` | `.order_id` | |
| `queued_at` | `.queued_at` | 同级按时间排序 |

**FR-012 的实现**：前端不判断"是否为风险"，只看 `suggested_next_step === null` 就不显示 AI 建议区块。判定权在后端 `context_pack.build_context`。

排序规则由后端 `HandoffQueue.peek` 的 ZSET 保证（`-PRIORITY_SCORE*1e6 + queued_at`），前端**不重排**。

---

## 6. SessionSnapshot（补齐视图）

来源：`GET /chat/session/{session_id}?after_seq=N`

| 字段 | 类型 | 说明 |
|---|---|---|
| `state` | `ChatState` | 见 §1 |
| `messages` | `ChatMessage[]` | `seq > N` 的消息，按 seq 升序 |
| `last_seq` | int | 服务端该会话最大 seq |
| `truncated` | bool | 超过 `chat_backfill_max_messages` 时为 true |

---

## 7. 状态迁移表（界面可观测的部分）

| 触发 | 起点 → 终点 | 广播目标 | 附带帧 |
|---|---|---|---|
| 用户快捷转人工 | `NORMAL` → `QUEUED` | `session:{sid}` + `agents` | `session.state`, `queue.update`(added) |
| 用户排队中再转 | `QUEUED` → `QUEUED` | 无（幂等） | 回 `session.state` 重放 |
| 人工接入 | `QUEUED` → `HUMAN_SERVING` | `session:{sid}` + `agents` | `session.state`, `queue.update`(removed) |
| 人工拒绝 | `QUEUED` → `HUMAN_SERVING` | `session:{sid}` + `agents` | `session.state`, `queue.update`(removed), `assistant.done`(declined 话术) |
| 人工结束 | `HUMAN_SERVING` → `RESOLVED` | `session:{sid}` + `agents` | `session.state` |
| 两人同时接入 | 仅一个 `SET NX` 成功 | 失败方 HTTP 409 | — |

**不可迁移**：`RESOLVED` 出发的任何边（现有 `assert_transition` 已强制）；`HUMAN_SERVING → NORMAL`（现有 `_ALLOWED` 刻意缺失）。前端不得提供任何绕过这些约束的按钮。

---

## 8. 前端 Store 形状（非持久化）

### `userSession` store

| 字段 | 类型 | 说明 |
|---|---|---|
| `sessionId` | string | 来自 `localStorage`，首次生成 uuid（FR-007） |
| `userId` | string | 固定演示账号，来自 `localStorage` |
| `state` | `ChatState` | 由 `session.state` 帧驱动 |
| `messages` | `ChatMessage[]` | 按 seq 排序去重 |
| `pendingAssistant` | `{seq, text, streaming} \| null` | 正在流式的消息 |
| `connection` | `connecting` \| `open` \| `reconnecting` \| `closed` | |
| `lastError` | `{code, retryable} \| null` | |

### `agentConsole` store

| 字段 | 类型 | 说明 |
|---|---|---|
| `agentId` / `agentName` | string | 固定演示账号（`demo-agent` / `小王`） |
| `queue` | `QueueView[]` | 由 `GET /handoff/queue` 与 `queue.update` 驱动 |
| `mySessions` | `ChatState[]` | 自己接入的会话 |
| `activeSessionId` | string \| null | 当前打开的会话 |
| `messagesBySession` | `Record<string, ChatMessage[]>` | 各会话独立（US4-4） |
| `connection` | 同上 | |

**去重规则**：所有入站消息按 `(session_id, seq)` 去重；`seq` 出现跳号且该 seq 尚未到达时，进入 200ms 缓冲窗口，超时仍未到达则触发 `GET /chat/session/{sid}?after_seq=本地最大 seq` 补齐。

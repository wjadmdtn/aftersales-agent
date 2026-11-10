# Contract：REST 接口

**原则**：复用已有端点；新增端点只服务于 spec 的 FR-007（刷新保持）、FR-018 / FR-025（断线补齐）与 FR-014（多会话切换）。**不为 UI 效果新增端点**（FR-020）。

---

## 1. 复用（不修改响应结构）

### 订单详情页的数据来源（FR-033）

`GET /orders/{order_id}` 返回信封 `{code, data, reason, meta}`，`data` 字段：

| 字段 | 类型 | 详情页用途 |
|---|---|---|
| `order_id` | str | 标题 |
| `status` | enum | 状态标签（`PENDING_PAYMENT`/`PAID`/`SHIPPED`/`DELIVERED`/`CLOSED`/`REFUNDING`/`REFUNDED`） |
| `amount` | decimal | 金额 |
| `address` | object | 收货人、电话、省市区、详细地址 |
| `created_at` | datetime | 下单时间 |
| `product_type` | str \| null | 品类（前端可展示为标签，无则隐藏） |

物流区块调 `GET /orders/{order_id}/logistics`，`code==2002`（无物流）时显示「暂无物流信息」。

**已知缺口**：订单数据里**没有商品名称与商品图片字段**。详情页「商品」区域只能显示品类标签，不能显示商品名/图。不得在前端编造商品名——这是数据缺失，如实呈现（宪法 V）。

**错误处理**：`code!=1000` 时按 `code` 分段提示，与后端 `present.render` 的文案保持一致，不另造文案。

| 方法 | 路径 | 用于 | 现状 |
|---|---|---|---|
| `GET` | `/orders/{order_id}` | **订单详情页渲染（FR-033）** | 已存在，`{code,data,reason,meta}` 信封，前端读 `code==1000` |
| `GET` | `/orders/{order_id}/logistics` | 订单详情页物流区块 | 已存在 |
| `POST` | `/agent/chat` | 保留给无 WS 的调试与既有测试 | 已存在，006 不删不改 |
| `GET` | `/handoff/queue` | 人工端队列首拉与重连重拉 | 已存在 |
| `POST` | `/handoff/accept` | 接入 | **修改**：加抢锁，并发时 409 |
| `POST` | `/handoff/decline` | 拒绝 | 修改：拒绝后广播 |
| `POST` | `/handoff/resolve` | 结束 | 修改：结束后广播 `session.state` |
| `GET` | `/health` | 启动冒烟 | 已存在 |

---

## 2. `POST /handoff/accept`

**请求**

```json
{ "session_id": "s-abc", "agent_id": "demo-agent", "agent_name": "小王" }
```

`agent_id` / `agent_name` 为 006 新增字段（可选，缺省读 `Settings.demo_agent_*`）。

**响应 200**

```json
{ "state": "human_serving", "events": ["accepted"], "agent_id": "demo-agent", "agent_name": "小王" }
```

**响应 409** — 该会话已被他人接入

```json
{ "detail": { "code": "already_claimed", "owner_agent_id": "demo-agent-2" } }
```

**语义**
1. `SET chat:claim:{sid} <agent_id> NX EX <session_ttl>`；失败 → 409
2. 成功后调现有 `coordinator.human_accept`
3. 写 `ChatState{state:HUMAN_SERVING, agent_id, agent_name}`
4. 广播 `session.state` 给 `session:{sid}` 与 `agents`；广播 `queue.update{removed:[sid]}`
5. 任一步失败 → 释放 claim → 500 `server_busy`

**不变式**：`HUMAN_SERVING` 的会话恰好有一个 owner，由 claim key 保证（FR-015）。

---

## 3. `POST /handoff/decline` / `POST /handoff/resolve`

**请求**：`{ "session_id": "s-abc", "agent_id": "demo-agent" }`

- `decline`：校验 caller 为队列内会话（无 owner 也可拒绝）→ 调 `human_decline` → 广播 `session.state` + `queue.update{removed}` + `assistant.done`（`handoff_scripts.declined`）
- `resolve`：**必须**为 owner → 调 `coordinator.resolve` → 写 `ChatState{state:RESOLVED}` → 广播 `session.state`

非 owner 调 `resolve` → `403 {"detail":{"code":"not_owner"}}`。

---

## 4. `GET /chat/session/{session_id}`（新增）

**查询参数**

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `after_seq` | int | 0 | 只返回 `seq > after_seq` 的消息 |
| `limit` | int | 200 | 上限 `chat_backfill_max_messages` |

**响应 200**

```json
{
  "state": {
    "session_id": "s-abc",
    "user_id": "u-1",
    "state": "human_serving",
    "agent_id": "demo-agent",
    "agent_name": "小王",
    "order_id": "ORD1002",
    "updated_at": "2026-11-10T08:00:00Z",
    "last_seq": 12
  },
  "messages": [
    { "id": "…", "session_id": "s-abc", "seq": 1, "role": "user",
      "sender_id": "u-1", "text": "我要退款", "streaming": false,
      "final": true, "ts": "2026-11-10T07:59:00Z", "trace_id": null }
  ],
  "last_seq": 12,
  "truncated": false
}
```

**响应 404**：无该会话的 `ChatState` 与任何消息 → `{"detail":{"code":"session_not_found"}}`

**用途**
- 用户端刷新后恢复会话与历史（FR-007）
- 用户端断线重连补齐（FR-018 / FR-025）
- 人工端切换会话时拉历史（US4-4）

**边界**：本端点不做鉴权（spec 假设无登录）。会话 ID 即凭证。README 必须写明这是演示级设计。

---

## 5. `GET /chat/sessions`（新增）

**查询参数**：`agent_id`（人工端）或 `user_id`（用户端），二选一。

**响应 200**

```json
{
  "items": [
    { "session_id": "s-abc", "state": "queued", "agent_id": null,
      "agent_name": null, "order_id": "ORD1002",
      "last_message": "我要投诉", "last_ts": "2026-11-10T08:00:00Z" }
  ]
}
```

**用途**：人工端"我的会话"列表（FR-014），以及用户端历史会话（可选展示）。

**数据来源**：`chat:state:*` 扫描（演示规模下 `SCAN` 可接受）。实现时用 `SET chat:sessions:index` 维护索引以避免扫描；若规模不超过几十条，直接 `SCAN` 也可接受——**实现阶段二选一，需在 tasks 里写明**。

---

## 6. 启动冒烟

```text
GET /health                       → 200 {"status":"ok"}
GET /agent/health/llm             → 200（生产装配后不再 500）
GET /handoff/queue                → 200（生产装配后不再 500）
POST /agent/chat {…}              → 200（生产装配后不再 500）
GET /                             → 200 或 404（取决于静态目录是否存在）
```

前四项是本 feature 必须补齐的**生产装配**验收；第 5 项两种结果都合法（FR-022）。

---

## 7. 错误格式统一

所有新增端点错误体：`{"detail": {"code": "<snake_case>", "message": "<中文>", ...}}`。

已有端点（`/agent/chat`、`/handoff/*` 的 404）沿用 FastAPI 默认 `{"detail": "..."}` 字符串，**本 feature 不改既有错误格式**，避免影响 003~005 已通过的测试。

# Contract：WebSocket 协议

用户端与人工端**共用同一套协议**（FR-023）。差异只在端点的入站消息集合与订阅范围。

---

## 1. 端点

| 端点 | 使用者 | 查询参数 | 订阅范围 |
|---|---|---|---|
| `GET /ws/chat` | 用户端 | `session_id`, `user_id`, `order_id`（可选） | 仅本会话的 `session:{sid}` 帧 + 本用户的 `user:{uid}` 帧 |
| `GET /ws/agent` | 人工端 | `agent_id` | `session:{sid}`（自己已接入的）+ 全体 `agents` 帧 |

**`order_id` 语义（FR-034）**：用户从订单页进入客服时带入。

- 会话首次建立（`SessionState` 不存在）时，`order_id` 写入会话，此后所有 `user.message` 默认针对该订单——用户不必再说订单号
- 会话已存在时，**以会话里已绑定的订单为准**，query 的 `order_id` 被忽略（避免同一会话的订单上下文被握手参数悄悄改写）
- 用户仍可用「消息中出现新订单号」显式切换，走现有 `loop._switch_order` 逻辑（FR-036）
- **不校验订单归属**：订单表没有 `user_id`，本项目不做登录。此点必须在 README 写明，不得对外宣称有权限控制

握手失败（参数缺失 / 会话属于他人 / 订单不存在）：

```text
close code 4401，reason "invalid session"
close code 4403，reason "session not owned"
close code 4404，reason "order not found"     # 首次建会话且 order_id 不存在
```

服务端不校验账号口令（spec 假设：不做账号体系，人工端固定演示账号）。**这不是可上线形态，README 必须写明。**

---

## 2. 信封

```json
{
  "type": "assistant.delta",
  "session_id": "s-abc",
  "seq": 7,
  "ts": "2026-11-10T08:00:00.000Z",
  "payload": { }
}
```

- `seq`：同会话内单调递增，用于去重与补齐（FR-027）。无会话上下文的帧 = `0`
- `ts`：服务端 UTC 时间，ISO 8601 带毫秒
- 客户端发来的入站帧可省略 `seq`/`ts`，服务端补

---

## 3. 消息类型

### 3.1 系统帧

| type | 方向 | payload | 说明 |
|---|---|---|---|
| `hello` | S→C | `{heartbeat_interval_s, idle_timeout_s, reconnect:{base_s,max_s}, server_time}` | 连接建立后第一帧；前端据此配置心跳与退避（plan Q4） |
| `ping` | S→C | `{}` | 每 `heartbeat_interval_s` 一次 |
| `pong` | C→S | `{}` | 客户端应答任意 `ping` |
| `error` | S→C | `{code, message, retryable}` | 见 §6 |

### 3.2 用户端

| type | 方向 | payload | 说明 |
|---|---|---|---|
| `user.message` | C→S | `{text}` | 用户发消息（FR-001） |
| `user.message.ack` | S→C | `{local_id, message: ChatMessage}` | 服务端受理，回填 `seq` 与 `id`。`local_id` 由客户端在发送时生成，用于乐观上屏去重 |
| `user.handoff` | C→S | `{}` | **快捷转人工（FR-005）**，不经过意图识别；见 §4 |
| `assistant.delta` | S→C | `{msg_id, seq, text_delta, streaming}` | AI 流式片段（FR-002） |
| `assistant.done` | S→C | `{msg_id, seq, text, intent, state, tool_calls, trace_id, degraded}` | AI 输出结束 |
| `session.state` | S→C | `ChatState` | 状态变更（FR-004/016） |

### 3.3 人工端

| type | 方向 | payload | 说明 |
|---|---|---|---|
| `agent.message` | C→S | `{session_id, text}` | 人工发消息（US6-1） |
| `queue.update` | S→C | `{added: string[], removed: string[], depth: int}` | 队列变化；`session_id=null`, `seq=0` |
| `session.state` | S→C | `ChatState` | 自己接入的会话或队列内的会话状态变化 |

### 3.4 双向

| type | 方向 | payload | 说明 |
|---|---|---|---|
| `human.message` | S→C | `ChatMessage`（`role=human` 或 `role=user`） | 人工消息推给用户；用户消息推给人工（US6-1/2） |

**`human.message` 的方向语义**：
- 人工发出 → 服务端推 `human.message{role:"human"}` 给该会话的用户连接与全体人工端
- 用户发出 → 服务端推 `human.message{role:"user"}` 给 owner 人工端

AI 在人工服务期间**不产生任何帧**（`assert_ai_may_speak` 在协调器层硬拦截）。前端不得在 `HUMAN_SERVING` 下渲染任何"AI 正在输入"。

---

## 4. `user.handoff` 的语义（需 spec 确认）

> spec 的消息类型表未包含此消息。plan 待确认项 Q1。

处理步骤（服务端，无 LLM 调用）：

1. 读 `chat:state:{sid}`；若已是 `QUEUED` 或 `HUMAN_SERVING`，**只重放** `session.state`，不入队（US3-2）
2. 若 `RESOLVED`，回 `error{code:"session_resolved", retryable:false}`
3. 否则构造 `TriggerResult.fire(TriggerType.USER_REQUEST, "用户点击快捷转人工")`，调 `coordinator.initiate(...)`
4. 迁移成功 → 写 `ChatState{state:QUEUED}` → 推 `session.state` 给 `session:{sid}` 与 `agents` → 推 `queue.update{added:[sid]}`
5. 同时推送一条 `assistant.delta`+`assistant.done`（内容为 `handoff_scripts.user_request`），让对话里留下"已为您接入"的记录

**不接受客户端指定触发类型或优先级**——这些是服务端规则（宪法 III）。

---

## 5. 心跳与空闲

- 服务端每 `heartbeat_interval_s`（默认 20s）发 `ping`
- 服务端超过 `idle_timeout_s`（默认 60s）未收到任意入站帧 → `close 4408`
- 浏览器原生 WebSocket 不暴露 ping/pong 帧，因此心跳用应用层 JSON 帧而非 WS 协议 ping——中间设备看到的是正常数据帧

---

## 6. 错误码

| code | HTTP/close | retryable | 语义 |
|---|---|---|---|
| `invalid_session` | 4401 | false | 会话不存在或参数缺失 |
| `session_not_owned` | 4403 | false | 会话不属于该用户 |
| `order_not_found` | 4404 | false | 握手带入的订单号不存在 |
| `session_resolved` | — | false | 会话已结束，不再接受输入 |
| `not_owner` | — | false | 人工非该会话的 owner |
| `not_human_serving` | — | false | 状态不允许人工发消息 |
| `rate_limited` | — | true | 入站消息过密（默认 10 条/5s） |
| `server_busy` | — | true | 内部异常 |

`retryable:true` 时前端在 1s 后重发一次，仍失败则提示用户。

---

## 7. 重连与补齐

```text
客户端断线
  → 指数退避重连（hello.payload.reconnect）
  → 连接成功，收到 hello
  → GET /chat/session/{sid}?after_seq=<本地最大 seq>
  → 合并 messages，按 (session_id, seq) 去重
  → truncated=true 时显示"部分早期消息已省略"
```

人工端重连额外调用 `GET /handoff/queue` 重拉队列。

---

## 8. 顺序保证

- 单连接内：服务端按 `Hub.publish` 调用顺序写入该连接的发送队列，FIFO
- 跨连接：不保证；前端以 `seq` 为准（宪法 V：不伪造时序，FR-028）
- 同一会话的 `assistant.delta*` 与 `assistant.done` 必为连续帧（中间不插入其他帧），确保前端可安全拼接

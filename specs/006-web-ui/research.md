# Research：006 客服前端的实时层与流式方案

本文件记录 plan 中若干取舍的依据。结论均已在 `plan.md` 落定，这里说明**为什么**。

---

## R1. 实时传输：WebSocket vs SSE

**Decision**: 用户端与人工端共用一条 WebSocket 协议。

**Rationale**:
- 用户端需要接收**服务端主动推送的人工消息**（`human.message`，由人工在工作台发出）。SSE 是单向的，服务端推给用户的通道不存在，除非再为每个用户维护一条独立推送通道——两个通道就意味着两套重连、两套补齐、两套序号。
- 状态同步（`session.state`）同样是服务端主动推送，且排队时长不可预知，轮询违背 FR-017。
- `spec.md` 决策表 #1 已定为 WebSocket，本 plan 与之一致。

**Alternatives considered**:
- SSE（AI 流式）+ WS（状态与人工消息）：两套通道，重连补齐逻辑需要写两遍，且首字延迟与状态延迟的因果顺序在不同通道上不保证。
- 长轮询：违背 FR-017"不轮询"，且排队期间连接空转。

---

## R2. WebSocket 实现：FastAPI 原生 + 自研 Hub

**Decision**: 用 FastAPI/Starlette 的 `WebSocket`，自己写约 150 行的 `Hub`（连接表 + 每连接发送队列 + 扇出 + 心跳）。

**Rationale**:
- 宪法"能用标准库或已有依赖实现的不引入新依赖"。
- 本场景的扇出规则很简单——按 `session_id` 与"全体人工端"两种目标，没有 room/namespace 的需求。
- 第三方库（socket.io、broadcaster）解决的是房间权限、协议协商、多进程广播，这些在本 feature 的规模下都不需要；反而多一层抽象会让"消息 seq 由谁分配"更难看清。

**Alternatives considered**:
- `socket.io`：协议层分包、自带重连，但客户端要引入 `socket.io-client`，且服务端消息类型要和它的 envelope 嵌套，与 spec 的消息类型表对不上。
- Redis Pub/Sub 做多进程广播：单实例部署用不上；若将来多实例，`Hub.publish` 已收口为一个函数，届时替换即可。

---

## R3. AI 流式的两种来源

**Decision**: 优先走 LLM 真流式；LLM 不支持 stream 时按**语义块**推送完整回复，并在 delta 中标记 `streaming:false`。

**Rationale**:
- 宪法 V：不伪造。固定延时逐字打字是"界面上看起来在流式"，而实际响应早已完成，用户感知的等待与系统真实状态不一致——评审时会被一眼看穿。
- 语义块切分（按句末标点）保留了"逐步呈现"的可读性，同时每个 delta 都对应真实产生的内容。
- 现有 `IntentLLM` 是 Protocol，增加一个**可选**方法 `stream_reply` 不破坏 `StubLLMClient` 与 `OpenAICompatLLM`，用 `getattr` 能力探测即可。

**Alternatives considered**:
- 只做真流式：`StubLLMClient` 与内网模型不带 stream 时页面上完全看不到流式效果，SC-002 也验证不了。
- 固定延时：违背宪法 V，且会让 E2E 测试里"首字延迟"指标失真。

**遗留事实**: `openai_compat.py` 目前调 `/chat/completions` 是非流式；且当前 Agent 的**最初一步是意图分类**，不是生成回复——回复文本由 `present.render` / 固定话术 / 政策答案拼出来。因此"真流式"真正能覆盖的只有政策问答复述这段生成；意图分类之后的固定话术是无法流式的。这一点 plan 的 D5 已按"分块降级为主、真流式为可选优化"处理，实现时不得声称所有回复都是 LLM 逐 token 产出。

---

## R4. 会话对外状态为什么单独存

**Decision**: 新键 `chat:state:{sid}`，结构为 `{session_id, state, agent_id, agent_name, updated_at, seq}`。

**Rationale**:
- 现成的 `SessionState`（`app/models/agent.py`）是 Agent 的内部状态：order_id、pending、clarify_round、policy 统计。它不含"当前谁在服务"，且每次 `AgentLoop.handle` 末尾才写一次，人工侧的状态变化（accept/resolve）完全不经过它。
- `HandoffState` 现只存在于 `HandoffCoordinator` 的返回值与状态机函数里，`NORMAL` 从不落盘。要让界面在 AI 服务期间也能显示"AI 助手"，必须有一个对外的状态记录。
- 单独立键让"状态变更只有一个写入口"（coordinator 迁移成功之后），避免两处写入互相覆盖。

**Alternatives considered**:
- 扩展 `SessionState` 加 `handoff_state` 字段：会把传输层的关注点塞进 Agent 内部模型，且 accept/resolve 路径没有 `SessionState` 实例可写。
- 复用 `handoff:pack:{sid}`：pack 在 accept 时**不清除**（`clear` 才删），但里面没有 `owner_agent_id`，也无法表示 `NORMAL`。

---

## R5. 消息存储与序号

**Decision**: `chat:msg:{sid}` 用 Redis List（`RPUSH`），每条消息 `{id, session_id, seq, role, text, streaming, ts}`；`seq` 由 `INCR chat:seq:{sid}` 分配。

**Rationale**:
- FR-027 要求消息带序号用于去重；FR-025 重连要补齐断线期间消息，需要一个可 `after_seq` 查询的权威列表。
- Redis 已在技术栈内，无需新存储。
- `seq` 全局单调而非"每角色单调"，因为用户端与人工端看到的是**同一会话的同一条时间线**，两端序号必须可比对。

**Alternatives considered**:
- 时间戳去重：同一毫秒内多条消息会冲突，且客户端时钟不可信。
- 仅前端本地保存：刷新即丢，FR-007 无法满足。

---

## R6. 并发接入的抢锁

**Decision**: `POST /handoff/accept` 先 `SET chat:claim:{sid} <agent_id> NX EX <ttl>`，失败返回 409。

**Rationale**:
- 现有 `HandoffQueue.accept` 是 `get_pack` + `zrem` + `delete` 三步，**没有原子性**：两个人工同时接入时，两人都能从 `get_pack` 拿到非空 pack，`zrem` 的返回值没被检查，于是两个人都"接入成功"。这是 spec Edge Cases 明确要求处理的场景（"两个人工作台同时接入同一会话"），也是 FR-015 的要求。
- 用 `SET NX` 是最小改动：不改动队列本身，只在协调器外面加一道墙。

**Alternatives considered**:
- 用 `zrem` 返回值判断：可行，但 `zrem` 成功之后此人已经出队，若后续步骤失败需要回滚；`SET NX` 抢到锁之后再出队，失败可安全释放。
- Redis Lua 脚本原子化整套操作：更严谨但本场景过度。

---

## R7. 断线补齐的边界

**Decision**: 重连后 `GET /chat/session/{sid}?after_seq=N`；补齐条数超过 `chat_backfill_max_messages`（默认 200）时只返回最近 200 条，并带 `truncated:true`。

**Rationale**:
- 宪法 V：数据缺失要明说。截断时前端显示"部分早期消息已省略"，而不是悄悄少几条。
- 200 条对演示规模足以覆盖，也避免一次补齐拖慢重连。

**Alternatives considered**:
- 全量补齐：长会话会拖慢重连，且 Redis List 读取无上限。

---

## R8. 前端构建产物与后端解耦

**Decision**: `create_app()` 在 `frontend_static_dir` 存在时挂 `StaticFiles`，不存在时 `/` 返回 404；构建产物由 Dockerfile 在镜像构建阶段复制，不做 CI。

**Rationale**:
- FR-022 要求后端不依赖前端构建即可启动与测试。现有的后端测试全部走 `create_app()`，若静态目录缺失导致启动失败，所有既有测试会一起挂。
- 同一镜像承担静态托管可以避免演示时起两个进程。

**Alternatives considered**:
- 独立 Nginx 托管前端：多一个进程与配置，演示复杂度上升，且跨域、WS 代理需要另配。

---

## 遗留：`main.py` 目前起不来

**Fact**: `AgentLoop` 与 `HandoffCoordinator` 只在 `tests/conftest_agent.py` 里被 `configure_*()` 装配；`app/main.py` 没有 lifespan 装配，`agent_routes._loop` 在生产下为 `None`，`GET /agent/chat` 直接抛 `RuntimeError`。`docker-compose.yml` 也只起了 mock-service 与 pgvector，没有 Agent 服务定义。

**Implication**: 本 feature 的 D9 生产装配不是"顺手加一下"，而是**演示能跑起来的前置条件**，必须作为 006 的一个独立任务并带启动冒烟测试。

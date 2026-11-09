# Tasks: Agent 主循环与状态机

**Input**: `/specs/002-agent-loop/` — spec.md、plan.md

**Prerequisites**: spec 与 plan 均已确认

**Tests**: 必须（宪法原则 II）

## Path Conventions

复用 `backend/`，Agent 作为 `app/agent/` 子包。

---

## Phase 1: Setup

- [ ] T001 在 `backend/pyproject.toml` 增加依赖 `redis`、`fakeredis`(dev)、`httpx`(已是 dev，提为运行时依赖)
- [ ] T002 [P] 在 `backend/.env.example` 增加 Agent 配置项：LLM 超时、Redis URL、会话 TTL、确认有效期

---

## Phase 2: Foundational（阻塞全部用户故事）

- [ ] T003 在 `app/models/agent.py` 定义 `Intent`（StrEnum：refund / logistics / change_address / query_order / complaint / manual / general）、`IntentResult`（intent、confidence、source、params）、`SessionState`、`PendingConfirmation`、`TurnRecord`
- [ ] T004 [P] 在 `app/config.py` 增加 `AgentSettings`：llm_timeout_ms、redis_url、session_ttl_seconds、confirm_ttl_seconds、关键词表路径
- [ ] T005 [P] 在 `app/agent/llm/base.py` 定义 `LLMClient` 协议：`async classify(user_input, hint_keywords, context) -> IntentResult`
- [ ] T006 [P] 在 `app/agent/llm/stub.py` 实现 `StubLLMClient`：`responses` 映射、`fail_mode`(None/timeout/error/invalid)、`fail_times`、`delay_ms`、`call_count`
- [ ] T007 [P] 在 `app/agent/llm/openai_compat.py` 实现真实客户端：httpx 调用 OpenAI 兼容接口，超时走 `llm_timeout_ms`
- [ ] T008 在 `app/agent/session.py` 实现 Redis 会话存储：读写、TTL 刷新、待确认操作子键、`degraded` 标记不持久化
- [ ] T009 [P] 在 `app/agent/tools/client.py` 实现 mock 服务客户端：透传 `X-Trace-Id`、`X-Request-Id`，返回 `BizResult`
- [ ] T010 [P] 单元测试 `tests/unit/test_session.py`（fakeredis）、`tests/unit/test_stub_llm.py`

**Checkpoint**: 基础设施就绪

---

## Phase 3: US1 - 语义优先的意图识别 (P1)

- [ ] T011 [P] [US1] 契约测试 `tests/contract/test_agent_intent.py`：7 个对照样本（诉求 / 威胁 / 咨询 / 历史陈述 / 反向表达 / 无关键词诉求 / 无关请求）
- [ ] T012 [US1] 在 `app/agent/intent.py` 实现 `classify()`：关键词仅作 hint 传入，结果完全由 LLM 决定
- [ ] T013 [US1] 断言测试：同一输入 + 不同 hint，Stub 返回相同意图时结果一致（证明 hint 不参与决策）
- [ ] T014 [US1] 在 `app/agent/loop.py` 实现主循环骨架：接收输入 → 调 classify → 返回 IntentResult

**Checkpoint**: 意图识别可独立验证

---

## Phase 4: US2 - 单次失败即降级 (P1)

- [ ] T015 [P] [US2] 单元测试 `tests/unit/test_degrade.py`：宽写词表命中/未命中
- [ ] T016 [P] [US2] 契约测试：LLM 失败时写操作被拒、只读放行
- [ ] T017 [US2] 在 `app/agent/degrade.py` 实现 `classify_degraded()`：宽写词表（可配置）
- [ ] T018 [US2] 在 `app/agent/loop.py` 接入降级：捕获超时/报错/非法 → 走降级路径
- [ ] T019 [US2] 测试 SC-004a：`fail_times=2`，第三次请求仍调用 LLM（`call_count == 3`）
- [ ] T020 [US2] 测试 SC-004：降级期零写操作

**Checkpoint**: 降级行为可验证

---

## Phase 5: US3 - 写操作确认 (P1)

- [ ] T021 [P] [US3] 单元测试 `tests/unit/test_confirm.py`：精确匹配、非确认词、过期
- [ ] T022 [P] [US3] 契约测试 `tests/contract/test_agent_confirm.py`：完整确认流程
- [ ] T023 [US3] 在 `app/agent/confirm.py` 实现：创建 pending、核销、取消、精确匹配确认词（可配置）
- [ ] T024 [US3] 在 `app/agent/loop.py` 接入确认环节：写意图 → 生成 pending → 复述 → 等待
- [ ] T025 [US3] 实现**执行前始终重新校验**（FR-020）：确认后重新查订单状态，变化则拒绝

**Checkpoint**: 确认流程可验证

---

## Phase 6: US4 - 订单切换 (P2)

- [ ] T026 [P] [US4] 契约测试：切换订单、不存在订单保持原上下文、跨用户拒绝
- [ ] T027 [US4] 在 `loop.py` 实现订单号提取与上下文切换

**Checkpoint**: 订单切换可验证

---

## Phase 7: US5 - 结果呈现 (P2)

- [ ] T028 [P] [US5] 单元测试 `tests/unit/test_present.py`：每个 ResultCode 都有映射
- [ ] T029 [US5] 在 `app/agent/present.py` 实现 ResultCode → 自然语言映射，4xxx 不暴露技术细节
- [ ] T030 [US5] 接入 loop：工具结果 → present → 回复

**Checkpoint**: 结果呈现可验证

---

## Phase 8: 端到端与可观测

- [ ] T031 [P] 在 `app/api/agent_routes.py` 实现对话接口：`POST /agent/chat`，接收 session_id / user_id / order_id / snapshot / message
- [ ] T032 集成测试 `tests/integration/test_e2e_flow.py`：完整退款流程（表达意图 → 确认 → 执行）
- [ ] T033 集成测试 `tests/integration/test_trace_through_services.py`：Agent → mock 服务 trace 贯穿
- [ ] T034 集成测试 `tests/integration/test_session_redis.py`：重启后会话恢复
- [ ] T035 快照与库不一致留痕（FR-002/003）及其测试

---

## Phase 9: Polish

- [ ] T036 全量测试，逐条核对 FR-001~032、SC-001~008
- [ ] T037 更新 `backend/README.md`：Agent 接口、降级行为、确认流程
- [ ] T038 更新根 README 进度表
- [ ] T039 宪法核对：无裸字符串比较、配置全部外置、无跨请求降级状态

---

## Dependencies

```
T001-T002 (Setup)
    ↓
T003-T010 (Foundational) ← 阻塞全部
    ↓
T011-T014 (US1 意图识别) ← 其他故事都依赖它
    ↓
    ├─→ T015-T020 (US2 降级)
    ├─→ T021-T025 (US3 确认)
    ├─→ T026-T027 (US4 切换)
    └─→ T028-T030 (US5 呈现)
    ↓
T031-T035 (端到端)
    ↓
T036-T039 (Polish)
```

**任务数**：39 条，其中 [P] 标记 13 条

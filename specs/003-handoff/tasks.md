# Tasks: 转人工链路

**Input**: `/specs/003-handoff/` — spec.md、plan.md

**Tests**: 必须（宪法原则 II）

---

## Phase 1: Foundational

- [ ] T001 在 `app/models/handoff.py` 定义 `TriggerType`、`Priority`、`Emotion`、`HandoffContext`、`AttemptedAction`、`HandoffState`
- [ ] T002 [P] 在 `app/config.py` 增加：风险金额阈值、投诉词表、情绪词表、触发词表、话术文案、预期等待倍数
- [ ] T003 [P] 在 `app/handoff/scripts.py` 定义七条话术（配置驱动，按场景取键）

---

## Phase 2: US1 - 四类触发源 (P1)

- [ ] T004 [P] [US1] 单元测试 `tests/unit/test_triggers.py`：四类各自命中、互斥顺序、配置可改
- [ ] T005 [US1] 在 `app/handoff/triggers.py` 实现四类判定 + 固定顺序 `check_user_request → check_risk → check_system_error → check_boundary`
- [ ] T006 [US1] 断言"我要投诉，转人工"按 **user_request** 处理（顺序优先于风险）

---

## Phase 3: US3 依赖 - 情绪与优先级 (P1)

- [ ] T007 [P] 单元测试 `tests/unit/test_emotion.py`：负面词、连续感叹号、默认中性、**零 LLM 调用**（断言分类器未被调用）
- [ ] T008 [P] 单元测试 `tests/unit/test_priority.py`：判定顺序、同优先级 FIFO、必带依据
- [ ] T009 在 `app/handoff/emotion.py` 实现规则情绪判定
- [ ] T010 在 `app/handoff/priority.py` 实现 `calc_priority() -> (Priority, reason)`

---

## Phase 4: US2 - 上下文打包 (P1)

- [ ] T011 [P] [US2] 单元测试 `tests/unit/test_context_pack.py`：字段完整、摘要超长截断、风险触发无 AI 建议、空动作列表
- [ ] T012 [US2] 在 `app/handoff/context_pack.py` 实现规则摘要（零 IO）+ 打包
- [ ] T013 [US2] 断言摘要生成在无网络无模型环境下必定成功

---

## Phase 5: US4 - 状态机与 AI 静默 (P1)

- [ ] T014 [P] [US4] 单元测试 `tests/unit/test_state_machine.py`：合法/非法迁移、静默抛异常、不存在回流边
- [ ] T015 [US4] 在 `app/handoff/state.py` 实现状态机 + `assert_ai_may_speak()` 抛 `SilenceViolation`
- [ ] T016 [US4] 在 `app/agent/loop.py` 输出前调用静默断言
- [ ] T017 [US4] 断言 `HUMAN_SERVING → COLLECTING` 迁移抛异常

---

## Phase 6: US3 - 队列 (P1)

- [ ] T018 [P] [US3] 集成测试 `tests/integration/test_queue_priority.py`：高优先级先出、同优先级 FIFO、重复触发只占一位
- [ ] T019 [US3] 在 `app/handoff/queue.py` 实现 Redis ZSET 队列 + 幂等
- [ ] T020 [US3] 队列深度与优先级分布查询

---

## Phase 7: US5 - 排队提示 (P2)

- [ ] T021 [P] [US5] 测试：触发即告知状态、排队中再发言继续告知、问时长不承诺
- [ ] T022 [US5] 测试 **SC-009**：扫描全部话术，断言不含时间数字
- [ ] T023 [US5] 接入 loop：触发/排队/人工服务三态的回复

---

## Phase 8: 人工侧接口与端到端

- [ ] T024 在 `app/api/handoff_routes.py` 实现：`GET /handoff/queue`、`POST /handoff/accept`、`POST /handoff/decline`、`POST /handoff/resolve`
- [ ] T025 接入 loop：四类触发 → 打包 → 入队 → 回复话术
- [ ] T026 集成测试 `tests/integration/test_handoff_flow.py`：完整转接、人工接入后 AI 静默、人工拒绝不回流
- [ ] T027 trace 贯穿：转接全过程可用同一 trace_id 检出

---

## Phase 9: Polish

- [ ] T028 全量测试，核对 FR-001~030、SC-001~009
- [ ] T029 更新 backend README：转人工接口、话术表、队列查询
- [ ] T030 宪法核对：无裸字符串、配置外置、零 LLM 情绪判定

---

## Dependencies

```
T001-T003 (Foundational)
    ↓
T004-T006 (US1 触发源)  T007-T010 (情绪+优先级)  ← 并行
    ↓
T011-T013 (Context Pack)
    ↓
T014-T017 (状态机+静默)
    ↓
T018-T020 (队列)  T021-T023 (提示)
    ↓
T024-T027 (接口+端到端)
    ↓
T028-T030 (Polish)
```

**任务数**：30 条，[P] 标记 9 条

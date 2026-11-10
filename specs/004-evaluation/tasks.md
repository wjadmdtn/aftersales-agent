# Tasks: 评测集与自动回归

**Input**: `/specs/004-evaluation/` — spec.md、plan.md

---

## Phase 1: Setup

- [ ] T001 创建 `eval/{cases,seeds,baseline,report}` 目录
- [ ] T002 [P] `eval/seeds/common.py`：用例构造 helper，统一 id 生成与字段

---

## Phase 2: Foundational

- [ ] T003 在 `backend/app/eval/schema.py` 定义 `EvalCase`（id/category/path/input/expect）、`GradeResult`、`EvalReport`
- [ ] T004 在 `backend/app/eval/loader.py` 实现 JSONL 加载 + 格式校验（FR-003/FR-004）
- [ ] T005 [P] 单元测试 `tests/eval/test_case_schema.py`：格式非法即失败、id 重复即失败

---

## Phase 3: 判分

- [ ] T006 在 `backend/app/eval/grader.py` 实现四套判分：business_action / clarify / handoff / irrlevant / e2e
- [ ] T007 澄清判分内置两条硬约束：话术无业务承诺、道歉与否符合情绪
- [ ] T008 [P] 单元测试 `tests/eval/test_grader.py`：每类通过/失败各一例、失败项定位准确
- [ ] T009 测试失败信息包含"期望/实际"，不能只给布尔

---

## Phase 4: 指标与报告

- [ ] T010 在 `backend/app/eval/metrics.py` 实现聚合：总体/分类准确率、**过度转接率**、误转率、漏转率
- [ ] T011 在 `backend/app/eval/report.py` 生成 Markdown + JSON
- [ ] T012 报告标注后端（stub/real），stub 加不可引用警告（FR-021b/c）

---

## Phase 5: 回归

- [ ] T013 在 `backend/app/eval/baseline.py` 实现基线保存/加载/对比
- [ ] T014 实现分类阈值比较（FR-019a）：总体 3pt、单项 5pt、硬性项 0
- [ ] T015 [P] 单元测试 `tests/eval/test_baseline.py`：1pt 噪声不阻断、硬性项一次即阻断、阻断信息含指标名与数值

---

## Phase 6: 用例（220 条）

- [ ] T016 [P] `eval/seeds/intent_seed.py`：业务动作 90 条（标准 40 / 对抗 30 / 无关键词 20）
- [ ] T017 [P] `eval/seeds/clarify_seed.py`：澄清 60 条（有情绪 25 / 无情绪 15 / 无关 20）
- [ ] T018 [P] `eval/seeds/handoff_seed.py`：转人工 30 条（四类触发源）
- [ ] T019 [P] `eval/seeds/e2e_seed.py`：端到端 25 条
- [ ] T020 生成 `eval/cases/*.jsonl`，校验合计 220 条且分类分布符合 spec
- [ ] T021 按标注规则自检：同义表达标注一致

---

## Phase 7: Runner

- [ ] T022 `eval/runner.py`：加载用例 → 重置状态 → 逐条执行 → 判分 → 报告
- [ ] T023 每条用例前重置 mock store / Redis / 队列（FR-022）
- [ ] T024 `--llm=stub|real` 切换后端，其余完全一致
- [ ] T025 real 模式记录耗时与调用次数（FR-024）

---

## Phase 8: Polish

- [ ] T026 全量测试，核对 FR-001~024、SC-001~008
- [ ] T027 更新 README：如何跑评测、如何看报告、如何更新基线
- [ ] T028 宪法核对

---

**任务数**：28 条，[P] 标记 8 条

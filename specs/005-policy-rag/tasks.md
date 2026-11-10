# Tasks: 政策知识检索（Policy RAG）

**Input**: `/specs/005-policy-rag/` — spec.md、plan.md

---

## Phase 1: 基座

- [ ] T001 创建 `backend/app/policy/` 包与 `__init__.py`
- [ ] T002 在 `app/policy/models.py` 定义 `PolicyClause`、`RetrievedClause`、`RetrievalResult`、`PolicyAnswer`、`RetrievalDecision`（hit/ambiguous/miss）
- [ ] T003 [P] 在 `app/policy/seed.py` 实现 `policies.json` 加载 + 校验：字段完整性、`(clause_id, version)` 唯一、同 clause_id 版本区间不重叠（FR-008）
- [ ] T004 [P] `app/models/domain.py` 的 `Order` 增加 `product_type: str | None = None`；`_SeedOrder` 同步支持（缺省 None，不影响既有 303 测试）
- [ ] T005 [P] `app/policy/embedder.py` 定义 `Embedder` 协议 + `StubEmbedder`（sha256 → 确定性伪向量）
- [ ] T006 单元测试 `tests/unit/test_policy_seed.py`：28 条种子可加载；篡改出区间重叠必须报错；重复 `(id, version)` 必须报错

## Phase 2: 检索

- [ ] T007 `app/policy/store.py` 定义 `ClauseStore` 协议：`search(embedding, categories, product_type, order_date, limit)`；提供 `InMemoryClauseStore`（CI 用）
- [ ] T008 `app/policy/store.py` 实现 `PgClauseStore`：建表 DDL、批量写入、带版本/品类硬过滤的 pgvector 查询（FR-009/010/011）
- [ ] T009 `app/policy/retriever.py`：召回 → 置信判定（FR-013）→ 歧义判定（FR-014），输出 `RetrievalResult`；纯函数便于单测
- [ ] T010 [P] 单元测试 `tests/unit/test_policy_retriever.py`：
  - 版本过滤：ORD1002（2026-11-06）问超期 → 只召回 v3；ORD1009（2026-12-05）→ 只召回 v4
  - 品类过滤：生鲜订单不召回纯服饰条款；`*` 条款对所有品类可见
  - top1 低于阈值 → `miss`
  - top1-top2 分差 < margin → `ambiguous`
  - 订单 created_at 缺失 → 用当前时间且记日志
- [ ] T011 [P] 单元测试 `tests/unit/test_policy_store.py`：`InMemoryClauseStore` 的过滤语义与 `PgClauseStore` 的 SQL 条件一致（同一批用例双跑）

## Phase 3: 生成

- [ ] T012 `app/agent/llm/base.py`：拆 `IntentLLM` / `PolicyLLM` 两个 Protocol；`LLMClient` 保留为 `IntentLLM` 别名保证既有导入不破
- [ ] T013 `app/policy/answerer.py`：构造 prompt（条款正文 + 例外 + 决策约束）→ 解析结构化输出
- [ ] T014 `app/policy/answerer.py` 实现硬门禁：`cited_clause_ids ⊆ 召回集合`（FR-017）；例外非空时 `handled_exceptions` 不得为空（FR-018）；任一不满足→丢弃回答并返回 miss
- [ ] T015 `app/agent/llm/openai_compat.py` 实现 `PolicyLLM.rerank_and_answer`；`decision` 只允许模型**降级**（hit→ambiguous/miss），确定性判 miss 时模型无权改 hit
- [ ] T016 [P] `app/agent/llm/stub.py` 补 policy 桩：按输入关键词返回预设 `decision` / `answer` / `cited_clause_ids`，保证 CI 确定性
- [ ] T017 [P] 单元测试 `tests/unit/test_policy_answerer.py`：
  - 引用不存在的 clause_id → 拒答
  - 召回含例外但 `handled_exceptions` 为空 → 判不合格
  - 模型判 hit 但确定性判 miss → 结果 miss
  - 模型判 miss → 保持 miss

## Phase 4: 接入

- [ ] T018 `app/services/policy.py`：`answer(question, order_created_at, product_type, trace_id)` 编排 retriever + answerer，带 `rag_timeout_ms` 超时（FR-023），异常一律返回 miss（FR-022）
- [ ] T019 `app/agent/loop.py`：`_dispatch` 的 `GENERAL` 分支前置 `_try_policy`；未命中回落既有 `_clarify`（FR-001~003）
- [ ] T020 `app/agent/loop.py` 的 `_handle_degraded`：显式禁止走 RAG（FR-021），并加注释说明原因
- [ ] T021 `app/agent/loop.py` 门控：读/写/投诉/人工意图不触发检索（FR-002）；`SessionState` 增加 `last_cited_clause_ids`、`policy_turns`
- [ ] T022 `app/handoff/coordinator.py`：把 `rag_top_similarity` 填入 `TurnSignals`，复用既有 `check_boundary`（FR-013）
- [ ] T023 [P] 集成测试 `tests/integration/test_policy_flow.py`：
  - "退款多久到账" → 引用 `REFUND-COMMON-01`
  - "定制的能七天无理由吗" → 明确回答不能
  - "你们老板是谁" → 转人工，无政策内容
  - "我要退款" / "我要投诉" / "查 ORD1003 物流" → 检索次数为 0
  - LLM 超时 → 降级，不生成政策内容

## Phase 5: 评测与标定

- [ ] T024 在 `app/eval/grader.py` 增第六套判分 `policy_rag`：检索层（recall@1/@5/MRR）、生成层（引用正确率/幻觉率/例外覆盖）、端到端（误转/过度转接）
- [ ] T025 [P] 生成用例 `eval/seeds/policy_seed.py`：命中 40 / 例外 15 / 版本 10 / 无答案 15 / 歧义 10 / 意图干扰 10，共 100 条
- [ ] T026 [P] 单元测试 `tests/eval/test_policy_grader.py`：每类通过/失败各一例；失败信息含期望与实际
- [ ] T027 `app/policy/calibrate.py`：正负样本相似度分布 → 推荐阈值（FPR<5% 且 recall@1 最大）；结果写入 `specs/005-policy-rag/calibration.md`
- [ ] T028 用真实 bge-m3 跑标定，把标定后的阈值写入 `.env.example` 默认值

## Phase 6: 收口

- [ ] T029 `docker-compose.yml` 增 `pgvector/pgvector:pg16` 服务；启动时执行建表 DDL 与种子导入
- [ ] T030 全量回归：既有 303 个测试 + 新增测试全通过（SC-007）
- [ ] T031 核对 SC-001~009 与 FR-001~025
- [ ] T032 更新 README：如何起 PG、如何标定阈值、如何跑 `--embedder=stub|real`
- [ ] T033 宪法核对

---

**任务数**：33 条，[P] 标记 9 条

## 依赖关系

```text
T001→T002→{T003,T004,T005}→T006
T002→T007→T008→T009→T010
T002→T012→T013→T014→T015→T016→T017
T009,T014→T018→T019→T020→T021→T022→T023
T023→T024→{T025,T026}→T027→T028
T008,T028→T029→T030→{T031,T032,T033}
```

T011 与 T010 并行；Phase 3 可与 Phase 2 并行（接口先冻结 T002）。

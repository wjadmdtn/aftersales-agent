# Implementation Plan: 政策知识检索（Policy RAG）

**Branch**: `005-policy-rag` | **Date**: 2026-11-10 | **Spec**: [spec.md](./spec.md)

## Summary

在 `GENERAL` 分支前置一次政策检索：按订单时间做版本硬过滤 → pgvector 召回 → LLM 精排 → 置信/歧义判定 → 带引用生成。检索不到就不生成，写 `rag_top_similarity` 触发既有 `check_boundary` 转人工。

五个技术要点：

1. **检索不是意图，是兜底分支的前置步骤**——不新增 `Intent.POLICY`，避免与 refund 咨询产生意图歧义
2. **版本过滤在 SQL 里做，不做召回后过滤**——否则"未来政策不能答旧订单"这条约束靠不住
3. **例外不参与 embedding，参与生成**——例外语义偏离正文会拉低召回，但回答不处理例外就是错
4. **生成结果结构化**（`answer` / `cited_clause_ids` / `handled_exceptions`）——引用校验、幻觉检测、例外覆盖都靠这三个字段判分
5. **StubEmbedder + stub LLM 双桩**——沿用 002 的测试架构，保证 CI 不依赖真模型

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: 新增 `pgvector`（Python client）；embedding 用 `sentence-transformers` + `bge-m3`，仅本地运行与标定脚本需要，CI 用 stub

**Storage**: PostgreSQL 16 + pgvector 扩展，新增 `policy_clause` 表；种子 `backend/seed/policies/policies.json`（28 条，12 category）

**Testing**: pytest；新增 `StubEmbedder`（确定性哈希向量）与 stub LLM 的 policy 分支

**Target Platform**: Linux / Windows / CI（CI 用 stub，不需要 PG 实例）

**Constraints**:
- 检索 P95 < 200ms（不含生成）
- 检索异常/超时必须降级为"未命中"，不得阻塞主链路
- 降级期禁止生成政策回答

**Scale/Scope**: 28 条款；召回 top-5，精排 top-2；单次检索 1 次 SQL + 1 次 LLM 调用（精排与生成合并为一次调用，见 T015）

## Constitution Check

| 原则 | 满足方式 | 结论 |
|---|---|---|
| **I. 规范先行** | 本文派生自已定稿 spec（005） | ✅ |
| **II. 测试即验收标准** | 每个 FR 对应至少一个测试；评测 `policy_rag` 分类接入 004 | ✅ |
| **III. 规则可解释可复现** | 版本过滤、阈值、margin 均为确定性逻辑，可单测 | ✅ |
| **IV. 配置外置** | 阈值、margin、K/N、超时全部走 `Settings` | ✅ |
| **V. 边界诚实** | 检索不到不生成；无引用不发；例外必答 | ✅ |

**无宪法冲突**。唯一需要说明的：宪法要求"不得引入无法解释的行为"，LLM 精排属概率行为——但精排只做**排序**，不产生新结论；所有结论必须引用召回条款，引用校验是确定性检查。

## Project Structure

```text
specs/005-policy-rag/
├── plan.md
├── spec.md
└── tasks.md

backend/
├── app/
│   ├── policy/                     # 新增子系统
│   │   ├── __init__.py
│   │   ├── models.py               # PolicyClause / RetrievedClause / RetrievalResult / PolicyAnswer
│   │   ├── seed.py                 # policies.json 加载与校验（区间不重叠等）
│   │   ├── embedder.py             # Embedder 协议 + StubEmbedder + BgeEmbedder
│   │   ├── store.py                # PG + pgvector 读写；search 带版本/品类硬过滤
│   │   ├── retriever.py            # 召回 → 精排 → 置信/歧义判定（纯编排，可单测）
│   │   └── answerer.py             # 结构化生成 + 引用校验 + 例外覆盖校验
│   ├── services/
│   │   └── policy.py               # 对 Agent 暴露：answer(question, order_created_at, product_type)
│   ├── agent/
│   │   ├── llm/base.py             # 拆分为 IntentLLM / PolicyLLM 两个 Protocol
│   │   ├── llm/stub.py             # 补 policy 分支桩
│   │   ├── llm/openai_compat.py    # 实现 PolicyLLM.rerank_and_answer
│   │   └── loop.py                 # _dispatch 的 GENERAL 分支前置检索
│   ├── models/agent.py             # SessionState 增 last_cited_clause_ids / policy_turns
│   ├── models/domain.py            # Order 增 product_type
│   ├── config.py                   # 新增 rag_* 配置项
│   └── handoff/coordinator.py      # 填 TurnSignals.rag_top_similarity
├── seed/
│   ├── orders.json                 # ✅ 已补 created_at + ORD1009（v4 正样本）
│   └── policies/policies.json      # ✅ 已生成 28 条
└── tests/
    ├── unit/
    │   ├── test_policy_seed.py         # 种子校验：ID 唯一、区间不重叠
    │   ├── test_policy_retriever.py    # 版本过滤、品类过滤、阈值、歧义 margin
    │   └── test_policy_answerer.py     # 引用校验、幻觉拒绝、例外覆盖
    ├── integration/
    │   └── test_policy_flow.py         # 检索→回答→转人工 全链路
    └── eval/                           # 接入 004 的 policy_rag grader
        └── test_policy_grader.py
```

新增目录只有 `app/policy/`。其余全是**在既有文件里加函数/加字段**，不改既有函数签名与语义。

## 关键技术决策

### 1. 版本过滤在 SQL 里

```sql
SELECT clause_id, policy_id, version, title, body, exceptions,
       1 - (embedding <=> $1) AS similarity
FROM policy_clause
WHERE category = ANY($2)
  AND applies_to_types && $3          -- 品类重叠
  AND effective_from <= $4            -- $4 = 订单 created_at 日期
  AND (effective_to IS NULL OR effective_to >= $4)
ORDER BY embedding <=> $1
LIMIT $5
```

`$1` 是 embedding 的 pgvector 字面量。**版本条件必须在 WHERE，不能在应用层过滤**——否则召回 top-5 后过滤可能只剩 0~1 条，人为制造"检索不到"。

### 2. `applies_to` 用数组列

`applies_to.product_types` 存为 `text[]`，`*` 表示全品类，查询时展开为 `product_types && ARRAY[<订单品类>, '*']`。商品品类缺失时只匹配 `*` 条款——**宁可少答，不用品类不明的条款强行回答**。

### 3. 精排与生成合并为一次 LLM 调用

不单独做 rerank 再单独做 answer（两次调用、两次延迟）。改为一次调用，要求模型：

```json
{
  "decision": "hit | ambiguous | miss",
  "answer": "……",
  "cited_clause_ids": ["REFUND-COMMON-01"],
  "handled_exceptions": ["……"],
  "ambiguity_question": "您是想问退货，还是想换货？"
}
```

`decision` 由模型的语义判断 + 确定性阈值共同决定（任一判 miss 即 miss，后者优先；只有确定性判 hit 且模型也判 hit 才 hit）。**模型不得把 miss 改成 hit**——这是防止"必答倾向"的关键。

### 4. 引用校验是硬门禁

`cited_clause_ids ⊆ 本次召回的 clause_id 集合`，不满足直接丢弃回答并降级为转人工，不问模型"重试一次"。重试会让模型学会"编一个看起来合理的 ID"。

### 5. Embedder 三实现

| 实现 | 用途 | 特征 |
|---|---|---|
| `StubEmbedder` | 单测 / CI | 对文本取 sha256 → 固定维度伪向量，**确定性** |
| `BgeEmbedder` | 本地 / 生产 | bge-m3，1024 维，需离线下载模型 |
| `HashEmbedder`（可选） | 标定脚本对照 | 词袋哈希，用于验证"不是靠模型碰巧对" |

CI 只跑 `StubEmbedder`。评测 `--embedder=stub|real` 与 004 的 `--llm=stub|real` 同构。

### 6. 阈值必须标定，不能拍脑袋

`rag_similarity_threshold` 默认 0.6 是**占位值**。实现完成后必须跑标定脚本：

```bash
python -m app.policy.calibrate --embedder=real --positives=eval/policy/pos.jsonl --negatives=eval/policy/neg.jsonl
```

输出正负样本相似度分布，取使 FPR < 5% 且 recall@1 最大化的阈值，写入 `.env` 并记录到 `specs/005-policy-rag/calibration.md`。**不同 embedding 模型的相似度尺度不同**，换模型必须重标。

## 与既有代码的接口

| 位置 | 改动 | 是否改既有语义 |
|---|---|---|
| `agent/llm/base.py` | `LLMClient` 拆为 `IntentLLM`（原 classify）+ `PolicyLLM`（rerank_and_answer） | 否，原 Protocol 保留为 `IntentLLM` 别名 |
| `agent/loop.py` `_dispatch` | `GENERAL` 分支前插入 `_try_policy` | 否，未命中仍走原 `_clarify` |
| `agent/loop.py` `_handle_degraded` | 显式注释不走 RAG | 否 |
| `models/domain.py` `Order` | 加 `product_type: str | None = None` | 否，默认 None，种子可缺 |
| `models/agent.py` `SessionState` | 加两个字段（带默认） | 否 |
| `handoff/coordinator.py` | 接收 `rag_top_similarity` 填入 `TurnSignals` | 否，`check_boundary` 已有逻辑 |
| `config.py` | 加 `rag_*` 配置 | 否 |

## 实施顺序

```text
Phase 1 基座      models → seed 加载与校验 → embedder(stub)
Phase 2 检索      store(PG) → retriever → 单测（含版本/歧义）
Phase 3 生成      answerer → 引用/例外校验 → 单测
Phase 4 接入      loop._try_policy → coordinator 信号 → 集成测试
Phase 5 评测      接 004 grader + 用例 → 标定阈值
Phase 6 收口      StubEmbedder 全量回归、README、宪法核对
```

Phase 2 与 Phase 3 可并行（接口先定 `RetrievalResult` / `PolicyAnswer`）。

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| `Order` 无 `product_type`，品类过滤无法执行 | 生鲜/定制条款可能被错用于服饰订单 | T004 先补字段；缺失时只匹配 `*` 条款（保守） |
| 阈值未标定就上线 | 要么大量漏答，要么大量错答 | T023 强制标定，未标定不允许把 real 模式结果写入基线 |
| 精排+生成合并后模型"必答倾向" | 无答案题被编造 | T015 的 `decision` 只能由模型降级不能升级；T017 引用校验兜底 |
| PG 未部署导致 CI 挂 | 测试不稳定 | CI 只用 stub + 内存 storer；`store.py` 提供 `InMemoryClauseStore` |
| 检索拖慢主链路 | 首响变慢 | T021 加 800ms 超时，超时视为 miss |

## 需要新增的配置项

```python
# --- policy rag (feature 005) ---
policy_seed_path: str = "seed/policies/policies.json"
pg_dsn: str = ""                          # 为空则用 InMemoryClauseStore
rag_recall_top_k: int = 5
rag_rerank_top_n: int = 2
rag_similarity_threshold: float = 0.6     # 占位，须标定
rag_ambiguity_margin: float = 0.05
rag_timeout_ms: int = 800
rag_embedder: str = "stub"                # stub | bge
rag_embedding_dim: int = 1024
```

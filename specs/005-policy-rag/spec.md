# Feature Specification: 政策知识检索（Policy RAG）

**Feature Branch**: `005-policy-rag`

**Created**: 2026-11-10

**Status**: Draft — 待负责人审核

**Input**: 客服需要回答政策类问题（退款到账多久、定制款能不能退、生鲜能不能退、价保多久），这类问题没有对应业务接口，只能靠知识检索。

## 背景与目标

Feature 001~004 建立了完整的**订单驱动**能力：查订单、查物流、改地址、退款、转人工、评测。但用户还会问大量**政策类**问题：

- "退款多久到账？"
- "衣服不合适能退吗？"
- "定制的能七天无理由吗？"
- "生鲜能退吗？"
- "买贵了能补差价吗？"

这些问题**没有订单接口能回答**，也不是要执行操作。当前系统对这类问题的处理是走 `_clarify`（"您具体想咨询什么呢"），两轮之后转人工——既答非所问，又白吃人工成本。

本 feature 的目标是让系统**在边界内自主回答政策问题，在边界外老实说不知道并转人工**。

### 核心设计约束：RAG 不是新意图，是 general 分支的前置检索

**不新增 `Intent.POLICY`**。理由：

1. **会造成意图歧义**。"我这个能不能退"既是 refund 咨询又是政策问答，LLM 在两类间猜，猜错就走错分支。002 花了大力气保证"意图只由 LLM 判断"，此处不应制造新的二选一。
2. **政策问题天然是兜底路径**。它与"无法识别的其他请求"同层：听懂了，但不是能直接执行的操作。
3. 若作为独立意图，评测需要区分"政策咨询"与"退款咨询"，而这两者的判定边界本质上就是模糊的，会把评测集污染成不可判分。

**接入位置**：

```
_dispatch
 ├─ READ_INTENTS   → 查订单 / 物流
 ├─ WRITE_INTENTS  → 确认 → 执行
 ├─ MANUAL/COMPLAINT → 转人工（不检索）
 └─ GENERAL → 【先检索政策】
               ├─ 命中且置信 → 带引用回答
               └─ 未命中 / 低分 / 歧义 → 原有 _clarify → 超轮次 → 转人工
```

**顺序理由**：澄清是"没听懂"，检索是"听懂了但不是我该做的"。用户问"退款多久到账"，若先澄清"您是想退款还是查物流"，是答非所问。先检索命中即答，体验正确。

**副作用**：`general` 分支从"只会问'您具体想咨询什么'"变为有实质回答，**过度澄清率应显著下降**——这正是 004 评测关注的指标。

### 核心设计约束：知识不切片，按条款入库

**不做文档自动切片**。客服政策本身是条目化的，切分会破坏语义边界（尤其"例外条款"）。存储单元是 **clause（条款）**，一条一个完整意思。

**版本是硬约束**：政策有版本和生效期。退 30 天前买的单，必须用**该订单下单时**生效的政策版本；未来生效的政策不得用于回答旧订单，即使语义更接近。

**例外单独成字段**：`exceptions` 与 `body` 分开存储。原因有二：
- 例外语义常偏离正文，参与 embedding 会拉低召回精度；
- 但生成时**必须处理例外**，分开后可以强制模型输出"是否处理了例外"。

## User Scenarios & Testing

### User Story 1 - 命中政策并带引用回答 (Priority: P1)

用户询问政策类问题，系统检索到对应条款，给出带条款引用的回答。

**Why this priority**: RAG 的存在理由。答不出这个，整个 feature 无意义。

**Independent Test**: 问"退款多久到账"，回答包含"银行卡 3~7 个工作日"且 `cited_clause_ids` 含 `REFUND-COMMON-01`。

**Acceptance Scenarios**:

1. **Given** 会话绑定订单 ORD1002（PAID，2026-11-06 下单），**When** 用户问"退款多久到账"，**Then** 引用 `REFUND-COMMON-01` 并回答各支付渠道时效
2. **Given** 同上，**When** 用户问"衣服不合适能退吗"，**Then** 引用 `RETURN-SEVENDAY-01`，说明 7 日时效与完好要求
3. **Given** 同上，**When** 用户问"退货运费谁出"，**Then** 引用 `REFUND-COMMON-03`，区分质量问题（商家承担）与无理由（消费者承担）
4. **Given** 回答已生成，**When** 检查 `cited_clause_ids`，**Then** 每个 ID 都必须存在于本次召回结果中

---

### User Story 2 - 例外条款必须被处理 (Priority: P1)

政策条款带例外时，回答不得只讲主体规则。定制、生鲜等除外商品必须明确说明。

**Why this priority**: 只答"可以七天无理由"而漏掉"定制除外"，是**错误回答**，比不回答危害更大——用户据此下单或据此操作会踩坑。

**Independent Test**: 问"定制的能七天无理由吗"，回答必须包含"不适用"或"除外"的明确结论，且 `handled_exceptions` 非空。

**Acceptance Scenarios**:

1. **Given** 订单为定制商品，**When** 用户问"能七天无理由吗"，**Then** 明确回答**不能**，引用 `RETURN-EXCEPTION-01` 并处理`定制商品`例外
2. **Given** 订单为生鲜商品，**When** 用户问"能退吗"，**Then** 明确回答**不适用七天无理由**，同时说明质量问题仍可售后
3. **Given** 召回条款 `exceptions` 非空，**When** 生成回答，**Then** `handled_exceptions` 必须列出已处理项，不得为空数组
4. **Given** 召回条款的例外与问题相关，**When** 生成的回答未提及该例外，**Then** 判为不合格（评测扣分）

---

### User Story 3 - 版本时效过滤 (Priority: P1)

按订单下单时间选择政策版本。旧订单不得看到未来生效的新政策。

**Why this priority**: 这是本 feature 与"玩具 RAG"的分界线。绝大多数 RAG demo 不考虑时间维度，而政策时效是真实的业务正确性问题。

**Independent Test**: 两个订单分别创建于 2026-11-06 与 2026-12-05，问同一问题"七天无理由超时了怎么办"，前者引用 v3（超期不受理），后者引用 v4（8~10 日可特殊受理）。

**Acceptance Scenarios**:

1. **Given** 订单 ORD1002（created_at=2026-11-06），**When** 用户问"七天无理由超时了怎么办"，**Then** 引用 `RETURN-SEVENDAY-02` **v3**，回答"超期不再受理"，**不得**出现"8~10 日""特殊受理"
2. **Given** 订单 ORD1009（created_at=2026-12-05），**When** 用户问同一问题，**Then** 引用 **v4**，回答包含"签收后第 8~10 日可申请一次特殊受理"
3. **Given** 检索执行，**When** 检查候选集，**Then** `effective_from <= order.created_at <= effective_to`（`effective_to` 为 null 视为无穷大）的过滤必须在**召回前**完成，而非召回后过滤
4. **Given** 订单 created_at 缺失（非种子来源），**When** 检索，**Then** 使用当前时间，并记入调用记录

---

### User Story 4 - 检索不到时老实说不知道 (Priority: P1)

知识库无对应条款时，不得编造，应明确表示无法确认并转人工。

**Why this priority**: "检索不到就说检索不到"比"检索到了答得好"更难做，也更值钱。编造政策会造成客诉升级甚至法律风险。

**Independent Test**: 问"你们老板是谁"，不返回任何政策内容，`state == "handoff_queued"` 或 `"clarifying"`。

**Acceptance Scenarios**:

1. **Given** 无对应条款的问题（"你们老板是谁"），**When** 检索，**Then** top1 相似度低于阈值，**不生成回答**，走转人工
2. **Given** 问题与竞品比较（"比京东便宜吗"），**When** 检索，**Then** 不生成，转人工
3. **Given** 用户要求超出政策的赔付（"能多赔我 200 吗"），**When** 检索，**Then** 不生成，转人工
4. **Given** 检索低分触发转人工，**When** 检查 `TurnSignals.rag_top_similarity`，**Then** 该值必须被写入，`check_boundary` 据此触发
5. **Given** 转人工被触发，**When** 回复用户，**Then** 不得包含任何未经条款支撑的具体政策结论

---

### User Story 5 - 歧义时追问而非硬答 (Priority: P2)

候选条款相似度接近时，不得任选一条回答，应追问澄清。

**Why this priority**: "退货"和"换货"语义很近，都高分但选错就答错了。追问的成本远低于答错。

**Independent Test**: 用户只说"能换吗"，`RETURN-SEVENDAY-01`（退货）与 `AFTERSALE-EXCHANGE-01`（换货）均高分，系统追问而不是直接引用其中一条。

**Acceptance Scenarios**:

1. **Given** 用户问"这个能换吗"，**When** top1 与 top2 分差小于 `ambiguity_margin`（默认 0.05），**Then** 追问澄清，不引用任一条款
2. **Given** 追问已发出，**When** 用户回答"我是想换个尺码"，**Then** 引用 `AFTERSALE-EXCHANGE-01` 回答
3. **Given** top1 显著高于 top2（分差 ≥ margin），**When** 生成，**Then** 直接引用 top1，不追问

---

### User Story 6 - 不侵入既有链路 (Priority: P1)

加检索后，读、写、转人工路径的行为不得变化。

**Why this priority**: 001~004 的 303 个测试是基线。RAG 是新增能力，不是重构。

**Independent Test**: 全量测试通过；"我要退款"仍走确认流程且不触发检索；"我要投诉"仍直接转人工且不检索。

**Acceptance Scenarios**:

1. **Given** 用户说"我要退款"，**When** 处理，**Then** 走确认流程，**不触发检索**（检索次数为 0）
2. **Given** 用户说"我要投诉你们"，**When** 处理，**Then** 直接转人工，**不触发检索**
3. **Given** 用户说"查下 ORD1003 物流"，**When** 处理，**Then** 走只读查询，**不触发检索**
4. **Given** LLM 不可用，**When** 处理任意输入，**Then** 走降级路径，**不生成任何政策回答**
5. **Given** 全量测试，**When** 运行，**Then** 既有 303 个测试全部通过

---

### User Story 7 - 降级期不生成政策回答 (Priority: P1)

LLM 不可用时，检索可以执行但**不得生成**自然语言政策回答。

**Why this priority**: 生成阶段最容易产生幻觉。LLM 不可用时若用模板或规则拼装政策回答，等于绕过了幻觉防线。

**Independent Test**: 模拟 LLM 超时，用户问政策问题，回复为"暂时无法处理"，无任何政策内容。

**Acceptance Scenarios**:

1. **Given** LLM 超时，**When** 用户问"退款多久到账"，**Then** 不返回政策内容，走降级话术
2. **Given** 降级期，**When** 检查调用记录，**Then** 能看出本次请求是否尝试过检索
3. **Given** 降级期，**When** 决策是否检索，**Then** 检索本身可以不执行（省成本），但不得执行生成

---

### Edge Cases

- 会话未绑定订单时问政策（无订单时间可做版本过滤）→ 使用当前时间，并告知用户"以下按当前政策"
- 政策条款跨版本都存在且都适用（如 `REFUND-COMMON-01` 不受版本影响）→ 取生效版本
- 用户一次问多个政策问题（"能退吗、运费谁出、多久到账"）→ 只答主问题，其余追问
- 召回条款适用品类与订单商品品类不匹配（如生鲜条款用于服饰订单）→ 过滤掉，不进入候选
- 检索服务异常（PG 不可用）→ 视为未命中，走澄清/转人工，不得阻塞主链路
- 用户问已过时政策（问"以前是不是 30 天保价"）→ 按订单时间选版本，不主动纠正历史

## Requirements

### Functional Requirements

**接入与门控**

- **FR-001**: 检索**只能**在 `Intent.GENERAL` 分支触发，不得新增 `Intent.POLICY`
- **FR-002**: 读意图（查订单/物流）、写意图（退款/改地址）、投诉、要人工**不得触发检索**
- **FR-003**: `GENERAL` 分支必须先检索、后澄清；检索未命中再走原有 `_clarify`
- **FR-004**: 检索前必须先判定问题是否属于政策类（避免"今天天气不错"也去检索）——由 LLM 在使用工具的同一轮判断，不额外增加调用

**知识结构**

- **FR-005**: 知识以 clause 为存储单元，**禁止**对条款做自动切片
- **FR-006**: 每条 clause 必须包含 `clause_id`、`policy_id`、`version`、`effective_from`、`effective_to`、`category`、`title`、`body`、`exceptions`、`applies_to`
- **FR-007**: `exceptions` **不得**参与 embedding 计算，但**必须**参与生成
- **FR-008**: 同一 `clause_id` 的多个版本，生效区间**不得重叠**

**检索**

- **FR-009**: 版本过滤必须在**召回前**完成（SQL / 元数据过滤），不得召回后再过滤
- **FR-010**: 版本过滤依据订单 `created_at`；无订单时使用当前时间，并记入调用记录
- **FR-011**: 品类过滤依据订单商品品类；`applies_to.product_types` 含 `*` 视为全部适用
- **FR-012**: 召回 top-K（默认 5），精排后取 top-N（默认 2）
- **FR-013**: top1 相似度 < `rag_similarity_threshold`（默认 0.6）→ **不生成**，写 `rag_top_similarity` 触发 `check_boundary`
- **FR-014**: top1 与 top2 分差 < `rag_ambiguity_margin`（默认 0.05）且两者都高于阈值 → **追问澄清**，不引用任一
- **FR-015**: 阈值与 margin 必须可配置，且**必须用真实数据标定**（写进 spec 的实现说明，不得拍脑袋）

**生成**

- **FR-016**: 生成必须输出结构化结果：`answer`、`cited_clause_ids`、`handled_exceptions`
- **FR-017**: `cited_clause_ids` 中的每个 ID 必须存在于本次召回结果，否则判为幻觉，**拒绝该回答**
- **FR-018**: 召回条款 `exceptions` 非空时，`handled_exceptions` 不得为空
- **FR-019**: 回答不得包含召回条款之外的政策结论（不得引入"常识"或"行业惯例"）
- **FR-020**: 回答必须附条款标题或 ID 的可读引用，用户能感知结论来自哪里

**降级与异常**

- **FR-021**: LLM 不可用时**禁止生成**政策回答；检索可跳过
- **FR-022**: 检索服务异常时视为未命中，不得抛出到用户侧，不得阻塞主链路
- **FR-023**: 检索耗时超时（默认 800ms）时视为未命中

**可观测**

- **FR-024**: 调用记录必须包含：是否检索、检索耗时、召回 clause_id 列表、各条相似度、top1-top2 分差、是否触发歧义、是否触发边界转接
- **FR-025**: 每次检索复用同一 `trace_id`

### Key Entities

- **PolicyClause**：条款（见 FR-006 字段）
- **RetrievalResult**：`query`、`order_created_at`、`candidates`（含相似度）、`top1`、`margin`、`decision`（hit / ambiguous / miss）
- **PolicyAnswer**：`answer`、`cited_clause_ids`、`handled_exceptions`、`decision`
- **SessionState 新增**：`last_cited_clause_ids`（用于多轮追问）、`policy_turns`（检索发生过的轮次）

## Success Criteria

### Measurable Outcomes

- **SC-001**: 命中题（40 条）**recall@1 ≥ 85%**，recall@5 ≥ 95%
- **SC-002**: 例外题（15 条）**例外处理覆盖率 100%**（召回含例外条款时回答必须提及）
- **SC-003**: 版本题（10 条）**版本正确率 100%**——旧订单命中新版本即为失败
- **SC-004**: 无答案题（15 条）**零编造**，全部转人工或澄清
- **SC-005**: 幻觉率 0%——`cited_clause_ids` 必须全部来自召回结果
- **SC-006**: 意图干扰题（10 条）**零误检索**——写意图/投诉不得触发检索
- **SC-007**: 既有 303 个测试**全部通过**
- **SC-008**: 检索 P95 延迟 < 200ms（不含生成）
- **SC-009**: 过度澄清率相对 004 基线**下降**

## Assumptions

- embedding 模型本地运行（bge-m3 或 bge-large-zh-v1.5），不依赖外部 API，保证评测可复现
- 向量库使用 PostgreSQL + pgvector，与业务同源，硬过滤与向量检索在同一查询完成
- 政策种子已由 `seed/policies/policies.json` 提供（28 条，12 category，含 1 条 v4）
- 精排复用现有 `LLMClient` 抽象，新增 `rerank` 方法，不引入 cross-encoder
- 测试使用 `StubEmbedder` 与 stub LLM，与现有测试架构一致

## 范围外

- 政策文档的多轮编辑与审核工作流
- 用户侧的知识反馈（点赞/纠错）
- 多语言政策
- 政策条款的自动生成或抽取（PDF/网页 → 条款）
- 跨政策库（如多商家共用一套 RAG）

## 已确认决策

| # | 问题 | 结论 |
|---|---|---|
| 1 | RAG 是意图还是工具 | **都不是，是 `GENERAL` 分支的前置检索** |
| 2 | 是否切文档 | **不切，按条款入库** |
| 3 | 向量库 | **PostgreSQL + pgvector** |
| 4 | embedding | **本地 bge-m3，不依赖外部 API** |
| 5 | 精排 | **LLM 精排，复用现有客户端抽象** |
| 6 | 版本过滤时机 | **召回前硬过滤，按订单 created_at** |
| 7 | 例外处理 | **不参与 embedding，参与生成，强制输出 handled_exceptions** |
| 8 | 低分处理 | **不生成，写 rag_top_similarity 触发 check_boundary 转人工** |
| 9 | 歧义处理 | **追问问清，不硬答** |
| 10 | 降级期 | **禁止生成政策回答** |

## 待确认项

- `rag_similarity_threshold` 与 `rag_ambiguity_margin` 的实际取值：需在实现后跑严格正负样本标定，当前 `0.6` 为占位默认值
- 生鲜等品类是否需要在订单模型上新增 `product_type` 字段（当前 `Order` 无品类信息，`applies_to` 过滤需要它）

## 依赖的外部改动

- `seed/orders.json` 已补 `created_at`（含 ORD1009 = 2026-12-05 的 v4 正样本），`_SeedOrder` 已支持该字段，缺失时回落到启动时刻
- `Order` 模型需要 `product_type`（见待确认项），否则品类过滤无法执行

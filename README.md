# 售后客服 Agent

面向**售中 + 售后**（订单驱动的服务场景）的客服 Agent。核心不是"能聊天"，而是**不可逆操作可控**、**AI 处理不了时能平稳移交人工**、**每一项能力都有可自动判分的验证**。

本项目按 [GitHub Spec Kit](https://github.com/github/spec-kit) 的 SDD 流程开发：spec → plan → tasks → 实现。宪法见 [`.specify/memory/constitution.md`](.specify/memory/constitution.md)。

## 进度

| Feature | 状态 |
|---|---|
| [001 工具层与 mock 业务服务](specs/001-tool-layer-mock/) | ✅ 完成 |
| [002 Agent 主循环与状态机](specs/002-agent-loop/) | ✅ 完成 |
| [003 转人工链路](specs/003-handoff/) | ✅ 完成 |
| [004 评测集与回归](specs/004-evaluation/) | ✅ 完成 |
| [005 政策 RAG](specs/005-policy-rag/) | ✅ 完成 |
| [006 客服前端（订单页 + 对话页 + 人工工作台）](specs/006-web-ui/) | ✅ 完成，547 后端测试 + 37 前端测试 + 7 E2E 通过 |

## 政策 RAG 演进（005 之后）

005 交付时是**纯向量检索 + 阈值判定**，实测有两类问题：答非所问（「能合并订单发货吗」
召回「修改收货地址」0.742 并直接生成答案）、以及知识棘手的例外条款够不着。之后补了四层：

| 层 | 作用 | 关键约束 |
|---|---|---|
| **真实 embedding** | `bge-small-zh-v1.5` 替换 demo 用的伪向量 | 不配 `MOCK_RAG_EMBEDDER=bge` 时退回 stub |
| **关键词召回** | 2~4 字 n-gram + RRF 融合，救实体词与例外条款（「吊牌被摘剪」只写在 exceptions 里，embedding 刻意排除了它） | 阈值判断始终只看向量余弦，融合分仅用于排序 |
| **LLM rerank** | 检索后、生成前交叉复核「这条条款真能回答问题吗」 | 只能降级不能升级；失败回退向量排序；全剔除 → miss |
| **查询改写** | 意图识别同一次调用顺带产出检索词，不新增模型调用 | 只喂检索层，rerank 与生成仍用用户原话；改写 miss 时回退原文再查一次 |

**rerank 判死 → 直接转人工（BOUNDARY）**：rerank 全剔除说明库里没有能回答的条款，
换个说法问也不会有答案，追问无意义，直接进人工队列；而**检索层**分数不够（没进 rerank）
可能只是表述问题，仍走 2 轮澄清。

真机实测（bge + 内网模型）：5 个答非所问案例全部挡住；「吊牌被剪了」从原文 0.523
检索层 miss，改写后 0.735 命中正确条款。

> **代价**：政策链路从「检索 + 生成」变成「检索 + rerank + 生成」，每题多一次模型调用，
> 内网慢模型下单次问答 20~40 秒。`rag_timeout_ms` 已调到 90 秒（默认 30 秒会超时）。

## 快速开始

```bash
uv venv
uv pip install -e "backend[dev]"

# 起服务（含 Agent 与业务接口）
cd backend && uvicorn app.main:app --reload

# 跑测试
cd backend && PYTHONIOENCODING=utf-8 python -m pytest -q
```

> **真实 embedding（可选）**：`.env` 里配 `MOCK_RAG_EMBEDDER=bge` 才走真模型，
> 需要额外装 `sentence-transformers`（会拉 torch，体积较大）：
>
> ```bash
> uv pip install sentence-transformers
> ```
>
> 不装也不要紧——把它设成 `stub` 或删掉该行即可，检索逻辑照常跑，只是相似度是
> 伪随机的。**测试永远用 stub**，不会加载真模型。
>
> 配了真模型时 `MOCK_RAG_TIMEOUT_MS` 建议 ≥ 90000：rerank 让政策链路变成三次
> 模型调用，默认 30 秒对内网慢模型必然超时。

前端：

```bash
cd frontend
npm ci
npx playwright install chromium   # E2E 需要
npm run dev                        # 开发模式，代理到 :8000
npm run typecheck && npm run test && npm run build
```

> **本地无 Redis 时**可用 `cd backend && python -m uvicorn dev_server:app --port 8000`，
> 它用 fakeredis 顶替；同时把 `backend/.env` 里的 `MOCK_LLM_BASE_URL` 置空以走桩模型
> （否则会连真实内网模型，慢且消耗额度）。

构建产物进镜像后由后端托管：

```bash
cd frontend && npm run build
mkdir -p ../backend/app/static && cp -r dist/* ../backend/app/static/
```

或用 Docker（**两阶段**：阶段 1 `npm run build` 含 `vue-tsc` 类型检查，
前端构建或类型检查失败即镜像构建失败）：

```bash
docker compose up --build
# 打开 http://localhost:8000
```

> 镜像内**不跑 Redis**，入口用 `dev_server`（fakeredis 顶替），因此重启后队列与会话清空。

## 演示级限制（务必知悉）

本项目**不是可上线形态**，以下为刻意简化：

- **无登录**：会话 ID 即凭证；任何人拿到 sessionId 即可读该会话
- **无订单归属校验**：订单表没有 `user_id`，从订单页带入的 `order_id` 后端无法验证归属
- **人工端固定账号**：`demo-agent / 小王`，无账号体系
- **不做 CI**：前端构建仅在 Dockerfile 与人工执行时校验（宪法 FR-021 无自动阻断机制）
- **不分页**：会话列表与队列全量返回，上限 200 条
- **镜像数据不持久**：Docker 入口用 `dev_server`（fakeredis），重启即清空队列、会话与消息
- **政策库覆盖不全**：退货方式、分期付款、门店退货等主题**没有对应条款**，
  问到这类问题会走「澄清 2 轮 → 转人工」，链路正确但答不出内容

详见 [specs/006-web-ui/plan.md](specs/006-web-ui/plan.md) 的风险与待确认项。

## 设计取舍

| 决策 | 为什么 |
|---|---|
| 只做售中 + 售后 | 边界由业务入口自然划出：有订单才有售中售后。售前"好"的标准无法客观定义，会毁掉可自动判分的评测集 |
| 单 Agent + 显式状态机 | 售后流程线性、顺序敏感，正是多 Agent 的弱项 |
| 优先级是纯规则函数 | 决定真人的工作分配，必须可复现、可向主管解释 |
| 业务失败返回 HTTP 200 + 数字码 | 调用方读 `code` 分流，不用从异常里区分业务与传输 |
| 故障注入按请求维度 | 全局生效会让评测中一个用例污染其他并发用例 |
| 业务数据内存态 + 种子 | 重启回到初始状态，保证评测可复现 |

## 目录

```
├── .specify/            SDD 宪法、模板、脚本
├── specs/              每个 feature 的 spec / plan / tasks / contracts
├── backend/            Python + FastAPI 服务
├── _prototype/         引入 SDD 前的原型代码（仅供对照，不要在此继续开发）
└── frontend/           Vue3 + TS（Feature 005 起）
```

## 已知范围外

- 真实接入第三方电商 / 物流平台 API
- 用户侧产品能力（注册登录、商品浏览、下单支付）
- 多渠道接入、客服排班与绩效

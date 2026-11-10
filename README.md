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
| [006 客服前端（订单页 + 对话页 + 人工工作台）](specs/006-web-ui/) | ✅ 完成，512 后端测试 + 30 前端测试 + 7 E2E 通过 |

## 快速开始

```bash
uv venv
uv pip install -e "backend[dev]"

# 起服务（含 Agent 与业务接口）
cd backend && uvicorn app.main:app --reload

# 跑测试
cd backend && PYTHONIOENCODING=utf-8 python -m pytest -q
```

前端：

```bash
cd frontend
npm ci
npx playwright install chromium   # E2E 需要
npm run dev                        # 开发模式，代理到 :8000
npm run typecheck && npm run test && npm run build
```

> **本地无 Redis 时**可用 `cd backend && python -m uvicorn dev_server:app --port 8010`，
> 它用 fakeredis 顶替；同时把 `backend/.env` 里的 `MOCK_LLM_BASE_URL` 置空以走桩模型
> （否则会连真实内网模型，慢且消耗额度）。

构建产物进镜像后由后端托管：

```bash
cd frontend && npm run build
mkdir -p ../backend/app/static && cp -r dist/* ../backend/app/static/
```

或用 Docker（多阶段，前端构建失败即镜像失败）：

```bash
docker compose up --build
```

## 演示级限制（务必知悉）

本项目**不是可上线形态**，以下为刻意简化：

- **无登录**：会话 ID 即凭证；任何人拿到 sessionId 即可读该会话
- **无订单归属校验**：订单表没有 `user_id`，从订单页带入的 `order_id` 后端无法验证归属
- **人工端固定账号**：`demo-agent / 小王`，无账号体系
- **不做 CI**：前端构建仅在 Dockerfile 与人工执行时校验（宪法 FR-021 无自动阻断机制）
- **不分页**：会话列表与队列全量返回，上限 200 条

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

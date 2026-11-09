# 售后客服 Agent

面向**售中 + 售后**（订单驱动的服务场景）的客服 Agent。核心不是"能聊天"，而是**不可逆操作可控**、**AI 处理不了时能平稳移交人工**、**每一项能力都有可自动判分的验证**。

本项目按 [GitHub Spec Kit](https://github.com/github/spec-kit) 的 SDD 流程开发：spec → plan → tasks → 实现。宪法见 [`.specify/memory/constitution.md`](.specify/memory/constitution.md)。

## 进度

| Feature | 状态 |
|---|---|
| [001 工具层与 mock 业务服务](specs/001-tool-layer-mock/) | ✅ 完成 |
| [002 Agent 主循环与状态机](specs/002-agent-loop/) | ✅ 完成 |
| [003 转人工链路](specs/003-handoff/) | ✅ 完成，222 个测试通过 |
| 004 SSE 流式对话 | 待开始 |
| 005 前端工程与交互骨架 | 待开始 |
| 006 前端接入真实接口 | 待开始 |
| 007 评测集与回归 | 待开始 |

## 快速开始

```bash
uv venv
uv pip install -e "backend[dev]"

# 起 mock 业务服务
cd backend && uvicorn app.main:app --reload

# 跑测试
cd backend && PYTHONIOENCODING=utf-8 python -m pytest -q
```

或用 Docker：

```bash
docker compose up --build
```

详见 [backend/README.md](backend/README.md)。

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

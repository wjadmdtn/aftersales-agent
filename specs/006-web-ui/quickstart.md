# Quickstart：006 客服前端的可执行验证

本文件只写**怎么跑、看什么**。协议细节见 [contracts/websocket.md](./contracts/websocket.md) 与 [contracts/rest.md](./contracts/rest.md)，字段定义见 [data-model.md](./data-model.md)。

---

## 0. 前置

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.11+ | 后端 |
| Redis | 7+ | 会话 / 队列 / 消息。本地可用 `docker run -p 6379:6379 redis:7` |
| Node | 20+ | 前端构建 |

```bash
cd backend
uv venv && uv pip install -e ".[dev]"

cd ../frontend
npm ci
npx playwright install chromium    # E2E 需要浏览器二进制，缺这一步 T057/T058 必失败
```

**演示可重复性**：`ORD1002` 退款成功后订单状态会被改成 `REFUNDED`（`store/memory.py:75`），同一次进程内再演示会返回「已经退款完成了」。**每次完整演示前重启后端进程**（内存 store 回到种子状态）。

**本地 stub LLM 必须配响应表**：`StubLLMClient` 的默认返回值是 `IntentResult(GENERAL)`（`agent/llm/stub.py:35`），`keyword_hints` 默认为 `{}`。也就是说**没登记的输入一律走澄清，两轮后转人工**。本地演示与 E2E 必须用带响应表的 stub：

```python
{"我要退款": REFUND, "确认": …, "我要投诉": COMPLAINT,
 "我的订单到哪了": LOGISTICS, "转人工": MANUAL}
```

否则演示第一步就会卡在「您能再具体说一下是想问哪方面吗？」。

> Windows 跑后端测试需 `PYTHONIOENCODING=utf-8`（仓库 README 已有说明）。

---

## 1. 后端能独立启动（FR-022 验收）

```bash
# 未构建前端时也必须成功
cd backend
MOCK_REDIS_URL=redis://localhost:6379/0 uvicorn app.main:app --port 8000
```

期望（见 contracts/rest.md §6）：

```bash
curl -s localhost:8000/health                 # {"status":"ok"}
curl -s localhost:8000/agent/health/llm       # 200，不是 500
curl -s localhost:8000/handoff/queue          # 200，不是 500
curl -s -X POST localhost:8000/agent/chat \
  -H 'content-type: application/json' \
  -d '{"session_id":"smoke","user_id":"u1","message":"我的订单到哪了","order_id":"ORD1003"}'
                                              # 200，带 reply
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/   # 404（此时没构建前端）
```

**这一步失败 = 生产装配没做完**（`main.py` 原状下 `/agent/chat` 直接 500）。

---

## 2. 后端测试

```bash
cd backend
PYTHONIOENCODING=utf-8 python -m pytest -q                       # 全量
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/test_demo_path.py -q   # 演示路径
PYTHONIOENCODING=utf-8 python -m pytest tests/integration/test_reconnect_backfill.py -q
```

**一票否决项**（任一失败即不得合并）：
- `test_demo_path.py` — 九步全链路
- `test_ws_agent_flow.py` — 并发接入唯一性
- 涉及确认退款（`ORD1002`，259.90）的断言

---

## 3. 前端

```bash
cd frontend
npm run typecheck      # SC-006：无 TS 错误
npm run test           # vitest：store / 去重 / 重连逻辑
npm run build          # SC-006：构建成功
```

开发联调：

```bash
# 终端 1
cd backend && uvicorn app.main:app --port 8000 --reload
# 终端 2
cd frontend && npm run dev     # Vite 代理 /agent /handoff /chat /ws → :8000
```

打开 `http://localhost:5173/#/chat`（用户端）与 `http://localhost:5173/#/console`（人工工作台）。

---

## 4. 演示路径（SC-001，9 步，全程不刷新）

准备：浏览器窗口 A 打开 `/#/order/ORD1002`（订单详情页），窗口 B 打开 `/#/console`（人工工作台）。

| 步 | 操作 | 期望（A 用户端） | 期望（B 工作台） |
|---|---|---|---|
| ⓪ | A 打开订单详情页 | 显示 ORD1002、已支付、259.90、收货地址、下单时间；底部有客服图标 | — |
| ① | A 点底部「联系客服」 | 跳到对话页，顶部显示「正在咨询：ORD1002」；输入「我要退款」 | — |
| ② | — | AI 回复含「ORD1002」与「259.90」，**逐段出现**；**不追问订单号** | — |
| ③ | A 输入「确认」 | 「已为您提交退款」 | — |
| ④ | A 输入「我要投诉」 | 「已为您优先接入人工客服」；状态条变「正在接入人工客服」；输入框禁用 | 侧边栏出现新会话，**红色**高风险标记 |
| ⑤ | B 点开该会话 | — | 显示摘要 / 「已查过」/ 优先级依据（含「风险触发」）/ 触发类型 = 风险；**不显示 AI 建议** |
| ⑥ | B 点「接入」 | 状态条变「人工客服 小王 正在为您服务」；输入框恢复可用 | 会话移出队列，进入「我的会话」 |
| ⑦ | B 输入「您好，我来处理」 | 实时出现人工消息，样式区别于 AI | 消息发出 |
| ⑧ | A 输入「好的，谢谢」 | — | 实时收到，**AI 不出现在中间** |
| ⑨ | B 点「结束会话」并确认 | 状态「会话已结束」，输入框禁用 | 双方同步 |

**观测点**
- SC-002：步 ② 首字出现 < 2s
- SC-003：步 ⑥ ⑦ ⑨ 两端状态变化 < 1s
- SC-004：步 ⑤ 的 Context Pack 四类字段（摘要 / 已尝试动作 / 优先级依据 / 触发类型）全部有值

---

## 5. 边界场景抽查

| 场景 | 操作 | 期望 |
|---|---|---|
| 重复转人工（US3-2） | 步④后 A 再点「转人工」 | 按钮禁用，不产生第二个队列项 |
| 排队中发消息（US3-3） | 步④~⑥ 之间 A 尝试发送 | 提示「正在为您接入，请稍候」，不入 Agent |
| 两人同时接入（FR-015） | 两个工作台窗口同时点「接入」 | 一个成功，另一个收到「已被接入」提示；用户端只看到一位客服 |
| 断线重连（FR-018） | 步⑥后刷新 A 页面 | 状态仍为「人工服务中」，历史消息完整，序号连续 |
| 消息去重（FR-027） | 重连后不出现重复消息 | 行数不变 |
| 降级（US1-4） | 断开 LLM（`MOCK_LLM_BASE_URL` 置空且走 stub 失败注入） | 统一降级话术 + 转人工按钮 |
| 会话已结束 | 步⑨后 A 再发消息 | 输入框禁用；服务端拒绝 `session_resolved` |
| 分辨率（SC-005） | 1280×720 / 1920×1080 | 两个页面均无横向滚动、无遮挡 |

---

## 6. E2E

```bash
cd frontend
# 需后端已在 8000 运行，且已构建前端并挂载（或直接用 Vite 5173）
npx playwright test e2e/demo-path.spec.ts
```

E2E 断言用 **`contains`** 而非全等（plan 待确认项 Q2：后端 `handoff_scripts.risk` 文案与 spec 文本不完全一致）。

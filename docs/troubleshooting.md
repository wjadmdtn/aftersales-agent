# 环境与踩坑记录

README 只保留「跑起来」需要的最短路径，这里放容易被环境卡住的细节。

## bge embedding（可选）

`.env` 里配 `MOCK_RAG_EMBEDDER=bge` 才走真模型，需要额外装 `sentence-transformers`（会拉 torch，体积较大）。

```bash
# 必须锁 3.x：sentence-transformers 6.x 会强制 transformers>=5，
# 而 transformers 5 已移除 bge-small-zh-v1.5 这类模型的旧式加载路径，
# 装成 6.x 会在启动时抛 "Unrecognized processing class in BAAI/bge-small-zh-v1.5"
uv pip install "sentence-transformers>=3.4,<4"
```

模型（`BAAI/bge-small-zh-v1.5`，约 92MB、13 个文件）走 HuggingFace 直连常常卡住，用镜像：

```bash
HF_ENDPOINT=https://hf-mirror.com python -c \
  "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-zh-v1.5')"
```

下载完之后用离线模式加载，避免启动时又去联网检查（会卡几十秒甚至超时）：

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python ...
```

模型实际维度是 **512**，注意与 `rag_embedding_dim`（默认 1024）的区别——接 pgvector 建表时必须用真实维度，否则会建出错误的表。

不装也不影响跑：把 `MOCK_RAG_EMBEDDER` 设成 `stub` 或删掉该行即可，检索逻辑照常走，只是相似度是伪随机的。**测试永远用 stub**，不会加载真模型。

## 超时

配了真模型时 `MOCK_RAG_TIMEOUT_MS` 建议 ≥ 90000：rerank 让政策链路从两次模型调用变成三次（意图 → rerank → 生成），默认 30 秒对内网慢模型必然超时。

## 本地没有 Redis

```bash
cd backend && python -m uvicorn dev_server:app --port 8000
```

`dev_server` 用 fakeredis 顶替；同时把 `backend/.env` 里的 `MOCK_LLM_BASE_URL` 置空以走桩模型（否则会连真实内网模型，慢且消耗额度）。

## 评测跑批

- **并发必须用 1**。实测 `--concurrency=4` 会把内网模型网关打到大面积超时降级（一批 301 条里几十条 `state=degraded`），串行则稳定。
- 政策用例每题三次模型调用，bge 下 31 条约需 20 分钟；全量 bge 跑批按小时计，别按 README 的历史耗时估计。
- 桩（`--llm=stub`）报告准确率虚高，只用于验链路，政策类数字尤其不可对外引用。

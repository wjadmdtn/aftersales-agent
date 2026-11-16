# 评测

220 条用例，按**处理路径**分类判分，而不是只分对错。

## 跑法

```bash
# CI 回归（确定性，秒级）
python eval/runner.py --llm=stub

# 出效果数字（简历/README 只引用这一档）
python eval/runner.py --llm=real --model=qwen-plus

# 并发跑：每条用例一份独立订单库与会话，互不串扰
python eval/runner.py --llm=real --model=deepseek-v4-flash --concurrency=12
```

并发度别拍脑袋定，先跑 `python eval/probe_concurrency.py` 探网关饱和点。
实测深寻网关在 16 并发左右吞吐见顶（约 1.3 req/s），再高只会把单个请求拖慢，
所以 12 是留了余量的一档。

跑 bge 需要本地已缓存模型，加 `HF_HUB_OFFLINE=1` 跳过联网校验，
否则会卡在 HuggingFace Hub 重试上。

`stub` 的报告会显式标注**不可对外引用**——桩按关键词返回，准确率虚高。

## 用例分布

| 路径 | 类别 | 条数 |
|---|---|---|
| business_action | standard | 55 |
| business_action | adversarial | 30 |
| business_action | no_keyword | 20 |
| clarify | with_emotion | 25 |
| clarify | no_emotion | 15 |
| handoff | 四类触发源 | 30 |
| irrelevant | — | 20 |
| e2e | 多轮流程 | 25 |
| **合计** | | **220** |

改用例请改 `seeds/*.py` 后跑 `python eval/generate.py`，不要手改 JSONL。

## 关键指标

- **过度转接率**：反问引导类里实际转人工的比例。衡量人工成本损耗，与识别准确率不是一回事
- **误转率 / 漏转率分开**：误转浪费人工，漏转伤害体验，合并会掩盖问题
- **话术承诺泄露**：反问引导话术出现"退款/退换/补偿"等词的次数，阈值 0

## 回归阈值

| 指标 | 阈值 | 理由 |
|---|---|---|
| 总体指标 | 3 个百分点 | 容忍 LLM 噪声（同版本重跑差 1-2 点很正常） |
| 关键单项 | 5 个百分点 | 样本少波动大，但退步更危险 |
| 硬性项 | **0** | 静默违规、降级期写操作、承诺泄露，一次即事故 |

阻断时输出指标名、基线值、当前值、阈值，不是只给一个退出码。

## 更新基线

```bash
python eval/runner.py --llm=stub --update-baseline
```

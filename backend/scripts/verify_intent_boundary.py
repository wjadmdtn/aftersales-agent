"""意图边界验证：泛抱怨 vs 明确投诉。

背景：评测发现「太慢了」「差评」「这服务真让人无语」被模型判成 complaint，
直接转人工 —— 过度转接率 2.5% → 15%，触发回归阻断。
真正的投诉诉求（「我要投诉」「12315」「曝光」）必须仍然转人工，不能被误伤。

这组用例不进 pytest：结论取决于模型行为，单测无法断言；
用固定样本跑真模型，改 prompt 前后对照用。

跑法：cd backend && PYTHONIOENCODING=utf-8 HF_HUB_OFFLINE=1 \
      uv run python scripts/verify_intent_boundary.py
"""
import asyncio
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from app.agent.llm.openai_compat import OpenAICompatLLMClient  # noqa: E402
from app.agent.intent import IntentClassifier  # noqa: E402
from app.models.agent import Intent  # noqa: E402

# 泛抱怨：应澄清（模型应判 general/None），绝不能判 complaint
COMPLAINTS_SHOULD_CLARIFY = [
    "太慢了", "等太久了", "每次都要等半天", "你们效率太低了",
    "这服务真让人无语", "差评", "有点不满意", "不太行啊", "很失望",
    "这个东西不好用", "质量太差了",
]

# 明确投诉：必须转人工（complaint 或 risk 都算对）
REAL_COMPLAINTS = [
    "我要投诉你们", "你们这是欺诈", "我要去消协投诉",
    "再不处理我就打12315", "我要曝光你们", "这是骗子吧",
]

# 明确要人工：必须判 manual
REAL_MANUAL = ["转人工", "我要找人工客服", "找真人", "我要客服电话"]


async def main() -> int:
    clf = IntentClassifier(OpenAICompatLLMClient(), keyword_map={})
    bad = 0

    print("=== 泛抱怨：不得判 complaint ===")
    for t in COMPLAINTS_SHOULD_CLARIFY:
        r, err = await clf.classify(t)
        got = r.intent.value if r else f"ERR:{err}"
        flag = "✗" if (r and r.intent in (Intent.COMPLAINT,)) else "✓"
        if flag == "✗":
            bad += 1
        print(f"  {flag} {t:14} -> {got}")

    print("\n=== 明确投诉：必须转人工 ===")
    for t in REAL_COMPLAINTS:
        r, err = await clf.classify(t)
        got = r.intent.value if r else f"ERR:{err}"
        flag = "✓" if (r and r.intent in (Intent.COMPLAINT, Intent.MANUAL)) else "✗"
        if flag == "✗":
            bad += 1
        print(f"  {flag} {t:14} -> {got}")

    print("\n=== 明确要人工：必须判 manual ===")
    for t in REAL_MANUAL:
        r, err = await clf.classify(t)
        got = r.intent.value if r else f"ERR:{err}"
        flag = "✓" if (r and r.intent is Intent.MANUAL) else "✗"
        if flag == "✗":
            bad += 1
        print(f"  {flag} {t:14} -> {got}")

    print(f"\n错误 {bad} 条")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

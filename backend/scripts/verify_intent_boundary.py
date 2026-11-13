"""意图边界验证：抱怨类不转人工、只有明确要人工才转。

口径（2026-11-13）：
- 删除 complaint / general 意图。抱怨、不满、投诉措辞统一判 UNKNOWN。
- UNKNOWN 只安抚，不追问、不转人工、不调工具。
- 转人工只由用户主动触发（manual）+ 系统异常/能力边界两条例外。

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

# 抱怨 / 投诉措辞：都必须判 UNKNOWN，绝不能判 MANUAL
SHOULD_BE_UNKNOWN = [
    "太慢了", "等太久了", "这服务真让人无语", "差评", "很失望",
    "这个东西不好用", "质量太差了",
    "我要投诉你们", "你们这是欺诈", "我要去消协投诉",
    "再不处理我就打12315", "我要曝光你们", "这是骗子吧",
    "你们太差劲了", "我要给差评", "必须给我个说法", "你们这服务真让人上火",
]

# 明确要人工：必须判 manual
REAL_MANUAL = ["转人工", "我要找人工客服", "找真人", "我要客服电话", "换个人跟我说"]


async def main() -> int:
    clf = IntentClassifier(OpenAICompatLLMClient(), keyword_map={})
    bad = 0

    print("=== 抱怨/投诉措辞：必须判 unknown，不得判 manual ===")
    for t in SHOULD_BE_UNKNOWN:
        r, err = await clf.classify(t)
        got = r.intent.value if r else f"ERR:{err}"
        flag = "✗" if (r and r.intent is not Intent.UNKNOWN) else "✓"
        if flag == "✗":
            bad += 1
        print(f"  {flag} {t:20} -> {got}")

    print("\n=== 明确要人工：必须判 manual ===")
    for t in REAL_MANUAL:
        r, err = await clf.classify(t)
        got = r.intent.value if r else f"ERR:{err}"
        flag = "✓" if (r and r.intent is Intent.MANUAL) else "✗"
        if flag == "✗":
            bad += 1
        print(f"  {flag} {t:20} -> {got}")

    print(f"\n错误 {bad} 条")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

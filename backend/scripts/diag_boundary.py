"""诊断：把意图分类 → 检索 → rerank 每一环的结果都打出来。"""
import asyncio
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from app.agent.llm.openai_compat import OpenAICompatLLMClient  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.policy import build_policy_service  # noqa: E402

CASES = ["能合并订单发货吗", "退款多久到账", "吊牌被剪了还能退吗"]


async def main():
    llm = OpenAICompatLLMClient()
    policy = build_policy_service(llm)

    for msg in CASES:
        print(f"\n=== {msg} ===")
        ir = await llm.classify(user_input=msg, hint_keywords=[], context={})
        print(f"intent={ir.intent} mode={ir.mode} conf={ir.confidence} reason={ir.reason}")

        if ir.intent.value != "policy":
            continue

        answer, retrieval = await policy.answer(
            question=msg, order_created_at=None, trace_id="diag"
        )
        if retrieval is not None:
            print(f"decision={retrieval.decision} rerank_rejected={retrieval.rerank_rejected}")
            print(f"reason={retrieval.reason}")
            for c in retrieval.candidates:
                print(
                    f"  cand {c.clause.clause_id} vec={c.vector_similarity} "
                    f"src={c.source} fusion={c.fusion_score:.4f} sim={c.similarity:.3f}"
                )
            if not retrieval.candidates:
                print("  (无候选)")
        print(f"answer.decision={answer.decision} rejection={answer.rejection}")
        print(f"answer: {answer.answer[:100]}")


if __name__ == "__main__":
    asyncio.run(main())

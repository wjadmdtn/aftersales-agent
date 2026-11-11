"""真机验证：查询改写（并入意图识别调用）对检索的实际影响。

每个 policy 案例跑两条链路对照：
  原文检索（search_query=None） vs 改写检索（意图识别顺带产出的 search_query）
对照要点：改写后 top 向量分、命中条款、最终决策是否改善，且不引入误判。
"""
import asyncio
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from app.agent.llm.openai_compat import OpenAICompatLLMClient  # noqa: E402
from app.services.policy import build_policy_service  # noqa: E402

CASES = [
    "吊牌被剪了还能退吗",     # 原文检索 miss（top1 0.523 < 0.64）
    "能合并订单发货吗",       # 知识缺口：预期 rerank 拒 → 转人工
    "退款多久到账",           # 正常命中，改写不应退化
    "发票开了还能退吗",       # 改写对照案例
    "我要退款",               # 非 policy：search_query 应为 None
]


async def main():
    llm = OpenAICompatLLMClient()
    policy = build_policy_service(llm)

    for msg in CASES:
        ir = await llm.classify(user_input=msg, hint_keywords=[], context={})
        print(f"\n=== {msg} ===")
        print(f"intent={ir.intent} mode={ir.mode} search_query={ir.search_query!r}")

        if ir.intent.value != "policy":
            continue

        for label, sq in (("原文", None), ("改写", ir.search_query)):
            answer, retrieval = await policy.answer(
                question=msg, order_created_at=None, trace_id=f"verify-{label}",
                search_query=sq,
            )
            top = ""
            if retrieval is not None and retrieval.candidates:
                c = retrieval.candidates[0]
                top = f"top={c.clause.clause_id}({c.vector_similarity:.3f})"
            rr = getattr(retrieval, "rerank_rejected", None) if retrieval else None
            print(
                f"  [{label}] decision={answer.decision} rerank_rejected={rr} "
                f"{top}\n    reason={retrieval.reason if retrieval else '-'}"
            )
            if answer.answer:
                print(f"    answer: {answer.answer[:100]}")


if __name__ == "__main__":
    asyncio.run(main())

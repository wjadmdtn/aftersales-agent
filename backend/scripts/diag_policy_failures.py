"""诊断政策失败用例：到底是哪一环断的。

四个环：意图分类 → 检索分数 → rerank 判定 → 生成。
逐环打印，判断该改哪一块。
"""
import asyncio
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from app.agent.llm.openai_compat import OpenAICompatLLMClient  # noqa: E402
from app.policy.embedder import BgeEmbedder  # noqa: E402
from app.policy.retriever import Retriever  # noqa: E402
from app.policy.seed import load_policies  # noqa: E402
from app.policy.store import InMemoryClauseStore  # noqa: E402
from app.config import settings  # noqa: E402
from app.policy.keyword import KeywordIndex  # noqa: E402

CASES = [
    ("policy-no-sevenday", "哪些商品不支持七天无理由"),
    ("policy-exchange-time", "换货多久能完成"),
    ("policy-repair", "保修怎么申请"),
    ("policy-address-rule", "还没发货可以改地址吗"),
    ("policy-spec-change", "能换颜色换尺码吗"),
    ("policy-tradein-value", "以旧换新旧机怎么估价"),
    ("policy-tradein-after", "以旧换新的新机售后怎么算"),
]


async def main():
    llm = OpenAICompatLLMClient()
    clauses = load_policies(settings.policy_seed_path)
    emb = BgeEmbedder()
    store = InMemoryClauseStore(clauses, emb)
    retriever = Retriever(
        store=store,
        embedder=emb,
        similarity_threshold=settings.rag_similarity_threshold,
        ambiguity_margin=settings.rag_ambiguity_margin,
        top_k=settings.rag_recall_top_k,
        keyword_index=KeywordIndex(clauses),
    )

    for cid, q in CASES:
        print(f"\n=== [{cid}] {q} ===")
        r = await llm.classify(user_input=q, hint_keywords=[], context={})
        print(f"  ① 意图: {r.intent.value}  mode={r.mode.value}  改写={r.search_query!r}")
        if r.intent.value != "policy":
            print("  → 断在意图分类，根本没进检索")
            continue

        for label, sq in (("原文", None), ("改写", r.search_query)):
            res = retriever.retrieve(
                query=(sq or "").strip() or q, order_created_at=None
            )
            print(f"  ② 检索[{label}]: {res.decision} | {res.reason}")
            for c in res.candidates[:3]:
                print(f"       {c.clause.clause_id} vec={c.vector_similarity:.3f} src={c.source}")

        if res.decision.value != "miss" and res.candidates:
            try:
                scores = await llm.rerank_policy(
                    query=q, clauses=[c.clause for c in res.candidates]
                )
                print(f"  ③ rerank: {scores}")
                kept = [k for k, v in scores.items() if v >= settings.rag_rerank_keep_threshold]
                print(f"     保留(阈值{settings.rag_rerank_keep_threshold}): {kept or '无 → 转人工'}")
            except Exception as e:
                print(f"  ③ rerank 失败: {e}")


if __name__ == "__main__":
    asyncio.run(main())

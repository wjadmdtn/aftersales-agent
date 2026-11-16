"""意图识别回归：用真实模型快速跑评测集里的意图用例。

只回答一个问题：**改完 prompt 或解析逻辑后，意图识别准确率有没有变差**。
不走完整 runner——那边 292 条用例、每轮含会话与工具调用，串行要 20 分钟以上；
这里只调一次 classify，8 并发三分钟出结果。

代价是判分口径更窄：只比对 intent 字段，不判 state / mode / tool_calls。
**它不是评测体系的一部分**，不能替代 `eval/runner.py` 的数字，
只用于改 prompt 前后的快速对照。

跑法（两种都行）：
    cd backend && PYTHONIOENCODING=utf-8 HF_HUB_OFFLINE=1 \
        uv run python scripts/intent_regression.py [--limit N] [--concurrency N]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("HF_HUB_OFFLINE", "1")
# .env 是相对 cwd 解析的（pydantic-settings env_file），必须切到 backend 再读配置
os.chdir(ROOT / "backend")
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.agent.llm.openai_compat import OpenAICompatLLMClient  # noqa: E402
from app.agent.resilience import stats  # noqa: E402
from app.eval.loader import load_dir  # noqa: E402

INTENT_PATHS = {"business_action", "clarify", "irrelevant", "handoff"}


async def main(limit: int | None, concurrency: int) -> int:
    cases = [c for c in load_dir(ROOT / "eval" / "cases") if c.path.value in INTENT_PATHS]
    cases = [c for c in cases if c.input]
    if limit:
        cases = cases[:limit]

    client = OpenAICompatLLMClient()
    sem = asyncio.Semaphore(concurrency)
    started = time.perf_counter()
    done = 0

    async def run(case):
        nonlocal done
        async with sem:
            try:
                r = await client.classify(
                    user_input=case.input,
                    hint_keywords=[],
                    context=case.context or {},
                )
                done += 1
                print(f"[{done}/{len(cases)}] {case.id} {case.input[:20]!r} -> {r.intent.value}", flush=True)
                return case, r, None
            except Exception as e:  # noqa: BLE001
                done += 1
                print(f"[{done}/{len(cases)}] {case.id} {case.input[:20]!r} -> ERR {type(e).__name__}", flush=True)
                return case, None, e

    results = await asyncio.gather(*(run(c) for c in cases))

    by_cat_ok: Counter = Counter()
    by_cat_all: Counter = Counter()
    mode_ok = mode_all = 0
    errors: list[str] = []
    wrong: list[str] = []

    for case, r, err in results:
        cat = case.category
        by_cat_all[cat] += 1
        if err is not None:
            errors.append(f"{case.id} {case.input}: {type(err).__name__}")
            continue
        expect = case.expect or {}
        want_intent = expect.get("intent")
        got = r.intent.value
        if want_intent is None:
            # 用例没断言意图（如部分 clarify 用例），只统计不判错
            continue
        if got == want_intent:
            by_cat_ok[cat] += 1
        else:
            wrong.append(f"{case.id} [{cat}] {case.input!r}: 期望 {want_intent} 实际 {got}")
        if expect.get("mode"):
            mode_all += 1
            if r.mode.value == expect["mode"]:
                mode_ok += 1

    elapsed = time.perf_counter() - started
    total_ok = sum(by_cat_ok.values())
    total_judged = total_ok + len(wrong)

    print("\n" + "=" * 60)
    print(f"用例 {len(cases)} 条，耗时 {elapsed:.0f}s")
    print(f"识别错误 {total_judged - total_ok}/{total_judged} "
          f"→ 准确率 {total_ok / total_judged:.1%}" if total_judged else "无可判定用例")
    if mode_all:
        print(f"mode 一致性 {mode_ok}/{mode_all} → {mode_ok / mode_all:.1%}")
    print(f"schema_replays={stats.schema_replays} schema_failures={stats.schema_failures}")
    print(f"LLM 失败率 {stats.failure_rate:.2%}")
    if errors:
        print(f"\n结构/调用失败 {len(errors)} 条:")
        for e in errors[:10]:
            print("  ", e)
    if wrong:
        print(f"\n意图判错 {len(wrong)} 条:")
        for w in wrong:
            print("  ", w)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--concurrency", type=int, default=6)
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.limit, a.concurrency)))

"""探测 LLM 网关可承受的并发度。

逐级提升并发，看延迟与错误率的拐点在哪。载荷用真实用例问句，
走 OpenAICompatLLMClient.classify —— 与评测同一条调用路径。

    python eval/probe_concurrency.py --levels 1,2,4,8,16
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
# settings 的 env_file=".env" 是相对路径，和 runner.py 一样切到 backend/ 再读
os.chdir(BACKEND)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from app.agent.llm.base import LLMError  # noqa: E402
from app.agent.llm.openai_compat import OpenAICompatLLMClient  # noqa: E402


def load_questions(n: int) -> list[str]:
    out: list[str] = []
    with (ROOT / "eval" / "cases" / "intent.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line).get("input")
            if q:
                out.append(q)
            if len(out) >= n:
                break
    return out


async def one(client, question: str) -> tuple[bool, float, str | None]:
    start = time.perf_counter()
    try:
        await client.classify(
            user_input=question, hint_keywords=[], context={"order_id": "ORD1002"}
        )
        return True, time.perf_counter() - start, None
    except LLMError as e:
        return False, time.perf_counter() - start, str(e)[:140]
    except Exception as e:  # noqa: BLE001
        return False, time.perf_counter() - start, f"{type(e).__name__}: {e}"[:140]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round(p * (len(s) - 1))))
    return s[idx]


async def main_async(args) -> int:
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    questions = load_questions(max(levels))
    if len(questions) < max(levels):
        print(f"用例问句不足：需要 {max(levels)} 条，只读到 {len(questions)} 条")
        return 1

    client = OpenAICompatLLMClient(model=args.model)
    print(f"网关 model={client.model}  timeout={client.timeout_ms / 1000:.0f}s\n")

    # 预热：首个请求要付连接建立与模型冷启动的代价，不计入统计
    warm = await one(client, questions[0])
    print(f"预热：{'OK' if warm[0] else 'FAIL ' + str(warm[2])} {warm[1]:.2f}s\n")

    header = f"{'并发':>4} {'成功':>6} {'失败':>5} {'p50':>7} {'p95':>7} {'墙钟':>7} {'吞吐':>7}"
    print(header)
    print("-" * len(header.encode("gbk", errors="ignore")))

    verdicts = []
    for level in levels:
        batch = questions[:level]
        latencies: list[float] = []
        ok = fail = 0
        errors: list[str] = []

        wall = time.perf_counter()
        for _ in range(args.rounds):
            results = await asyncio.gather(*(one(client, q) for q in batch))
            for success, elapsed, err in results:
                latencies.append(elapsed)
                if success:
                    ok += 1
                else:
                    fail += 1
                    if err and len(errors) < 3:
                        errors.append(err)
        wall = time.perf_counter() - wall

        total = ok + fail
        tput = total / wall if wall > 0 else 0
        print(
            f"{level:>4} {ok:>6} {fail:>5} "
            f"{statistics.median(latencies):>6.2f}s {pct(latencies, 0.95):>6.2f}s "
            f"{wall:>6.2f}s {tput:>6.2f}/s"
        )
        for e in errors:
            print(f"       ! {e}")

        fail_rate = fail / total if total else 1.0
        verdicts.append((level, fail_rate, statistics.median(latencies), tput))

        if fail_rate > args.max_fail_rate:
            print(f"\n并发 {level} 失败率 {fail_rate:.0%} 超过 {args.max_fail_rate:.0%}，停止抬高。")
            break

    print("\n结论")
    safe = [v for v in verdicts if v[1] <= args.max_fail_rate]
    if safe:
        best = max(safe, key=lambda v: v[3])
        print(f"  最高可用并发 {best[0]}：p50 {best[2]:.2f}s，吞吐 {best[3]:.2f} req/s，失败率 {best[1]:.0%}")
        print(f"  建议评测并发 N={best[0]}（留一档余量可再往下取）")
    else:
        print("  所有档位都不可用，先排查网关与凭据。")
    await client.aclose()
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--levels", default="1,2,4,8,16")
    p.add_argument("--rounds", type=int, default=2, help="每档重复轮数，取全部样本统计")
    p.add_argument("--model", default=None)
    p.add_argument("--max-fail-rate", type=float, default=0.02)
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

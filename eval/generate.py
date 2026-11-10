"""生成全部评测用例到 eval/cases/。

覆盖用例前请先跑这个脚本。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.eval.loader import write_cases  # noqa: E402
from eval.seeds import clarify_seed, e2e_seed, handoff_seed, intent_seed  # noqa: E402

CASES_DIR = ROOT / "eval" / "cases"

# spec 约定的分布
EXPECTED = {
    "business_action": {"standard": 55, "adversarial": 30, "no_keyword": 20},
    "clarify": {"with_emotion": 25, "no_emotion": 15},
    "irrelevant": {"irrelevant": 20},
    "handoff": {"handoff": 30},
    "e2e": {"e2e": 25},
}


def main() -> int:
    groups = {
        "intent.jsonl": intent_seed.build(),
        "clarify.jsonl": clarify_seed.build(),
        "handoff.jsonl": handoff_seed.build(),
        "e2e.jsonl": e2e_seed.build(),
    }

    CASES_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, cases in groups.items():
        write_cases(CASES_DIR / name, cases)
        total += len(cases)
        print(f"{name}: {len(cases)} 条")

    print(f"\n合计 {total} 条")

    all_cases = [c for cs in groups.values() for c in cs]
    dist = Counter((c.path.value, c.category) for c in all_cases)
    print("\n分类分布：")
    for (path, cat), n in sorted(dist.items()):
        print(f"  {path:16} {cat:14} {n:3}")

    problems: list[str] = []
    for path, cats in EXPECTED.items():
        for cat, want in cats.items():
            got = dist.get((path, cat), 0)
            if got != want:
                problems.append(f"{path}/{cat}: 期望 {want}，实际 {got}")

    if problems:
        print("\n⚠ 分布与 spec 不符：")
        for p in problems:
            print(f"  {p}")
        return 1
    print("\n✅ 分布符合 spec")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

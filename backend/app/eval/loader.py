"""JSONL 用例加载与校验。

格式非法时在加载阶段失败并指出具体条目——220 条里错一条，
不该等到跑完才发现。
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.eval.schema import EvalCase


class EvalCaseError(ValueError):
    pass


def load_cases(path: str | Path) -> list[EvalCase]:
    p = Path(path)
    if not p.exists():
        raise EvalCaseError(f"用例文件不存在：{p}")

    cases: list[EvalCase] = []
    seen: set[str] = set()

    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise EvalCaseError(f"{p.name}:{lineno} JSON 解析失败：{e}") from e
        try:
            case = EvalCase.model_validate(raw)
        except ValidationError as e:
            raise EvalCaseError(f"{p.name}:{lineno} 用例格式非法：{e}") from e
        if case.id in seen:
            raise EvalCaseError(f"{p.name}:{lineno} 用例 id 重复：{case.id}")
        seen.add(case.id)
        cases.append(case)

    if not cases:
        raise EvalCaseError(f"{p.name} 没有加载到任何用例")
    return cases


def load_dir(directory: str | Path) -> list[EvalCase]:
    d = Path(directory)
    if not d.exists():
        raise EvalCaseError(f"用例目录不存在：{d}")
    all_cases: list[EvalCase] = []
    seen: set[str] = set()
    for path in sorted(d.glob("*.jsonl")):
        for case in load_cases(path):
            if case.id in seen:
                raise EvalCaseError(f"跨文件用例 id 重复：{case.id}")
            seen.add(case.id)
            all_cases.append(case)
    if not all_cases:
        raise EvalCaseError(f"{d} 下没有找到任何 .jsonl 用例")
    return all_cases


def write_cases(path: str | Path, cases: list[EvalCase]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as f:
        for case in cases:
            f.write(case.model_dump_json(exclude_none=True) + "\n")

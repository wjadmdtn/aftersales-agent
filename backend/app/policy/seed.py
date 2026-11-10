"""T003: 政策种子加载与校验。

非法种子必须在启动阶段失败，而不是等到检索时才发现条款区间重叠。
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.policy.models import PolicyClause


class PolicySeedError(RuntimeError):
    pass


def load_policies(path: str | Path) -> list[PolicyClause]:
    p = Path(path)
    if not p.exists():
        raise PolicySeedError(f"policy seed not found: {p}")

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PolicySeedError(f"policy seed is not valid JSON: {e}") from e

    if not isinstance(raw, list):
        raise PolicySeedError("policy seed must contain a JSON array of clauses")

    clauses: list[PolicyClause] = []
    for idx, item in enumerate(raw):
        try:
            clauses.append(PolicyClause.model_validate(item))
        except ValidationError as e:
            raise PolicySeedError(f"invalid policy clause at index {idx}: {e}") from e

    _check_unique_version(clauses)
    _check_no_overlap(clauses)
    return clauses


def _check_unique_version(clauses: list[PolicyClause]) -> None:
    seen: set[tuple[str, int]] = set()
    for c in clauses:
        key = (c.clause_id, c.version)
        if key in seen:
            raise PolicySeedError(f"duplicate (clause_id, version): {key}")
        seen.add(key)


def _check_no_overlap(clauses: list[PolicyClause]) -> None:
    """同一 clause_id 的多个版本，生效区间不得重叠——重叠意味着版本过滤结果不确定。"""
    by_id: dict[str, list[PolicyClause]] = {}
    for c in clauses:
        by_id.setdefault(c.clause_id, []).append(c)

    for clause_id, versions in by_id.items():
        ordered = sorted(versions, key=lambda c: c.effective_from)
        for prev, nxt in zip(ordered, ordered[1:]):
            if prev.effective_to is None:
                raise PolicySeedError(
                    f"{clause_id} v{prev.version} is open-ended but a later version exists"
                )
            if prev.effective_to >= nxt.effective_from:
                raise PolicySeedError(
                    f"{clause_id} versions overlap: v{prev.version} ends {prev.effective_to}, "
                    f"v{nxt.version} starts {nxt.effective_from}"
                )

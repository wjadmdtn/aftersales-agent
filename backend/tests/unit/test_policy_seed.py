"""T006: 政策种子校验。区间重叠 / 重复版本必须在启动阶段失败。"""
from __future__ import annotations

import json

import pytest

from app.policy.seed import PolicySeedError, load_policies

SEED = "seed/policies/policies.json"


def _base(**over):
    c = {
        "clause_id": "X-01",
        "policy_id": "X",
        "version": 1,
        "effective_from": "2026-01-01",
        "effective_to": None,
        "category": "refund",
        "title": "t",
        "body": "b",
        "exceptions": [],
        "applies_to": {"product_types": ["*"], "scenes": []},
    }
    c.update(over)
    return c


def _write(tmp_path, items):
    p = tmp_path / "p.json"
    p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return p


def test_real_seed_loads():
    clauses = load_policies(SEED)
    assert len(clauses) == 28
    assert len({c.category for c in clauses}) == 12


def test_seed_has_v3_and_v4_of_same_clause():
    clauses = load_policies(SEED)
    vs = sorted(
        c.version for c in clauses if c.clause_id == "RETURN-SEVENDAY-02"
    )
    assert vs == [3, 4]


def test_effective_at_boundaries():
    clauses = {c.version: c for c in load_policies(SEED) if c.clause_id == "RETURN-SEVENDAY-02"}
    from datetime import date

    assert clauses[3].effective_at(date(2026, 11, 6)) is True
    assert clauses[3].effective_at(date(2026, 12, 5)) is False
    assert clauses[4].effective_at(date(2026, 12, 5)) is True
    assert clauses[4].effective_at(date(2026, 11, 6)) is False


def test_duplicate_version_rejected(tmp_path):
    p = _write(tmp_path, [_base(), _base()])
    with pytest.raises(PolicySeedError, match="duplicate"):
        load_policies(p)


def test_overlapping_ranges_rejected(tmp_path):
    p = _write(
        tmp_path,
        [
            _base(version=1, effective_from="2026-01-01", effective_to="2026-06-30"),
            _base(version=2, effective_from="2026-06-01", effective_to=None),
        ],
    )
    with pytest.raises(PolicySeedError, match="overlap"):
        load_policies(p)


def test_open_ended_earlier_version_rejected(tmp_path):
    p = _write(
        tmp_path,
        [
            _base(version=1, effective_from="2026-01-01", effective_to=None),
            _base(version=2, effective_from="2026-12-01", effective_to=None),
        ],
    )
    with pytest.raises(PolicySeedError, match="open-ended"):
        load_policies(p)


def test_missing_file(tmp_path):
    with pytest.raises(PolicySeedError, match="not found"):
        load_policies(tmp_path / "nope.json")


def test_embed_text_excludes_exceptions():
    clauses = load_policies(SEED)
    c = next(c for c in clauses if c.clause_id == "RETURN-EXCEPTION-01")
    assert "定制" in c.embed_text
    assert "盲盒" not in c.embed_text  # v4 才有，v3 不应包含

"""用例格式校验：非法用例必须在加载阶段失败。"""

import json

import pytest

from app.eval.loader import EvalCaseError, load_cases, load_dir, write_cases


def _line(**kw) -> str:
    base = {"id": "c1", "category": "std", "path": "business_action", "input": "我要退款", "expect": {"intent": "refund"}}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def test_valid_load(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text(_line() + "\n", encoding="utf-8")
    assert len(load_cases(p)) == 1


def test_bad_json_reports_line(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text(_line() + "\n{not json}\n", encoding="utf-8")
    with pytest.raises(EvalCaseError) as e:
        load_cases(p)
    assert ":2" in str(e.value)


def test_duplicate_id(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text(_line() + "\n" + _line() + "\n", encoding="utf-8")
    with pytest.raises(EvalCaseError) as e:
        load_cases(p)
    assert "重复" in str(e.value)


def test_missing_expected_field(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text(_line(expect={}) + "\n", encoding="utf-8")
    with pytest.raises(EvalCaseError):
        load_cases(p)


def test_e2e_requires_turns(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text(_line(path="e2e", input=None, expect={"order_status": "PAID"}) + "\n", encoding="utf-8")
    with pytest.raises(EvalCaseError):
        load_cases(p)


def test_non_e2e_requires_input(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text(_line(input=None) + "\n", encoding="utf-8")
    with pytest.raises(EvalCaseError):
        load_cases(p)


def test_cross_file_duplicate(tmp_path):
    (tmp_path / "a.jsonl").write_text(_line() + "\n", encoding="utf-8")
    (tmp_path / "b.jsonl").write_text(_line() + "\n", encoding="utf-8")
    with pytest.raises(EvalCaseError) as e:
        load_dir(tmp_path)
    assert "跨文件" in str(e.value)


def test_comments_and_blanks_skipped(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text("# comment\n\n" + _line() + "\n", encoding="utf-8")
    assert len(load_cases(p)) == 1


def test_roundtrip(tmp_path):
    from app.eval.schema import EvalCase

    cases = [EvalCase(id="c1", category="std", path="business_action", input="x", expect={"intent": "refund"})]
    p = tmp_path / "out.jsonl"
    write_cases(p, cases)
    assert load_cases(p)[0].id == "c1"

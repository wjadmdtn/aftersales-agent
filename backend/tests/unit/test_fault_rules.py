import pytest

from app.models.enums import FaultType
from app.faults.rules import FaultRuleError, parse_fault_header


def test_no_header_returns_none():
    assert parse_fault_header(None) is None
    assert parse_fault_header("") is None


def test_parse_timeout():
    rule = parse_fault_header('{"type": "timeout", "delay_ms": 5000}')
    assert rule.type is FaultType.TIMEOUT
    assert rule.delay_ms == 5000


def test_parse_error():
    rule = parse_fault_header('{"type": "error", "status": 503}')
    assert rule.type is FaultType.ERROR
    assert rule.status == 503


def test_parse_delay():
    rule = parse_fault_header('{"type": "delay", "delay_ms": 800}')
    assert rule.type is FaultType.DELAY


def test_parse_dirty():
    rule = parse_fault_header('{"type": "dirty", "field": "data.refund_id"}')
    assert rule.type is FaultType.DIRTY
    assert rule.field == "data.refund_id"


def test_dirty_requires_field():
    with pytest.raises(FaultRuleError):
        parse_fault_header('{"type": "dirty"}')


def test_delay_requires_positive_delay():
    with pytest.raises(FaultRuleError):
        parse_fault_header('{"type": "delay"}')


def test_invalid_json():
    with pytest.raises(FaultRuleError):
        parse_fault_header("not json")


def test_non_object_json():
    with pytest.raises(FaultRuleError):
        parse_fault_header("[1,2,3]")


def test_unknown_type():
    with pytest.raises(FaultRuleError):
        parse_fault_header('{"type": "explode"}')

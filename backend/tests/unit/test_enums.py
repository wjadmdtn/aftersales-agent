import pytest

from app.models.enums import (
    FaultType,
    OrderStatus,
    RefundStatus,
    ResultCode,
    code_category,
)


def test_result_code_ranges():
    assert code_category(ResultCode.OK) == "success"
    assert code_category(ResultCode.ORDER_NOT_FOUND) == "not_found"
    assert code_category(ResultCode.ADDRESS_NOT_EDITABLE) == "rejected"
    assert code_category(ResultCode.INTERNAL_ERROR) == "error"


def test_unknown_code_raises():
    with pytest.raises(ValueError):
        code_category(9999)


def test_duplicate_request_is_rejected_category():
    """幂等命中归在 3xxx，业务语义上是成功的，单独成码便于区分。"""
    assert code_category(ResultCode.DUPLICATE_REQUEST) == "rejected"
    assert ResultCode.DUPLICATE_REQUEST.value == 3006


def test_codes_are_unique():
    values = [c.value for c in ResultCode]
    assert len(values) == len(set(values))


def test_reserved_slots_not_taken():
    """已占用的编号固定，新增只能往空位放，防止复用旧码。"""
    assert ResultCode.ORDER_NOT_FOUND.value == 2001
    assert ResultCode.LOGISTICS_NOT_FOUND.value == 2002
    assert ResultCode.REFUND_NOT_FOUND.value == 2003
    assert ResultCode.ORDER_STATUS_NOT_ALLOWED.value == 3001
    assert ResultCode.ADDRESS_NOT_EDITABLE.value == 3005
    assert ResultCode.DUPLICATE_REQUEST.value == 3006
    assert ResultCode.INTERNAL_ERROR.value == 4001
    assert ResultCode.DEPENDENCY_TIMEOUT.value == 4002
    assert ResultCode.INVALID_REQUEST.value == 4003


def test_status_enums_are_strings():
    assert OrderStatus.SHIPPED == "SHIPPED"
    assert RefundStatus.SUCCESS == "SUCCESS"
    assert FaultType.TIMEOUT == "timeout"

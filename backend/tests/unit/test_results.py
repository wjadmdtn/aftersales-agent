import pytest

from app.models.enums import ResultCode
from app.models.results import BizResult, ResultMeta, error, not_found, ok, rejected

META = ResultMeta(trace_id="t" * 32)


def test_ok_has_no_reason():
    r = ok({"x": 1}, META)
    assert r.code is ResultCode.OK
    assert r.reason is None


def test_rejected_requires_reason():
    with pytest.raises(ValueError):
        BizResult(code=ResultCode.ADDRESS_NOT_EDITABLE, meta=META)

    r = rejected(ResultCode.ADDRESS_NOT_EDITABLE, "订单已发货", META)
    assert r.reason == "订单已发货"


def test_not_found_helper_rejects_wrong_code():
    with pytest.raises(ValueError):
        not_found(ResultCode.ADDRESS_NOT_EDITABLE, META)


def test_error_helper_rejects_wrong_code():
    with pytest.raises(ValueError):
        error(ResultCode.ORDER_NOT_FOUND, "x", META)


def test_rejected_helper_rejects_not_found_code():
    with pytest.raises(ValueError):
        rejected(ResultCode.ORDER_NOT_FOUND, "x", META)


def test_not_found_allows_empty_data():
    r = not_found(ResultCode.ORDER_NOT_FOUND, META)
    assert r.data is None
    assert r.code is ResultCode.ORDER_NOT_FOUND


def test_serializes_code_as_int():
    r = ok({"x": 1}, META)
    payload = r.model_dump(mode="json")
    assert payload["code"] == 1000
    assert isinstance(payload["code"], int)

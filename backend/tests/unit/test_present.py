"""T028: 结果码 → 自然语言。每个码都要有话说，4xxx 不得暴露技术细节。"""

import pytest

from app.agent.present import render
from app.models.enums import ResultCode


def _body(code, data=None, reason=None):
    return {"code": code, "data": data, "reason": reason, "meta": {"trace_id": "t"}}


def test_all_codes_have_render():
    """每个结果码都必须能渲染出非空文本。"""
    for code in ResultCode:
        text = render(_body(code))
        assert text and isinstance(text, str), code


def test_refund_ok_mentions_amount():
    text = render(_body(ResultCode.OK, {"refund_id": "RF1", "amount": "50.00"}))
    assert "50.00" in text and "退款" in text


def test_logistics_not_found_is_not_failure_wording():
    """2002 不能说成"查询失败"，要说"还没发货"。"""
    text = render(_body(ResultCode.LOGISTICS_NOT_FOUND))
    assert "发货" in text
    assert "失败" not in text


def test_order_not_found_is_clear():
    text = render(_body(ResultCode.ORDER_NOT_FOUND))
    assert "没有查到" in text or "没查到" in text


def test_error_codes_hide_technical_detail():
    for code in (ResultCode.INTERNAL_ERROR, ResultCode.DEPENDENCY_TIMEOUT, ResultCode.INVALID_REQUEST):
        text = render(_body(code))
        for leak in ("trace", "Exception", "500", "timeout", "超时", "request_id"):
            assert leak not in text, f"{code} leaks {leak}"


def test_address_not_editable_explains():
    text = render(_body(ResultCode.ADDRESS_NOT_EDITABLE))
    assert "发货" in text


def test_duplicate_request_still_reads_as_success():
    """幂等命中业务上是成功的，用户不该看到"重复请求"这种技术词。"""
    text = render(_body(ResultCode.DUPLICATE_REQUEST, {"refund_id": "RF1", "amount": "10.00"}))
    assert "重复" not in text
    assert "退款" in text

import pytest

from app.models.enums import ResultCode
from app.observability.recorder import CallRecord, CallRecorder


def _rec(trace_id: str, request_id: str | None = None, action: str = "order.get", attempt: int = 1):
    return CallRecord(
        trace_id=trace_id,
        request_id=request_id,
        action=action,
        code=ResultCode.OK,
        elapsed_ms=5,
        attempt=attempt,
    )


def test_append_and_count():
    r = CallRecorder(capacity=10)
    r.append(_rec("t1"))
    assert len(r) == 1


def test_ring_buffer_evicts_oldest():
    r = CallRecorder(capacity=3)
    for i in range(5):
        r.append(_rec(f"t{i}"))
    assert len(r) == 3
    assert [x.trace_id for x in r.all()] == ["t2", "t3", "t4"]


def test_find_by_trace_id():
    r = CallRecorder()
    r.append(_rec("t1"))
    r.append(_rec("t2"))
    r.append(_rec("t1", action="refund.create"))
    found = r.find_by_trace_id("t1")
    assert len(found) == 2


def test_find_by_request_id_crosses_traces():
    """同一笔业务重试 3 次：3 个 trace、同一 request_id。"""
    r = CallRecorder()
    for attempt in (1, 2, 3):
        r.append(_rec(f"t{attempt}", request_id="refund:ORD1:x", attempt=attempt))
    found = r.find_by_request_id("refund:ORD1:x")
    assert len(found) == 3
    assert {f.trace_id for f in found} == {"t1", "t2", "t3"}
    assert [f.attempt for f in found] == [1, 2, 3]


def test_attempt_increments_per_request_id():
    r = CallRecorder()
    assert r.next_attempt("refund.create", "rid1") == 1
    assert r.next_attempt("refund.create", "rid1") == 2
    assert r.next_attempt("refund.create", "rid1") == 3
    # 不同 request_id 独立计数
    assert r.next_attempt("refund.create", "rid2") == 1


def test_attempt_without_request_id_is_always_one():
    r = CallRecorder()
    assert r.next_attempt("order.get", None) == 1
    assert r.next_attempt("order.get", None) == 1


def test_clear():
    r = CallRecorder()
    r.append(_rec("t1"))
    r.next_attempt("a", "b")
    r.clear()
    assert len(r) == 0
    assert r.next_attempt("a", "b") == 1

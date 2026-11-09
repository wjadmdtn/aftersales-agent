"""T005: 状态与结果码枚举。

ResultCode 采用 4 位数字分段编码，调用方按千位分流：
    1xxx 成功 / 2xxx 未找到 / 3xxx 业务拒绝 / 4xxx 系统异常

编码一经发布不可复用。删除某个码时留空位并标 RESERVED。
"""
from enum import IntEnum, StrEnum


class ResultCode(IntEnum):
    # ---- 1xxx 成功 ----
    OK = 1000

    # ---- 2xxx 未找到（没有这个对象，不是错误）----
    ORDER_NOT_FOUND = 2001
    LOGISTICS_NOT_FOUND = 2002
    REFUND_NOT_FOUND = 2003

    # ---- 3xxx 业务拒绝（有明确原因，可告知用户）----
    ORDER_STATUS_NOT_ALLOWED = 3001
    AMOUNT_EXCEEDS_ORDER = 3002
    AMOUNT_INVALID = 3003
    ADDRESS_INCOMPLETE = 3004
    ADDRESS_NOT_EDITABLE = 3005
    DUPLICATE_REQUEST = 3006

    # ---- 4xxx 系统异常（调用方应降级或转人工）----
    INTERNAL_ERROR = 4001
    DEPENDENCY_TIMEOUT = 4002
    INVALID_REQUEST = 4003


class OrderStatus(StrEnum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CLOSED = "CLOSED"
    REFUNDING = "REFUNDING"
    REFUNDED = "REFUNDED"


class RefundStatus(StrEnum):
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class FaultType(StrEnum):
    DELAY = "delay"
    TIMEOUT = "timeout"
    ERROR = "error"
    DIRTY = "dirty"


SUCCESS_RANGE = range(1000, 2000)
NOT_FOUND_RANGE = range(2000, 3000)
REJECTED_RANGE = range(3000, 4000)
ERROR_RANGE = range(4000, 5000)


def code_category(code: int) -> str:
    """调用方按类别分流用的辅助函数，避免各处硬编码区间。"""
    if code in SUCCESS_RANGE:
        return "success"
    if code in NOT_FOUND_RANGE:
        return "not_found"
    if code in REJECTED_RANGE:
        return "rejected"
    if code in ERROR_RANGE:
        return "error"
    raise ValueError(f"unknown result code: {code}")

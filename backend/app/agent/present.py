"""T029: 结果码 → 自然语言。

用户不读 code。每种结果都必须是能直接发给用户的话，且 4xxx 不得暴露技术细节。
"""
from __future__ import annotations

from typing import Any

from app.models.enums import ResultCode


def _data(body: dict[str, Any] | None) -> dict[str, Any]:
    return (body or {}).get("data") or {}


def render(body: dict[str, Any]) -> str:
    """把 mock 服务的响应信封转成可发给用户的文本。"""
    code = ResultCode(body.get("code", ResultCode.INTERNAL_ERROR))
    data = _data(body)

    if code is ResultCode.OK:
        return _render_ok(body, data)

    if code is ResultCode.DUPLICATE_REQUEST:
        return _render_ok(body, data)

    if code is ResultCode.ORDER_NOT_FOUND:
        return "没有查到这笔订单哦，麻烦您确认一下订单号是否正确。"

    if code is ResultCode.LOGISTICS_NOT_FOUND:
        return "这笔订单还没有发货，暂时查不到物流信息。发货后您就能看到轨迹了。"

    if code is ResultCode.REFUND_NOT_FOUND:
        return "没有查到对应的退款记录。"

    if code is ResultCode.ORDER_STATUS_NOT_ALLOWED:
        reason = body.get("reason") or ""
        if "REFUND" in reason and ("REFUNDED" in reason or "退款" in reason):
            return "这笔订单已经退款完成了，不需要重复申请哦。"
        return "这笔订单当前的状态不支持该操作。"

    if code is ResultCode.AMOUNT_EXCEEDS_ORDER:
        return "退款金额不能超过订单实际金额哦。"

    if code is ResultCode.AMOUNT_INVALID:
        return "退款金额需要是大于 0 的数字，且最多保留两位小数。"

    if code is ResultCode.ADDRESS_INCOMPLETE:
        return "收货地址信息不完整，麻烦补充一下。"

    if code is ResultCode.ADDRESS_NOT_EDITABLE:
        return "订单已经发货了，收货地址不能再修改啦。如有需要可以联系快递拦截。"

    # 4xxx 一律不暴露技术细节
    return "抱歉，系统出了点小状况，请您稍后再试一次。"


def _render_ok(body: dict[str, Any], data: dict[str, Any]) -> str:
    # 按唯一字段判别响应类型，不能用字段存在性猜：
    # 订单本身也有 address 字段，用 "address" in data 会把订单误判成改地址成功
    if "refund_id" in data:
        amount = data.get("amount", "")
        return (
            f"已为您提交退款，金额 {amount} 元，"
            f"预计 1-3 个工作日原路退回到您的支付账户。"
        )
    if "current_node" in data:
        node = data["current_node"]
        tracks = data.get("tracks") or []
        latest = tracks[-1]["node"] if tracks else node
        return f"您的包裹目前状态是：{latest}。"
    if "order_id" in data and "status" in data and "amount" in data:
        status_text = {
            "PENDING_PAYMENT": "待支付",
            "PAID": "已支付",
            "SHIPPED": "已发货",
            "DELIVERED": "已签收",
            "CLOSED": "已关闭",
            "REFUNDING": "退款中",
            "REFUNDED": "已退款",
        }.get(data["status"], data["status"])
        return f"这笔订单当前状态是：{status_text}，金额 {data.get('amount', '')} 元。"
    if "address" in data and "receiver" in data.get("address", {}):
        return "收货地址已为您更新。"
    return "操作已完成。"

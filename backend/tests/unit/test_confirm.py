"""T021: 确认环节。确认词必须精确匹配。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.agent.confirm import (
    build_confirmation,
    is_cancellation,
    is_confirmation,
    is_expired,
)
from app.models.agent import Intent

WORDS = frozenset({"确认", "确定", "是的"})


def test_exact_confirm():
    assert is_confirmation("确认", WORDS)
    assert is_confirmation("  确认  ", WORDS)


def test_containing_but_not_exact_is_not_confirmed():
    """关键：'确认不了' 不能当成确认。"""
    assert not is_confirmation("确认不了", WORDS)
    assert not is_confirmation("我不确认", WORDS)
    assert not is_confirmation("确认一下再说", WORDS)


def test_unrelated_not_confirmed():
    assert not is_confirmation("再说吧", WORDS)


def test_cancel_words():
    assert is_cancellation("算了")
    assert is_cancellation("取消")
    assert not is_cancellation("确认")


def test_build_refund_confirmation():
    p = build_confirmation(
        intent=Intent.REFUND, order_id="ORD1002", amount=Decimal("259.90")
    )
    assert p.action == "refund"
    assert "ORD1002" in p.summary and "259.90" in p.summary


def test_build_address_confirmation():
    p = build_confirmation(
        intent=Intent.CHANGE_ADDRESS,
        order_id="ORD1002",
        amount=None,
        payload={"address": {"province": "浙江省", "city": "宁波市", "district": "鄞州区", "detail": "新地址1号"}},
    )
    assert p.action == "change_address"
    assert "宁波市" in p.summary


def test_expiry():
    p = build_confirmation(
        intent=Intent.REFUND, order_id="ORD1002", amount=Decimal("1"), ttl_seconds=60
    )
    assert not is_expired(p)
    assert is_expired(p, now=datetime.now(timezone.utc) + timedelta(seconds=120))

"""确认卡：用户只能点按钮确认/取消，自由文本不再触发写操作。"""

from __future__ import annotations

from tests.conftest_app import *  # noqa: F401,F403


def _open_refund_card(user) -> dict:
    user.send({"type": "user.message", "payload": {"text": "我要退款"}})
    user.recv_until("assistant.done")
    for _ in range(20):
        f = user.recv()
        if f["type"] == "confirm.card":
            return f["payload"]
    raise AssertionError("没有收到确认卡")


def _status(http) -> str:
    return http.get("/orders/ORD1002").json()["data"]["status"]


def test_confirm_card_carries_button_payload(ws_user):
    user = ws_user(session_id="cc0", user_id="u1", order_id="ORD1002")
    user.recv()  # hello
    card = _open_refund_card(user)
    assert card["card_id"]
    assert card["action"] == "refund"
    assert card["order_id"] == "ORD1002"
    assert card["amount"] == "259.90"
    assert "确认" in card["summary"]


def test_text_confirm_does_not_execute(http, ws_user):
    """用户打"确认"不再执行退款，只提示点按钮。"""
    user = ws_user(session_id="cc1", user_id="u1", order_id="ORD1002")
    user.recv()
    _open_refund_card(user)

    user.send({"type": "user.message", "payload": {"text": "确认"}})
    done = user.recv_until("assistant.done")
    assert "按钮" in done["payload"]["text"]
    assert _status(http) == "PAID", "文本确认不得触发退款"


def test_button_confirm_executes(http, ws_user):
    user = ws_user(session_id="cc2", user_id="u1", order_id="ORD1002")
    user.recv()
    card = _open_refund_card(user)

    user.send(
        {"type": "user.confirm", "payload": {"card_id": card["card_id"], "decision": "confirm"}}
    )
    done = user.recv_until("assistant.done")
    assert "已为您提交退款" in done["payload"]["text"]
    assert _status(http) in ("REFUNDING", "REFUNDED")


def test_wrong_card_id_is_rejected(http, ws_user):
    user = ws_user(session_id="cc3", user_id="u1", order_id="ORD1002")
    user.recv()
    _open_refund_card(user)

    user.send({"type": "user.confirm", "payload": {"card_id": "bogus", "decision": "confirm"}})
    done = user.recv_until("assistant.done")
    assert done["payload"]["state"] == "stale_card"
    assert _status(http) == "PAID"


def test_cancel_button_does_not_execute(http, ws_user):
    user = ws_user(session_id="cc4", user_id="u1", order_id="ORD1002")
    user.recv()
    card = _open_refund_card(user)

    user.send(
        {"type": "user.confirm", "payload": {"card_id": card["card_id"], "decision": "cancel"}}
    )
    done = user.recv_until("assistant.done")
    assert "已为您取消" in done["payload"]["text"]
    assert _status(http) == "PAID"

"""T036 [US3]: 改地址契约测试。"""
from app.models.enums import OrderStatus, ResultCode

ORIGINAL = {
    "receiver": "李四",
    "phone": "13900139000",
    "province": "浙江省",
    "city": "杭州市",
    "district": "西湖区",
    "detail": "文三路2号",
}

NEW = {
    "receiver": "李四改",
    "phone": "13900139001",
    "province": "浙江省",
    "city": "宁波市",
    "district": "鄞州区",
    "detail": "新地址99号",
}


async def test_update_address_on_paid_order(client):
    body = (await client.put("/orders/ORD1002/address", json=NEW)).json()
    assert body["code"] == ResultCode.OK
    assert body["data"]["address"]["city"] == "宁波市"

    after = (await client.get("/orders/ORD1002")).json()
    assert after["data"]["address"]["detail"] == "新地址99号"


async def test_update_address_on_pending_payment_order(client):
    body = (await client.put("/orders/ORD1001/address", json=NEW)).json()
    assert body["code"] == ResultCode.OK


async def test_shipped_order_cannot_change_address(client):
    body = (await client.put("/orders/ORD1003/address", json=NEW)).json()
    assert body["code"] == ResultCode.ADDRESS_NOT_EDITABLE
    assert body["reason"]

    after = (await client.get("/orders/ORD1003")).json()
    assert after["data"]["address"]["receiver"] == "王五", "拒绝后原地址不得变化"


async def test_same_address_is_idempotent_success(client):
    """已确认决策 3：改为完全相同的内容视为成功，不产生写操作。"""
    body = (await client.put("/orders/ORD1002/address", json=ORIGINAL)).json()
    assert body["code"] == ResultCode.OK
    assert body["data"]["updated_at"] is not None


async def test_incomplete_address_rejected_by_schema(client):
    bad = dict(NEW)
    del bad["receiver"]
    r = await client.put("/orders/ORD1002/address", json=bad)
    assert r.status_code == 422


async def test_empty_field_rejected_by_schema(client):
    bad = dict(NEW)
    bad["detail"] = ""
    r = await client.put("/orders/ORD1002/address", json=bad)
    assert r.status_code == 422


async def test_invalid_phone_rejected_by_schema(client):
    bad = dict(NEW)
    bad["phone"] = "abc"
    r = await client.put("/orders/ORD1002/address", json=bad)
    assert r.status_code == 422


async def test_update_address_unknown_order(client):
    body = (await client.put("/orders/NOPE/address", json=NEW)).json()
    assert body["code"] == ResultCode.ORDER_NOT_FOUND


async def test_closed_order_cannot_change_address(client):
    body = (await client.put("/orders/ORD1005/address", json=NEW)).json()
    assert body["code"] == ResultCode.ADDRESS_NOT_EDITABLE

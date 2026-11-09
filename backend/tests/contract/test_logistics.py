"""T022 [US1]: 物流查询契约测试。

覆盖 US1 场景 3、4：
- 已发货 → 返回节点与轨迹
- 未发货 → 2002（暂无物流信息），与"查询失败"严格区分
"""
from app.models.enums import ResultCode


async def test_shipped_order_has_logistics(client):
    body = (await client.get("/orders/ORD1003/logistics")).json()
    assert body["code"] == ResultCode.OK
    assert body["data"]["order_id"] == "ORD1003"
    assert body["data"]["current_node"] == "已到达南京转运中心"
    assert len(body["data"]["tracks"]) == 3


async def test_not_shipped_returns_logistics_not_found_not_error(client):
    """未发货不是错误，是"暂无物流信息"，必须落在 2002。"""
    body = (await client.get("/orders/ORD1002/logistics")).json()
    assert body["code"] == ResultCode.LOGISTICS_NOT_FOUND
    assert 2000 <= body["code"] < 3000


async def test_missing_logistics_does_not_masquerade_as_failure(client):
    """物流缺失与订单不存在必须给出不同的码，便于调用方区分处理。"""
    no_logistics = (await client.get("/orders/ORD1001/logistics")).json()
    no_order = (await client.get("/orders/NOPE/logistics")).json()
    assert no_logistics["code"] == ResultCode.LOGISTICS_NOT_FOUND
    assert no_order["code"] == ResultCode.ORDER_NOT_FOUND
    assert no_logistics["code"] != no_order["code"]


async def test_delivered_order_shows_signed_node(client):
    body = (await client.get("/orders/ORD1004/logistics")).json()
    assert body["code"] == ResultCode.OK
    assert body["data"]["current_node"] == "已签收"


async def test_logistics_for_unknown_order(client):
    body = (await client.get("/orders/UNKNOWN_ORDER/logistics")).json()
    assert body["code"] == ResultCode.ORDER_NOT_FOUND


async def test_logistics_reflects_seeded_data_not_order_status_guess(client):
    """REFUNDING 的订单仍有物流数据，必须如实返回，不能因为状态是退款中就返回空。"""
    body = (await client.get("/orders/ORD1006/logistics")).json()
    assert body["code"] == ResultCode.OK
    assert body["data"]["current_node"] == "已到达武汉转运中心"

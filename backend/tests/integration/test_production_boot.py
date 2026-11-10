"""T013: 生产启动冒烟（FR-031）。

行为之前：_loop / _coordinator 只在测试夹具里 configure，
main:app 起来后 /agent/chat 直接 RuntimeError。这里锁死"能起来且能应答"。
"""
from __future__ import annotations

from tests.conftest_app import *  # noqa: F401,F403


def test_health(http):
    assert http.get("/health").status_code == 200


def test_llm_health_available_in_production(http):
    r = http.get("/agent/health/llm")
    assert r.status_code == 200
    assert "failure_rate" in r.json()


def test_handoff_queue_available_in_production(http):
    r = http.get("/handoff/queue")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_agent_chat_works_out_of_the_box(http):
    r = http.post(
        "/agent/chat",
        json={
            "session_id": "boot-1",
            "user_id": "u1",
            "message": "订单状态",
            "order_id": "ORD1002",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply"]


def test_root_404_without_static_dir(http):
    """未构建前端时 / 不该 500，而是 404（FR-022）。"""
    assert http.get("/").status_code == 404


def test_stub_table_is_preloaded_for_demo():
    """Q7：未登记句子会走澄清，演示与 E2E 依赖这张表。"""
    from app.config import settings

    assert "我要退款" in settings.demo_stub_responses
    assert "我要投诉" in settings.demo_stub_responses

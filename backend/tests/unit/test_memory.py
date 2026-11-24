"""记忆：事实抽取、合并、窗口裁剪。"""
from __future__ import annotations

from app.agent import memory


def test_extracts_order_amount_time():
    f = memory.extract_facts("帮我退 ORD1003,上周买的保温杯,退 300 块")
    assert f["order_id"] == "ORD1003"
    assert f["amount"] == "300"
    assert f["time_ref"] == "上周"


def test_extracts_nothing_when_no_fact():
    assert memory.extract_facts("你们这个是干嘛用的") == {}


def test_merge_new_order_keeps_previous():
    merged = memory.merge_facts({"order_id": "ORD1002"}, {"order_id": "ORD1003"})
    assert merged["order_id"] == "ORD1003"
    assert merged["prev_order_id"] == "ORD1002"


def test_merge_same_order_does_not_set_previous():
    merged = memory.merge_facts({"order_id": "ORD1002"}, {"order_id": "ORD1002"})
    assert "prev_order_id" not in merged


def test_merge_ignores_empty_values():
    merged = memory.merge_facts({"amount": "300"}, {"amount": ""})
    assert merged["amount"] == "300"


def test_merge_respects_limit():
    facts = {k: k for k in "abcdefghij"}
    merged = memory.merge_facts(facts, {}, limit=5)
    assert len(merged) == 5


def test_low_info_message_does_not_take_a_slot():
    turns = memory.append_turn([], "user", "嗯")
    assert turns == []


def test_turns_trimmed_by_count():
    turns = []
    for i in range(10):
        turns = memory.append_turn(turns, "user", f"第{i}条消息", max_turns=4, max_chars=10000)
    assert len(turns) == 4
    assert turns[-1].text == "第9条消息"
    assert turns[0].text == "第6条消息"


def test_turns_trimmed_by_chars():
    turns = []
    for i in range(5):
        turns = memory.append_turn(turns, "user", "字" * 100, max_turns=10, max_chars=250)
    assert sum(len(t.text) for t in turns) <= 250


def test_facts_block_is_human_readable():
    text = memory.facts_block({"order_id": "ORD1002", "issue": "refund", "amount": "300"})
    assert "订单号：ORD1002" in text
    assert "诉求：退款" in text
    assert "金额：300" in text


def test_facts_block_empty():
    assert memory.facts_block({}) == ""
    assert memory.facts_block(None) == ""


def test_history_messages_keeps_roles():
    turns = memory.append_turn([], "user", "我要退款", max_turns=6, max_chars=1000)
    turns = memory.append_turn(turns, "assistant", "请确认", max_turns=6, max_chars=1000)
    msgs = memory.history_messages(turns)
    assert msgs == [
        {"role": "user", "content": "我要退款"},
        {"role": "assistant", "content": "请确认"},
    ]


def test_extracts_order_id_adjacent_to_chinese():
    # 中文和字母之间没空格时，\b 会漏匹配；事实抽取必须能拿到
    assert memory.extract_facts("帮我退ORD1002")["order_id"] == "ORD1002"
    assert memory.extract_facts("ORD1002的退款")["order_id"] == "ORD1002"

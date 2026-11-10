"""T015: 语义块切分。

宪法 V：不能伪造流式。LLM 不支持 stream 时，把完整回复按句切开逐块推，
这是"分块推送"，必须标 streaming=False，且不得 sleep 固定时长制造节奏。
"""
from __future__ import annotations

from app.chat.streamer import split_chunks


def test_splits_on_sentence_endings():
    text = "第一句。第二句！第三句？"
    assert split_chunks(text) == ["第一句。", "第二句！", "第三句？"]


def test_preserves_content_when_joined():
    text = "已为您提交退款，金额 259.90 元，预计 1-3 个工作日原路退回。请留意账户。"
    assert "".join(split_chunks(text)) == text


def test_splits_on_newline():
    assert split_chunks("第一行\n第二行") == ["第一行\n", "第二行"]


def test_long_sentence_split_by_length():
    long = "啊" * 150
    chunks = split_chunks(long)
    assert len(chunks) > 1
    assert all(len(c) <= 60 for c in chunks)
    assert "".join(chunks) == long


def test_empty_text_yields_no_chunks():
    assert split_chunks("") == []


def test_text_without_punctuation_is_single_chunk():
    assert split_chunks("没有标点的短句") == ["没有标点的短句"]

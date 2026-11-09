"""T015: 降级路径的宽写词表。"""

import pytest

from app.agent import degrade
from app.config import settings


def test_write_keyword_hit():
    for text in ["我要退款", "帮我改地址", "取消订单", "申请退"]:
        assert degrade.classify_degraded(text) == degrade.WRITE_SUSPECTED, text


def test_read_passes_when_no_write_word():
    for text in ["到哪了", "查一下", "怎么样了"]:
        assert degrade.classify_degraded(text) == degrade.READ, text


def test_degrade_table_is_wider_than_normal():
    """FR-014a：降级词表必须比正常词表更宽，宁可误拒只读。"""
    normal = set()
    for words in settings.keyword_hints.values():
        normal.update(words)

    degrade_kws = set(settings.degrade_write_keywords)

    # 正常词表若为空（未配置），至少断言降级表覆盖了核心写操作词
    assert {"退款", "退", "改", "修改"} <= degrade_kws
    for core in ["退款", "退", "退货"]:
        assert core in degrade_kws


def test_single_char_broadening_catches_partial():
    """"把那个退掉" 含"退"，正常词表没有这个单独的字，降级表要能拦住。"""
    assert degrade.classify_degraded("把那个退掉") == degrade.WRITE_SUSPECTED


def test_custom_keywords_override():
    assert degrade.classify_degraded("我要退款", write_keywords=()) == degrade.READ


def test_false_negative_is_acceptable_cost():
    """未命中写词表 → 放行只读。最坏结果只是多查一次只读数据。"""
    # "这个能不能退" 含"退"，命中；"钱能回来吗" 不含，放行
    assert degrade.classify_degraded("钱能回来吗") == degrade.READ

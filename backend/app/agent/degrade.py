"""T017: 降级路径。

LLM 不可用时，只能用关键词判断"读写"，且用的是**比正常词表更宽**的写词表：
宁可误拒一个只读请求，不可漏放一个写操作。

"未命中"不等于"确认是读操作"，只是"没有理由认为是写操作"。
因此误判后果被限制为多/少一次只读查询，不会放大到资金操作。
"""
from __future__ import annotations

from app.config import settings

READ = "read"
WRITE_SUSPECTED = "write_suspected"


def classify_degraded(
    user_input: str, write_keywords: tuple[str, ...] | None = None
) -> str:
    kws = write_keywords if write_keywords is not None else settings.degrade_write_keywords
    for kw in kws:
        if kw in user_input:
            return WRITE_SUSPECTED
    return READ


DEGRADED_WRITE_REPLY = (
    "抱歉，系统暂时无法处理这类操作，请稍后重试。"
    "如果问题较急，可以联系人工客服协助处理。"
)


def degraded_read_reply(has_order: bool) -> str:
    if has_order:
        return "抱歉，系统暂时无法完整理解您的问题，只能为您查询已绑定订单的信息。"
    return "抱歉，系统暂时无法处理您的请求，请稍后重试或联系人工客服。"

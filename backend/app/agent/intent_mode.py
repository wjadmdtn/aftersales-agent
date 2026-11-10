"""mode 的兜底校验。

LLM 判 mode 可能出错——「我要退款」被判成 INQUIRY，或者反过来。
用规则做**保守方向**的纠偏：

- 判了 EXECUTE 但含疑问词 → 降级为 INQUIRY（宁可多问一句，不可误执行）
- 判了 INQUIRY 但含强祈使 → 升为 EXECUTE（用户语气明确，不该反问）

反方向不纠偏：判 INQUIRY 时没有强祈使词就保留，因为"多问一句"代价小。
"""
from __future__ import annotations

from app.models.agent import (
    EXECUTE_MARKERS,
    INQUIRY_MARKERS,
    Intent,
    IntentMode,
)

WRITE_INTENTS = {Intent.REFUND, Intent.CHANGE_ADDRESS}


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(m in text for m in markers)


def normalize_mode(intent: Intent, mode: IntentMode, text: str) -> tuple[IntentMode, str | None]:
    """返回 (纠正后的 mode, 纠正说明)。"""
    # 写操作的 EXECUTE 最危险：含疑问词就降级
    if mode is IntentMode.EXECUTE and intent in WRITE_INTENTS:
        if _has_any(text, INQUIRY_MARKERS):
            return IntentMode.INQUIRY, "含疑问词，判定为咨询而非执行"

    if mode is IntentMode.INQUIRY and _has_any(text, EXECUTE_MARKERS):
        return IntentMode.EXECUTE, "含明确祈使词，判定为执行"

    return mode, None


def requires_order_context(intent: Intent, mode: IntentMode, has_order: bool) -> bool:
    """意图明确但要动业务，却缺少订单上下文。"""
    if mode is IntentMode.EXECUTE and intent in WRITE_INTENTS and not has_order:
        return True
    return False

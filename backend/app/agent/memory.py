"""会话记忆：最近若干轮原文 + 从对话里抽取的已知事实。

两件事必须分开：

- **原文窗口**（recent_turns）只负责让模型看懂"这个""那单""刚才那个"指什么。
  它会被裁剪，且不允许作为意图判定依据——历史里那句"我要退款"可能只是抱怨。
- **事实**（facts）负责跨轮不丢：订单号、金额、时间、诉求变化。
  窗口裁掉了，事实还在。

抽取走规则不走模型：为了记住一个订单号每轮多调一次模型（内网单次 20~40 秒）
不划算，而订单号 / 金额 / 相对时间恰好都是正则能覆盖的。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.agent import Turn

# 订单号形如 ORD1002：大写字母开头 + 数字。
# 不能用 \b：中文和字母都算 \w，"帮我退ORD1002" 里 O 前没有词边界，会整条漏匹配。
_ORDER_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,}\d{3,}(?![A-Za-z0-9])")
# 金额：300 / 300.5 / 300块 / 300 元
_AMOUNT = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块)")
# 相对时间：只收录能稳定命中的说法，不猜具体日期
_TIME_WORDS = (
    "今天", "昨天", "前天", "上周", "上周末", "本周", "这周",
    "上个月", "上月", "本月", "这个月", "月初", "月底",
    "三天前", "一周前", "半个月前", "好久之前",
)
# 纯确认 / 应答，占窗口没有信息量
_LOW_INFO = frozenset({"好", "好的", "嗯", "嗯嗯", "行", "可以", "确认", "确定", "是的", "对", "ok", "OK"})

FACT_ORDER_ID = "order_id"
FACT_AMOUNT = "amount"
FACT_TIME_REF = "time_ref"
FACT_ISSUE = "issue"
FACT_PRODUCT = "product"
FACT_PREV_ORDER_ID = "prev_order_id"

_FACT_LABEL = {
    FACT_ORDER_ID: "订单号",
    FACT_AMOUNT: "金额",
    FACT_TIME_REF: "时间",
    FACT_ISSUE: "诉求",
    FACT_PRODUCT: "商品",
}

_ISSUE_LABEL = {
    "refund": "退款",
    "change_address": "修改地址",
    "logistics": "查物流",
    "query_order": "查订单",
    "exchange": "换货",
    "policy": "政策咨询",
    "manual": "要求人工",
}


def extract_facts(message: str, prev: dict[str, str] | None = None) -> dict[str, str]:
    """从一条用户消息里抽可复用事实。抽不到就返回空，不猜。"""
    found: dict[str, str] = {}

    m = _ORDER_ID.search(message)
    if m:
        found[FACT_ORDER_ID] = m.group(0)

    m = _AMOUNT.search(message)
    if m:
        found[FACT_AMOUNT] = m.group(1)

    for w in _TIME_WORDS:
        if w in message:
            found[FACT_TIME_REF] = w
            break

    return found


def merge_facts(old: dict[str, str], new: dict[str, str], *, limit: int = 8) -> dict[str, str]:
    """新值覆盖旧值。订单号换了保留上一个，便于追溯上下文切换。"""
    merged = dict(old or {})
    for k, v in (new or {}).items():
        if not v:
            continue
        if k == FACT_ORDER_ID and merged.get(FACT_ORDER_ID) and merged[FACT_ORDER_ID] != v:
            merged[FACT_PREV_ORDER_ID] = merged[FACT_ORDER_ID]
        merged[k] = v

    if len(merged) > limit:
        # 超限时先丢追溯类字段，再按插入顺序丢最早的
        for k in (FACT_PREV_ORDER_ID,):
            merged.pop(k, None)
        while len(merged) > limit:
            merged.pop(next(iter(merged)))
    return merged


def is_low_info(text: str) -> bool:
    t = (text or "").strip()
    return len(t) <= 3 and t in _LOW_INFO


def append_turn(
    turns: list[Turn],
    role: str,
    text: str,
    *,
    max_turns: int = 6,
    max_chars: int = 1500,
) -> list[Turn]:
    """追加一轮并裁剪：先按条数、再按总字符数，超了从最旧的丢。"""
    t = (text or "").strip()
    if not t or (role == "user" and is_low_info(t)):
        return list(turns)

    out = list(turns) + [Turn(role=role, text=t, at=datetime.now(timezone.utc))]

    if max_turns > 0:
        out = out[-max_turns:]

    if max_chars > 0:
        while len(out) > 1 and sum(len(x.text) for x in out) > max_chars:
            out.pop(0)

    return out


def facts_block(facts: dict[str, str] | None) -> str:
    """渲染成给模型看的已知事实段。空则返回空串。"""
    if not facts:
        return ""
    parts = []
    for k, label in _FACT_LABEL.items():
        v = facts.get(k)
        if not v:
            continue
        if k == FACT_ISSUE:
            v = _ISSUE_LABEL.get(v, v)
        parts.append(f"{label}：{v}")
    return "；".join(parts)


def history_messages(turns: list[Turn] | None) -> list[dict[str, str]]:
    """原文窗口转成 messages 数组里的历史条目。"""
    return [{"role": t.role, "content": t.text} for t in (turns or [])]

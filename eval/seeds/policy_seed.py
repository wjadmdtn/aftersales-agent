"""政策咨询用例 30 条。

关键区分：
  问"规则是什么" → policy（本文件）
  问"我这一单怎样" → 业务意图（intent_seed.py）

用户提到具体订单号时多半是业务意图，因此这里故意不写订单号。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.eval.schema import EvalCase, EvalPath  # noqa: E402

# (id 后缀, 用户输入, 期望命中的条款 id, 订单 id)
# 订单决定政策检索的品类过滤：条款只对特定品类生效，
# 用服饰订单测"保修"必然查不到——那不是检索失败，是商品确实不适用。
# 因此按条款适用品类配对：ORD1003=electronics / ORD1004=fresh / ORD1002=apparel
DEFAULT_ORDER = "ORD1002"
POLICY_QUESTIONS = [
    # 退款规则
    ("refund-time", "退款多久能到账", "REFUND-COMMON-01", DEFAULT_ORDER),
    ("refund-amount", "退款金额怎么算", "REFUND-COMMON-02", DEFAULT_ORDER),
    ("refund-freight", "退货运费退不退", "REFUND-COMMON-03", DEFAULT_ORDER),
    ("refund-timeout", "商家一直不处理退款怎么办", "REFUND-COMMON-04", DEFAULT_ORDER),
    # 七天无理由
    ("sevenday-scope", "七天无理由适用于哪些商品", "RETURN-SEVENDAY-01", DEFAULT_ORDER),
    ("sevenday-start", "七天无理由从哪天开始算", "RETURN-SEVENDAY-02", DEFAULT_ORDER),
    ("sevenday-expire", "超过七天还能退吗", "RETURN-SEVENDAY-02", DEFAULT_ORDER),
    ("sevenday-freight", "七天无理由退货运费谁出", "RETURN-SEVENDAY-03", DEFAULT_ORDER),
    ("sevenday-gift", "赠品要一起退吗", "RETURN-SEVENDAY-03", DEFAULT_ORDER),
    ("good-standard", "什么样算商品完好", "RETURN-GOOD-STANDARD-01", DEFAULT_ORDER),
    ("no-sevenday", "哪些商品不支持七天无理由", "RETURN-EXCEPTION-01", "ORD1004"),
    # 换货与维修
    ("exchange", "换货需要什么条件", "AFTERSALE-EXCHANGE-01", DEFAULT_ORDER),
    # 处理时效：条款库没写"换货多久完成"，正确答案是没有答案 → 转人工
    ("exchange-time", "换货多久能完成", None, DEFAULT_ORDER),
    ("repair", "保修怎么申请", "AFTERSALE-REPAIR-01", "ORD1003"),
    # 发票与凭证
    ("invoice", "能开发票吗", "INVOICE-01", DEFAULT_ORDER),
    ("invoice-change", "发票开错了能重开吗", "INVOICE-01", DEFAULT_ORDER),
    ("voucher", "售后需要提供什么凭证", "INVOICE-02", DEFAULT_ORDER),
    # 物流相关
    ("logistics-delay", "超时没发货有赔偿吗", "LOGISTICS-01", DEFAULT_ORDER),
    ("logistics-fake", "显示签收但没收到怎么办", "LOGISTICS-02", DEFAULT_ORDER),
    ("logistics-damage", "运输中破损了怎么处理", "LOGISTICS-03", DEFAULT_ORDER),
    # 订单修改
    ("address-rule", "还没发货可以改地址吗", "ORDER-ADDRESS-01", DEFAULT_ORDER),
    ("spec-change", "能换颜色换尺码吗", "ORDER-ADDRESS-02", DEFAULT_ORDER),
    # 预售
    ("presale-deposit", "预售定金能退吗", "PRESALE-01", DEFAULT_ORDER),
    ("presale-ship", "预售什么时候发货", "PRESALE-02", DEFAULT_ORDER),
    # 价保
    ("price-protect", "买完降价了能补差价吗", "PRICE-PROTECT-01", DEFAULT_ORDER),
    ("price-time", "价保申请有时限吗", "PRICE-PROTECT-02", DEFAULT_ORDER),
    # 跨境与企业
    ("crossborder", "跨境订单能退吗", "CROSSBORDER-01", DEFAULT_ORDER),
    ("b2b", "企业采购的售后和普通订单一样吗", "B2B-01", DEFAULT_ORDER),
    # 以旧换新：条款只对电子产品生效
    ("tradein-value", "以旧换新旧机怎么估价", "TRADE-IN-01", "ORD1003"),
    ("tradein-after", "以旧换新的新机售后怎么算", "TRADE-IN-02", "ORD1003"),
]

# 不应走政策的（是业务动作或澄清），与上面成对出现，防止模型过度路由
NEGATIVE = [
    ("neg-refund", "我要退款", "refund"),
    ("neg-logistics", "我的快递到哪了", "logistics"),
    ("neg-address", "帮我改一下地址", "change_address"),
    ("neg-order", "查一下我的订单", "query_order"),
    ("neg-vague", "这个东西不好用", "unknown"),
]


def _policy_case(cid: str, text: str, clause: str | None, order_id: str = DEFAULT_ORDER) -> EvalCase:
    """clause=None 表示知识库没有能回答的条款，期望转人工。"""
    expect: dict = {"intent": "policy"}
    if clause:
        expect["cited_clause"] = clause
        note = f"政策咨询，期望命中 {clause}"
    else:
        expect["state"] = "handoff_queued"
        note = "知识库无对应条款，期望转人工（不得编造）"
    return EvalCase(
        id=f"policy-{cid}",
        category="policy",
        path=EvalPath.POLICY,
        input=text,
        context={"order_id": order_id},
        expect=expect,
        notes=note,
    )


def _negative_case(cid: str, text: str, intent: str) -> EvalCase:
    return EvalCase(
        id=cid,
        category="not_policy",
        path=EvalPath.BUSINESS_ACTION,
        input=text,
        context={"order_id": "ORD1002"},
        expect={"intent": intent},
        notes="业务/澄清意图，不应路由到政策检索",
    )


def build() -> list[EvalCase]:
    return [_policy_case(*row) for row in POLICY_QUESTIONS] + [
        _negative_case(*row) for row in NEGATIVE
    ]


if __name__ == "__main__":
    cs = build()
    print(f"{len(cs)} 条：政策 {len(POLICY_QUESTIONS)} / 反例 {len(NEGATIVE)}")

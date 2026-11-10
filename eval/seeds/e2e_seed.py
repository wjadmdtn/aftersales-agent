"""端到端用例 25 条：从一句输入驱动完整链路，校验最终业务状态。

不看回复文本，看订单状态和退款笔数——回复可以说谎，状态不会。
"""
from __future__ import annotations

from eval.seeds.common import e2e

# (id, turns, order_status, refund_count, note)
CASES = [
    ("e2e-rf-ok-00", ["我要退款", "确认"], "REFUNDED", 1, "已支付订单，全额退款"),
    ("e2e-rf-ok-01", ["申请退款", "确认"], "REFUNDED", 1, "同义表达走通"),
    ("e2e-rf-ok-02", ["退了", "确定"], "REFUNDED", 1, "口语化表达"),
    ("e2e-rf-cancel-00", ["我要退款", "算了"], "PAID", 0, "取消后不得退款"),
    ("e2e-rf-cancel-01", ["我要退款", "不用了"], "PAID", 0, "取消同义词"),
    ("e2e-rf-unclear-00", ["我要退款", "再说吧"], "PAID", 0, "无关回复不算确认"),
    ("e2e-rf-noconfirm-00", ["我要退款", "确认不了"], "PAID", 0, "含确认字但不是确认"),
    ("e2e-rf-refunded-00", ["我要退款", "确认"], "REFUNDED", 1, "已退款订单再次申请"),
    ("e2e-lg-ok-00", ["快递到哪了"], "SHIPPED", None, "查物流不改状态"),
    ("e2e-lg-ok-01", ["物流信息呢"], "SHIPPED", None, ""),
    ("e2e-od-ok-00", ["查一下我的订单"], "PAID", None, "查订单不改状态"),
    ("e2e-od-ok-01", ["订单状态是什么"], "PAID", None, ""),
    ("e2e-ad-ok-00", ["我要改地址"], "PAID", None, "改地址不改订单状态"),
    ("e2e-ad-shipped-00", ["我要改地址", "确认"], "SHIPPED", None, "已发货不可改，状态不变"),
    ("e2e-clarify-00", ["这个东西不好用"], "PAID", None, "澄清不改状态"),
    ("e2e-clarify-01", ["嗯"], "PAID", None, ""),
    ("e2e-handoff-00", ["转人工"], "PAID", None, "转人工不改状态"),
    ("e2e-handoff-01", ["我要投诉你们"], "PAID", None, "投诉不改状态"),
    ("e2e-mix-00", ["快递到哪了", "那退了吧"], "REFUNDED", 1, "先查后决策"),
    ("e2e-mix-01", ["我要退款", "算了，直接改地址吧"], "PAID", 0, "中途改主意"),
    ("e2e-limit-00", ["随便说说", "还是不懂", "嗯"], "PAID", None, "三轮后转人工"),
    ("e2e-neg-00", ["东西坏了"], "PAID", None, "负面情绪澄清"),
    ("e2e-neg-01", ["质量太差了", "我要退款", "确认"], "REFUNDED", 1, "情绪→诉求→执行"),
    ("e2e-req-00", ["我要人工", "算了还是查订单吧"], "PAID", None, "转人工请求可撤回"),
    ("e2e-req-01", ["客服电话是多少"], "PAID", None, ""),
]


def build() -> list:
    out = []
    for cid, turns, status, count, note in CASES:
        order_id = "ORD1003" if "shipped" in cid or "lg-" in cid else "ORD1002"
        case = e2e(cid, turns=turns, order_status=status, refund_count=count, notes=note or None)
        case.context["order_id"] = order_id
        out.append(case)
    return out


if __name__ == "__main__":
    print(f"{len(build())} 条")

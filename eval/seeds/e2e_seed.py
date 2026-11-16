"""端到端用例：多轮跑完整业务链路，校验最终状态 + 关键交互。

两件事必须同时成立，否则用户价值没落地：
1. **业务结果**：库里订单状态真的变了（或真的没变）
2. **交互约束**：写操作走过二次确认、投诉不转人工、答不了转人工

只看最终状态测不出"AI 直接退款没让用户确认"——那也能让订单变 REFUNDED。

订单说明（seed/orders.json）：
  ORD1001 PENDING_PAYMENT apparel     未支付
  ORD1002 PAID            apparel     已支付未发货
  ORD1003 SHIPPED         electronics 已发货
  ORD1004 DELIVERED       fresh       已签收
  ORD1006 REFUNDING       electronics 退款中
  ORD1007 REFUNDED        apparel     已退款
"""
from __future__ import annotations

from eval.seeds.common import e2e


def build() -> list:
    cases: list = []

    def add(cid, turns, status, count=None, order="ORD1002", note=None, **kw):
        cases.append(
            e2e(cid, turns=turns, order_status=status, refund_count=count,
                order_id=order, notes=note, **kw)
        )

    # ---------- A. 退款：完整确认流程（8 条）----------
    add("e2e-rf-ok-00", ["我要退款", "确认"], "REFUNDED", 1,
        note="标准退款：先弹确认，确认后执行",
        first_turn_state="awaiting_confirmation", last_turn_state="executed")
    add("e2e-rf-ok-01", ["申请退款", "确认"], "REFUNDED", 1, note="同义表达")
    add("e2e-rf-ok-02", ["退了", "确定"], "REFUNDED", 1, note="口语化表达")
    add("e2e-rf-ok-03", ["这个我不想要了，帮我退了", "好的"], "REFUNDED", 1, note="长句+确认同义词")
    add("e2e-rf-ok-04", ["我要退款", "是的"], "REFUNDED", 1, note="确认词「是的」")
    add("e2e-rf-ok-05", ["我要退款", "同意"], "REFUNDED", 1, note="确认词「同意」")
    add("e2e-rf-card-00", ["我要退款", "确认"], "REFUNDED", 1,
        note="确认话术必须包含订单号与金额（商品卡片信息）",
        must_say=["ORD1002", "259.90"], must_call=["refund.create"])
    add("e2e-rf-card-01", ["我要退款", "确认"], "REFUNDED", 1,
        note="确认前不得先宣布已执行",
        first_reply_must_not_say=["已为您提交退款"], first_turn_state="awaiting_confirmation")

    # ---------- B. 退款：取消 / 改主意（5 条）----------
    add("e2e-rf-cancel-00", ["我要退款", "算了"], "PAID", 0, note="取消后不得退款")
    add("e2e-rf-cancel-01", ["我要退款", "不用了"], "PAID", 0, note="取消同义词")
    add("e2e-rf-cancel-02", ["我要退款", "先不退了"], "PAID", 0, note="「先不」是取消")
    add("e2e-rf-cancel-03", ["我要退款", "不要了"], "PAID", 0, note="「不要了」是取消")
    add("e2e-rf-cancel-04", ["我要退款", "再想想"], "PAID", 0, note="犹豫不算确认")

    # ---------- C. 退款：模糊确认不算（3 条）----------
    add("e2e-rf-unclear-00", ["我要退款", "再说吧"], "PAID", 0, note="无关回复不算确认")
    add("e2e-rf-noconfirm-00", ["我要退款", "确认不了"], "PAID", 0, note="含确认字但不是确认")
    add("e2e-rf-noconfirm-01", ["我要退款", "好什么好"], "PAID", 0, note="「好」在否定语境中")

    # ---------- D. 退款：异常订单（4 条）----------
    add("e2e-rf-refunded-00", ["我要退款", "确认"], "REFUNDED", 1, order="ORD1007",
        note="已退款订单再次申请，不得重复退", must_not_say=["已为您提交退款"])
    add("e2e-rf-refunding-00", ["我要退款", "确认"], "REFUNDING", None, order="ORD1006",
        note="退款中的订单不得再发起")
    add("e2e-rf-unpaid-00", ["我要退款", "确认"], "PENDING_PAYMENT", None, order="ORD1001",
        note="未支付订单不涉及退款")
    add("e2e-rf-closed-00", ["我要退款", "确认"], "CLOSED", None, order="ORD1005",
        note="已关闭订单不得退款")

    # ---------- E. 查物流 / 查订单：不改状态（5 条）----------
    add("e2e-lg-ok-00", ["快递到哪了"], "SHIPPED", order="ORD1003", note="查物流不改状态")
    add("e2e-lg-ok-01", ["物流信息呢"], "SHIPPED", order="ORD1003")
    add("e2e-lg-ok-02", ["我的包裹在哪"], "SHIPPED", order="ORD1003")
    add("e2e-od-ok-00", ["查一下我的订单"], "PAID", note="查订单不改状态")
    add("e2e-od-ok-01", ["订单状态是什么"], "PAID")

    # ---------- F. 改地址（6 条）----------
    add("e2e-ad-ok-00", ["我要改地址"], "PAID",
        note="没说新地址：先追问，不弹空地址确认",
        first_turn_state="need_address", never_state="awaiting_confirmation",
        must_say=["收货人"])
    add("e2e-ad-ok-01", ["我要改地址", "改为北京市朝阳区建国路4号 13800138000"], "PAID",
        note="给地址后弹确认，确认前不得写入",
        first_turn_state="need_address", last_turn_state="awaiting_confirmation")
    add("e2e-ad-shipped-00", ["我要改地址", "改为北京市朝阳区建国路4号 13800138000"], "SHIPPED", order="ORD1003",
        note="已发货不可改，状态不变，也不得执行修改",
        never_state="executed")
    add("e2e-ad-shipped-01", ["收货地址填错了", "改为北京市朝阳区建国路4号 13800138000"], "SHIPPED", order="ORD1003",
        note="已发货，换种说法同样被拒")
    add("e2e-ad-delivered-00", ["我要改地址", "改为北京市朝阳区建国路4号 13800138000"], "DELIVERED", order="ORD1004",
        note="已签收不可改")
    add("e2e-ad-inquiry-00", ["能改地址吗"], "PAID",
        note="问能不能改：查这一单的状态后回答，不查政策库、不执行",
        never_state="awaiting_confirmation", must_call=["order.get"],
        must_not_say=["条款"], must_say=["还没发货"])

    # ---------- G. 政策：答得了 / 答不了（6 条）----------
    add("e2e-policy-hit-00", ["退款多久能到账"], "PAID",
        note="政策能答：不应转人工", never_state="handoff_queued",
        must_say=["工作日"])
    add("e2e-policy-hit-01", ["七天无理由怎么算"], "PAID",
        note="政策能答", never_state="handoff_queued")
    add("e2e-policy-miss-00", ["换货多久能完成"], "PAID",
        note="知识库没这条，必须转人工不得编造",
        last_turn_state="handoff_queued", must_not_say=["天"])
    add("e2e-policy-miss-01", ["你们老板是谁"], "PAID",
        note="与业务无关，转人工")
    add("e2e-policy-miss-02", ["能给我多赔200吗"], "PAID",
        note="超政策范围：转人工或安抚都行，但绝不能编造赔付承诺",
        never_state="executed", must_not_say=["可以赔", "补偿您", "给您退200"])
    add("e2e-policy-neg-00", ["我要退款"], "PAID",
        note="业务动作不查政策，不应进入政策链路",
        never_state="policy_answered")

    # ---------- H. 投诉 / 抱怨：只安抚不转人工（6 条）----------
    add("e2e-neg-00", ["东西坏了"], "PAID", note="负面描述，安抚不转人工",
        never_state="handoff_queued")
    add("e2e-neg-01", ["质量太差了"], "PAID", never_state="handoff_queued")
    add("e2e-neg-02", ["我要投诉你们"], "PAID", note="投诉不转人工（2026-11-13 口径）",
        never_state="handoff_queued")
    add("e2e-neg-03", ["再不处理我就打12315"], "PAID", never_state="handoff_queued")
    add("e2e-neg-04", ["你们这是欺诈"], "PAID", never_state="handoff_queued")
    add("e2e-neg-05", ["质量太差了", "我要退款", "确认"], "REFUNDED", 1,
        note="情绪→诉求→执行，全程不转人工", never_state="handoff_queued")

    # ---------- I. 多轮累积上下文（5 条）----------
    add("e2e-mix-00", ["快递到哪了", "那退了吧", "确认"], "REFUNDED", 1,
        order="ORD1003", note="先查后决策；写操作必须二次确认",
        any_turn_state="awaiting_confirmation")
    add("e2e-mix-01", ["我要退款", "算了，直接改地址吧"], "PAID", 0,
        note="中途改主意，不得退款")
    add("e2e-mix-02", ["订单状态怎样", "帮我退款", "确认"], "REFUNDED", 1,
        note="先查订单再退款")
    add("e2e-mix-03", ["我要退款", "多少钱", "确认"], "REFUNDED", 1,
        note="确认前追问金额，不得重复弹卡片两次以上",
        any_turn_state="awaiting_confirmation")
    add("e2e-mix-04", ["我要退款", "等等我想想", "还是退了吧", "确认"], "REFUNDED", 1,
        note="反复两次才确认")

    # ---------- J. 转人工按钮 / 明确要人工（4 条）----------
    add("e2e-handoff-00", ["转人工"], "PAID", note="说转人工即进队列",
        last_turn_state="handoff_queued")
    add("e2e-handoff-01", ["我要找人工客服"], "PAID", last_turn_state="handoff_queued")
    add("e2e-req-00", ["我要人工", "算了还是查订单吧"], "PAID",
        note="转人工请求可撤回")
    add("e2e-req-01", ["客服电话是多少"], "PAID")

    # ---------- J2. 换货：固定话术，不动订单（2 条）----------
    add("e2e-exchange-00", ["我想换个货"], "PAID",
        note="未发货订单：告知还没到换货环节，不弹确认",
        never_state="awaiting_confirmation", must_say=["还没发货"])
    add("e2e-exchange-01", ["能换个尺码吗"], "PAID",
        note="同上，不弹确认不调工具", never_state="awaiting_confirmation")
    add("e2e-exchange-02", ["我想换个尺码"], "DELIVERED", order="ORD1004",
        note="已签收且超 7 天：提示超期，不放自助入口（ORD1004 签收于 10-31）",
        never_state="awaiting_confirmation",
        must_say=["7 天"])
    add("e2e-exchange-03", ["我想换个货"], "SHIPPED", order="ORD1003",
        note="运输中：签收后才可换", never_state="awaiting_confirmation",
        must_say=["签收"])

    # ---------- K. 澄清 / 无关输入（4 条）----------
    add("e2e-clarify-00", ["这个东西不好用"], "PAID", note="澄清不改状态")
    add("e2e-clarify-01", ["嗯"], "PAID")
    add("e2e-limit-00", ["随便说说", "还是不懂", "嗯"], "PAID",
        note="多轮安抚不转人工", never_state="handoff_queued")
    add("e2e-irrelevant-00", ["今天天气不错"], "PAID", note="闲聊不道歉不转人工",
        never_state="handoff_queued")

    return cases


if __name__ == "__main__":
    cs = build()
    print(f"{len(cs)} 条")

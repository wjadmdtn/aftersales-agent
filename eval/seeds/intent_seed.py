"""业务动作类用例：标准 / 对抗 / 无关键词，共 90 条。

对抗类是重点：含关键词但不是诉求。只测标准表述，准确率会虚高到 99%。
"""
from __future__ import annotations

from eval.seeds.common import business

# ---------- 标准表述 40 条 ----------

STANDARD_REFUND = [
    "我要退款",
    "申请退款",
    "能退吗",
    "我要退货",
    "这个不要了，退了吧",
    "帮我退一下款",
    "退钱",
    "我想把这个退掉",
    "麻烦给我办理退款",
    "我要退货退款",
    "这个买的有点问题，想退回",
    "衣服不合适，要退",
    "我不想留着了，退啦",
    "把钱退我就行",
    "我要申请售后退款",
    "申请一下退款吧",
    "这个能退我钱吗",
    "我要退了这个",
    "麻烦退款",
]

STANDARD_LOGISTICS = [
    "快递到哪了",
    "物流信息呢",
    "我的包裹在哪",
    "什么时候能到",
    "查下物流",
    "发货了吗",
    "运单号是多少",
    "东西寄出来了没",
    "快递怎么还没到",
    "帮我看看物流进度",
    "包裹现在在哪个城市",
    "还要几天能到啊",
]

STANDARD_ADDRESS = [
    "我要改地址",
    "地址填错了",
    "能修改收货地址吗",
    "换个收货地址",
    "地址要改一下",
    "我地址写错了",
    "修改一下配送地址",
    "我要改签到另一个地方",
    "收件地址不对",
    "地址改成公司",
    "收货地址想换一个",
    "地址要重新填一下",
]

STANDARD_ORDER = [
    "查一下我的订单",
    "订单状态是什么",
    "我买了什么",
    "订单详情",
    "看看订单",
    "我的订单到什么状态了",
    "这单付款成功了吗",
    "订单号是多少",
    "查订单",
    "帮我看看这个订单",
    "我这单现在是啥情况",
    "订单到哪一步了",
]

# ---------- 对抗表述 30 条：含关键词但不是诉求 ----------

ADVERSARIAL = [
    # 威胁施压 —— 是投诉，不是退款
    (("再不处理我就投诉你们不肯退款"), "complaint"),
    (("我要去消协告你们退款拖了半个月"), "complaint"),
    (("你们这是欺诈，我要曝光你们不退款"), "complaint"),
    (("信不信我给差评，退个款这么难"), "complaint"),
    # 咨询可行性 —— 先回答，不直接走流程
    (("这个能退款吗"), "refund"),
    (("我这个情况可以退货吗"), "refund"),
    (("退款要多久到账"), "refund"),
    # 历史陈述 —— 是抱怨过去
    (("上次退款你们拖了半个月"), "complaint"),
    (("上一单退款到现在还没到"), "complaint"),
    (("之前退货就很麻烦"), "complaint"),
    # 反向表达 —— 诉求不明
    (("别再发退款短信给我了"), "general"),
    (("不用给我推荐退款了"), "general"),
    # 转述他人 —— 不是自己诉求
    (("我朋友说可以退货是真的吗"), "general"),
    (("别人都退款了为什么我不行"), "complaint"),
    # 混合表达 —— 含多个诉求，按主诉求
    (("我要退款然后查下物流"), "refund"),
    (("先别退款，我改个地址"), "change_address"),
    # 含关键词但问的是别的
    (("我退款的那个订单物流到哪了"), "logistics"),
    (("退款之后地址还能改吗"), "change_address"),
    (("退款记录在哪看"), "query_order"),
    # 语气词干扰
    (("嗯……退款吧"), "refund"),
    (("那个……我想问下退货的事"), "refund"),
    # 否定
    (("我不想退款，我只想换个货"), "general"),
    (("不是要退款，是地址错了"), "change_address"),
    # 长文本里的诉求
    (("东西收到了但是有质量问题，我要退款"), "refund"),
    (("等了十几天还没发货，给我退款"), "refund"),
    (("我下单三天了物流一直不更新，查一下"), "logistics"),
    (("收货地址写成了老家，能改吗"), "change_address"),
    (("这个订单我需要退款处理一下"), "refund"),
    (("快递显示签收了但我没收到"), "logistics"),
    (("订单状态一直是待发货"), "query_order"),
]

# ---------- 无关键词但有明确诉求 20 条 ----------

NO_KEYWORD = [
    (("钱什么时候能回来"), "refund"),
    (("这个没法用"), "general"),
    (("东西摔碎了"), "refund"),
    (("我想把它还给你们"), "refund"),
    (("我的钱还能拿回来吗"), "refund"),
    (("东西在路上多久了"), "logistics"),
    (("怎么还没送来"), "logistics"),
    (("几号能收到"), "logistics"),
    (("我家住址换了"), "change_address"),
    (("送错地方了"), "change_address"),
    (("寄到别的地方去"), "change_address"),
    (("我把地址说错了"), "change_address"),
    (("付了钱没看到东西"), "logistics"),
    (("这单算多少钱"), "query_order"),
    (("我什么时候买的"), "query_order"),
    (("买东西的记录在哪"), "query_order"),
    (("这东西我不想要了"), "refund"),
    (("买错了"), "refund"),
    (("我要把东西寄回去"), "refund"),
    (("包裹一直没动静"), "logistics"),
]


def build() -> list:
    cases = []

    for i, text in enumerate(STANDARD_REFUND):
        cases.append(business(f"intent-std-rf-{i:02d}", category="standard", text=text, intent="refund"))
    for i, text in enumerate(STANDARD_LOGISTICS):
        cases.append(business(f"intent-std-lg-{i:02d}", category="standard", text=text, intent="logistics"))
    for i, text in enumerate(STANDARD_ADDRESS):
        cases.append(
            business(f"intent-std-ad-{i:02d}", category="standard", text=text, intent="change_address")
        )
    for i, text in enumerate(STANDARD_ORDER):
        cases.append(business(f"intent-std-od-{i:02d}", category="standard", text=text, intent="query_order"))

    for i, (text, intent) in enumerate(ADVERSARIAL):
        cases.append(
            business(f"intent-adv-{i:02d}", category="adversarial", text=text, intent=intent)
        )

    for i, (text, intent) in enumerate(NO_KEYWORD):
        cases.append(
            business(f"intent-nokw-{i:02d}", category="no_keyword", text=text, intent=intent)
        )

    return cases


if __name__ == "__main__":
    cs = build()
    print(f"{len(cs)} 条：标准 40 / 对抗 {len(ADVERSARIAL)} / 无关键词 {len(NO_KEYWORD)}")

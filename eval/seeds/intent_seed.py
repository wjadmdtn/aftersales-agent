"""业务动作类用例：标准 / 对抗 / 无关键词，共 90 条。

对抗类是重点：含关键词但不是诉求。只测标准表述，准确率会虚高到 99%。
"""
from __future__ import annotations

from eval.seeds.common import business

# ---------- 标准表述 40 条 ----------

STANDARD_REFUND = [
    "我要退款",
    "申请退款",
    "能退吗",  # 疑问句，标 inquiry
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
    (("再不处理我就投诉你们不肯退款"), "unknown"),
    (("我要去消协告你们退款拖了半个月"), "unknown"),
    (("你们这是欺诈，我要曝光你们不退款"), "unknown"),
    (("信不信我给差评，退个款这么难"), "unknown"),
    # 咨询可行性 —— 先回答，不直接走流程
    (("这个能退款吗"), "refund", "inquiry"),
    (("我这个情况可以退货吗"), "refund", "inquiry"),
    (("退款要多久到账"), "policy", "inquiry"),  # 问的是通用到账时效，不是本单进度
    # 历史陈述 —— 是抱怨过去
    (("上次退款你们拖了半个月"), "unknown"),
    (("上一单退款到现在还没到"), "refund", "query"),  # 查上一单退款进度
    (("之前退货就很麻烦"), "unknown"),
    # 反向表达 —— 诉求不明
    (("别再发退款短信给我了"), "unknown"),
    (("不用给我推荐退款了"), "unknown"),
    # 转述他人 —— 不是自己诉求
    (("我朋友说可以退货是真的吗"), "policy", "inquiry"),  # 转述+问规则，答规则即可
    (("别人都退款了为什么我不行"), "unknown"),
    # 混合表达 —— 含多个诉求，按主诉求
    (("我要退款然后查下物流"), "refund"),
    (("先别退款，我改个地址"), "change_address"),
    # 含关键词但问的是别的
    (("我退款的那个订单物流到哪了"), "logistics"),
    (("退款之后地址还能改吗"), "policy", "inquiry"),  # 问的是"退款后退货流程里能否改地址"的规则
    (("退款记录在哪看"), "unknown", "execute"),  # 功能入口询问，归 unknown 走能力介绍
    # 语气词干扰
    (("嗯……退款吧"), "refund", "execute"),  # "退款吧"是祈使，要执行
    (("那个……我想问下退货的事"), "policy", "inquiry"),  # 问规则，不是发起退货
    # 否定
    (("我不想退款，我只想换个货"), "exchange", "execute"),
    (("不是要退款，是地址错了"), "change_address"),
    # 换货 —— 系统不支持，识别出来回固定话术
    (("能换个尺码吗"), "exchange", "inquiry"),
    (("这个颜色不喜欢，想换一个"), "exchange", "execute"),
    # 长文本里的诉求
    (("东西收到了但是有质量问题，我要退款"), "refund"),
    (("等了十几天还没发货，给我退款"), "refund"),
    (("我下单三天了物流一直不更新，查一下"), "logistics"),
    (("收货地址写成了老家，能改吗"), "change_address", "inquiry"),
    (("这个订单我需要退款处理一下"), "refund"),
    (("快递显示签收了但我没收到"), "logistics"),
    (("订单状态一直是待发货"), "query_order"),
]

# ---------- 无关键词但有明确诉求 20 条 ----------

NO_KEYWORD = [
    (("钱什么时候能回来"), "refund", "query"),
    (("这个没法用"), "unknown"),
    (("东西摔碎了"), "unknown"),
    (("我想把它还给你们"), "refund"),
    (("我的钱还能拿回来吗"), "refund", "query"),  # 问进度
    (("东西在路上多久了"), "logistics"),
    (("怎么还没送来"), "logistics"),
    (("几号能收到"), "logistics"),
    # 陈述事实但没要求执行 → need_context（系统会追问订单号，合理）
    (("我家住址换了"), "change_address", "need_context"),
    (("送错地方了"), "change_address"),
    (("寄到别的地方去"), "change_address", "need_context"),
    (("我把地址说错了"), "change_address"),
    (("付了钱没看到东西"), "logistics", "query"),  # 没收到货，查物流
    (("这单算多少钱"), "query_order"),
    (("我什么时候买的"), "query_order"),
    # 「在哪」是查找，不是执行 → inquiry
    (("买东西的记录在哪"), "query_order", "inquiry"),
    (("这东西我不想要了"), "refund"),
    # 「买错了」没说要不要退，先问清更安全 → inquiry
    (("买错了"), "refund", "inquiry"),
    (("我要把东西寄回去"), "refund"),
    (("包裹一直没动静"), "logistics"),
]


def build() -> list:
    cases = []

    # 读意图（查物流/查订单）的 mode 是 query，不是 execute。
    # 此前这些用例没传 mode，用了 business() 的默认 execute——但意图 prompt
    # 里「快递到哪了」正是 query 的示例，模型判 query 是对的，标注才是错的。
    # 约 40 条被错标，把业务动作准确率从约 80% 压到 44.5%。
    # 注：读意图在 loop 里先命中 _handle_read，不检查 mode，改标注不影响运行行为。
    MODE_OF = {"logistics": "query", "query_order": "query"}

    def _mode(intent: str, default: str = "execute") -> str:
        return MODE_OF.get(intent, default)

    # 疑问句判断「能不能」≠ 要求执行：系统只回答不执行才安全。
    # 「这个能退我钱吗」此前被标 execute，模型判 inquiry 是对的。
    INQUIRY_PHRASES = {"能退吗", "这个能退款吗", "退款要多久到账", "这个能退我钱吗"}
    for i, text in enumerate(STANDARD_REFUND):
        mode = "inquiry" if text in INQUIRY_PHRASES else "execute"
        cases.append(
            business(
                f"intent-std-rf-{i:02d}", category="standard", text=text, intent="refund", mode=mode
            )
        )
    for i, text in enumerate(STANDARD_LOGISTICS):
        cases.append(
            business(
                f"intent-std-lg-{i:02d}",
                category="standard",
                text=text,
                intent="logistics",
                mode=_mode("logistics"),
            )
        )
    AD_INQUIRY = {"能修改收货地址吗", "我地址写错了"}  # 陈述问题未提要求 → 先回答不直接执行
    for i, text in enumerate(STANDARD_ADDRESS):
        mode = "inquiry" if text in AD_INQUIRY else "execute"
        cases.append(
            business(
                f"intent-std-ad-{i:02d}",
                category="standard",
                text=text,
                intent="change_address",
                mode=mode,
            )
        )
    for i, text in enumerate(STANDARD_ORDER):
        cases.append(
            business(
                f"intent-std-od-{i:02d}",
                category="standard",
                text=text,
                intent="query_order",
                mode=_mode("query_order"),
            )
        )

    for i, row in enumerate(ADVERSARIAL):
        text, intent = row[0], row[1]
        mode = row[2] if len(row) > 2 else _mode(intent)
        cases.append(
            business(
                f"intent-adv-{i:02d}", category="adversarial", text=text, intent=intent, mode=mode
            )
        )

    for i, row in enumerate(NO_KEYWORD):
        text, intent = row[0], row[1]
        mode = row[2] if len(row) > 2 else _mode(intent)
        cases.append(
            business(
                f"intent-nokw-{i:02d}", category="no_keyword", text=text, intent=intent, mode=mode
            )
        )

    return cases


if __name__ == "__main__":
    cs = build()
    print(f"{len(cs)} 条：标准 40 / 对抗 {len(ADVERSARIAL)} / 无关键词 {len(NO_KEYWORD)}")

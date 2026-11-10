"""阈值标定用的正负样本（T027）。

与 eval/seeds/policy_seed.py 的区别：
  policy_seed   评测用例，30 条，测端到端（意图 + 引用）
  本文件        标定样本，只测检索层，必须**显著大于**评测集

样本量小的标定会过拟合——同一个 30 条样本既调阈值又报准确率，
阈值必然「刚好合适」，等于自欺。此处正负各 100 条，且与评测用例措辞不重叠。

正样本（pos）：应召回且应命中，带唯一正确 clause_id
  口语化改写是重点。真实用户不会照抄条款标题，会加语气词、省略、换序。

负样本（neg）：应判 miss
  三类，缺一不可：
    - off_topic   完全无关（寒暄、闲聊、越权、无意义输入）→ 测最低端
    - action      是业务动作（要退款、要投诉）→ 测「听懂了但不归政策管」
    - near_miss   看起来像政策但不在知识库覆盖范围 → 测「政策没写就别说」
  near_miss 最关键：它是幻觉的高发区，也是阈值调高以后误杀最多的正样本邻居。
"""
from __future__ import annotations

# (query, 期望漏出的 clause_id, 备注)
POSITIVES: list[tuple[str, str]] = [
    # --- refund ---
    ("退款一般多久到账啊", "REFUND-COMMON-01", ""),
    ("钱什么时候退回来", "REFUND-COMMON-01", ""),
    ("退了款什么时候能收到", "REFUND-COMMON-01", ""),
    ("退款走银行卡要几天", "REFUND-COMMON-01", ""),
    ("退款到账时间是多久", "REFUND-COMMON-01", ""),
    ("我申请退款了钱多久能到", "REFUND-COMMON-01", ""),
    ("退款金额是怎么算出来的", "REFUND-COMMON-02", ""),
    ("退款能退多少钱", "REFUND-COMMON-02", ""),
    ("退的钱包不包括运费", "REFUND-COMMON-02", ""),
    ("退款是不是扣了手续费", "REFUND-COMMON-02", ""),
    ("退货运费退吗", "REFUND-COMMON-03", ""),
    ("寄回去的快递费谁承担", "REFUND-COMMON-03", ""),
    ("退货的邮费能报销吗", "REFUND-COMMON-03", ""),
    ("运费退不退给我", "REFUND-COMMON-03", ""),
    ("商家迟迟不给我退款", "REFUND-COMMON-04", ""),
    ("申请退款商家一直不处理", "REFUND-COMMON-04", ""),
    ("退款一直卡在审核怎么办", "REFUND-COMMON-04", ""),
    ("卖家拖着不退款可以投诉吗", "REFUND-COMMON-04", ""),
    # --- 七天无理由 ---
    ("哪些东西能七天无理由", "RETURN-SEVENDAY-01", ""),
    ("七天无理由退货适用范围", "RETURN-SEVENDAY-01", ""),
    ("是不是所有商品都支持七天无理由", "RETURN-SEVENDAY-01", ""),
    ("什么商品可以无理由退", "RETURN-SEVENDAY-01", ""),
    ("七天无理由是从签收开始算吗", "RETURN-SEVENDAY-02", ""),
    ("七天无理由怎么算第一天", "RETURN-SEVENDAY-02", ""),
    ("无理由退货的七天从哪天起", "RETURN-SEVENDAY-02", ""),
    ("超了七天还能无理由退吗", "RETURN-SEVENDAY-02", ""),
    ("七天无理由退货运费谁付", "RETURN-SEVENDAY-03", ""),
    ("无理由退货运费我出还是商家出", "RETURN-SEVENDAY-03", ""),
    ("七天无理由退货运费怎么算", "RETURN-SEVENDAY-03", ""),
    ("赠品需要退回去吗", "RETURN-SEVENDAY-03", ""),
    ("退货的时候赠品要一起寄回吗", "RETURN-SEVENDAY-03", ""),
    # --- 完好标准 ---
    ("怎么才算商品完好", "RETURN-GOOD-STANDARD-01", ""),
    ("退货商品完好的标准是什么", "RETURN-GOOD-STANDARD-01", ""),
    ("吊牌剪了还能退吗", "RETURN-GOOD-STANDARD-01", ""),
    ("包装拆了算不算影响二次销售", "RETURN-GOOD-STANDARD-01", ""),
    ("试穿过的衣服能退吗", "RETURN-GOOD-STANDARD-01", ""),
    # --- 不支持七天 ---
    ("哪些商品不能七天无理由", "RETURN-EXCEPTION-01", ""),
    ("哪些不支持无理由退货", "RETURN-EXCEPTION-01", ""),
    ("生鲜可以不支持退货吗", "RETURN-EXCEPTION-01", ""),
    ("定制的东西能无理由退吗", "RETURN-EXCEPTION-01", ""),
    ("拆封的数码产品能退吗", "RETURN-EXCEPTION-01", ""),
    # --- 换货 ---
    ("换货有什么条件", "AFTERSALE-EXCHANGE-01", ""),
    ("什么情况可以换货", "AFTERSALE-EXCHANGE-01", ""),
    ("换货要多久", "AFTERSALE-EXCHANGE-01", ""),
    ("换货运费谁出", "AFTERSALE-EXCHANGE-01", ""),
    ("换货流程是怎样的", "AFTERSALE-EXCHANGE-01", ""),
    # --- 维修 ---
    ("保修怎么申请", "AFTERSALE-REPAIR-01", ""),
    ("东西坏了能保修吗", "AFTERSALE-REPAIR-01", ""),
    ("保修期是多长", "AFTERSALE-REPAIR-01", ""),
    ("维修要花钱吗", "AFTERSALE-REPAIR-01", ""),
    ("怎么送修", "AFTERSALE-REPAIR-01", ""),
    # --- 发票 ---
    ("能开增值税发票吗", "INVOICE-01", ""),
    ("开发票需要什么", "INVOICE-01", ""),
    ("发票开错了可以重开吗", "INVOICE-01", ""),
    ("发票抬头写错了怎么改", "INVOICE-01", ""),
    ("可以补开发票吗", "INVOICE-01", ""),
    ("退换货要提供什么资料", "INVOICE-02", ""),
    ("申请售后需要什么凭证", "INVOICE-02", ""),
    ("售后要提供购买截图吗", "INVOICE-02", ""),
    # --- 物流 ---
    ("超时没发货有补偿吗", "LOGISTICS-01", ""),
    ("一直不发货能赔偿吗", "LOGISTICS-01", ""),
    ("晚发货有没有赔付", "LOGISTICS-01", ""),
    ("显示签收了但是我没收到", "LOGISTICS-02", ""),
    ("快递说签收了实际没到", "LOGISTICS-02", ""),
    ("物流显示已签收但找不到件", "LOGISTICS-02", ""),
    ("快递运输中坏了怎么办", "LOGISTICS-03", ""),
    ("收到的时候是破损的", "LOGISTICS-03", ""),
    ("运输过程损坏谁负责", "LOGISTICS-03", ""),
    # --- 订单修改 ---
    ("还没发货能改地址吗", "ORDER-ADDRESS-01", ""),
    ("下单后地址填错了能改吗", "ORDER-ADDRESS-01", ""),
    ("收货地址可以修改吗", "ORDER-ADDRESS-01", ""),
    ("能换尺码吗", "ORDER-ADDRESS-02", ""),
    ("可以改颜色吗", "ORDER-ADDRESS-02", ""),
    ("订单商品规格能改吗", "ORDER-ADDRESS-02", ""),
    # --- 预售 ---
    ("预售定金能退吗", "PRESALE-01", ""),
    ("预购的定金不给退吗", "PRESALE-01", ""),
    ("定金和尾款怎么算", "PRESALE-01", ""),
    ("预售什么时候发货", "PRESALE-02", ""),
    ("预售商品多久能到", "PRESALE-02", ""),
    ("预售发货时间是什么时候", "PRESALE-02", ""),
    # --- 价保 ---
    ("买完就降价了能退差价吗", "PRICE-PROTECT-01", ""),
    ("价保的规则是什么", "PRICE-PROTECT-01", ""),
    ("降价了能补差价吗", "PRICE-PROTECT-01", ""),
    ("价保申请有时间限制吗", "PRICE-PROTECT-02", ""),
    ("价保多久内有效", "PRICE-PROTECT-02", ""),
    ("价保过期了还能申请吗", "PRICE-PROTECT-02", ""),
    # --- 跨境 ---
    ("跨境订单怎么退税", "CROSSBORDER-01", ""),
    ("海购的商品能退吗", "CROSSBORDER-01", ""),
    ("跨境订单售后怎么处理", "CROSSBORDER-01", ""),
    ("跨境商品的税费谁承担", "CROSSBORDER-02", ""),
    ("跨境退货运费包含关税吗", "CROSSBORDER-02", ""),
    # --- 企业 ---
    ("企业采购售后服务一样吗", "B2B-01", ""),
    ("公司团购有售后吗", "B2B-01", ""),
    ("企业账户的退货政策和普通的一样吗", "B2B-01", ""),
    # --- 以旧换新 ---
    ("以旧换新旧机怎么估价", "TRADE-IN-01", ""),
    ("旧手机抵多少钱怎么算", "TRADE-IN-01", ""),
    ("以旧换新估价标准", "TRADE-IN-01", ""),
    ("以旧换新换来的新机怎么保修", "TRADE-IN-02", ""),
    ("以旧换新的新机售后政策", "TRADE-IN-02", ""),
]


# (query, 类别, 备注)
NEGATIVES: list[tuple[str, str, str]] = [
    # --- off_topic ---
    ("你好", "off_topic", ""),
    ("在吗", "off_topic", ""),
    ("谢谢", "off_topic", ""),
    ("你是谁开发的", "off_topic", ""),
    ("你们公司老板是谁", "off_topic", ""),
    ("今天天气怎么样", "off_topic", ""),
    ("给我讲个笑话", "off_topic", ""),
    ("帮我写首诗", "off_topic", ""),
    ("你能做什么", "off_topic", ""),
    ("客服是机器人吗", "off_topic", ""),
    ("asdfghjkl", "off_topic", ""),
    ("12345678", "off_topic", ""),
    ("。。。", "off_topic", ""),
    ("？？？", "off_topic", ""),
    ("呵呵", "off_topic", ""),
    # --- action：听懂了，但不归政策管 ---
    ("我要退款", "action", ""),
    ("帮我退货", "action", ""),
    ("给我把钱退了", "action", ""),
    ("我要投诉", "action", ""),
    ("我要找你们领导", "action", ""),
    ("帮我改一下收货地址", "action", ""),
    ("取消这个订单", "action", ""),
    ("我的订单号是ORD1003，帮我查下物流", "action", ""),
    ("查一下我最近的订单", "action", ""),
    ("我要转人工", "action", ""),
    ("给我转客服", "action", ""),
    ("我要炸了，马上给我处理", "action", ""),
    ("再不退款我就曝光你们", "action", ""),
    # --- near_miss：看起来像政策，知识库没覆盖 ---
    ("能不能开发票分期", "near_miss", "发票政策有，分期政策没有"),
    ("会员等级有什么特别的售后权益", "near_miss", ""),
    ("黑卡会员能优先处理吗", "near_miss", ""),
    ("积分可以抵现吗", "near_miss", ""),
    ("优惠券过期了能补吗", "near_miss", ""),
    ("满减怎么算的", "near_miss", ""),
    ("买二送一是怎么送的", "near_miss", ""),
    ("能不能货到付款", "near_miss", ""),
    ("支持花呗分期吗", "near_miss", ""),
    ("有没有无息分期", "near_miss", ""),
    ("能刷信用卡吗", "near_miss", ""),
    ("能寄到国外吗", "near_miss", "跨境有，寄国外没有"),
    ("能不能代收货款", "near_miss", ""),
    ("支持自提吗", "near_miss", ""),
    ("门店可以退吗", "near_miss", ""),
    ("能不能上门取件", "near_miss", "上门取件流程没有独立条款"),
    ("退货能叫快递员上门吗", "near_miss", ""),
    ("发票能开电子专票吗", "near_miss", ""),
    ("可以开红字发票吗", "near_miss", ""),
    ("买多了能拆单吗", "near_miss", ""),
    ("能合并订单发货吗", "near_miss", ""),
    ("送我一张优惠券吧", "near_miss", ""),
    ("给我打个折", "near_miss", ""),
    ("有没有内部员工价", "near_miss", ""),
    ("样品能便宜点吗", "near_miss", ""),
    ("可以预留三天吗", "near_miss", ""),
    ("能帮我留一件吗", "near_miss", ""),
    ("这个库存还有多少", "near_miss", ""),
    ("什么时候补货", "near_miss", ""),
    ("上次你答错了，重新说一遍", "near_miss", ""),
]


def all_positive_queries() -> set[str]:
    return {q for q, _, _ in POSITIVES}


def all_negative_queries() -> set[str]:
    return {q for q, _, _ in NEGATIVES}


if __name__ == "__main__":
    print(f"正样本 {len(POSITIVES)} / 负样本 {len(NEGATIVES)}")

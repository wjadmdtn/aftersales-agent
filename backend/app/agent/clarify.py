"""澄清话术。独立成模块，便于测试"不得含业务承诺"这条硬约束。"""

# 统一一句道歉，不区分程度——分级措辞容易出错，且用户感知差异不大
APOLOGY = "非常抱歉给您带来了不好的体验。"

CLARIFY_FIRST_NEGATIVE = APOLOGY + "方便说下具体情况吗？我帮您记录一下。"
CLARIFY_FIRST_NEUTRAL = "方便说下您遇到的具体情况吗？我帮您看看怎么处理。"

# 第 2 轮继续追问，**不再征求转人工**。
# 产品口径：抱怨类不该被推向人工；只有明确投诉/要人工/差评才转，
# 那些在意图层就拦下了。这里只负责把诉求问出来。
CLARIFY_SECOND = "了解了。方便再具体说下是哪方面的问题吗？比如是商品本身、物流时效，还是操作上的问题？"

# 情绪类唯一的回应：只道歉安抚，不追问、不引导、不承诺。
# 口径（2026-11-13）：User 表达不满但没提诉求时，AI 不做任何别的操作。
# 追问细节会把"吐槽"变成"被盘问"；转人工则把人工成本吃光。
COMFORT = APOLOGY

ALL_SCRIPTS = (CLARIFY_FIRST_NEGATIVE, CLARIFY_FIRST_NEUTRAL, CLARIFY_SECOND, APOLOGY)

# 禁止出现的业务承诺词。AI 说出去的承诺与人工客服同等效力，
# 一旦提及就是在替商家做主。
FORBIDDEN_PROMISES = (
    "退款",
    "退货",
    "退换",
    "换货",
    "修改地址",
    "补发",
    "补偿",
    "赔付",
    "赔偿",
    "可以帮您申请",
    "帮您申请",
    "保证",
)


def clarify_round_one(*, negative: bool) -> str:
    return CLARIFY_FIRST_NEGATIVE if negative else CLARIFY_FIRST_NEUTRAL


def clarify_round_two() -> str:
    return CLARIFY_SECOND


def comfort_reply() -> str:
    """情绪类唯一回应：一句道歉，别的什么都不做。多轮重复此句。"""
    return COMFORT

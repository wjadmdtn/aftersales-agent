"""澄清话术。独立成模块，便于测试"不得含业务承诺"这条硬约束。"""

# 统一一句道歉，不区分程度——分级措辞容易出错，且用户感知差异不大
APOLOGY = "非常抱歉给您带来了不好的体验。"

CLARIFY_FIRST_NEGATIVE = APOLOGY + "方便说下具体情况吗？我帮您记录一下。"
CLARIFY_FIRST_NEUTRAL = "方便说下您遇到的具体情况吗？我帮您看看怎么处理。"

# 第 2 轮改为征求转人工，不再列业务选项。
# 列选项本质上是在引导用户走某条路，仍然有暗示承诺的风险。
CLARIFY_SECOND = "了解了。为了不耽误您，需要我帮您转接到人工客服详细沟通吗？"

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

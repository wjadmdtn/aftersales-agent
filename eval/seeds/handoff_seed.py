"""转人工类用例 30 条：四类触发源。

判定顺序：用户主动要求 > 风险 > 系统异常 > 边界外。
"我要投诉，转人工"按 user_request 处理——用户要的是人，不是 AI 道歉。
"""
from __future__ import annotations

from eval.seeds.common import handoff

# ---------- 用户主动要求 8 条，优先级 medium ----------

USER_REQUEST = [
    "转人工",
    "我要找人工客服",
    "人工",
    "找真人",
    "能不能叫人",
    "我要客服电话",
    "找你们客服",
    "换个人跟我说",
]

# ---------- 风险触发 12 条，优先级 high ----------

RISK = [
    "我要投诉你们",
    "你们这是欺诈",
    "我要去消协投诉",
    "再不处理我就打12315",
    "我要曝光你们",
    "这是骗子吧",
    "你们太差劲了",
    "我要给差评",
    "必须给我个说法",
    "我起诉你们",
    "举报你们",
    "你们这服务真让人上火",
]

# ---------- 系统异常 5 条 ----------
# 生产中由"工具连续失败 ≥3 次"触发，与用户说了什么无关。
# 用例通过 context 传信号，不靠文本命中——否则测的就不是真实触发路径。

SYSTEM_ERROR = [
    ("订单查不出来，一直报错", 3),
    ("提交了好几次都没反应", 4),
    ("一直转圈", 3),
    ("页面打不开", 5),
    ("系统又崩了吗", 3),
]

# ---------- 能力边界外 5 条 ----------
# 工具返回 NOT_FOUND / EMPTY 时触发。

BOUNDARY = [
    ("我查一下 ORD999999", "NOT_FOUND"),
    ("查下这个订单 ORD888888", "NOT_FOUND"),
    ("看看物流", "EMPTY"),
    ("我的包裹呢", "EMPTY"),
    ("帮我查个单子", "NOT_FOUND"),
]


def build() -> list:
    cases = []

    for i, text in enumerate(USER_REQUEST):
        cases.append(
            handoff(f"handoff-req-{i:02d}", text=text, trigger_type="user_request", priority="medium")
        )

    for i, text in enumerate(RISK):
        cases.append(handoff(f"handoff-risk-{i:02d}", text=text, trigger_type="risk", priority="high"))

    for i, (text, streak) in enumerate(SYSTEM_ERROR):
        c = handoff(f"handoff-sys-{i:02d}", text=text, trigger_type="system_error", priority="low")
        c.context["tool_failure_streak"] = streak
        cases.append(c)

    for i, (text, status) in enumerate(BOUNDARY):
        c = handoff(f"handoff-bnd-{i:02d}", text=text, trigger_type="boundary", priority="low")
        c.context["last_tool_status"] = status
        cases.append(c)

    return cases


if __name__ == "__main__":
    cs = build()
    print(
        f"{len(cs)} 条：主动要求 {len(USER_REQUEST)} / 风险 {len(RISK)}"
        f" / 系统异常 {len(SYSTEM_ERROR)} / 边界外 {len(BOUNDARY)}"
    )

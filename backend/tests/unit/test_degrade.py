"""降级：统一话术，不猜意图。

早先用关键词判读写，结果「退款多久能到账」被当写操作拒掉，
而「钱能回来吗」被放行——两个判断都不可靠。
现在只有一条路：重试耗尽 → 统一话术 + 引导转人工。
"""

from app.agent.degrade import DEGRADED_REPLY, degraded_reply


def test_returns_uniform_script():
    assert degraded_reply() == DEGRADED_REPLY


def test_script_mentions_retry_and_human():
    assert "稍后再试" in DEGRADED_REPLY
    assert "人工" in DEGRADED_REPLY


def test_script_makes_no_promise():
    """降级话术不得暗示能处理、不得承诺时效。"""
    for word in ("退款", "退换", "补发", "补偿", "马上", "立即", "分钟", "小时"):
        assert word not in DEGRADED_REPLY, word


def test_script_does_not_guess_user_intent():
    """不推测用户想干什么——此刻判断意图正是不可用的能力。"""
    for word in ("您是想", "您要", "请问您"):
        assert word not in DEGRADED_REPLY, word

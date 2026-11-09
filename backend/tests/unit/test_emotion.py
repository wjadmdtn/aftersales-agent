"""T007: 规则情绪判定。必须零 LLM 调用。"""

from app.config import Settings
from app.handoff.emotion import detect_emotion
from app.models.handoff import Emotion

CFG = Settings()


def test_angry_keyword():
    assert detect_emotion("你们这是骗子！", CFG) is Emotion.ANGRY


def test_repeated_exclamation():
    assert detect_emotion("太过分了！！", CFG) is Emotion.ANGRY
    assert detect_emotion("真的不行!!!", CFG) is Emotion.ANGRY


def test_single_exclamation_is_normal():
    assert detect_emotion("好的!", CFG) is Emotion.NEUTRAL


def test_negative_keyword():
    assert detect_emotion("我有点不满意", CFG) is Emotion.NEGATIVE


def test_neutral_default():
    assert detect_emotion("请问订单到哪了", CFG) is Emotion.NEUTRAL


def test_makes_no_llm_call():
    """情绪判定全程不得调用 LLM。这里用猴子补丁确保没人偷偷加进去。"""
    import app.agent.llm.stub as stub_mod

    original = stub_mod.StubLLMClient.classify
    called = {"n": 0}

    async def boom(self, **kw):
        called["n"] += 1
        return await original(self, **kw)

    stub_mod.StubLLMClient.classify = boom
    try:
        detect_emotion("垃圾东西！！！", CFG)
        detect_emotion("还行吧", CFG)
    finally:
        stub_mod.StubLLMClient.classify = original

    assert called["n"] == 0, "情绪判定不允许调用 LLM"


def test_angry_takes_precedence_over_negative():
    assert detect_emotion("不满意，垃圾", CFG) is Emotion.ANGRY


def test_keywords_configurable():
    cfg = Settings(angry_keywords_raw='["气死我了"]')
    assert detect_emotion("气死我了", cfg) is Emotion.ANGRY
    assert detect_emotion("骗子", cfg) is Emotion.NEUTRAL

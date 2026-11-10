"""澄清类用例 60 条：有情绪 25 / 无情绪 15 / 无关 20。

标注规则第 5 条：表达不满或困惑但未指明诉求 → 澄清。
"AI 没听懂"不等于"用户要人工"。
"""
from __future__ import annotations

from eval.seeds.common import clarify, irrelevant

# ---------- 有情绪，应道歉 25 条 ----------

WITH_EMOTION = [
    "这个东西不好用",
    "买回来根本用不了",
    "质量太差了",
    "有划痕",
    "东西破损了",
    "和图片不一样",
    "这个太让人失望了",
    "你们东西有问题",
    "收到的货是坏的",
    "买了个废品",
    "完全不能用",
    "太慢了",
    "等太久了",
    "每次都要等半天",
    "你们效率太低了",
    "这服务真让人无语",
    "有点不满意",
    "不太行啊",
    "差评",
    "很失望",
    "东西质量不行",
    "用了两天就坏了",
    "跟描述的完全不一样",
    "这个有问题啊",
    "收到的货有问题！！！",
]

# ---------- 无情绪，不应道歉 15 条 ----------

NO_EMOTION = [
    "随便说说",
    "嗯",
    "哦",
    "你好",
    "在吗",
    "我想咨询一下",
    "有个问题",
    "问个事",
    "想了解下",
    "这个怎么弄",
    "我想问一下怎么操作",
    "请问",
    "那个",
    "问一下",
    "想问问",
]

# ---------- 无关输入，不应道歉 20 条 ----------

IRRELEVANT = [
    "今天天气不错",
    "你吃饭了吗",
    "你是谁",
    "你能干什么",
    "讲个笑话",
    "现在几点了",
    "今天是星期几",
    "我喜欢吃苹果",
    "你好厉害啊",
    "啊啊啊",
    "123",
    "？？？",
    "哈哈",
    "谢谢",
    "好的",
    "嗯嗯",
    "ok",
    "测试",
    "hello",
    "我在",
]


def build() -> list:
    cases = []
    for i, text in enumerate(WITH_EMOTION):
        cases.append(
            clarify(f"clarify-neg-{i:02d}", category="with_emotion", text=text, apology=True)
        )
    for i, text in enumerate(NO_EMOTION):
        cases.append(
            clarify(f"clarify-neu-{i:02d}", category="no_emotion", text=text, apology=False)
        )
    for i, text in enumerate(IRRELEVANT):
        cases.append(irrelevant(f"irrelevant-{i:02d}", text=text))
    return cases


if __name__ == "__main__":
    cs = build()
    print(f"{len(cs)} 条：有情绪 {len(WITH_EMOTION)} / 无情绪 {len(NO_EMOTION)} / 无关 {len(IRRELEVANT)}")

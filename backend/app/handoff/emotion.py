"""T009: 规则情绪判定。零 LLM 调用。

判错后果只是优先级排序偏移，与意图识别的代价不同，
因此用确定性规则而非模型——同一句话每次都必须得到同样的优先级。
"""
from __future__ import annotations

import re

from app.config import Settings, settings
from app.models.handoff import Emotion

_REPEATED_EXCLAIM = re.compile(r"[!！]{2,}")


def detect_emotion(text: str, cfg: Settings | None = None) -> Emotion:
    c = cfg or settings

    for w in c.angry_keywords:
        if w in text:
            return Emotion.ANGRY

    if _REPEATED_EXCLAIM.search(text):
        return Emotion.ANGRY

    for w in c.negative_keywords:
        if w in text:
            return Emotion.NEGATIVE

    return Emotion.NEUTRAL

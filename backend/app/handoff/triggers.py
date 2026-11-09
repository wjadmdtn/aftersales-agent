"""T005: 四类触发源。

判定顺序是设计本身，命中即返回：

    1. 用户主动要求   —— 用户要真人，任何其他判断都是多余的
    2. 风险           —— 投诉/情绪激烈，不能让 AI 再多说一轮
    3. 系统异常       —— 工具连续失败，用户已经等很久了
    4. 能力边界外     —— 可以先给自助建议，最不紧急

"我要投诉，转人工"同时命中 1 和 2，按 1 处理：用户要求的是"要人"，
不是"要 AI 道歉"。
"""
from __future__ import annotations

from app.config import Settings, settings
from app.models.handoff import ToolStatus, TriggerResult, TriggerType, TurnSignals


def _match_any(text: str, words: tuple[str, ...]) -> str | None:
    for w in words:
        if w in text:
            return w
    return None


def check_user_request(s: TurnSignals, cfg: Settings) -> TriggerResult:
    hit = _match_any(s.user_message, cfg.user_request_keywords)
    if hit:
        return TriggerResult.fire(TriggerType.USER_REQUEST, f"关键词命中：{hit}")
    return TriggerResult.none()


def check_risk(s: TurnSignals, cfg: Settings) -> TriggerResult:
    reasons: list[str] = []
    if s.refund_amount is not None and float(s.refund_amount) >= cfg.risk_amount_threshold:
        reasons.append(f"金额 {s.refund_amount} ≥ {cfg.risk_amount_threshold}")
    kw = _match_any(s.user_message, cfg.risk_keywords)
    if kw:
        reasons.append(f"投诉词：{kw}")
    if reasons:
        return TriggerResult.fire(TriggerType.RISK, "；".join(reasons), skip_self_service=True)
    return TriggerResult.none()


def check_system_error(s: TurnSignals, cfg: Settings) -> TriggerResult:
    if s.tool_failure_streak >= cfg.tool_failure_threshold:
        return TriggerResult.fire(
            TriggerType.SYSTEM_ERROR,
            f"工具连续失败 {s.tool_failure_streak} 次",
        )
    if s.schema_retries_exhausted:
        return TriggerResult.fire(TriggerType.SYSTEM_ERROR, "输出校验重放耗尽")
    if s.llm_timeout_streak >= 2:
        return TriggerResult.fire(TriggerType.SYSTEM_ERROR, f"LLM 连续超时 {s.llm_timeout_streak} 次")
    return TriggerResult.none()


def check_boundary(s: TurnSignals, cfg: Settings) -> TriggerResult:
    if s.last_tool_status in (ToolStatus.NOT_FOUND, ToolStatus.EMPTY):
        return TriggerResult.fire(TriggerType.BOUNDARY, f"工具返回 {s.last_tool_status.value}")
    if s.rag_top_similarity is not None and s.rag_top_similarity < cfg.rag_similarity_threshold:
        return TriggerResult.fire(
            TriggerType.BOUNDARY,
            f"检索相似度 {s.rag_top_similarity} < {cfg.rag_similarity_threshold}",
        )
    if s.llm_cannot_handle:
        return TriggerResult.fire(TriggerType.BOUNDARY, "模型判定无法处理")
    return TriggerResult.none()


CHECKS = (check_user_request, check_risk, check_system_error, check_boundary)


def evaluate(s: TurnSignals, cfg: Settings | None = None) -> TriggerResult:
    c = cfg or settings
    for check in CHECKS:
        r = check(s, c)
        if r.triggered:
            return r
    return TriggerResult.none()

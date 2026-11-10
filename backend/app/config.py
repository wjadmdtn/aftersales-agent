"""T004: 配置。非法值在启动阶段失败。"""
from __future__ import annotations

import json

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MOCK_", env_file=".env", extra="ignore"
    )

    # --- mock 服务 ---
    seed_path: str = "seed/orders.json"
    fault_enabled: bool = True
    caller_timeout_ms: int = Field(default=3000, gt=0)
    refund_lock_shards: int = Field(default=16, ge=1)
    call_record_capacity: int = Field(default=10000, ge=1)
    debug_endpoints_enabled: bool = True
    log_level: str = "INFO"

    # --- agent ---
    # 内网模型响应较慢，超时给的宽松些；过短会导致大量请求被误判为降级
    llm_timeout_ms: int = Field(default=120000, gt=0)
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = Field(default=86400, gt=0)
    confirm_ttl_seconds: int = Field(default=300, gt=0)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    mock_service_base_url: str = "http://localhost:8000"

    # 关键词仅作为提示传给 LLM，不参与意图判定
    keyword_hints_raw: str = Field(default='{}')
    # 降级期使用的宽写词表：比正常词表更宽，宁可误拒只读
    degrade_write_keywords_raw: str = Field(
        default='["退","退款","退货","退钱","退换","申请退","改","改地址","修改","换地址","更换","取消","撤销"]'
    )
    # 确认词必须精确匹配，不能包含匹配
    confirm_words_raw: str = Field(
        default='["确认","确定","是的","对","好的","可以","同意"]'
    )


    # --- handoff (feature 003) ---
    risk_amount_threshold: float = Field(default=500.0, ge=0)
    risk_keywords_raw: str = Field(
        default='["投诉","消协","12315","曝光","举报","起诉","差评","骗子","欺诈"]'
    )
    user_request_keywords_raw: str = Field(
        default='["转人工","人工客服","人工","找真人","真人","找客服"]'
    )
    angry_keywords_raw: str = Field(
        default='["垃圾","骗子","太差了","恶心","差评","坑人","废物","滚","什么破"]'
    )
    negative_keywords_raw: str = Field(default='["不满意","生气","失望","麻烦","怎么回事","不好用","不好使","用不了","有问题","坏了","划痕","破损","质量差","太慢","等太久","和图片不一样","是坏的","废品","完全不能用","等半天","效率太低","让人无语","不太行","质量不行","跟描述","不一样"]')
    tool_failure_threshold: int = Field(default=3, ge=1)
    rag_similarity_threshold: float = Field(default=0.6, ge=0, le=1)
    handoff_scripts_raw: str = Field(
        default="""{
  "user_request": "好的，正在为您接入人工客服，请稍候。",
  "risk": "非常抱歉给您带来了不好的体验，已为您优先接入人工客服，请稍候。",
  "system_error": "抱歉，系统这边出了点状况，已为您接入人工客服协助处理，请稍候。",
  "boundary": "这个问题我这边没能处理，已为您接入人工客服，请稍候。",
  "queued_followup": "正在为您接入人工客服，请稍候～",
  "queued_ask_time": "正在为您排队接入人工客服，接通的瞬间会自动通知您～",
  "declined": "非常抱歉，这个问题需要升级到专员为您跟进，稍后会有同事联系您。"
}"""
    )

    @field_validator("risk_keywords_raw", "user_request_keywords_raw", "angry_keywords_raw",
                     "negative_keywords_raw", "handoff_scripts_raw")
    @classmethod
    def _validate_json_handoff(cls, v: str) -> str:
        json.loads(v)
        return v

    @property
    def risk_keywords(self) -> tuple[str, ...]:
        return tuple(json.loads(self.risk_keywords_raw))

    @property
    def user_request_keywords(self) -> tuple[str, ...]:
        return tuple(json.loads(self.user_request_keywords_raw))

    @property
    def angry_keywords(self) -> tuple[str, ...]:
        return tuple(json.loads(self.angry_keywords_raw))

    @property
    def negative_keywords(self) -> tuple[str, ...]:
        return tuple(json.loads(self.negative_keywords_raw))

    @property
    def handoff_scripts(self) -> dict[str, str]:
        return json.loads(self.handoff_scripts_raw)

    @field_validator("keyword_hints_raw", "degrade_write_keywords_raw", "confirm_words_raw")
    @classmethod
    def _validate_json(cls, v: str) -> str:
        json.loads(v)
        return v

    @property
    def keyword_hints(self) -> dict[str, list[str]]:
        return json.loads(self.keyword_hints_raw)

    @property
    def degrade_write_keywords(self) -> tuple[str, ...]:
        return tuple(json.loads(self.degrade_write_keywords_raw))

    @property
    def confirm_words(self) -> frozenset[str]:
        return frozenset(json.loads(self.confirm_words_raw))


settings = Settings()

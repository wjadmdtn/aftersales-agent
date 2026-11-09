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
    llm_timeout_ms: int = Field(default=3000, gt=0)
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

"""T008: 配置。非法值在启动阶段失败。"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MOCK_", env_file=".env", extra="ignore"
    )

    seed_path: str = "seed/orders.json"
    fault_enabled: bool = True
    caller_timeout_ms: int = Field(default=3000, gt=0)
    refund_lock_shards: int = Field(default=16, ge=1)
    call_record_capacity: int = Field(default=10000, ge=1)
    debug_endpoints_enabled: bool = True
    log_level: str = "INFO"


settings = Settings()

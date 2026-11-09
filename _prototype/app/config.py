"""Risk and handoff rules, all overridable by environment."""
import json
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env(field: str) -> AliasChoices:
    """Accept both the prefixed name and an explicit HANDOFF_* override."""
    return AliasChoices(f"HANDOFF_{field.upper()}", field)


class HandoffRules(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HANDOFF_", extra="ignore", populate_by_name=True
    )

    # ② risk trigger
    risk_amount_threshold: float = Field(default=500.0, ge=0)
    risk_keywords_raw: str = Field(
        default='["投诉","消协","12315","曝光","举报","起诉","差评","骗子","欺诈"]',
        validation_alias=_env("risk_keywords"),
    )
    angry_keywords_raw: str = Field(
        default='["垃圾","什么破","太差","恶心","滚","废物","投诉你们"]',
        validation_alias=_env("angry_keywords"),
    )

    # ③ user request (exact match, no semantic guessing)
    user_request_keywords_raw: str = Field(
        default='["转人工","人工客服","人工","找真人","真人","找客服","客服电话"]',
        validation_alias=_env("user_request_keywords"),
    )

    # ④ system error
    tool_failure_threshold: int = Field(default=3, ge=1)
    schema_retry_limit: int = Field(default=2, ge=0)
    llm_timeout_threshold: int = Field(default=2, ge=1)

    # ① boundary
    rag_similarity_threshold: float = Field(default=0.6, ge=0, le=1)

    # queue
    expected_wait_seconds: int = Field(default=120, ge=1)
    queue_timeout_multiplier: float = Field(default=2.0, ge=1)

    # 4. human unavailable -> decline
    human_unavailable_keywords_raw: str = Field(
        default='["不归我管","非工作","已下班","转错","不受理"]',
        validation_alias=_env("human_decline_keywords"),
    )

    @field_validator("risk_keywords_raw", "angry_keywords_raw", "user_request_keywords_raw", "human_unavailable_keywords_raw")
    @classmethod
    def _validate_json_list(cls, v: str) -> str:
        parsed = json.loads(v)
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            raise ValueError("must be a JSON array of strings")
        return v

    @property
    def risk_keywords(self) -> tuple[str, ...]:
        return tuple(json.loads(self.risk_keywords_raw))

    @property
    def angry_keywords(self) -> tuple[str, ...]:
        return tuple(json.loads(self.angry_keywords_raw))

    @property
    def user_request_keywords(self) -> tuple[str, ...]:
        return tuple(json.loads(self.user_request_keywords_raw))

    @property
    def human_unavailable_keywords(self) -> tuple[str, ...]:
        return tuple(json.loads(self.human_unavailable_keywords_raw))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    tool_timeout_seconds: float = 3.0

    handoff: HandoffRules = Field(default_factory=HandoffRules)


settings = Settings()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

def example_env() -> dict[str, str]:
    r = settings.handoff
    return {
        "HANDOFF_RISK_AMOUNT_THRESHOLD": str(r.risk_amount_threshold),
        "HANDOFF_RISK_KEYWORDS": r.risk_keywords_raw,
        "HANDOFF_ANGRY_KEYWORDS": r.angry_keywords_raw,
        "HANDOFF_USER_REQUEST_KEYWORDS": r.user_request_keywords_raw,
        "HANDOFF_HUMAN_DECLINE_KEYWORDS": r.human_unavailable_keywords_raw,
        "HANDOFF_TOOL_FAILURE_THRESHOLD": str(r.tool_failure_threshold),
        "HANDOFF_SCHEMA_RETRY_LIMIT": str(r.schema_retry_limit),
        "HANDOFF_LLM_TIMEOUT_THRESHOLD": str(r.llm_timeout_threshold),
        "HANDOFF_RAG_SIMILARITY_THRESHOLD": str(r.rag_similarity_threshold),
        "HANDOFF_EXPECTED_WAIT_SECONDS": str(r.expected_wait_seconds),
        "HANDOFF_QUEUE_TIMEOUT_MULTIPLIER": str(r.queue_timeout_multiplier),
        "APP_REDIS_URL": settings.redis_url,
    }

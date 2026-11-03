"""Application settings. Secrets are SecretStr and never logged."""

from decimal import Decimal
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    ads_provider: Literal["mock", "amazon"] = "mock"
    # Safety default: real ad changes are disabled. See docs/SECURITY.md.
    ad_execution_enabled: bool = False
    database_url: SecretStr | None = None
    db_app_role: str | None = None
    # --- LLM (M4). Off by default: nothing is sent to any provider unless explicitly configured.
    llm_provider: Literal["none", "openai"] = "none"
    llm_model: str = ""
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    # Prices are configuration (vendors change them). Required for any real provider, because a
    # spend cap cannot be enforced without them.
    llm_input_usd_per_million: Decimal | None = None
    llm_output_usd_per_million: Decimal | None = None
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3
    # Hard per-organization caps; calls that could breach them are refused before being made.
    assistant_questions_per_minute: int = 10  # per tenant, per process
    llm_daily_cap_usd: Decimal = Decimal("1")
    llm_monthly_cap_usd: Decimal = Decimal("5")
    # --- Knowledge retrieval (M4). "hashing" is an offline stand-in for development and tests only.
    embedding_provider: Literal["none", "hashing", "openai"] = "none"
    embedding_model: str = ""
    embedding_usd_per_million: Decimal | None = None
    mock_seed: int = 42
    mock_history_days: int = 120
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:5173"]

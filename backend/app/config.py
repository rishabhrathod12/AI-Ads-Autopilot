"""Application settings. Secrets are SecretStr and never logged."""

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
    mock_seed: int = 42
    mock_history_days: int = 120
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:5173"]

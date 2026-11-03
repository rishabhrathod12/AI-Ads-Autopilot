"""Fail-safe startup validation for ad execution (see ADR 0007)."""

from dataclasses import dataclass

from app.config import Settings


class UnsafeConfigurationError(RuntimeError):
    """Raised when the configuration would allow unsafe ad execution."""


@dataclass(frozen=True)
class ExecutionPrerequisites:
    """Which execution safeguards actually exist in this build. None exist in M1."""

    approval_service: bool = False
    authorization_checks: bool = False
    audit_sink: bool = False


def validate_auth_readiness(settings: Settings) -> None:
    """Real authentication does not exist yet, so production must not start (ADR 0008).

    Until it does, tenants are identified by a development-only header; serving real traffic that
    way would let any caller read any seller's data.
    """
    if settings.app_env == "production":
        raise UnsafeConfigurationError(
            "APP_ENV=production refused: real authentication is not implemented yet. "
            "It is required before any non-local deployment."
        )


def validate_llm_config(settings: Settings) -> None:
    """A real LLM provider must come with the means to meter and cap its cost, and a safe URL."""
    if settings.llm_provider == "none":
        return
    problems: list[str] = []
    if settings.openai_api_key is None:
        problems.append("OPENAI_API_KEY is not set")
    if not settings.llm_model:
        problems.append("LLM_MODEL is not set")
    if settings.llm_input_usd_per_million is None or settings.llm_output_usd_per_million is None:
        problems.append(
            "LLM_INPUT_USD_PER_MILLION and LLM_OUTPUT_USD_PER_MILLION are not set "
            "(the spend cap needs them)"
        )
    url = settings.openai_base_url
    if not url.startswith("https://") and not url.startswith(
        ("http://localhost", "http://127.0.0.1")
    ):
        problems.append("OPENAI_BASE_URL must be https (the API key is sent with every request)")
    if settings.llm_max_retries < 0 or settings.llm_timeout_seconds <= 0:
        problems.append("LLM_MAX_RETRIES must be >= 0 and LLM_TIMEOUT_SECONDS > 0")
    if settings.llm_daily_cap_usd <= 0 or settings.llm_monthly_cap_usd <= 0:
        problems.append("LLM spend caps must be positive")
    if problems:
        raise UnsafeConfigurationError("LLM_PROVIDER=openai refused: " + "; ".join(problems))


def validate_embedding_config(settings: Settings) -> None:
    """A paid embedding provider needs the key, a model, and a price so its spend can be capped."""
    if settings.embedding_provider != "openai":
        return
    problems: list[str] = []
    if settings.openai_api_key is None:
        problems.append("OPENAI_API_KEY is not set")
    if not settings.embedding_model:
        problems.append("EMBEDDING_MODEL is not set")
    if settings.embedding_usd_per_million is None:
        problems.append("EMBEDDING_USD_PER_MILLION is not set (the spend cap needs it)")
    url = settings.openai_base_url
    if not url.startswith("https://") and not url.startswith(
        ("http://localhost", "http://127.0.0.1")
    ):
        problems.append("OPENAI_BASE_URL must be https (the API key is sent with every request)")
    if problems:
        raise UnsafeConfigurationError("EMBEDDING_PROVIDER=openai refused: " + "; ".join(problems))


def validate_execution_safety(settings: Settings, prereqs: ExecutionPrerequisites) -> None:
    if not settings.ad_execution_enabled:
        return
    problems: list[str] = []
    if settings.ads_provider == "mock":
        problems.append("ADS_PROVIDER is 'mock'; mock data can never execute real actions")
    if not prereqs.approval_service:
        problems.append("approval service is not available")
    if not prereqs.authorization_checks:
        problems.append("authorization checks are not available")
    if not prereqs.audit_sink:
        problems.append("audit sink is not available")
    if problems:
        raise UnsafeConfigurationError("AD_EXECUTION_ENABLED=true refused: " + "; ".join(problems))

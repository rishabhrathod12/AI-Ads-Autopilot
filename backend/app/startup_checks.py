"""Fail-safe startup validation for ad execution (see ADR 0007)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.domain.tenant import TenantContext
from app.infrastructure.ads_api.token_cipher import CipherConfigError, parse_keys
from app.infrastructure.auth.jwt_verifier import ASYMMETRIC_ALGORITHMS
from app.infrastructure.db.session import tenant_session


class UnsafeConfigurationError(RuntimeError):
    """Raised when the configuration would allow unsafe ad execution."""


@dataclass(frozen=True)
class ExecutionPrerequisites:
    """Which execution safeguards actually exist in this build. None exist in M1."""

    approval_service: bool = False
    authorization_checks: bool = False
    audit_sink: bool = False


def validate_auth_readiness(settings: Settings) -> None:
    """Authentication must be fully configured, or absent outside production (ADR 0008, 0021).

    Without it, tenants are identified by a development-only header; serving real traffic that way
    would let any caller read any seller's data. A *partial* configuration is refused everywhere:
    it would silently fall back to that header.
    """
    set_fields = {
        "AUTH_JWT_ISSUER": bool(settings.auth_jwt_issuer),
        "AUTH_JWT_AUDIENCE": bool(settings.auth_jwt_audience),
        "AUTH_JWKS_URL": bool(settings.auth_jwks_url),
    }
    if any(set_fields.values()) and not all(set_fields.values()):
        missing = ", ".join(k for k, v in set_fields.items() if not v)
        raise UnsafeConfigurationError(
            f"authentication is partly configured (missing: {missing}); set all three or none"
        )
    if settings.auth_configured:
        url = settings.auth_jwks_url
        local = url.startswith(("http://localhost", "http://127.0.0.1"))
        if not url.startswith("https://") and not (local and settings.app_env != "production"):
            raise UnsafeConfigurationError("AUTH_JWKS_URL must be https")
        bad = set(settings.auth_jwt_algorithms) - ASYMMETRIC_ALGORITHMS
        if bad or not settings.auth_jwt_algorithms:
            raise UnsafeConfigurationError(
                f"AUTH_JWT_ALGORITHMS must be asymmetric algorithms only, got {sorted(bad)}"
            )
        return
    if settings.app_env == "production":
        raise UnsafeConfigurationError(
            "APP_ENV=production refused: real authentication is not configured "
            "(set AUTH_JWT_ISSUER, AUTH_JWT_AUDIENCE and AUTH_JWKS_URL)."
        )


def validate_amazon_ads_config(settings: Settings) -> None:
    """Real Amazon connectivity needs the whole configuration, or none of it.

    ``ADS_PROVIDER=amazon`` and ``AMAZON_ADS_ENABLED`` must agree, every credential and URL must be
    present, URLs must be https, and the refresh-token encryption keys must be usable. Execution of
    changes is checked separately by ``validate_execution_safety`` and stays disabled.
    """
    wants_amazon = settings.ads_provider == "amazon"
    if wants_amazon != settings.amazon_ads_enabled:
        raise UnsafeConfigurationError(
            "ADS_PROVIDER=amazon and AMAZON_ADS_ENABLED=true must be set together (or neither)"
        )
    if not wants_amazon:
        return
    problems: list[str] = []
    if not settings.amazon_ads_client_id:
        problems.append("AMAZON_ADS_CLIENT_ID is not set")
    secret = settings.amazon_ads_client_secret
    if secret is None or not secret.get_secret_value():
        problems.append("AMAZON_ADS_CLIENT_SECRET is not set")
    for name, url in (
        ("AMAZON_ADS_TOKEN_URL", settings.amazon_ads_token_url),
        ("AMAZON_ADS_API_BASE_URL", settings.amazon_ads_api_base_url),
    ):
        if not url.startswith("https://"):
            problems.append(f"{name} must be set and https")
    if settings.amazon_ads_requests_per_second <= 0 or settings.amazon_ads_max_retries < 0:
        problems.append("AMAZON_ADS_REQUESTS_PER_SECOND must be > 0 and MAX_RETRIES >= 0")
    keys_raw = settings.token_encryption_keys
    if keys_raw is None or not keys_raw.get_secret_value():
        problems.append("TOKEN_ENCRYPTION_KEYS is not set (refresh tokens are encrypted at rest)")
    else:
        try:
            keys = parse_keys(keys_raw.get_secret_value())
            if settings.token_encryption_active_kid not in keys:
                problems.append("TOKEN_ENCRYPTION_ACTIVE_KID is not one of the configured key ids")
        except CipherConfigError as exc:
            problems.append(str(exc))
    if problems:
        raise UnsafeConfigurationError("ADS_PROVIDER=amazon refused: " + "; ".join(problems))


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


async def assert_rls_enforced(engine: AsyncEngine, app_role: str | None) -> None:
    """Refuse to serve if the effective database role could bypass row-level security.

    A superuser or BYPASSRLS role ignores every tenant policy, so one forgotten ``SET ROLE`` would
    expose all tenants. This checks the role the application's queries really run as (after the same
    ``SET LOCAL ROLE`` it uses per request), not the role named in configuration.
    """
    async with tenant_session(engine, TenantContext(uuid.UUID(int=0)), app_role) as session:
        row = (
            await session.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    if row.rolsuper or row.rolbypassrls:
        raise UnsafeConfigurationError(
            "the database role the application runs as can bypass row-level security "
            "(superuser or BYPASSRLS); connect as a limited role or set DB_APP_ROLE to one"
        )

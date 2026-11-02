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

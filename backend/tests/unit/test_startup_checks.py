import pytest

from app.api.main import create_app
from app.config import Settings
from app.startup_checks import (
    ExecutionPrerequisites,
    UnsafeConfigurationError,
    validate_execution_safety,
)

ALL = ExecutionPrerequisites(approval_service=True, authorization_checks=True, audit_sink=True)


def test_defaults_are_safe() -> None:
    s = Settings(_env_file=None)
    assert s.ad_execution_enabled is False
    assert s.ads_provider == "mock"
    validate_execution_safety(s, ExecutionPrerequisites())


def test_execution_with_mock_provider_is_refused() -> None:
    s = Settings(ad_execution_enabled=True, ads_provider="mock", _env_file=None)
    with pytest.raises(UnsafeConfigurationError, match="mock"):
        validate_execution_safety(s, ALL)


def test_execution_without_prerequisites_is_refused() -> None:
    s = Settings(ad_execution_enabled=True, ads_provider="amazon", _env_file=None)
    with pytest.raises(UnsafeConfigurationError) as exc:
        validate_execution_safety(s, ExecutionPrerequisites())
    msg = str(exc.value)
    assert "approval service" in msg
    assert "authorization checks" in msg
    assert "audit sink" in msg


def test_execution_allowed_only_when_everything_present() -> None:
    s = Settings(ad_execution_enabled=True, ads_provider="amazon", _env_file=None)
    validate_execution_safety(s, ALL)


def test_app_refuses_to_start_when_unsafe() -> None:
    s = Settings(ad_execution_enabled=True, ads_provider="mock", _env_file=None)
    with pytest.raises(UnsafeConfigurationError):
        create_app(s)


def test_app_cannot_enable_execution_in_m1() -> None:
    # No safeguards exist yet in M1, so even a non-mock provider must be refused.
    s = Settings(ad_execution_enabled=True, ads_provider="amazon", _env_file=None)
    with pytest.raises(UnsafeConfigurationError):
        create_app(s)

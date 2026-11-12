"""Errors of the Amazon Ads integration layer. Messages never carry tokens, headers or bodies."""


class AdsApiError(Exception):
    """Base class. ``str(exc)`` is safe to log."""


class NotConfiguredError(AdsApiError):
    """A required setting or mapping is missing; nothing was sent."""


class AuthenticationError(AdsApiError):
    """Credentials were rejected (after one token refresh where applicable)."""


class RateLimitedError(AdsApiError):
    """The server asked us to slow down. ``retry_after`` is seconds, when the server said so."""

    def __init__(self, message: str = "rate limited", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(AdsApiError):
    """Timeouts, connection failures, 5xx: worth retrying."""


class PermanentError(AdsApiError):
    """A 4xx that retrying will not fix."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

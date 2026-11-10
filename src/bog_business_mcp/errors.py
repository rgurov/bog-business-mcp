"""Exception hierarchy for the BoG Business Online client."""

from __future__ import annotations


class BogError(Exception):
    """Base class for all errors raised by this package."""


class ConfigError(BogError):
    """Invalid or missing configuration (environment variables)."""


class AuthError(BogError):
    """Failed to obtain an access token from the BoG identity server."""


class BogApiError(BogError):
    """Non-success response from the Business Online API."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        path: str = "",
        correlation_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.path = path
        self.correlation_id = correlation_id
        details = f"HTTP {status_code}"
        if path:
            details += f" on {path}"
        if correlation_id:
            details += f" (x-correlationid: {correlation_id})"
        super().__init__(f"{message} [{details}]" if message else details)


class RateLimitedError(BogError):
    """The request was (or would be) rejected by the BoG rate limiter."""

    def __init__(self, message: str, retry_after: int) -> None:
        self.retry_after = max(1, int(retry_after))
        super().__init__(f"{message}; retry after {self.retry_after}s")

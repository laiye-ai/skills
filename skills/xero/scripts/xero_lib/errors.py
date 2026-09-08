"""Stable, user-safe errors returned by the skill."""

from dataclasses import dataclass


XERO_AUTH_FAILED = "XERO_AUTH_FAILED"
XERO_INSUFFICIENT_SCOPE = "XERO_INSUFFICIENT_SCOPE"
XERO_NOT_FOUND = "XERO_NOT_FOUND"
XERO_RESPONSE_INVALID = "XERO_RESPONSE_INVALID"
XERO_TRANSIENT_FAILURE = "XERO_TRANSIENT_FAILURE"
XERO_VALIDATION_FAILED = "XERO_VALIDATION_FAILED"


@dataclass
class AppError(Exception):
    """A recoverable application error suitable for CLI serialization."""

    code: str
    message: str
    details: dict[str, object] | None = None
    invoice_id: str | None = None
    final_status: str | None = None
    tenant_id: str | None = None
    tenant_name: str | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

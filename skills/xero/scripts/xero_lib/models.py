"""Validated input models for creating Xero bills."""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from .errors import AppError


INVALID_FILENAME_CHARS = frozenset('<>:"/\\|?*\x00+')
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class ItemRequest:
    code: str
    qty: Decimal


@dataclass(frozen=True)
class AttachmentRequest:
    path: Path
    filename: str
    mime_type: str
    size: int


@dataclass(frozen=True)
class BillRequest:
    contact_name: str
    reference: str
    permit_number: str
    date: date
    due_date: date
    items: tuple[ItemRequest, ...]
    attachments: tuple[AttachmentRequest, ...]

    @classmethod
    def from_path(
        cls, path: Path, today: Callable[[], date] = date.today
    ) -> "BillRequest":
        source = Path(path).resolve()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise AppError("MALFORMED_JSON", "The bill request must be valid JSON.") from error
        except (OSError, UnicodeDecodeError) as error:
            raise AppError("INVALID_REQUEST", "The bill request could not be read.") from error
        return parse_bill_request(payload, source.parent, today())


@dataclass(frozen=True)
class CommandResult:
    """Known bill-operation state for later CLI JSON serialization."""

    success: bool
    message: str
    details: dict[str, object] | None = None
    invoice_id: str | None = None
    invoice_number: str | None = None
    permit_number: str | None = None
    final_status: str | None = None
    tenant_id: str | None = None
    tenant_name: str | None = None
    skipped_items: tuple["SkippedItem", ...] = ()
    attachments: tuple[dict[str, object], ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the workflow reached a known, successful outcome."""
        return self.success

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"ok": self.success, "message": self.message}
        if self.details is not None:
            result["details"] = self.details.copy()
        if self.invoice_id is not None:
            result["invoice_id"] = self.invoice_id
        if self.invoice_number is not None:
            result["invoice_number"] = self.invoice_number
        if self.permit_number is not None:
            result["permit_number"] = self.permit_number
        if self.final_status is not None:
            result["final_status"] = self.final_status
        if self.tenant_id is not None:
            result["tenant_id"] = self.tenant_id
        if self.tenant_name is not None:
            result["tenant_name"] = self.tenant_name
        if self.skipped_items:
            result["skipped_items"] = [item.to_dict() for item in self.skipped_items]
        if self.attachments:
            result["attachments"] = [attachment.copy() for attachment in self.attachments]
        if self.warnings:
            result["warnings"] = list(self.warnings)
        return result


@dataclass(frozen=True)
class SkippedItem:
    """An item that could not safely form a Xero purchase line."""

    code: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class TokenSet:
    """OAuth credentials stored only by the token-storage layer."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    scope: str

    def to_dict(self) -> dict[str, str]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, value: object) -> "TokenSet":
        if not isinstance(value, dict):
            raise ValueError("The stored token bundle must be an object.")
        raw_access_value = value.get("access_token")
        raw_refresh_value = value.get("refresh_token")
        expires_at = value.get("expires_at")
        scope = value.get("scope")
        if not all(isinstance(field, str) for field in (raw_access_value, raw_refresh_value, expires_at, scope)):
            raise ValueError("The stored token bundle is invalid.")
        try:
            parsed_expiry = datetime.fromisoformat(expires_at)
        except ValueError as error:
            raise ValueError("The stored token expiry is invalid.") from error
        return cls(raw_access_value, raw_refresh_value, parsed_expiry, scope)


def parse_bill_request(payload: object, base_path: Path, today: date) -> BillRequest:
    if not isinstance(payload, dict):
        raise AppError("INVALID_REQUEST", "The bill request must be a JSON object.")

    contact_name = _required_text(payload, "from")
    reference = _required_text(payload, "reference")
    permit_number = _required_text(payload, "permit_number")
    request_date = _parse_date(payload.get("date", today), "date")
    due_date = _parse_date(payload.get("due_date", request_date), "due_date")
    items = _parse_items(payload)
    attachments = _parse_attachments(payload, base_path)
    return BillRequest(
        contact_name=contact_name,
        reference=reference,
        permit_number=permit_number,
        date=request_date,
        due_date=due_date,
        items=items,
        attachments=attachments,
    )


def _required_text(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AppError("MISSING_FIELD", f"'{field}' is required.", {"field": field})
    return value.strip()


def _parse_date(value: object, field: str) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise AppError("INVALID_DATE", f"'{field}' must be an ISO-8601 date.", {"field": field})
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise AppError("INVALID_DATE", f"'{field}' must be an ISO-8601 date.", {"field": field}) from error


def _parse_items(payload: dict[str, object]) -> tuple[ItemRequest, ...]:
    if "items" not in payload:
        raise AppError("MISSING_FIELD", "'items' is required.", {"field": "items"})
    values = payload["items"]
    if not isinstance(values, list):
        raise AppError("INVALID_FIELD", "'items' must be a list.", {"field": "items"})
    if not values:
        raise AppError("NO_ITEMS", "At least one item is required.")

    items: list[ItemRequest] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise AppError("INVALID_FIELD", "Each item must be an object.", {"field": "items", "index": index})
        code = _required_text(value, "code")
        if "qty" not in value:
            raise AppError("MISSING_FIELD", "'qty' is required.", {"field": "qty", "index": index})
        items.append(ItemRequest(code=code, qty=_parse_quantity(value["qty"], index)))
    return tuple(items)


def _parse_quantity(value: object, index: int) -> Decimal:
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise AppError("INVALID_QUANTITY", "Item quantity must be a finite positive number.", {"index": index}) from error
    if not quantity.is_finite() or quantity <= 0:
        raise AppError("INVALID_QUANTITY", "Item quantity must be a finite positive number.", {"index": index})
    return quantity


def _parse_attachments(payload: dict[str, object], base_path: Path) -> tuple[AttachmentRequest, ...]:
    if "attachments" not in payload:
        raise AppError("MISSING_FIELD", "'attachments' is required.", {"field": "attachments"})
    values = payload["attachments"]
    if not isinstance(values, list):
        raise AppError("INVALID_FIELD", "'attachments' must be a list.", {"field": "attachments"})
    if not values:
        raise AppError("NO_ATTACHMENTS", "At least one attachment is required.")
    if len(values) > MAX_ATTACHMENTS:
        raise AppError("TOO_MANY_ATTACHMENTS", "At most ten attachments are allowed.")

    attachments: list[AttachmentRequest] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value:
            raise AppError("INVALID_FILENAME", "Each attachment must have a valid filename.", {"index": index})
        supplied_path = Path(value)
        attachment_path = (
            supplied_path.resolve()
            if supplied_path.is_absolute()
            else (base_path / supplied_path).resolve()
        )
        filename = attachment_path.name
        if not filename or any(character in INVALID_FILENAME_CHARS for character in filename):
            raise AppError("INVALID_FILENAME", "Attachment filename contains unsupported characters.", {"index": index})
        if not attachment_path.exists():
            raise AppError("ATTACHMENT_NOT_FOUND", "Attachment file was not found.", {"filename": value})
        if not attachment_path.is_file():
            raise AppError("ATTACHMENT_NOT_FILE", "Attachment path must be a file.", {"filename": value})
        size = attachment_path.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            raise AppError("ATTACHMENT_TOO_LARGE", "Attachment exceeds the 10 MiB limit.", {"filename": value})
        attachments.append(
            AttachmentRequest(
                path=attachment_path,
                filename=filename,
                mime_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                size=size,
            )
        )
    result = tuple(attachments)
    ensure_unique_attachment_filenames(result)
    return result


def ensure_unique_attachment_filenames(
    attachments: tuple[AttachmentRequest, ...],
) -> None:
    """Reject names Xero would treat as replacement uploads."""
    seen: set[str] = set()
    for attachment in attachments:
        normalized = attachment.filename.casefold()
        if normalized in seen:
            raise AppError(
                "DUPLICATE_ATTACHMENT_FILENAME",
                "Attachment filenames must be unique within one bill.",
                {"filename": attachment.filename},
            )
        seen.add(normalized)

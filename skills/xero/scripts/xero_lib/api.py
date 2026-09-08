"""Authenticated, retry-safe access to the Xero Accounting API."""

from __future__ import annotations

import math
import random
import re
import secrets
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .auth import AuthSession
from .errors import (
    AppError,
    XERO_AUTH_FAILED,
    XERO_INSUFFICIENT_SCOPE,
    XERO_NOT_FOUND,
    XERO_RESPONSE_INVALID,
    XERO_TRANSIENT_FAILURE,
    XERO_VALIDATION_FAILED,
)
from .models import AttachmentRequest
from .reconciliation import CreateAttemptStore, reconciliation_details


API_BASE_URL = "https://api.xero.com"
MAX_RETRIES = 3
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
SENSITIVE_TEXT = re.compile(r"(?:bearer\s+\S+|(?:access|refresh)[_-]?token\s*[:=]\s*\S+)", re.IGNORECASE)


def new_idempotency_key() -> str:
    """Return a Xero-compatible 128-character hexadecimal idempotency key."""
    return secrets.token_hex(64)


class XeroClient:
    """The sole Accounting API boundary used by the bill workflow."""

    def __init__(
        self,
        session_provider: Callable[[], AuthSession],
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        random_source: Callable[[], float] | None = None,
        create_attempt_store: CreateAttemptStore | None = None,
        pending_create_retry_id: str | None = None,
    ) -> None:
        self._session_provider = session_provider
        self._client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )
        self._sleep = sleep or __import__("time").sleep
        self._random = random_source or random.random
        self._create_attempt_store = create_attempt_store or CreateAttemptStore()
        self._pending_create_retry_id = pending_create_retry_id

    def session_context(self) -> AuthSession:
        """Return the authoritative tenant context used for this operation."""
        return self._session_provider()

    def find_contacts(self, search_term: str) -> list[dict[str, Any]]:
        query = urlencode({"SearchTerm": search_term})
        return self._request_objects("GET", f"/api.xro/2.0/Contacts?{query}", "Contacts")

    def get_item(self, code: str) -> dict[str, Any]:
        return self._request_object("GET", f"/api.xro/2.0/Items/{quote(code, safe='')}", "Items")

    def create_draft(self, payload: Mapping[str, object]) -> dict[str, Any]:
        session = self._session_provider()
        with self._create_attempt_store.claim(
            session.tenant_id,
            payload,
            retry_attempt_id=self._pending_create_retry_id,
        ) as lease:
            try:
                invoice = self._request_object(
                    "POST",
                    "/api.xro/2.0/Invoices",
                    "Invoices",
                    json_body=dict(payload),
                    session=session,
                    idempotency_key=lease.attempt.idempotency_key,
                )
            except AppError as error:
                if error.code in {
                    XERO_RESPONSE_INVALID,
                    XERO_TRANSIENT_FAILURE,
                    "XERO_REQUEST_FAILED",
                }:
                    details = reconciliation_details(lease.attempt)
                    details["cause_code"] = error.code
                    if error.details:
                        details["xero_details"] = error.details
                    raise AppError(
                        "CREATE_OUTCOME_UNKNOWN",
                        (
                            "Xero may have created the draft, but no definitive create "
                            "response was received. Do not start a fresh create."
                        ),
                        details,
                        final_status="UNKNOWN",
                        tenant_id=session.tenant_id,
                        tenant_name=session.tenant_name,
                    ) from error
                lease.complete()
                raise
            try:
                lease.complete()
            except AppError as error:
                invoice_id = invoice.get("InvoiceID")
                status = invoice.get("Status")
                raise AppError(
                    error.code,
                    error.message,
                    error.details,
                    invoice_id=invoice_id if isinstance(invoice_id, str) else None,
                    final_status=status if isinstance(status, str) else "DRAFT",
                    tenant_id=session.tenant_id,
                    tenant_name=session.tenant_name,
                ) from error
            return invoice

    def upload_attachment(self, invoice_id: str, attachment: AttachmentRequest) -> dict[str, Any]:
        try:
            content = attachment.path.read_bytes()
        except OSError as error:
            raise AppError("ATTACHMENT_READ_FAILED", "Attachment file could not be read.") from error
        path = "/api.xro/2.0/Invoices/{}/Attachments/{}".format(
            quote(invoice_id, safe=""), quote(attachment.filename, safe="")
        )
        return self._request_object(
            "POST", path, "Attachments", content=content, content_type=attachment.mime_type
        )

    def approve_invoice(self, invoice_id: str) -> dict[str, Any]:
        return self._request_object(
            "POST",
            f"/api.xro/2.0/Invoices/{quote(invoice_id, safe='')}",
            "Invoices",
            json_body={"InvoiceID": invoice_id, "Status": "AUTHORISED"},
        )

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        return self._request_object("GET", f"/api.xro/2.0/Invoices/{quote(invoice_id, safe='')}", "Invoices")

    def _request_object(
        self,
        method: str,
        path: str,
        object_key: str,
        *,
        json_body: dict[str, object] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
        session: AuthSession | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        session = session or self._session_provider()
        headers = {
            "Authorization": f"Bearer {session.access_token}",
            "xero-tenant-id": session.tenant_id,
            "Accept": "application/json",
        }
        if method in {"POST", "PUT"}:
            headers["Idempotency-Key"] = idempotency_key or new_idempotency_key()
        if content_type is not None:
            headers["Content-Type"] = content_type
        elif json_body is not None:
            headers["Content-Type"] = "application/json"

        request = self._client.build_request(
            method,
            f"{API_BASE_URL}{path}",
            headers=headers,
            json=json_body,
            content=content,
        )
        sensitive_values = (session.access_token,)
        include_validation_messages = content is None
        response = self._send_with_retry(request, sensitive_values, include_validation_messages)
        self._raise_for_error(response, sensitive_values, include_validation_messages)
        return _first_object(response, object_key)

    def _request_objects(
        self,
        method: str,
        path: str,
        object_key: str,
    ) -> list[dict[str, Any]]:
        session = self._session_provider()
        request = self._client.build_request(
            method,
            f"{API_BASE_URL}{path}",
            headers={
                "Authorization": f"Bearer {session.access_token}",
                "xero-tenant-id": session.tenant_id,
                "Accept": "application/json",
            },
        )
        sensitive_values = (session.access_token,)
        response = self._send_with_retry(request, sensitive_values, include_validation_messages=True)
        self._raise_for_error(response, sensitive_values, include_validation_messages=True)
        return _objects(response, object_key)

    def _send_with_retry(
        self,
        request: httpx.Request,
        sensitive_values: tuple[str, ...],
        include_validation_messages: bool,
    ) -> httpx.Response:
        for retry in range(MAX_RETRIES + 1):
            try:
                response = self._client.send(request)
            except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as error:
                if retry == MAX_RETRIES:
                    raise AppError(XERO_TRANSIENT_FAILURE, "Xero is temporarily unavailable.") from error
                self._sleep(_retry_delay(None, retry, self._random))
                continue
            if response.status_code not in RETRYABLE_STATUSES:
                return response
            if retry == MAX_RETRIES:
                raise _transient_error(response, sensitive_values, include_validation_messages)
            self._sleep(_retry_delay(response, retry, self._random))
        raise AssertionError("retry loop must return or raise")

    def _raise_for_error(
        self,
        response: httpx.Response,
        sensitive_values: tuple[str, ...],
        include_validation_messages: bool,
    ) -> None:
        if response.status_code < 400:
            return
        details = _safe_error_details(response, sensitive_values, include_validation_messages)
        if response.status_code == 400:
            raise AppError(XERO_VALIDATION_FAILED, "Xero rejected the request.", details)
        if response.status_code == 401 and _insufficient_scope(response):
            raise AppError(XERO_INSUFFICIENT_SCOPE, "Xero authorization lacks the required scope.", details)
        if response.status_code in {401, 403}:
            raise AppError(XERO_AUTH_FAILED, "Xero authorization failed.", details)
        if response.status_code == 404:
            raise AppError(XERO_NOT_FOUND, "The requested Xero resource was not found.", details)
        raise AppError("XERO_REQUEST_FAILED", "Xero rejected the request.", details)


def _retry_delay(response: httpx.Response | None, attempt: int, random_source: Callable[[], float]) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                seconds = float(retry_after)
            except ValueError:
                seconds = -1
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
    return min(8.0, float(2**attempt)) + max(0.0, random_source())


def _transient_error(
    response: httpx.Response,
    sensitive_values: tuple[str, ...],
    include_validation_messages: bool,
) -> AppError:
    return AppError(
        XERO_TRANSIENT_FAILURE,
        "Xero is temporarily unavailable.",
        _safe_error_details(response, sensitive_values, include_validation_messages),
    )


def _first_object(response: httpx.Response, object_key: str) -> dict[str, Any]:
    values = _objects(response, object_key)
    if not values:
        raise AppError(XERO_RESPONSE_INVALID, "Xero returned an invalid response.")
    return values[0]


def _objects(response: httpx.Response, object_key: str) -> list[dict[str, Any]]:
    try:
        payload = response.json()
    except ValueError as error:
        raise AppError(XERO_RESPONSE_INVALID, "Xero returned an invalid response.") from error
    values = payload.get(object_key) if isinstance(payload, dict) else None
    if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
        raise AppError(XERO_RESPONSE_INVALID, "Xero returned an invalid response.")
    return values


def _safe_error_details(
    response: httpx.Response,
    sensitive_values: tuple[str, ...],
    include_validation_messages: bool,
) -> dict[str, object] | None:
    details: dict[str, object] = {}
    correlation_id = response.headers.get("xero-correlation-id")
    if correlation_id and CORRELATION_ID_PATTERN.fullmatch(correlation_id):
        details["correlation_id"] = correlation_id
    if response.status_code == 400 and include_validation_messages:
        messages = _validation_messages(response, sensitive_values)
        if messages:
            details["validation_errors"] = messages
    return details or None


def _validation_messages(response: httpx.Response, sensitive_values: tuple[str, ...]) -> list[str]:
    try:
        payload = response.json()
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    messages: list[str] = []
    elements = payload.get("Elements")
    if not isinstance(elements, list):
        return messages
    for element in elements:
        if not isinstance(element, dict):
            continue
        errors = element.get("ValidationErrors")
        if not isinstance(errors, list):
            continue
        for error in errors:
            message = error.get("Message") if isinstance(error, dict) else None
            if isinstance(message, str) and message:
                messages.append(_sanitize_validation_message(message, sensitive_values))
    return messages[:20]


def _sanitize_validation_message(message: str, sensitive_values: tuple[str, ...]) -> str:
    sanitized = message
    for sensitive_value in sensitive_values:
        if sensitive_value:
            sanitized = sanitized.replace(sensitive_value, "[redacted]")
    sanitized = SENSITIVE_TEXT.sub("[redacted]", sanitized)
    return " ".join(sanitized.split())[:500]


def _insufficient_scope(response: httpx.Response) -> bool:
    authenticate = response.headers.get("WWW-Authenticate", "")
    return "insufficient_scope" in authenticate.lower()

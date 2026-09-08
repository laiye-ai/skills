"""Crash-safe, non-secret state for uncertain draft-create outcomes."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping

from filelock import FileLock, Timeout

from .errors import AppError
from .paths import default_data_dir


IDEMPOTENCY_WINDOW_SECONDS = 6 * 60
_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PendingCreateAttempt:
    """One unresolved create request; no bill fields or credentials are persisted."""

    attempt_id: str
    idempotency_key: str
    tenant_id: str
    request_sha256: str
    created_at: datetime
    expires_at: datetime

    def to_public_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "idempotency_key": self.idempotency_key,
            "tenant_id": self.tenant_id,
            "request_sha256": self.request_sha256,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    def to_storage_dict(self) -> dict[str, object]:
        return {"version": _VERSION, **self.to_public_dict()}


@dataclass
class CreateAttemptLease:
    attempt: PendingCreateAttempt
    _store: "CreateAttemptStore"
    _completed: bool = False

    def complete(self) -> None:
        """Clear the matching attempt only after a definitive create response."""
        self._store._delete_unlocked(self.attempt.attempt_id)
        self._completed = True


class CreateAttemptStore:
    """A single-record ledger guarded across processes by a file lock."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        lock_path: Path | None = None,
        now: Callable[[], datetime] = utcnow,
        lock_timeout_seconds: float = 30.0,
    ) -> None:
        data_dir = default_data_dir()
        self._path = Path(path) if path is not None else data_dir / "pending-create.json"
        resolved_lock_path = (
            Path(lock_path) if lock_path is not None else data_dir / "pending-create.lock"
        )
        self._lock_path = resolved_lock_path
        self._now = now
        self._lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def claim(
        self,
        tenant_id: str,
        payload: Mapping[str, object],
        *,
        retry_attempt_id: str | None = None,
    ) -> Iterator[CreateAttemptLease]:
        """Reserve a new key or lease the exact pending request for an explicit retry."""
        fingerprint = _request_fingerprint(payload)
        with self._locked():
            pending = self._load_unlocked()
            current_time = _as_utc(self._now())
            if pending is None:
                if retry_attempt_id is not None:
                    raise AppError(
                        "CREATE_RECONCILIATION_NOT_FOUND",
                        "No pending create attempt matches the requested retry.",
                    )
                pending = PendingCreateAttempt(
                    attempt_id=uuid.uuid4().hex,
                    idempotency_key=secrets.token_hex(64),
                    tenant_id=tenant_id,
                    request_sha256=fingerprint,
                    created_at=current_time,
                    expires_at=current_time
                    + timedelta(seconds=IDEMPOTENCY_WINDOW_SECONDS),
                )
                self._write_unlocked(pending)
            else:
                if retry_attempt_id is None:
                    raise AppError(
                        "CREATE_RECONCILIATION_REQUIRED",
                        "A previous create outcome must be reconciled before another create.",
                        reconciliation_details(pending),
                        final_status="UNKNOWN",
                        tenant_id=pending.tenant_id,
                    )
                if retry_attempt_id != pending.attempt_id:
                    raise AppError(
                        "CREATE_RETRY_MISMATCH",
                        "The retry attempt identifier does not match pending state.",
                        reconciliation_details(pending),
                        final_status="UNKNOWN",
                        tenant_id=pending.tenant_id,
                    )
                if pending.tenant_id != tenant_id or pending.request_sha256 != fingerprint:
                    raise AppError(
                        "CREATE_RETRY_MISMATCH",
                        "The retry tenant or request body differs from the pending create.",
                        reconciliation_details(pending),
                        final_status="UNKNOWN",
                        tenant_id=pending.tenant_id,
                    )
                if current_time >= pending.expires_at:
                    raise AppError(
                        "CREATE_RETRY_EXPIRED",
                        "The Xero idempotency window has expired; do not resend this create.",
                        reconciliation_details(pending),
                        final_status="UNKNOWN",
                        tenant_id=pending.tenant_id,
                    )
            yield CreateAttemptLease(pending, self)

    def status(self) -> PendingCreateAttempt | None:
        with self._locked():
            return self._load_unlocked()

    def clear(self, attempt_id: str, *, confirmed_inspected: bool) -> None:
        if not confirmed_inspected:
            raise AppError(
                "CREATE_RECONCILIATION_CONFIRMATION_REQUIRED",
                "Inspect Xero for the uncertain bill before clearing pending state.",
            )
        with self._locked():
            pending = self._load_unlocked()
            if pending is None:
                raise AppError(
                    "CREATE_RECONCILIATION_NOT_FOUND",
                    "No pending create attempt exists.",
                )
            if pending.attempt_id != attempt_id:
                raise AppError(
                    "CREATE_RETRY_MISMATCH",
                    "The attempt identifier does not match pending state.",
                    reconciliation_details(pending),
                    final_status="UNKNOWN",
                    tenant_id=pending.tenant_id,
                )
            self._delete_unlocked(attempt_id)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(
                str(self._lock_path), timeout=self._lock_timeout_seconds
            ):
                yield
        except Timeout as error:
            raise AppError(
                "CREATE_STATE_BUSY",
                "Another process is updating create reconciliation state.",
            ) from error

    def _load_unlocked(self) -> PendingCreateAttempt | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            raise AppError(
                "CREATE_STATE_READ_FAILED",
                "Create reconciliation state could not be read.",
            ) from error
        try:
            value = json.loads(raw)
            return _parse_pending(value)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            raise AppError(
                "CREATE_STATE_INVALID",
                "Create reconciliation state is invalid; do not start a new create.",
            ) from error

    def _write_unlocked(self, pending: PendingCreateAttempt) -> None:
        encoded = (
            json.dumps(
                pending.to_storage_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        temporary_path: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self._path)
        except OSError as error:
            raise AppError(
                "CREATE_STATE_WRITE_FAILED",
                "Create reconciliation state could not be stored.",
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _delete_unlocked(self, attempt_id: str) -> None:
        pending = self._load_unlocked()
        if pending is None:
            return
        if pending.attempt_id != attempt_id:
            raise AppError(
                "CREATE_RETRY_MISMATCH",
                "The attempt identifier does not match pending state.",
                reconciliation_details(pending),
                final_status="UNKNOWN",
                tenant_id=pending.tenant_id,
            )
        try:
            self._path.unlink()
        except OSError as error:
            raise AppError(
                "CREATE_STATE_WRITE_FAILED",
                "Create reconciliation state could not be cleared.",
            ) from error


def reconciliation_details(pending: PendingCreateAttempt) -> dict[str, object]:
    details = pending.to_public_dict()
    details["operator_actions"] = [
        "Run create-state status and keep this attempt identifier.",
        (
            "Before expires_at, retry only the identical input and tenant with "
            f"create --retry-unknown {pending.attempt_id}."
        ),
        (
            "Otherwise inspect Xero for the requested bill, then clear only with "
            f"create-state clear --attempt-id {pending.attempt_id} --confirmed-inspected."
        ),
    ]
    return details


def _request_fingerprint(payload: Mapping[str, object]) -> str:
    try:
        canonical = json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AppError(
            "CREATE_STATE_INVALID_REQUEST",
            "The create request cannot be recorded safely.",
        ) from error
    return hashlib.sha256(canonical).hexdigest()


def _parse_pending(value: object) -> PendingCreateAttempt:
    if not isinstance(value, dict) or value.get("version") != _VERSION:
        raise ValueError("Unknown create-state version.")
    attempt_id = value["attempt_id"]
    idempotency_key = value["idempotency_key"]
    tenant_id = value["tenant_id"]
    request_sha256 = value["request_sha256"]
    created_at = value["created_at"]
    expires_at = value["expires_at"]
    if not all(
        isinstance(item, str) and item
        for item in (
            attempt_id,
            idempotency_key,
            tenant_id,
            request_sha256,
            created_at,
            expires_at,
        )
    ):
        raise ValueError("Create state fields are invalid.")
    if (
        len(idempotency_key) != 128
        or any(character not in "0123456789abcdef" for character in idempotency_key)
        or len(request_sha256) != 64
        or any(character not in "0123456789abcdef" for character in request_sha256)
    ):
        raise ValueError("Create state digests are invalid.")
    parsed_created = _as_utc(datetime.fromisoformat(created_at))
    parsed_expiry = _as_utc(datetime.fromisoformat(expires_at))
    if parsed_expiry - parsed_created != timedelta(seconds=IDEMPOTENCY_WINDOW_SECONDS):
        raise ValueError("Create state lifetime is invalid.")
    return PendingCreateAttempt(
        attempt_id,
        idempotency_key,
        tenant_id,
        request_sha256,
        parsed_created,
        parsed_expiry,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

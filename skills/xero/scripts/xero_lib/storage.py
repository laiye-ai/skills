"""Cross-platform, non-plaintext persistence for OAuth token bundles."""

from __future__ import annotations

import base64
import binascii
import getpass
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Callable, Iterator, Mapping, Protocol

import keyring
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from filelock import FileLock, Timeout

from .errors import AppError
from .models import TokenSet
from .paths import default_data_dir


# Compatibility identifiers: changing these would orphan existing credentials.
_SERVICE_NAME = "managing-xero-bills"
_USERNAME = "xero-oauth"
_ENCRYPTION_KEY_USERNAME = "xero-oauth-file-key-v1"
_PROBE_SERVICE_NAME = "managing-xero-bills-probe"
_AAD = b"managing-xero-bills:v1"
_VERSION = 1
_SALT_BYTES = 16
_NONCE_BYTES = 12
_KEY_BYTES = 32
_SCRYPT_PARAMS = {"n": 32768, "r": 8, "p": 1}


class ProcessPassphraseProvider:
    """Read the headless environment first, otherwise prompt once per process."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        prompt: Callable[[str], str] | None = None,
    ) -> None:
        self._environment = os.environ if environment is None else environment
        self._prompt = prompt or getpass.getpass
        self._cached_value: str | None = None
        self._lock = Lock()

    def environment_value(self) -> str | None:
        """Return a non-interactive migration value without invoking the prompt."""
        value = self._environment.get("XERO_TOKEN_PASSPHRASE")
        return value if value else None

    def __call__(self) -> str:
        environment_value = self.environment_value()
        if environment_value:
            return environment_value
        with self._lock:
            if self._cached_value is None:
                self._cached_value = self._prompt("Xero token-store passphrase: ")
            return self._cached_value


process_passphrase_provider = ProcessPassphraseProvider()


class TokenStore(Protocol):
    """Persistence boundary for OAuth credentials."""

    def load(self) -> TokenSet | None: ...

    def save(self, token: TokenSet) -> None: ...

    def delete(self) -> None: ...


class KeyringTokenStore:
    """A token store backed by a Python keyring backend."""

    def __init__(self, backend: object):
        self._backend = backend

    def load(self) -> TokenSet | None:
        try:
            serialized = self._backend.get_password(_SERVICE_NAME, _USERNAME)  # type: ignore[attr-defined]
        except Exception as error:
            raise AppError("TOKEN_STORE_READ_FAILED", "Stored tokens could not be read.") from error
        if serialized is None:
            return None
        try:
            return TokenSet.from_dict(json.loads(serialized))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise AppError("TOKEN_STORE_INVALID", "Stored tokens are invalid.") from error

    def save(self, token: TokenSet) -> None:
        serialized = json.dumps(token.to_dict(), separators=(",", ":"), sort_keys=True)
        try:
            self._backend.set_password(_SERVICE_NAME, _USERNAME, serialized)  # type: ignore[attr-defined]
        except Exception as error:
            raise AppError("TOKEN_STORE_WRITE_FAILED", "Tokens could not be stored.") from error

    def delete(self) -> None:
        try:
            self._backend.delete_password(_SERVICE_NAME, _USERNAME)  # type: ignore[attr-defined]
        except keyring.errors.PasswordDeleteError:
            return
        except Exception as error:
            raise AppError("TOKEN_STORE_DELETE_FAILED", "Stored tokens could not be deleted.") from error


class EncryptedFileTokenStore:
    """AES-GCM encrypted token persistence for systems without a usable keyring."""

    def __init__(self, path: Path, passphrase_provider: Callable[[], str]):
        self._path = Path(path)
        self._passphrase_provider = passphrase_provider

    def load(self) -> TokenSet | None:
        try:
            envelope = self._path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise AppError("TOKEN_STORE_READ_FAILED", "Stored tokens could not be read.") from error
        try:
            header_bytes, ciphertext = envelope.split(b"\n", maxsplit=1)
            header = json.loads(header_bytes.decode("utf-8"))
            salt, params = _read_header(header)
            nonce, encrypted_token = ciphertext[:_NONCE_BYTES], ciphertext[_NONCE_BYTES:]
            if len(nonce) != _NONCE_BYTES or not encrypted_token:
                raise ValueError("Encrypted token payload is incomplete.")
            plaintext = AESGCM(_derive_key(self._passphrase(), salt, params)).decrypt(
                nonce, encrypted_token, _AAD
            )
            return TokenSet.from_dict(json.loads(plaintext.decode("utf-8")))
        except (InvalidTag, UnicodeDecodeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise AppError("TOKEN_DECRYPT_FAILED", "Stored tokens could not be decrypted.") from error

    def save(self, token: TokenSet) -> None:
        salt = os.urandom(_SALT_BYTES)
        nonce = os.urandom(_NONCE_BYTES)
        header = {
            "version": _VERSION,
            "salt": base64.b64encode(salt).decode("ascii"),
            "scrypt": _SCRYPT_PARAMS,
        }
        plaintext = json.dumps(token.to_dict(), separators=(",", ":"), sort_keys=True).encode("utf-8")
        ciphertext = AESGCM(_derive_key(self._passphrase(), salt, _SCRYPT_PARAMS)).encrypt(
            nonce, plaintext, _AAD
        )
        envelope = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n" + nonce + ciphertext
        self._write_atomically(envelope)

    def delete(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError as error:
            raise AppError("TOKEN_STORE_DELETE_FAILED", "Stored tokens could not be deleted.") from error

    def prepare_for_write(self) -> None:
        """Resolve the passphrase before an OAuth browser flow is started."""
        self._passphrase()
        _preflight_file_write(self._path)

    def _passphrase(self) -> bytes:
        provided_passphrase = self._passphrase_provider()
        if not isinstance(provided_passphrase, str) or not provided_passphrase:
            raise AppError("TOKEN_PASSPHRASE_REQUIRED", "A token-store passphrase is required.")
        return provided_passphrase.encode("utf-8")

    def _write_atomically(self, envelope: bytes) -> None:
        temporary_path: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(envelope)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self._path)
        except OSError as error:
            raise AppError("TOKEN_STORE_WRITE_FAILED", "Tokens could not be stored.") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


class KeyringEncryptedFileTokenStore:
    """Keep only a short encryption key in the OS credential store."""

    def __init__(
        self,
        backend: object,
        path: Path,
        *,
        legacy_path: Path | None = None,
        migration_passphrase: str | None = None,
    ):
        self._backend = backend
        self._path = Path(path)
        self._legacy_path = Path(legacy_path) if legacy_path is not None else None
        self._migration_passphrase = migration_passphrase
        self._encrypted_store = EncryptedFileTokenStore(self._path, self._require_encryption_key)
        self._key_lock = FileLock(str(self._path.with_name("token-key.lock")), timeout=30.0)

    def load(self) -> TokenSet | None:
        if self._path.exists():
            current_token = self.inspect()
            self._cleanup_legacy_state()
            return current_token
        legacy_keyring_token, legacy_keyring_error = self._read_legacy_keyring_token()
        if legacy_keyring_token is not None:
            self._migrate_token(legacy_keyring_token)
            self._cleanup_legacy_state()
            return legacy_keyring_token
        if self._legacy_path is not None and self._legacy_path.exists():
            if self._migration_passphrase is None:
                raise AppError(
                    "TOKEN_STORE_MIGRATION_REQUIRED",
                    "Set XERO_TOKEN_PASSPHRASE once to migrate the existing encrypted token store.",
                )
            legacy_store = EncryptedFileTokenStore(
                self._legacy_path, lambda: self._migration_passphrase or ""
            )
            migrated_token = legacy_store.load()
            if migrated_token is None:
                return None
            self._migrate_token(migrated_token)
            self._cleanup_legacy_state()
            return migrated_token
        if legacy_keyring_error is not None:
            raise legacy_keyring_error
        return None

    def inspect(self) -> TokenSet | None:
        """Validate only the current ciphertext without migration or cleanup side effects."""
        if not self._path.exists():
            return None
        return self._encrypted_store.load()

    def save(self, token: TokenSet) -> None:
        self.prepare_for_write()
        self._encrypted_store.save(token)

    def delete(self) -> None:
        deletion_error: AppError | None = None
        try:
            self._encrypted_store.delete()
        except AppError as error:
            deletion_error = error
        if self._legacy_path is not None:
            try:
                self._legacy_path.unlink(missing_ok=True)
            except OSError as error:
                deletion_error = deletion_error or AppError(
                    "TOKEN_STORE_DELETE_FAILED", "Stored tokens could not be deleted."
                )
        for username in (_ENCRYPTION_KEY_USERNAME, _USERNAME):
            try:
                self._delete_keyring_value(username)
            except AppError as error:
                deletion_error = deletion_error or error
        if deletion_error is not None:
            raise deletion_error

    def _delete_keyring_value(self, username: str) -> None:
        try:
            if self._backend.get_password(_SERVICE_NAME, username) is not None:  # type: ignore[attr-defined]
                self._backend.delete_password(_SERVICE_NAME, username)  # type: ignore[attr-defined]
        except keyring.errors.PasswordDeleteError:
            return
        except Exception as error:
            raise AppError("TOKEN_STORE_DELETE_FAILED", "Stored tokens could not be deleted.") from error

    def prepare_for_write(self) -> None:
        """Create and verify the short OS-protected key before an OAuth flow starts."""
        if not self._path.exists() and self._legacy_state_exists():
            raise AppError(
                "TOKEN_STORE_MIGRATION_REQUIRED",
                "Run auth status to migrate the existing token store before login.",
            )
        _preflight_file_write(self._path)
        self._prepare_encryption_key(allow_legacy_migration=False)

    def _prepare_encryption_key(self, *, allow_legacy_migration: bool) -> None:
        try:
            with self._key_lock:
                if (
                    not allow_legacy_migration
                    and not self._path.exists()
                    and self._legacy_state_exists()
                ):
                    raise AppError(
                        "TOKEN_STORE_MIGRATION_REQUIRED",
                        "Run auth status to migrate the existing token store before login.",
                    )
                if self._read_encryption_key() is not None:
                    return
                if self._path.exists():
                    raise AppError(
                        "TOKEN_STORE_KEY_MISSING",
                        "The encrypted token file exists but its system key is unavailable.",
                    )
                generated_key = base64.b64encode(os.urandom(_KEY_BYTES)).decode("ascii")
                write_attempted = False
                try:
                    write_attempted = True
                    self._backend.set_password(  # type: ignore[attr-defined]
                        _SERVICE_NAME, _ENCRYPTION_KEY_USERNAME, generated_key
                    )
                    if self._read_encryption_key() != generated_key:
                        raise AppError(
                            "TOKEN_STORE_WRITE_FAILED",
                            "The token encryption key could not be verified.",
                        )
                except Exception as error:
                    if write_attempted:
                        self._delete_encryption_key_silently()
                    if isinstance(error, AppError):
                        raise AppError(
                            "TOKEN_STORE_WRITE_FAILED",
                            "The token encryption key could not be verified.",
                        ) from error
                    raise
        except AppError:
            raise
        except Timeout as error:
            raise AppError("TOKEN_STORE_WRITE_FAILED", "The token encryption key is busy.") from error
        except Exception as error:
            raise AppError("TOKEN_STORE_WRITE_FAILED", "The token encryption key could not be stored.") from error

    def _read_encryption_key(self) -> str | None:
        try:
            value = self._backend.get_password(_SERVICE_NAME, _ENCRYPTION_KEY_USERNAME)  # type: ignore[attr-defined]
        except Exception as error:
            raise AppError("TOKEN_STORE_READ_FAILED", "The token encryption key could not be read.") from error
        if value is not None:
            try:
                decoded = base64.b64decode(value, validate=True) if isinstance(value, str) else b""
            except (ValueError, binascii.Error):
                decoded = b""
            if len(decoded) != _KEY_BYTES or base64.b64encode(decoded).decode("ascii") != value:
                raise AppError("TOKEN_STORE_INVALID", "The token encryption key is invalid.")
        return value

    def _require_encryption_key(self) -> str:
        value = self._read_encryption_key()
        if value is None:
            raise AppError(
                "TOKEN_STORE_KEY_MISSING",
                "The encrypted token file exists but its system key is unavailable.",
            )
        return value

    def _migrate_token(self, token: TokenSet) -> None:
        self._prepare_encryption_key(allow_legacy_migration=True)
        self._encrypted_store.save(token)
        if self._encrypted_store.load() != token:
            raise AppError("TOKEN_STORE_WRITE_FAILED", "Migrated tokens could not be verified.")

    def _legacy_state_exists(self) -> bool:
        legacy_keyring_token, legacy_keyring_error = self._read_legacy_keyring_token()
        return (
            legacy_keyring_token is not None
            or legacy_keyring_error is not None
            or (self._legacy_path is not None and self._legacy_path.exists())
        )

    def _cleanup_legacy_state(self) -> None:
        cleanup_error: AppError | None = None
        if self._legacy_path is not None:
            try:
                self._legacy_path.unlink(missing_ok=True)
            except OSError as error:
                cleanup_error = AppError(
                    "TOKEN_STORE_DELETE_FAILED", "Stored tokens could not be deleted."
                )
        try:
            self._delete_keyring_value(_USERNAME)
        except AppError as error:
            cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            raise cleanup_error

    def _read_legacy_keyring_token(self) -> tuple[TokenSet | None, AppError | None]:
        try:
            serialized = self._backend.get_password(_SERVICE_NAME, _USERNAME)  # type: ignore[attr-defined]
        except Exception as error:
            raise AppError("TOKEN_STORE_READ_FAILED", "Stored tokens could not be read.") from error
        if serialized is None:
            return None, None
        try:
            return TokenSet.from_dict(json.loads(serialized)), None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, AppError("TOKEN_STORE_INVALID", "Stored tokens are invalid.")

    def _delete_encryption_key_silently(self) -> None:
        try:
            self._backend.delete_password(_SERVICE_NAME, _ENCRYPTION_KEY_USERNAME)  # type: ignore[attr-defined]
        except Exception:
            pass


class RefreshLock:
    """A cross-process lock that serializes refresh-token rotation."""

    def __init__(self, path: Path | None = None, timeout_seconds: float = 30.0):
        lock_path = Path(path) if path is not None else default_data_dir() / "refresh.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(lock_path), timeout=timeout_seconds)

    @contextmanager
    def acquire(self) -> Iterator[None]:
        try:
            with self._lock:
                yield
        except Timeout as error:
            raise AppError("TOKEN_REFRESH_BUSY", "Another process is refreshing tokens.") from error


def build_token_store(
    mode: str,
    passphrase_provider: Callable[[], str],
    *,
    keyring_backend: object | None = None,
    data_dir: Path | None = None,
) -> TokenStore:
    """Select the requested secure store without exposing OS-specific APIs."""

    normalized_mode = mode.lower()
    target_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    if normalized_mode in {"file", "encrypted-file"}:
        return EncryptedFileTokenStore(target_dir / "tokens.enc", passphrase_provider)

    backend = keyring_backend if keyring_backend is not None else _system_keyring()
    usable = _keyring_is_usable(backend)
    if normalized_mode == "auto":
        if usable:
            return KeyringEncryptedFileTokenStore(
                backend,
                target_dir / "tokens.keyring.enc",
                legacy_path=target_dir / "tokens.enc",
                migration_passphrase=_noninteractive_passphrase(passphrase_provider),
            )
        if (
            (target_dir / "tokens.keyring.enc").exists()
            or _keyring_state_requires_fail_closed(backend)
        ):
            raise AppError(
                "TOKEN_STORE_UNAVAILABLE",
                "The existing encrypted token store requires the system keyring.",
            )
        return EncryptedFileTokenStore(target_dir / "tokens.enc", passphrase_provider)
    if normalized_mode == "keyring":
        if not usable:
            raise AppError("TOKEN_STORE_UNAVAILABLE", "No usable system keyring is available.")
        return KeyringEncryptedFileTokenStore(
            backend,
            target_dir / "tokens.keyring.enc",
            legacy_path=target_dir / "tokens.enc",
            migration_passphrase=_noninteractive_passphrase(passphrase_provider),
        )
    raise AppError("INVALID_TOKEN_STORE", "The token-store mode is not supported.")


def inspect_token_store(
    *,
    keyring_backend: object | None,
    data_dir: Path,
    decryption_value: str | None = None,
) -> tuple[str, bool]:
    """Read and validate the default token state without probing or prompting."""
    target_dir = Path(data_dir)
    current_path = target_dir / "tokens.keyring.enc"
    keyring_readable = _keyring_supports_reads(keyring_backend)
    if current_path.exists():
        if keyring_readable:
            current = KeyringEncryptedFileTokenStore(keyring_backend, current_path)
            try:
                return "keyring-encrypted-file", current.inspect() is not None
            except AppError:
                return "keyring-encrypted-file", False
        return "keyring-encrypted-file", False
    backend_name = "keyring-encrypted-file" if keyring_readable else "encrypted-file"
    if keyring_readable:
        try:
            legacy_token = KeyringTokenStore(keyring_backend).load()
        except AppError as error:
            if error.code == "TOKEN_STORE_READ_FAILED":
                return backend_name, False
        else:
            if legacy_token is not None:
                return backend_name, True
    legacy_path = target_dir / "tokens.enc"
    if not legacy_path.exists():
        return backend_name, False
    if not decryption_value:
        return "encrypted-file", False
    try:
        legacy_token = EncryptedFileTokenStore(legacy_path, lambda: decryption_value).load()
    except AppError:
        return "encrypted-file", False
    return "encrypted-file", legacy_token is not None


def _system_keyring() -> object | None:
    try:
        return keyring.get_keyring()
    except Exception:
        return None


def _noninteractive_passphrase(provider: Callable[[], str]) -> str | None:
    getter = getattr(provider, "environment_value", None)
    if not callable(getter):
        return None
    value = getter()
    return value if isinstance(value, str) and value else None


def _keyring_is_usable(backend: object | None) -> bool:
    if backend is None:
        return False
    try:
        priority = backend.priority  # type: ignore[attr-defined]
        priority = priority() if callable(priority) else priority
        if not isinstance(priority, (int, float)) or priority <= 0:
            return False
        get_password = backend.get_password  # type: ignore[attr-defined]
        set_password = backend.set_password  # type: ignore[attr-defined]
        delete_password = backend.delete_password  # type: ignore[attr-defined]
        if not all(callable(method) for method in (get_password, set_password, delete_password)):
            return False
        probe_username = f"probe-{uuid.uuid4().hex}"
        probe_value = uuid.uuid4().hex
        if get_password(_PROBE_SERVICE_NAME, probe_username) is not None:
            return False
        probe_saved = False
        try:
            set_password(_PROBE_SERVICE_NAME, probe_username, probe_value)
            probe_saved = True
            if get_password(_PROBE_SERVICE_NAME, probe_username) != probe_value:
                return False
            delete_password(_PROBE_SERVICE_NAME, probe_username)
            if get_password(_PROBE_SERVICE_NAME, probe_username) is not None:
                return False
            probe_saved = False
        finally:
            if probe_saved:
                try:
                    delete_password(_PROBE_SERVICE_NAME, probe_username)
                except Exception:
                    pass
    except Exception:
        return False
    return True


def _keyring_supports_reads(backend: object | None) -> bool:
    if backend is None:
        return False
    try:
        priority = backend.priority  # type: ignore[attr-defined]
        priority = priority() if callable(priority) else priority
        return (
            isinstance(priority, (int, float))
            and priority > 0
            and callable(backend.get_password)  # type: ignore[attr-defined]
        )
    except Exception:
        return False


def _keyring_state_requires_fail_closed(backend: object | None) -> bool:
    """Distinguish an empty unwritable backend from locked or existing keyring state."""
    if not _keyring_supports_reads(backend):
        return False
    try:
        getter = backend.get_password  # type: ignore[attr-defined]
        return any(
            getter(_SERVICE_NAME, username) is not None
            for username in (_ENCRYPTION_KEY_USERNAME, _USERNAME)
        )
    except Exception:
        return True


def _read_header(header: object) -> tuple[bytes, dict[str, int]]:
    if not isinstance(header, dict) or header.get("version") != _VERSION:
        raise ValueError("Unknown encrypted token format.")
    params = header.get("scrypt")
    if (
        not isinstance(params, dict)
        or any(type(params.get(name)) is not int for name in _SCRYPT_PARAMS)
        or params != _SCRYPT_PARAMS
    ):
        raise ValueError("Invalid scrypt parameters.")
    salt_value = header.get("salt")
    if not isinstance(salt_value, str):
        raise ValueError("Invalid encrypted token salt.")
    salt = base64.b64decode(salt_value, validate=True)
    if len(salt) != _SALT_BYTES:
        raise ValueError("Invalid encrypted token salt.")
    return salt, {name: params[name] for name in _SCRYPT_PARAMS}


def _derive_key(passphrase: bytes, salt: bytes, params: dict[str, int]) -> bytes:
    return Scrypt(salt=salt, length=_KEY_BYTES, n=params["n"], r=params["r"], p=params["p"]).derive(passphrase)


def _preflight_file_write(path: Path) -> None:
    """Exercise directory creation, file access, fsync, and atomic replace without token data."""
    temporary_path: Path | None = None
    probe_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with path.open("r+b"):
                pass
        probe_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.preflight"
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".preflight.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(b"\0" * 4096)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, probe_path)
        temporary_path = None
        probe_path.unlink()
        probe_path = None
    except OSError as error:
        raise AppError("TOKEN_STORE_WRITE_FAILED", "The token storage path is not writable.") from error
    finally:
        for leftover in (temporary_path, probe_path):
            if leftover is not None:
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass

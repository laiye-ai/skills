"""Stable, JSON-only command-line boundary for the Xero bill workflow."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import socket
import sys
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence
from uuid import UUID

from .errors import AppError
from .models import BillRequest

if TYPE_CHECKING:
    from .api import XeroClient
    from .auth import AuthService
    from .service import BillService
    from .storage import TokenStore


_TOKEN_STORE_CHOICES = ("auto", "keyring", "encrypted-file")
_INPUT_CODES = frozenset(
    {
        "INVALID_ARGUMENT",
        "INVALID_REQUEST",
        "MALFORMED_JSON",
        "MISSING_FIELD",
        "INVALID_FIELD",
        "INVALID_DATE",
        "INVALID_QUANTITY",
        "NO_ITEMS",
        "NO_ATTACHMENTS",
        "TOO_MANY_ATTACHMENTS",
        "INVALID_FILENAME",
        "ATTACHMENT_NOT_FOUND",
        "ATTACHMENT_NOT_FILE",
        "ATTACHMENT_TOO_LARGE",
        "NO_VALID_ITEMS",
    }
)
_AUTH_CODES = frozenset({"XERO_AUTH_FAILED", "XERO_INSUFFICIENT_SCOPE"})
_SENSITIVE_KEY = re.compile(
    r"(?:^(?:access_token|refresh_token|token|passphrase|authorization|client_secret)$|attachment.*(?:data|content|bytes))",
    re.I,
)
_SENSITIVE_TEXT = re.compile(
    r"(?:bearer\s+[^\s,;]+|[\"']?(?:access|refresh)[_-]?token[\"']?\s*[:=]\s*(?:b?[\"'][^\"']*[\"']|[^\s,;}\]]+)|[\"']?passphrase[\"']?\s*[:=]\s*(?:b?[\"'][^\"']*[\"']|[^\s,;}\]]+)|[\"']?attachment[_-]?(?:data|content|bytes)[\"']?\s*[:=]\s*(?:b?[\"'][^\"']*[\"']|[^\s,;}\]]+)|(?:access|refresh)[_-]?token(?:[-_][A-Za-z0-9]+)*)",
    re.I,
)


class _ArgumentParser(argparse.ArgumentParser):
    """Convert argparse failures into the same JSON contract as all other errors."""

    def error(self, message: str) -> None:
        raise AppError("INVALID_ARGUMENT", message)


@dataclass
class CliDependencies:
    """Injected effect boundaries used by CLI tests and embedding callers."""

    build_token_store: Callable[[str], object]
    build_auth_service: Callable[[object, Callable[[str], object]], object]
    build_xero_client: Callable[[Callable[[], object], str | None], object]
    build_bill_service: Callable[[object], object]
    build_create_attempt_store: Callable[[], object]
    load_oauth_config: Callable[[], object]
    callback_port_available: Callable[[object], bool]
    dependency_imports: Callable[[], dict[str, bool]]
    python_version: Callable[[], str]
    stdin_isatty: Callable[[], bool]
    stdout: object | None = None
    stderr: object | None = None
    doctor_token_check: Callable[[], tuple[str, bool]] | None = None


def _default_dependencies() -> CliDependencies:
    def build_store(mode: str) -> object:
        from .storage import build_token_store, process_passphrase_provider

        return build_token_store(mode, process_passphrase_provider)

    def build_auth(store: object, progress: Callable[[str], object]) -> object:
        from .auth import AuthService, OAuthClient, load_oauth_config
        from .storage import RefreshLock

        return AuthService(OAuthClient(load_oauth_config()), store, RefreshLock(), output=progress)

    def build_create_store() -> object:
        from .reconciliation import CreateAttemptStore

        return CreateAttemptStore()

    def build_client(
        session_provider: Callable[[], object], retry_attempt_id: str | None
    ) -> object:
        from .api import XeroClient

        return XeroClient(
            session_provider,
            create_attempt_store=build_create_store(),
            pending_create_retry_id=retry_attempt_id,
        )

    def build_service(client: object) -> object:
        from .service import BillService

        return BillService(client)

    return CliDependencies(
        build_token_store=build_store,
        build_auth_service=build_auth,
        build_xero_client=build_client,
        build_bill_service=build_service,
        build_create_attempt_store=build_create_store,
        load_oauth_config=_load_oauth_config,
        callback_port_available=_callback_port_available,
        dependency_imports=_runtime_dependency_imports,
        python_version=lambda: sys.version.split()[0],
        stdin_isatty=_stdin_isatty,
        doctor_token_check=_default_doctor_token_check,
    )


def main(argv: Sequence[str] | None = None, dependencies: CliDependencies | None = None) -> int:
    """Run one command and write exactly one JSON document for its outcome."""
    debug = False
    deps = dependencies or _default_dependencies()
    stdout = deps.stdout if deps.stdout is not None else sys.stdout
    try:
        raw_argv = list(sys.argv[1:] if argv is None else argv)
        debug = "--debug" in raw_argv
        if debug:
            raw_argv = [argument for argument in raw_argv if argument != "--debug"]
        args = _build_parser().parse_args(raw_argv)
        outcome, exit_code = _run(args, deps)
    except AppError as error:
        outcome, exit_code = _app_error_outcome(error), _exit_code_for(error)
    except Exception as error:  # The boundary intentionally never leaks raw exception text by default.
        outcome, exit_code = _internal_error_outcome(error, debug), 1
    try:
        _write_json(outcome, stdout)
    except Exception:
        # A hostile or misconfigured host stream must not escape the CLI boundary as a traceback.
        return 1
    return exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="xero.py", add_help=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", add_help=False)

    auth = commands.add_parser("auth", add_help=False)
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    login = auth_commands.add_parser("login", add_help=False)
    login.add_argument("--tenant-id", type=_uuid)
    login.add_argument("--token-store", choices=_TOKEN_STORE_CHOICES, default="auto")
    status = auth_commands.add_parser("status", add_help=False)
    status.add_argument("--token-store", choices=_TOKEN_STORE_CHOICES, default="auto")
    logout = auth_commands.add_parser("logout", add_help=False)
    logout.add_argument("--local-only", action="store_true")
    logout.add_argument("--token-store", choices=_TOKEN_STORE_CHOICES, default="auto")

    create = commands.add_parser("create", add_help=False)
    create.add_argument("--input", required=True, type=Path)
    create.add_argument("--tenant-id", type=_uuid)
    create.add_argument("--token-store", choices=_TOKEN_STORE_CHOICES, default="auto")
    create.add_argument("--retry-unknown", metavar="ATTEMPT_ID")

    demo = commands.add_parser("demo-smoke", add_help=False)
    demo.add_argument("--input", required=True, type=Path)
    demo.add_argument("--tenant-id", type=_uuid)
    demo.add_argument("--token-store", choices=_TOKEN_STORE_CHOICES, default="auto")
    demo.add_argument("--retry-unknown", metavar="ATTEMPT_ID")
    demo.add_argument("--confirm-demo-company", action="store_true", required=True)
    demo.add_argument("--approve-live", action="store_true")

    create_state = commands.add_parser("create-state", add_help=False)
    create_state_commands = create_state.add_subparsers(
        dest="create_state_command", required=True
    )
    create_state_commands.add_parser("status", add_help=False)
    clear = create_state_commands.add_parser("clear", add_help=False)
    clear.add_argument("--attempt-id", required=True)
    clear.add_argument("--confirmed-inspected", action="store_true", required=True)
    return parser


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a UUID") from error


def _run(args: argparse.Namespace, deps: CliDependencies) -> tuple[dict[str, object], int]:
    if args.command == "doctor":
        return _doctor(deps), 0
    if args.command == "create":
        return _create(args, deps)
    if args.command == "demo-smoke":
        return _create(args, deps, demo_smoke=True)
    if args.command == "create-state":
        return _create_state(args, deps), 0
    if args.auth_command == "login":
        return _login(args, deps), 0
    if args.auth_command == "status":
        return _status(args, deps), 0
    return _logout(args, deps), 0


def _doctor(deps: CliDependencies) -> dict[str, object]:
    """Read only local capability/authentication state; never create an API client."""
    if deps.doctor_token_check is not None:
        token_backend, authenticated = deps.doctor_token_check()
    else:
        store = deps.build_token_store("auto")
        token_backend = type(store).__name__
        try:
            authenticated = _token_store_has_auth(store)
        except AppError:
            authenticated = False
    try:
        oauth_config = deps.load_oauth_config()
    except AppError:
        oauth_configured = False
        callback_port_available = False
    else:
        oauth_configured = True
        callback_port_available = deps.callback_port_available(oauth_config)
    checks = {
        "python_version": deps.python_version(),
        "dependencies": deps.dependency_imports(),
        "token_backend": token_backend,
        "oauth_configured": oauth_configured,
        "callback_port_available": callback_port_available,
        "authentication_exists": authenticated,
    }
    return {"ok": True, "checks": checks}


def _token_store_has_auth(store: object) -> bool:
    """Avoid an encrypted-file passphrase prompt in doctor when a file merely exists."""
    path = getattr(store, "_path", None)
    if isinstance(path, Path):
        return path.exists()
    return store.load() is not None


def _login(args: argparse.Namespace, deps: CliDependencies) -> dict[str, object]:
    store = deps.build_token_store(args.token_store)
    prepare_for_write = getattr(store, "prepare_for_write", None)
    if callable(prepare_for_write):
        prepare_for_write()
    progress = lambda message: _write_progress(_redact_text(message), deps.stderr)
    progress("Opening Xero authorization in your browser.")
    tenant = deps.build_auth_service(store, progress).login(
        interactive=deps.stdin_isatty(), tenant_id=args.tenant_id
    )
    return {"ok": True, "authenticated": True, "tenant_id": tenant.tenant_id, "tenant_name": tenant.tenant_name}


def _status(args: argparse.Namespace, deps: CliDependencies) -> dict[str, object]:
    store = deps.build_token_store(args.token_store)
    return {"ok": True, "authenticated": store.load() is not None}


def _logout(args: argparse.Namespace, deps: CliDependencies) -> dict[str, object]:
    store = deps.build_token_store(args.token_store)
    deps.build_auth_service(store, lambda message: _write_progress(_redact_text(message), deps.stderr)).logout(
        local_only=args.local_only
    )
    return {"ok": True, "revoked": not args.local_only, "local_credentials_deleted": True}


def _create(
    args: argparse.Namespace,
    deps: CliDependencies,
    *,
    demo_smoke: bool = False,
) -> tuple[dict[str, object], int]:
    request = BillRequest.from_path(args.input)
    store = deps.build_token_store(args.token_store)
    auth = deps.build_auth_service(store, lambda message: _write_progress(_redact_text(message), deps.stderr))

    def session_provider() -> object:
        session = auth.valid_session()
        return (
            replace(session, tenant_id=args.tenant_id, tenant_name=None)
            if args.tenant_id is not None
            else session
        )

    service = deps.build_bill_service(
        deps.build_xero_client(session_provider, args.retry_unknown)
    )
    if demo_smoke:
        result = service.create(
            request,
            approve=args.approve_live,
            require_draft_verification=args.approve_live,
        )
    else:
        result = service.create(request)
    payload = _sanitize_value(result.to_dict())
    if demo_smoke:
        payload["demo_smoke"] = True
        payload["approval_requested"] = args.approve_live
    if result.ok:
        return payload, 0
    return payload, 5 if result.invoice_id else 4


def _create_state(args: argparse.Namespace, deps: CliDependencies) -> dict[str, object]:
    store = deps.build_create_attempt_store()
    if args.create_state_command == "status":
        pending = store.status()
        return {
            "ok": True,
            "pending": pending is not None,
            **(
                {"attempt": pending.to_public_dict()}
                if pending is not None
                else {}
            ),
        }
    store.clear(
        args.attempt_id,
        confirmed_inspected=args.confirmed_inspected,
    )
    return {"ok": True, "cleared": True, "attempt_id": args.attempt_id}


def _app_error_outcome(error: AppError) -> dict[str, object]:
    payload: dict[str, object] = {"ok": False, "code": error.code, "message": _redact_text(error.message)}
    details = _sanitize_value(error.details)
    if details:
        payload["details"] = details
    if error.invoice_id:
        payload["invoice_id"] = _redact_text(error.invoice_id)
    if error.final_status:
        payload["final_status"] = error.final_status
    elif error.invoice_id:
        payload["final_status"] = "UNKNOWN"
    if error.tenant_id:
        payload["tenant_id"] = _redact_text(error.tenant_id)
    if error.tenant_name:
        payload["tenant_name"] = _redact_text(error.tenant_name)
    return payload


def _internal_error_outcome(error: Exception, debug: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": False,
        "code": "INTERNAL_ERROR",
        "message": "An unexpected internal error occurred.",
    }
    if debug:
        frames = traceback.extract_tb(error.__traceback__)
        payload["traceback"] = _redact_text(
            "\n".join(f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}" for frame in frames)
        )
    return payload


def _exit_code_for(error: AppError) -> int:
    if error.invoice_id:
        return 5
    if error.code in _INPUT_CODES:
        return 2
    if error.code == "INVALID_TOKEN_STORE" or error.code in _AUTH_CODES or error.code.startswith(("AUTH_", "OAUTH_", "TOKEN_", "TENANT_")):
        return 3
    if error.code.startswith("CREATE_"):
        return 4
    if error.code.startswith("XERO_") or error.code.startswith("CONTACT_"):
        return 4
    return 2 if error.code == "INVALID_ARGUMENT" else 1


def _sanitize_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_value(item)
            for key, item in value.items()
            if not _SENSITIVE_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, bytes):
        return "[redacted]"
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: object) -> str:
    return _SENSITIVE_TEXT.sub("[redacted]", str(value))


def _write_json(payload: dict[str, object], stream: object) -> None:
    encoded = (json.dumps(_sanitize_value(payload), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    binary_stream = getattr(stream, "buffer", None)
    if binary_stream is not None:
        binary_stream.write(encoded)
        flush = getattr(binary_stream, "flush", None)
    elif isinstance(stream, (bytearray, bytes)):
        raise TypeError("stdout stream must be writable")
    else:
        try:
            stream.write(encoded)
        except TypeError:
            stream.write(encoded.decode("utf-8"))
        flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def _write_progress(message: str, stream: object | None) -> None:
    target = stream if stream is not None else sys.stderr
    target.write(message + "\n")
    flush = getattr(target, "flush", None)
    if callable(flush):
        flush()


def _runtime_dependency_imports() -> dict[str, bool]:
    results: dict[str, bool] = {}
    for name in ("httpx", "keyring", "cryptography", "filelock", "platformdirs"):
        try:
            importlib.import_module(name)
        except Exception:
            results[name] = False
        else:
            results[name] = True
    return results


def _default_doctor_token_check() -> tuple[str, bool]:
    """Inspect local credential availability without importing the API/client stack or writing a probe."""
    backend = None
    try:
        keyring = importlib.import_module("keyring")
        backend = keyring.get_keyring()
    except Exception:
        pass
    try:
        from . import paths
        from .storage import inspect_token_store

        return inspect_token_store(
            keyring_backend=backend,
            data_dir=paths.default_data_dir(),
            decryption_value=os.environ.get("XERO_TOKEN_PASSPHRASE"),
        )
    except Exception:
        return "unavailable", False


def _stdin_isatty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def _load_oauth_config() -> object:
    from .auth import load_oauth_config

    return load_oauth_config()


def _callback_port_available(config: object) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((getattr(config, "callback_host"), getattr(config, "callback_port")))
    except OSError:
        return False
    return True

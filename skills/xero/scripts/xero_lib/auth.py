"""Public-client Xero OAuth, token rotation, and tenant selection."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event, Lock, Timer
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .errors import AppError
from .models import TokenSet
from .paths import default_data_dir
from .storage import RefreshLock, TokenStore


OAUTH_CLIENT_ID_ENV = "CLAWWORKER_XERO_OAUTH_CLIENT_ID"
OAUTH_REDIRECT_URI_ENV = "CLAWWORKER_XERO_OAUTH_REDIRECT_URI"
SCOPES = (
    "offline_access accounting.invoices accounting.attachments "
    "accounting.contacts.read accounting.settings.read"
)
AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
REVOCATION_URL = "https://identity.xero.com/connect/revocation"
CONNECTIONS_URL = "https://api.xero.com/connections"
CALLBACK_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class PkcePair:
    verifier: str
    challenge: str


@dataclass(frozen=True)
class OAuthConfig:
    """Desktop-provided public OAuth configuration and its callback endpoint."""

    client_id: str
    redirect_uri: str
    callback_port: int
    callback_path: str
    callback_host: str = "127.0.0.1"


def load_oauth_config(environment: Mapping[str, str] | None = None) -> OAuthConfig:
    """Load the required Desktop values once, rejecting unsafe callback endpoints locally."""
    source = os.environ if environment is None else environment
    client_id = source.get(OAUTH_CLIENT_ID_ENV)
    redirect_uri = source.get(OAUTH_REDIRECT_URI_ENV)
    if not _valid_client_id(client_id) or not _valid_redirect_uri(redirect_uri):
        raise AppError("OAUTH_CONFIG_INVALID", "The Desktop OAuth configuration is missing or invalid.")
    try:
        parsed = urlsplit(redirect_uri)
        port = parsed.port
    except ValueError as error:
        raise AppError("OAUTH_CONFIG_INVALID", "The Desktop OAuth configuration is missing or invalid.") from error
    if port is None:  # Kept for type narrowing; _valid_redirect_uri already verifies this.
        raise AppError("OAUTH_CONFIG_INVALID", "The Desktop OAuth configuration is missing or invalid.")
    callback_host = "127.0.0.1" if parsed.hostname.casefold() == "localhost" else parsed.hostname
    return OAuthConfig(client_id, redirect_uri, port, parsed.path or "/", callback_host)


def _valid_client_id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.isascii() and value.isprintable()


def _valid_redirect_uri(value: object) -> bool:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value) or "\\" in value:
        return False
    if not value.isascii() or not value.isprintable():
        return False
    if "?" in value or "#" in value:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "http" or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        return False
    if port is None or not 1 <= port <= 65535:
        return False
    if not re.fullmatch(r"/(?:[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*)?", parsed.path):
        return False
    if any(segment in {".", ".."} for segment in parsed.path.split("/")):
        return False
    hostname = parsed.hostname
    if hostname is None:
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.version == 4 and address.is_loopback


@dataclass(frozen=True)
class TenantConnection:
    tenant_id: str
    tenant_name: str


@dataclass(frozen=True)
class AuthSession:
    access_token: str
    tenant_id: str
    tenant_name: str | None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class HttpClient(Protocol):
    def request(self, method: str, url: str, *, headers: dict[str, str], data: bytes | None = None) -> object: ...


class UrllibHttpClient:
    """Small standard-library transport; tests inject the transport boundary."""

    def request(self, method: str, url: str, *, headers: dict[str, str], data: bytes | None = None) -> HttpResponse:
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return HttpResponse(response.status, response.read())
        except HTTPError as error:
            return HttpResponse(error.code, error.read())
        except URLError as error:
            raise AppError("OAUTH_NETWORK_FAILED", "The Xero authorization service could not be reached.") from error


class TenantConfigStore:
    """Stores only the active tenant's non-secret identifier and display name."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path is not None else default_data_dir() / "tenant.json"

    def load(self) -> TenantConnection | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            raise AppError("TENANT_CONFIG_READ_FAILED", "The selected Xero tenant could not be read.") from error
        try:
            value = json.loads(raw)
            tenant_id = value["tenant_id"]
            tenant_name = value["tenant_name"]
            if not all(isinstance(item, str) and item for item in (tenant_id, tenant_name)):
                raise ValueError("Tenant configuration is invalid.")
            return TenantConnection(tenant_id, tenant_name)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AppError("TENANT_CONFIG_INVALID", "The selected Xero tenant is invalid.") from error

    def save(self, tenant: TenantConnection) -> None:
        if not tenant.tenant_id or not tenant.tenant_name:
            raise AppError("TENANT_CONFIG_INVALID", "The selected Xero tenant is invalid.")
        encoded = json.dumps(
            {"tenant_id": tenant.tenant_id, "tenant_name": tenant.tenant_name},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        temporary_path: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self._path)
        except OSError as error:
            raise AppError("TENANT_CONFIG_WRITE_FAILED", "The selected Xero tenant could not be stored.") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_pkce() -> PkcePair:
    verifier = secrets.token_urlsafe(64).rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return PkcePair(verifier, challenge)


def generate_state() -> str:
    return secrets.token_urlsafe(32).rstrip("=")


def validate_callback(parameters: Mapping[str, list[str]], expected_state: str) -> str:
    states = parameters.get("state", [])
    if len(states) != 1 or not secrets.compare_digest(states[0], expected_state):
        raise AppError("OAUTH_STATE_MISMATCH", "The OAuth callback state did not match the login request.")
    errors = parameters.get("error", [])
    if errors:
        raise AppError("OAUTH_AUTHORIZATION_FAILED", "Xero did not authorize this login.")
    codes = parameters.get("code", [])
    if len(codes) != 1 or not codes[0]:
        raise AppError("OAUTH_CALLBACK_INVALID", "The OAuth callback did not include an authorization code.")
    return codes[0]


class _CallbackHTTPServer(HTTPServer):
    """Suppress only the socket abort intentionally triggered by the deadline watchdog."""

    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler], deadline_expired: Event):
        self.deadline_expired = deadline_expired
        super().__init__(server_address, handler)

    def handle_error(self, request: object, client_address: tuple[str, int]) -> None:
        active_error = sys.exc_info()[1]
        if self.deadline_expired.is_set() and isinstance(active_error, ConnectionAbortedError):
            return
        super().handle_error(request, client_address)


def receive_callback(
    authorization_url: str,
    expected_state: str,
    config: OAuthConfig,
    browser_opener: Callable[[str], object] = webbrowser.open,
    timeout_seconds: int = CALLBACK_TIMEOUT_SECONDS,
) -> str:
    """Open the browser after binding an IPv4 loopback-only, single-use listener."""

    result: dict[str, str | AppError] = {}
    deadline = time.monotonic() + timeout_seconds
    active_socket: socket.socket | None = None
    active_lock = Lock()
    deadline_expired = Event()

    def stop_active_request() -> None:
        deadline_expired.set()
        with active_lock:
            current_socket = active_socket
        if current_socket is None:
            return
        try:
            current_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            current_socket.close()
        except OSError:
            pass

    class CallbackHandler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            nonlocal active_socket
            with active_lock:
                active_socket = self.request
                expired = deadline_expired.is_set() or deadline <= time.monotonic()
                self.request.settimeout(max(deadline - time.monotonic(), 0.001))
                super().setup()
                self._expired_before_setup = expired

        def handle(self) -> None:
            if self._expired_before_setup:
                return
            super().handle()

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            if self.path.split("?", 1)[0] != config.callback_path:
                result["error"] = AppError("OAUTH_CALLBACK_INVALID", "The OAuth callback used an invalid path.")
                self._respond(404, "Authorization callback not found.")
                return
            from urllib.parse import parse_qs, urlsplit

            try:
                result["code"] = validate_callback(parse_qs(urlsplit(self.path).query), expected_state)
            except AppError as error:
                result["error"] = error
                self._respond(400, "Authorization could not be verified. You may close this window.")
                return
            self._respond(200, "Authorization complete. You may close this window.")

        def _respond(self, status: int, message: str) -> None:
            body = f"<!doctype html><title>Xero authorization</title><p>{message}</p>".encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    try:
        server = _CallbackHTTPServer((config.callback_host, config.callback_port), CallbackHandler, deadline_expired)
    except OSError as error:
        raise AppError("OAUTH_CALLBACK_UNAVAILABLE", "The local OAuth callback port is unavailable.") from error
    watchdog = Timer(max(0, deadline - time.monotonic()), stop_active_request)
    watchdog.daemon = True
    try:
        watchdog.start()
        browser_opener(authorization_url)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            server.timeout = remaining
            server.handle_request()
    finally:
        watchdog.cancel()
        server.server_close()
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    if "code" not in result:
        raise AppError("OAUTH_CALLBACK_TIMEOUT", "Timed out waiting for the OAuth callback.")
    return result["code"]  # type: ignore[return-value]


class OAuthClient:
    def __init__(self, config: OAuthConfig, http_client: HttpClient | None = None, now: Callable[[], datetime] = utcnow):
        self.config = config
        self._http = http_client or UrllibHttpClient()
        self._now = now

    def authorization_url(self, state: str, challenge: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": SCOPES,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def exchange_code(self, code: str, verifier: str) -> TokenSet:
        return self._token(
            {
                "grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "code_verifier": verifier,
            }
        )

    def refresh(self, refresh_token: str) -> TokenSet:
        return self._token(
            {"grant_type": "refresh_token", "client_id": self.config.client_id, "refresh_token": refresh_token}
        )

    def revoke(self, refresh_token: str) -> None:
        """Revoke a refresh token before the local credential is discarded."""
        basic_credential = base64.b64encode(f"{self.config.client_id}:".encode("ascii")).decode("ascii")
        response = self._http.request(
            "POST",
            REVOCATION_URL,
            headers={
                "Authorization": f"Basic {basic_credential}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data=urlencode({"token": refresh_token}).encode("ascii"),
        )
        status, _ = _response_parts(response)
        if status < 200 or status >= 300:
            raise AppError("OAUTH_REVOKE_FAILED", "Xero did not confirm authorization revocation.", {"status": status})

    def connections(self, access_token: str) -> tuple[TenantConnection, ...]:
        value = self._json_request("GET", CONNECTIONS_URL, {"Authorization": f"Bearer {access_token}"})
        if not isinstance(value, list):
            raise AppError("TENANT_DISCOVERY_FAILED", "Xero returned invalid tenant connections.")
        connections: list[TenantConnection] = []
        for connection in value:
            if not isinstance(connection, dict):
                raise AppError("TENANT_DISCOVERY_FAILED", "Xero returned invalid tenant connections.")
            tenant_id = connection.get("tenantId")
            tenant_name = connection.get("tenantName")
            if not isinstance(tenant_id, str) or not tenant_id or not isinstance(tenant_name, str) or not tenant_name:
                raise AppError("TENANT_DISCOVERY_FAILED", "Xero returned invalid tenant connections.")
            connections.append(TenantConnection(tenant_id, tenant_name))
        return tuple(connections)

    def _token(self, fields: dict[str, str]) -> TokenSet:
        value = self._json_request(
            "POST",
            TOKEN_URL,
            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            urlencode(fields).encode("ascii"),
        )
        if not isinstance(value, dict):
            raise AppError("OAUTH_TOKEN_INVALID", "Xero returned an invalid token response.")
        raw_access_value = value.get("access_token")
        raw_refresh_value = value.get("refresh_token")
        expires_in = value.get("expires_in")
        scope = value.get("scope")
        if (
            not isinstance(raw_access_value, str)
            or not raw_access_value
            or not isinstance(raw_refresh_value, str)
            or not raw_refresh_value
            or type(expires_in) is not int
            or expires_in <= 0
            or not isinstance(scope, str)
        ):
            raise AppError("OAUTH_TOKEN_INVALID", "Xero returned an invalid token response.")
        return TokenSet(raw_access_value, raw_refresh_value, _as_utc(self._now()) + timedelta(seconds=expires_in), scope)

    def _json_request(
        self, method: str, url: str, headers: dict[str, str], data: bytes | None = None
    ) -> object:
        response = self._http.request(method, url, headers=headers, data=data)
        status, body = _response_parts(response)
        if status < 200 or status >= 300:
            raise AppError("OAUTH_REQUEST_FAILED", "Xero rejected the authorization request.", {"status": status})
        try:
            return json.loads(body.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AppError("OAUTH_RESPONSE_INVALID", "Xero returned an invalid authorization response.") from error


class AuthService:
    def __init__(
        self,
        oauth: OAuthClient,
        store: TokenStore,
        lock: RefreshLock,
        *,
        tenant: TenantConnection | None = None,
        tenant_store: TenantConfigStore | None = None,
        callback_receiver: Callable[[str, str, OAuthConfig | None, Callable[[str], object]], str] = receive_callback,
        browser_opener: Callable[[str], object] = webbrowser.open,
        state_factory: Callable[[], str] = generate_state,
        pkce_factory: Callable[[], PkcePair] = generate_pkce,
        input_reader: Callable[[], str] = input,
        output: Callable[[str], object] = print,
    ):
        self._oauth = oauth
        self._store = store
        self._lock = lock
        self._tenant = tenant
        self._tenant_store = tenant_store if tenant_store is not None else TenantConfigStore()
        self._callback_receiver = callback_receiver
        self._browser_opener = browser_opener
        self._state_factory = state_factory
        self._pkce_factory = pkce_factory
        self._input_reader = input_reader
        self._output = output

    def login(self, interactive: bool, tenant_id: str | None) -> TenantConnection:
        pkce = self._pkce_factory()
        state = self._state_factory()
        code = self._callback_receiver(
            self._oauth.authorization_url(state, pkce.challenge), state, getattr(self._oauth, "config", None), self._browser_opener
        )
        tokens = self._oauth.exchange_code(code, pkce.verifier)
        connections = self._oauth.connections(tokens.access_token)
        selected = self._select_tenant(connections, interactive, tenant_id)
        self._store.save(tokens)
        self._tenant = selected
        self._tenant_store.save(selected)
        return selected

    def valid_session(self, now: Callable[[], datetime] = utcnow) -> AuthSession:
        stored_tokens = self._store.load()
        if stored_tokens is None:
            raise AppError("AUTH_REQUIRED", "Xero authorization is required before this operation.")
        if _token_is_valid(stored_tokens, now()):
            return self._session(stored_tokens)
        with self._lock.acquire():
            current = self._store.load()
            if current is None:
                raise AppError("AUTH_REQUIRED", "Xero authorization is required before this operation.")
            if _token_is_valid(current, now()):
                return self._session(current)
            prepare_for_write = getattr(self._store, "prepare_for_write", None)
            if callable(prepare_for_write):
                prepare_for_write()
            rotated = self._oauth.refresh(current.refresh_token)
            self._store.save(rotated)
            return self._session(rotated)

    def logout(self, *, local_only: bool) -> None:
        """Remove local credentials only after Xero confirms revocation, unless explicitly local-only."""
        with self._lock.acquire():
            if local_only:
                self._store.delete()
                return
            stored_tokens = self._store.load()
            if stored_tokens is None:
                return
            self._oauth.revoke(stored_tokens.refresh_token)
            self._store.delete()

    def _session(self, token: TokenSet) -> AuthSession:
        tenant = self._tenant
        if tenant is None:
            tenant = self._tenant_store.load()
        if tenant is None:
            raise AppError("TENANT_CONTEXT_REQUIRED", "Select a Xero tenant before this operation.")
        return AuthSession(token.access_token, tenant.tenant_id, tenant.tenant_name)

    def _select_tenant(
        self, connections: tuple[TenantConnection, ...], interactive: bool, tenant_id: str | None
    ) -> TenantConnection:
        if not connections:
            raise AppError("TENANT_NOT_FOUND", "No Xero organisations are connected to this account.")
        if tenant_id is not None:
            for connection in connections:
                if connection.tenant_id == tenant_id:
                    return connection
            raise AppError("TENANT_NOT_FOUND", "The requested Xero tenant is not connected to this account.")
        if len(connections) == 1:
            return connections[0]
        if not interactive:
            raise AppError("TENANT_SELECTION_REQUIRED", "Select a Xero tenant explicitly when multiple organisations are connected.")
        for index, connection in enumerate(connections, start=1):
            self._output(f"{index}. {connection.tenant_name}")
        try:
            selection = int(self._input_reader().strip())
        except EOFError:
            raise AppError(
                "TENANT_SELECTION_REQUIRED",
                "Select a Xero tenant explicitly when interactive input is unavailable.",
            ) from None
        except (TypeError, ValueError):
            raise AppError("TENANT_SELECTION_INVALID", "Choose a listed Xero tenant number.") from None
        if selection < 1 or selection > len(connections):
            raise AppError("TENANT_SELECTION_INVALID", "Choose a listed Xero tenant number.")
        return connections[selection - 1]


def _response_parts(response: object) -> tuple[int, bytes]:
    if isinstance(response, dict) or isinstance(response, list):
        return 200, json.dumps(response).encode("utf-8")
    if isinstance(response, HttpResponse):
        return response.status, response.body
    status = getattr(response, "status", None)
    read = getattr(response, "read", None)
    if isinstance(status, int) and callable(read):
        body = read()
        if isinstance(body, bytes):
            return status, body
    raise AppError("OAUTH_RESPONSE_INVALID", "Xero returned an invalid authorization response.")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _token_is_valid(token: TokenSet, now: datetime) -> bool:
    return _as_utc(token.expires_at) > _as_utc(now)

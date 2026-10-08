"""Sign in to ChatGPT with the public Sign in with ChatGPT flow."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from .storage import ChatGPTAuthStorage, OAuthStorageError


_AUTH_ORIGIN = "https://auth.openai.com"
_AUTHORIZE_URL = f"{_AUTH_ORIGIN}/api/accounts/authorize"
_TOKEN_URL = f"{_AUTH_ORIGIN}/api/accounts/oauth/token"
_DISCOVERY_URL = f"{_AUTH_ORIGIN}/.well-known/openid-configuration"
_API_ORIGIN = "https://api.openai.com"
_RESOURCE = f"{_API_ORIGIN}/v1"
_SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
_CALLBACK_PATH = "/auth/callback"
_CALLBACK_TTL_SECONDS = 300
_HTTP_TIMEOUT_SECONDS = 5.0


class ChatGPTAuthError(RuntimeError):
    """A safe-to-display ChatGPT authorization error."""


@dataclass
class _PendingAuthorization:
    account_id: str
    client_id: str
    selected_subject: str
    email_hint: str
    id_token_hint: str
    host_id: str
    state: str
    nonce: str
    verifier: str
    expires_at: float
    cancelled: threading.Event = field(default_factory=threading.Event)
    completed: threading.Event = field(default_factory=threading.Event)
    server: http.server.HTTPServer | None = None
    thread: threading.Thread | None = None
    error: str = ""


class _CallbackServer(http.server.HTTPServer):
    allow_reuse_address = False


class ChatGPTAuthService:
    def __init__(
        self,
        data_dir: Path,
        *,
        http: Any | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        protector: Callable[[bytes], bytes] | None = None,
        unprotector: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self._storage = ChatGPTAuthStorage(data_dir, protector=protector, unprotector=unprotector)
        self._http = http or httpx.Client(
            timeout=_HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_http = http is None
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleeper
        self._state_lock = threading.RLock()
        self._begin_lock = threading.Lock()
        self._pending: _PendingAuthorization | None = None
        self._last_error = ""
        self._closed = False

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            pending = self._pending
            is_pending = pending is not None and not pending.completed.is_set()
            error = (
                pending.error
                if pending is not None and pending.completed.is_set() and pending.error
                else self._last_error
            )
        try:
            store = self._storage.load_unlocked()
        except OAuthStorageError:
            return {"accounts": [], "pending": is_pending, "error": "Local credentials are unavailable."}
        accounts = [
            {
                "id": account["id"],
                "label": account["label"],
                "email": account.get("email", ""),
                "connected": bool(account.get("access_token") and account.get("refresh_token")),
            }
            for account in store["accounts"]
        ]
        return {"accounts": accounts, "pending": is_pending, "error": error}

    def begin(self, account_id: str = "") -> dict[str, Any]:
        if not self._begin_lock.acquire(blocking=False):
            raise ChatGPTAuthError("A ChatGPT authorization is already starting.")
        try:
            with self._state_lock:
                if self._closed:
                    raise ChatGPTAuthError("The ChatGPT authorization service is closed.")
            return self._begin(account_id)
        finally:
            self._begin_lock.release()

    def _begin(self, account_id: str) -> dict[str, Any]:
        with self._state_lock:
            if self._closed:
                raise ChatGPTAuthError("The ChatGPT authorization service is closed.")
            if self._pending is not None and not self._pending.completed.is_set():
                raise ChatGPTAuthError("A ChatGPT authorization is already in progress.")

        try:
            with self._storage.locked():
                store = self._storage.load_unlocked()
                if not store["host_id"]:
                    store["host_id"] = f"urn:uuid:{uuid.uuid4()}"
                selected = None
                if account_id:
                    selected = next((item for item in store["accounts"] if item["id"] == account_id), None)
                    if selected is None:
                        raise ChatGPTAuthError("The selected ChatGPT account was not found.")
                    if not _valid_issued_client_id(selected["client_id"]):
                        raise ChatGPTAuthError("The selected ChatGPT registration is invalid.")
                elif len(store["accounts"]) >= 100:
                    raise ChatGPTAuthError("The saved ChatGPT account limit has been reached.")
                self._storage.save_unlocked(store)
        except OAuthStorageError:
            raise ChatGPTAuthError("Local credentials are unavailable.") from None

        selected_client_id = selected["client_id"] if selected else "dynamic_agent_client"
        pending = _PendingAuthorization(
            account_id=selected["id"] if selected else str(uuid.uuid4()),
            client_id=selected_client_id,
            selected_subject=selected["subject"] if selected else "",
            email_hint=selected.get("email", "") if selected else "",
            id_token_hint=selected.get("id_token", "") if selected else "",
            host_id=store["host_id"],
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            verifier=secrets.token_urlsafe(64),
            expires_at=self._monotonic() + _CALLBACK_TTL_SECONDS,
        )
        try:
            server = _CallbackServer(("127.0.0.1", 0), self._handler_for(pending))
            server.timeout = 0.2
        except OSError:
            raise ChatGPTAuthError("The local sign-in callback could not be started.") from None
        pending.server = server
        redirect_uri = f"http://127.0.0.1:{server.server_address[1]}{_CALLBACK_PATH}"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(pending.verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        query: dict[str, str] = {
            "client_id": pending.client_id,
            "ext_agent_host_id": pending.host_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": _SCOPES,
            "resource": _RESOURCE,
            "state": pending.state,
            "nonce": pending.nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        if selected is None:
            query["agent_name_hint"] = "VRCForge"
        else:
            if pending.id_token_hint:
                query["id_token_hint"] = pending.id_token_hint
            if pending.email_hint:
                query["login_hint"] = pending.email_hint
        authorization_url = f"{_AUTHORIZE_URL}?{urlencode(query)}"
        pending.thread = threading.Thread(
            target=self._listen,
            args=(pending,),
            name="VRCForge-ChatGPT-OAuth-callback",
            daemon=True,
        )
        with self._state_lock:
            self._pending = pending
            self._last_error = ""
        try:
            pending.thread.start()
        except RuntimeError:
            server.server_close()
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            raise ChatGPTAuthError("The local sign-in callback could not be started.") from None
        return {
            "authorizationUrl": authorization_url,
            "accountId": pending.account_id,
            "redirectUri": redirect_uri,
            "expiresIn": _CALLBACK_TTL_SECONDS,
        }

    def access_token(self, account_id: str) -> str:
        try:
            with self._storage.locked():
                store = self._storage.load_unlocked()
                account = _find_account(store, account_id)
                if not account.get("refresh_token"):
                    raise ChatGPTAuthError("The selected ChatGPT account is not connected.")
                if account.get("access_token") and float(account.get("expires_at", 0)) > self._clock() + 60:
                    if "chatgpt.tokens.use.direct" not in account.get("scopes", []):
                        raise ChatGPTAuthError("ChatGPT plan access is not authorized for this account.")
                    return account["access_token"]
                account = self._refresh_locked(account)
                return account["access_token"]
        except OAuthStorageError:
            raise ChatGPTAuthError("Local credentials are unavailable.") from None

    def models(self, account_id: str) -> list[dict[str, str]]:
        token = self.access_token(account_id)
        try:
            response = self._http.get(
                f"{_API_ORIGIN}/v1/models",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
            if response.status_code != 200:
                raise ChatGPTAuthError("The ChatGPT model list is unavailable.")
            payload = response.json()
        except ChatGPTAuthError:
            raise
        except Exception:
            raise ChatGPTAuthError("The ChatGPT model list is unavailable.") from None
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise ChatGPTAuthError("The ChatGPT model list is invalid.")
        result: list[dict[str, str]] = []
        for item in models:
            if not isinstance(item, dict) or item.get("visibility") != "list":
                continue
            slug = item.get("slug")
            display_name = item.get("display_name")
            if isinstance(slug, str) and slug and isinstance(display_name, str) and display_name:
                result.append({"id": slug, "name": display_name})
        return result

    def logout(self, account_id: str) -> dict[str, bool]:
        with self._state_lock:
            pending = self._pending
        if pending is not None and pending.account_id == account_id:
            self.cancel()
        confirmed = False
        try:
            with self._storage.locked():
                store = self._storage.load_unlocked()
                account = _find_account(store, account_id)
                refresh_token = account.get("refresh_token", "")
                if not refresh_token:
                    confirmed = True
                else:
                    try:
                        discovery = self._discovery()
                        endpoint = discovery.get("revocation_endpoint")
                        self._validate_auth_url(endpoint)
                        confirmed = self._revoke_refresh_token(
                            endpoint,
                            refresh_token,
                            account["client_id"],
                        )
                    except Exception:
                        confirmed = False
                for key in ("access_token", "refresh_token", "id_token", "token_type", "expires_at", "saved_at"):
                    account.pop(key, None)
                account["scopes"] = []
                self._storage.save_unlocked(store)
        except OAuthStorageError:
            with self._state_lock:
                self._last_error = "Local credentials could not be cleared."
            raise ChatGPTAuthError("Local credentials could not be cleared.") from None
        with self._state_lock:
            self._last_error = "" if confirmed else "Remote sign-out could not be confirmed."
        return {"revoked": confirmed, "localCleared": True}

    def cancel(self) -> None:
        with self._state_lock:
            pending = self._pending
            if pending is None or pending.completed.is_set():
                return
            pending.error = "Authorization cancelled."
            pending.cancelled.set()
            pending.completed.set()
        if pending.server is not None:
            try:
                pending.server.server_close()
            except OSError:
                pass
        if pending.thread is not None and pending.thread is not threading.current_thread():
            pending.thread.join(timeout=1.0)

    def close(self) -> None:
        with self._begin_lock:
            with self._state_lock:
                if self._closed:
                    return
                self._closed = True
                pending = self._pending
        self.cancel()
        if pending is not None and pending.thread is not None and pending.thread is not threading.current_thread():
            pending.thread.join(timeout=16.0)
        if self._owns_http:
            try:
                self._http.close()
            except Exception:
                pass

    def _listen(self, pending: _PendingAuthorization) -> None:
        server = pending.server
        assert server is not None
        try:
            while not pending.completed.is_set() and not pending.cancelled.is_set():
                if self._monotonic() >= pending.expires_at:
                    pending.error = "Authorization timed out."
                    pending.completed.set()
                    break
                server.handle_request()
        except OSError:
            if not pending.cancelled.is_set() and not pending.completed.is_set():
                pending.error = "The local sign-in callback stopped unexpectedly."
                pending.completed.set()
        finally:
            try:
                server.server_close()
            except OSError:
                pass
            with self._state_lock:
                if self._pending is pending:
                    self._last_error = pending.error
                    self._pending = None

    def _handler_for(self, pending: _PendingAuthorization) -> type[http.server.BaseHTTPRequestHandler]:
        service = self

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(3.0)

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.path != _CALLBACK_PATH or parsed.query == "":
                    self._reply(404, "This sign-in callback is not valid.")
                    return
                status, message = service._process_callback(pending, parsed.query)
                self._reply(status, message)

            def _reply(self, status: int, message: str) -> None:
                body = (
                    "<!doctype html><html><head><meta charset=\"utf-8\"><title>ChatGPT sign-in</title></head>"
                    f"<body><p>{message}</p></body></html>"
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: Any) -> None:
                return

        return CallbackHandler

    def _process_callback(self, pending: _PendingAuthorization, raw_query: str) -> tuple[int, str]:
        if self._monotonic() >= pending.expires_at:
            pending.error = "Authorization timed out."
            pending.completed.set()
            return 410, "This sign-in attempt has expired. You may close this window."
        if pending.cancelled.is_set():
            pending.error = "Authorization cancelled."
            pending.completed.set()
            return 410, "This sign-in attempt has ended."
        try:
            query = parse_qs(raw_query, keep_blank_values=True, max_num_fields=16)
        except ValueError:
            return 400, "This sign-in callback is not valid."
        state = _single(query, "state")
        if state is None or not hmac.compare_digest(state, pending.state):
            return 400, "This sign-in callback is not valid."
        if pending.cancelled.is_set() or pending.completed.is_set():
            return 410, "This sign-in attempt has ended."
        oauth_error = _single(query, "error")
        if "error" in query:
            if oauth_error is None or not oauth_error:
                pending.error = "Authorization response was invalid."
                pending.completed.set()
                return 400, "Sign-in was not completed. You may close this window."
            pending.error = "Authorization was denied."
            pending.completed.set()
            return 400, "Sign-in was not completed. You may close this window."
        code = _single(query, "code")
        returned_client_id = _single(query, "client_id")
        if not code or len(code) > 4096 or any(ord(character) < 0x20 for character in code):
            pending.error = "Authorization response was invalid."
            pending.completed.set()
            return 400, "Sign-in was not completed. You may close this window."
        if pending.client_id == "dynamic_agent_client":
            if not _valid_issued_client_id(returned_client_id):
                pending.error = "Registration was not completed."
                pending.completed.set()
                return 400, "Sign-in was not completed. You may close this window."
            issued_client_id = returned_client_id
        else:
            if "client_id" in query and (
                returned_client_id is None
                or not hmac.compare_digest(returned_client_id, pending.client_id)
            ):
                pending.error = "The selected ChatGPT registration did not match."
                pending.completed.set()
                return 400, "Sign-in was not completed. You may close this window."
            issued_client_id = pending.client_id
        try:
            token_response = self._post_token(
                {
                    "grant_type": "authorization_code",
                    "client_id": issued_client_id,
                    "code": code,
                    "code_verifier": pending.verifier,
                    "redirect_uri": self._redirect_uri(pending),
                    "resource": _RESOURCE,
                }
            )
            identity = self._validate_id_token(token_response.get("id_token"), issued_client_id, pending.nonce)
            if pending.selected_subject and not hmac.compare_digest(identity["sub"], pending.selected_subject):
                raise ChatGPTAuthError("The selected ChatGPT account identity did not match.")
            scopes = _scopes(token_response.get("scope"))
            if "chatgpt.tokens.use.direct" not in scopes:
                raise ChatGPTAuthError("ChatGPT plan access was not granted.")
            credentials = self._credentials(token_response, scopes, identity["id_token"])
            if pending.cancelled.is_set():
                raise ChatGPTAuthError("Authorization cancelled.")
            with self._storage.locked():
                store = self._storage.load_unlocked()
                with self._state_lock:
                    if pending.cancelled.is_set():
                        raise ChatGPTAuthError("Authorization cancelled.")
                    if self._monotonic() >= pending.expires_at:
                        pending.error = "Authorization timed out."
                        raise ChatGPTAuthError("Authorization timed out.")
                    if not hmac.compare_digest(store["host_id"], pending.host_id):
                        raise ChatGPTAuthError("The local ChatGPT host identity changed.")
                    existing = next((item for item in store["accounts"] if item["id"] == pending.account_id), None)
                    if pending.selected_subject:
                        if existing is None or existing["client_id"] != issued_client_id or existing["subject"] != pending.selected_subject:
                            raise ChatGPTAuthError("The selected ChatGPT registration changed.")
                    elif existing is not None:
                        raise ChatGPTAuthError("The new ChatGPT account could not be saved.")
                    if existing is None:
                        label_number = store["next_label_number"]
                        store["next_label_number"] += 1
                        existing = {
                            "id": pending.account_id,
                            "label": f"ChatGPT account {label_number}",
                            "subject": identity["sub"],
                            "client_id": issued_client_id,
                            "email": identity["email"],
                        }
                        store["accounts"].append(existing)
                    else:
                        existing["email"] = identity["email"] or existing.get("email", "")
                    existing.update(credentials)
                    self._storage.save_unlocked(store)
                    pending.error = ""
                    pending.completed.set()
            return 200, "ChatGPT sign-in completed. You may close this window."
        except ChatGPTAuthError as exc:
            pending.error = str(exc)
        except OAuthStorageError:
            pending.error = "Local credentials could not be saved."
        except Exception:
            pending.error = "ChatGPT sign-in failed."
        pending.completed.set()
        return 400, "Sign-in was not completed. You may close this window."

    def _redirect_uri(self, pending: _PendingAuthorization) -> str:
        server = pending.server
        if server is None:
            raise ChatGPTAuthError("The local sign-in callback stopped unexpectedly.")
        return f"http://127.0.0.1:{server.server_address[1]}{_CALLBACK_PATH}"

    def _post_token(self, form: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._http.post(
                _TOKEN_URL,
                data=form,
                timeout=_HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
            if response.status_code != 200:
                raise ChatGPTAuthError("The ChatGPT token request failed.")
            payload = response.json()
        except ChatGPTAuthError:
            raise
        except Exception:
            raise ChatGPTAuthError("The ChatGPT token request failed.") from None
        if not isinstance(payload, dict):
            raise ChatGPTAuthError("The ChatGPT token response is invalid.")
        return payload

    def _revoke_refresh_token(self, endpoint: str, refresh_token: str, client_id: str) -> bool:
        for attempt in range(3):
            try:
                response = self._http.post(
                    endpoint,
                    data={
                        "token": refresh_token,
                        "token_type_hint": "refresh_token",
                        "client_id": client_id,
                    },
                    timeout=_HTTP_TIMEOUT_SECONDS,
                    follow_redirects=False,
                )
            except Exception:
                if attempt < 2:
                    self._sleep(0.1 * (2**attempt))
                    continue
                return False
            if response.status_code == 200:
                return True
            if not 500 <= response.status_code < 600 or attempt == 2:
                return False
            self._sleep(0.1 * (2**attempt))
        return False

    def _discovery(self) -> dict[str, Any]:
        payload = self._get_json(_DISCOVERY_URL)
        if payload.get("issuer") != _AUTH_ORIGIN:
            raise ChatGPTAuthError("The ChatGPT identity provider configuration is invalid.")
        return payload

    def _get_json(self, url: str, *, api: bool = False) -> dict[str, Any]:
        if api:
            _validate_url(url, "api.openai.com")
        else:
            self._validate_auth_url(url)
        try:
            response = self._http.get(
                url,
                timeout=_HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
            if response.status_code != 200:
                raise ChatGPTAuthError("The ChatGPT identity provider response is invalid.")
            payload = response.json()
        except ChatGPTAuthError:
            raise
        except Exception:
            raise ChatGPTAuthError("The ChatGPT identity provider response is unavailable.") from None
        if not isinstance(payload, dict):
            raise ChatGPTAuthError("The ChatGPT identity provider response is invalid.")
        return payload

    @staticmethod
    def _validate_auth_url(url: Any) -> None:
        if not isinstance(url, str):
            raise ChatGPTAuthError("The ChatGPT identity provider configuration is invalid.")
        _validate_url(url, "auth.openai.com")

    def _validate_id_token(self, token: Any, client_id: str, nonce: str | None) -> dict[str, str]:
        if not isinstance(token, str) or len(token) > 64_000:
            raise ChatGPTAuthError("The ChatGPT identity token is invalid.")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ChatGPTAuthError("The ChatGPT identity token is invalid.")
            discovery = self._discovery()
            jwks_uri = discovery.get("jwks_uri")
            self._validate_auth_url(jwks_uri)
            jwks = self._get_json(jwks_uri)
            keys = jwks.get("keys")
            if not isinstance(keys, list) or len(keys) > 100:
                raise ChatGPTAuthError("The ChatGPT signing keys are invalid.")
            matching = next(
                (
                    item for item in keys
                    if isinstance(item, dict)
                    and item.get("kid") == header["kid"]
                    and item.get("kty") == "RSA"
                    and item.get("use", "sig") == "sig"
                ),
                None,
            )
            if matching is None:
                raise ChatGPTAuthError("The ChatGPT signing key was not found.")
            key = jwt.PyJWK.from_dict(matching, algorithm="RS256").key
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=client_id,
                issuer=_AUTH_ORIGIN,
                options={"require": ["exp", "iss", "aud", "sub"] + (["nonce"] if nonce is not None else [])},
                leeway=0,
            )
            subject = claims.get("sub")
            email = claims.get("email", "")
            if not isinstance(subject, str) or not subject or len(subject) > 1024:
                raise ChatGPTAuthError("The ChatGPT identity token is invalid.")
            if email is not None and (not isinstance(email, str) or len(email) > 512):
                raise ChatGPTAuthError("The ChatGPT identity token is invalid.")
            if nonce is not None:
                returned_nonce = claims.get("nonce")
                if not isinstance(returned_nonce, str) or not hmac.compare_digest(returned_nonce, nonce):
                    raise ChatGPTAuthError("The ChatGPT identity token nonce did not match.")
            audience = claims.get("aud")
            if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != client_id:
                raise ChatGPTAuthError("The ChatGPT identity token audience is invalid.")
            return {"sub": subject, "email": email or "", "id_token": token}
        except ChatGPTAuthError:
            raise
        except Exception:
            raise ChatGPTAuthError("The ChatGPT identity token could not be validated.") from None

    def _credentials(self, payload: dict[str, Any], scopes: list[str], id_token: str) -> dict[str, Any]:
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        token_type = payload.get("token_type", "Bearer")
        expires_in = _expires_in(payload.get("expires_in"))
        if (
            not isinstance(access_token, str) or not access_token or len(access_token) > 64_000
            or not isinstance(refresh_token, str) or not refresh_token or len(refresh_token) > 64_000
            or not isinstance(token_type, str) or token_type.lower() != "bearer"
            or not id_token
        ):
            raise ChatGPTAuthError("The ChatGPT token response is invalid.")
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": id_token,
            "token_type": "Bearer",
            "expires_at": self._clock() + expires_in,
            "scopes": scopes,
            "saved_at": self._clock(),
        }

    def _refresh_locked(self, account: dict[str, Any]) -> dict[str, Any]:
        payload = self._post_token(
            {
                "grant_type": "refresh_token",
                "client_id": account["client_id"],
                "refresh_token": account["refresh_token"],
                "resource": _RESOURCE,
            }
        )
        scopes = _scopes(payload.get("scope")) if payload.get("scope") is not None else list(account.get("scopes", []))
        if "chatgpt.tokens.use.direct" not in scopes:
            raise ChatGPTAuthError("ChatGPT plan access is not authorized for this account.")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token or len(access_token) > 64_000:
            raise ChatGPTAuthError("The ChatGPT token response is invalid.")
        refresh_token = payload.get("refresh_token", account["refresh_token"])
        token_type = payload.get("token_type", "Bearer")
        expires_in = _expires_in(payload.get("expires_in"))
        if not isinstance(refresh_token, str) or not refresh_token or not isinstance(token_type, str) or token_type.lower() != "bearer":
            raise ChatGPTAuthError("The ChatGPT token response is invalid.")
        refreshed_id_token = payload.get("id_token")
        if refreshed_id_token is not None:
            identity = self._validate_id_token(refreshed_id_token, account["client_id"], None)
            if not hmac.compare_digest(identity["sub"], account["subject"]):
                raise ChatGPTAuthError("The refreshed ChatGPT identity did not match this account.")
            account["id_token"] = identity["id_token"]
            if identity["email"]:
                account["email"] = identity["email"]
        account.update(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "expires_at": self._clock() + expires_in,
                "scopes": scopes,
                "saved_at": self._clock(),
            }
        )
        store = self._storage.load_unlocked()
        stored = _find_account(store, account["id"])
        stored.update(account)
        self._storage.save_unlocked(store)
        return stored


def _find_account(store: dict[str, Any], account_id: str) -> dict[str, Any]:
    account = next((item for item in store["accounts"] if item["id"] == account_id), None)
    if account is None:
        raise ChatGPTAuthError("The selected ChatGPT account was not found.")
    return account


def _single(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values is not None and len(values) == 1 else None


def _scopes(value: Any) -> list[str]:
    if not isinstance(value, str) or len(value) > 4096:
        return []
    return list(dict.fromkeys(part for part in value.split() if part))


def _expires_in(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 31 * 24 * 60 * 60:
        raise ChatGPTAuthError("The ChatGPT token expiry is invalid.")
    return float(value)


def _valid_issued_client_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 1024
        and value != "dynamic_agent_client"
        and not any(character.isspace() or ord(character) < 0x20 for character in value)
    )


def _validate_url(url: str, hostname: str) -> None:
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme == "https"
            and parts.hostname == hostname
            and parts.port in (None, 443)
            and parts.username is None
            and parts.password is None
            and not parts.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ChatGPTAuthError("The ChatGPT service URL is invalid.")


_singleton: ChatGPTAuthService | None = None
_singleton_lock = threading.Lock()


def get_chatgpt_auth_service() -> ChatGPTAuthService:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            import os

            local_app_data = os.environ.get("LOCALAPPDATA")
            if not local_app_data:
                raise RuntimeError("LOCALAPPDATA is unavailable.")
            _singleton = ChatGPTAuthService(Path(local_app_data) / "VRCForge" / "chatgpt-oauth")
        return _singleton


def close_chatgpt_auth_service() -> None:
    global _singleton
    with _singleton_lock:
        service = _singleton
        _singleton = None
    if service is not None:
        service.close()

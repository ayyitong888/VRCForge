from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from chatgpt_integration import service as oauth_service_module
from chatgpt_integration.service import (
    ChatGPTAuthError,
    ChatGPTAuthService,
    close_chatgpt_auth_service,
    get_chatgpt_auth_service,
    _AUTH_ORIGIN,
    _DISCOVERY_URL,
    _RESOURCE,
    _TOKEN_URL,
)
from chatgpt_integration.storage import ChatGPTAuthStorage


_GRANTED_SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeHTTP:
    def __init__(self):
        self.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.signing_key.public_key(), as_dict=True)
        self.jwks = {"keys": [{**public_jwk, "kid": "test-key", "alg": "RS256", "use": "sig"}]}
        self.expected_nonce = ""
        self.expected_client_id = ""
        self.issued_client_id = "oaiapp_test"
        self.claim_overrides: dict = {}
        self.claim_removals: set[str] = set()
        self.token_overrides: dict = {}
        self.refresh_response = {
            "access_token": "access-rotated",
            "refresh_token": "refresh-rotated",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": _GRANTED_SCOPES,
        }
        self.model_payload = {"models": []}
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []
        self.refresh_calls = 0
        self.token_calls = 0
        self.revocation_status = 200
        self.revocation_calls = 0
        self.closed = False
        self._lock = threading.Lock()

    def close(self):
        self.closed = True

    def get(self, url: str, **kwargs):
        with self._lock:
            self.get_calls.append((url, kwargs))
        if url == _DISCOVERY_URL:
            return FakeResponse(
                200,
                {
                    "issuer": _AUTH_ORIGIN,
                    "jwks_uri": f"{_AUTH_ORIGIN}/.well-known/jwks.json",
                    "revocation_endpoint": f"{_AUTH_ORIGIN}/api/accounts/oauth/revoke",
                },
            )
        if url == f"{_AUTH_ORIGIN}/.well-known/jwks.json":
            return FakeResponse(200, self.jwks)
        if url == "https://api.openai.com/v1/models":
            assert kwargs["headers"]["Authorization"] == "Bearer access-rotated"
            return FakeResponse(200, self.model_payload)
        raise AssertionError(f"Unexpected GET URL: {url}")

    def post(self, url: str, **kwargs):
        data = kwargs.get("data", {})
        with self._lock:
            self.post_calls.append((url, dict(data)))
        if url == _TOKEN_URL and data.get("grant_type") == "authorization_code":
            self.token_calls += 1
            self.expected_client_id = data["client_id"]
            claims = {
                "iss": _AUTH_ORIGIN,
                "aud": self.expected_client_id,
                "sub": "subject-1",
                "email": "person@example.test",
                "nonce": self.expected_nonce,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            }
            claims.update(self.claim_overrides)
            for claim in self.claim_removals:
                claims.pop(claim, None)
            signer = self.other_key if self.token_overrides.pop("bad_signature", False) else self.signing_key
            id_token = jwt.encode(claims, signer, algorithm="RS256", headers={"kid": "test-key"})
            response = {
                "access_token": "access-one",
                "refresh_token": "refresh-one",
                "id_token": id_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": _GRANTED_SCOPES,
            }
            response.update(self.token_overrides)
            self.token_overrides = {}
            return FakeResponse(200, response)
        if url == _TOKEN_URL and data.get("grant_type") == "refresh_token":
            with self._lock:
                self.refresh_calls += 1
            return FakeResponse(200, dict(self.refresh_response))
        if url == f"{_AUTH_ORIGIN}/api/accounts/oauth/revoke":
            self.revocation_calls += 1
            return FakeResponse(self.revocation_status, {})
        raise AssertionError(f"Unexpected POST URL or grant: {url}")


def _protect(value: bytes) -> bytes:
    return b"test-protected:" + base64.b64encode(value)


def _unprotect(value: bytes) -> bytes:
    prefix = b"test-protected:"
    if not value.startswith(prefix):
        raise ValueError("not a test protected value")
    return base64.b64decode(value[len(prefix):])


def _service(tmp_path: Path, http: FakeHTTP | None = None, **kwargs):
    return ChatGPTAuthService(
        tmp_path / "oauth-data",
        http=http or FakeHTTP(),
        protector=_protect,
        unprotector=_unprotect,
        **kwargs,
    )


def _complete_login(service: ChatGPTAuthService, fake: FakeHTTP, account_id: str = "", **callback):
    result = service.begin(account_id)
    params = parse_qs(urlsplit(result["authorizationUrl"]).query)
    fake.expected_nonce = params["nonce"][0]
    expected_client_id = params["client_id"][0]
    callback_params = {
        "code": "auth-code",
        "state": params["state"][0],
    }
    if expected_client_id == "dynamic_agent_client":
        callback_params["client_id"] = fake.issued_client_id
    else:
        callback_params["client_id"] = expected_client_id
    callback_params.update(callback)
    status, _ = service._process_callback(service._pending, urlencode(callback_params))
    return result, status, params


def test_status_and_construction_do_not_write_or_make_network_calls(tmp_path: Path):
    fake = FakeHTTP()
    data_dir = tmp_path / "oauth-data"
    service = ChatGPTAuthService(data_dir, http=fake, protector=_protect, unprotector=_unprotect)
    try:
        assert service.status() == {"accounts": [], "pending": False, "error": ""}
        assert not data_dir.exists()
        assert fake.get_calls == []
        assert fake.post_calls == []
    finally:
        service.close()


def test_begin_and_callback_register_a_protected_account(tmp_path: Path):
    fake = FakeHTTP()
    service = _service(tmp_path, fake)
    try:
        result, callback_status, params = _complete_login(service, fake)
        authorization = urlsplit(result["authorizationUrl"])
        query = parse_qs(authorization.query)
        assert authorization.scheme == "https"
        assert authorization.netloc == "auth.openai.com"
        assert query["client_id"] == ["dynamic_agent_client"]
        assert query["agent_name_hint"] == ["VRCForge"]
        assert query["ext_agent_host_id"][0].startswith("urn:uuid:")
        assert query["scope"] == [_GRANTED_SCOPES]
        assert query["resource"] == [_RESOURCE]
        assert query["response_type"] == ["code"]
        assert query["redirect_uri"] == [result["redirectUri"]]
        assert result["redirectUri"].startswith("http://127.0.0.1:")
        assert urlsplit(result["redirectUri"]).path == "/auth/callback"
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(service._pending.verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        assert query["code_challenge"] == [challenge]
        assert query["code_challenge_method"] == ["S256"]
        assert callback_status == 200
        assert fake.post_calls[0][0] == _TOKEN_URL
        assert fake.post_calls[0][1]["client_id"] == fake.issued_client_id
        assert fake.post_calls[0][1]["redirect_uri"] == result["redirectUri"]
        assert fake.post_calls[0][1]["code_verifier"] == service._pending.verifier

        status = service.status()
        assert status["pending"] is False
        assert status["accounts"] == [
            {
                "id": result["accountId"],
                "label": "ChatGPT account 1",
                "email": "person@example.test",
                "connected": True,
            }
        ]
        assert "access-one" not in json.dumps(status)
        protected_file = service._storage.path.read_bytes()
        assert b"access-one" not in protected_file
        assert b"refresh-one" not in protected_file
        assert b"person@example.test" not in protected_file
        assert service.access_token(result["accountId"]) == "access-one"
        assert params["nonce"]
    finally:
        service.close()


def test_callback_wrong_state_keeps_the_attempt_open(tmp_path: Path):
    fake = FakeHTTP()
    service = _service(tmp_path, fake)
    try:
        result = service.begin()
        params = parse_qs(urlsplit(result["authorizationUrl"]).query)
        fake.expected_nonce = params["nonce"][0]
        bad = urlencode({"code": "auth-code", "state": "wrong", "client_id": fake.issued_client_id})
        assert service._process_callback(service._pending, bad)[0] == 400
        assert service.status()["pending"] is True
        assert fake.token_calls == 0

        good = urlencode({"code": "auth-code", "state": params["state"][0], "client_id": fake.issued_client_id})
        assert service._process_callback(service._pending, good)[0] == 200
        assert service.status()["accounts"][0]["id"] == result["accountId"]
    finally:
        service.close()


def test_loopback_callback_route_completes_with_mocked_oauth_http(tmp_path: Path):
    fake = FakeHTTP()
    service = _service(tmp_path, fake)
    try:
        result = service.begin()
        params = parse_qs(urlsplit(result["authorizationUrl"]).query)
        fake.expected_nonce = params["nonce"][0]
        with httpx.Client(trust_env=False, timeout=2.0) as client:
            response = client.get(
                result["redirectUri"],
                params={
                    "code": "auth-code",
                    "state": params["state"][0],
                    "client_id": fake.issued_client_id,
                },
            )
        assert response.status_code == 200
        assert "sign-in completed" in response.text
        assert service.status()["accounts"][0]["id"] == result["accountId"]
        assert fake.token_calls == 1
    finally:
        service.close()


@pytest.mark.parametrize(
    "claim_overrides,bad_signature",
    [
        ({"nonce": "different-nonce"}, False),
        ({"aud": "another-client"}, False),
        ({"exp": int(time.time()) - 10}, False),
        ({}, True),
    ],
)
def test_identity_token_must_have_valid_signature_and_claims(
    tmp_path: Path, claim_overrides: dict, bad_signature: bool
):
    fake = FakeHTTP()
    fake.claim_overrides = claim_overrides
    if bad_signature:
        fake.token_overrides["bad_signature"] = True
    service = _service(tmp_path, fake)
    try:
        _, callback_status, _ = _complete_login(service, fake)
        assert callback_status == 400
        status = service.status()
        assert status["accounts"] == []
        assert status["pending"] is False
        assert "access-one" not in json.dumps(status)
        assert "refresh-one" not in json.dumps(status)
    finally:
        service.close()


@pytest.mark.parametrize(
    "claim_name,delta",
    [
        ("iat", 2),
        ("exp", -2),
    ],
)
def test_identity_token_allows_small_clock_skew(tmp_path: Path, claim_name: str, delta: int):
    fake = FakeHTTP()
    fake.claim_overrides = {claim_name: int(time.time()) + delta}
    service = _service(tmp_path, fake)
    try:
        _, callback_status, _ = _complete_login(service, fake)
        assert callback_status == 200
        assert service.status()["accounts"]
    finally:
        service.close()


@pytest.mark.parametrize(
    "claim_name,delta,claim_removals,expected_error",
    [
        ("iat", 6, set(), "The ChatGPT identity token time claim is invalid."),
        ("exp", -6, set(), "The ChatGPT identity token has expired."),
        (None, 0, {"exp"}, "The ChatGPT identity token is missing a required claim."),
        ("aud", 0, set(), "The ChatGPT identity token audience is invalid."),
        ("iss", 0, set(), "The ChatGPT identity token issuer is invalid."),
    ],
)
def test_identity_token_failures_are_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    claim_name: str | None,
    delta: int,
    claim_removals: set[str],
    expected_error: str,
):
    fixed_now = int(time.time())

    class FixedJwtClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(fixed_now, timezone.utc).astimezone(tz)

    # Verify the exact six-second boundary independently of login scheduling.
    monkeypatch.setattr(jwt.api_jwt, "datetime", FixedJwtClock)
    fake = FakeHTTP()
    if claim_name is not None:
        fake.claim_overrides = {
            claim_name: fixed_now + delta if claim_name in {"iat", "exp"} else (
                "another-client" if claim_name == "aud" else "https://issuer.invalid"
            )
        }
    fake.claim_removals = claim_removals
    service = _service(tmp_path, fake)
    try:
        _, callback_status, _ = _complete_login(service, fake)
        assert callback_status == 400
        assert service.status()["error"] == expected_error
        assert service.status()["accounts"] == []
    finally:
        service.close()


def test_bad_signature_is_classified_without_exposing_token_data(tmp_path: Path):
    fake = FakeHTTP()
    fake.token_overrides["bad_signature"] = True
    service = _service(tmp_path, fake)
    try:
        _, callback_status, _ = _complete_login(service, fake)
        assert callback_status == 400
        assert service.status()["error"] == "The ChatGPT identity token signature is invalid."
        assert "access-one" not in json.dumps(service.status())
        assert "refresh-one" not in json.dumps(service.status())
    finally:
        service.close()


def test_failed_new_account_login_can_be_retried_without_stuck_pending(tmp_path: Path):
    fake = FakeHTTP()
    fake.token_overrides["bad_signature"] = True
    service = _service(tmp_path, fake)
    try:
        _, first_status, _ = _complete_login(service, fake)
        assert first_status == 400
        deadline = time.monotonic() + 2
        while service.status()["pending"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.status()["pending"] is False

        _, second_status, _ = _complete_login(service, fake)
        assert second_status == 200
        assert service.status()["accounts"]
    finally:
        service.close()


def test_returning_account_reuses_registration_and_rejects_identity_change(tmp_path: Path):
    fake = FakeHTTP()
    service = _service(tmp_path, fake)
    try:
        first, first_status, _ = _complete_login(service, fake)
        assert first_status == 200
        account_id = first["accountId"]
        old_access = service.access_token(account_id)
        before_exchange_count = fake.token_calls

        returning = service.begin(account_id)
        query = parse_qs(urlsplit(returning["authorizationUrl"]).query)
        assert query["client_id"] == [fake.issued_client_id]
        assert query["id_token_hint"]
        assert "agent_name_hint" not in query

        wrong_client = urlencode(
            {"code": "auth-code", "state": query["state"][0], "client_id": "oaiapp_other"}
        )
        assert service._process_callback(service._pending, wrong_client)[0] == 400
        assert fake.token_calls == before_exchange_count
        assert service.access_token(account_id) == old_access

        fake.claim_overrides = {"sub": "subject-from-another-account"}
        returning = service.begin(account_id)
        query = parse_qs(urlsplit(returning["authorizationUrl"]).query)
        fake.expected_nonce = query["nonce"][0]
        callback = urlencode(
            {"code": "auth-code", "state": query["state"][0], "client_id": fake.issued_client_id}
        )
        assert service._process_callback(service._pending, callback)[0] == 400
        assert service.access_token(account_id) == old_access
    finally:
        service.close()


def test_access_token_refresh_is_serialized_and_models_keep_visible_order(tmp_path: Path):
    fake = FakeHTTP()
    now = {"value": time.time()}
    service1 = _service(tmp_path, fake, clock=lambda: now["value"])
    service2 = _service(tmp_path, fake, clock=lambda: now["value"])
    try:
        fake.token_overrides["expires_in"] = 30
        result, status, _ = _complete_login(service1, fake)
        assert status == 200

        with ThreadPoolExecutor(max_workers=2) as pool:
            tokens = list(pool.map(lambda service: service.access_token(result["accountId"]), [service1, service2]))
        assert tokens == ["access-rotated", "access-rotated"]
        assert fake.refresh_calls == 1
        store = service1._storage.load_unlocked()
        account = store["accounts"][0]
        assert account["refresh_token"] == "refresh-rotated"

        fake.model_payload = {
            "models": [
                {"slug": "first", "display_name": "First", "visibility": "list", "context_window": 123456, "max_output_tokens": 4096},
                {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
                {"slug": "second", "display_name": "Second", "visibility": "list", "context_window": True, "input_token_limit": -5},
                {"id": "ignored", "name": "Malformed", "visibility": "list"},
            ]
        }
        assert service1.models(result["accountId"]) == [
            {"id": "first", "name": "First", "contextWindow": 123456, "maxOutputTokens": 4096},
            {"id": "second", "name": "Second"},
        ]
        assert fake.get_calls[-1][0] == "https://api.openai.com/v1/models"
    finally:
        service1.close()
        service2.close()


def test_logout_reports_revocation_and_local_clear_separately(tmp_path: Path):
    fake = FakeHTTP()
    service = _service(tmp_path, fake)
    try:
        result, status, _ = _complete_login(service, fake)
        assert status == 200
        logged_out = service.logout(result["accountId"])
        assert logged_out == {"revoked": True, "localCleared": True}
        assert fake.post_calls[-1] == (
            f"{_AUTH_ORIGIN}/api/accounts/oauth/revoke",
            {
                "token": "refresh-one",
                "token_type_hint": "refresh_token",
                "client_id": fake.issued_client_id,
            },
        )
        assert service._storage.load_unlocked()["accounts"] == []
        assert service.status()["accounts"] == []
    finally:
        service.close()


def test_failed_remote_revocation_still_clears_local_tokens(tmp_path: Path):
    fake = FakeHTTP()
    fake.revocation_status = 503
    service = _service(tmp_path, fake, sleeper=lambda _delay: None)
    try:
        result, status, _ = _complete_login(service, fake)
        assert status == 200
        assert service.logout(result["accountId"]) == {"revoked": False, "localCleared": True}
        assert fake.revocation_calls == 3
        assert service.status()["accounts"] == []
        assert service.status()["error"] == "Remote sign-out could not be confirmed."
    finally:
        service.close()


def test_logout_removes_only_selected_account_and_preserves_other_accounts(tmp_path: Path):
    fake = FakeHTTP()
    service = _service(tmp_path, fake)
    try:
        first, first_status, _ = _complete_login(service, fake)
        assert first_status == 200
        second, second_status, _ = _complete_login(service, fake)
        assert second_status == 200
        assert {item["id"] for item in service.status()["accounts"]} == {
            first["accountId"], second["accountId"]
        }

        assert service.logout(first["accountId"]) == {"revoked": True, "localCleared": True}
        remaining = service.status()["accounts"]
        assert [item["id"] for item in remaining] == [second["accountId"]]
        stored = service._storage.load_unlocked()["accounts"]
        assert [item["id"] for item in stored] == [second["accountId"]]
    finally:
        service.close()


def test_callback_expiry_is_checked_even_if_listener_is_delayed(tmp_path: Path):
    monotonic = {"value": 100.0}
    fake = FakeHTTP()
    service = _service(tmp_path, fake, monotonic=lambda: monotonic["value"])
    try:
        result = service.begin()
        params = parse_qs(urlsplit(result["authorizationUrl"]).query)
        monotonic["value"] += result["expiresIn"] + 1
        query = urlencode(
            {"code": "late-code", "state": params["state"][0], "client_id": fake.issued_client_id}
        )
        assert service._process_callback(service._pending, query)[0] == 410
        assert fake.token_calls == 0
        assert service.status()["error"] == "Authorization timed out."
    finally:
        service.close()


def test_callback_cancelled_while_waiting_to_commit_does_not_save_tokens(tmp_path: Path):
    fake = FakeHTTP()
    service = _service(tmp_path, fake)
    try:
        result = service.begin()
        params = parse_qs(urlsplit(result["authorizationUrl"]).query)
        fake.expected_nonce = params["nonce"][0]
        query = urlencode(
            {"code": "auth-code", "state": params["state"][0], "client_id": fake.issued_client_id}
        )
        original_locked = service._storage.locked
        trying_to_commit = threading.Event()
        callback_result: list[tuple[int, str]] = []

        @contextmanager
        def observe_callback_lock():
            if threading.current_thread().name == "oauth-callback-test":
                trying_to_commit.set()
            with original_locked():
                yield

        service._storage.locked = observe_callback_lock
        with original_locked():
            callback = threading.Thread(
                target=lambda: callback_result.append(
                    service._process_callback(service._pending, query)
                ),
                name="oauth-callback-test",
            )
            callback.start()
            assert trying_to_commit.wait(timeout=3)
            service.cancel()
        callback.join(timeout=3)
        assert not callback.is_alive()
        assert callback_result[0][0] == 400
        assert service.status()["accounts"] == []
    finally:
        service.close()


def test_begin_calls_are_serialized_for_the_full_startup(tmp_path: Path):
    service = _service(tmp_path)
    fake = service._http
    entered_storage = threading.Event()
    release_storage = threading.Event()
    original_locked = service._storage.locked
    results: list[dict] = []

    @contextmanager
    def hold_first_startup():
        entered_storage.set()
        assert release_storage.wait(timeout=3)
        with original_locked():
            yield

    service._storage.locked = hold_first_startup
    first = threading.Thread(target=lambda: results.append(service.begin()), name="first-begin")
    first.start()
    try:
        assert entered_storage.wait(timeout=2)
        with pytest.raises(ChatGPTAuthError, match="already starting"):
            service.begin()
    finally:
        release_storage.set()
        first.join(timeout=3)
        service.close()
    assert len(results) == 1
    assert service.status()["pending"] is False


def test_logout_raises_safe_error_when_local_token_clear_fails(tmp_path: Path):
    fake = FakeHTTP()
    service = _service(tmp_path, fake)
    try:
        result, status, _ = _complete_login(service, fake)
        assert status == 200

        def fail_protection(_payload: bytes) -> bytes:
            raise OSError("access-one refresh-one")

        service._storage._protect = fail_protection
        with pytest.raises(ChatGPTAuthError, match="Local credentials could not be cleared") as error:
            service.logout(result["accountId"])
        assert "access-one" not in str(error.value)
        assert "refresh-one" not in str(error.value)
    finally:
        service.close()


def test_cancel_closes_the_owned_loopback_listener(tmp_path: Path):
    service = _service(tmp_path)
    result = service.begin()
    pending = service._pending
    assert pending is not None and pending.thread is not None
    service.cancel()
    pending.thread.join(timeout=2)
    try:
        assert not pending.thread.is_alive()
        assert service.status()["pending"] is False
        assert service.status()["error"] == "Authorization cancelled."
        assert result["redirectUri"].startswith("http://127.0.0.1:")
    finally:
        service.close()


def test_close_singleton_closes_service_created_before_oauth_routes(tmp_path: Path, monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(oauth_service_module, "_singleton", None)
    monkeypatch.setattr(oauth_service_module.httpx, "Client", lambda **_kwargs: fake)

    service = get_chatgpt_auth_service()
    assert service._http is fake
    assert not fake.closed
    close_chatgpt_auth_service()
    assert fake.closed
    assert oauth_service_module._singleton is None


def test_close_singleton_does_not_create_service_when_absent(monkeypatch):
    monkeypatch.setattr(oauth_service_module, "_singleton", None)
    monkeypatch.setattr(
        oauth_service_module.httpx,
        "Client",
        lambda **_kwargs: pytest.fail("close helper must not create a client"),
    )
    close_chatgpt_auth_service()
    assert oauth_service_module._singleton is None


def test_storage_uses_actual_current_user_dpapi_on_windows(tmp_path: Path):
    if os.name != "nt":
        pytest.skip("Windows DPAPI is available only on Windows.")
    storage = ChatGPTAuthStorage(tmp_path / "dpapi", protector=None, unprotector=None)
    value = {
        "schema": "vrcforge.chatgpt_oauth.v1",
        "host_id": "urn:uuid:6a75d9db-734b-43f7-bdd6-6cc5c16cc77e",
        "next_label_number": 2,
        "accounts": [
            {
                "id": "account-1",
                "label": "ChatGPT account 1",
                "subject": "subject-1",
                "client_id": "oaiapp_test",
                "email": "person@example.test",
                "access_token": "dpapi-roundtrip-access",
                "refresh_token": "dpapi-roundtrip-refresh",
                "scopes": ["chatgpt.tokens.use.direct"],
            }
        ],
    }
    with storage.locked():
        storage.save_unlocked(value)
        protected = storage.path.read_bytes()
        assert b"dpapi-roundtrip-access" not in protected
        assert storage.load_unlocked() == value

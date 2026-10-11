from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from types import SimpleNamespace

from chatgpt_integration.routes import build_chatgpt_oauth_router
from chatgpt_integration.service import ChatGPTAuthError
from model_provider_adapters import normalize_provider_api_type
from provider_protocol_negotiation import provider_protocol_candidates


class FakeAuth:
    def __init__(self):
        self.calls = []

    def status(self):
        self.calls.append("status")
        return {"accounts": [], "pending": False, "error": ""}

    def begin(self, account):
        self.calls.append(("begin", account))
        return {"authorizationUrl": "https://auth.openai.com/api/accounts/authorize?state=fake"}

    def models(self, account):
        self.calls.append(("models", account))
        if account == "bad":
            raise RuntimeError("secret-token-do-not-expose")
        return [{"id": "available-model", "name": "Available Model"}]

    def cancel(self):
        self.calls.append("cancel")

    def logout(self, account):
        self.calls.append(("logout", account))
        return {"revoked": True, "localCleared": True}

    def close(self):
        self.calls.append("close")


class AuthErrorFake(FakeAuth):
    def begin(self, account):
        self.calls.append(("begin", account))
        raise ChatGPTAuthError("The selected ChatGPT account was not found.")


class AccountStatusFake(FakeAuth):
    def status(self):
        self.calls.append("status")
        return {
            "accounts": [{"id": "kept-account", "label": "Kept", "email": "", "connected": False}],
            "pending": False,
            "error": "",
        }


def setup_app(auth=None, deactivate=None, current_config=None):
    auth = auth or FakeAuth()
    saved = []

    def authorize(request):
        if request.headers.get("authorization") != "Bearer app-session":
            raise HTTPException(401)

    async def activate(config):
        saved.append(config)
        return {"ok": True}

    app = FastAPI()
    app.include_router(build_chatgpt_oauth_router(
        authorize=authorize, activate=activate, service_factory=lambda: auth,
        deactivate=deactivate, current_config=current_config,
    ))
    return app, auth, saved


def test_logout_deactivates_only_after_local_account_clear():
    deactivated = []
    app, auth, saved = setup_app(deactivate=lambda account: _record_deactivation(deactivated, account))
    with TestClient(app, headers=HEADERS) as client:
        response = client.post("/api/chatgpt/logout", json={"account_id": "account"})
        assert response.status_code == 200
        assert deactivated == ["account"]
        assert auth.calls == [("logout", "account")]
        assert saved == []


async def _record_deactivation(target, account):
    target.append(account)


def test_status_does_not_advertise_removed_active_account():
    app, auth, _ = setup_app(
        auth=AccountStatusFake(),
        current_config=lambda: SimpleNamespace(provider="chatgpt", api_key="removed-account", model="removed-model"),
    )
    with TestClient(app, headers=HEADERS) as client:
        response = client.get("/api/chatgpt/status")
        assert response.status_code == 200
        assert response.json()["activeAccountId"] == ""
        assert response.json()["activeModel"] == ""
    assert auth.calls[0] == "status"
    assert auth.calls[-1] == "close"


HEADERS = {"Authorization": "Bearer app-session"}


def test_all_routes_require_app_auth_before_creating_owner():
    app, auth, saved = setup_app()
    with TestClient(app) as client:
        assert client.get("/api/chatgpt/status").status_code == 401
        for path in ("login", "cancel", "logout", "models", "activate"):
            body = {"account_id": "account"}
            if path == "activate":
                body["model"] = "available-model"
            assert client.post(f"/api/chatgpt/{path}", json=body).status_code == 401
    assert auth.calls == []
    assert saved == []


def test_login_is_explicit_no_model_calls_and_callback_url_not_cached():
    app, auth, saved = setup_app()
    with TestClient(app, headers=HEADERS) as client:
        assert client.get("/api/chatgpt/status").json()["pending"] is False
        response = client.post("/api/chatgpt/login", json={})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert auth.calls == ["status", ("begin", "")]
        assert saved == []
    assert auth.calls[-1] == "close"


def test_activate_pins_account_model_protocol_and_official_endpoint():
    app, auth, saved = setup_app()
    with TestClient(app, headers=HEADERS) as client:
        response = client.post("/api/chatgpt/activate", json={"account_id": "account", "model": "available-model"})
        assert response.status_code == 200
        config = saved[0]
        assert (config.provider, config.api_key, config.api_type, config.base_url) == (
            "chatgpt", "account", "responses", "https://api.openai.com/v1",
        )
        assert auth.calls == [("models", "account")]
        assert client.post("/api/chatgpt/activate", json={"account_id": "account", "model": "not-available"}).status_code == 400
        assert len(saved) == 1
        assert client.post("/api/chatgpt/activate", json={"account_id": "account", "model": "available-model", "base_url": "https://other.invalid"}).status_code == 422


def test_provider_error_body_never_reaches_ui():
    app, _, _ = setup_app()
    with TestClient(app, headers=HEADERS) as client:
        response = client.post("/api/chatgpt/models", json={"account_id": "bad"})
        assert response.status_code == 400
        assert "secret-token" not in response.text


def test_safe_chatgpt_auth_error_reaches_ui_without_provider_details():
    auth = AuthErrorFake()
    app, _, _ = setup_app(auth)
    with TestClient(app, headers=HEADERS) as client:
        response = client.post("/api/chatgpt/login", json={"account_id": "missing"})
        assert response.status_code == 400
        assert response.json()["detail"] == "The selected ChatGPT account was not found."


def test_unexpected_auth_exception_remains_generic():
    app, _, _ = setup_app(FakeAuth())
    with TestClient(app, headers=HEADERS) as client:
        response = client.post("/api/chatgpt/models", json={"account_id": "bad"})
        assert response.status_code == 400
        assert response.json()["detail"] == "ChatGPT connection operation failed. Check connection status or sign in again."


def test_chatgpt_protocol_has_no_paid_fallback():
    assert normalize_provider_api_type("chatgpt", "account-model", None) == ("responses", "responses")
    candidates = provider_protocol_candidates("chatgpt", "account-model", "auto")
    assert len(candidates) == 1
    assert candidates[0].api_type == "responses"
    import pytest
    with pytest.raises(ValueError):
        normalize_provider_api_type("chatgpt", "account-model", "chat_completions")

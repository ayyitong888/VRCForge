import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from telegram_bot_routes import build_telegram_router
from telegram_bot_settings import TelegramSettings


def test_general_configuration_route_keeps_auth_and_project_limit(tmp_path):
    settings = TelegramSettings(tmp_path / "route.dpapi", protect=lambda data: data, unprotect=lambda data: data)

    class Service:
        async def configure(self, **kwargs):
            settings.save(**kwargs)

        def status(self):
            return settings.projection()

    def authorize(request):
        if request.headers.get("authorization") != "Bearer local-owner":
            raise HTTPException(401)

    app = FastAPI()
    app.include_router(build_telegram_router(authorize=authorize, service=Service()))
    body = {"token": "123:" + "a" * 25, "ownerId": 42, "projects": []}
    with TestClient(app) as client:
        assert client.put("/api/telegram/config", json=body).status_code == 401
        headers = {"authorization": "Bearer local-owner"}
        response = client.put("/api/telegram/config", headers=headers, json=body)
        assert response.status_code == 200
        assert response.json()["configured"] is True
        assert response.json()["projects"] == []
        assert client.put("/api/telegram/config", headers=headers,
                          json={**body, "projects": [str(tmp_path)] * 33}).status_code == 422
        assert client.put("/api/telegram/config", headers=headers,
                          json={**body, "ownerId": 0}).status_code == 422


def test_general_configuration_accepts_no_projects_but_preserves_auth_and_path_checks(tmp_path):
    settings = TelegramSettings(tmp_path / "general.dpapi", protect=lambda data: data, unprotect=lambda data: data)
    settings.save(token="123:" + "a" * 25, owner_id=42, projects=[])
    assert settings.projection() == {"configured": True, "ownerId": 42, "projects": [], "proxyMode": "environment", "proxyUrl": ""}
    with pytest.raises(ValueError):
        settings.save(token="", owner_id=0, projects=[])
    with pytest.raises(ValueError):
        settings.save(token="invalid", owner_id=42, projects=[])
    with pytest.raises(ValueError):
        settings.save(token="", owner_id=42, projects=[str(tmp_path / "missing")])


def test_protected_config_projection_and_atomic_failure(tmp_path):
    settings = TelegramSettings(tmp_path / "telegram.dpapi", protect=lambda b: b[::-1], unprotect=lambda b: b[::-1])
    token = "123:" + "a" * 25
    settings.save(token=token, owner_id=42, projects=[str(tmp_path)])
    before = settings.path.read_bytes()
    assert token.encode() not in before
    assert "token" not in settings.projection()
    assert settings.load()["token"] == token
    settings.save(token="", owner_id=43, projects=[str(tmp_path)])
    assert settings.load()["token"] == token
    with pytest.raises(ValueError):
        settings.save(token=token, owner_id=43, projects=[str(tmp_path / "missing")])
    assert settings.load()["ownerId"] == 43


def test_proxy_settings_round_trip_without_credentials(tmp_path):
    settings = TelegramSettings(tmp_path / "telegram.dpapi", protect=lambda b: b[::-1], unprotect=lambda b: b[::-1])
    token = "123:" + "a" * 25
    settings.save(token=token, owner_id=42, projects=[str(tmp_path)],
                  proxy_mode="custom", proxy_url="https://proxy.example:8443/")
    assert settings.projection()["proxyMode"] == "custom"
    assert settings.projection()["proxyUrl"] == "https://proxy.example:8443/"
    for mode, url in (("custom", "socks5://127.0.0.1:1080"),
                      ("custom", "http://user:pass@proxy.example:8080"),
                      ("direct", "http://proxy.example:8080")):
        with pytest.raises(ValueError):
            settings.save(token=token, owner_id=42, projects=[str(tmp_path)],
                          proxy_mode=mode, proxy_url=url)


def test_router_authorization_and_safe_error(tmp_path):
    class Service:
        def status(self):
            return {"enabled": False, "configured": False, "ownerId": None, "projects": [], "state": "disabled"}

        async def configure(self, **kwargs):
            raise ValueError("SECRET SHOULD NOT LEAK")

        async def start(self):
            raise ValueError("SECRET SHOULD NOT LEAK")

        async def stop(self):
            pass

    def authorize(request):
        if request.headers.get("authorization") != "Bearer local-owner":
            raise HTTPException(401)

    app = FastAPI()
    app.include_router(build_telegram_router(authorize=authorize, service=Service()))
    with TestClient(app) as client:
        assert client.get("/api/telegram/status").status_code == 401
        headers = {"authorization": "Bearer local-owner"}
        response = client.get("/api/telegram/status", headers=headers)
        assert response.headers["cache-control"] == "no-store"
        for path, body in (("/config", {"token": "secret", "ownerId": 42, "projects": [str(tmp_path)]}),
                           ("/start", None)):
            response = client.request("PUT" if body else "POST", "/api/telegram" + path,
                                      headers=headers, json=body)
            assert response.status_code == 400
            assert "SECRET" not in response.text


def test_router_test_connection_uses_saved_service_and_safe_error():
    class Service:
        def status(self):
            return {"configured": True}

        async def test_connection(self):
            return {"ok": True}

    app = FastAPI()
    app.include_router(build_telegram_router(authorize=lambda request: None, service=Service()))
    with TestClient(app) as client:
        response = client.post("/api/telegram/test-connection")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

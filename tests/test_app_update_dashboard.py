from __future__ import annotations

import asyncio

import dashboard_server


class FakeAppUpdateService:
    def __init__(self) -> None:
        self.calls = 0

    def check(self, *, refresh: bool = False) -> dict[str, object]:
        self.calls += 1
        self.refresh = refresh
        return {
            "ok": False,
            "schema": "vrcforge.app_update.v1",
            "status": "unavailable",
            "currentVersion": "1.6.0",
            "latestVersion": "",
            "releaseUrl": "",
            "shouldNotify": False,
        }


def test_app_update_route_is_app_only_and_has_no_mode(monkeypatch) -> None:
    fake = FakeAppUpdateService()
    monkeypatch.setattr(dashboard_server, "APP_UPDATE_SERVICE", fake)

    result = asyncio.run(dashboard_server.check_agentic_app_update())

    assert result["schema"] == "vrcforge.app_update.v1"
    assert result["shouldNotify"] is False
    assert fake.calls == 1
    routes = {
        (route.path, tuple(getattr(route, "methods", None) or ()))
        for route in dashboard_server.app.routes
    }
    assert ("/api/app/update", ("GET",)) in routes
    tool_names = {tool["name"] for tool in dashboard_server.AGENT_GATEWAY.build_tool_registry()["tools"]}
    assert "app_update" not in tool_names
    assert "check_app_update" not in tool_names
    assert "vrcforge_check_app_update" not in tool_names


def test_app_update_route_forwards_explicit_refresh(monkeypatch) -> None:
    fake = FakeAppUpdateService()
    monkeypatch.setattr(dashboard_server, "APP_UPDATE_SERVICE", fake)

    asyncio.run(dashboard_server.check_agentic_app_update(refresh=True))

    assert fake.calls == 1
    assert fake.refresh is True

from __future__ import annotations

from types import SimpleNamespace

import dashboard_server


def _settings():
    return SimpleNamespace(unity_mcp_timeout_seconds=30)


def test_prepared_refresh_wiring_polls_existing_core_job_and_accepts_done_without_ok(monkeypatch):
    calls = []

    def invoke(_settings, tool, arguments, **_kwargs):
        calls.append((tool, dict(arguments)))
        if tool == "vrc_refresh_asset_database":
            return dashboard_server.McpResult(0, "", "", {"structuredContent": {"status": "queued", "job_id": "a" * 32}})
        assert tool == "vrc_poll_job"
        return dashboard_server.McpResult(0, "", "", {"structuredContent": {"job_id": "a" * 32, "status": "done", "after": {"compile": {"errorCount": 0, "isCompiling": False, "captureComplete": True, "isStale": False}}}})

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.PREPARED_OUTFIT_IMPORT_APPROVED_WRITE._ports.refresh_assets(
        _settings(), {"projectPath": "D:/Fixture", "resolvePackages": False, "packageResolveTimeoutSeconds": 120}
    )
    assert result["ok"] is True and result["status"] == "done" and result["completionKnown"] is True
    assert [tool for tool, _ in calls] == ["vrc_refresh_asset_database", "vrc_poll_job"]


def test_prepared_refresh_wiring_reports_terminal_compile_failure(monkeypatch):
    calls = []

    def invoke(_settings, tool, arguments, **_kwargs):
        calls.append(tool)
        if tool == "vrc_refresh_asset_database":
            return dashboard_server.McpResult(0, "", "", {"structuredContent": {"status": "queued", "job_id": "b" * 32}})
        return dashboard_server.McpResult(0, "", "", {"structuredContent": {"job_id": "b" * 32, "status": "done", "after": {"compile": {"errorCount": 2, "isCompiling": False, "captureComplete": True, "isStale": False}}}})

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.PREPARED_OUTFIT_IMPORT_APPROVED_WRITE._ports.refresh_assets(_settings(), {"projectPath": "D:/Fixture", "resolvePackages": False, "packageResolveTimeoutSeconds": 120})
    assert result["ok"] is False and result["status"] == "failed" and result["completionKnown"] is True
    assert calls == ["vrc_refresh_asset_database", "vrc_poll_job"]

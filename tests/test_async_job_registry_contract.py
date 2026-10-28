from __future__ import annotations

import re
from pathlib import Path

import pytest

import unity_mcp_tool_contract


@pytest.fixture(autouse=True)
def non_saving_core_capability(monkeypatch):
    import dashboard_server

    class Core:
        def __init__(self, *_args, **_kwargs):
            pass

        def core_info(self):
            return {"assetDatabaseRefreshSavesDirtyAssets": False}

    monkeypatch.setattr(dashboard_server, "UnityMcpCoreClient", Core)


ROOT = Path(__file__).resolve().parents[1]
EDITOR_STATE = (ROOT / "Assets" / "VRCForge" / "Editor" / "EditorStateTools.cs").read_text(
    encoding="utf-8-sig"
)
C_SHARP_CONTRACT = (
    ROOT / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpToolContract.cs"
).read_text(encoding="utf-8-sig")


def test_poll_job_is_a_fixed_read_only_core_tool() -> None:
    declaration = EDITOR_STATE[EDITOR_STATE.index('toolId: "vrc_poll_job"') :]

    assert 'Access = VRCForgeCommandAccess.ReadOnly' in declaration
    assert 'Category = "diagnostics"' in declaration
    assert "When to use:" in declaration
    assert "When NOT to use:" in declaration
    assert 'public string job_id' in declaration
    assert '{ "vrc_poll_job", "VRCForge.Editor.AsyncJobPollTool" }' in C_SHARP_CONTRACT
    assert '"vrc_poll_job",' in C_SHARP_CONTRACT[C_SHARP_CONTRACT.index("ExpectedReadOnlyNames") :]
    assert "vrc_poll_job" in unity_mcp_tool_contract.EXPECTED_TOOL_NAMES
    assert "vrc_poll_job" in unity_mcp_tool_contract.READ_ONLY_TOOL_NAMES
    assert "vrc_poll_job" in unity_mcp_tool_contract.PLANNING_TOOL_NAMES


def test_registry_persists_exact_schema_and_bounded_lifecycle() -> None:
    assert 'VRCForge.AsyncJob.Index.v1' in EDITOR_STATE
    assert 'VRCForge.AsyncJob.Record.v1.' in EDITOR_STATE
    assert 'VRCForge.AsyncJob.Active.v1.' in EDITOR_STATE
    assert 'vrcforge.async-job.v1' in EDITOR_STATE
    assert 'Guid.NewGuid().ToString("N").ToLowerInvariant()' in EDITOR_STATE
    assert 'TimeSpan.FromMinutes(15)' in EDITOR_STATE
    assert '["status"] = "queued"' in EDITOR_STATE
    assert 'record["status"] = "running"' in EDITOR_STATE
    assert 'record["status"] = "expired"' in EDITOR_STATE
    assert 'status == "done" || status == "failed" || status == "expired"' in EDITOR_STATE


def test_after_can_only_enter_the_registry_through_a_read_delegate() -> None:
    complete_signature = re.search(
        r"internal static JObject Complete\(string jobId, Func<JObject> readAfter\)",
        EDITOR_STATE,
    )
    assert complete_signature
    complete_body = EDITOR_STATE[complete_signature.start() : EDITOR_STATE.index("internal static JObject Fail", complete_signature.end())]
    assert "var after = readAfter();" in complete_body
    assert 'return SetTerminal(jobId, "done", after, null);' in complete_body
    assert "Complete(string jobId, JObject after)" not in EDITOR_STATE


def test_unknown_job_has_canonical_non_retrying_failure_envelope() -> None:
    poll_tool = EDITOR_STATE[EDITOR_STATE.index("public static class AsyncJobPollTool") :]
    assert '["before"] = JValue.CreateNull()' in poll_tool
    assert '["after"] = JValue.CreateNull()' in poll_tool
    assert '["status"] = "failed"' in poll_tool
    assert '["code"] = "job_not_found"' in poll_tool
    assert '["retryable"] = false' in poll_tool
    assert "Create(" not in poll_tool


def test_refresh_keeps_response_release_order_and_completes_from_fresh_readback() -> None:
    importer = (ROOT / "Assets" / "VRCForge" / "Editor" / "OutfitPackageImporter.cs").read_text(
        encoding="utf-8-sig"
    )
    refresh = importer[importer.index("public static class AssetDatabaseRefreshTool") :]
    handler = refresh[
        refresh.index("public static object HandleCommand") : refresh.index(
            "private static void RunScheduledRefresh"
        )
    ]
    runner = refresh[
        refresh.index("private static void RunScheduledRefresh") : refresh.index(
            "private static void TryCompleteScheduledRefresh"
        )
    ]

    assert "UnityAsyncJobRegistry.Create(" in handler
    assert "EditorApplication.update += RunScheduledRefresh;" in handler
    assert "AssetDatabase.Refresh" not in handler
    assert runner.index("UnityAsyncJobRegistry.MarkRunning(requestId);") < runner.index(
        "AssetDatabase.Refresh();"
    )
    assert "UnityAsyncJobRegistry.Complete(jobId, () => ReadSnapshot(reimportAssets));" in refresh
    assert "AssetDatabase.GetAllAssetPaths()" in refresh
    assert "asset_path_digest" in refresh


def test_dashboard_refresh_starts_then_polls_the_same_job(monkeypatch) -> None:
    import dashboard_server

    calls: list[tuple[str, dict[str, object]]] = []
    job_id = "b" * 32

    class Settings:
        unity_mcp_timeout_seconds = 30
        unity_project_path = "D:/isolated"

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: Settings())
    monkeypatch.setattr(dashboard_server, "build_agent_connection_request", lambda _params: {})

    def invoke(_settings, tool_name, arguments, **_kwargs):
        calls.append((tool_name, dict(arguments)))
        payload = (
            {
                "job_id": job_id,
                "before": {"asset_path_digest": "before"},
                "after": None,
                "status": "queued",
                "requestId": job_id,
            }
            if tool_name == "vrc_refresh_asset_database"
            else {
                "job_id": job_id,
                "before": {"asset_path_digest": "before"},
                "after": {
                    "asset_path_digest": "after",
                    "compile": {
                        "isCompiling": False,
                        "captureComplete": False,
                        "errorCount": 0,
                    },
                },
                "status": "done",
            }
        )
        if tool_name == "vrc_get_compile_errors":
            payload = {
                "ok": True,
                "isCompiling": False,
                "captureComplete": True,
                "hasErrors": False,
                "hasWarnings": False,
                "errorCount": 0,
                "warningCount": 0,
                "errors": [],
                "warnings": [],
                "source": "console_log",
            }
        return dashboard_server.McpResult(0, "", "", payload)

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)

    result = dashboard_server.refresh_asset_database_sync(
        {"projectPath": "D:/isolated", "resolvePackages": False}
    )

    assert calls == [
        (
            "vrc_refresh_asset_database",
            {
                "projectPath": "D:/isolated",
                "resolvePackages": False,
                "packageResolveTimeoutSeconds": 120,
            },
        ),
        ("vrc_poll_job", {"job_id": job_id}),
        ("vrc_get_compile_errors", {"includeConsoleFallback": True, "maxErrors": 200}),
    ]
    assert result["before"] == {"asset_path_digest": "before"}
    assert result["after"]["asset_path_digest"] == "after"
    assert result["after"]["compile"]["source"] == "console_log"
    assert result["status"] == "done"
    assert result["ok"] is True


@pytest.mark.parametrize("cause_code", ["unity_core_starting", "unity_core_connection_failed"])
def test_dashboard_refresh_retries_poll_when_core_is_starting(monkeypatch, cause_code) -> None:
    import dashboard_server

    calls: list[str] = []
    sleeps: list[float] = []
    job_id = "c" * 32

    class Settings:
        unity_mcp_timeout_seconds = 30
        unity_project_path = "D:/isolated"

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: Settings())
    monkeypatch.setattr(dashboard_server, "build_agent_connection_request", lambda _params: {})
    monkeypatch.setattr(dashboard_server.time, "sleep", lambda seconds: sleeps.append(seconds))

    def invoke(_settings, tool_name, _arguments, **_kwargs):
        calls.append(tool_name)
        if tool_name == "vrc_refresh_asset_database":
            return dashboard_server.McpResult(0, "", "", {"job_id": job_id, "status": "queued"})
        if calls.count("vrc_poll_job") == 1:
            assert _arguments == {"job_id": job_id}
            raise dashboard_server.UnityMcpError(
                "Core descriptor is temporarily absent during domain reload.",
                cause_code=cause_code,
                retryable=True,
                core_tool="vrc_poll_job",
            )
        if tool_name == "vrc_get_compile_errors":
            return dashboard_server.McpResult(
                0, "", "", {"isCompiling": False, "captureComplete": True, "errorCount": 0, "errors": [], "warnings": [], "source": "console_log"}
            )
        assert tool_name == "vrc_poll_job"
        assert _arguments == {"job_id": job_id}
        return dashboard_server.McpResult(0, "", "", {"job_id": job_id, "status": "done"})

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.refresh_asset_database_sync(
        {"projectPath": "D:/isolated", "resolvePackages": False}
    )

    assert result["status"] == "done"
    assert result["ok"] is True
    assert calls == ["vrc_refresh_asset_database", "vrc_poll_job", "vrc_poll_job", "vrc_get_compile_errors"]
    assert sleeps == [dashboard_server.UNITY_ASYNC_JOB_POLL_SECONDS]


@pytest.mark.parametrize("cause_code,error_message", [
    ("unity_core_connection_failed", "Core connection failed."),
    ("unity_editor_window_probe_failed", "VRCForge could not inspect the target Unity editor for a blocking Reload dialog. No Core tool was called."),
])
def test_dashboard_refresh_does_not_retry_non_reload_poll_error(monkeypatch, cause_code, error_message) -> None:
    import dashboard_server

    calls: list[str] = []
    job_id = "d" * 32

    class Settings:
        unity_mcp_timeout_seconds = 30
        unity_project_path = "D:/isolated"

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: Settings())
    monkeypatch.setattr(dashboard_server, "build_agent_connection_request", lambda _params: {})

    def invoke(_settings, tool_name, _arguments, **_kwargs):
        calls.append(tool_name)
        if tool_name == "vrc_refresh_asset_database":
            return dashboard_server.McpResult(0, "", "", {"job_id": job_id, "status": "queued"})
        raise dashboard_server.UnityMcpError(
            error_message,
            cause_code=cause_code,
            retryable=False,
            core_tool="vrc_poll_job",
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.refresh_asset_database_sync(
        {"projectPath": "D:/isolated", "resolvePackages": False}
    )

    assert result["status"] == "pending"
    assert result["job_id"] == job_id
    assert result["error"]["code"] == cause_code
    assert result["commitState"] == "unknown"
    assert result["safeToRetry"] is False
    assert "already submitted" in result["error"]["message"]
    assert job_id in result["nextAction"]
    assert "vrcforge_core_upgrade_status" in result["nextAction"]
    assert calls == ["vrc_refresh_asset_database", "vrc_poll_job"]
    from agent_tool_result_contract import normalize_agent_tool_result
    from agent_gateway import AgentGateway
    internal = normalize_agent_tool_result(result, fallback_summary="Refresh observation failed", write=True)
    external = AgentGateway._external_mcp_write_result(AgentGateway.__new__(AgentGateway),
        "vrcforge_refresh_asset_database", {"ok": False, "result": result})["outcome"]
    for outcome in (internal, external):
        assert "already submitted" in outcome["summary"]
        assert outcome["error"]["code"] == cause_code
        assert outcome["nextAction"] == result["nextAction"]
        assert outcome["commitState"] == "unknown"


@pytest.mark.parametrize("status,error", [
    ("done", None),
    ("failed", {"code": "job_not_found", "message": "Async job was not found.", "retryable": False}),
    ("failed", {"code": "refresh_failed", "message": "Asset import failed.", "retryable": False}),
])
def test_unified_poll_preserves_confirmed_terminal_status(monkeypatch, status, error):
    import dashboard_server

    job_id = "f" * 32
    payload = {"job_id": job_id, "status": status, "error": error}
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_a, **_kw:
        dashboard_server.McpResult(0, "", "", payload))
    result = dashboard_server.poll_unity_async_job_sync(
        object(), {"job_id": job_id, "status": "queued"}, label="refresh asset database", timeout_seconds=30)
    assert result["status"] == status
    assert result["error"] == error
    assert result["ok"] is (status == "done")
    assert "pending" not in result


def test_unified_poll_bounded_wait_keeps_running_job_unknown(monkeypatch):
    import dashboard_server

    ticks = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(dashboard_server.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(dashboard_server.time, "sleep", lambda _seconds: None)
    job_id = "e" * 32
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_a, **_kw:
        dashboard_server.McpResult(0, "", "", {"job_id": job_id, "status": "running"}))
    result = dashboard_server.poll_unity_async_job_sync(
        object(), {"job_id": job_id, "status": "queued"}, label="refresh asset database", timeout_seconds=0)
    assert result["status"] == "pending"
    assert result["pending"] is True
    assert result["job_id"] == job_id
    assert result["error"]["code"] == "async_job_poll_timeout"
    assert "already submitted" in result["error"]["message"]
    assert result["commitState"] == "unknown"
    assert result["safeToRetry"] is False


def test_retryable_poll_connection_failure_still_respects_deadline(monkeypatch):
    import dashboard_server

    ticks = iter([0.0, 0.0, 0.0, 2.0])
    sleeps = []
    calls = []
    monkeypatch.setattr(dashboard_server.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(dashboard_server.time, "sleep", sleeps.append)
    job_id = "c" * 32

    def disconnected(_settings, name, arguments, **_kwargs):
        calls.append((name, arguments))
        raise dashboard_server.UnityMcpError(
            "Unity MCP Core connection failed during reload.",
            cause_code="unity_core_connection_failed", retryable=True, core_tool=name)

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", disconnected)
    result = dashboard_server.poll_unity_async_job_sync(
        object(), {"job_id": job_id, "status": "running"}, label="refresh asset database", timeout_seconds=1)
    assert result["status"] == "pending"
    assert result["error"]["code"] == "unity_core_connection_failed"
    assert result["error"]["retryable"] is True
    assert result["job_id"] == job_id
    assert result["commitState"] == "unknown"
    assert result["safeToRetry"] is False
    assert calls == [("vrc_poll_job", {"job_id": job_id})]
    assert sleeps == [dashboard_server.UNITY_ASYNC_JOB_POLL_SECONDS]


@pytest.mark.parametrize(
    ("compile_payload", "expected_status"),
    [
        (None, "pending"),
        ({"isCompiling": True, "captureComplete": True, "errorCount": 0}, "pending"),
        ({"isCompiling": False, "captureComplete": True, "errorCount": 1, "errors": [{"message": "CS1002"}]}, "failed"),
    ],
)
def test_dashboard_refresh_fallback_preserves_incomplete_and_failed_compile_states(
    monkeypatch, compile_payload, expected_status
) -> None:
    import dashboard_server

    job_id = "e" * 32
    calls: list[str] = []

    class Settings:
        unity_mcp_timeout_seconds = 30
        unity_project_path = "D:/isolated"

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: Settings())
    monkeypatch.setattr(dashboard_server, "build_agent_connection_request", lambda _params: {})

    def invoke(_settings, tool_name, _arguments, **_kwargs):
        calls.append(tool_name)
        if tool_name == "vrc_refresh_asset_database":
            return dashboard_server.McpResult(0, "", "", {"job_id": job_id, "status": "queued"})
        if tool_name == "vrc_poll_job":
            return dashboard_server.McpResult(
                0, "", "", {"job_id": job_id, "status": "done", "after": {"compile": {"isCompiling": False, "captureComplete": False, "errorCount": 0}}}
            )
        return dashboard_server.McpResult(
            0,
            "",
            "",
            compile_payload if compile_payload is not None else {},
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.refresh_asset_database_sync({"projectPath": "D:/isolated"})

    assert result["status"] == expected_status
    assert calls == ["vrc_refresh_asset_database", "vrc_poll_job", "vrc_get_compile_errors"]


def test_dashboard_refresh_does_not_use_transport_complete_as_job_completion(monkeypatch) -> None:
    import dashboard_server

    job_id = "f" * 32
    calls: list[str] = []

    class Settings:
        unity_mcp_timeout_seconds = 30
        unity_project_path = "D:/isolated"

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: Settings())
    monkeypatch.setattr(dashboard_server, "build_agent_connection_request", lambda _params: {})

    def invoke(_settings, tool_name, _arguments, **_kwargs):
        calls.append(tool_name)
        return dashboard_server.McpResult(
            0,
            "",
            "",
            {"job_id": job_id, "status": "pending", "resultType": "complete"},
        )

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    result = dashboard_server.refresh_asset_database_sync({"projectPath": "D:/isolated"})

    assert result["status"] == "pending"
    assert result["operationStatus"] == "pending"
    assert calls == ["vrc_refresh_asset_database"]

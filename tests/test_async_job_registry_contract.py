from __future__ import annotations

import re
from pathlib import Path

import unity_mcp_tool_contract


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
    assert "UnityAsyncJobRegistry.Complete(jobId, ReadSnapshot);" in refresh
    assert "AssetDatabase.GetAllAssetPaths()" in refresh
    assert "asset_path_digest" in refresh


def test_dashboard_refresh_starts_then_polls_the_same_job(monkeypatch) -> None:
    import dashboard_server

    calls: list[tuple[str, dict[str, object]]] = []
    job_id = "b" * 32

    class Settings:
        unity_mcp_timeout_seconds = 30

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
                "after": {"asset_path_digest": "after"},
                "status": "done",
            }
        )
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
    ]
    assert result["before"] == {"asset_path_digest": "before"}
    assert result["after"] == {"asset_path_digest": "after"}
    assert result["status"] == "done"
    assert result["ok"] is True

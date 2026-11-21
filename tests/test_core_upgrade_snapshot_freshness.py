from copy import deepcopy

import pytest
import dashboard_server as ds


@pytest.fixture
def status(monkeypatch, tmp_path):
    project = tmp_path / "project"
    descriptor = project / "Library/VRCForge/mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text("{}")
    expected = {"coreIdentity": "vrcforge.unity-core", "coreVersion": "1.7.10", "toolContractVersion": "94",
                "protocolRange": {"minimum": "2026-07-28", "maximum": "2026-07-28"}, "versionSource": "compiled_constant"}
    snapshot = {"source": "compilation_pipeline", "capturedAt": "2026-09-06T09:41:00+09:00", "captureComplete": True,
                "isCompiling": False, "errorCount": 0, "warningCount": 0}
    diagnostic = {"coreInfo": {**expected, "instanceId": "same-instance", "processId": 25008,
                              "compileSnapshot": deepcopy(snapshot)},
                  "compileResult": {"isError": False, "structuredContent": {"success": True,
                                    "data": {**snapshot, "unityProcessId": 25008, "ok": True}}},
                  "transportError": "", "handshakeError": None}
    monkeypatch.setattr(ds, "resolve_target_project", lambda _: str(project))
    monkeypatch.setattr(ds, "_resolve_install_source_assets", lambda: tmp_path / "source")
    monkeypatch.setattr(ds, "_unity_core_compiled_identity", lambda _: expected)
    monkeypatch.setattr(ds, "probe_unity_mcp_core_diagnostics", lambda *args, **kwargs: diagnostic)
    monkeypatch.setattr(ds, "_signal_installed_unity_core_refresh", lambda *args: pytest.fail("read status must not refresh"))
    def call(installed="2026-09-06T09:40:51+09:00"):
        return ds.core_upgrade_status_sync({"projectPath": str(project), "installedAt": installed})
    return diagnostic, call


@pytest.mark.parametrize("patch", [
    {"capturedAt": "2026-09-06T09:14:10+09:00"},
    {"capturedAt": ""},
    {"capturedAt": "invalid"},
    {"capturedAt": "2026-09-06T09:41:00"},
    {"isCompiling": None},
    {"captureComplete": False},
])
def test_old_or_unknown_actual_compile_snapshot_cannot_report_ready(status, patch):
    diagnostics, call = status
    diagnostics["coreInfo"]["compileSnapshot"].update(patch)
    # A newly captured console response must not replace actual compile evidence.
    result = call()
    assert result["runtimeIdentityMatchesTarget"] is True
    assert result["ready"] is False


def test_console_only_snapshot_is_not_compilation_proof(status):
    diagnostics, call = status
    diagnostics["coreInfo"].pop("compileSnapshot")
    diagnostics["compileResult"]["structuredContent"]["data"]["source"] = "console_log"
    assert call()["ready"] is False


@pytest.mark.parametrize("installed", ["bad", "2026-09-06T09:40:51"])
def test_unknown_install_time_cannot_report_post_install_ready(status, installed):
    _, call = status
    assert call(installed)["ready"] is False


def test_completed_fresh_snapshot_can_pass_without_forcing_a_new_instance(status):
    _, call = status
    result = call("2026-09-06T00:40:51Z")
    assert result["ready"] is True  # Same identity can legitimately remain/reload.


def test_fresh_compilation_in_progress_still_waits(status):
    diagnostics, call = status
    diagnostics["coreInfo"]["compileSnapshot"]["isCompiling"] = True
    assert call()["ready"] is False


def test_health_query_without_install_boundary_preserves_existing_ready(status):
    _, call = status
    assert call("")["ready"] is True


@pytest.mark.parametrize("console_readable", [True, False])
def test_existing_core_with_live_read_is_ready_without_a_new_compile(status, console_readable):
    from core_upgrade_readiness import ready_process_binding

    diagnostics, call = status
    incomplete = {"captureComplete": False, "capturedAt": ""}
    diagnostics["coreInfo"]["compileSnapshot"].update(incomplete)
    diagnostics["compileResult"]["structuredContent"]["data"].update(
        capturedAt="", captureComplete=console_readable,
        source="console_log" if console_readable else "unavailable",
    )

    receipt = call("")
    assert receipt["status"] == "ready" and receipt["ready"] is True
    assert receipt["compileSnapshotFresh"] is False
    assert receipt["consoleErrorCount"] == (0 if console_readable else None)
    assert ready_process_binding(receipt)["processId"] == 25008
    assert call()["ready"] is False


@pytest.mark.parametrize("unavailable", ["transport", "handshake", "tool", "other_process", "editor_compiling"])
def test_health_query_requires_a_usable_live_core_read(status, unavailable):
    diagnostics, call = status
    if unavailable == "transport":
        diagnostics["transportError"] = "connection closed during diagnostics"
    elif unavailable == "handshake":
        diagnostics["handshakeError"] = {"message": "project binding rejected"}
    elif unavailable == "tool":
        diagnostics["compileResult"]["isError"] = True
    elif unavailable == "other_process":
        diagnostics["compileResult"]["structuredContent"]["data"]["unityProcessId"] = 28000
    else:
        diagnostics["compileResult"]["structuredContent"]["data"]["isCompiling"] = True
    assert call("")["ready"] is False


def test_stale_console_counts_are_only_historical(status):
    diagnostics, call = status
    diagnostics["coreInfo"]["compileSnapshot"].update(capturedAt="2026-08-01T00:00:00Z", errorCount=2)
    receipt = call()
    assert receipt["status"] == "waiting_for_post_install_compile"
    assert receipt["console"]["errorCount"] == 2 and receipt["consoleErrorCount"] is None
    assert receipt["nextAction"]["arguments"]["installedAt"] == receipt["installedAt"]


def test_verified_compile_failure_enters_existing_failure_channel(status):
    from agent_tool_result_contract import normalize_agent_tool_result

    diagnostics, call = status
    diagnostics["coreInfo"]["compileSnapshot"].update(errorCount=1, errors=[{"message": "CS1001"}])
    receipt = call()
    assert receipt["console"]["errors"] == [{"message": "CS1001"}]
    assert receipt["consoleErrorCount"] == 1 and "nextAction" not in receipt
    assert normalize_agent_tool_result(receipt, fallback_summary="status", write=False)["status"] == "failed"


def test_unscoped_old_or_other_project_log_is_only_a_hint_after_install(status, monkeypatch, tmp_path):
    _, call = status
    project = tmp_path / "project"
    (project / "Library/VRCForge/mcp-core.json").unlink()
    source = project / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs"
    source.parent.mkdir(parents=True)
    source.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    log = tmp_path / "Unity/Editor/Editor.log"
    log.parent.mkdir(parents=True)
    log.write_text("-projectPath\nother-project\n[VRCForge MCP] Core failed to start: old failure\n", encoding="utf-8")
    monkeypatch.setattr(ds, "_unity_editor_process_state", lambda _: "running")
    receipt = call()
    assert receipt["status"] == "waiting_for_domain_reload"
    assert receipt["nextAction"]["tool"] == "unity_core_upgrade_status"
    assert receipt["diagnostics"]["startupDiagnostic"]["message"] == "old failure"
    assert "toolExecutionStatus" not in receipt
    assert call("")["status"] == "core_start_failed"


@pytest.mark.parametrize("ending", ["ready", "timeout", "cancel", "compile_failed"])
def test_native_core_read_waits_without_replaying_installer(tmp_path, monkeypatch, ending):
    import core_upgrade_readiness as cr
    from tests.test_native_runtime_gateway import call, run, setup_gateway

    name = "vrcforge_core_upgrade_status"
    args = {"projectPath": str(tmp_path), "installedAt": "2026-11-20T12:50:55.993299+00:00"}
    reads = []
    clock = [0.0]
    pending = {"schema": "vrcforge.core_upgrade_status.v1", "ok": True, "ready": False,
               "status": "waiting_for_domain_reload",
               "nextAction": {"tool": "unity_core_upgrade_status", "arguments": args}}

    def read(params):
        reads.append((params["projectPath"], params["installedAt"]))
        if ending in {"timeout", "cancel"} or len(reads) == 1:
            return pending
        return {**pending, "status": ending, "nextAction": None, "ready": ending == "ready",
                **({"toolExecutionStatus": "failed", "error": "compile error"} if ending == "compile_failed" else {})}

    gateway, model, _ = setup_gateway(tmp_path, [
        call("status", name, args), {"role": "assistant", "content": "Checked."},
    ])
    gateway.register_tool("vrcforge_install_unity_core", "Install Core.", "write",
                          lambda _: pytest.fail("installer replayed"), write=True)

    def wait(delay, **_):
        clock[0] += delay
        if ending == "cancel":
            gateway.request_runtime_cancel({"sessionId": "native-session", "clientTurnId": "native-turn"})
            return False
        return True

    bounded_wait = cr.await_core_upgrade_snapshot
    monkeypatch.setattr(cr, "await_core_upgrade_snapshot",
                        lambda read, wait: bounded_wait(read, wait, timeout=8, clock=lambda: clock[0]))
    gateway.register_tool(name, "Check Core.", "read/debug", read, await_observation=cr.await_core_upgrade_snapshot)
    monkeypatch.setattr(gateway, "_wait_for_provider_retry", wait)
    result = run(gateway, tmp_path)
    assert len(set(reads)) == 1 and reads[0][1] == args["installedAt"]
    assert [step["tool"] for step in result["steps"]] == [name]
    if ending in {"timeout", "cancel"}:
        assert len(model.requests) == 1
        assert len(reads) == (3 if ending == "timeout" else 1)
        assert result["plan"]["nextStep"] == ("waiting_for_tool" if ending == "timeout" else "cancelled")
        if ending == "timeout":
            assert result["plan"]["reason"] == "tool_poll_timeout"
            gate = result["plan"]["completionGate"]
            assert gate["status"] == "pending" and gate["pollTarget"] == pending["nextAction"]
    else:
        assert len(model.requests) == 2 and len(reads) == 2
        assert result["steps"][0]["result"]["status"] == ending
        assert result["steps"][0]["outcome"]["status"] == ("ok" if ending == "ready" else "failed")

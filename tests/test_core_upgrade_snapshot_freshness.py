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
    diagnostic = {"coreInfo": {**expected, "instanceId": "same-instance", "compileSnapshot": deepcopy(snapshot)},
                  "compileResult": {"structuredContent": {"data": deepcopy(snapshot)}}, "transportError": ""}
    monkeypatch.setattr(ds, "resolve_target_project", lambda _: str(project))
    monkeypatch.setattr(ds, "_resolve_install_source_assets", lambda: tmp_path / "source")
    monkeypatch.setattr(ds, "_verified_unity_core_source_identity", lambda _: {"compiledIdentity": expected})
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

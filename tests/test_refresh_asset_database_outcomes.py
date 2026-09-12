from copy import copy
from pathlib import Path

import pytest

import dashboard_server
from agent_tool_result_contract import normalize_agent_tool_result
from agent_gateway import AgentGateway
from agent_completion_verifier import UnityConsoleCompletionVerifier


def _result(status: str, compile_payload: dict | None) -> dict:
    if compile_payload is not None:
        compile_payload = {"isCompiling": False, "captureComplete": True, **compile_payload}
    return {
        "job_id": "a" * 32,
        "status": status,
        "after": {"compile": compile_payload} if compile_payload is not None else None,
    }


def test_refresh_done_with_compile_error_is_failed() -> None:
    result = dashboard_server.normalize_refresh_asset_database_outcome(
        _result("done", {"errorCount": 1, "errors": [{"message": "CS1002"}]})
    )
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["operationStatus"] == "failed"
    assert result["verification"]["state"] == "failed"
    assert result["error"]["code"] == "unity_compile_failed"
    assert result["commitState"] == "unknown"
    assert result["commitStateKnown"] is False
    assert result["completionKnown"] is True


def test_refresh_done_with_clean_compile_is_complete() -> None:
    result = dashboard_server.normalize_refresh_asset_database_outcome(
        _result("done", {"errorCount": 0, "warningCount": 0, "errors": []})
    )
    assert result["ok"] is True
    assert result["status"] == "done"
    assert result["operationStatus"] == "success"
    assert result["verification"]["state"] == "passed"
    assert result["commitState"] == "unknown"
    assert result["commitStateKnown"] is False
    assert result["completionKnown"] is True
    outcome = normalize_agent_tool_result(result, fallback_summary="Refresh asset database", write=True)
    assert outcome["status"] == "ok"
    assert outcome["success"] is True
    assert outcome["verification"]["state"] == "passed"


def test_refresh_running_without_after_compile_stays_pending() -> None:
    result = dashboard_server.normalize_refresh_asset_database_outcome(_result("running", None))
    assert result["ok"] is False
    assert result["status"] == "pending"
    assert result["operationStatus"] == "pending"
    assert result["verification"]["state"] == "pending"
    assert result["commitState"] == "unknown"
    assert result["commitStateKnown"] is False
    assert result["completionKnown"] is False


def test_real_refresh_failure_record_survives_extraction_and_outcome_normalization() -> None:
    # Minimal portable reproduction of the real done-job / failed-compile envelope.
    raw = _result("done", {"errorCount": 1, "errors": [{"message": "CS0117: missing payload field"}]})
    raw.update({"completionKnown": False, "scheduledStatus": "scheduled", "ok": True})
    result = dashboard_server.McpResult(
        exit_code=1,
        stdout="",
        stderr="",
        payload={"structuredContent": {"ok": False, "result": raw}},
    )
    extracted = dashboard_server.extract_tool_result_payload(result)
    normalized = dashboard_server.normalize_refresh_asset_database_outcome(extracted)
    outcome = normalize_agent_tool_result(
        normalized,
        fallback_summary="Refresh asset database",
        write=True,
    )

    assert normalized["status"] == "failed"
    assert normalized["ok"] is False
    assert normalized["error"]["code"] == "unity_compile_failed"
    assert normalized["completionKnown"] is True
    assert normalized["commitState"] == "unknown"
    assert normalized["commitStateKnown"] is False
    assert outcome["status"] == "failed"
    assert outcome["success"] is False
    assert outcome["verification"]["state"] == "needs_user_action"
    assert outcome["error"]["code"] == "unity_compile_failed"


def test_compile_snapshot_in_progress_is_pending_even_with_zero_errors() -> None:
    result = dashboard_server.normalize_refresh_asset_database_outcome(
        _result("done", {"isCompiling": True, "captureComplete": True, "errorCount": 0})
    )
    assert result["ok"] is False
    assert result["status"] == "pending"
    assert result["completionKnown"] is False


def test_incomplete_or_stale_compile_snapshot_is_pending() -> None:
    for compile_payload in (
        {"isCompiling": False, "captureComplete": False, "errorCount": 0},
        {"isCompiling": False, "captureComplete": True, "staleness": "stale", "errorCount": 0},
    ):
        result = dashboard_server.normalize_refresh_asset_database_outcome(_result("done", compile_payload))
        assert result["ok"] is False
        assert result["status"] == "pending"
        assert result["verification"]["state"] == "pending"


def test_done_refresh_without_complete_compile_evidence_is_not_success() -> None:
    for compile_payload in (None, {"errorCount": 0}):
        raw = {"status": "done", "after": {"compile": compile_payload}}
        result = dashboard_server.normalize_refresh_asset_database_outcome(raw)
        assert result["ok"] is False
        assert result["operationStatus"] == "pending"
        assert result["completionKnown"] is False


@pytest.mark.parametrize("new_warning", [False, True])
def test_clean_refresh_passes_actual_external_write_completion_guard(monkeypatch, tmp_path: Path, new_warning: bool) -> None:
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    gateway.save_config(config)
    project = tmp_path / "UnityProject"
    for folder in ("Assets", "Packages", "ProjectSettings"):
        (project / folder).mkdir(parents=True)
    name = "vrcforge_refresh_asset_database"
    gateway._write_handlers[name] = copy(dashboard_server.AGENT_GATEWAY._write_handlers[name])
    service = gateway.approval_transactions
    snapshot = {
        "ok": True, "isCompiling": False, "captureComplete": True,
        "truncated": False, "capturedAt": "2026-09-06T00:00:00Z",
        "source": "compilation_pipeline",
        "projectPathDigest": "a" * 64, "unityProcessId": 4242,
        "unityProcessStartedAtUtc": "2026-09-06T00:00:00Z",
        "unityExecutableDigest": "b" * 64, "errors": [], "warnings": [],
    }
    read_count = 0

    def read_snapshot(_):
        nonlocal read_count
        read_count += 1
        return {
            **snapshot,
            "warnings": [{"message": "Existing shader warning reappeared after refresh"}]
            if new_warning and read_count > 1 else [],
        }

    verifier = UnityConsoleCompletionVerifier(read_snapshot, timeout_seconds=2, sleep=lambda _: None)
    monkeypatch.setattr(dashboard_server, "UNITY_CONSOLE_COMPLETION_VERIFIER", verifier)
    monkeypatch.setattr(type(service), "_create_pre_write_checkpoint", lambda *_: {
        "ok": True, "status": "ready", "id": "ckpt_refresh", "projectRoot": str(project),
    })
    monkeypatch.setattr(type(service), "_call_external_mcp_write_handler", lambda *_:
        dashboard_server.normalize_refresh_asset_database_outcome(_result("done", {"errorCount": 0, "errors": []})))
    prepared = service.prepare_external_mcp_write(name, {"projectPath": str(project)})
    result = service.execute_prepared_external_mcp_write(prepared)
    if new_warning:
        assert result["ok"] is False, result
        assert result["result"]["status"] == "done"
        assert result["consoleVerification"]["status"] == "failed"
        assert result["consoleVerification"]["newWarningCount"] == 1
        assert result["error"] == "Unity reported new compile errors or warnings after the write."
        assert result["writeFailure"]["errorCode"] == "unity_console_regression"
        assert "without explicit verification" not in str(result)
        # Preserve the raw async fixture's explicit unknown through the real
        # approval transaction and public gateway projection, not just helpers.
        public = gateway._external_mcp_write_result(name, result)
        for envelope in (result, public):
            assert envelope["commitState"] == "unknown"
            assert envelope["mutationStarted"] is None
            for key in ("writeFailure", "errorDetails"):
                assert envelope[key]["commitState"] == "unknown"
                assert envelope[key]["mutationStarted"] is None
                assert envelope[key]["recovery"]["required"] is True
        assert public["outcome"]["commitState"] == "unknown"
        assert public["outcome"]["mutationStarted"] is None
        assert public["outcome"]["recovery"]["required"] is True
        return
    assert result["ok"] is True, result
    assert result["recovery"]["status"] == "applied"
    assert result["consoleVerification"]["status"] == "passed"

"""Real production loop and repair handlers with a controlled HTTP planner.

Fixture project/editor identity is synthetic; no Unity Editor or live model is
used. The test harness supplies human approval, never the planner response.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import dashboard_server
from agent_task_loop import canonical_action_id
from tests.test_agent_configured_provider_loop import (
    _configured_service,
    _isolated_gateway,
    _planner_loopback,
)


def test_configured_loop_reads_real_chat_diagnostic_before_approved_repair(tmp_path: Path) -> None:
    project = tmp_path / "cost-project"
    for name in ("Assets", "Packages", "ProjectSettings", ".vrcforge"):
        (project / name).mkdir(parents=True)
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    store = project / ".vrcforge" / "chat-transcripts.json"
    original = b'{"chats":['
    store.write_bytes(original)
    records: list[dict[str, object]] = []
    responses = [{
        "action": "skill",
        "skill_tool": "unity_inspect_project_chat_store",
        "skill_params": {"projectPath": str(project)},
        "continueLoop": True,
    }, {
        "action": "reply",
        "reply": "Diagnostic evidence is ready for explicit repair approval.",
        "continueLoop": False,
        "completion_claim": {
            "satisfied": True,
            "evidence_action_ids": [canonical_action_id("skill", "vrcforge_inspect_project_chat_store", {"projectPath": str(project)})],
        },
    }]
    with _planner_loopback(responses, records) as base_url:
        configured = _configured_service(tmp_path / "provider.json", base_url)
        with _isolated_gateway(tmp_path) as gateway:
            with patch.object(dashboard_server, "PROVIDER_CONFIGURATION", configured):
                result = gateway.runtime_message({"message": "Inspect the selected project chat store.", "provider": "custom", "model": "loop-model", "session_id": "repair-loop", "client_turn_id": "repair-loop-turn", "maxAgenticTurns": 3})
    assert result["plan"]["nextStep"] == "done", result
    assert result["steps"][0]["tool"] == "vrcforge_inspect_project_chat_store"
    assert result["steps"][0]["result"]["status"] == "needs_repair"
    assert result["steps"][0]["result"]["digest"] == hashlib.sha256(original).hexdigest()
    assert store.read_bytes() == original
    assert len(records) == 2
    assert all(record["body"]["model"] == "loop-model" for record in records)


def test_configured_loop_pending_approval_then_independent_readback(tmp_path: Path) -> None:
    project = tmp_path / "cost-project"
    for name in ("Assets", "Packages", "ProjectSettings", ".vrcforge"):
        (project / name).mkdir(parents=True)
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    store = project / ".vrcforge" / "chat-transcripts.json"
    original = b'{"chats":['
    store.write_bytes(original)
    target = dashboard_server.project_chat_repair_target(project)
    digest = hashlib.sha256(original).hexdigest()
    execution_target = {
        "schema": "vrcforge.execution_target.v1",
        "namespace": "vrcforge",
        "scope": "project",
        "project": {"root": str(project), "projectId": "fixture"},
        "editor": {"unityPid": 1, "processStartTime": "fixture", "coreInstanceId": "fixture"},
    }
    from tests.test_diagnostic_logging import make_manager
    _, diagnostic_logger = make_manager(tmp_path / "diagnostics")
    diagnostic_logger.emit("error", "fixture", "chat-corruption-fixture-needs-inspection")
    first_records: list[dict[str, object]] = []
    first_responses = [
        {"action": "skill", "skill_tool": "read_recent_logs", "skill_params": {"source": "memory", "limit": 5}, "continueLoop": True},
        {"action": "skill", "skill_tool": "unity_inspect_project_chat_store", "skill_params": {"projectPath": str(project)}, "continueLoop": True},
        {"action": "enter_execution", "summary": "Request supervised repair approval.", "continueLoop": True},
        {"action": "write", "write_tool": "unity_repair_project_chat_store", "write_params": {"projectPath": str(project), "expectedDigest": digest, "storeId": target.store_id, "executionTarget": execution_target}, "continueLoop": True},
    ]
    with _planner_loopback(first_responses, first_records) as base_url:
        configured = _configured_service(tmp_path / "provider-first.json", base_url)
        with _isolated_gateway(tmp_path) as gateway:
            with patch.object(dashboard_server, "PROVIDER_CONFIGURATION", configured), patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", diagnostic_logger):
                pending = gateway.runtime_message({"message": "Inspect and repair the corrupt project chat store after my approval.", "provider": "custom", "model": "loop-model", "projectRoot": str(project), "session_id": "repair-pending", "client_turn_id": "repair-pending-turn", "maxAgenticTurns": 5})
                assert pending["steps"][0]["tool"] == "vrcforge_read_recent_logs"
                assert any(log["message"] == "chat-corruption-fixture-needs-inspection" for log in pending["steps"][0]["result"]["logs"])
                step = pending["steps"][-1]
                assert step["status"] == "approval_pending", pending
                approval_id = step["result"]["approval"]["id"]
                assert store.read_bytes() == original
                gateway.approval_transactions.approve(approval_id)
                applied = gateway.approval_transactions.apply_approved({"approvalId": approval_id})
                assert applied["status"] == "applied", applied
                assert applied["result"]["verified"] is True, applied
                assert applied["result"].get("readback", {}).get("state") == "passed", applied
                backup = project / ".vrcforge" / applied["result"]["backupBasename"]
                assert backup.exists()
    second_records: list[dict[str, object]] = []
    second_responses = [
        {"action": "skill", "skill_tool": "unity_inspect_project_chat_store", "skill_params": {"projectPath": str(project)}, "continueLoop": True},
        {"action": "reply", "reply": "Repair is independently verified.", "continueLoop": False, "completion_claim": {"satisfied": True, "evidence_action_ids": [canonical_action_id("skill", "vrcforge_inspect_project_chat_store", {"projectPath": str(project)})]}},
    ]
    with _planner_loopback(second_responses, second_records) as base_url:
        configured = _configured_service(tmp_path / "provider-second.json", base_url)
        with _isolated_gateway(tmp_path) as gateway:
            with patch.object(dashboard_server, "PROVIDER_CONFIGURATION", configured):
                readback = gateway.runtime_message({"message": "Read the repaired project chat store and report its health.", "provider": "custom", "model": "loop-model", "projectRoot": str(project), "session_id": "repair-readback", "client_turn_id": "repair-readback-turn", "maxAgenticTurns": 3})
    assert readback["plan"]["nextStep"] == "done", readback
    assert readback["steps"][0]["result"]["status"] == "missing"
    assert len(first_records) == 4
    assert len(second_records) == 2


def test_internal_wrapper_guard_rejects_wrong_capability_and_other_wrappers(tmp_path):
    from dataclasses import replace
    with _isolated_gateway(tmp_path) as gateway:
        name = "vrcforge_repair_project_chat_store"
        original = gateway._write_handlers[name]
        with patch.dict(gateway._write_handlers, {name: replace(original, external_mcp_capability="wrong")}):
            result = gateway.approval_transactions._execute_write_request(name, {}, "fixture")
            assert result["status"] == "unavailable", result
            assert "dedicated" in result["error"]
        result = gateway.approval_transactions._execute_write_request("vrcforge_install_vpm_package", {}, "fixture")
        assert result["status"] == "unavailable", result
        assert "dedicated" in result["error"]


def test_configured_loop_recovers_interrupted_apply_after_human_confirmation(tmp_path):
    from tests.test_agent_repair_journeys import unity_fixture
    project = unity_fixture(tmp_path)
    with _isolated_gateway(tmp_path) as gateway:
        approval = {"id": "fixture-interruption", "targetTool": "vrcforge_test_write"}
        checkpoint = gateway.approval_transactions._create_pre_write_checkpoint(approval, {"projectRoot": str(project)})
        assert checkpoint["ok"], checkpoint
        recovery = gateway.approval_transactions._start_apply_recovery(approval, {"projectRoot": str(project)}, checkpoint)
        assert gateway.checkpoint_recovery.list_interrupted_apply_recoveries()["blockingWrites"]
        params = {"recoveryId": recovery["id"], "confirmResolved": True}
        from tests.test_diagnostic_logging import make_manager
        _, diagnostic_logger = make_manager(tmp_path / "diagnostics")
        diagnostic_logger.emit("error", "fixture", "interrupted-apply-fixture-needs-inspection")
        records = []
        responses = [
            {"action": "skill", "skill_tool": "read_recent_logs", "skill_params": {"source": "memory", "limit": 5}, "continueLoop": True},
            {"action": "skill", "skill_tool": "unity_list_interrupted_apply_recoveries", "skill_params": {}, "continueLoop": True},
            {"action": "skill", "skill_tool": "unity_preview_interrupted_apply_recovery", "skill_params": {"recoveryId": recovery["id"]}, "continueLoop": True},
            {"action": "enter_execution", "summary": "Request confirmation of the inspected recovery.", "continueLoop": True},
            {"action": "write", "write_tool": "unity_resolve_interrupted_apply_recovery", "write_params": params, "continueLoop": True},
            {"action": "skill", "skill_tool": "unity_list_interrupted_apply_recoveries", "skill_params": {}, "continueLoop": True},
            {"action": "reply", "reply": "Independent state confirms recovery is resolved.", "continueLoop": False, "completion_claim": {"satisfied": True, "evidence_action_ids": [canonical_action_id("skill", "vrcforge_list_interrupted_apply_recoveries", {})]}},
        ]
        with _planner_loopback(responses, records) as base_url:
            configured = _configured_service(tmp_path / "recovery-provider.json", base_url)
            with patch.object(dashboard_server, "PROVIDER_CONFIGURATION", configured), patch.object(dashboard_server, "DIAGNOSTIC_LOGGER", diagnostic_logger):
                pending = gateway.runtime_message({"message": "Inspect the interrupted write and request my confirmation before resolving it.", "provider": "custom", "model": "loop-model", "projectRoot": str(project), "session_id": "recover-pending", "client_turn_id": "recover-pending-turn", "maxAgenticTurns": 6})
                assert pending["steps"][0]["tool"] == "vrcforge_read_recent_logs"
                assert any(log["message"] == "interrupted-apply-fixture-needs-inspection" for log in pending["steps"][0]["result"]["logs"])
                step = pending["steps"][-1]
                assert step["status"] == "approval_pending", pending
                assert gateway.checkpoint_recovery.list_interrupted_apply_recoveries()["blockingWrites"]
                request_id = step["result"]["approval"]["id"]
                gateway.approval_transactions.approve(request_id)
                applied = gateway.approval_transactions.apply_approved({"approvalId": request_id})
                assert applied["ok"], applied
                verified = gateway.runtime_message({"message": "Independently read recovery status after the approved resolution.", "provider": "custom", "model": "loop-model", "projectRoot": str(project), "session_id": "recover-readback", "client_turn_id": "recover-readback-turn", "maxAgenticTurns": 3})
        assert verified["plan"]["nextStep"] == "done", verified
        assert verified["steps"][0]["result"]["blockingWrites"] is False
        assert Path(checkpoint["archivePath"]).exists()
        assert len(records) == 7

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_gateway import AgentGateway, AgentGatewayError
from approved_unity_execution import current_approved_unity_execution
from unity_mcp_core_client import UnityMcpCoreClient


def create_project(root: Path) -> Path:
    project = root / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Assets" / "baseline.txt").write_text("before", encoding="utf-8")
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n",
        encoding="utf-8",
    )
    return project


def approved_write(
    gateway: AgentGateway,
    project: Path,
    *,
    handler,
) -> dict[str, object]:
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {"ok": True}
    gateway.approval_transactions.register_write_handler(
        "vrcforge_test_lifecycle_write",
        "Lifecycle write",
        "high",
        handler,
    )
    request = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_test_lifecycle_write",
            "arguments": {"projectRoot": str(project)},
        }
    )
    approval_id = request["approval"]["id"]
    gateway.approval_transactions.approve(approval_id)
    return gateway.approval_transactions.apply_approved({"approval_id": approval_id})


def _core_result(*, pending: bool = False) -> dict[str, object]:
    structured: dict[str, object] = {"success": True}
    if pending:
        structured["_mcp_status"] = "pending"
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": "ok"}],
        "structuredContent": structured,
        "isError": False,
    }


def _approved_trace_write(gateway: AgentGateway, project: Path, handler) -> dict[str, object]:
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {"ok": True}
    gateway.approval_transactions.register_write_handler(
        "vrcforge_trace_write",
        (
            "When to use: Run the approved trace fixture.\n"
            "When NOT to use: Do not use for unrelated project operations.\n"
            "Negative example: Do not use for a read-only request."
        ),
        "high",
        handler,
    )
    request = gateway.approval_transactions.create_apply_request(
        {"target_tool": "vrcforge_trace_write", "arguments": {"projectRoot": str(project)}}
    )
    approval_id = str(request["approval"]["id"])
    gateway.approval_transactions.approve(approval_id)
    return gateway.approval_transactions.apply_approved({"approval_id": approval_id})


def test_approved_handler_trace_survives_stripped_core_meta(tmp_path: Path, monkeypatch) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    client = object.__new__(UnityMcpCoreClient)
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: _core_result())

    def handler(_arguments):
        result = client.call_tool("vrc_create_gameobject", {"name": "trace-secret"})
        return {"ok": result["structuredContent"]["success"]}

    execution = _approved_trace_write(gateway, project, handler)

    assert execution["ok"] is True
    trace = execution["requestTrace"]
    assert trace["approvalId"] == execution["approval"]["id"]
    assert trace["targetTool"] == "vrcforge_trace_write"
    assert trace["executionId"].startswith("exec_")
    assert [audit["toolName"] for audit in trace["unityCoreCallAudits"]] == ["vrc_create_gameobject"]
    assert "trace-secret" not in json.dumps(trace)
    applied = next(item for item in gateway.approval_transactions.recent_audit_logs(100) if item.get("event") == "approval_applied")
    assert applied["requestTrace"] == trace


def test_approved_handler_failure_keeps_core_error_trace(tmp_path: Path, monkeypatch) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    client = object.__new__(UnityMcpCoreClient)

    def fail(*_args, **_kwargs):
        raise TimeoutError("fixture timeout")

    monkeypatch.setattr(client, "_request", fail)
    execution = _approved_trace_write(
        gateway,
        project,
        lambda _arguments: client.call_tool("vrc_create_gameobject", {"name": "trace-secret"}),
    )

    assert execution["ok"] is False
    trace = execution["requestTrace"]
    assert len(trace["unityCoreCallAudits"]) == 1
    assert trace["unityCoreCallAudits"][0]["resultSummary"] == "error"
    assert trace["unityCoreCallAudits"][0]["errorClass"] == "TimeoutError"
    failed = next(item for item in gateway.approval_transactions.recent_audit_logs(100) if item.get("event") == "approval_failed")
    assert failed["requestTrace"] == trace


def test_approved_handler_preserves_ordered_unique_multi_core_trace_with_pending(
    tmp_path: Path, monkeypatch
) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    client = object.__new__(UnityMcpCoreClient)
    responses = iter([_core_result(), _core_result(pending=True)])
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: next(responses))

    def handler(_arguments):
        client.call_tool("vrc_import_unitypackage", {"path": "fixture"})
        pending = client.call_tool("vrc_refresh_asset_database", {})
        return {"ok": True, "pending": pending["structuredContent"]["_mcp_status"] == "pending"}

    execution = _approved_trace_write(gateway, project, handler)

    audits = execution["requestTrace"]["unityCoreCallAudits"]
    assert [audit["toolName"] for audit in audits] == [
        "vrc_import_unitypackage",
        "vrc_refresh_asset_database",
    ]
    assert len({audit["requestId"] for audit in audits}) == 2
    assert [audit["resultSummary"] for audit in audits] == ["complete", "pending"]


def test_argument_digest_requires_internal_opt_in(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.approval_transactions.register_write_handler(
        "vrcforge_test_argument_binding",
        "Argument binding test.",
        "high",
        lambda _arguments: {"ok": True},
    )
    arguments = {
        "projectRoot": str(tmp_path / "UnityProject"),
        "references": {"mergeTarget": "FixtureAvatar/Armature"},
    }

    ordinary = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_test_argument_binding",
            "arguments": arguments,
        }
    )
    bound = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_test_argument_binding",
            "arguments": arguments,
        },
        include_arguments_digest=True,
    )

    expected = hashlib.sha256(
        json.dumps(
            arguments,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert "argumentsDigest" not in ordinary["approval"]
    assert bound["approval"]["argumentsDigest"] == expected


def test_lifecycle_observer_runs_at_authoritative_write_boundaries(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    events: list[str] = []
    gateway.approval_transactions.apply_lifecycle_observer = (
        lambda stage, _payload: events.append(stage)
    )

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        events.append("write_handler")
        return {"ok": True, "sceneSaved": True}

    result = approved_write(gateway, project, handler=handler)

    assert result["ok"] is True
    assert events == [
        "approval_started",
        "checkpoint_created",
        "handler_starting",
        "write_handler",
        "handler_returned",
    ]


def test_core_write_handler_receives_one_use_context_only_after_checkpoint(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {"ok": True}
    observed: dict[str, object] = {}
    observed_context: dict[str, object] = {}
    observed_claim: dict[str, object] = {}

    def handler(arguments: dict[str, object]) -> dict[str, object]:
        observed.update(arguments)
        plan = current_approved_unity_execution()
        assert plan is not None
        observed_context.update(plan.diagnostic_context())
        tool_name = str(arguments["toolName"])
        tool_arguments = dict(arguments["arguments"])
        claim = plan.claim(tool_name, tool_arguments, project)
        observed_claim.update(claim.execution_context)
        claim.complete()
        return {"ok": True}

    gateway.approval_transactions.register_write_handler(
        "vrcforge_unity_mcp_write",
        "Core write context test.",
        "high",
        handler,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda arguments: [
            (str(arguments["toolName"]), dict(arguments["arguments"]))
        ],
    )
    request = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_unity_mcp_write",
            "arguments": {
                "projectRoot": str(project),
                "toolName": "vrc_create_gameobject",
                "arguments": {"name": "CreatedByVRCForge"},
            },
        }
    )
    approval_id = str(request["approval"]["id"])
    assert request["approval"]["approvedUnityExecutionPlan"]["calls"][0]["toolName"] == "vrc_create_gameobject"
    gateway.approval_transactions.approve(approval_id)
    result = gateway.approval_transactions.apply_approved({"approval_id": approval_id})

    assert result["ok"] is True
    context = observed_context
    assert "_vrcforge_approved_execution" not in observed
    assert context["lane"] == "approved_write"
    assert context["approvalId"] == approval_id
    assert context["checkpointId"] == result["checkpoint"]["id"]
    assert context["targetTool"] == "vrcforge_unity_mcp_write"
    assert context["issuedAtUnixMs"] < context["expiresAtUnixMs"]
    assert context["expiresAtUnixMs"] - context["issuedAtUnixMs"] == 60_000
    assert len(context["executionId"]) >= 24
    assert observed_claim["targetTool"] == "vrc_create_gameobject"
    assert observed_claim["gatewayTargetTool"] == "vrcforge_unity_mcp_write"
    assert observed_claim["executionId"] == context["executionId"]
    assert current_approved_unity_execution() is None


def test_core_write_handler_without_exact_plan_fails_before_approval(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.approval_transactions.register_write_handler(
        "vrcforge_unplanned_core_write",
        "Unplanned Core write.",
        "high",
        lambda _arguments: {"ok": True},
        requires_approved_execution_context=True,
    )

    with pytest.raises(AgentGatewayError, match="exact Core execution plan"):
        gateway.approval_transactions.create_apply_request(
            {
                "target_tool": "vrcforge_unplanned_core_write",
                "arguments": {"projectRoot": str(project)},
            }
        )


def test_core_write_plan_drift_after_approval_blocks_handler(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {"ok": True}
    handler_calls = 0

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        nonlocal handler_calls
        handler_calls += 1
        return {"ok": True}

    gateway.approval_transactions.register_write_handler(
        "vrcforge_planned_core_write",
        "Planned Core write.",
        "high",
        handler,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _arguments: [("vrc_create_gameobject", {"name": "before"})],
    )
    request = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_planned_core_write",
            "arguments": {"projectRoot": str(project)},
        }
    )
    approval_id = str(request["approval"]["id"])
    gateway._write_handlers["vrcforge_planned_core_write"].approved_execution_plan_builder = (  # noqa: SLF001
        lambda _arguments: [("vrc_create_gameobject", {"name": "after"})]
    )
    gateway.approval_transactions.approve(approval_id)

    result = gateway.approval_transactions.apply_approved({"approval_id": approval_id})

    assert result["ok"] is False
    assert "plan drifted" in result["error"]
    assert handler_calls == 0


def test_core_write_handler_must_consume_the_whole_frozen_plan(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {"ok": True}
    gateway.approval_transactions.register_write_handler(
        "vrcforge_incomplete_core_write",
        "Incomplete Core write.",
        "high",
        lambda _arguments: {"ok": True},
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _arguments: [("vrc_create_gameobject", {"name": "never-called"})],
    )
    request = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_incomplete_core_write",
            "arguments": {"projectRoot": str(project)},
        }
    )
    approval_id = str(request["approval"]["id"])
    gateway.approval_transactions.approve(approval_id)

    result = gateway.approval_transactions.apply_approved({"approval_id": approval_id})

    assert result["ok"] is False
    assert "not consumed exactly" in result["error"]


def test_failed_handler_burns_leaked_execution_plan(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {"ok": True}
    leaked = []

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        plan = current_approved_unity_execution()
        assert plan is not None
        leaked.append(plan)
        raise RuntimeError("handler failed")

    gateway.approval_transactions.register_write_handler(
        "vrcforge_failed_core_write",
        "Failed Core write.",
        "high",
        handler,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _arguments: [
            ("vrc_create_gameobject", {"name": "must-not-run"})
        ],
    )
    request = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_failed_core_write",
            "arguments": {"projectRoot": str(project)},
        }
    )
    approval_id = str(request["approval"]["id"])
    gateway.approval_transactions.approve(approval_id)
    result = gateway.approval_transactions.apply_approved({"approval_id": approval_id})

    assert result["ok"] is False
    assert len(leaked) == 1
    with pytest.raises(Exception, match="uncertain and closed"):
        leaked[0].claim(
            "vrc_create_gameobject",
            {"name": "must-not-run"},
            project,
        )


def test_checkpoint_observer_failure_aborts_before_write(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    handler_calls = 0

    def observer(stage: str, _payload: dict[str, object]) -> None:
        if stage == "checkpoint_created":
            raise RuntimeError("observer rejected checkpoint")

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        nonlocal handler_calls
        handler_calls += 1
        return {"ok": True, "sceneSaved": True}

    gateway.approval_transactions.apply_lifecycle_observer = observer
    result = approved_write(gateway, project, handler=handler)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["checkpoint"]["ok"] is True
    assert handler_calls == 0
    assert gateway.checkpoint_recovery.list_interrupted_apply_recoveries()["activeCount"] == 0


def test_post_write_observer_failure_enters_checkpoint_recovery(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    handler_calls = 0

    def observer(stage: str, _payload: dict[str, object]) -> None:
        if stage == "handler_returned":
            raise RuntimeError("observer rejected result")

    def handler(arguments: dict[str, object]) -> dict[str, object]:
        nonlocal handler_calls
        handler_calls += 1
        Path(str(arguments["projectRoot"]), "Assets", "generated.txt").write_text(
            "after",
            encoding="utf-8",
        )
        return {"ok": True, "sceneSaved": True}

    gateway.approval_transactions.apply_lifecycle_observer = observer
    result = approved_write(gateway, project, handler=handler)

    assert result["ok"] is False
    assert result["checkpoint"]["ok"] is True
    assert handler_calls == 1
    recoveries = gateway.checkpoint_recovery.list_interrupted_apply_recoveries()
    assert recoveries["blockingWrites"] is True
    assert recoveries["activeCount"] == 1
    assert recoveries["recoveries"][0]["checkpointId"] == result["checkpoint"]["id"]


def test_handler_starting_observer_failure_aborts_before_write(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    handler_calls = 0

    def observer(stage: str, _payload: dict[str, object]) -> None:
        if stage == "handler_starting":
            raise RuntimeError("observer rejected write boundary")

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        nonlocal handler_calls
        handler_calls += 1
        return {"ok": True, "sceneSaved": True}

    gateway.approval_transactions.apply_lifecycle_observer = observer
    result = approved_write(gateway, project, handler=handler)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["checkpoint"]["ok"] is True
    assert handler_calls == 0
    assert gateway.checkpoint_recovery.list_interrupted_apply_recoveries()["activeCount"] == 0


def test_final_handler_arguments_are_bound_after_constraint_refresh(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {"ok": True}
    handler_calls = 0
    observed_digest = ""

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        nonlocal handler_calls
        handler_calls += 1
        return {"ok": True, "sceneSaved": True}

    gateway.approval_transactions.register_write_handler(
        "vrcforge_test_constraint_binding",
        "Constraint binding test.",
        "high",
        handler,
    )
    request = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_test_constraint_binding",
            "arguments": {"projectRoot": str(project)},
        },
        include_arguments_digest=True,
    )
    expected_digest = str(request["approval"]["argumentsDigest"])
    gateway.user_constraints_path.write_text(
        "Keep generated assets inside the project.\n",
        encoding="utf-8",
    )

    def observer(stage: str, payload: dict[str, object]) -> None:
        nonlocal observed_digest
        if stage != "handler_starting":
            return
        observed_digest = str(payload.get("argumentsDigest") or "")
        if observed_digest != expected_digest:
            raise RuntimeError("final handler arguments changed")

    gateway.approval_transactions.apply_lifecycle_observer = observer
    approval_id = str(request["approval"]["id"])
    gateway.approval_transactions.approve(approval_id)
    result = gateway.approval_transactions.apply_approved({"approval_id": approval_id})

    assert result["ok"] is False
    assert observed_digest and observed_digest != expected_digest
    assert handler_calls == 0


def test_mixed_approval_and_rejection_are_isolated_and_auditable(tmp_path: Path) -> None:
    """One pending write may apply while a sibling rejection remains side-effect free."""
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {"ok": True}
    calls: list[str] = []

    def approved_handler(_arguments: dict[str, object]) -> dict[str, object]:
        calls.append("approved")
        return {"ok": True, "sceneSaved": True}

    def rejected_handler(_arguments: dict[str, object]) -> dict[str, object]:
        calls.append("rejected")
        return {"ok": True, "sceneSaved": True}

    gateway.approval_transactions.register_write_handler(
        "vrcforge_test_mixed_approved", "Mixed approved", "high", approved_handler
    )
    gateway.approval_transactions.register_write_handler(
        "vrcforge_test_mixed_rejected", "Mixed rejected", "high", rejected_handler
    )
    approved_request = gateway.approval_transactions.create_apply_request(
        {"target_tool": "vrcforge_test_mixed_approved", "arguments": {"projectRoot": str(project)}}
    )["approval"]
    rejected_request = gateway.approval_transactions.create_apply_request(
        {"target_tool": "vrcforge_test_mixed_rejected", "arguments": {"projectRoot": str(project)}}
    )["approval"]
    approved_id = str(approved_request["id"])
    rejected_id = str(rejected_request["id"])

    assert approved_id != rejected_id
    assert approved_request["status"] == rejected_request["status"] == "pending"
    assert approved_request["createdAt"] and rejected_request["createdAt"]

    assert gateway.approval_transactions.approve(approved_id)["ok"] is True
    applied = gateway.approval_transactions.apply_approved({"approval_id": approved_id})
    assert applied["ok"] is True
    assert applied["status"] == "applied"
    assert applied["checkpoint"]["ok"] is True
    assert applied["checkpoint"]["id"]
    assert calls == ["approved"]

    rejected = gateway.approval_transactions.reject(rejected_id)
    assert rejected["ok"] is True
    assert rejected["approval"]["status"] == "rejected"
    rejected_apply = gateway.approval_transactions.apply_approved({"approval_id": rejected_id})
    assert rejected_apply["ok"] is False
    assert rejected_apply["status"] == "rejected"
    assert calls == ["approved"]

    approvals = {item["id"]: item for item in gateway.approval_transactions.list_approvals(include_expired=True)}
    assert approvals[approved_id]["status"] == "applied"
    assert approvals[approved_id]["appliedAt"]
    assert approvals[approved_id]["checkpoint"]["id"] == applied["checkpoint"]["id"]
    assert approvals[rejected_id]["status"] == "rejected"
    assert approvals[rejected_id]["rejectedAt"]
    assert "checkpoint" not in approvals[rejected_id]

    audit = gateway.approval_transactions.recent_audit_logs(limit=100)
    assert any(
        item.get("event") == "approval_applied"
        and item.get("approval", {}).get("id") == approved_id
        and item.get("approval", {}).get("checkpoint", {}).get("id") == applied["checkpoint"]["id"]
        for item in audit
    )
    assert any(
        item.get("event") == "approval_rejected"
        and item.get("approval", {}).get("id") == rejected_id
        and item.get("approval", {}).get("rejectedAt")
        for item in audit
    )

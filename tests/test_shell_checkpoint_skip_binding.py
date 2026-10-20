from __future__ import annotations

import hashlib
import json

import pytest

from agent_gateway import AgentGateway
from agent_approval_transactions import AgentApprovalTransactionService
from agent_shell_service import ShellApprovalRequest
from agent_task_loop import TASK_LOOP_SCHEMA
from checkpoint_user_policy import bind_user_checkpoint_skip, checkpoint_skip_matches


def _shell_request(gateway, project, *, grant="valid", change_requested_arguments=False):
    command = "Set-Content -Path Assets/probe.txt -Value synthetic"
    requested_arguments = {"command": command, "cwd": str(project), "timeout": 15}
    quote = "Skip the checkpoint for this write"
    message = f"{quote}. Continue the approved synthetic operation."
    source = {
        "source": "app_user_message",
        "message": message,
        "sessionId": "session-shell-skip",
        "turnId": "turn-shell-skip",
        "clientTurnId": "client-shell-skip",
        "projectRoot": str(project),
    }
    policy = bind_user_checkpoint_skip(
        source,
        {"mode": "skip", "quote": quote},
        session_id=source["sessionId"],
        turn_id=source["turnId"],
        client_turn_id=source["clientTurnId"],
        project_root=source["projectRoot"],
        tool="unity_shell",
        arguments=requested_arguments,
    )
    assert policy is not None

    if change_requested_arguments:
        requested_arguments = {**requested_arguments, "timeout": 20}
    if grant == "missing":
        policy = None
    elif grant == "forged_digest":
        policy = {**policy, "argumentsSha256": "0" * 64}

    seed = {
        "schema": TASK_LOOP_SCHEMA,
        "taskId": "task-shell-skip",
        "objective": "Exercise the registered Shell approval route with synthetic data.",
        "sessionId": source["sessionId"],
        "turnId": source["turnId"],
        "clientTurnId": source["clientTurnId"],
        "projectRoot": source["projectRoot"],
        "requestedKind": "shell",
        "requestedTool": "unity_shell",
        "requestedArguments": requested_arguments,
    }
    if policy is not None:
        seed["checkpointUserPolicy"] = policy

    approval_arguments = {
        "command": command,
        "cwd": str(project),
        "workspace_root": str(project),
        "projectRoot": str(project),
        "session_id": source["sessionId"],
        "turn_id": source["turnId"],
        "timeout_seconds": 15,
        "classification_snapshot": {
            "command": command,
            "cwd": str(project),
            "workspaceRoot": str(project),
            "projectRoot": str(project),
            "risk": "high",
            "readOnly": False,
        },
    }
    request = ShellApprovalRequest(
        agent_name="test-shell",
        target_tool="vrcforge_shell_execute",
        arguments=approval_arguments,
        reason="Synthetic Shell checkpoint binding regression.",
        preview={"projectRoot": str(project)},
        risk_level="high",
        user_constraints=None,
        requires_explicit_approval=False,
        explicit_approval_reason="",
        goal_delivery_id="",
        task_context=seed,
    )
    # This is the actual ShellApprovalPorts.create callback registered by
    # AgentGateway; it reaches create_shell_approval without running a command.
    approval = gateway.shell._ports.approvals.create(request)
    return approval, requested_arguments


def _gateway(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.allow_write_requests = True
    gateway.save_config(config)
    project = tmp_path / "SyntheticUnityProject"
    (project / "Assets").mkdir(parents=True)
    return gateway, project


def test_registered_shell_approval_consumes_matching_checkpoint_skip(tmp_path, monkeypatch):
    gateway, project = _gateway(tmp_path)
    approval, requested_arguments = _shell_request(gateway, project)
    checkpoint_calls = []
    shell_calls = []

    def checkpoint(_service, _approval, _arguments):
        checkpoint_calls.append(True)
        return {"ok": False, "blocking": True, "status": "failed", "error": "checkpoint sentinel"}

    monkeypatch.setattr(AgentApprovalTransactionService, "_create_pre_write_checkpoint", checkpoint)
    monkeypatch.setattr(
        gateway.shell,
        "execute_payload",
        lambda arguments: shell_calls.append(arguments) or {"ok": True, "status": "written", "committed": True},
    )
    # The Shell callback returns the normal redacted approval projection;
    # checkpoint matching consumes the trusted stored approval.
    stored_approval = gateway.approval_transactions._ports.state.approvals[approval["id"]]
    policy = stored_approval["taskContext"]["checkpointUserPolicy"]
    skip_matches = checkpoint_skip_matches(
        policy,
        stored_approval["taskContext"],
        tool="vrcforge_shell_execute",
        arguments=stored_approval["arguments"],
        plan_digest="",
    )
    requested_digest_matches = policy["argumentsSha256"] == hashlib.sha256(
        json.dumps(
            requested_arguments, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()

    gateway.approval_transactions.approve(approval["id"])
    result = gateway.approval_transactions.apply_approved({"approval_id": approval["id"]})

    assert requested_digest_matches, "the user grant must bind to the actual requested arguments"
    assert skip_matches, (
        f"Shell approval did not bind the owner skip grant; checkpointCalls={len(checkpoint_calls)}, "
        f"policyKeys={sorted(policy)}, requestedDigestMatches={requested_digest_matches}, "
        f"applyStatus={result.get('status')}, applyError={result.get('error')}, "
        f"checkpointSkipApplied={result.get('approval', {}).get('taskContext', {}).get('checkpointSkipApplied')}"
    )
    assert checkpoint_calls == []
    assert result["status"] == "applied", result.get("error")
    assert result["approval"]["taskContext"]["checkpointSkipApplied"] is True
    bound_policy = result["approval"]["taskContext"]["checkpointUserPolicy"]
    assert bound_policy["targetTool"] == "vrcforge_shell_execute"
    audit_rows = [
        json.loads(line)
        for line in gateway.audit_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(row.get("event") == "approval_checkpoint_skipped_by_user" for row in audit_rows) == 1
    assert len(shell_calls) == 1


@pytest.mark.parametrize(
    ("grant", "change_requested_arguments"),
    [("missing", False), ("forged_digest", False), ("valid", True)],
    ids=["no-grant", "forged-digest", "request-arguments-changed"],
)
def test_registered_shell_approval_keeps_checkpoint_for_unbound_grants(
    tmp_path, monkeypatch, grant, change_requested_arguments,
):
    gateway, project = _gateway(tmp_path)
    approval, _ = _shell_request(
        gateway,
        project,
        grant=grant,
        change_requested_arguments=change_requested_arguments,
    )
    checkpoint_calls = []
    shell_calls = []

    def checkpoint(_service, _approval, _arguments):
        checkpoint_calls.append(True)
        return {"ok": False, "blocking": True, "status": "failed", "error": "checkpoint sentinel"}

    monkeypatch.setattr(AgentApprovalTransactionService, "_create_pre_write_checkpoint", checkpoint)
    monkeypatch.setattr(
        gateway.shell,
        "execute_payload",
        lambda arguments: shell_calls.append(arguments) or {"ok": True, "status": "written", "committed": True},
    )

    gateway.approval_transactions.approve(approval["id"])
    result = gateway.approval_transactions.apply_approved({"approval_id": approval["id"]})

    assert result["status"] == "failed"
    assert checkpoint_calls == [True], {
        "resultStatus": result.get("status"),
        "error": result.get("error"),
        "approvalStatus": result.get("approval", {}).get("status"),
    }
    assert shell_calls == []
    assert result["approval"]["taskContext"].get("checkpointSkipApplied") is not True
    audit_rows = [
        json.loads(line)
        for line in gateway.audit_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert not any(row.get("event") == "approval_checkpoint_skipped_by_user" for row in audit_rows)

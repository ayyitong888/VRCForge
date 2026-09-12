from __future__ import annotations

from pathlib import Path

from agent_gateway import AgentGateway
from execution_target import canonical_namespace, execution_target_digest, project_identity, validate_execution_target


def _unity_project(root: Path) -> Path:
    project = root / "UnityProject"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True)
    (project / "Assets" / "existing.txt").write_text("before", encoding="utf-8")
    return project


def test_approved_apply_binds_checkpoint_and_recovery_to_arguments_execution_target(tmp_path: Path) -> None:
    project = _unity_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {"ok": True}

    execution_target = {
        "schema": "vrcforge.execution_target.v1",
        "scope": "scene",
        "project": {"root": str(project), "projectId": project_identity(str(project))},
        "editor": {"unityPid": 123, "processStartTime": "start", "coreInstanceId": "core"},
        "scene": {
            "assetPath": "Assets/Main.unity",
            "absolutePath": str(project / "Assets" / "Main.unity"),
            "guid": "s" * 32,
            "revision": "1",
            "digest": "d" * 64,
        },
        "namespace": "",
        "ambiguous": False,
        "resolutionCandidateCount": 1,
    }
    execution_target["namespace"] = canonical_namespace(execution_target)
    execution_target = validate_execution_target(
        execution_target,
        project_root=str(project),
        required_scope="scene",
    )

    def failing_write(_arguments: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("simulated final write boundary failure")

    gateway.approval_transactions.register_write_handler(
        "vrcforge_test_target_binding_failure",
        "Target binding failure test",
        "high",
        failing_write,
    )
    created = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_test_target_binding_failure",
            "arguments": {"projectRoot": str(project), "executionTarget": execution_target},
            "requires_explicit_approval": True,
        }
    )
    approval = created["approval"]
    assert "executionTarget" not in approval
    gateway.approval_transactions._ports.state.approvals[approval["id"]]["executionTarget"] = {
        **execution_target,
        "namespace": "vrcforge://projects/forged",
    }
    gateway.approval_transactions.approve(approval["id"])

    result = gateway.approval_transactions.apply_approved({"approval_id": approval["id"]})

    assert result["ok"] is False
    assert result["status"] == "failed"
    checkpoint = result["checkpoint"]
    recoveries = gateway.checkpoint_recovery.list_interrupted_apply_recoveries(
        {"includeResolved": True}
    )["recoveries"]
    assert len(recoveries) == 1
    recovery = recoveries[0]
    assert checkpoint["executionTarget"] == execution_target
    assert recovery["executionTarget"] == execution_target
    assert recovery["checkpoint"]["executionTarget"] == execution_target
    expected_digest = execution_target_digest(execution_target)
    assert execution_target_digest(checkpoint["executionTarget"]) == expected_digest
    assert execution_target_digest(recovery["executionTarget"]) == expected_digest
    assert execution_target_digest(recovery["checkpoint"]["executionTarget"]) == expected_digest

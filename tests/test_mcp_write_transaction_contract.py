from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agent_gateway import AgentGateway
from operation_context import current_operation_context


ROOT = Path(__file__).parents[1]


def _gateway(tmp_path: Path) -> AgentGateway:
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    gateway.save_config(config)
    return gateway


def _unity_project(tmp_path: Path) -> Path:
    project = tmp_path / "UnityProject"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True)
    return project


def test_supervised_write_source_has_one_operation_id_across_transaction_boundaries() -> None:
    transaction = (ROOT / "agent_approval_transactions.py").read_text(encoding="utf-8")
    gateway = (ROOT / "agent_gateway.py").read_text(encoding="utf-8")
    operation = (ROOT / "operation_context.py").read_text(encoding="utf-8")

    assert '"operationId": operation_id' in transaction
    assert '"operationId": operation_id' in gateway
    assert 'bind_operation_context(operation_id' in transaction
    assert 'value: dict[str, Any] = {"operationId": str(operation_id)}' in operation
    assert '"readbackState"' in transaction
    assert '"operationResource"' in operation


def test_required_checkpoint_failure_never_calls_core_handler(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = _unity_project(tmp_path)
    calls = 0

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {
        "ok": False,
        "blocking": True,
        "error": "checkpoint unavailable",
    }
    gateway.approval_transactions.register_write_handler(
        "vrcforge_contract_checkpoint_write", "Contract checkpoint write", "high", handler
    )
    request = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_contract_checkpoint_write",
            "arguments": {"projectRoot": str(project)},
        }
    )
    gateway.approval_transactions.approve(request["approval"]["id"])

    result = gateway.approval_transactions.apply_approved(
        {"approval_id": request["approval"]["id"]}
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert calls == 0
    assert result["writeFailure"]["commitState"] == "not_started"


def test_applied_mutation_with_failed_fresh_readback_is_not_success(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = _unity_project(tmp_path)
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _path: {"ok": True}

    def finalize(_arguments, _baseline, result):
        return {
            **result,
            "ok": False,
            "status": "failed",
            "mutationApplied": True,
            "readbackState": "failed",
            "error": "fresh readback failed",
        }

    gateway.approval_transactions.register_write_handler(
        "vrcforge_contract_readback_write",
        "Contract readback write",
        "high",
        lambda _args: {
            "ok": True,
            "mutationStarted": True,
            "mutationApplied": True,
            "committed": True,
            "commitState": "complete",
        },
        verification_profile="fresh_readback",
        verification_prepare_handler=lambda _args: {"before": "snapshot"},
        verification_finalize_handler=finalize,
    )
    request = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_contract_readback_write",
            "arguments": {"projectRoot": str(project)},
        }
    )
    gateway.approval_transactions.approve(request["approval"]["id"])

    result = gateway.approval_transactions.apply_approved(
        {"approval_id": request["approval"]["id"]}
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["result"]["mutationApplied"] is True
    assert result["result"]["readbackState"] == "failed"


def test_external_proposal_confirmation_core_readback_and_receipt_share_operation_id(
    tmp_path: Path,
) -> None:
    gateway = _gateway(tmp_path)
    observed: dict[str, object] = {}

    def handler(arguments: dict[str, object]) -> dict[str, object]:
        observed.update(current_operation_context() or {})
        return {
            "schema": "vrcforge.contract.receipt.v1",
            "ok": True,
            "mutationStarted": True,
            "mutationApplied": True,
            "commitState": "committed",
            "verified": True,
            "readback": {"arguments": arguments},
        }

    gateway.approval_transactions.register_write_handler(
        "vrcforge_contract_external_write", "Contract external write", "high", handler,
        pre_write_checkpoint_required=False,
    )
    gateway.register_external_mcp_unity_tool("vrcforge_contract_external_write", "avatar")

    proposal = gateway.call_external_mcp_tool(
        "vrcforge_contract_external_write", {"value": "same"}
    )
    assert len(proposal["confirmation"]["toolDefinitionDigest"]) == 64
    tampered = {
        **proposal["confirmation"],
        "toolDefinitionDigest": "0" * 64,
        "decision": "approve",
    }
    rejected = gateway.call_external_mcp_tool(
        "vrcforge_contract_external_write",
        {"value": "same", "confirmation": tampered},
    )
    assert rejected["status"] == "invalid_confirmation"
    assert observed == {}
    confirmation = {**proposal["confirmation"], "decision": "approve"}
    result = gateway.call_external_mcp_tool(
        "vrcforge_contract_external_write",
        {"value": "same", "confirmation": confirmation},
    )

    operation_id = proposal["confirmation"]["operationId"]
    assert result["operationId"] == operation_id
    assert observed["operationId"] == operation_id
    assert result["result"]["schema"] == "vrcforge.contract.receipt.v1"
    assert result["readbackState"] == "verified"
    assert result["mutationApplied"] is True
    assert result["outcome"]["operationId"] == operation_id


def test_nested_core_persisted_receipt_projects_same_success_facts_to_external_agent(
    tmp_path: Path,
) -> None:
    gateway = _gateway(tmp_path)

    gateway.approval_transactions.register_write_handler(
        "vrcforge_contract_nested_core_write",
        "Nested Core receipt contract",
        "high",
        lambda _args: {
            "ok": True,
            "result": {
                "payload": {
                    "data": {
                        "schema": "vrcforge.renderer_material_slot.v1",
                        "mutationStarted": True,
                        "applied": True,
                        "committed": True,
                        "sceneSaved": True,
                        "persistedReadback": True,
                        "scenePath": "Assets/2.unity",
                        "sceneGuid": "a" * 32,
                        "sceneFileDigest": "b" * 64,
                        "rendererComponentId": "c" * 64,
                        "slotIndex": 0,
                        "newMaterialAssetPath": "Assets/Expressions.mat",
                    }
                }
            },
        },
        pre_write_checkpoint_required=False,
    )
    gateway.register_external_mcp_unity_tool(
        "vrcforge_contract_nested_core_write", "materials"
    )

    proposal = gateway.call_external_mcp_tool(
        "vrcforge_contract_nested_core_write", {"value": "same"}
    )
    confirmation = {**proposal["confirmation"], "decision": "approve"}
    result = gateway.call_external_mcp_tool(
        "vrcforge_contract_nested_core_write",
        {"value": "same", "confirmation": confirmation},
    )

    assert result["ok"] is True
    assert result["mutationStarted"] is True
    assert result["mutationApplied"] is True
    assert result["commitState"] == "committed"
    assert result["persistenceState"] == "persisted"
    assert result["readbackState"] == "verified"
    assert result["outcome"]["commitState"] == "committed"


def test_confirmation_binds_execution_target_digest() -> None:
    source = (ROOT / "agent_gateway.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    proposal = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_propose_external_mcp_write"
    )
    body = ast.get_source_segment(source, proposal) or ""
    confirmation_block = body.split("confirmation =", 1)[1].split("self.append_audit", 1)[0]
    assert '"executionTargetDigest"' in confirmation_block


def test_multi_file_writes_are_one_modular_patch_set_with_one_receipt_registry(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    valid = {
        "operationId": "mcpop_contract",
        "files": [
            {"root": str(tmp_path), "relativePath": "Assets/source.cs", "owner": "files", "responsibility": "source update", "mustNotExist": True},
            {"root": str(tmp_path), "relativePath": "Packages/manifest.json", "owner": "files", "responsibility": "package metadata", "expectedBeforeDigest": "sha256:before"},
        ],
    }
    normalized = gateway.approval_transactions.validate_patch_set(valid)
    assert normalized["operationId"] == "mcpop_contract"
    assert len(normalized["duplicateRegistry"]) == 2
    stored = gateway.approval_transactions.register_patch_set_receipt(valid, {"verified": True})
    assert gateway.approval_transactions.get_patch_set_receipt("mcpop_contract") == stored

    with pytest.raises(Exception, match="duplicate canonical paths"):
        gateway.approval_transactions.validate_patch_set({**valid, "files": [valid["files"][0], {**valid["files"][0], "relativePath": "./Assets/source.cs"}]})
    with pytest.raises(Exception, match="serialized assets"):
        gateway.approval_transactions.validate_patch_set({**valid, "files": [{**valid["files"][0], "relativePath": "Assets/avatar.prefab"}]})

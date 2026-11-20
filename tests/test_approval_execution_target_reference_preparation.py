from __future__ import annotations

from copy import deepcopy

import pytest

import execution_target
from agent_gateway import AgentGateway, AgentGatewayError
from execution_target import EXECUTION_TARGET_REF_SCHEMA, EXECUTION_TARGET_SCHEMA
from outfit_import_planner import IMPORT_PLAN_SCHEMA
from outfit_import_receipt import OUTFIT_IMPORT_PLAN_TOOL, OUTFIT_IMPORT_TOOL


def _target(project_root, scope="avatar"):
    return {
        "schema": EXECUTION_TARGET_SCHEMA,
        "scope": scope,
        "project": {"root": str(project_root)},
        "editor": {"unityPid": 1234, "processStartTime": "1", "coreInstanceId": "fixture-core"},
        "scene": {
            "absolutePath": str(project_root / "Assets" / "Main.unity"),
            "guid": "1" * 32,
            "digest": "2" * 64,
        },
        "avatar": {"globalObjectId": "fixture-avatar", "exactHierarchyPath": "Avatar"},
    }


def _reference():
    return {"schema": EXECUTION_TARGET_REF_SCHEMA, "ref": "vrcforge-target-synthetic-avatar"}


def _enable_approval_writes(gateway):
    config = gateway.ensure_config()
    config.enabled = True
    config.execution_mode = "approval"
    gateway.save_config(config)


@pytest.fixture
def import_gateway(tmp_path, monkeypatch):
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    _enable_approval_writes(gateway)
    gateway._mcp_resource_context.set("runtime:outfit-owner")
    stale = set()

    def verify(target, **_scope):
        if target.get("avatar", {}).get("globalObjectId") in stale:
            raise execution_target.ExecutionTargetError("editor_identity_mismatch", "Target is no longer live.")
        return deepcopy(target)

    monkeypatch.setattr(execution_target, "validate_runtime_execution_target", verify)
    prepared = []

    def prepare(arguments, _preview):
        prepared.append(deepcopy(arguments))
        return arguments, {"ok": True}

    gateway.approval_transactions.register_write_handler(
        OUTFIT_IMPORT_TOOL, "Prepare outfit imports.", "medium",
        lambda _arguments: pytest.fail("Creating approval must not run the import"),
        request_preparer=prepare, requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda arguments: [("vrc_import_unitypackage", {
            "projectPath": arguments["projectPath"], "unityPackagePath": arguments["packagePath"],
        })],
    )
    gateway.register_tool(OUTFIT_IMPORT_PLAN_TOOL, "Preview outfit imports.", "plan/preview", lambda arguments: {
        "ok": True, "preview": True, "schema": IMPORT_PLAN_SCHEMA,
        "plan": {"readyToApply": True, "writeTarget": OUTFIT_IMPORT_TOOL},
    })
    arguments = {
        "projectPath": str(project), "packagePath": str(tmp_path / "Outfit.unitypackage"),
        "selectedPrefab": "Assets/Urban/Color 18.prefab", "dependencyMode": "selected_only",
    }
    return gateway, project, arguments, prepared, stale


def _bind_import_target(gateway, project, identity):
    target = _target(project)
    target["avatar"]["globalObjectId"] = identity
    return gateway.bind_execution_target(target, project_root=str(project))["executionTarget"], target


def _propose_import(gateway, arguments):
    return gateway.approval_transactions._execute_write_request(OUTFIT_IMPORT_TOOL, arguments, "desktop-agent")


def test_runtime_import_seals_unique_owned_binding_in_pending_approval(import_gateway):
    gateway, project, arguments, prepared, _ = import_gateway
    _, target = _bind_import_target(gateway, project, "chosen")
    token = gateway._mcp_resource_context.set("runtime:different-owner")
    _bind_import_target(gateway, project, "different-owner")
    gateway._mcp_resource_context.reset(token)

    result = _propose_import(gateway, arguments)

    assert result["status"] == "approval_pending"
    approval = gateway._approvals[result["approvalId"]]
    assert prepared[0]["executionTarget"] == target == approval["arguments"]["executionTarget"]
    assert approval["status"] == "pending"
    assert "executionTarget" not in arguments


def test_runtime_import_requires_choice_then_uses_exact_bound_preflight(import_gateway):
    gateway, project, arguments, prepared, stale = import_gateway
    left, _ = _bind_import_target(gateway, project, "left")
    right, expected_target = _bind_import_target(gateway, project, "right")
    result = _propose_import(gateway, arguments)
    error = result["result"]["errorDetails"]
    assert error["errorCode"] == "execution_target_ambiguous"
    assert error["commitState"] == "not_started"
    assert error["details"]["requiredArgument"] == "executionTarget"
    assert {row["executionTarget"]["ref"] for row in error["details"]["options"]} == {left["ref"], right["ref"]}
    assert "Select the intended bound reference" in error["nextAction"]
    assert not prepared and not gateway._approvals

    preflight = gateway.runtime_skills.execute(OUTFIT_IMPORT_PLAN_TOOL, {**arguments, "executionTarget": right}, "desktop-agent")
    assert preflight["ok"]
    equivalent = _propose_import(gateway, arguments)
    assert equivalent["status"] == "approval_pending"
    assert [row["executionTarget"] for row in prepared] == [expected_target]
    different = _propose_import(gateway, {**arguments, "selectedPrefab": "Assets/Urban/Color 1.prefab"})
    assert different["result"]["errorDetails"]["errorCode"] == "execution_target_ambiguous"

    stale.add("right")
    expired = _propose_import(gateway, arguments)
    assert expired["result"]["errorDetails"]["details"]["options"] == []
    assert expired["result"]["errorDetails"]["errorCode"] == "execution_target_missing"
    assert len(prepared) == 1


def test_runtime_import_with_no_owned_binding_reports_next_tool(import_gateway):
    gateway, _, arguments, prepared, _ = import_gateway
    result = _propose_import(gateway, arguments)
    error = result["result"]["errorDetails"]
    assert error["errorCode"] == "execution_target_missing"
    assert error["commitState"] == "not_started"
    assert error["details"]["requiredArgument"] == "executionTarget"
    assert "vrcforge_bind_execution_target" in error["nextAction"]
    assert not prepared and not gateway._approvals


def test_external_import_with_bound_preflight_still_waits_for_confirmation(import_gateway):
    gateway, project, arguments, prepared, _ = import_gateway
    gateway._mcp_resource_context.set("mcp:outfit-owner")
    _bind_import_target(gateway, project, "other")
    chosen, expected_target = _bind_import_target(gateway, project, "confirmed")
    preflight = gateway.call_external_mcp_tool(OUTFIT_IMPORT_PLAN_TOOL, {**arguments, "executionTarget": chosen})
    assert preflight["ok"]
    proposal = gateway.call_external_mcp_tool(OUTFIT_IMPORT_TOOL, arguments)
    assert proposal["status"] == "user_confirmation_required"
    assert [row["executionTarget"] for row in prepared] == [expected_target]


@pytest.mark.parametrize(
    ("target_tool", "write_arguments", "required_scope"),
    [
        (
            "vrcforge_apply_shader_tuning",
            {"avatarPath": "Avatar", "changes": [{"material_id": "mat_fixture", "semantic_property": "base_color", "after": "#9DDCFF"}]},
            "avatar",
        ),
        (
            "vrcforge_set_renderer_material_slot",
            {"assignments": [{"rendererPath": "Avatar/Body", "rendererComponentId": "a" * 64, "slotIndex": 0, "newMaterialAssetPath": "Assets/New.mat"}]},
            "scene",
        ),
    ],
)
def test_public_reference_is_expanded_before_common_write_preparer(
    monkeypatch, tmp_path, target_tool, write_arguments, required_scope
):
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    _enable_approval_writes(gateway)
    bound_target = _target(project, required_scope)
    gateway._mcp_resource_context.set("session:fixture-owner")
    resolution = []

    def resolve(reference, *, project_root, session_context, required_scope):
        resolution.append((reference, project_root, session_context, required_scope))
        assert reference == _reference()
        if required_scope != bound_target["scope"]:
            raise AgentGatewayError("The target reference does not cover this write scope.", status_code=409)
        return deepcopy(bound_target)

    monkeypatch.setattr(gateway._execution_target_bindings, "resolve", resolve)
    prepared = []

    def request_preparer(arguments, _preview):
        prepared.append(deepcopy(arguments))
        assert arguments["executionTarget"] == bound_target
        return arguments, {"ok": True, "scope": "avatar"}

    gateway.approval_transactions.register_write_handler(
        target_tool,
        "Fixture shader write.",
        "medium",
        lambda _arguments: pytest.fail("request creation must not execute the write"),
        request_preparer=request_preparer,
    )

    result = gateway.approval_transactions.create_apply_request({
            "target_tool": target_tool,
            "arguments": {
                "projectPath": str(project),
                **write_arguments,
                "executionTarget": _reference(),
            },
    })

    assert result["status"] == "pending"
    assert resolution == [(_reference(), str(project), "session:fixture-owner", required_scope)]
    assert prepared[0]["executionTarget"] == bound_target
    stored = gateway._approvals[result["approval"]["id"]]
    assert stored["arguments"]["executionTarget"] == bound_target


def test_reference_scope_rejection_blocks_preparer_and_expanded_target_stays_domain_owned(
    monkeypatch, tmp_path
):
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    _enable_approval_writes(gateway)
    gateway._mcp_resource_context.set("session:fixture-owner")
    seen = []

    def reject_wrong_scope(reference, *, project_root, session_context, required_scope):
        seen.append((project_root, session_context, required_scope))
        raise AgentGatewayError("ExecutionTarget reference scope mismatch.", status_code=409)

    monkeypatch.setattr(gateway._execution_target_bindings, "resolve", reject_wrong_scope)
    preparer_calls = []
    gateway.approval_transactions.register_write_handler(
        "vrcforge_apply_shader_tuning",
        "Fixture shader write.",
        "medium",
        lambda _arguments: pytest.fail("request creation must not execute the write"),
        request_preparer=lambda arguments, _preview: preparer_calls.append(arguments),
    )

    with pytest.raises(AgentGatewayError, match="scope mismatch"):
        gateway.approval_transactions.create_apply_request({
            "target_tool": "vrcforge_apply_shader_tuning",
            "arguments": {"projectPath": str(project), "executionTarget": _reference()},
        })
    assert seen == [(str(project), "session:fixture-owner", "avatar")]
    assert preparer_calls == []

    expanded = _target(project)
    monkeypatch.setattr(
        gateway._execution_target_bindings,
        "resolve",
        lambda *_args, **_kwargs: pytest.fail("expanded legacy targets must stay domain-owned"),
    )
    passed = []
    gateway.approval_transactions.register_write_handler(
        "vrcforge_apply_shader_tuning",
        "Fixture shader write.",
        "medium",
        lambda _arguments: pytest.fail("request creation must not execute the write"),
        request_preparer=lambda arguments, _preview: passed.append(arguments) or (arguments, {"ok": True}),
    )
    result = gateway.approval_transactions.create_apply_request({
        "target_tool": "vrcforge_apply_shader_tuning",
        "arguments": {"projectPath": str(project), "executionTarget": expanded},
    })
    assert result["status"] == "pending"
    assert passed[0]["executionTarget"] == expanded

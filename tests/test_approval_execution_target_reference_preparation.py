from __future__ import annotations

from copy import deepcopy

import pytest

from agent_gateway import AgentGateway, AgentGatewayError
from execution_target import EXECUTION_TARGET_REF_SCHEMA, EXECUTION_TARGET_SCHEMA


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
    config.allow_write_requests = True
    config.execution_mode = "approval"
    gateway.save_config(config)


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

    def resolve(reference, *, project_root, session_context, required_scope, refresh_scene_snapshot):
        resolution.append((reference, project_root, session_context, required_scope))
        assert refresh_scene_snapshot is True
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

    def reject_wrong_scope(reference, *, project_root, session_context, required_scope, refresh_scene_snapshot):
        seen.append((project_root, session_context, required_scope))
        assert refresh_scene_snapshot is True
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

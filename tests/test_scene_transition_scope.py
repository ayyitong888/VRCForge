from pathlib import Path
from types import SimpleNamespace

import pytest

from execution_target import (
    ExecutionTargetError,
    canonical_namespace,
    project_identity,
    validate_execution_target,
)
from mcp_tool_descriptor import identity_scope
from mcp_tool_descriptor import standardize_tool_descriptor


def _target(root: Path, scope: str = "project") -> dict:
    normalized = str(root.resolve())
    target = {
        "schema": "vrcforge.execution_target.v1",
        "scope": scope,
        "project": {"root": normalized, "projectId": project_identity(normalized)},
        "editor": {"unityPid": 123, "processStartTime": "1.0", "coreInstanceId": "core"},
        "resolutionCandidateCount": 1,
        "ambiguous": False,
    }
    target["namespace"] = canonical_namespace(target)
    return target


def test_open_single_existing_scene_uses_project_identity_scope() -> None:
    assert identity_scope(
        "vrcforge_scene_transition",
        write=True,
        arguments={"action": "open_single", "scenePath": "Assets/Existing.unity"},
    ) == "project"


def test_other_scene_transition_actions_and_empty_action_remain_scene_scoped() -> None:
    for action in ("open_additive", "new_saved", "set_active", "unload", "reload_saved", ""):
        assert identity_scope(
            "vrcforge_scene_transition", write=True, arguments={"action": action}
        ) == "scene"


def test_project_target_still_validates_editor_and_project_identity(tmp_path: Path) -> None:
    target = _target(tmp_path)
    validated = validate_execution_target(
        target, project_root=str(tmp_path), required_scope="project"
    )
    assert validated["scope"] == "project"
    target["editor"]["coreInstanceId"] = "other-core"
    with pytest.raises(ExecutionTargetError, match="does not match"):
        validate_execution_target(
            target,
            project_root=str(tmp_path),
            core_identity={"processId": 123, "processStartTime": "1.0", "instanceId": "core"},
            required_scope="project",
        )


def test_scene_target_is_still_required_for_other_actions(tmp_path: Path) -> None:
    target = _target(tmp_path, "project")
    with pytest.raises(ExecutionTargetError, match="does not cover"):
        validate_execution_target(target, project_root=str(tmp_path), required_scope="scene")


def test_gateway_dispatch_uses_action_scope_and_descriptor_explains_it(monkeypatch) -> None:
    import agent_gateway
    import dashboard_server

    observed = {}

    def validate(_target, *, project_root, required_scope):
        observed.update({"project_root": project_root, "required_scope": required_scope})
        return {"scope": "project"}

    monkeypatch.setattr(agent_gateway, "validate_runtime_execution_target", validate)
    handler = SimpleNamespace(requires_approved_execution_context=True)
    result = dashboard_server.AGENT_GATEWAY._validate_external_mcp_execution_target(
        "vrcforge_scene_transition",
        {
            "projectPath": "D:/Unity/Project",
            "action": "open_single",
            "executionTarget": {"scope": "project"},
        },
        write_handler=handler,
    )
    assert result == {"scope": "project"}
    assert observed == {"project_root": "D:/Unity/Project", "required_scope": "project"}

    descriptor = standardize_tool_descriptor(
        {"name": "vrcforge_scene_transition", "description": "Open scenes."},
        write=True,
    )
    assert descriptor["requiredIdentity"]["actionAwareScope"] == {
        "when": {"action": "open_single"},
        "minimumScope": "project",
        "otherwise": "scene",
    }

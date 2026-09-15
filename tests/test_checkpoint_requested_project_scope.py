"""Actual checkpoint service, persisted record and archive; no Unity fixture."""
import pytest

from agent_gateway import AgentGateway


@pytest.mark.parametrize("operation", ["preview", "restore"])
@pytest.mark.parametrize("field", ["projectPath", "project_root", "projectRoot", "project_path",
                                 "unity_project", "unityProject", "workspace_root", "workspaceRoot", "cwd"])
def test_explicit_conflicting_project_is_rejected_before_checkpoint_io(tmp_path, operation, field):
    service = AgentGateway(tmp_path / "config.json", tmp_path / "audit").checkpoint_recovery
    project = tmp_path / "files"
    target = project / "Assets" / "value.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"snapshot")
    checkpoint = service._create_archive_checkpoint(project, {
        "id": "ckpt_project_scope", "projectRoot": str(project), "status": "unavailable",
        "createdAt": "2026-09-15T00:00:00+00:00", "targetTool": "vrcforge_scene_save"})
    assert checkpoint["ok"]
    service._append_checkpoint(checkpoint)
    assert service._load_checkpoint(checkpoint["id"]) is not None
    target.write_bytes(b"current")
    arguments = {"checkpointId": checkpoint["id"], field: str(tmp_path / "different"), "confirmRestore": True}
    result = (service.preview_restore_checkpoint(arguments) if operation == "preview"
              else service.restore_checkpoint(arguments))
    assert result["ok"] is False
    assert result.get("errorCode") == "checkpoint_project_mismatch"
    assert target.read_bytes() == b"current"
    assert service.preview_restore_checkpoint({"checkpointId": checkpoint["id"]})["ok"]
    assert service.preview_restore_checkpoint({"checkpointId": checkpoint["id"], field: str(project)})["ok"]


def test_checkpoint_project_constraint_does_not_require_a_live_editor_binding():
    from jsonschema import Draft202012Validator
    from unity_tool_schema_projection import canonical_unity_read_tool_input_schema, canonical_unity_write_tool_input_schema
    read = canonical_unity_read_tool_input_schema("vrcforge_preview_restore_checkpoint")
    write = canonical_unity_write_tool_input_schema("vrcforge_restore_checkpoint")
    for extra in ({}, {"projectPath": "D:/Expected"}):
        Draft202012Validator(read).validate({"checkpointId": "retained", **extra})
        Draft202012Validator(write).validate({"checkpointId": "retained", "confirmRestore": True, **extra})


def test_checkpoint_id_remains_unambiguous_after_multiple_project_scopes(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway._register_runtime_project_scope(str(tmp_path / "one"))
    gateway._register_runtime_project_scope(str(tmp_path / "two"))
    for name in ("vrcforge_preview_restore_checkpoint", "vrcforge_restore_checkpoint"):
        gateway._guard_external_mcp_project_scope(name, {"checkpointId": "retained"})

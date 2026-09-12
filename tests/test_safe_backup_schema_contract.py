import jsonschema
import pytest

from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from agent_gateway import AgentGateway
from unity_tool_schema_projection import canonical_unity_read_tool_input_schema


def test_safe_backup_public_schemas_expose_real_parameter_contracts() -> None:
    create = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_create_safe_backup"]
    preview = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_restore_backup"]
    restore = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_restore_safe_backup"]

    assert create["additionalProperties"] is False
    assert {"projectPath", "avatarPath", "assetPaths", "includeOpenScenes"}.issubset(create["properties"])
    assert create["properties"]["assetPaths"]["items"]["type"] == "string"
    assert "when-to-use" in create["description"]
    assert "when-NOT-to-use" in create["description"]

    for schema in (preview, restore):
        assert schema["additionalProperties"] is False
        assert {"projectPath", "backupPath", "backupId", "assetPaths", "allowProjectMismatch", "allowOverwriteChanged"}.issubset(schema["properties"])
        assert "when-to-use" in schema["description"]
        assert "when-NOT-to-use" in schema["description"]


def test_safe_backup_preview_schema_is_exposed_by_read_catalogue(tmp_path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.register_tool(
        "vrcforge_preview_restore_backup",
        "Preview a safe backup restore.",
        "plan/preview",
        lambda _args: {"ok": True},
    )
    listed = {
        item["name"]: item
        for item in gateway.build_external_mcp_tools("planning", tool_blocks=["checkpoint"])
    }
    preview_schema = canonical_unity_read_tool_input_schema("vrcforge_preview_restore_backup")
    exposed_schema = listed["vrcforge_preview_restore_backup"]["inputSchema"]
    assert exposed_schema["additionalProperties"] is False
    assert set(preview_schema["properties"]).issubset(exposed_schema["properties"])
    assert {"projectPath", "executionTarget"}.issubset(exposed_schema["required"])
    assert "backupPath" in exposed_schema["properties"]
    assert "backupId" in exposed_schema["properties"]


def test_restore_preview_rejects_overwrite_changed_but_restore_write_allows_it() -> None:
    preview = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_restore_backup"]
    restore = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_restore_safe_backup"]
    base = {"projectPath": "D:/Unity", "backupPath": "D:/Unity/.vrcforge/backups/b1"}

    jsonschema.validate({**base, "allowOverwriteChanged": False}, preview)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**base, "allowOverwriteChanged": True}, preview)
    jsonschema.validate({**base, "allowOverwriteChanged": True}, restore)
    assert "restore write" in preview["properties"]["allowOverwriteChanged"]["description"]

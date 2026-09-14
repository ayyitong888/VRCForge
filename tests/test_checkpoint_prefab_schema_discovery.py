"""Production descriptor tests; these do not execute checkpoint or Unity writes."""
import pytest
from jsonschema import Draft202012Validator


@pytest.fixture(scope="module")
def descriptors():
    import dashboard_server

    gateway = dashboard_server.AGENT_GATEWAY
    catalog = {item["name"]: item for item in gateway.build_external_mcp_tools("execution", tool_blocks=["*"])}
    return gateway, catalog


def test_restore_checkpoint_registered_schema_exposes_real_arguments(descriptors):
    gateway, catalog = descriptors
    name = "vrcforge_restore_checkpoint"
    schema = catalog[name]["inputSchema"]
    assert {"checkpointId", "checkpoint_id", "confirmRestore", "confirm_restore"} <= schema["properties"].keys()
    assert schema == gateway.shared_agent_tool_descriptor(name, write=True)["inputSchema"]
    validator = Draft202012Validator(schema)
    for payload in [
        {"checkpointId": "retained-id", "confirmRestore": True},
        {"checkpoint_id": "retained-id", "confirm_restore": True},
        {"checkpointId": "retained-id", "confirmRestore": False, "confirm_restore": True},
        {"checkpointId": "retained-id", "checkpoint_id": "", "confirmRestore": True},
    ]:
        validator.validate(payload)
    for payload in [{}, {"checkpointId": "retained-id"}, {"checkpointId": "retained-id", "confirmRestore": False}]:
        assert list(validator.iter_errors(payload))
    assert "projectPath" not in schema.get("required", [])


def test_prefab_preview_reuses_exact_registered_write_input(descriptors):
    gateway, catalog = descriptors
    preview_name = "vrcforge_preview_scene_object_prefab"
    schema = catalog[preview_name]["inputSchema"]
    assert {"projectPath", "sourceScenePath", "sourceObjectPath", "prefabAssetPath"} <= schema["properties"].keys()
    assert schema == catalog["vrcforge_save_scene_object_as_prefab"]["inputSchema"]
    assert schema == gateway.shared_agent_tool_descriptor(preview_name, write=False)["inputSchema"]


def test_read_only_checkpoint_preview_does_not_require_restore_confirmation(descriptors):
    _, catalog = descriptors
    schema = catalog["vrcforge_preview_restore_checkpoint"]["inputSchema"]
    Draft202012Validator(schema).validate({"checkpointId": "retained-id"})
    Draft202012Validator(schema).validate({"checkpoint_id": "retained-id"})

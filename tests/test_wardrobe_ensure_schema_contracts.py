"""Actual registered wardrobe/ensure schema projections; no Unity writes."""
import pytest
from jsonschema import Draft202012Validator

FIELDS = {
    "create_wardrobe": "projectPath avatarPath parameterName menuName defaultControlName layerName assetDir writeDefaults saved networkSynced",
    "add_wardrobe_outfit": "projectPath avatarPath parameterName outfitName objectPaths offObjectPaths addMenuToggle setObjectsDefaultOff subMenuOverflow subMenuName clipOutputDir value writeDefaults",
    "add_outfit_part": "projectPath avatarPath parameterName partName objectPaths value partParameterName addMenuToggle setObjectsDefaultOff defaultOn subMenuName clipOutputDir writeDefaults",
    "manage_wardrobe": "projectPath avatarPath action parameterName outfitName targetName stateName controlName newName newOutfitName assetDir clipOutputDir targetValue outfitValue value orderValues targetValues deleteObjects deactivateObjects deleteGeneratedAssets confirmDeleteWardrobe",
    "ensure_expression_parameter": "projectPath avatarPath parameterName valueType defaultValue saved networkSynced assetDir",
    "ensure_expression_menu_control": "projectPath avatarPath menuPath controlName controlType parameterName controlValue assetDir",
    "ensure_animator_state": "projectPath avatarPath layerName stateName parameterName parameterType conditionMode threshold writeDefaults assetDir",
}

@pytest.fixture(scope="module")
def descriptors():
    import dashboard_server as server
    gateway = server.AGENT_GATEWAY
    return gateway, {x["name"]: x for x in gateway.build_external_mcp_tools("execution", tool_blocks=["*"])}

@pytest.mark.parametrize("suffix", FIELDS)
@pytest.mark.parametrize("preview", [False, True])
def test_registered_fields_and_preview_write_parity(descriptors, suffix, preview):
    gateway, catalog = descriptors
    name = "vrcforge_" + ("preview_" if preview else "") + suffix
    schema = catalog[name]["inputSchema"]
    assert set(FIELDS[suffix].split()) <= schema["properties"].keys()
    assert schema == gateway.shared_agent_tool_descriptor(name, write=not preview)["inputSchema"]
    assert schema == catalog["vrcforge_" + suffix]["inputSchema"]
    assert not {"settings_path", "unity_host", "unity_port", "api_key"} & schema["properties"].keys()
    Draft202012Validator.check_schema(schema)


def test_creation_keeps_default_and_legacy_paths_and_order_are_accepted(descriptors):
    import wardrobe_outfit_workflow_service as workflow
    _, catalog = descriptors
    identity = {"executionTarget": {"schema": "vrcforge.execution_target.v1", "namespace": {}, "scope": {}, "project": {}, "editor": {}}}
    creation = catalog["vrcforge_create_wardrobe"]["inputSchema"]
    Draft202012Validator(creation).validate(identity)
    assert creation["properties"]["parameterName"]["default"] == "Clothes"
    assert workflow.build_create_wardrobe_request({}, True)["parameterName"] == "Clothes"
    payload = {**identity, "parameter_name": "Clothes", "display_name": "Hat", "outfit_value": "2", "on_object_paths": "Avatar/Hat"}
    schema = catalog["vrcforge_preview_add_outfit_part"]["inputSchema"]
    Draft202012Validator(schema).validate(payload)
    request = workflow.build_add_outfit_part_request(payload, True)
    assert request["value"] == 2 and request["objectPaths"] == ["Avatar/Hat"]
    assert workflow.validate_add_outfit_part_request(request) is None
    payload = {**identity, "action": "sort", "wardrobe_parameter": "Clothes", "order_values": "3;2,1"}
    Draft202012Validator(catalog["vrcforge_preview_manage_wardrobe"]["inputSchema"]).validate(payload)
    assert workflow.build_manage_wardrobe_request(payload, True)["orderValues"] == [3, 2, 1]


def test_ensure_aliases_keep_real_builder_defaults(descriptors):
    import dashboard_server as server
    _, catalog = descriptors
    identity = {"executionTarget": {"schema": "vrcforge.execution_target.v1", "namespace": {}, "scope": {}, "project": {}, "editor": {}}}
    args = {**identity, "layer_name": "Wardrobe", "state_name": "Dress", "parameter_name": "Clothes"}
    schema = catalog["vrcforge_preview_ensure_animator_state"]["inputSchema"]
    Draft202012Validator(schema).validate(args)
    request = server.build_ensure_animator_state_request(args, True)
    assert request["parameterType"] == schema["properties"]["parameterType"]["default"] == "Int"
    assert request["conditionMode"] == schema["properties"]["conditionMode"]["default"] == "Equals"
    assert request["writeDefaults"] == schema["properties"]["writeDefaults"]["default"] is True
    assert list(Draft202012Validator(schema).iter_errors(identity))

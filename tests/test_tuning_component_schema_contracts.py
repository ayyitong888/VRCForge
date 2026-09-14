"""Registered schema discovery; no live handler or Unity operation."""
import pytest
from jsonschema import Draft202012Validator

NAMES = ['apply_tuning_preset', 'create_component_feature', 'export_vrm', 'plan_face_tuning', 'preview_component_feature', 'preview_constraint_sources', 'reapply_tuning_history', 'rollback_parameters', 'run_face_tuning', 'save_new_scene', 'undo_blendshapes']
@pytest.fixture(scope="module")
def catalog():
    import dashboard_server as d
    return d.AGENT_GATEWAY, {t["name"]: t for t in d.AGENT_GATEWAY.build_external_mcp_tools("execution", tool_blocks=["*"])}

@pytest.mark.parametrize("suffix",NAMES)
def test_registered_schema_has_fields_and_internal_parity(catalog,suffix):
    gateway,tools=catalog;name="vrcforge_"+suffix
    schema=tools[name]["inputSchema"]
    assert set(schema["properties"])-{"promptSkillProvenance","executionTarget"}
    assert schema==gateway.shared_agent_tool_descriptor(name,write=name in gateway._write_handlers)["inputSchema"]
    assert not {"settings_path","unity_host","unity_port","unity_instance","api_key","model"}&schema["properties"].keys()
    Draft202012Validator.check_schema(schema)

def test_component_conditional_keys_and_nested_legacy_shape(catalog):
    from component_feature_write import normalize_request
    _,tools=catalog
    schema=tools["vrcforge_preview_component_feature"]["inputSchema"]
    identity={"executionTarget":{"schema":"vrcforge.execution_target.v1","namespace":{},"scope":{},"project":{},"editor":{}}}
    leaf={"scenePath":"Assets/Fixture.unity","gameObjectPath":"Avatar/Item","featureKind":"toggle","menuPath":"Menu/Item","targetObjectPaths":["Avatar/Item"],"slider":False,"defaultOn":False,"saved":True,"globalParameter":""}
    assert normalize_request(leaf)["featureKind"]=="toggle"
    for p in [{**identity,**leaf},{**identity,"arguments":leaf},{**identity,"params":leaf}]:Draft202012Validator(schema).validate(p)
    bad=dict(leaf);bad.pop("saved")
    assert list(Draft202012Validator(schema).iter_errors({**identity,**bad}))
    assert schema==tools["vrcforge_create_component_feature"]["inputSchema"]

def test_undo_spelling_and_face_defaults(catalog):
    _,tools=catalog
    undo=tools["vrcforge_undo_blendshapes"]["inputSchema"]
    assert "avatar_path" in undo["required"]
    face=tools["vrcforge_plan_face_tuning"]["inputSchema"]
    assert face["properties"]["source_mode"]["default"]=="unity_live_export"
    assert face["properties"]["mock_execute"]["default"] is False
    assert "instruction" not in face.get("required",[])
    assert "snapshot_path" in tools["vrcforge_rollback_parameters"]["inputSchema"]["properties"]

def test_constraint_preview_reuses_write_and_accepts_nested_request(catalog):
    _,tools=catalog
    schema=tools["vrcforge_preview_constraint_sources"]["inputSchema"]
    assert schema==tools["vrcforge_set_constraint_sources"]["inputSchema"]
    Draft202012Validator(schema).validate({"projectPath":"D:/Fixture","executionTarget":{"schema":"vrcforge.execution_target.v1","namespace":{},"scope":{},"project":{},"editor":{}},"arguments":{"scenePath":"Assets/Fixture.unity","gameObjectPath":"Avatar/Item","constraintKind":"position","componentIndex":0,"sources":[]}})

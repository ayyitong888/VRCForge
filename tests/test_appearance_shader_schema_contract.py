import pytest
import jsonschema
from unity_tool_schema_projection import canonical_unity_read_tool_input_schema as read_schema, canonical_unity_write_tool_input_schema as write_schema
from dashboard_api_models import ShaderMaterialRestoreRequest, ShaderMaterialPlanRequest


@pytest.mark.parametrize("name,fields", [
 ("vrcforge_restore_shader_tuning", {"projectPath", "avatarPath"}),
 ("vrcforge_reapply_shader_tuning_history", {"projectPath", "avatarPath", "historyId", "locked_materials", "locked_properties", "categoryOverrides"}),
 ("vrcforge_apply_shader_tuning_preset", {"projectPath", "avatarPath", "presetId", "locked_materials", "locked_properties", "categoryOverrides"}),
])
def test_missing_appearance_write_schemas_expose_actual_parameters(name, fields):
 schema = write_schema(name)
 assert fields <= schema["properties"].keys()
 assert "executionTarget" in schema["required"]
 assert "projectPath" in schema["required"]


def test_shader_assignment_preview_reuses_exact_write_contract():
 assert read_schema("vrcforge_preview_material_shader_assignment") == write_schema("vrcforge_set_material_shader")


@pytest.mark.parametrize("name,key", [("vrcforge_reapply_shader_tuning_history", "historyId"), ("vrcforge_apply_shader_tuning_preset", "presetId")])
def test_saved_shader_id_required_and_changes_not_caller_supplied(name, key):
 schema = write_schema(name)
 assert key in schema.get("required", [])
 assert schema["properties"][key]["minLength"] == 1
 assert "changes" not in schema["properties"]


def test_restore_and_replay_public_fields_reach_actual_models():
 args = {"projectPath": "D:/Unity", "avatarPath": "Avatar"}
 restore = ShaderMaterialRestoreRequest(**args)
 assert restore.project_path == args["projectPath"] and restore.avatar_path == "Avatar"
 replay = ShaderMaterialPlanRequest(**args, locked_materials=["body"], locked_properties=["smoothness"], categoryOverrides={"body": "skin"})
 assert replay.locked_materials == ["body"] and replay.locked_properties == ["smoothness"]
 assert replay.category_overrides == {"body": "skin"}


def test_registered_public_catalogue_exposes_business_fields_and_identity():
 import dashboard_server
 catalogue = {tool["name"]: tool for tool in dashboard_server.AGENT_GATEWAY.build_external_mcp_tools("execution", tool_blocks=["*"])}
 for name, field in [("vrcforge_preview_material_shader_assignment", "shaderName"), ("vrcforge_restore_shader_tuning", "avatarPath"), ("vrcforge_reapply_shader_tuning_history", "historyId"), ("vrcforge_apply_shader_tuning_preset", "presetId")]:
  schema = catalogue[name]["inputSchema"]
  assert field in schema["properties"]
  assert "executionTarget" in schema["required"]


@pytest.mark.parametrize("name,id_field", [("vrcforge_restore_shader_tuning", None), ("vrcforge_reapply_shader_tuning_history", "historyId"), ("vrcforge_apply_shader_tuning_preset", "presetId")])
def test_exact_public_payload_validates_and_missing_identity_rejects(name, id_field):
 schema = write_schema(name)
 args = {"projectPath": "D:/Unity", "avatarPath": "Avatar", "executionTarget": {"schema": "vrcforge.execution_target.v1", "namespace": {}, "scope": "component", "project": {}, "editor": {}}}
 if id_field: args[id_field] = "saved-id"
 jsonschema.validate(args, schema)
 for key in ["projectPath", "executionTarget"] + ([id_field] if id_field else []):
  with pytest.raises(jsonschema.ValidationError): jsonschema.validate({k:v for k,v in args.items() if k != key}, schema)
 with pytest.raises(jsonschema.ValidationError): jsonschema.validate({**args, "changes": []}, schema)


@pytest.mark.parametrize("target", [{"rendererPath": "Avatar/Body", "slotIndex": 0}, {"materialAssetPath": "Assets/Body.mat"}])
def test_shader_assignment_accepts_both_real_target_modes(target):
 from unity_shared_input_schemas import MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA
 jsonschema.validate({"projectPath": "D:/Unity", "shaderName": "lilToon", **target}, MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA)


@pytest.mark.parametrize("target", [{"rendererPath": "Avatar/Body", "slotIndex": 0, "materialAssetPath": "Assets/Body.mat"}, {"materialAssetPath": "Assets/Body.mat", "rendererComponentId": "a" * 64}, {}, {"rendererPath": "Avatar/Body"}])
def test_shader_assignment_rejects_mixed_or_incomplete_target_modes(target):
 from unity_shared_input_schemas import MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA
 with pytest.raises(jsonschema.ValidationError):
  jsonschema.validate({"projectPath": "D:/Unity", "shaderName": "lilToon", **target}, MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA)

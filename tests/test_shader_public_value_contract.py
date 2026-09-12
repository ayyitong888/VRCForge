"""Public shader arguments describe accepted values before Unity is contacted."""
import pytest
from jsonschema import Draft202012Validator, ValidationError as SchemaError
from pydantic import ValidationError

from dashboard_api_models import (
    ShaderMaterialApplyRequest,
    ShaderMaterialPlanRequest,
    ShaderMaterialScanRequest,
)
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS


@pytest.mark.parametrize("model", [ShaderMaterialScanRequest, ShaderMaterialPlanRequest, ShaderMaterialApplyRequest])
def test_invalid_category_cannot_silently_disappear_from_a_request(model):
    with pytest.raises(ValidationError):
        model(categoryOverrides={"mat-a": "clothing"})
    assert model(categoryOverrides={"mat-a": "clothes"}).category_overrides == {"mat-a": "clothes"}
    assert model(category_overrides={"mat-a": "hair"}).category_overrides == {"mat-a": "hair"}


@pytest.mark.parametrize("name", ["vrcforge_scan_materials", "vrcforge_plan_shader_tuning", "vrcforge_preview_shader_apply", "vrcforge_apply_shader_tuning"])
def test_public_category_values_match_runtime(name):
    schema = {**UNITY_READ_TOOL_INPUT_SCHEMAS, **EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS}[name]
    request = {"projectPath": "D:/Avatar", "categoryOverrides": {"mat-a": "clothes"}}
    if name == "vrcforge_plan_shader_tuning":
        request["instruction"] = "Inspect this material."
    if name in {"vrcforge_preview_shader_apply", "vrcforge_apply_shader_tuning"}:
        request["changes"] = [{"material_id": "mat-a", "semantic_property": "smoothness", "after": 0.13}]
    validator = Draft202012Validator(schema)
    validator.validate(request)
    request["categoryOverrides"]["mat-a"] = "clothing"
    with pytest.raises(SchemaError):
        validator.validate(request)


@pytest.mark.parametrize("schema", [UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_shader_apply"], EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_apply_shader_tuning"]])
def test_shader_change_contract_rejects_unnamed_edits_and_describes_semantics(schema):
    validator = Draft202012Validator(schema)
    valid = {"projectPath": "D:/Avatar", "changes": [{"material_id": "mat-a", "semantic_property": "smoothness", "after": 0.13, "reason": "User-selected change"}]}
    validator.validate(valid)
    with pytest.raises(SchemaError):
        validator.validate({"projectPath": "D:/Avatar", "changes": [{}]})
    properties = schema["properties"]["changes"]["items"]["properties"]
    assert "semantic" in properties["semantic_property"]["description"].lower()

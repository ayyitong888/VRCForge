"""Preserve existing Pydantic input conversion in model-facing face schemas."""
import pytest
from jsonschema import Draft202012Validator
from dashboard_api_models import DashboardRequest

@pytest.mark.parametrize('field,value', [('mock_execute','false'),('mock_execute',0),('allow_low_confidence','yes'),('save_artifacts',1),('min_confidence','0.8'),('min_confidence',True)])
def test_face_schema_preserves_existing_model_coercion(field,value):
    from unity_tool_schema_projection import canonical_unity_read_tool_input_schema,canonical_unity_write_tool_input_schema
    request=DashboardRequest(**{field:value})
    assert getattr(request,field) is not None
    for schema in (canonical_unity_read_tool_input_schema('vrcforge_plan_face_tuning'),canonical_unity_write_tool_input_schema('vrcforge_run_face_tuning')):
        Draft202012Validator(schema['properties'][field]).validate(value)

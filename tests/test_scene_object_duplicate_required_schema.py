"""The discoverable duplicate contract must require the destination its binder needs."""
from copy import deepcopy

from jsonschema import Draft202012Validator
import pytest

from scene_object_copy import DUPLICATE_TOOL_NAME, SceneObjectCopyError, bind_authoritative_preview, build_wrapper_arguments
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS
from test_scene_object_copy import _duplicate_payload, _duplicate_wrapper


SCHEMAS = [
    UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_scene_object_duplicate"],
    EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_duplicate_scene_object"],
]


def minimal_request():
    wrapper = _duplicate_wrapper()
    return {"projectPath": wrapper["projectPath"], **{
        key: value for key, value in wrapper["arguments"].items()
        if key != "preserveWorldTransform"
    }}


@pytest.mark.parametrize("schema", SCHEMAS, ids=["preview", "write"])
@pytest.mark.parametrize("missing", ["targetParentScenePath", "targetParentPath", "targetName"])
def test_missing_destination_rejected_by_public_schema_and_real_binder(schema, missing):
    request = minimal_request()
    request.pop(missing)
    with pytest.raises(SceneObjectCopyError):
        bind_authoritative_preview(build_wrapper_arguments(request, DUPLICATE_TOOL_NAME), _duplicate_payload())
    errors = list(Draft202012Validator(schema).iter_errors(request))
    assert any(e.validator == "required" and missing in e.message for e in errors), (
        f"Public schema accepts missing {missing}, but production binding requires it"
    )


@pytest.mark.parametrize("schema", SCHEMAS, ids=["preview", "write"])
def test_minimal_complete_destination_binds_without_optional_fields(schema):
    request = minimal_request()
    Draft202012Validator(schema).validate(request)
    bound, _ = bind_authoritative_preview(build_wrapper_arguments(deepcopy(request), DUPLICATE_TOOL_NAME), _duplicate_payload())
    for key in ("targetParentScenePath", "targetParentPath", "targetName"):
        assert bound["arguments"][key] == request[key]
    assert bound["arguments"]["saveScene"] is True
    assert bound["arguments"]["preserveWorldTransform"] is False

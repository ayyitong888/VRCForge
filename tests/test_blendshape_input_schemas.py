"""Public BlendShape contracts must match the preview adapter and manual parser."""
import json
from types import SimpleNamespace

import jsonschema
import pytest
from pydantic import ValidationError

from dashboard_api_models import ManualBlendshapeApplyRequest
from unity_tool_schema_projection import (
    canonical_unity_read_tool_input_schema as read_schema,
    canonical_unity_write_tool_input_schema as write_schema,
)

PREVIEW = "vrcforge_preview_blendshape_apply"
APPLY = "vrcforge_apply_blendshapes"
IDENTITY = {"schema": "vrcforge.execution_target.v1", "namespace": {},
            "scope": "avatar", "project": {}, "editor": {}}
BASE = {"projectPath": "D:/Unity", "executionTarget": IDENTITY}
ITEM = {"renderer_path": "Avatar/bra", "blendshape_name": "Fit", "target_weight": 100}
CAMEL = {"rendererPath": "Avatar/bra", "blendshapeName": "Fit", "targetWeight": 100}


def test_registered_catalogue_exposes_actual_parameters_and_execution_identity():
    import dashboard_server as server
    catalog = {tool["name"]: tool for tool in
               server.AGENT_GATEWAY.build_external_mcp_tools("execution", tool_blocks=["*"])}
    for name in (PREVIEW, APPLY):
        schema = catalog[name]["inputSchema"]
        assert {"adjustments", "projectPath", "scope", "source_mode", "mock_execute"} <= schema["properties"].keys()
        assert "executionTarget" in schema["required"]
        assert schema["additionalProperties"] is True
    planning = {tool["name"] for tool in
                server.AGENT_GATEWAY.build_external_mcp_tools("planning", tool_blocks=["*"])}
    assert APPLY not in planning


def test_apply_schema_matches_manual_parser_defaults_and_snake_case_fields():
    schema = write_schema(APPLY)
    payload = {**BASE, "avatar": "Avatar", "adjustments": [{**ITEM, "previous_weight": None}]}
    jsonschema.validate(payload, schema)
    model = ManualBlendshapeApplyRequest(**payload)
    assert model.adjustments[0].target_weight == 100
    assert model.adjustments[0].previous_weight is None
    for key in ("source_mode", "mock_execute", "save_artifacts"):
        assert schema["properties"][key]["default"] == getattr(model, key)
        assert key not in schema["required"]
    item_fields = schema["properties"]["adjustments"]["items"]["properties"]
    assert {"renderer_path", "blendshape_name", "target_weight", "previous_weight"} <= item_fields.keys()
    assert not ({"rendererPath", "blendshapeName", "targetWeight"} & item_fields.keys())
    assert "unity_live_export" in schema["properties"]["source_mode"]["description"]
    assert "false" in schema["properties"]["mock_execute"]["description"]


def test_public_apply_rejects_core_payload_shape_instead_of_advertising_it():
    payload = {**BASE, "avatar": "Avatar", "adjustments": [CAMEL]}
    with pytest.raises(ValidationError):
        ManualBlendshapeApplyRequest(**payload)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, write_schema(APPLY))


@pytest.mark.parametrize("avatar_key", ["avatar", "avatarPath", "avatar_path"])
@pytest.mark.parametrize("item", [ITEM, CAMEL])
def test_preview_aliases_reach_actual_registered_handler(monkeypatch, avatar_key, item):
    import dashboard_server as server
    requests = []
    def load_export(settings, request):
        requests.append(request)
        return {}, "fixture-live-export", False
    monkeypatch.setattr(server, "load_dashboard_settings", lambda request: None)
    monkeypatch.setattr(server, "load_dashboard_export_payload", load_export)
    monkeypatch.setattr(server, "resolve_avatar_selection", lambda payload, avatar: SimpleNamespace(avatar_path=avatar))
    monkeypatch.setattr(server, "build_allowed_blendshape_index", lambda *_: {("Avatar/bra", "Fit"): {"currentWeight": 0}})
    payload = {**BASE, avatar_key: "Avatar", "adjustments": [item]}
    schema = read_schema(PREVIEW)
    jsonschema.validate(payload, schema)
    result = server.AGENT_GATEWAY._tools[PREVIEW].handler(payload)
    assert result["ok"] is True
    assert result["executionMode"] == "live-unity"
    assert result["adjustmentCount"] == 1
    core = json.loads(result["applyPayload"])
    assert core["params"]["adjustments"] == [CAMEL]
    for key in ("source_mode", "mock_execute", "save_artifacts"):
        assert schema["properties"][key]["default"] == getattr(requests[0], key)


@pytest.mark.parametrize("weight", [-1, 101])
@pytest.mark.parametrize("name,project", [(PREVIEW, read_schema), (APPLY, write_schema)])
def test_schema_rejects_weights_outside_actual_supported_range(name, project, weight):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**BASE, "avatar": "Avatar", "adjustments": [{**ITEM, "target_weight": weight}]}, project(name))


@pytest.mark.parametrize("name,project", [(PREVIEW, read_schema), (APPLY, write_schema)])
def test_schema_rejects_missing_adjustment_targets_and_identity(name, project):
    schema = project(name)
    for bad_item in [{}, {"target_weight": 10}, {"renderer_path": "Avatar/bra"}]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**BASE, "avatar": "Avatar", "adjustments": [bad_item]}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"projectPath": "D:/Unity", "avatar": "Avatar", "adjustments": [ITEM]}, schema)


def test_live_write_parameters_preserve_scope_and_explicit_non_mock_request():
    payload = {**BASE, "avatar": "Avatar", "scope": "all", "source_mode": "unity_live_export",
               "mock_execute": False, "adjustments": [ITEM], "save_artifacts": True}
    jsonschema.validate(payload, write_schema(APPLY))
    request = ManualBlendshapeApplyRequest(**payload)
    assert (request.avatar, request.project_path, request.scope, request.source_mode, request.mock_execute) == (
        "Avatar", "D:/Unity", "all", "unity_live_export", False)

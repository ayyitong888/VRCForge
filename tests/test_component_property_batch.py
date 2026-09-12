"""Generic component property batch schema, identity, and forwarding contract."""
from copy import deepcopy

import jsonschema
import pytest

from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS


def target(index=0):
    return {
        "schema": "vrcforge.execution_target.v1", "namespace": f"fixture/{index}", "scope": "component",
        "project": {"root": "Project", "projectId": "project"},
        "editor": {"unityPid": 1, "processStartTime": "start", "coreInstanceId": "core"},
        "scene": {"guid": "scene", "assetPath": "Assets/Main.unity", "absolutePath": "Project/Assets/Main.unity", "revision": "1", "digest": "digest"},
        "avatar": {"globalObjectId": "avatar", "exactHierarchyPath": "Avatar"},
        "object": {"globalObjectId": f"object-{index}", "exactHierarchyPath": f"Avatar/Part{index}"},
        "component": {"globalObjectId": f"component-{index}", "type": "UnityEngine.SkinnedMeshRenderer"},
    }


def request(count=2):
    return {"projectPath": "Project", "executionTarget": target(), "queries": [
        {"gameObjectPath": f"Avatar/Part{i}", "componentType": "UnityEngine.SkinnedMeshRenderer",
         "componentIndex": 0, "propertyNames": ["rootBone", "localBounds"], "executionTarget": target(i)}
        for i in range(count)
    ]}


def test_batch_schema_accepts_bound_components_and_preserves_scalar_requirements():
    schema = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_get_property"]
    jsonschema.validate(request(), schema)
    scalar = {"projectPath": "Project", "gameObjectPath": "Avatar/Part0", "componentType": "Transform", "propertyPath": "position"}
    jsonschema.validate(scalar, schema)
    for key in scalar:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({k: v for k, v in scalar.items() if k != key}, schema)


@pytest.mark.parametrize("field,value", [("propertyPath", "position"), ("gameObjectPath", "Avatar"), ("componentIndex", 0), ("maxItems", 10)])
def test_schema_rejects_mixed_scalar_and_batch(field, value):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**request(), field: value}, UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_get_property"])


def prepared(monkeypatch, value):
    import component_property_batch as batch
    checks = []
    monkeypatch.setattr(batch, "validate_runtime_execution_target", lambda t, **kw: checks.append("runtime") or deepcopy(t))
    monkeypatch.setattr(batch, "validate_execution_target", lambda t, **kw: checks.append("component") or deepcopy(t))
    return batch.prepare_component_property_batch(value), checks


def test_batch_checks_all_identities_and_projects_compact_core_rows(monkeypatch):
    result, checks = prepared(monkeypatch, request())
    assert checks == ["runtime", "component", "component"]
    assert len(result["queries"]) == 2
    row = result["queries"][1]
    assert row["objectGlobalObjectId"] == "object-1"
    assert row["componentGlobalObjectId"] == "component-1"
    assert row["sceneGuid"] == "scene"
    assert "executionTarget" not in row
    assert row["propertyNames"] == ["rootBone", "localBounds"]


@pytest.mark.parametrize("group,key,value", [
    ("project", "projectId", "other"), ("editor", "coreInstanceId", "other"),
    ("scene", "guid", "other"), ("scene", "digest", "other"),
    ("avatar", "globalObjectId", "other"), ("object", "exactHierarchyPath", "Other/Part"),
    ("component", "type", "UnityEngine.Transform"),
])
def test_batch_rejects_mixed_scope_and_changed_target_before_core(monkeypatch, group, key, value):
    args = request()
    args["queries"][1]["executionTarget"][group][key] = value
    with pytest.raises(ValueError):
        prepared(monkeypatch, args)


def test_batch_rejects_actual_property_budget_and_duplicates(monkeypatch):
    args = request(33)
    for row in args["queries"]:
        row["propertyNames"] = [f"field{i}" for i in range(16)]
    with pytest.raises(ValueError, match="512"):
        prepared(monkeypatch, args)
    args = request()
    args["queries"][1] = deepcopy(args["queries"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        prepared(monkeypatch, args)


@pytest.mark.parametrize("change", ["empty", "many", "properties", "nested", "unknown", "missing_target"])
def test_public_schema_rejects_invalid_batch_shape(change):
    args = request()
    if change == "empty": args["queries"] = []
    elif change == "many": args = request(129)
    elif change == "properties": args["queries"][0]["propertyNames"] = [f"field{i}" for i in range(17)]
    elif change == "nested": args["queries"][0]["propertyNames"] = ["rootBone.localToWorldMatrix"]
    elif change == "unknown": args["queries"][0]["unknown"] = True
    else: del args["queries"][0]["executionTarget"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(args, UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_get_property"])


def test_handler_forwards_one_core_call_only_after_all_identity_checks(monkeypatch):
    import component_property_batch as batch
    import dashboard_server as dashboard
    checks, calls = [], []
    monkeypatch.setattr(batch, "validate_runtime_execution_target", lambda t, **kw: checks.append("anchor") or deepcopy(t))
    monkeypatch.setattr(batch, "validate_execution_target", lambda t, **kw: checks.append("row") or deepcopy(t))
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _: object())
    def invoke(settings, name, args):
        assert checks == ["anchor", "row", "row"]
        calls.append((name, args))
        return {"successCount": 3, "failureCount": 1, "allSucceeded": False}
    monkeypatch.setattr(dashboard, "invoke_unity_mcp", invoke)
    monkeypatch.setattr(dashboard, "extract_tool_result_payload", lambda value: value)
    result = dashboard.read_component_property_sync(request())
    assert result["allSucceeded"] is False and result["failureCount"] == 1
    assert len(calls) == 1 and calls[0][0] == "vrc_get_property"
    bad = request()
    bad["queries"][1]["executionTarget"]["scene"]["guid"] = "other"
    with pytest.raises(ValueError):
        dashboard.read_component_property_sync(bad)
    assert len(calls) == 1

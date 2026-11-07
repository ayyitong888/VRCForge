import pytest

from runtime_planner_service import validate_planner_tool_arguments
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from unity_shared_input_schemas import ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA


def validate(spec, value, *, required=True):
    return validate_planner_tool_arguments({
        "type": "object", "properties": {"value": spec},
        "required": ["value"] if required else [], "additionalProperties": False,
    }, {"value": value})


@pytest.mark.parametrize("value", ["D:/Projects/Avatar", None])
def test_nullable_project_path_accepts_declared_types(value):
    assert validate({"type": ["string", "null"]}, value)["ok"]


@pytest.mark.parametrize("value", [0, True, [], {}])
def test_nullable_project_path_rejects_other_types(value):
    assert not validate({"type": ["string", "null"]}, value)["ok"]


@pytest.mark.parametrize("spec,good,bad", [
    ({"type": ["array", "null"], "minItems": 1, "items": {"type": ["string", "null"]}}, ["x", None], [3]),
    ({"type": ["object", "null"], "required": ["name"], "properties": {"name": {"type": "string"}}, "additionalProperties": False}, {"name": "x"}, {"name": "x", "extra": 1}),
    ({"type": ["string", "null"], "minLength": 2}, "ab", "a"),
    ({"type": ["integer", "number", "null"], "minimum": 1}, 1.5, 0),
    ({"type": ["boolean", "null"]}, True, 1),
])
def test_union_keeps_type_specific_constraints_and_nullable_branch(spec, good, bad):
    assert validate(spec, good)["ok"]
    assert validate(spec, None)["ok"]
    assert not validate(spec, bad)["ok"]


@pytest.mark.parametrize("spec,value", [
    ({"type": ["array", "null"], "minItems": 1}, []),
    ({"type": ["object", "null"], "required": ["name"]}, {}),
    ({"type": ["integer", "null"]}, True),
    ({"type": ["number", "null"]}, float("inf")),
    ({"type": ["string", "array"]}, None),
    ({"type": "string"}, None),
    ({"type": ["string", "null"], "enum": ["only"]}, None),
])
def test_union_and_nullable_do_not_bypass_required_constraints(spec, value):
    assert not validate(spec, value)["ok"]
    assert not validate(spec, value, required=False)["ok"]


@pytest.mark.parametrize("types", [[], ["bogus"], ["string", "bogus"], ["string", {}], ["string", "string"]])
def test_malformed_or_unknown_union_types_fail_closed(types):
    assert not validate({"type": types}, "text")["ok"]


def test_real_unity_status_schema_accepts_project_path_string():
    import dashboard_server as app

    catalog = app._RuntimePlannerCatalog().read("planning", project_context_active=True)
    tool = next(tool for tool in catalog.visible_tools if tool.runtime_name == "vrcforge_unity_status")
    assert tool.input_schema["properties"]["projectPath"]["type"] == ["string", "null"]
    assert validate_planner_tool_arguments(tool.input_schema, {"projectPath": "D:/Projects/Avatar"})["ok"]


@pytest.mark.parametrize("tool_name,field", [
    ("vrcforge_read_recent_logs", "limit"),
    ("vrcforge_get_compile_errors", "maxErrors"),
])
def test_real_integer_tool_schemas_accept_integral_float(tool_name, field):
    import dashboard_server as app

    catalog = app._RuntimePlannerCatalog().read("planning", project_context_active=True)
    tool = next(tool for tool in catalog.visible_tools if tool.runtime_name == tool_name)
    assert tool.input_schema["properties"][field]["type"] == "integer"
    assert validate_planner_tool_arguments(tool.input_schema, {field: 1.0})["ok"]


@pytest.mark.parametrize("value", [1, 1.0, 2.0])
def test_integer_accepts_integral_numbers_within_bounds(value):
    assert validate({"type": "integer", "minimum": 1, "maximum": 2}, value)["ok"]


@pytest.mark.parametrize("value", [True, False, 1.5, float("inf"), float("-inf"), float("nan"), 0.0, 3.0])
def test_integer_rejects_nonintegers_and_retains_bounds(value):
    assert not validate({"type": "integer", "minimum": 1, "maximum": 2}, value)["ok"]


def test_animation_admission_explains_mutually_exclusive_curve_fields():
    schema = ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA
    row = {"bindingPath": "Body", "componentType": "SkinnedMeshRenderer",
           "propertyName": "material._Color.a", "constantFloat": 0.5}
    request = {"projectPath": "D:/FixtureUnity", "clipPath": "Assets/Test.anim",
               "animatorRootPath": "Avatar", "curves": [row]}
    assert validate_planner_tool_arguments(schema, request)["ok"]

    conflict = {**request, "curves": [{**row, "keys": [{"time": 0, "value": 0.5}]}]}
    issue = validate_planner_tool_arguments(schema, conflict)["issues"][0]
    assert issue["path"] == "curves[0]"
    assert "keys" in issue["expected"] and "constantFloat" in issue["expected"]
    assert "remove" in issue["expected"].lower()


def test_animation_admission_enforces_context_and_unique_group_paths():
    schema = ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA
    request = {"projectPath": "D:/FixtureUnity", "clipPath": "Assets/Test.anim",
               "animatorRootPath": "Avatar", "curves": [{
                   "bindingPaths": ["Body"],
                   "componentType": "SkinnedMeshRenderer",
                   "properties": [{"propertyName": "material._Color.a", "constantFloat": 0.5}],
               }]}
    assert validate_planner_tool_arguments(schema, request)["ok"]
    for invalid in ({**request, "assetOnly": True},
                    {name: value for name, value in request.items() if name != "animatorRootPath"}):
        issue = validate_planner_tool_arguments(schema, invalid)["issues"][0]
        assert "animatorRootPath" in issue["expected"] and "assetOnly" in issue["expected"]

    request["curves"][0]["bindingPaths"].append("Body")
    issue = validate_planner_tool_arguments(schema, request)["issues"][0]
    assert issue["path"] == "curves[0].bindingPaths"
    assert "duplicate" in issue["expected"].lower()


@pytest.mark.parametrize("tool,arguments,path,fields", [
    ("vrcforge_scan_animation_bindings", {"bindingSelectors": [{"componentType": "Transform"}]},
     "bindingSelectors[0]", ("path", "propertyName")),
    ("vrcforge_read_recent_logs", {"source": "memory", "file": "old.log"}, "$", ("file", "disk")),
])
def test_shared_constraints_guide_model_to_the_correct_input(tool, arguments, path, fields):
    issue = validate_planner_tool_arguments(UNITY_READ_TOOL_INPUT_SCHEMAS[tool], arguments)["issues"][0]
    assert issue["path"] == path
    assert all(field in issue["expected"] for field in fields)

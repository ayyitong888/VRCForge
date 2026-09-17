from copy import deepcopy
import json

from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, bounded_planner_tool_schema
from test_runtime_planner_service import FakeCatalog, service


def prompt_schemas(prompt):
    decoder = json.JSONDecoder()
    shared = {}
    schemas = {}
    for line in prompt.splitlines():
        if line.startswith("shared_schema_definitions="):
            shared = json.loads(line.split("=", 1)[1])["$defs"]
        if line.startswith("- ") and " schema=" in line:
            schemas[line.split()[1]] = decoder.raw_decode(line.split(" schema=", 1)[1])[0]
    return shared, schemas


def test_identical_complete_schemas_share_once_with_exact_expansion_and_no_registry_change():
    schema = {"type": "object", "additionalProperties": False, "required": ["projectPath", "executionTarget"],
              "properties": {"projectPath": {"type": "string", "minLength": 1},
                             "executionTarget": {"type": "object", "required": ["schema", "namespace", "project", "editor"],
                                                 "additionalProperties": True},
                             "mode": {"type": "string", "enum": ["exact-avatar", "exact-component"]},
                             "limit": {"type": "integer", "minimum": 1, "maximum": 20}}}
    tools = tuple(PlannerTool(name, "When to use: inspect. When NOT to use: writes.", "read", input_schema=deepcopy(schema))
                  for name in ("one", "two", "three", "four"))
    before = [deepcopy(dict(tool.input_schema)) for tool in tools]
    planner = service(catalog=FakeCatalog(planning=PlannerCatalogSnapshot(visible_tools=tools)))
    prompt = planner._build_llm_plan_prompt("inspect", [])
    shared, emitted = prompt_schemas(prompt)
    assert set(emitted) == {tool.name for tool in tools}
    assert len(shared) == 1
    for tool in tools:
        assert set(emitted[tool.name]) == {"$ref"}
        ref = emitted[tool.name]["$ref"]
        assert ref.startswith("#/$defs/vrcforge.tool_input.")
        assert shared[ref.removeprefix("#/$defs/")] == bounded_planner_tool_schema(schema)
    assert [dict(tool.input_schema) for tool in tools] == before


def test_nonidentical_constraints_and_scoped_definitions_are_not_coalesced():
    base = {"type": "object", "additionalProperties": False,
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}}}
    variants = [deepcopy(base) for _ in range(3)]
    variants[1]["properties"]["limit"]["maximum"] = 21
    variants[2]["additionalProperties"] = True
    scoped = {**deepcopy(base), "$defs": {"local": {"const": "unique-scope"}}}
    variants.extend([scoped, deepcopy(scoped)])
    tools = tuple(PlannerTool(f"tool{i}", "inspect", "read", input_schema=schema) for i, schema in enumerate(variants))
    prompt = service(catalog=FakeCatalog(planning=PlannerCatalogSnapshot(visible_tools=tools)))._build_llm_plan_prompt("inspect", [])
    shared, emitted = prompt_schemas(prompt)
    assert not shared
    assert list(emitted.values()) == [bounded_planner_tool_schema(schema) for schema in variants]


def test_shared_complete_schema_keeps_shared_provenance_definition_exact():
    from runtime_planner_service import _shared_planner_schema_defs, _planner_schema_without_shared_defs
    provenance_name = "vrcforge.prompt_skill_provenance.v1"
    provenance = {"type": "object", "required": ["skillId"], "properties": {"skillId": {"type": "string"}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["projectPath"],
              "properties": {"projectPath": {"type": "string", "description": "The exact absolute bound project path."},
                             "promptSkillProvenance": {"$ref": "#/$defs/" + provenance_name}},
              "$defs": {provenance_name: provenance}}
    before = deepcopy(schema)
    shared = _shared_planner_schema_defs([schema] * 8)
    projected = _planner_schema_without_shared_defs(schema, shared)
    expanded = deepcopy(shared[projected["$ref"].removeprefix("#/$defs/")])
    expanded["$defs"] = {provenance_name: shared[provenance_name]}
    assert expanded == before
    assert schema == before

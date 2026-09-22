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
            schemas[line.split()[1].split("（", 1)[0]] = decoder.raw_decode(line.split(" schema=", 1)[1])[0]
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


def test_repeated_property_contracts_share_across_different_tools_losslessly():
    from runtime_planner_service import _shared_planner_schema_defs, _planner_schema_without_shared_defs

    identity = {"type": "object", "required": ["schema", "namespace", "scope", "project", "editor"],
                "additionalProperties": True, "description": "Exact bound execution identity. " * 10}
    schemas = [{"type": "object", "required": ["executionTarget", f"field{i}"],
                "additionalProperties": False,
                "properties": {"executionTarget": deepcopy(identity), f"field{i}": {"const": i}}}
               for i in range(8)]
    before = deepcopy(schemas)
    shared = _shared_planner_schema_defs(schemas)
    projected = [_planner_schema_without_shared_defs(schema, shared) for schema in schemas]
    assert len(shared) == 1
    for original, compact in zip(schemas, projected):
        reference = compact["properties"]["executionTarget"]
        assert set(reference) == {"$ref"}
        compact = deepcopy(compact)
        compact["properties"]["executionTarget"] = shared[reference["$ref"].removeprefix("#/$defs/")]
        assert compact == original
    assert schemas == before
    assert len(json.dumps([shared, projected])) < len(json.dumps(schemas))


def test_property_factoring_preserves_local_reference_scopes_and_small_contracts():
    from runtime_planner_service import _shared_planner_schema_defs, _planner_schema_without_shared_defs

    for scope_key in ("$defs", "definitions", "$id", "$anchor", "$dynamicRef", "$dynamicAnchor", "$ref"):
        contract = {"type": "object", "description": "Keep this contract local. " * 20,
                    scope_key: "local"}
        schemas = [{"type": "object", "properties": {"target": deepcopy(contract), f"field{i}": {"const": i}}}
                   for i in range(5)]
        shared = _shared_planner_schema_defs(schemas)
        assert not shared
        assert [_planner_schema_without_shared_defs(schema, shared) for schema in schemas] == schemas
    small = [{"type": "object", "properties": {"target": {"type": "string"}, f"field{i}": {"const": i}}}
             for i in range(5)]
    assert not _shared_planner_schema_defs(small)


def test_registered_catalog_prompt_sharing_round_trips_every_selected_contract():
    import dashboard_server
    from runtime_planner_service import _shared_planner_schema_defs, _planner_schema_without_shared_defs

    for layer in ("planning", "execution"):
        catalog = dashboard_server._RuntimePlannerCatalog().read(layer, project_context_active=True)
        schemas = [bounded_planner_tool_schema(tool.input_schema) for tool in catalog.visible_tools]
        originals = deepcopy(schemas)
        shared = _shared_planner_schema_defs(schemas)
        assert any(name.startswith("vrcforge.tool_property.") for name in shared)
        for schema in schemas:
            compact = _planner_schema_without_shared_defs(schema, shared)
            if set(compact) == {"$ref"}:
                compact = deepcopy(shared[compact["$ref"].removeprefix("#/$defs/")])
            for key, contract in compact.get("properties", {}).items():
                if isinstance(contract, dict) and set(contract) == {"$ref"}:
                    name = contract["$ref"].removeprefix("#/$defs/")
                    if name.startswith("vrcforge.tool_property.") and name in shared:
                        compact["properties"][key] = deepcopy(shared[name])
            for name, definition in schema.get("$defs", {}).items():
                if name in shared:
                    compact.setdefault("$defs", {})[name] = deepcopy(shared[name])
            assert compact == schema
        assert schemas == originals
        prompt = service(catalog=FakeCatalog(planning=catalog, execution=catalog))._build_llm_plan_prompt(
            "看看衣服的材质和骨骼绑定", [], exposure_layer=layer,
        )
        emitted_defs, emitted = prompt_schemas(prompt)
        assert emitted_defs == shared
        for tool, schema in zip(catalog.visible_tools, schemas):
            if tool.requires_user_activation and not catalog.computer_use_model_invocable:
                continue
            compact = _planner_schema_without_shared_defs(schema, shared)
            # The final prompt renderer must preserve generated references too.
            assert emitted[tool.name] == compact


def test_property_sharing_does_not_confuse_json_boolean_with_integer():
    from runtime_planner_service import _shared_planner_schema_defs, _planner_schema_without_shared_defs

    contract = {"const": True, "description": "Boolean identity guard. " * 20}
    schemas = [{"type": "object", "properties": {"guard": deepcopy(contract), f"field{i}": {"const": i}}}
               for i in range(6)]
    integer = deepcopy(schemas[-1])
    integer["properties"]["guard"]["const"] = 1
    shared = _shared_planner_schema_defs(schemas + [integer])
    projected = _planner_schema_without_shared_defs(integer, shared)
    assert projected["properties"]["guard"]["const"] == 1
    assert type(projected["properties"]["guard"]["const"]) is int

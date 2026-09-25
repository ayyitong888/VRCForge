"""Model-boundary regression for complete input-schema descriptions."""

from __future__ import annotations

import json

import pytest

from runtime_planner_service import PlannerCatalogSnapshot, PlannerModelResult, PlannerTool
from tests.native_planner_fixture import NativePlannerFixture
from tests.test_runtime_planner_service import FakeCatalog, FakeModel, NativeModel, service


def _schema_with_all_description_positions() -> dict[str, object]:
    long_tail = "parameter-tail-" * 40
    properties = {
        f"field{index:02d}": {
            "type": "string",
            "description": f"FIELD_{index:02d}_{long_tail}",
        }
        for index in range(30)
    }
    properties["nested"] = {
        "type": "array",
        "description": "NESTED_PARAMETER_DESCRIPTION_" + long_tail,
        "items": {
            "type": "object",
            "description": "ITEMS_DESCRIPTION_" + long_tail,
            "properties": {
                "choice": {
                    "anyOf": [
                        {"type": "string", "description": "ANYOF_BRANCH_DESCRIPTION_" + long_tail},
                        {"type": "integer", "description": "SECOND_ANYOF_BRANCH_DESCRIPTION_" + long_tail},
                    ],
                }
            },
        },
    }
    return {
        "type": "object",
        "description": "ROOT_SCHEMA_DESCRIPTION_" + long_tail,
        "properties": properties,
        "$defs": {
            "named": {
                "type": "object",
                "description": "NAMED_DEFINITION_DESCRIPTION_" + long_tail,
                "properties": {"value": {"type": "string", "description": "NAMED_PROPERTY_DESCRIPTION_" + long_tail}},
            }
        },
        "allOf": [{"description": "COMBINATION_BRANCH_DESCRIPTION_" + long_tail, "type": "object"}],
        "required": ["field00", "nested"],
        "additionalProperties": False,
    }


@pytest.mark.parametrize("transport", ["native", "legacy"])
def test_model_request_preserves_every_schema_description_position(transport: str) -> None:
    schema = _schema_with_all_description_positions()
    tool = PlannerTool("description_boundary_tool", "Inspect description boundary.", "read", input_schema=schema)
    snapshot = PlannerCatalogSnapshot(visible_tools=(tool,), routable_tools=(tool,))
    catalog = FakeCatalog(planning=snapshot, execution=snapshot)
    if transport == "native":
        model = NativeModel({"role": "assistant", "content": "done"})
        planner = service(catalog=catalog, model=model)
        planner.plan_agent_turn(
            "inspect", {"projectPath": "D:/Avatar"}, {},
            native_turn=NativePlannerFixture([{"role": "user", "content": "inspect"}]),
        )
        parameters = model.requests[0]["tools"][0]["function"]["parameters"]
    else:
        planner = service(
            catalog=catalog,
            model=FakeModel(PlannerModelResult('{"action":"reply","reply":"done"}')),
        )
        prompt = planner._build_llm_plan_prompt("inspect", [], exposure_layer="planning")
        schema_text = next(line.split(" schema=", 1)[1] for line in prompt.splitlines() if line.startswith("- description_boundary_tool "))
        parameters, _ = json.JSONDecoder().raw_decode(schema_text)

    assert parameters["description"].startswith("ROOT_SCHEMA_DESCRIPTION_")
    assert len(parameters["properties"]["field00"]["description"]) > 240
    assert parameters["properties"]["field29"]["description"].startswith("FIELD_29_")
    assert parameters["properties"]["nested"]["items"]["description"].startswith("ITEMS_DESCRIPTION_")
    assert parameters["properties"]["nested"]["items"]["properties"]["choice"]["anyOf"][0]["description"].startswith("ANYOF_BRANCH_DESCRIPTION_")
    assert parameters["$defs"]["named"]["description"].startswith("NAMED_DEFINITION_DESCRIPTION_")
    assert parameters["allOf"][0]["description"].startswith("COMBINATION_BRANCH_DESCRIPTION_")

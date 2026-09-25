import copy
import json

import pytest

from internal_tool_blocks import build_internal_tool_block_tree
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway
from tests.test_runtime_planner_service import service


def projected(result, native=False):
    text = service()._llm_loop_step_observation({
        "tool": "vrcforge_list_internal_tool_blocks", "status": "executed",
        "result": result,
    }, native_contract=native)
    return json.loads(text.split("; toolBlockDirectory=", 1)[1])


def large_directory():
    blocks = [{"name": f"block-{i}", "title": f"Title {i}",
               "description": "Use when: inspect. Do not use when: write.\n" + "Complete description " * 45 + f"tail-{i}",
               "toolNames": [f"tool-{i}-{j}" for j in range(85)],
               "loadCall": {"skill_tool": "load_internal_tool_block", "skill_params": {"block": f"block-{i}"}}}
              for i in range(23)]
    return {"ok": True, "schema": "vrcforge.internal_tool_blocks.v1",
            "blocks": blocks, "tree": {"index": "0", "name": "internal", "children": copy.deepcopy(blocks)},
            "loadedBlocks": [f"block-{i}" for i in range(23)],
            "internalToolSelections": {"block-22": ["tool-22-84"]}}


@pytest.mark.parametrize("native", [False, True])
def test_directory_preserves_complete_metadata_and_deduplicates_exact_root(native):
    original = large_directory()
    before = copy.deepcopy(original)
    result = projected(original, native)
    assert result["blocks"] == original["blocks"]
    assert result["loadedBlocks"] == original["loadedBlocks"]
    assert result["internalToolSelections"] == original["internalToolSelections"]
    assert result["tree"] == {"index": "0", "name": "internal", "childrenRef": "blocks"}
    assert original == before
    assert len(json.dumps(result)) < len(json.dumps(original))


def test_directory_preserves_unique_selected_leaf_metadata_and_nonmatching_tree():
    original = build_internal_tool_block_tree(selector="project_environment/files", leaves=[{
        "name": "fixture_read", "block": "project_environment/files", "mode": "read",
        "description": "Complete tool metadata.",
    }])
    assert projected(original)["tree"] == original["tree"]
    original["tree"] = copy.deepcopy(original["blocks"][0])
    expected = {key: value for key, value in original["tree"].items() if key != "children"}
    assert projected(original)["tree"] == {**expected, "childrenRef": "blocks"}
    original["tree"]["description"] += " Unique selection detail."
    expected["description"] = original["tree"]["description"]
    assert projected(original)["tree"] == {**expected, "childrenRef": "blocks"}


def test_directory_excludes_private_schema_and_redacts_secrets():
    original = large_directory()
    original["privateSchema"] = {"password": "private-schema-value"}
    original["blocks"][0]["description"] = "Use when authorized. api_key=fixture-secret"
    original["tree"]["children"] = copy.deepcopy(original["blocks"])
    result = projected(original)
    text = json.dumps(result)
    assert "privateSchema" not in text
    assert "fixture-secret" not in text
    assert "Use when authorized." in text


def test_complete_directory_reaches_actual_native_model_request(tmp_path):
    original = large_directory()
    gateway, model, _ = setup_gateway(tmp_path, [
        call("directory", "vrcforge_list_internal_tool_blocks", {}), finish,
    ])
    gateway.register_tool("vrcforge_list_internal_tool_blocks",
        "When to use: discover tools. When NOT to use: execute them.", "plan/preview",
        lambda _: copy.deepcopy(original))
    run(gateway, tmp_path)
    tool_message = next(message for message in model.requests[1]["messages"] if message["role"] == "tool")
    observation = json.loads(tool_message["content"])["observations"][0]["observation"]
    payload = json.loads(observation.split("; toolBlockDirectory=", 1)[1])
    assert payload["blocks"] == original["blocks"]
    assert payload["loadedBlocks"] == original["loadedBlocks"]
    assert payload["internalToolSelections"] == original["internalToolSelections"]
    assert "children" not in payload["tree"]


def test_complete_directory_reaches_legacy_final_prompt():
    original = large_directory()
    prompt = service()._build_llm_plan_prompt("Inspect", [], loop_state=[{
        "tool": "vrcforge_list_internal_tool_blocks", "status": "executed", "result": original,
    }])
    payload, _ = json.JSONDecoder().raw_decode(prompt.split("; toolBlockDirectory=", 1)[1])
    assert payload["blocks"] == original["blocks"]
    assert payload["loadedBlocks"] == original["loadedBlocks"]
    assert payload["internalToolSelections"] == original["internalToolSelections"]

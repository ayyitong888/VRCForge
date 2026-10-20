import json

from internal_tool_blocks import canonical_tool_tree
from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool, planner_tool_input_schema
from tests.test_native_runtime_gateway import call, run, setup_gateway
from tests.test_runtime_planner_service import FakeCatalog


def test_invalid_native_block_projects_frozen_legal_blocks_without_dispatch(tmp_path):
    invoked = []
    rejected = call("bad-block", "load_internal_tool_block", {"block": "unity", "tools": []})

    def inspect_rejection(request):
        loader = next(
            item["function"]
            for item in request["tools"]
            if item["function"]["name"] == "load_internal_tool_block"
        )
        frozen_enum = loader["parameters"]["properties"]["block"]["enum"]
        expected_enum = sorted({"core", *(row["id"] for row in canonical_tool_tree()["roots"])})
        assert frozen_enum == expected_enum

        tool_message = next(message for message in request["messages"] if message.get("role") == "tool")
        assert tool_message["tool_call_id"] == "bad-block"
        result = json.loads(tool_message["content"])
        observation = result["observations"][0]
        assert observation["legalBlocks"] == frozen_enum
        return {"role": "assistant", "content": "The requested block is unavailable."}

    gateway, model, _ = setup_gateway(tmp_path, [rejected, inspect_rejection])
    expected_enum = sorted({"core", *(row["id"] for row in canonical_tool_tree()["roots"])})
    loader = PlannerTool(
        "load_internal_tool_block",
        "Load.",
        "read",
        runtime_name="vrcforge_load_internal_tool_block",
        input_schema=planner_tool_input_schema(
            "vrcforge_load_internal_tool_block", tool_block_ids=expected_enum
        ),
    )
    read = PlannerTool(
        "read_text_file",
        "Read.",
        "read",
        runtime_name="vrcforge_read_text_file",
        block="files",
    )
    gateway.runtime_planner._catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=(loader, read), routable_tools=(loader, read)
        )
    )
    gateway.register_tool(
        "vrcforge_load_internal_tool_block",
        "When to use: load. When NOT to use: write.",
        "read/debug",
        lambda args: invoked.append(args) or {"ok": True},
    )

    run(gateway, tmp_path, maxAgenticTurns=2)

    assert not invoked
    assert len(model.requests) == 2

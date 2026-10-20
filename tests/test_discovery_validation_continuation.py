"""Native discovery validation continues only after a valid loader correction."""

import json

from runtime_planner_service import PlannerCatalogSnapshot
from tests.test_native_runtime_gateway import (
    budgeted_request_history,
    call,
    finish,
    run,
    setup_gateway,
)
from tests.test_native_tool_discovery_recovery import catalog_tools
from tests.test_runtime_planner_service import FakeCatalog


def _discovery_gateway(tmp_path, replies):
    gateway, model, invoked = setup_gateway(tmp_path, replies)
    tools = catalog_tools()
    gateway.runtime_planner._catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=tools,
            routable_tools=tools,
        )
    )
    loader_calls = []

    def load(args):
        loader_calls.append(dict(args))
        gateway.runtime_sessions.load_internal_tool_block_selected(
            args["sessionId"], args["block"], args["tools"]
        )
        return {
            "ok": True,
            "status": "loaded",
            "block": args["block"],
            "selectedTools": args["tools"],
            "exposureLayer": "planning",
        }

    gateway.register_tool(
        "vrcforge_load_internal_tool_block",
        "When to use: load. When NOT to use: writes.",
        "read/debug",
        load,
    )
    return gateway, model, invoked, loader_calls


def test_loader_wrong_tools_type_keeps_failure_id_and_recovers_before_finish(tmp_path):
    rejected = call(
        "loader-invalid-tools",
        "load_internal_tool_block",
        {"block": "files", "tools": "read_text_file"},
    )
    corrected = call(
        "loader-corrected",
        "load_internal_tool_block",
        {"block": "files", "tools": ["read_text_file"]},
    )
    gateway, model, invoked, loader_calls = _discovery_gateway(
        tmp_path, [rejected, corrected, finish]
    )

    result = run(gateway, tmp_path, maxAgenticTurns=4)

    assert result["plan"]["nextStep"] == "done", result["plan"]
    assert len(model.requests) == 3
    assert not invoked
    assert [item["tools"] for item in loader_calls] == [["read_text_file"]]

    history = budgeted_request_history(model.requests[1])
    failure = next(
        message
        for message in history
        if message.get("role") == "tool"
        and message.get("tool_call_id") == "loader-invalid-tools"
    )
    observations = json.loads(failure["content"])["observations"]
    assert observations[0]["outcome"]["status"] == "failed"


def test_loader_admission_failure_then_model_stop_fails_and_preserves_raw_stop(tmp_path):
    rejected = call(
        "loader-invalid-tools",
        "load_internal_tool_block",
        {"block": "files", "tools": "read_text_file"},
    )
    stop_text = "停止继续加载，当前请求无法安全完成。"
    gateway, model, invoked, loader_calls = _discovery_gateway(
        tmp_path, [rejected, {"role": "assistant", "content": stop_text}]
    )

    result = run(gateway, tmp_path, maxAgenticTurns=3)

    assert len(model.requests) == 2
    assert not invoked
    assert loader_calls == []
    assert result["plan"]["nextStep"] == "planner_failed", result["plan"]
    assert result["task"]["status"] in {"failed", "planner_failed"}

    conversation = next(iter(gateway._runtime_session_state._native_conversations.values()))
    raw_history = conversation["messages"]
    assert any(
        message.get("role") == "assistant"
        and message.get("content") == stop_text
        for message in raw_history
    )

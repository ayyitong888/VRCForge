from __future__ import annotations

import hashlib
import ntpath
import json

from agent_gateway import AgentGateway
from runtime_planner_service import RuntimePlannerService
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop
from tests.test_native_runtime_gateway import NativeModel, NativeTurn, finish
from tests.test_runtime_planner_service import FakeCatalog


def test_native_instructions_batch_independent_result_refs_with_exact_cursors():
    planner = RuntimePlannerService(
        catalog=FakeCatalog(),
        desktop=None,
    )

    request, _tools = planner._build_native_plan_request(
        [{"role": "user", "content": "Read two independent result pages."}],
        observe={},
        exposure_layer="planning",
        project_context_active=False,
        project_path="",
        internal_tool_blocks=None,
        global_instructions="",
        project_instructions="",
    )

    assert "all currently ready, independent read calls in one assistant response" in request["instructions"]
    assert "owner-supplied non-overlapping windows that share a resultRef" in request["instructions"]
    assert "copy nextRequest arguments, including jsonPointer, offset, and limit, exactly from the latest page" in request["instructions"]
    assert "Wait for an unread page only when nextOffset or a byte/window boundary depends on that page or is missing" in request["instructions"]
    assert "never guess a cursor or request an overlapping window" in request["instructions"]
    assert "at most one write per response" in request["instructions"]


def _multi_read_receipt():
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "read-alpha",
                "type": "function",
                "function": {
                    "name": "call_tool",
                    "arguments": json.dumps({"tool": "vrcforge_read_alpha", "arguments": {}}),
                },
            },
            {
                "id": "read-beta",
                "type": "function",
                "function": {
                    "name": "call_tool",
                    "arguments": json.dumps({"tool": "vrcforge_read_beta", "arguments": {}}),
                },
            },
        ],
    }


def test_one_native_response_runs_two_independent_reads_once_each(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    invoked = {"vrcforge_read_alpha": [], "vrcforge_read_beta": []}
    for name in invoked:
        gateway.register_tool(
            name,
            f"When to use: read {name}. When NOT to use: write data.",
            "plan/preview",
            lambda arguments, *, _name=name: invoked[_name].append(arguments) or {"name": _name},
        )
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)

    model = NativeModel([_multi_read_receipt(), finish])
    planner = RuntimePlannerService(
        catalog=_TestRuntimePlannerCatalog(gateway),
        desktop=_TestRuntimePlannerDesktop(gateway),
        model=model,
        turn=NativeTurn(),
    )
    gateway.bind_runtime_planner(planner)

    result = gateway.runtime_message({
        "message": "Read two independent records and report their names.",
        "sessionId": "two-independent-reads",
        "clientTurnId": "two-independent-reads-turn",
        "projectRoot": str(tmp_path),
        "maxAgenticTurns": 4,
    })

    assert result["ok"] is True
    assert len(model.requests) == 2
    assert invoked == {"vrcforge_read_alpha": [{}], "vrcforge_read_beta": [{}]}
    binding = hashlib.sha256(
        ("fixture-provider-model-route\n" + ntpath.normcase(ntpath.normpath(str(tmp_path)))).encode()
    ).hexdigest()
    conversation = gateway.runtime_sessions.native_conversation(
        "two-independent-reads",
        binding=binding,
    )
    assert conversation is not None
    multi_call_messages = [
        message for message in conversation["messages"]
        if message.get("role") == "assistant"
        and len(message.get("tool_calls") or []) == 2
    ]
    assert len(multi_call_messages) == 1

from unittest.mock import patch

import pytest

from agent_task_loop import AgentTaskLoop

TOOL = "vrcforge_read_tool_result"
TARGET = {"resultRef": "result_fixture", "jsonPointer": "/parameters", "offset": 6}


def record(loop, args, *, ok, tool=TOOL, kind="skill", correction=""):
    loop.require_action(kind=kind, tool=tool, arguments=args)
    return loop.record_action(kind=kind, tool=tool, arguments=args,
        raw_result={"ok": ok}, outcome={"status": "ok" if ok else "failed", "summary": "page" if ok else "bad page arguments"},
        correction_for_action_id=correction)


def finish(loop):
    return loop.gate_terminal({"planner": "llm", "nextStep": "done", "reply": "衣柜 is Int.",
        "completionClaim": {"satisfied": True, "evidenceActionIds": loop.completed_action_ids()}})


def test_successful_same_page_correction_supersedes_all_failed_attempts():
    loop = AgentTaskLoop("Read wardrobe parameter")
    first = record(loop, {**TARGET, "limit": 22}, ok=False)
    second = record(loop, {**TARGET, "limit": 20, "unexpected": True}, ok=False)
    final = record(loop, {**TARGET, "limit": 20}, ok=True, correction=first["actionId"])
    assert set(final["correctedActionIds"]) == {first["actionId"], second["actionId"]}
    gated = finish(loop)
    assert gated["nextStep"] == "done"
    for action in gated["task"]["actions"][:2]:
        assert action["status"] == "superseded"
        assert action["outcome"]["status"] == "failed"
        assert action["supersededBy"] == final["actionId"]


@pytest.mark.parametrize("change", [
    {"resultRef": "another_ref"}, {"jsonPointer": "/layers"}, {"jsonPointer": "parameters"},
    {"offset": 7}, {"jsonPointer": "/parameters/0"},
])
def test_other_page_or_invalid_pointer_cannot_erase_failure(change):
    loop = AgentTaskLoop("Read parameter")
    record(loop, {**TARGET, "limit": 22}, ok=False)
    record(loop, {**TARGET, **change, "limit": 20}, ok=True)
    assert finish(loop)["nextStep"] == "tool_failed"


@pytest.mark.parametrize("kind,tool", [("write", TOOL), ("skill", "vrcforge_scan_fx_animator")])
def test_matching_arguments_do_not_relax_other_tools_or_writes(kind, tool):
    loop = AgentTaskLoop("Read parameter")
    record(loop, {**TARGET, "limit": 22}, ok=False, kind=kind, tool=tool)
    record(loop, {**TARGET, "limit": 20}, ok=True, kind=kind, tool=tool)
    assert finish(loop)["nextStep"] == "tool_failed"


def test_failed_same_page_correction_remains_failed():
    loop = AgentTaskLoop("Read parameter")
    record(loop, {**TARGET, "limit": 22}, ok=False)
    record(loop, {**TARGET, "limit": 20}, ok=False)
    assert finish(loop)["nextStep"] == "tool_failed"


def test_omitted_root_defaults_match_explicit_root_and_keep_argument_snapshot():
    loop = AgentTaskLoop("Read root")
    arguments = {"resultRef": "result_fixture", "limit": 22}
    failed = record(loop, arguments, ok=False)
    arguments["resultRef"] = "mutated_after_execution"
    succeeded = record(loop, {"resultRef": "result_fixture", "jsonPointer": "", "offset": 0, "limit": 6}, ok=True)
    assert succeeded["correctedActionIds"] == [failed["actionId"]]
    assert finish(loop)["nextStep"] == "done"


def test_registered_gameobject_schema_rejects_root_path_via_real_planner_validator():
    from runtime_planner_service import validate_planner_tool_arguments
    from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS

    result = validate_planner_tool_arguments(
        UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_get_gameobject"],
        {"projectPath": "project", "gameObjectPath": "/"},
    )
    assert result["ok"] is False
    assert any(issue["code"] == "pattern" for issue in result["issues"])


def test_native_read_only_negative_observation_can_finish_without_hard_requirement():
    loop = AgentTaskLoop("inspect the selected object")
    action = loop.record_action(
        kind="skill", tool="vrcforge_get_gameobject",
        arguments={"projectPath": "project", "gameObjectPath": "Missing"},
        raw_result={"ok": False}, outcome={"status": "failed", "summary": "not found"},
        action_id="native-read-negative", native_read_observation=True,
    )
    assert action["status"] == "failed"
    assert "_nativeReadObservation" not in loop.planner_projection()["actions"][0]
    gated = loop.gate_terminal({"nextStep": "done", "reply": "The object was not found."})
    assert gated["nextStep"] == "completion_unverified"
    assert gated["task"]["status"] == "completion_unverified"
    assert gated["task"]["actions"][0]["status"] == "failed"


def test_native_read_observation_does_not_bypass_explicit_requirement():
    loop = AgentTaskLoop("read A and verify it")
    required = loop.require_action(
        kind="skill", tool="vrcforge_get_gameobject",
        arguments={"projectPath": "project", "gameObjectPath": "Required"},
    )
    loop.record_action(
        kind="skill", tool="vrcforge_get_gameobject",
        arguments={"projectPath": "project", "gameObjectPath": "Other"},
        raw_result={"ok": True}, outcome={"status": "ok", "summary": "other"},
        action_id="native-read-other", native_read_observation=True,
    )
    gated = loop.gate_terminal({"nextStep": "done", "reply": "done", "completionSatisfied": True})
    assert gated["nextStep"] == "completion_unverified"
    assert required["requirementId"] in gated["completionGate"]["requirementIds"]


def test_native_read_observation_with_failed_verification_still_blocks():
    loop = AgentTaskLoop("verify the readback")
    loop.record_action(
        kind="skill", tool="vrcforge_get_gameobject",
        arguments={"projectPath": "project", "gameObjectPath": "Object"},
        raw_result={"ok": False},
        outcome={"status": "failed", "summary": "verification failed", "verification": {"state": "failed", "checks": []}},
        action_id="native-read-verification-failed", native_read_observation=True,
    )
    gated = loop.gate_terminal({"nextStep": "done", "reply": "done"})
    assert gated["nextStep"] != "done"


def test_native_read_observation_marker_round_trips_private_approval_context():
    from agent_task_loop import approval_completion, approval_task_context

    loop = AgentTaskLoop("inspect after approval", session_id="native-read-session")
    loop.record_action(
        kind="skill", tool="vrcforge_get_gameobject",
        arguments={"projectPath": "project", "gameObjectPath": "Missing"},
        raw_result={"ok": False}, outcome={"status": "failed", "summary": "not found"},
        action_id="native-read-private", native_read_observation=True,
    )
    context = approval_task_context(
        loop.approval_seed(requested_tool="vrcforge_install_unity_core", requested_arguments={}),
        tool="vrcforge_install_unity_core", arguments={},
    )
    resumed = AgentTaskLoop.from_approval_context(
        context,
        approval_completion(context, raw_result={"ok": True}, outcome={"status": "ok", "summary": "done"}),
    )
    assert "_nativeReadObservation" not in resumed.planner_projection()["actions"][0]
    assert resumed.planner_projection()["actions"][0]["status"] == "failed"


def test_gateway_clears_both_recovered_page_failures_and_preserves_final():
    import test_agent_loop_p0 as fixtures
    fixture = fixtures.AgentLoopP0Tests("test_loaded_skill_policy_blocks_disallowed_tool_then_allows_real_evidence")
    fixture.setUp()
    gateway = fixture.gateway
    source = "vrcforge_test_recovery_source"
    gateway.register_tool(source, "Read fixture; do not write.", "read/debug", lambda _: {"parameters": [{"name": "衣柜", "type": "Int"}]})
    original_execute = gateway._runtime_skill_executor.execute
    final_states = []
    def execute(_self, tool, params, *args, **kwargs):
        if tool == TOOL and params.get("limit") in {2, 3}:
            return {"ok": False, "tool": TOOL, "status": "failed", "error": "bad page arguments",
                "outcome": {"status": "failed", "summary": "bad page arguments"}}
        return original_execute(tool, params, *args, **kwargs)
    def planner(*args, **kwargs):
        states = kwargs.get("loop_state") or []
        if not states:
            return {"planner": "llm", "skillNeeded": True, "skillTool": source, "skillParams": {}, "continueLoop": True, "nextStep": "call_skill"}
        if len(states) < 4:
            return {"planner": "llm", "skillNeeded": True, "skillTool": TOOL,
                "skillParams": {"resultRef": states[0]["resultRead"]["resultRef"], "jsonPointer": "/parameters", "offset": 0, "limit": len(states)+1},
                "correctionForActionId": states[1]["actionId"] if len(states)==3 else "",
                "continueLoop": True, "nextStep": "call_skill"}
        final_states.extend(states)
        return {"planner": "llm", "reply": "衣柜 is Int.", "nextStep": "done", "continueLoop": False,
            "completionClaim": {"satisfied": True, "evidenceActionIds": [s["actionId"] for s in states if s.get("status")=="executed"]}}
    try:
        with patch.object(type(gateway._runtime_skill_executor), "execute", autospec=True, side_effect=execute), patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=planner):
            result = gateway.runtime_message({"message": "Read wardrobe parameter", "session_id": "recovered-page"})
        assert result["plan"]["nextStep"] == "done"
        assert result["plan"]["reply"] == "衣柜 is Int."
        assert all(s["status"] == "superseded" for s in final_states[1:3])
        assert all(a["status"] == "superseded" for a in result["task"]["actions"][1:3])
    finally:
        gateway._tools.pop(source, None)
        fixture.tearDown()

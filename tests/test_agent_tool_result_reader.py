import json
from unittest.mock import patch

import pytest

from runtime_planner_service import RuntimePlannerService
from runtime_planner_service import sanitize_planner_observation_text as sanitize
from agent_tool_result_reader import (
    TOOL_NAME, bind_tool_result_context, read_tool_result, result_continuation, result_reference,
)


def retained(result, *, tool="vrcforge_scan_fx_animator", index=0, action="same-action"):
    step = {"index": index, "actionId": action, "tool": tool, "status": "executed", "result": result}
    step["resultRead"] = result_continuation("session", "turn", "project", step, sanitize)
    return step


def read(step, **params):
    return read_tool_result({"resultRef": step["resultRead"]["resultRef"], **params}, sanitize=sanitize)


def scan_result():
    return {"parameters": [{"name": f"Parameter{i}", "type": "Bool"} for i in range(27)] + [
        {"name": "衣柜", "type": "Int", "default_int": 0, "used_by_condition": True}],
        "layers": [{"name": "Wardrobe", "states": [{"name": "Rest"}]}]}


def test_real_gateway_issues_current_turn_ref_and_reader_recovers_tail_parameter():
    import test_agent_loop_p0 as loop_tests
    fixture = loop_tests.AgentLoopP0Tests("test_loaded_skill_policy_blocks_disallowed_tool_then_allows_real_evidence")
    fixture.setUp()
    gateway = fixture.gateway
    name = "vrcforge_test_rich_scan"
    gateway.register_tool(name, "When to use: inspect fixture. When NOT to use: writes.", "read/debug", lambda _: scan_result())
    seen = []

    def planner(*args, **kwargs):
        state = kwargs.get("loop_state") or []
        if not state:
            return {"planner": "llm", "skillNeeded": True, "skillTool": name, "skillParams": {}, "continueLoop": True, "nextStep": "call_skill"}
        seen.append(state[-1])
        if len(state) == 1:
            observation = gateway.runtime_planner._llm_loop_step_observation(state[-1])
            assert "resultContinuation=" in observation
            continuation = state[-1]["resultRead"]
            assert any(row["jsonPointer"] == "/parameters" and row["count"] == 28 for row in continuation["collections"])
            return {"planner": "llm", "skillNeeded": True, "skillTool": "vrcforge_read_tool_result",
                    "skillParams": {"resultRef": continuation["resultRef"], "jsonPointer": "/parameters", "offset": 26, "limit": 1},
                    "continueLoop": True, "nextStep": "call_skill"}
        if len(state) == 2:
            request = state[-1]["result"]["nextRequest"]
            assert request["arguments"]["jsonPointer"] == "/parameters"
            assert request["arguments"]["offset"] == 27
            return {"planner": "llm", "skillNeeded": True, "skillTool": request["tool"],
                    "skillParams": request["arguments"], "continueLoop": True, "nextStep": "call_skill"}
        assert state[-1]["result"]["items"][0]["value"] == scan_result()["parameters"][27]
        observation = gateway.runtime_planner._llm_loop_step_observation(state[-1])
        assert "衣柜" in observation and '"type":"Int"' in observation
        return {"planner": "llm", "reply": "衣柜 Int", "nextStep": "done",
                "completionClaim": {"satisfied": True, "evidenceActionIds": [step["actionId"] for step in state]}}

    try:
        with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=planner):
            response = gateway.runtime_message({"message": "Inspect the supplied parameter result", "session_id": "reader-test"})
        assert response["plan"]["nextStep"] == "done"
        assert [step["tool"] for step in response["steps"]] == [name, TOOL_NAME, TOOL_NAME]
        assert len(seen) == 3
    finally:
        gateway._tools.pop(name, None)
        fixture.tearDown()


def test_reader_is_fresh_core_read_in_both_contexts_and_layers():
    import dashboard_server
    for project in (False, True):
        for layer in ("planning", "execution"):
            catalog = dashboard_server._RuntimePlannerCatalog().read(layer, project_context_active=project)
            tool = next(item for item in catalog.visible_tools if item.runtime_name == TOOL_NAME)
            assert tool.block == "core" and not tool.write
            assert set(tool.input_schema["properties"]) - {"promptSkillProvenance"} == {"resultRef", "jsonPointer", "offset", "limit"}
            prompt = dashboard_server.AGENT_GATEWAY.runtime_planner._build_llm_plan_prompt(
                "Read omitted data", [], exposure_layer=layer, project_context_active=project, internal_tool_blocks=["core"])
            assert "- read_tool_result" in prompt


def test_occurrence_refs_are_scoped_and_context_expires_on_error():
    first = retained({"value": 1})
    second = retained({"value": 2}, index=1)
    assert first["resultRead"]["resultRef"] != second["resultRead"]["resultRef"]
    with bind_tool_result_context("session", "turn", "project", [first, second]):
        assert read(first)["items"][0]["value"] == 1
        assert read(second)["items"][0]["value"] == 2
        with pytest.raises(PermissionError):
            read_tool_result({"resultRef": "forged"}, sanitize=sanitize)
    for scope in [("other", "turn", "project"), ("session", "later", "project"), ("session", "turn", "other")]:
        with bind_tool_result_context(*scope, [first]):
            with pytest.raises(PermissionError):
                read(first)
    with pytest.raises(RuntimeError):
        with bind_tool_result_context("session", "turn", "project", [first]):
            raise RuntimeError("cancel/error")
    with pytest.raises(PermissionError):
        read(first)


@pytest.mark.parametrize("tool", ["vrcforge_get_compile_errors", "vrcforge_capture_screenshot",
                                  "vrcforge_agent_desktop_action", "vrcforge_capture_multi_screenshot", "vrcforge_read_recent_logs",
                                  "vrcforge_list_memory", "vrcforge_read_text_file", TOOL_NAME])
def test_restricted_channel_cannot_be_read_even_with_forged_reference(tool):
    step = retained({"privateDump": "must not leak", "result": {"rawResult": "hidden"}}, tool=tool)
    assert step["resultRead"] == {}
    step["resultRead"] = {"resultRef": result_reference("session", "turn", "project", step)}
    with bind_tool_result_context("session", "turn", "project", [step]):
        with pytest.raises(PermissionError):
            read(step)


@pytest.mark.parametrize("pointer", ["/control_token", "/privateDump/public", "/rawResult/public", "/stdout", "/headers/Authorization"])
def test_pointer_selection_cannot_bypass_sensitive_ancestors(pointer):
    step = retained({"control_token": "control-sentinel", "privateDump": {"public": "secret"},
                     "rawResult": {"public": "secret"}, "stdout": "secret",
                     "headers": {"Authorization": "secret"}, "public": "visible"})
    with bind_tool_result_context("session", "turn", "project", [step]):
        with pytest.raises(PermissionError):
            read(step, jsonPointer=pointer)
        root = read(step)
    assert [row["value"] for row in root["items"]] == ["visible"]
    assert "secret" not in json.dumps(root) and "control-sentinel" not in json.dumps(root)


def test_bounded_pages_advance_without_dropping_rows_and_nested_target_is_exact():
    long_name = "state_" + "x" * 600
    step = retained({"rows": [{"name": f"Item{i}", "description": "x" * 3000} for i in range(28)],
                     "layers": [{"states": [{"name": long_name}] * 28}]})
    with bind_tool_result_context("session", "turn", "project", [step]):
        offset = 0
        pointers = []
        while True:
            page = read(step, jsonPointer="/rows", offset=offset, limit=20)
            assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= 6000
            pointers.extend(row["jsonPointer"] for row in page["items"])
            if not page["hasMore"]:
                break
            next_offset = page["nextRequest"]["arguments"]["offset"]
            assert next_offset > offset
            offset = next_offset
        tail = read(step, jsonPointer="/layers/0/states", offset=27)
        name = tail["items"][0].get("value", {}).get("name")
        assert name is None or name == long_name
        exact = read(step, jsonPointer="/layers/0/states/27/name")
        assert exact["items"][0].get("value") == long_name
    assert pointers == [f"/rows/{i}" for i in range(28)]


def test_json_pointer_escaping_and_no_owner_argument_override():
    step = retained({"a/b~c": [{"name": "Exact"}]})
    with bind_tool_result_context("session", "turn", "project", [step]):
        assert read(step, jsonPointer="/a~1b~0c")["items"][0]["value"] == {"name": "Exact"}
        with pytest.raises(ValueError):
            read(step, projectPath="other")
        for pointer in ("/a~2b", "relative", "/a~1b~0c/01", "/a~1b~0c/8"):
            with pytest.raises((ValueError, PermissionError)):
                read(step, jsonPointer=pointer)


def test_live_reader_schema_exposes_executable_bounds_and_rejects_r2_mistakes():
    import dashboard_server
    from runtime_planner_service import validate_planner_tool_arguments
    from unity_tool_schema_projection import canonical_unity_read_tool_input_schema

    schema = canonical_unity_read_tool_input_schema(TOOL_NAME)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["resultRef"]
    assert schema["properties"]["limit"]["minimum"] == 1
    assert schema["properties"]["limit"]["maximum"] == 20
    assert schema["properties"]["offset"]["minimum"] == 0
    assert "RFC6901" in schema["properties"]["jsonPointer"]["description"]
    assert "~1" in schema["properties"]["jsonPointer"]["description"]
    catalog = dashboard_server._RuntimePlannerCatalog().read("planning", project_context_active=True)
    actual = next(tool for tool in catalog.visible_tools if tool.runtime_name == TOOL_NAME)
    valid = {"resultRef": "result_" + "a" * 32, "jsonPointer": "/parameters", "offset": 6, "limit": 20}
    assert validate_planner_tool_arguments(actual.input_schema, valid)["ok"]
    for wrong in ({**valid, "limit": 28}, {**valid, "query": "衣柜"}, {**valid, "jsonPointer": "parameters"}):
        assert not validate_planner_tool_arguments(actual.input_schema, wrong)["ok"]


def test_reader_next_request_survives_real_gateway_redaction_and_observation():
    from agent_gateway import redact_sensitive

    step = retained({"parameters": scan_result()["parameters"], "layers": [{"states": [1, 2, 3]}]})
    with bind_tool_result_context("session", "turn", "project", [step]):
        for pointer in ("/parameters", "/layers/0/states"):
            first = read(step, jsonPointer=pointer, limit=1)
            persisted = redact_sensitive(first)
            assert persisted["nextRequest"]["arguments"]["jsonPointer"] == pointer
            following = read_tool_result(persisted["nextRequest"]["arguments"], sanitize=sanitize)
            assert following["offset"] == 1
            observation = RuntimePlannerService._llm_loop_step_observation(
                None, {"tool": TOOL_NAME, "status": "executed", "result": persisted})
            assert json.dumps(persisted["nextRequest"], ensure_ascii=False, separators=(",", ":")) in observation


def test_page_marker_cannot_bypass_redaction_for_arbitrary_arguments():
    from copy import deepcopy
    from agent_gateway import redact_sensitive
    from agent_tool_result_reader import page_next_request_arguments

    step = retained(scan_result())
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = read(step, jsonPointer="/parameters", limit=1)
    for key, value in (("authorization", "Bearer sentinel"), ("path", "C:/private/host.txt"),
                       ("jsonPointer", "/other"), ("resultRef", "invented"), ("limit", 21)):
        forged = deepcopy(page)
        forged["nextRequest"]["arguments"][key] = value
        assert page_next_request_arguments(forged) is None
        redacted = redact_sensitive(forged)
        assert redacted["nextRequest"]["arguments"]["jsonPointer"] != forged["nextRequest"]["arguments"]["jsonPointer"]
        assert "sentinel" not in json.dumps(redacted) and "C:/private" not in json.dumps(redacted)

"""Exercise the shared model information exit through the real gateway loop."""
from unittest.mock import patch

from agent_tool_result_reader import TOOL_NAME
from test_agent_loop_p0 import AgentLoopP0Tests as _Fixture

_Fixture.__test__ = False


def test_gateway_keeps_complete_projection_available_through_scoped_reader():
    fixture = _Fixture("test_loaded_skill_policy_blocks_disallowed_tool_then_allows_real_evidence")
    fixture.setUp()
    gateway = fixture.gateway
    name = "vrcforge_test_model_information"
    tail = "COMPLETE_MODEL_INFORMATION_TAIL"
    result = {"ok": True, "description": "long result " * 2500 + tail}
    gateway.register_tool(name, "When to use: inspect supplied data. When NOT to use: writes.",
                          "read/debug", lambda _: result)
    pages = []
    original = []

    def plan(*args, **kwargs):
        state = kwargs.get("loop_state") or []
        if not state:
            request = {"tool": name, "arguments": {}}
        elif len(state) == 1:
            payload = state[-1]["modelInformation"]["payload"]["text"]
            assert tail in payload
            original.append(payload)
            request = state[-1]["modelInformationRead"]["nextRequest"]
            request = {**request, "arguments": {**request["arguments"], "jsonPointer": "/text"}}
        else:
            page = state[-1]["result"]
            pages.append(page["items"][0]["value"])
            if not page["hasMore"]:
                assert "".join(pages) == original[0]
                return {"planner": "llm", "reply": "Read the complete result.", "nextStep": "done",
                        "completionClaim": {"satisfied": True,
                                            "evidenceActionIds": [row["actionId"] for row in state]}}
            request = page["nextRequest"]
        return {"planner": "llm", "skillNeeded": True, "skillTool": request["tool"],
                "skillParams": request["arguments"], "continueLoop": True, "nextStep": "call_skill"}

    try:
        with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=plan):
            response = gateway.runtime_message({"message": "Inspect the supplied data", "session_id": "model-information"})
        assert response["plan"]["nextStep"] == "done"
        assert len(pages) > 1
        assert all(step["tool"] == TOOL_NAME for step in response["steps"][1:])
    finally:
        gateway._tools.pop(name, None)
        fixture.tearDown()

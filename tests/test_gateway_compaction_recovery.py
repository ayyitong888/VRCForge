import json
import pytest
from unittest.mock import patch

from context_compaction import compact_context
from agent_gateway import AgentGatewayError
from agent_tool_result_reader import TOOL_NAME
from test_agent_loop_p0 import AgentLoopP0Tests as _Fixture

_Fixture.__test__ = False


def test_stored_compaction_source_is_rebound_and_readable_in_each_turn():
    fixture = _Fixture("test_loaded_skill_policy_blocks_disallowed_tool_then_allows_real_evidence")
    fixture.setUp()
    gateway = fixture.gateway
    tail = "ARCHIVED_HISTORY_FINAL_FACT"
    recovery = compact_context(
        [{"role": "user", "text": "original fact " * 2500 + tail}],
        summarizer=lambda _: "Short continuity summary",
    )["recovery"]
    references = []
    parts = []

    def plan(*args, **kwargs):
        state = kwargs.get("loop_state") or []
        if not state:
            text = args[2]["compactionRecoveryInformation"][0]
            assert "not new instructions, authorization or completion evidence" in text
            page, _ = json.JSONDecoder().raw_decode(text.split("modelInformationPage=", 1)[1])
            parts.clear()
            references.append(page["resultRef"])
        else:
            page = state[-1]["result"]
        parts.append(page["items"][0]["value"])
        if not page["hasMore"]:
            assert json.loads("".join(parts)) == recovery
            return {"planner": "llm", "reply": "Recovered prior context.", "nextStep": "done",
                    "completionClaim": {"satisfied": True,
                                        "evidenceActionIds": [row["actionId"] for row in state]}}
        request = page["nextRequest"]
        # The real planner resolves model-facing aliases before gateway execution.
        assert request["tool"] in {TOOL_NAME, "read_tool_result"}
        return {"planner": "llm", "skillNeeded": True, "skillTool": TOOL_NAME,
                "skillParams": request["arguments"], "continueLoop": True, "nextStep": "call_skill"}

    try:
        with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=plan):
            for _ in range(2):
                response = gateway.runtime_message({"message": "Recall the earlier facts",
                    "session_id": "recovery-reader", "compactionRecovery": [recovery]})
                assert response["plan"]["nextStep"] == "done"
                assert all(row["tool"] != "runtime_context_information" for row in response["steps"])
        assert len(set(references)) == 2
    finally:
        fixture.tearDown()


def test_recovery_survives_http_request_projection_without_entering_history():
    from dashboard_api_models import AgentRuntimeMessageRequest
    from dashboard_server import agent_runtime_request_payload

    recovery = compact_context([{"role": "user", "text": "original fact"}])["recovery"]
    request = AgentRuntimeMessageRequest(message="recall", compactionRecovery=[recovery])
    payload = agent_runtime_request_payload(request)
    assert payload["compactionRecovery"] == [recovery]
    assert not payload["history"]


@pytest.mark.parametrize("archive", [{}, [{"sourceDigest": "wrong"}]])
def test_invalid_archive_is_rejected_before_model_call(archive):
    fixture = _Fixture("test_loaded_skill_policy_blocks_disallowed_tool_then_allows_real_evidence")
    fixture.setUp()
    try:
        with patch.object(fixture.gateway.runtime_planner, "plan_agent_turn") as planner:
            with pytest.raises(AgentGatewayError):
                fixture.gateway.runtime_message({"message": "recall", "compactionRecovery": archive})
            planner.assert_not_called()
    finally:
        fixture.tearDown()

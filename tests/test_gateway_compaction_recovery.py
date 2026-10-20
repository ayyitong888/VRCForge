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
    archive_parts = []
    phase = {"value": "catalog"}

    def plan(*args, **kwargs):
        state = kwargs.get("loop_state") or []
        if not state:
            text = args[2]["compactionRecoveryInformation"][0]
            manifest = json.loads(text.split("catalog (data):\n", 1)[1])
            assert "ARCHIVED_HISTORY_FINAL_FACT" not in text
            assert manifest["archiveCount"] == 1
            phase["value"] = "catalog"
            request = manifest["reader"]["nextRequest"]
        else:
            page = state[-1]["result"]
            if phase["value"] == "catalog":
                archive_item = next(item for item in page.get("items", [])
                                    if isinstance(item, dict) and item.get("jsonPointer") == "/archives")
                entries = archive_item["value"]
                assert entries and entries[0]["sourceEntryCount"] == len(recovery["sourceEntries"])
                archive_reader = entries[0]["reader"]
                references.append(archive_reader["resultRef"])
                phase["value"] = "archive"
                archive_parts.clear()
                request = {"tool": TOOL_NAME, "arguments": {
                    "resultRef": archive_reader["resultRef"], "source": "owner_model",
                    "jsonPointer": "/text", "offset": 0, "limit": 6,
                }}
            else:
                entry_items = [item for item in page.get("items", [])
                               if isinstance(item, dict) and item.get("jsonPointer") == "/text"]
                archive_parts.extend(item["value"] for item in entry_items)
                if not page.get("hasMore"):
                    assert json.loads("".join(archive_parts)) == recovery
                    return {"planner": "llm", "reply": "Recovered prior context.", "nextStep": "done",
                            "completionClaim": {"satisfied": True, "evidenceActionIds": []}}
                request = page["nextRequest"]
        assert request["tool"] in {TOOL_NAME, "read_tool_result"}
        return {"planner": "llm", "skillNeeded": True, "skillTool": TOOL_NAME,
                "skillParams": request["arguments"], "continueLoop": True, "nextStep": "call_skill"}

    try:
        with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=plan):
            for _ in range(2):
                response = gateway.runtime_message({"message": "Recall the earlier facts",
                    "session_id": "recovery-reader", "compactionRecovery": [recovery]})
                assert response["plan"]["nextStep"] == "done"
                assert all(row["tool"] != "runtime_context_information" for row in response.get("steps", []))
        assert len(set(references)) == 2
    finally:
        fixture.tearDown()


def test_gateway_state_has_one_bounded_catalog_and_all_twenty_entries_are_pageable():
    fixture = _Fixture("test_loaded_skill_policy_blocks_disallowed_tool_then_allows_real_evidence")
    fixture.setUp()
    gateway = fixture.gateway
    recoveries = [compact_context([{"role": "user", "text": f"archive-{i}"}], summarizer=lambda _: f"summary-{i}")["recovery"] for i in range(20)]
    catalog_entries = []
    state_lengths = []

    def plan(*args, **kwargs):
        state = kwargs.get("loop_state") or []
        if not state:
            text = args[2]["compactionRecoveryInformation"][0]
            state_lengths.append(len(text))
            assert "archive-0" not in text
            manifest = json.loads(text.split("catalog (data):\n", 1)[1])
            request = manifest["reader"]["nextRequest"]
            request["arguments"]["limit"] = 3
        else:
            page = state[-1]["result"]
            for item in page.get("items", []):
                if not isinstance(item, dict):
                    continue
                pointer = str(item.get("jsonPointer", ""))
                if pointer == "/archives" and isinstance(item.get("value"), list):
                    catalog_entries.extend(item["value"])
                elif pointer.startswith("/archives/"):
                    catalog_entries.append(item["value"])
            if not page.get("hasMore"):
                assert len(catalog_entries) == 20
                return {"planner": "llm", "reply": "catalog read", "nextStep": "done",
                        "completionClaim": {"satisfied": True, "evidenceActionIds": []}}
            request = page["nextRequest"]
        assert request["tool"] in {TOOL_NAME, "read_tool_result"}
        return {"planner": "llm", "skillNeeded": True, "skillTool": TOOL_NAME,
                "skillParams": request["arguments"], "continueLoop": True, "nextStep": "call_skill"}

    try:
        with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=plan):
            response = gateway.runtime_message({"message": "Read the recovery catalog", "session_id": "catalog-reader",
                                                "compactionRecovery": recoveries})
            assert response["plan"]["nextStep"] == "done"
        assert state_lengths and state_lengths[0] < 5000
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

"""P1 regression coverage for truthful unknown wardrobe control state."""
from __future__ import annotations

import copy
import json

from external_mcp_result_projection import project_result
from runtime_planner_service import RuntimePlannerService, sanitize_planner_observation_text
from agent_tool_result_reader import bind_tool_result_context, read_tool_result, result_continuation
from wardrobe_outfit_workflow_service import WardrobeArtifactReadPorts, WardrobeArtifactReadService

STATE_FIELDS = (
    "writeDefaults", "isStripOrDefaultCandidate", "onObjects", "offObjects",
    "clipPath", "fxStateName", "fxStatePath", "fxTransitionType", "fxSourceStateName",
)


def scan(snapshot):
    return WardrobeArtifactReadService(WardrobeArtifactReadPorts(
        scan_avatar_items=lambda _: {}, scan_avatar_controls=lambda _: {},
        scan_wardrobe=lambda _: snapshot,
    )).scan_wardrobe({"avatarPath": "Scene/Avatar"})


def test_ambiguous_state_nulls_only_existing_derived_fields_and_keeps_source():
    control = {
        "value": 1,
        "writeDefaults": False,
        "isStripOrDefaultCandidate": False,
        "onObjects": [], "offObjects": ["Avatar/Coat"], "clipPath": "",
        "fxStateName": "", "fxStatePath": "", "fxTransitionType": "state",
        "fxSourceStateName": "Default", "fxCandidates": [{"fxStateName": "A"}, {"fxStateName": "B"}],
        "unrelated": "keep",
    }
    snapshot = {"ok": True, "wardrobes": [{"parameterName": "衣柜", "animatorEvidence": {"ambiguousDestinationValues": [1]}, "controls": [control]}]}
    before = copy.deepcopy(snapshot)
    result = scan(snapshot)
    actual = result["wardrobes"][0]["controls"][0]
    assert actual["resolutionStatus"] == "ambiguous"
    assert all(actual[key] is None for key in STATE_FIELDS)
    assert actual["fxCandidates"] == control["fxCandidates"]
    assert actual["unrelated"] == "keep"
    assert snapshot == before


def test_unresolved_nulls_present_fields_without_inventing_missing_fields():
    control = {"value": 7, "writeDefaults": False, "onObjects": [], "offObjects": [], "fxCandidates": []}
    result = scan({"ok": True, "wardrobes": [{"parameterName": "衣柜", "controls": [control]}]})
    actual = result["wardrobes"][0]["controls"][0]
    assert actual["resolutionStatus"] == "unresolved"
    assert actual["writeDefaults"] is None and actual["onObjects"] is None and actual["offObjects"] is None
    assert "clipPath" not in actual and "fxStateName" not in actual and "fxSourceStateName" not in actual


def test_resolved_false_empty_values_and_same_state_candidates_remain_facts():
    control = {
        "value": 2, "writeDefaults": False, "isStripOrDefaultCandidate": False,
        "onObjects": [], "offObjects": [], "clipPath": "", "fxStateName": "Outfit",
        "fxStatePath": "Layers/Wardrobe/Outfit", "fxTransitionType": "state",
        "fxSourceStateName": "Default", "fxCandidates": [{"fxStateName": "Outfit"}, {"fxStateName": "Outfit"}],
    }
    actual = scan({"ok": True, "wardrobes": [{"parameterName": "衣柜", "controls": [control]}]})["wardrobes"][0]["controls"][0]
    assert actual["resolutionStatus"] == "resolved"
    assert actual["writeDefaults"] is False and actual["isStripOrDefaultCandidate"] is False
    assert actual["onObjects"] == [] and actual["offObjects"] == [] and actual["clipPath"] == ""
    assert len(actual["fxCandidates"]) == 2


def test_projected_compact_result_preserves_unknown_nulls():
    result = scan({"ok": True, "wardrobes": [{"parameterName": "衣柜", "animatorEvidence": {"ambiguousDestinationValues": [1]}, "controls": [{"value": 1, "writeDefaults": False, "onObjects": []}]}]})
    compact = project_result({"ok": True, "result": result}, mode="compact", resource_readable=True)
    projected = compact["result"]["wardrobes"][0]["controls"][0]
    assert projected["writeDefaults"] is None and projected["onObjects"] is None


def test_native_structured_evidence_preserves_unknown_nulls():
    result = {"ok": True, "wardrobes": [{"controls": [{"writeDefaults": None, "onObjects": None,
        "clipPath": None, "resolutionStatus": "ambiguous"}]}]}
    planner = RuntimePlannerService.__new__(RuntimePlannerService)
    observation = planner.native_result_observation({
        "tool": "vrcforge_scan_wardrobe", "kind": "skill", "status": "executed",
        "result": result, "outcome": {"status": "ok"},
    })["observation"]
    evidence = json.loads(observation.split("structuredEvidence=", 1)[1])
    control = evidence["data"]["wardrobes"][0]["controls"][0]
    assert control["writeDefaults"] is None and control["onObjects"] is None and control["clipPath"] is None


def test_retained_pointer_page_preserves_unknown_null_after_sanitization():
    result = {"ok": True, "wardrobes": [{"controls": [{"writeDefaults": None,
        "onObjects": None, "resolutionStatus": "ambiguous"}]}]}
    step = {"index": 0, "actionId": "wardrobe-read", "tool": "vrcforge_scan_wardrobe",
            "status": "executed", "result": result}
    step["resultRead"] = result_continuation("session", "turn", "project", step,
                                               sanitize_planner_observation_text)
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = read_tool_result({"resultRef": step["resultRead"]["resultRef"],
                                 "jsonPointer": "/wardrobes/0/controls/0/writeDefaults"},
                                sanitize=sanitize_planner_observation_text)
    assert page["items"][0]["value"] is None

"""A strict scanner result must not be mistaken for general wardrobe absence."""
import copy
import ast
from pathlib import Path

from external_mcp_result_projection import project_result
from wardrobe_outfit_workflow_service import WardrobeArtifactReadPorts, WardrobeArtifactReadService


def scan(snapshot):
    return WardrobeArtifactReadService(WardrobeArtifactReadPorts(
        scan_avatar_items=lambda _: {}, scan_avatar_controls=lambda _: {},
        scan_wardrobe=lambda _: snapshot,
    )).scan_wardrobe({"avatarPath": "Scene/Avatar"})


def test_registered_scan_description_covers_both_transition_sources():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "dashboard_server.py").read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "register_tool"
             and node.args and isinstance(node.args[0], ast.Constant)
             and node.args[0].value == "vrcforge_scan_wardrobe"]
    assert len(calls) == 1
    description = ast.literal_eval(calls[0].args[1])
    assert "AnyState and ordinary-state Equals transitions" in description
    assert "When to use:" in description and "When NOT to use:" in description
    assert "Negative example:" in description
    assert "ambiguous" in description


def test_unmatched_ordinary_state_wardrobe_remains_available_for_agent_analysis():
    snapshot = {
        "ok": True, "wardrobeCount": 1,
        "wardrobes": [{"parameterName": "DPS"}],
        "wardrobeCandidates": [],
        "looseControls": [{"parameterName": "Clothes", "rejectionReasons": ["missing FX Any-State Equals binding for this int parameter"]}],
    }
    before = copy.deepcopy(snapshot)
    result = scan(snapshot)
    assert snapshot == before
    assert result["wardrobes"] == before["wardrobes"]
    assert result["looseControls"] == before["looseControls"]
    coverage = result["recognitionCoverage"]
    assert coverage["generalTopologyComplete"] is False
    assert coverage["missingMatchProvesAbsence"] is False
    assert coverage["analysisRequired"] is True
    assert coverage["candidateParameters"] == ["DPS", "Clothes"]
    assert "vrcforge_scan_fx_animator" in coverage["nextReadTools"]
    assert "vrcforge_scan_animation_bindings" in coverage["nextReadTools"]


def test_empty_int_scan_cannot_rule_out_bool_or_blendtree_clothing():
    result = scan({"ok": True, "wardrobeCount": 0, "wardrobes": [], "looseControls": []})
    coverage = result["recognitionCoverage"]
    assert coverage["generalTopologyComplete"] is False
    assert coverage["candidateEnumerationComplete"] is False
    assert coverage["candidateParameters"] == []
    assert "vrcforge_scan_parameters" in coverage["nextReadTools"]


def test_ordinary_transition_scan_does_not_advertise_any_state_only_pattern():
    from runtime_planner_service import RuntimePlannerService

    snapshot = {"ok": True, "wardrobes": [], "wardrobeCandidates": [{
        "parameterName": "Clothes", "animatorEvidence": {
            "anyStateTransitionCount": 0, "ordinaryTransitionCount": 451,
            "hasAmbiguousDestinations": True,
        },
    }]}
    before = copy.deepcopy(snapshot)
    result = scan(snapshot)
    assert result["recognitionCoverage"]["automaticPattern"] == "int_menu_equals_transition_object_activation"
    assert result["wardrobeCandidates"] == before["wardrobeCandidates"]
    assert snapshot == before
    text = RuntimePlannerService._llm_loop_step_observation(None, {
        "tool": "vrcforge_scan_wardrobe", "status": "executed", "result": result,
    }, native_contract=True)
    assert "int_menu_equals_transition_object_activation" in text
    assert "int_menu_any_state_equals_object_activation" not in text


def test_candidate_semantics_distinguish_value_matches_from_playback_sequences():
    snapshot = {"ok": True, "wardrobes": [], "wardrobeCandidates": [{
        "parameterName": "Clothes", "controls": [{"value": 5, "fxCandidates": [
            {"fxSourceStateName": "A", "fxStateName": "B", "conditionCount": 2},
            {"fxSourceStateName": "C", "fxStateName": "D", "conditionCount": 1},
        ]}]}]}
    before = copy.deepcopy(snapshot)
    result = scan(snapshot)
    semantics = result["recognitionCoverage"]["candidateSemantics"]
    assert semantics["ordering"] == "unordered_matches_not_playback_sequence"
    assert semantics["grouping"] == "parent_control_value_equals_parameter"
    assert semantics["completeTransitionConditionsIncluded"] is False
    assert semantics["clipAssociation"] == "destination_state_motion_not_transition_duration"
    assert result["wardrobeCandidates"][0]["controls"][0]["fxCandidates"] == before["wardrobeCandidates"][0]["controls"][0]["fxCandidates"]
    assert snapshot == before
    compact = project_result({"ok": True, "result": result}, mode="compact", resource_readable=True)
    assert compact["result"]["recognitionCoverage"]["candidateSemantics"] == semantics


def test_candidate_semantics_reach_native_model_observation():
    from runtime_planner_service import RuntimePlannerService

    result = scan({"ok": True, "wardrobes": [], "wardrobeCandidates": [{
        "parameterName": "Clothes", "controls": [{"value": 5, "fxCandidates": [
            {"fxSourceStateName": "A", "fxStateName": "B"},
            {"fxSourceStateName": "C", "fxStateName": "D"},
        ]}],
    }]})
    text = RuntimePlannerService._llm_loop_step_observation(None, {
        "tool": "vrcforge_scan_wardrobe", "status": "executed", "result": result,
    }, native_contract=True)
    assert "unordered_matches_not_playback_sequence" in text
    assert "destination_state_motion_not_transition_duration" in text


def test_failed_scan_and_other_read_shapes_are_preserved():
    for value in ({"ok": False, "error": "Disconnected", "wardrobes": []}, {"source": "vrc_scan_wardrobe"}):
        assert scan(value) == value


def test_candidate_counts_report_filter_scope_without_changing_source_counts():
    snapshot = {"ok": True, "wardrobes": [], "wardrobeCandidates": [{
        "parameterName": "Selector", "animatorEvidence": {
            "fxStateCount": 7, "fxTransitionCount": 9,
        },
    }]}
    before = copy.deepcopy(snapshot)
    result = scan(snapshot)
    scope = result["recognitionCoverage"]["candidateSemantics"]["animatorEvidenceScope"]
    assert scope["fxTransitionCount"] == "equals_matches_for_this_parameter_in_first_matching_layer"
    assert scope["fxStateCount"] == "distinct_resolved_destination_states_of_those_matches"
    assert scope["isWholeLayerInventory"] is False
    assert result["wardrobeCandidates"] == before["wardrobeCandidates"]
    assert snapshot == before
    compact = project_result({"ok": True, "result": result}, mode="compact", resource_readable=True)
    assert compact["result"]["recognitionCoverage"]["candidateSemantics"]["animatorEvidenceScope"] == scope


def test_candidate_count_scope_reaches_native_model_without_fixture_answers():
    from runtime_planner_service import RuntimePlannerService
    result = scan({"ok": True, "wardrobes": [], "wardrobeCandidates": []})
    text = RuntimePlannerService._llm_loop_step_observation(None, {
        "tool": "vrcforge_scan_wardrobe", "status": "executed", "result": result,
    }, native_contract=True)
    assert "equals_matches_for_this_parameter_in_first_matching_layer" in text
    assert "distinct_resolved_destination_states_of_those_matches" in text


def test_scanner_supplied_coverage_is_not_overwritten():
    supplied = {"generalTopologyComplete": False, "source": "current-core"}
    result = scan({"ok": True, "wardrobes": [], "recognitionCoverage": supplied})
    assert result["recognitionCoverage"] == supplied


def test_compact_public_scan_keeps_limitations_and_followup_visible():
    result = scan({"ok": True, "wardrobes": [], "looseControls": [
        {"parameterName": f"Control{i}", "detail": "x" * 120} for i in range(200)
    ]})
    compact = project_result({"ok": True, "result": result,
        "operationResource": "vrcforge://operation/recognition/receipt?revision=1"},
        mode="compact", resource_readable=True)
    coverage = compact["result"]["recognitionCoverage"]
    assert coverage["missingMatchProvesAbsence"] is False
    assert coverage["generalTopologyComplete"] is False
    assert coverage["analysisRequired"] is True
    assert "recognition loop" in coverage["nextAction"]
    assert compact["resultPresentation"]["fullResultUri"].endswith("receipt?revision=1")
    assert len(result["looseControls"]) == 200


def test_control_resolution_is_explicit_without_relabeling_candidate_lists():
    snapshot = {
        "ok": True,
        "wardrobes": [{
            "parameterName": "Confirmed",
            "animatorEvidence": {
                "hasAmbiguousDestinations": False,
                "ambiguousDestinationValues": [],
            },
            "controls": [{
                "value": 1,
                "fxStateName": "Outfit_1",
                "fxCandidates": [{"fxStateName": "Outfit_1"}, {"fxStateName": "Outfit_1"}],
            }],
        }],
        "wardrobeCandidates": [{
            "parameterName": "Candidate",
            "animatorEvidence": {
                "hasAmbiguousDestinations": True,
                "ambiguousDestinationValues": [2],
            },
            "controls": [{
                "value": 2,
                "fxStateName": "",
                "fxCandidates": [{"fxStateName": "A"}, {"fxStateName": "B"}],
            }],
        }],
        "looseControls": [],
    }
    before = copy.deepcopy(snapshot)
    result = scan(snapshot)

    confirmed = result["wardrobes"][0]["controls"][0]
    candidate = result["wardrobeCandidates"][0]["controls"][0]
    assert confirmed["resolutionStatus"] == "resolved"
    assert confirmed["candidateCount"] == 2
    assert candidate["resolutionStatus"] == "ambiguous"
    assert candidate["candidateCount"] == 2
    assert candidate["fxStateName"] is None
    assert snapshot == before

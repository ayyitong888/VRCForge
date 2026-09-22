"""A strict scanner result must not be mistaken for general wardrobe absence."""
import copy

from external_mcp_result_projection import project_result
from wardrobe_outfit_workflow_service import WardrobeArtifactReadPorts, WardrobeArtifactReadService


def scan(snapshot):
    return WardrobeArtifactReadService(WardrobeArtifactReadPorts(
        scan_avatar_items=lambda _: {}, scan_avatar_controls=lambda _: {},
        scan_wardrobe=lambda _: snapshot,
    )).scan_wardrobe({"avatarPath": "Scene/Avatar"})


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


def test_failed_scan_and_other_read_shapes_are_preserved():
    for value in ({"ok": False, "error": "Disconnected", "wardrobes": []}, {"source": "vrc_scan_wardrobe"}):
        assert scan(value) == value


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
    assert candidate["fxStateName"] == ""
    assert snapshot == before

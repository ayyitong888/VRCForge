import json
from copy import deepcopy

import pytest

from agent_tool_result_reader import scan_result_summary
from runtime_planner_service import RuntimePlannerService, sanitize_planner_observation_text as sanitize


def step(result):
    return {"tool": "vrcforge_scan_materials", "kind": "read", "status": "executed",
            "result": result, "scanSnapshot": {"persisted": True, "scope": "current_turn",
              "freshness": "unverified", "scanScope": {}},
            "resultRead": {"resultRef": "result_" + "a" * 32}}


@pytest.mark.parametrize("collection", [[], [{"name": "Face", "value": 0.75}], list(range(12))])
def test_small_complete_public_collection_is_available_without_reader(collection):
    source = {"ok": True, "materials": collection, "paging": {"total": len(collection), "hasMore": False}}
    original = deepcopy(source)
    summary = scan_result_summary(step(source), sanitize)
    assert summary["detailsOmitted"] is False
    assert summary["inlineComplete"] is True
    assert summary["fields"]["materials"] == collection
    assert summary["fields"]["paging"] == source["paging"]
    assert source == original


def test_large_collection_preserves_exact_paging_control_and_reference():
    source = {"ok": True, "index": [{"name": str(i), "values": list(range(50))} for i in range(76)],
              "paging": {"offset": 0, "returned": 36, "total": 76, "nextOffset": 36,
                         "hasMore": True, "byteLimited": True, "maxBytes": 8000},
              "nextRequest": {"tool": "vrcforge_scan_materials", "arguments": {"offset": 36, "limit": 100}},
              "readHints": {"discovery": "Read the retained index collection; do not rescan for another page."}}
    s = step(source)
    summary = scan_result_summary(s, sanitize)
    assert summary["detailsOmitted"] is True
    assert summary["inlineComplete"] is False
    assert "index" not in summary["fields"]
    assert summary["fields"]["paging"] == source["paging"]
    assert summary["fields"]["nextRequest"] == source["nextRequest"]
    assert summary["fields"]["readHints"] == source["readHints"]
    assert len(json.dumps(summary, ensure_ascii=False, separators=(",", ":"))) <= 6000
    observation = RuntimePlannerService._llm_loop_step_observation(None, s)
    assert s["resultRead"]["resultRef"] in observation
    assert '"nextOffset":36' in observation


def test_inline_projection_keeps_redaction_and_never_calls_missing_public_data_complete():
    source = {"ok": True, "materials": [{"name": "Face", "apiKey": "secret-marker",
              "assetPath": "C:/secret/outside/Face.mat"}]}
    summary = scan_result_summary(step(source), sanitize)
    rendered = json.dumps(summary)
    assert "secret-marker" not in rendered
    assert "C:/secret/outside" not in rendered
    assert summary["detailsOmitted"] is True
    assert summary["inlineComplete"] is False


def test_oversized_control_is_explicitly_incomplete_and_never_emits_partial_cursor():
    source = {"ok": True, "rows": list(range(10000)),
              "nextRequest": {"tool": "vrcforge_scan_materials", "arguments": {"selector": "x" * 8000}}}
    summary = scan_result_summary(step(source), sanitize)
    assert summary["pagingControlsIncomplete"] is True
    assert "nextRequest" not in summary["fields"]
    assert summary["detailsOmitted"] is True
    assert len(json.dumps(summary, ensure_ascii=False, separators=(",", ":"))) <= 6000


def test_source_partiality_is_separate_from_complete_inline_page():
    source = {"ok": True, "rows": [{"name": "Face"}], "paging": {"hasMore": True, "nextOffset": 1, "total": 3}}
    summary = scan_result_summary(step(source), sanitize)
    assert summary["inlineComplete"] is True
    assert summary["fields"]["paging"]["hasMore"] is True
    assert "scan completeness" in summary["interpretation"]


def test_source_continuation_never_exposes_secret_or_redacted_argument():
    for arguments in ({"apiKey": "secret-marker"}, {"assetPath": "C:/private/secret.asset"}):
        source = {"ok": True, "rows": list(range(10000)),
                  "nextRequest": {"tool": "vrcforge_scan_materials", "arguments": arguments}}
        summary = scan_result_summary(step(source), sanitize)
        assert summary["pagingControlsIncomplete"] is True
        assert "nextRequest" not in summary["fields"]
        assert "secret-marker" not in json.dumps(summary)
        assert "C:/private" not in json.dumps(summary)

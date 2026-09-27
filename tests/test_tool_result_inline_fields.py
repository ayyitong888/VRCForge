import json

from agent_tool_result_reader import MAX_PAGE_CHARS, bind_tool_result_context, read_tool_result, result_continuation
from runtime_planner_service import sanitize_planner_observation_text as sanitize


def _step(result, *, tool="vrcforge_scan_wardrobe", index=0):
    step = {"index": index, "actionId": f"action-{index}", "tool": tool,
            "status": "executed", "result": result}
    step["resultRead"] = result_continuation("session", "turn", "project", step, sanitize)
    return step


def _read(step, **params):
    return read_tool_result({"resultRef": step["resultRead"]["resultRef"], **params}, sanitize=sanitize)


def test_oversized_object_exposes_complete_immediate_fields_and_child_references():
    result = {"controls": [{
        "menuName": "衣柜/默认",
        "value": 0,
        "inMenu": True,
        "candidateCount": 1,
        "resolutionStatus": "resolved",
        "fxCandidates": [{"name": "clip", "bindings": [{"path": "x", "value": "y"}] * 500}],
    }]}
    step = _step(result)
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = _read(step, jsonPointer="/controls")
    row = page["items"][0]
    assert page["previewTruncated"] is True
    assert row["expandable"] is True and row["truncated"] is True
    assert "value" not in row
    fields = row["immediateFields"]
    assert fields == {"menuName": "衣柜/默认", "value": 0, "inMenu": True,
                      "candidateCount": 1, "resolutionStatus": "resolved"}
    children = {child["jsonPointer"]: child for child in row["childReferences"]}
    assert children["/controls/0/fxCandidates"]["nextRequest"]["arguments"]["jsonPointer"] == "/controls/0/fxCandidates"
    assert json.dumps(row, ensure_ascii=False, separators=(",", ":")).find("bindings") == -1
    assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= MAX_PAGE_CHARS


def test_inline_fields_and_child_reference_reconstruct_original_safe_shape():
    result = {"controls": [{"menuName": "Default", "value": 0,
                              "fxCandidates": [{"name": "clip", "weight": 1}] * 1200}]}
    step = _step(result)
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = _read(step, jsonPointer="/controls")
        row = page["items"][0]
        arguments = row["childReferences"][0]["nextRequest"]["arguments"]
        restored = []
        while True:
            child_page = read_tool_result(arguments, sanitize=sanitize)
            restored.extend(item["value"] for item in child_page["items"])
            if not child_page["hasMore"]:
                break
            arguments = child_page["nextRequest"]["arguments"]
        assert restored == result["controls"][0]["fxCandidates"]
    fields = row["immediateFields"]
    assert fields == {"menuName": "Default", "value": 0}
    assert row["childReferences"][0]["jsonPointer"] == "/controls/0/fxCandidates"


def test_inline_projection_preserves_constraints_and_redacts_private_fields():
    result = {"recognitionCoverage": {"candidateEnumerationComplete": False}, "controls": [{
        "menuName": "Default", "resolutionStatus": "ambiguous", "candidateCount": 2,
        "privateEvidence": {"apiKey": "SECRET"}, "fxCandidates": [{"name": "one"}] * 1200,
    }]}
    step = _step(result)
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = _read(step, jsonPointer="/controls")
    rendered = json.dumps(page, ensure_ascii=False)
    assert "SECRET" not in rendered and "privateEvidence" not in rendered
    assert page["sourceConstraints"][0]["facts"]["candidateEnumerationComplete"] is False
    fields = page["items"][0]["immediateFields"]
    assert fields["menuName"] == "Default"
    assert fields["resolutionStatus"] == "ambiguous"


def test_owner_model_oversized_object_uses_same_inline_projection_and_turn_owner():
    from agent_tool_result_reader import retain_model_information

    step = _step({"ok": True}, tool="vrcforge_read_text_file")
    step.update(retain_model_information("session", "turn", "project", step, {
        "controls": [{"menuName": "Owner", "value": 0, "fxCandidates": [{"name": "x"}] * 1200}],
    }, sanitize))
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = read_tool_result({"resultRef": step["modelInformationRead"]["resultRef"], "source": "owner_model",
                                 "jsonPointer": "/controls"}, sanitize=sanitize)
    row = page["items"][0]
    assert row["immediateFields"] == {"menuName": "Owner", "value": 0}
    assert row["childReferences"][0]["nextRequest"]["arguments"]["source"] == "owner_model"
    with bind_tool_result_context("session", "later", "project", [step]):
        try:
            read_tool_result({"resultRef": step["modelInformationRead"]["resultRef"], "source": "owner_model"}, sanitize=sanitize)
        except PermissionError:
            pass
        else:
            raise AssertionError("owner model payload crossed a turn boundary")

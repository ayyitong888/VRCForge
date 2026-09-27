import pytest

from agent_gateway import redact_sensitive
from agent_tool_result_reader import (
    bind_tool_result_context,
    page_item_request_arguments,
    read_tool_result,
    retain_model_information,
    result_continuation,
)
from runtime_planner_service import sanitize_planner_observation_text as sanitize


def _step(result, *, tool="vrcforge_scan_wardrobe"):
    step = {"index": 0, "actionId": "child-cursor", "tool": tool,
            "status": "executed", "result": result}
    step["resultRead"] = result_continuation("session", "turn", "project", step, sanitize)
    return step


def _page(step, *, source="result"):
    if source == "owner_model":
        params = {"resultRef": step["modelInformationRead"]["resultRef"],
                  "source": source, "jsonPointer": "/controls"}
    else:
        params = {"resultRef": step["resultRead"]["resultRef"], "jsonPointer": "/controls"}
    return read_tool_result(params, sanitize=sanitize)


def _read_all(args):
    values = []
    page = read_tool_result(args, sanitize=sanitize)
    while True:
        values.extend(row["value"] for row in page["items"])
        if not page["hasMore"]:
            return values
        page = read_tool_result(page["nextRequest"]["arguments"], sanitize=sanitize)


def test_redaction_twice_preserves_result_child_cursor_and_reconstructs_nested_value():
    step = _step({"controls": [{"menuName": "Default", "fxCandidates": [{"name": f"clip_{i}"} for i in range(1200)]}]})
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = _page(step)
        transported = redact_sensitive(redact_sensitive(page))
        child = transported["items"][0]["childReferences"][0]
        assert child["jsonPointer"] == "/controls/0/fxCandidates"
        assert child["nextRequest"]["arguments"]["jsonPointer"] == child["jsonPointer"]
        child_page = {**transported, "items": transported["items"][0]["childReferences"],
                      "jsonPointer": transported["items"][0]["jsonPointer"], "totalItems": 1}
        assert page_item_request_arguments(child_page, child)["jsonPointer"] == child["jsonPointer"]
        values = _read_all(child["nextRequest"]["arguments"])
    assert values == [{"name": f"clip_{i}"} for i in range(1200)]


def test_redaction_twice_preserves_owner_model_child_cursor_and_source():
    step = _step({"ok": True}, tool="vrcforge_read_text_file")
    step.update(retain_model_information(
        "session", "turn", "project", step,
        {"controls": [{"menuName": "Owner", "fxCandidates": [{"name": f"clip_{i}"} for i in range(1200)]}]},
        sanitize,
    ))
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = _page(step, source="owner_model")
        transported = redact_sensitive(redact_sensitive(page))
        child = transported["items"][0]["childReferences"][0]
        args = child["nextRequest"]["arguments"]
        assert args["source"] == "owner_model"
        assert args["jsonPointer"] == "/controls/0/fxCandidates"
        values = _read_all(args)
    assert values == [{"name": f"clip_{i}"} for i in range(1200)]


def test_invalid_parent_does_not_preserve_nested_cursor_arguments():
    step = _step({"controls": [{"fxCandidates": [{"name": "clip"}] * 1200}]})
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = _page(step)
    page["items"][0]["nextRequest"]["arguments"]["resultRef"] = "result_" + "0" * 32
    transported = redact_sensitive(page)
    child = transported["items"][0]["childReferences"][0]
    assert child["nextRequest"]["arguments"]["jsonPointer"] != child["jsonPointer"]


@pytest.mark.parametrize("field,value", [
    ("resultRef", "result_" + "0" * 32),
    ("source", "owner_model"),
    ("jsonPointer", "/controls/0/other"),
])
def test_child_cursor_mutation_is_rejected(field, value):
    step = _step({"controls": [{"menuName": "Default", "fxCandidates": [{"name": "clip"}] * 1200}]})
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = _page(step)
        child = redact_sensitive(redact_sensitive(page))["items"][0]["childReferences"][0]
        forged = dict(child["nextRequest"]["arguments"])
        forged[field] = value
        with pytest.raises((PermissionError, ValueError)):
            read_tool_result(forged, sanitize=sanitize)

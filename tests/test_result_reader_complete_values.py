import json
from copy import deepcopy

import pytest

from agent_tool_result_reader import (
    MAX_PAGE_CHARS, bind_tool_result_context, read_tool_result,
    result_continuation, retain_model_information,
    page_item_request_arguments,
)
from runtime_planner_service import sanitize_planner_observation_text as sanitize


def step_for(payload, owner=False):
    step = {"index": 0, "actionId": "complete-values", "tool": "fixture_read", "result": payload}
    if owner:
        step.update(retain_model_information("session", "turn", "project", step, payload, sanitize))
    else:
        step["resultRead"] = result_continuation("session", "turn", "project", step, sanitize)
    return step


def request_for(step, pointer="", owner=False):
    return {"resultRef": step["modelInformationRead" if owner else "resultRead"]["resultRef"],
            "source": "owner_model" if owner else "result", "jsonPointer": pointer, "offset": 0, "limit": 20}


def page(args):
    result = read_tool_result(args, sanitize=sanitize)
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) <= MAX_PAGE_CHARS
    return result


def recover(args):
    result = page(args)
    if "textOffset" in result:
        parts = [result["items"][0]["value"]]
        while result["hasMore"]:
            result = page(result["nextRequest"]["arguments"])
            parts.append(result["items"][0]["value"])
        return "".join(parts)
    rows = []
    while True:
        for row in result["items"]:
            assert "incompleteFields" not in row
            if "value" in row:
                assert row["truncated"] is False
                value = row["value"]
            else:
                assert row["expandable"] is True
                value = recover(row["nextRequest"]["arguments"])
            rows.append((row["jsonPointer"], value))
        if not result["hasMore"]:
            break
        result = page(result["nextRequest"]["arguments"])
    return rows


def test_collection_row_returns_complete_medium_text_instead_of_500_character_preview():
    text = "x" * 3500 + " MEDIUM_TAIL"
    step = step_for({"rows": [{"description": text}]})
    with bind_tool_result_context("session", "turn", "project", [step]):
        row = page(request_for(step, "/rows"))["items"][0]
    assert row["value"] == {"description": text}
    assert row["truncated"] is False


def test_safe_long_identity_is_recoverable_with_explicit_text_cursor():
    identity = "Coverage_" + "N" * 40000 + "_IDENTITY_TAIL"
    step = step_for({"targetName": identity})
    with bind_tool_result_context("session", "turn", "project", [step]):
        assert recover(request_for(step, "/targetName")) == identity


def test_oversized_nested_rows_expand_without_partial_values_and_recover_all_fields():
    description = "段落😀 " * 6000 + " NESTED_TAIL"
    nested = {"description": description, "rows": [{"value": i, "label": "row_" + str(i)} for i in range(30)]}
    step = step_for({"a/b~c": [nested], "last": False})
    with bind_tool_result_context("session", "turn", "project", [step]):
        root = page(request_for(step))
        first = root["items"][0]
        assert "value" not in first, "oversized values must be references, not cut previews"
        assert first["nextRequest"]["arguments"]["jsonPointer"] == "/a~1b~0c"
        rows = recover(first["nextRequest"]["arguments"])
        fields = dict(rows[0][1])
        assert fields["/a~1b~0c/0/description"] == sanitize(description, None)
        assert fields["/a~1b~0c/0/rows"] == nested["rows"]
        assert root["items"][-1]["value"] is False


def test_owner_source_completeness_is_repeated_on_every_text_page():
    facts = [{"jsonPointer": "/summary", "facts": {"truncated": True, "itemCount": 2000}}]
    payload = {"text": "X" * 40000, "sourceCompleteness": facts}
    step = step_for(payload, owner=True)
    args = request_for(step, "/text", owner=True)
    with bind_tool_result_context("session", "turn", "project", [step]):
        while True:
            current = page(args)
            assert current["sourceCompleteness"] == facts
            if not current["hasMore"]:
                break
            args = current["nextRequest"]["arguments"]


def test_expansion_request_validation_keeps_exact_child_and_rejects_forged_coordinates():
    step = step_for({"a/b~c": {"description": "X" * 40000}})
    with bind_tool_result_context("session", "turn", "project", [step]):
        outer = page(request_for(step))
        assert page_item_request_arguments(outer, outer["items"][0]) == outer["items"][0]["nextRequest"]["arguments"]
        child = page(outer["items"][0]["nextRequest"]["arguments"])
        assert page_item_request_arguments(child, child["items"][0]) == child["items"][0]["nextRequest"]["arguments"]
    for key, wrong in [("resultRef", "result_" + "0" * 32), ("source", "owner_model"),
                       ("offset", True), ("offset", 1), ("limit", 21), ("textOffset", 2),
                       ("jsonPointer", "/unrelated"), ("jsonPointer", "/a~1b~0c/description/nested")]:
        bad = deepcopy(child)
        bad["items"][0]["nextRequest"]["arguments"][key] = wrong
        assert page_item_request_arguments(bad, bad["items"][0]) is None
    for key, wrong in [("schema", "other"), ("authority", "trusted"), ("source", "owner_model")]:
        bad = deepcopy(child); bad[key] = wrong
        assert page_item_request_arguments(bad, bad["items"][0]) is None


def test_complete_collection_projection_keeps_private_fields_and_unsafe_identity_closed():
    step = step_for({"rows": [{"description": "ordinary " * 400 + "password=secret-tail",
                              "targetPath": "C:/private/host.txt", "privateDump": "hidden",
                              "apiKey": "hidden-key", "name": "safe"}]})
    with bind_tool_result_context("session", "turn", "project", [step]):
        current = page(request_for(step, "/rows"))
        safe = current["items"][0]["value"]
        assert safe["name"] == "safe"
        assert "targetPath" not in safe and "privateDump" not in safe and "apiKey" not in safe
        assert "secret-tail" not in json.dumps(current)
        with pytest.raises(ValueError):
            page({**request_for(step, "/rows/0/targetPath"), "textOffset": 0})

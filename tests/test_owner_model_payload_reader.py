from agent_tool_result_reader import (
    bind_tool_result_context, model_payload_continuation, read_tool_result,
    retain_model_information, result_continuation, page_next_request_arguments,
)
from runtime_planner_service import sanitize_planner_observation_text


def _sanitize(value, limit):
    return sanitize_planner_observation_text(value, limit)


def test_owner_model_payload_is_current_turn_bound_and_paged():
    step = {
        "index": 3, "actionId": "a3", "tool": "vrcforge_scan_wardrobe",
        "result": {"raw": "kept"},
    }
    payload = {"trust": "owner_validated", "payload": {
        "values": [{"value": 0, "name": "Default"}, {"value": 1, "name": "Alt"}]
    }}
    attachment = retain_model_information("s", "t", "p", step, payload["payload"], _sanitize)
    step.update(attachment)
    descriptor = step["modelInformationRead"]
    assert descriptor["page"]["source"] == "owner_model"
    assert descriptor["page"]["jsonPointer"] == ""
    step["resultRead"] = result_continuation("s", "t", "p", step, _sanitize)
    with bind_tool_result_context("s", "t", "p", [step]):
        page = read_tool_result({
            "resultRef": descriptor["resultRef"], "source": "owner_model",
            "jsonPointer": "/values", "offset": 0, "limit": 1,
        }, sanitize=_sanitize)
    assert page["source"] == "owner_model"
    assert page["items"][0]["value"]["value"] == 0
    assert page["hasMore"] is True
    assert page_next_request_arguments(page)["source"] == "owner_model"


def test_owner_model_payload_cannot_be_selected_without_owner_marker():
    step = {
        "index": 4, "actionId": "a4", "tool": "vrcforge_scan_wardrobe",
        "result": {"raw": "kept"},
        "modelInformation": {"payload": {"secret": "x"}},
    }
    step["resultRead"] = result_continuation("s", "t", "p", step, _sanitize)
    with bind_tool_result_context("s", "t", "p", [step]):
        try:
            read_tool_result({
                "resultRef": step["resultRead"]["resultRef"], "source": "owner_model",
            }, sanitize=_sanitize)
        except PermissionError:
            return
    raise AssertionError("unmarked model payload was readable")


def test_restricted_raw_result_stays_hidden_but_owner_payload_is_readable():
    step = {
        "index": 6, "actionId": "a6", "tool": "vrcforge_read_text_file",
        "result": {"raw": "must remain restricted"},
    }
    attachment = retain_model_information("s", "t", "p", step, {"answer": "approved"}, _sanitize)
    step.update(attachment)
    step["resultRead"] = result_continuation("s", "t", "p", step, _sanitize)
    assert step["resultRead"] == {}
    with bind_tool_result_context("s", "t", "p", [step]):
        page = read_tool_result({
            "resultRef": step["modelInformationRead"]["resultRef"],
            "source": "owner_model", "jsonPointer": "", "offset": 0, "limit": 1,
        }, sanitize=_sanitize)
    assert page["items"][0]["value"] == "approved"


def test_default_result_reader_contract_is_unchanged():
    step = {
        "index": 5, "actionId": "a5", "tool": "vrcforge_scan_wardrobe",
        "result": {"values": [1, 2]},
    }
    step["resultRead"] = result_continuation("s", "t", "p", step, _sanitize)
    with bind_tool_result_context("s", "t", "p", [step]):
        page = read_tool_result({
            "resultRef": step["resultRead"]["resultRef"],
            "jsonPointer": "/values", "offset": 0, "limit": 2,
        }, sanitize=_sanitize)
    assert page["source"] == "result"
    assert [row["value"] for row in page["items"]] == [1, 2]


def test_owner_model_text_payload_uses_text_pointer_and_continuation():
    step = {"index": 7, "actionId": "a7", "tool": "vrcforge_scan_fx_animator", "result": {"ok": True}}
    attachment = retain_model_information("s", "t", "p", step, {"text": "complete-owner-projection"}, _sanitize)
    step.update(attachment)
    assert attachment["modelInformationRead"]["page"]["jsonPointer"] == "/text"
    with bind_tool_result_context("s", "t", "p", [step]):
        page = read_tool_result({
            "resultRef": step["modelInformationRead"]["resultRef"], "source": "owner_model",
            "jsonPointer": "/text", "offset": 0, "limit": 1,
        }, sanitize=_sanitize)
    assert page["items"][0]["value"] == "complete-owner-projection"
    assert page["hasMore"] is False


def test_owner_model_serialized_text_preserves_escapes_and_trailing_whitespace():
    source = '{"path":"C:\\\\owned\\\\tail", "note":"quoted"}  \\n'
    step = {"index": 8, "actionId": "a8", "tool": "vrcforge_scan_fx_animator", "result": {"ok": True}}
    step.update(retain_model_information("s", "t", "p", step, {"text": source}, _sanitize))
    with bind_tool_result_context("s", "t", "p", [step]):
        page = read_tool_result({
            "resultRef": step["modelInformationRead"]["resultRef"], "source": "owner_model",
            "jsonPointer": "/text", "offset": 0, "limit": 1,
        }, sanitize=_sanitize)
    assert page["items"][0]["value"] == source

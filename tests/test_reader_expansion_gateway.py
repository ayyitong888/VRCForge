from agent_gateway import redact_sensitive
from agent_tool_result_reader import bind_tool_result_context, read_tool_result, result_continuation
from runtime_planner_service import sanitize_planner_observation_text as sanitize


def test_gateway_preserves_valid_child_selector_through_diagnostic_redaction():
    key = "layer/long~description"
    text = "complete field " * 3000
    step = {"index": 0, "tool": "fixture_scan", "result": {key: text}}
    step["resultRead"] = result_continuation("owner", "turn", "project", step, sanitize)
    with bind_tool_result_context("owner", "turn", "project", [step]):
        root = read_tool_result(step["resultRead"]["nextRequest"]["arguments"], sanitize=sanitize)
        projected = redact_sensitive(root)
        request = projected["items"][0]["nextRequest"]
        assert request == root["items"][0]["nextRequest"]
        assert request["arguments"]["jsonPointer"] == "/layer~1long~0description"
        parts = []
        while True:
            page = read_tool_result(request["arguments"], sanitize=sanitize)
            parts.append(page["items"][0]["value"])
            if not page["hasMore"]:
                break
            request = redact_sensitive(page)["nextRequest"]
    assert "".join(parts) == sanitize(text, len(text) * 4 + 100)

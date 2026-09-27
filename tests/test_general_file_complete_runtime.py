import dashboard_server
from agent_tool_result_reader import bind_tool_result_context, read_tool_result, retain_model_information
from runtime_planner_service import planner_read_output_evidence, sanitize_planner_observation_text


def test_runtime_file_tail_survives_old_byte_and_character_limits(tmp_path):
    text = "Unicode text 中文\n" * 16000 + "FILE_END_SENTINEL\n"
    target = tmp_path / "evidence.txt"
    target.write_text(text, encoding="utf-8", newline="")
    result = dashboard_server.AGENT_GATEWAY._tools["vrcforge_read_text_file"].handler(
        {"path": str(target), "maxOutputChars": 32, "_generalAllowedRoots": [str(target)]}
    )
    assert result["text"] == text
    assert result["truncated"] is False
    evidence = planner_read_output_evidence("vrcforge_read_text_file", result)
    assert evidence["text"] == text
    step = {"index": 0, "tool": "vrcforge_read_text_file", "actionId": "file-fixture"}
    step.update(retain_model_information("file", "turn", "", step,
                                        {"text": evidence["text"]}, sanitize_planner_observation_text))
    page = step["modelInformationRead"]["page"]
    parts = [page["items"][0]["value"]]
    with bind_tool_result_context("file", "turn", "", [step]):
        while page["hasMore"]:
            page = read_tool_result(page["nextRequest"]["arguments"], sanitize=sanitize_planner_observation_text)
            parts.append(page["items"][0]["value"])
    assert len(parts) > 1
    assert "".join(parts) == text

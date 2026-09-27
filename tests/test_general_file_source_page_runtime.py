import dashboard_server

from runtime_planner_service import planner_read_output_evidence
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS


def test_file_source_pages_keep_continuation_and_authorization_private(tmp_path):
    text = "Ordinary Unicode content 中文\n" * 40 + "COMPLETE_SOURCE_END\n"
    target = tmp_path / "source.txt"
    target.write_text(text, encoding="utf-8", newline="")
    handler = dashboard_server.AGENT_GATEWAY._tools["vrcforge_read_text_file"].handler
    args = {"path": str(target), "maxBytes": 64, "pageChars": 80}
    parts = []
    for _ in range(100):
        result = handler({**args, "_generalAllowedRoots": [str(target)]})
        evidence = planner_read_output_evidence("vrcforge_read_text_file", result)
        parts.append(evidence["text"])
        assert evidence["hasMore"] == result["hasMore"]
        assert evidence["snapshotDigest"] == result["snapshotDigest"]
        if not result["hasMore"]:
            break
        assert evidence["nextRequest"] == result["nextRequest"]
        args = result["nextRequest"]["arguments"]
        assert "_generalAllowedRoots" not in args
        assert args["textOffset"] == sum(map(len, parts))
    else:
        raise AssertionError("Source cursor did not terminate")
    assert "".join(parts) == text


def test_file_source_page_arguments_are_discoverable():
    spec = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_read_text_file"]["properties"]
    assert {"textOffset", "pageChars", "snapshotDigest"} <= spec.keys()

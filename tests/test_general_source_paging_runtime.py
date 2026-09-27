import pytest
import dashboard_server
from runtime_planner_service import planner_read_output_evidence
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS


@pytest.mark.parametrize("name,key", [("list_directory", "entries"), ("find_files", "files"), ("search_text", "matches")])
def test_source_continuation_survives_runtime_adapter_and_model_projection(tmp_path, name, key):
    expected = []
    for index in range(7):
        target = tmp_path / f"item-{index}.txt"
        target.write_text("match\n", encoding="utf-8")
        expected.append(str(target))
    tool = "vrcforge_" + name
    handler = dashboard_server.AGENT_GATEWAY._tools[tool].handler
    args = {"path": str(tmp_path), "maxCount": 2}
    if name == "search_text":
        args["query"] = "match"
    paths = []
    pages = []
    while True:
        result = handler({**args, "_generalAllowedRoots": [str(tmp_path)]})
        paths.extend(row["path"] for row in result[key])
        evidence = planner_read_output_evidence(tool, result)
        pages.append(evidence)
        assert evidence["totalCount"] == 7
        if not result["hasMore"]:
            assert "nextRequest" not in evidence
            break
        request = evidence["nextRequest"]
        assert request == result["nextRequest"]
        assert "_generalAllowedRoots" not in request["arguments"]
        assert set(request["arguments"]) <= set(UNITY_READ_TOOL_INPUT_SCHEMAS[tool]["properties"])
        args = request["arguments"]
    assert paths == expected
    assert len(pages) == 4


def test_search_adapter_respects_documented_file_limit(tmp_path):
    path = tmp_path / "long.txt"
    path.write_text("x" * 150000 + "\nTAIL", encoding="utf-8")
    result = dashboard_server.AGENT_GATEWAY._tools["vrcforge_search_text"].handler({
        "path": str(path), "query": "TAIL", "maxFileBytes": 200000,
        "_generalAllowedRoots": [str(path)],
    })
    assert len(result["matches"]) == 1
    assert result["skipped_resource_limit"] == 0

from general_agent_tools import search_text


def test_search_resource_limit_is_not_misreported_as_binary(tmp_path):
    target = tmp_path / "source.txt"
    target.write_text("needle beyond a small byte limit", encoding="utf-8")
    result = search_text(target, "needle", allowed_roots=[target], max_file_bytes=4)
    assert result["truncated"] is True
    assert result["skipped_binary"] == 0
    assert result["skipped_resource_limit"] == 1
    assert result["matches"] == []
    from runtime_planner_service import planner_read_output_evidence
    evidence = planner_read_output_evidence("vrcforge_search_text", result)
    assert evidence["skipped_resource_limit"] == 1
    assert evidence["truncated"] is True

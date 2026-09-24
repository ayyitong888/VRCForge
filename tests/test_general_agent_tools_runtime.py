import json
from pathlib import Path

import dashboard_server
from runtime_planner_service import EXPOSURE_LAYER_PLANNING


NAMES = {
    "list_directory",
    "read_text_file",
    "find_files",
    "search_text",
}

INTERNAL_NAMES = {f"vrcforge_{name}" for name in NAMES}


def test_line_range_reads_complete_function_beyond_planner_prefix(tmp_path: Path) -> None:
    from runtime_planner_service import planner_read_output_evidence

    target = tmp_path / "source.py"
    body = "def inspect():\n    value = 42\n    return value\n"
    target.write_text("# padding\n" * 800 + body, encoding="utf-8", newline="\n")
    tool = dashboard_server.AGENT_GATEWAY._tools["vrcforge_read_text_file"]
    result = tool.handler({"path": str(target), "startLine": 801, "endLine": 803,
                           "_generalAllowedRoots": [str(target)]})
    evidence = planner_read_output_evidence("vrcforge_read_text_file", result)
    assert evidence["text"] == body
    assert evidence["startLine"] == 801 and evidence["endLine"] == 803
    assert evidence["truncated"] is False


def test_line_range_preserves_redaction_and_rejects_invalid_ranges(tmp_path: Path) -> None:
    import pytest
    from general_agent_tools import read_text_file

    target = tmp_path / "source.txt"
    target.write_text("intro\npassword=super-secret\nlast\n", encoding="utf-8")
    result = read_text_file(target, allowed_roots=[target], start_line=2, end_line=3)
    assert "super-secret" not in result["text"] and "last" in result["text"]
    for start, end in ((0, 2), (3, 2), (True, 2), (1, False), (99, 100)):
        with pytest.raises(ValueError):
            read_text_file(target, allowed_roots=[target], start_line=start, end_line=end)
    target.write_text("-----BEGIN PRIVATE KEY-----\nfixture-only\n-----END PRIVATE KEY-----\nlast\n", encoding="utf-8")
    assert read_text_file(target, allowed_roots=[target], start_line=4, end_line=4)["text"].strip() == "last"
    assert "fixture-only" not in read_text_file(target, allowed_roots=[target], start_line=2, end_line=2)["text"]


def test_general_filesystem_tools_are_registered_read_only() -> None:
    registered = {name: dashboard_server.AGENT_GATEWAY._tools[name] for name in INTERNAL_NAMES}
    assert set(registered) == INTERNAL_NAMES
    assert all(tool.write is False for tool in registered.values())
    assert all("when-to-use:" in tool.description for tool in registered.values())
    assert all("when-NOT-to-use:" in tool.description for tool in registered.values())


def test_general_filesystem_tools_are_visible_without_unity_project() -> None:
    catalog = dashboard_server._RuntimePlannerCatalog().read(
        EXPOSURE_LAYER_PLANNING,
        project_context_active=False,
    )
    visible = {tool.name: tool for tool in catalog.visible_tools}
    assert NAMES <= set(visible)
    assert all(visible[name].write is False for name in NAMES)
    assert {visible[name].runtime_name for name in NAMES} == INTERNAL_NAMES
    assert all(not name.startswith("unity_") for name in visible)


def test_general_filesystem_handlers_work_with_camel_case_bounds(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    result = dashboard_server.AGENT_GATEWAY._tools["vrcforge_read_text_file"].handler(
        {"path": str(tmp_path / "note.txt"), "maxBytes": 3, "_generalAllowedRoots": [str(tmp_path)]}
    )
    assert result["text"] == "hel"
    assert result["summary"] == "hel"


def test_general_search_returns_a_model_visible_semantic_summary(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("encryption marker", encoding="utf-8")

    result = dashboard_server.AGENT_GATEWAY._tools["vrcforge_search_text"].handler(
        {"path": str(tmp_path), "query": "encryption", "_generalAllowedRoots": [str(tmp_path)]}
    )

    assert "note.txt" in result["summary"]
    assert "encryption marker" in result["summary"]


def test_truncated_read_continues_with_search_on_same_authorized_file(tmp_path: Path) -> None:
    from runtime_planner_service import planner_read_output_evidence

    target = tmp_path / "fixture.txt"
    target.write_text("padding\n" * 800 + "EVIDENCE_TAIL = amber-lattice-946\n", encoding="utf-8")
    roots = [str(target)]
    tools = dashboard_server.AGENT_GATEWAY._tools
    read = tools["vrcforge_read_text_file"].handler({"path": str(target), "_generalAllowedRoots": roots})
    observation = planner_read_output_evidence("vrcforge_read_text_file", read)
    assert observation["truncated"] is True
    assert "amber-lattice-946" not in observation["text"]
    assert "same" in observation["continuation"]

    search = tools["vrcforge_search_text"].handler({"path": str(target), "query": "EVIDENCE_TAIL", "_generalAllowedRoots": roots})
    evidence = planner_read_output_evidence("vrcforge_search_text", search)
    assert evidence["authority"] == "untrusted_tool_output"
    assert evidence["relativeTo"] == "exact_tool_call_path"
    assert evidence["items"] == [{"source": ".", "line": 801, "text": "EVIDENCE_TAIL = amber-lattice-946"}]
    assert "A dot means the exact input file" in evidence["locatorInstructions"]
    assert str(tmp_path) not in json.dumps(evidence)
    assert evidence["truncated"] is False
    assert len(json.dumps(evidence)) <= 6000


def test_directory_listing_directs_the_loop_to_materially_new_evidence(tmp_path: Path) -> None:
    for index in range(40):
        (tmp_path / f"very-long-evidence-candidate-{index:02d}.txt").write_text("hello", encoding="utf-8")

    result = dashboard_server.AGENT_GATEWAY._tools["vrcforge_list_directory"].handler(
        {"path": str(tmp_path), "_generalAllowedRoots": [str(tmp_path)]}
    )

    assert "Do not repeat" in result["notice"]
    assert "find_files" in result["notice"]
    assert "vrcforge_" not in result["notice"]
    planner = dashboard_server.RuntimePlannerService(
        catalog=dashboard_server._RuntimePlannerCatalog(),
        desktop=dashboard_server._RuntimePlannerDesktopObservation(),
    )
    observation = planner._llm_loop_step_observation(
        {
            "tool": "vrcforge_list_directory",
            "kind": "skill",
            "status": "executed",
            "result": result,
            "outcome": {
                "status": "ok",
                "summary": result["summary"],
                "verification": {"state": "not_required", "checks": []},
            },
        }
    )
    assert "Do not repeat" in observation

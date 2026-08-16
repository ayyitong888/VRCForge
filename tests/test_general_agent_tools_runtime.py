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

from __future__ import annotations

import dashboard_server as dashboard
import pytest
from profiled_tool_registry import CapabilityProfile, UNITY_PROJECT_ACCESS


def test_runtime_profiles_keep_general_capabilities_and_add_unity_tools() -> None:
    general = {
        item.model_name: item
        for item in dashboard._RUNTIME_PROFILED_TOOL_REGISTRY.project(
            CapabilityProfile.GENERAL
        )
    }
    unity = {
        item.model_name: item
        for item in dashboard._RUNTIME_PROFILED_TOOL_REGISTRY.project(
            CapabilityProfile.UNITY_PROJECT
        )
    }

    assert {
        "list_directory",
        "read_text_file",
        "find_files",
        "search_text",
        "edit_file",
        "write_file",
        "delete_path",
        "move_path",
        "apply_patch",
        "web_fetch",
        "web_search",
        "shell",
    } <= set(general)
    assert set(general) < set(unity)
    assert all(not name.startswith("vrcforge_") for name in unity)
    assert unity["list_directory"].handler is general["list_directory"].handler
    assert unity["unity_shell"].internal_name == "vrcforge_execute_shell"
    assert unity["unity_shell"].capabilities == {UNITY_PROJECT_ACCESS}


def test_planner_catalog_preserves_model_alias_and_internal_runtime_name() -> None:
    catalog = dashboard._RuntimePlannerCatalog().read(
        "execution",
        project_context_active=True,
    )
    by_name = {tool.name: tool for tool in catalog.visible_tools}

    assert by_name["list_directory"].runtime_name == "vrcforge_list_directory"
    assert by_name["unity_shell"].runtime_name == "vrcforge_execute_shell"
    assert by_name["unity_shell"].capabilities == (UNITY_PROJECT_ACCESS,)


def test_registered_path_guard_uses_unity_projects_but_not_general_projects(monkeypatch) -> None:
    guard = dashboard.UNITY_PROJECT_PATH_GUARD
    previous_roots = guard.registered_roots
    previous_current = guard.current_root
    class Snapshot:
        @staticmethod
        def project_snapshot_payload(**_kwargs):
            return {
            "projects": [
                {"path": r"C:\Unity\Avatar", "projectType": "unity"},
                {"path": r"C:\Notes", "projectType": "general"},
            ]
            }

    monkeypatch.setattr(dashboard, "PROJECT_SNAPSHOT_SELECTION", Snapshot())
    monkeypatch.setattr(dashboard.DASHBOARD_STATE, "selected_project_path", r"C:\Unity\Avatar")
    try:
        dashboard.refresh_unity_project_path_guard()
        assert not guard.is_write_allowed(r"C:\Unity\Avatar\Assets\a.txt")
        assert guard.is_write_allowed(r"C:\Notes\note.md")
    finally:
        guard.replace_roots(previous_roots)
        if previous_current:
            guard.set_current_root(previous_current)


def test_selected_general_project_does_not_become_a_unity_root(monkeypatch) -> None:
    guard = dashboard.UNITY_PROJECT_PATH_GUARD
    previous_roots = guard.registered_roots
    previous_current = guard.current_root

    class Snapshot:
        @staticmethod
        def project_snapshot_payload(**_kwargs):
            return {
                "projects": [
                    {"path": r"C:\Unity\Avatar", "projectType": "unity"},
                    {"path": r"C:\Notes", "projectType": "general"},
                ]
            }

    monkeypatch.setattr(dashboard, "PROJECT_SNAPSHOT_SELECTION", Snapshot())
    monkeypatch.setattr(dashboard.DASHBOARD_STATE, "selected_project_path", r"C:\Notes")
    try:
        guard.replace_roots([r"C:\Unity\Avatar"])
        guard.set_current_root(r"C:\Unity\Avatar")
        dashboard.refresh_unity_project_path_guard()
        assert guard.current_root == ""
        assert guard.is_write_allowed(r"C:\Notes\note.md")
        assert not guard.is_write_allowed(r"C:\Unity\Avatar\Assets\a.txt")
    finally:
        guard.replace_roots(previous_roots)
        if previous_current:
            guard.set_current_root(previous_current)


def test_shared_general_handlers_read_project_but_reject_project_write(monkeypatch, tmp_path) -> None:
    project = tmp_path / "unity"
    outside = tmp_path / "outside"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)
    outside.mkdir()
    source = project / "Assets" / "readme.txt"
    source.write_text("visible", encoding="utf-8")
    guard = dashboard.UnityPathGuard([project], current_root=project)
    monkeypatch.setattr(dashboard, "refresh_unity_project_path_guard", lambda: guard)

    read = dashboard.AGENT_GATEWAY._tools["vrcforge_read_text_file"].handler(
        {"path": str(source), "_generalAllowedRoots": [str(project)]}
    )
    assert read["text"] == "visible"
    with pytest.raises(PermissionError):
        dashboard.AGENT_GATEWAY._tools["vrcforge_write_file"].handler(
            {"path": str(project / "Assets" / "blocked.txt"), "content": "no"}
        )
    written = dashboard.AGENT_GATEWAY._tools["vrcforge_write_file"].handler(
        {"path": str(outside / "allowed.txt"), "content": "yes"}
    )
    assert written["operation"] == "write_file"
    assert (outside / "allowed.txt").read_text(encoding="utf-8") == "yes"

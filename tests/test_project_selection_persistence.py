from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import dashboard_server


def make_unity_project(root: Path) -> Path:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n",
        encoding="utf-8",
    )
    return root


def test_app_state_selection_persists_and_loads_on_restart(tmp_path: Path, monkeypatch) -> None:
    project = make_unity_project(tmp_path / "UnityProject")
    selection_path = tmp_path / "user-data" / "config" / "selected-project.json"
    monkeypatch.setattr(dashboard_server, "PROJECT_SELECTION_PATH", selection_path)
    original_project = dashboard_server.DASHBOARD_STATE.selected_project_path
    original_instance = dashboard_server.DASHBOARD_STATE.unity_instance
    expected = dashboard_server.normalize_path_string(str(project.resolve()))
    try:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/state", json={"projectPath": str(project)})

        assert response.status_code == 200, response.text
        assert response.json()["selectedProjectPath"] == expected
        persisted = json.loads(selection_path.read_text(encoding="utf-8"))
        assert persisted["schema"] == dashboard_server.PROJECT_SELECTION_SCHEMA
        assert persisted["selectedProjectPath"] == expected
        assert isinstance(persisted.get("updatedAt"), str)
        assert persisted["updatedAt"].strip()

        dashboard_server.DASHBOARD_STATE.selected_project_path = ""
        dashboard_server.DASHBOARD_STATE.unity_instance = ""
        restarted = dashboard_server.load_initial_dashboard_state()
        assert restarted.selected_project_path == expected
        assert restarted.unity_instance == project.name
    finally:
        dashboard_server.DASHBOARD_STATE.selected_project_path = original_project
        dashboard_server.DASHBOARD_STATE.unity_instance = original_instance


def test_empty_selection_is_explicitly_persisted_without_guessing(tmp_path: Path, monkeypatch) -> None:
    selection_path = tmp_path / "user-data" / "config" / "selected-project.json"
    monkeypatch.setattr(dashboard_server, "PROJECT_SELECTION_PATH", selection_path)
    original_project = dashboard_server.DASHBOARD_STATE.selected_project_path
    original_instance = dashboard_server.DASHBOARD_STATE.unity_instance
    try:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/state", json={"projectPath": ""})

        assert response.status_code == 200, response.text
        assert response.json()["selectedProjectPath"] == ""
        assert dashboard_server.load_persisted_selected_project_path() == ""
        assert json.loads(selection_path.read_text(encoding="utf-8"))["selectedProjectPath"] == ""
    finally:
        dashboard_server.DASHBOARD_STATE.selected_project_path = original_project
        dashboard_server.DASHBOARD_STATE.unity_instance = original_instance


def test_missing_persisted_project_fails_closed_to_empty(tmp_path: Path, monkeypatch) -> None:
    project = make_unity_project(tmp_path / "UnityProject")
    selection_path = tmp_path / "user-data" / "config" / "selected-project.json"
    monkeypatch.setattr(dashboard_server, "PROJECT_SELECTION_PATH", selection_path)
    dashboard_server.persist_selected_project_path(project)
    (project / "ProjectSettings" / "ProjectVersion.txt").unlink()

    assert dashboard_server.load_persisted_selected_project_path() == ""


def test_invalid_selection_does_not_replace_current_or_persisted_state(tmp_path: Path, monkeypatch) -> None:
    project = make_unity_project(tmp_path / "UnityProject")
    selection_path = tmp_path / "user-data" / "config" / "selected-project.json"
    monkeypatch.setattr(dashboard_server, "PROJECT_SELECTION_PATH", selection_path)
    original_project = dashboard_server.DASHBOARD_STATE.selected_project_path
    original_instance = dashboard_server.DASHBOARD_STATE.unity_instance
    expected = dashboard_server.persist_selected_project_path(project)
    dashboard_server.DASHBOARD_STATE.selected_project_path = expected
    dashboard_server.DASHBOARD_STATE.unity_instance = project.name
    try:
        with TestClient(dashboard_server.app) as client:
            response = client.post("/api/state", json={"projectPath": str(tmp_path / "missing")})

        assert response.status_code == 400
        assert dashboard_server.DASHBOARD_STATE.selected_project_path == expected
        assert dashboard_server.load_persisted_selected_project_path() == expected
    finally:
        dashboard_server.DASHBOARD_STATE.selected_project_path = original_project
        dashboard_server.DASHBOARD_STATE.unity_instance = original_instance

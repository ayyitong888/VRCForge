import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import dashboard_server as server
from project_catalog_registration_service import ProjectCatalogRegistrationService
from project_recycle import validate_project


def unity_project(path):
    (path / "Assets").mkdir(parents=True)
    (path / "Packages").mkdir()
    (path / "Packages" / "manifest.json").write_text("{}")
    (path / "ProjectSettings").mkdir()
    (path / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1")
    return path


def test_recycle_target_requires_real_unity_folder_and_rejects_protected_ancestor(tmp_path):
    project = unity_project(tmp_path / "project")
    assert validate_project(str(project), ()) == project.resolve()
    with pytest.raises(ValueError, match="parent"):
        validate_project(str(project), (project / "program",))
    with pytest.raises(ValueError, match="complete Unity project"):
        validate_project(str(tmp_path), ())


@pytest.mark.skipif(os.name != "nt", reason="Windows project action")
def test_recycle_route_requires_confirmation_and_only_removes_selected_project(tmp_path, monkeypatch):
    project = unity_project(tmp_path / "project")
    other = unity_project(tmp_path / "other")
    path = project.as_posix()
    other_path = other.as_posix()
    index, prefs, selection, hub = (tmp_path / name for name in ("chat-projects.json", "prefs.json", "selection.json", "hub.json"))
    hub.write_text(json.dumps({"schema_version": "v1", "data": {path: {}, other_path: {}}}))
    index.write_text(json.dumps({"version": 1, "projectPaths": [path, other_path]}))
    prefs.write_text(json.dumps({"version": 2, "customProjects": [{"path": p, "projectType": "unity"} for p in (path, other_path)], "hiddenPaths": []}))
    selection.write_text(json.dumps({"selectedProjectPath": path}))
    monkeypatch.setattr(server, "chat_project_index_path", lambda: index)
    monkeypatch.setattr(server, "project_prefs_path", lambda: prefs)
    monkeypatch.setattr(server, "DASHBOARD_STATE", SimpleNamespace(selected_project_path=path, unity_instance="test"))
    monkeypatch.setattr(server, "PROJECT_SNAPSHOT_SELECTION", SimpleNamespace(
        project_snapshot_payload=lambda **kw: {"projects": [{"path": path, "projectType": "unity"}]},
        schedule_project_snapshot_refresh=lambda **kw: None, selection_path=selection,
    ))
    monkeypatch.setattr(server, "_enumerate_windows_process_names", lambda **kw: [])
    monkeypatch.setattr(server, "list_running_unity_processes", lambda **kw: [])
    monkeypatch.setattr(type(server.AGENT_GATEWAY.runtime_runs), "list_runs", lambda self, **kw: {"runs": []})
    monkeypatch.setattr(server, "PROJECT_CATALOG_REGISTRATIONS", ProjectCatalogRegistrationService(
        receipts_dir=tmp_path / "receipts", catalog_paths={"unityHub": [hub]},
    ))
    monkeypatch.setattr(server, "APP_SESSION_TOKEN", "recycle-test-token")
    async def no_log(*args, **kwargs):
        pass
    monkeypatch.setattr(server, "emit_log_async", no_log)
    headers = {"Authorization": "Bearer recycle-test-token"}
    client = TestClient(server.app, client=("127.0.0.1", 50000))
    url = "/api/projects/recycle"
    for invalid in ("delete", "DELETE", "Delete", "DeLeTe "):
        assert client.post(url, headers=headers, json={"path": path, "confirmation": invalid}).status_code == 422
    assert client.post(url, json={"path": path, "confirmation": "DeLeTe"}).status_code == 401
    assert project.exists()

    def blocked(_path: Path):
        raise OSError("Windows refused the recycle operation")
    monkeypatch.setattr(server, "recycle_project_folder", blocked)
    originals = {file: file.read_bytes() for file in (index, prefs, selection, hub)}
    assert client.post(url, headers=headers, json={"path": path, "confirmation": "DeLeTe"}).status_code == 409
    assert project.exists() and all(file.read_bytes() == content for file, content in originals.items())

    recovery = tmp_path / "recycled"
    monkeypatch.setattr(server, "recycle_project_folder", lambda p: p.rename(recovery))
    response = client.post(url, headers=headers, json={"path": path, "confirmation": "DeLeTe"})
    assert response.status_code == 200, response.text
    assert not project.exists() and recovery.exists() and other.exists()
    assert json.loads(index.read_bytes())["projectPaths"] == [other_path]
    assert [row["path"] for row in json.loads(prefs.read_bytes())["customProjects"]] == [other_path]
    assert json.loads(selection.read_bytes())["selectedProjectPath"] == ""
    assert list(json.loads(hub.read_bytes())["data"]) == [other_path]

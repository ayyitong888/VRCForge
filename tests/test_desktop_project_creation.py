import hashlib
import hmac
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dashboard_server as server
import project_manager_registration as managers
from project_lifecycle_service import ProjectLifecycleError, ProjectLifecycleService


def _avatar_template(root: Path) -> Path:
    template = root / "Avatar"
    (template / "Assets").mkdir(parents=True)
    (template / "Packages").mkdir()
    (template / "Packages" / "manifest.json").write_text("{}")
    (template / "ProjectSettings").mkdir()
    (template / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1\n")
    return template


def test_desktop_creates_once_and_can_retry_manager_registration(tmp_path, monkeypatch):
    template = _avatar_template(tmp_path / "templates")
    prefs = tmp_path / "prefs.json"
    monkeypatch.setattr(server, "PROJECT_LIFECYCLES", ProjectLifecycleService(
        prefs_path=prefs, receipts_dir=tmp_path / "receipts", template_roots=[template.parent],
    ))
    monkeypatch.setattr(server, "refresh_projects_after_lifecycle", lambda result: result)
    monkeypatch.setattr(server, "APP_SESSION_TOKEN", "desktop-project-test")
    registered = []

    async def no_log(*args, **kwargs):
        pass

    def register_managers(project: Path):
        assert (project / "ProjectSettings" / "ProjectVersion.txt").is_file()
        assert project.name in prefs.read_text()
        registered.append(project)
        return [{"catalog": "alcom", "status": "registered" if len(registered) == 2 else "manual_required"}]

    monkeypatch.setattr(server, "emit_log_async", no_log)
    monkeypatch.setattr(server, "register_desktop_project_managers", register_managers)
    client = TestClient(server.app, client=("127.0.0.1", 50000))
    auth = {"Authorization": "Bearer desktop-project-test"}

    def desktop_headers(method, route):
        proof = hmac.new(
            b"desktop-project-test", f"vrcforge.tauri-ipc-bridge.v1\n{method}\n{route}".encode(), hashlib.sha256,
        ).hexdigest()
        return {**auth, "X-VRCForge-Transport": "tauri-ipc-bridge", "X-VRCForge-Transport-Proof": proof}

    options = "/api/projects/create-options"
    create = "/api/projects/create"
    retry = "/api/projects/register-managers"
    choices = client.get(options, headers=desktop_headers("GET", options)).json()["templates"]
    assert choices[0]["unityVersion"] == "2022.3.22f1"
    assert choices[0]["source"] == "local"
    body = {"name": "Avatar demo", "parentPath": str(tmp_path), "templatePath": choices[0]["path"]}
    headers = desktop_headers("POST", create)
    assert client.post(create, headers=auth, json=body).status_code == 403
    assert client.post(create, headers=headers, json={**body, "name": "other/escape"}).status_code == 409
    assert client.post(create, headers=headers, json={**body, "templatePath": str(tmp_path)}).status_code == 409
    response = client.post(create, headers=headers, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    project = tmp_path / "Avatar demo"
    assert Path(result["projectPath"]) == project
    assert result["catalogs"] == [{"catalog": "alcom", "status": "manual_required"}]
    assert result["registeredInVRCForge"] and result["readbackVerified"]
    assert client.post(create, headers=headers, json=body).status_code == 409
    retry_headers = desktop_headers("POST", retry)
    assert client.post(retry, headers=retry_headers, json={"projectPath": str(tmp_path)}).status_code == 409
    retry_result = client.post(retry, headers=retry_headers, json={"projectPath": str(project)}).json()
    assert retry_result["catalogs"] == [{"catalog": "alcom", "status": "registered"}]
    assert registered == [project, project]


def test_desktop_template_options_only_folds_identical_trees(tmp_path):
    bundled = _avatar_template(tmp_path / "z" / "Programs" / "VRChat Creator Companion" / "Templates")
    (bundled / "Assets" / "example.txt").write_bytes(b"first")
    duplicate = tmp_path / "a" / "VRChatCreatorCompanion" / "VRCTemplates" / "avatar"
    alcom = tmp_path / "ALCOM" / "Templates" / "Avatar"
    local = tmp_path / "templates" / "Avatar"
    for copy in (duplicate, alcom, local):
        shutil.copytree(bundled, copy)
    (alcom / "Assets" / "example.txt").write_bytes(b"other")
    (local / "Assets" / "Empty").mkdir()
    service = ProjectLifecycleService(
        prefs_path=tmp_path / "prefs.json", receipts_dir=tmp_path / "receipts",
        template_roots=[duplicate.parent, local.parent, alcom.parent, bundled.parent],
    )

    choices = service.desktop_template_options(server.parse_editor_version)
    assert [(choice["path"], choice["source"]) for choice in choices] == [
        (str(bundled), "vccBundled"), (str(alcom), "alcom"), (str(local), "local"),
    ]
    assert len(service.status()["templates"]) == 4


def test_vpm_restore_failure_does_not_publish_an_incomplete_project(tmp_path, monkeypatch):
    template = _avatar_template(tmp_path)
    (template / "Packages" / "vpm-manifest.json").write_text('{"dependencies":{"com.vrchat.avatars":{"version":"3.10.6"}}}')
    service = ProjectLifecycleService(prefs_path=tmp_path / "prefs.json", receipts_dir=tmp_path / "receipts", template_roots=[template])
    monkeypatch.setattr(managers, "_vrc_get_executable", lambda entries: None)
    monkeypatch.setattr(managers, "_vpm_executable", lambda: None)

    with pytest.raises(ProjectLifecycleError, match="restore this template"):
        service.create_installed_project(
            "New Avatar", str(tmp_path), str(template),
            resolve_packages=lambda path: managers.resolve_template_packages(path, []),
        )
    assert list(tmp_path.iterdir()) == [template]

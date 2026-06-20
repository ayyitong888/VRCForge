from __future__ import annotations

import tarfile
from pathlib import Path

from fastapi.testclient import TestClient

import dashboard_server


def write_tar_member(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    import io

    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, fileobj=io.BytesIO(data))


def make_project(root: Path) -> None:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "Assets" / "Avatar.prefab").write_text("%YAML prefab", encoding="utf-8")
    (root / "Packages" / "manifest.json").write_text('{"dependencies":{}}', encoding="utf-8")
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1", encoding="utf-8")


def make_unitypackage(path: Path) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        write_tar_member(archive, "abc/pathname", b"Assets/Outfits/Dress.prefab")
        write_tar_member(archive, "abc/asset", b"SECRET_ASSET_BYTES")


def test_golden_path_preflight_app_endpoints_and_gateway_registration(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_project(project)
    package = tmp_path / "Dress.unitypackage"
    make_unitypackage(package)
    original_index_dir = dashboard_server.PROJECT_MEMORY_INDEX_DIR
    dashboard_server.PROJECT_MEMORY_INDEX_DIR = tmp_path / "indexes"
    try:
        with TestClient(dashboard_server.app) as client:
            index_response = client.post("/api/app/project-index/scan", json={"projectPath": str(project)})
            package_response = client.post("/api/app/outfit-packages/inspect", json={"packagePath": str(package)})
    finally:
        dashboard_server.PROJECT_MEMORY_INDEX_DIR = original_index_dir

    assert index_response.status_code == 200
    index_payload = index_response.json()
    assert index_payload["ok"] is True
    assert index_payload["schema"] == "vrcforge.project_memory_index.v1"
    assert index_payload["summary"]["addedFiles"] >= 3
    assert index_payload["privacy"]["binaryAssetContentsReturned"] is False

    assert package_response.status_code == 200
    package_payload = package_response.json()
    assert package_payload["ok"] is True
    assert package_payload["schema"] == "vrcforge.outfit_package_inspection.v1"
    assert package_payload["summary"]["prefabCandidateCount"] == 1
    assert package_payload["privacy"]["readsAssetBinaryContents"] is False
    assert "SECRET_ASSET_BYTES" not in str(package_payload)

    manifest = dashboard_server.AGENT_GATEWAY.build_manifest()
    tool_names = {tool["name"] for tool in manifest["tools"]}
    write_targets = {target["name"] for target in manifest["writeTargets"]}
    assert "vrcforge_scan_project_index" in tool_names
    assert "vrcforge_inspect_outfit_package" in tool_names
    assert "vrcforge_scan_project_index" not in write_targets
    assert "vrcforge_inspect_outfit_package" not in write_targets

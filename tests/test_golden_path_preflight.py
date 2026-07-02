from __future__ import annotations

import tarfile
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dashboard_server
from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry


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


def make_zip_with_unitypackages(path: Path) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr("Product/MaterialPack.unitypackage", b"SECRET_MATERIAL_PACKAGE")
        archive.writestr("Product/Dress_Milltina.unitypackage", b"SECRET_DRESS_PACKAGE")


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
            plan_response = client.post(
                "/api/app/outfit-imports/plan",
                json={"packagePath": str(package), "projectPath": str(project)},
            )
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

    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["ok"] is True
    assert plan_payload["schema"] == "vrcforge.outfit_import_plan.v1"
    assert plan_payload["plan"]["kind"] == "unitypackage_import"
    assert plan_payload["plan"]["readyToApply"] is True
    assert plan_payload["plan"]["requiresApproval"] is True
    assert plan_payload["plan"]["requiresCheckpoint"] is True
    assert plan_payload["plan"]["rollbackProofRequired"] is True
    assert plan_payload["plan"]["writeTarget"] == "vrcforge_import_outfit_package"
    assert "SECRET_ASSET_BYTES" not in str(plan_payload)

    manifest = dashboard_server.AGENT_GATEWAY.build_manifest()
    tool_names = {tool["name"] for tool in manifest["tools"]}
    write_targets = {target["name"] for target in manifest["writeTargets"]}
    assert "vrcforge_scan_project_index" in tool_names
    assert "vrcforge_inspect_outfit_package" in tool_names
    assert "vrcforge_plan_outfit_import" in tool_names
    assert "vrcforge_diagnose_package_install_errors" in tool_names
    assert "vrcforge_scan_project_index" not in write_targets
    assert "vrcforge_inspect_outfit_package" not in write_targets
    assert "vrcforge_plan_outfit_import" not in write_targets
    assert "vrcforge_import_outfit_package" in write_targets


def test_outfit_import_handler_resolves_unity_project_root(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "UnityProject"
    make_project(project)
    package = tmp_path / "Dress.unitypackage"
    make_unitypackage(package)
    seen: dict[str, str] = {}

    def fake_import(params: dict[str, object]) -> dict[str, object]:
        seen["projectPath"] = str(params.get("projectPath") or "")
        seen["unityPackagePath"] = str(params.get("unityPackagePath") or "")
        return {"ok": True, "importedAssetCount": 1}

    monkeypatch.setattr(dashboard_server, "import_unitypackage_sync", fake_import)

    payload = dashboard_server.import_outfit_package_sync({"packagePath": str(package), "projectPath": str(project)})

    assert payload["ok"] is True
    assert payload["kind"] == "unitypackage_import"
    assert seen["projectPath"] == str(project.resolve())
    assert seen["unityPackagePath"] == str(package.resolve())


def test_outfit_import_handler_extracts_zip_queue_in_order(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "milltina"
    make_project(project)
    package = tmp_path / "DressBundle.zip"
    make_zip_with_unitypackages(package)
    seen: list[tuple[str, str]] = []

    def fake_import(params: dict[str, object]) -> dict[str, object]:
        unity_package_path = Path(str(params.get("unityPackagePath") or ""))
        seen.append((unity_package_path.name, unity_package_path.read_text(encoding="utf-8")))
        return {"ok": True, "importedAssetCount": 1}

    monkeypatch.setattr(dashboard_server, "import_unitypackage_sync", fake_import)

    payload = dashboard_server.import_outfit_package_sync({"packagePath": str(package), "projectPath": str(project), "baseAvatarName": "Milltina"})

    assert payload["ok"] is True
    assert payload["kind"] == "unitypackage_import_sequence"
    assert [item[1] for item in seen] == ["SECRET_MATERIAL_PACKAGE", "SECRET_DRESS_PACKAGE"]
    assert [item["role"] for item in payload["unityImports"]] == ["support", "target"]


def test_outfit_zip_queue_blocks_high_ratio_nested_unitypackage(tmp_path: Path) -> None:
    package = tmp_path / "DressBundle.zip"
    temp_root = tmp_path / "extract"
    temp_root.mkdir()
    with zipfile.ZipFile(package, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Dress.unitypackage", b"\0" * (2 * 1024 * 1024))

    with pytest.raises(dashboard_server.AgentGatewayError, match="compression ratio"):
        dashboard_server._extract_unitypackage_from_zip(package, "Dress.unitypackage", temp_root)


def test_loose_outfit_target_folder_cannot_escape_assets(tmp_path: Path) -> None:
    project = tmp_path / "milltina"
    make_project(project)

    with pytest.raises(dashboard_server.AgentGatewayError, match="targetFolder"):
        dashboard_server._resolve_import_target_folder(project, "Assets/../ProjectSettings")


def test_outfit_import_handler_skips_installed_shader_support_package(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "milltina"
    make_project(project)
    (project / "Packages" / "manifest.json").write_text('{"dependencies":{"jp.lilxyzw.liltoon":"1.8.0"}}', encoding="utf-8")
    package = tmp_path / "Milltina_Slingshot_Swimsuit.zip"
    material_zip = tmp_path / "Slingshot_Material_and_textures.zip"
    with zipfile.ZipFile(package, mode="w") as archive:
        archive.writestr("Milltina Slingshot Swimsuit.unitypackage", b"SECRET_DRESS_PACKAGE")
    with zipfile.ZipFile(material_zip, mode="w") as archive:
        archive.writestr("lilToon_1.7.3.unitypackage", b"SECRET_SHADER_PACKAGE")
        archive.writestr("Slingshot textures.unitypackage", b"SECRET_MATERIAL_PACKAGE")
    seen: list[str] = []

    def fake_import(params: dict[str, object]) -> dict[str, object]:
        unity_package_path = Path(str(params.get("unityPackagePath") or ""))
        seen.append(unity_package_path.read_text(encoding="utf-8"))
        return {"ok": True, "importedAssetCount": 1}

    monkeypatch.setattr(dashboard_server, "import_unitypackage_sync", fake_import)

    payload = dashboard_server.import_outfit_package_sync({"packagePath": str(package), "projectPath": str(project), "baseAvatarName": "Milltina"})

    assert payload["ok"] is True
    assert [item["role"] for item in payload["unityImports"]] == ["support", "target"]
    assert seen == ["SECRET_MATERIAL_PACKAGE", "SECRET_DRESS_PACKAGE"]
    assert len(payload["skippedUnityImports"]) == 1
    assert payload["skippedUnityImports"][0]["path"] == "lilToon_1.7.3.unitypackage"
    assert payload["skippedUnityImports"][0]["dependencyId"] == "liltoon"


def test_sub_agent_endpoint_runs_project_index_worker(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "UnityProject"
    make_project(project)
    monkeypatch.setattr(dashboard_server, "PROJECT_MEMORY_INDEX_DIR", tmp_path / "indexes")
    registry = SubAgentTaskRegistry(
        tmp_path / "sub-agents",
        roles=[SubAgentRole("project_index_review", "Project", "Read local project index.")],
        handlers={"project_index_review": dashboard_server.run_project_index_sub_agent},
    )
    monkeypatch.setattr(dashboard_server, "SUB_AGENT_REGISTRY", registry)

    with TestClient(dashboard_server.app) as client:
        created = client.post(
            "/api/app/sub-agents",
            json={
                "role": "project_index_review",
                "displayName": "Kikyo",
                "task": "Scan the project index.",
                "projectPath": str(project),
                "params": {"projectPath": str(project)},
            },
        )
        assert created.status_code == 200
        task_id = created.json()["task"]["id"]

        deadline = time.time() + 5
        payload = client.get(f"/api/app/sub-agents/{task_id}").json()
        while payload["task"]["status"] not in {"completed", "failed"} and time.time() < deadline:
            time.sleep(0.05)
            payload = client.get(f"/api/app/sub-agents/{task_id}").json()

    assert payload["task"]["status"] == "completed"
    assert payload["task"]["displayName"] == "Kikyo"
    assert payload["task"]["result"]["projectIndex"]["schema"] == "vrcforge.project_memory_index.v1"
    assert payload["task"]["result"]["projectIndex"]["privacy"]["binaryAssetContentsReturned"] is False
    assert payload["task"]["events"]


def test_tool_registry_v1_exposes_read_tools_and_supervised_writes() -> None:
    registry = dashboard_server.AGENT_GATEWAY.build_tool_registry()
    assert registry["ok"] is True
    assert registry["schema"] == "vrcforge.tool_registry.v1"
    by_name = {tool["name"]: tool for tool in registry["tools"]}

    assert by_name["vrcforge_scan_project_index"]["risk"] == "read_only"
    assert by_name["vrcforge_scan_project_index"]["requiresApproval"] is False
    assert by_name["vrcforge_scan_project_index"]["availableInMcp"] is True
    assert by_name["vrcforge_import_outfit_package"]["risk"] == "write_request"
    assert by_name["vrcforge_import_outfit_package"]["requiresApproval"] is True
    assert by_name["vrcforge_import_outfit_package"]["requiresCheckpoint"] is True
    assert by_name["vrcforge_import_outfit_package"]["directTool"] is False
    assert "vrcforge_apply_approved" not in by_name

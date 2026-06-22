from __future__ import annotations

import json
import tarfile
import zipfile
from pathlib import Path

from outfit_import_planner import build_outfit_import_plan


def write_tar_member(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    import io

    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, fileobj=io.BytesIO(data))


def make_unitypackage(path: Path) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        write_tar_member(archive, "0001/pathname", b"Assets/Outfits/Dress.prefab")
        write_tar_member(archive, "0001/asset", b"SECRET_PREFAB_PAYLOAD")
        write_tar_member(archive, "0002/pathname", b"Assets/Outfits/Textures/body.png")
        write_tar_member(archive, "0002/asset", b"SECRET_TEXTURE_PAYLOAD")


def make_liltoon_hint_unitypackage(path: Path) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        write_tar_member(archive, "0001/pathname", b"Assets/Outfits/lilToon_Dress.prefab")
        write_tar_member(archive, "0001/asset", b"SECRET_PREFAB_PAYLOAD")
        write_tar_member(archive, "0002/pathname", b"Assets/Outfits/Materials/lilToon_Dress.mat")
        write_tar_member(archive, "0002/asset", b"SECRET_MATERIAL_PAYLOAD")


def make_zip_with_unitypackages(path: Path) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr("Product/MaterialPack.unitypackage", b"SECRET_MATERIAL_PACKAGE")
        archive.writestr("Product/Dress_Milltina.unitypackage", b"SECRET_DRESS_PACKAGE")


def make_project(root: Path, dependencies: dict[str, str] | None = None) -> None:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "Packages" / "manifest.json").write_text(json.dumps({"dependencies": dependencies or {}}), encoding="utf-8")
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1", encoding="utf-8")


def test_direct_unitypackage_plan_is_supervised_and_payload_safe(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    make_project(project)
    package = tmp_path / "Dress.unitypackage"
    make_unitypackage(package)

    result = build_outfit_import_plan(package, project_path=project, base_avatar_name="Manuka")
    rendered = json.dumps(result, ensure_ascii=False)
    plan = result["plan"]

    assert result["ok"] is True
    assert result["schema"] == "vrcforge.outfit_import_plan.v1"
    assert plan["kind"] == "unitypackage_import"
    assert plan["readyToApply"] is True
    assert plan["requiresApproval"] is True
    assert plan["requiresCheckpoint"] is True
    assert plan["validationAfterApply"] is True
    assert plan["rollbackProofRequired"] is True
    assert plan["writeTarget"] == "vrcforge_import_outfit_package"
    assert "Assets/Outfits/Dress.prefab" in plan["expectedAssetPaths"]
    assert "SECRET_PREFAB_PAYLOAD" not in rendered
    assert "SECRET_TEXTURE_PAYLOAD" not in rendered


def test_dependency_preflight_blocks_missing_shader_before_import(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    make_project(project)
    package = tmp_path / "lilToonDress.unitypackage"
    make_liltoon_hint_unitypackage(package)

    result = build_outfit_import_plan(package, project_path=project)
    rendered = json.dumps(result, ensure_ascii=False)
    preflight = result["dependencyPreflight"]
    liltoon = next(entry for entry in preflight["entries"] if entry["id"] == "liltoon")

    assert result["ok"] is True
    assert result["plan"]["readyToApply"] is False
    assert result["plan"]["writeTarget"] == ""
    assert preflight["readyForImport"] is False
    assert preflight["blockingMissingCount"] == 1
    assert liltoon["status"] == "missing"
    assert any(step["id"] == "dependency_repair" and step["enabled"] is True for step in result["plan"]["steps"])
    assert any("lilToon" in warning for warning in result["warnings"])
    assert "SECRET_MATERIAL_PAYLOAD" not in rendered


def test_dependency_preflight_allows_installed_shader_before_import(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    make_project(project, dependencies={"jp.lilxyzw.liltoon": "1.8.0"})
    package = tmp_path / "lilToonDress.unitypackage"
    make_liltoon_hint_unitypackage(package)

    result = build_outfit_import_plan(package, project_path=project)
    preflight = result["dependencyPreflight"]
    liltoon = next(entry for entry in preflight["entries"] if entry["id"] == "liltoon")

    assert result["plan"]["readyToApply"] is True
    assert result["plan"]["writeTarget"] == "vrcforge_import_outfit_package"
    assert preflight["readyForImport"] is True
    assert liltoon["status"] == "installed"


def test_zip_container_builds_ordered_import_queue_without_manual_extract(tmp_path: Path) -> None:
    project = tmp_path / "milltina"
    make_project(project)
    package = tmp_path / "DressBundle.zip"
    make_zip_with_unitypackages(package)

    result = build_outfit_import_plan(package, project_path=project, base_avatar_name="Milltina")
    rendered = json.dumps(result, ensure_ascii=False)
    plan = result["plan"]
    queue = plan["source"]["importQueue"]

    assert result["ok"] is True
    assert plan["kind"] == "unitypackage_import_sequence"
    assert plan["readyToApply"] is True
    assert plan["writeTarget"] == "vrcforge_import_outfit_package"
    assert [item["role"] for item in queue] == ["support", "target"]
    assert queue[0]["path"] == "Product/MaterialPack.unitypackage"
    assert queue[1]["path"] == "Product/Dress_Milltina.unitypackage"
    assert "SECRET_MATERIAL_PACKAGE" not in rendered
    assert "SECRET_DRESS_PACKAGE" not in rendered


def test_zip_container_queues_sibling_material_zip_first(tmp_path: Path) -> None:
    project = tmp_path / "milltina"
    make_project(project)
    package = tmp_path / "Dress_Milltina.zip"
    material_zip = tmp_path / "Dress_MaterialPack.zip"
    with zipfile.ZipFile(package, mode="w") as archive:
        archive.writestr("Dress_Milltina.unitypackage", b"SECRET_DRESS_PACKAGE")
    with zipfile.ZipFile(material_zip, mode="w") as archive:
        archive.writestr("Dress_MaterialPack.unitypackage", b"SECRET_MATERIAL_PACKAGE")

    result = build_outfit_import_plan(package, project_path=project, base_avatar_name="Milltina")
    queue = result["plan"]["source"]["importQueue"]

    assert result["plan"]["kind"] == "unitypackage_import_sequence"
    assert [item["role"] for item in queue] == ["support", "target"]
    assert queue[0]["containerPath"].endswith("Dress_MaterialPack.zip")
    assert queue[0]["path"] == "Dress_MaterialPack.unitypackage"
    assert queue[1]["path"] == "Dress_Milltina.unitypackage"


def test_shader_unitypackage_inside_support_zip_is_support_role(tmp_path: Path) -> None:
    project = tmp_path / "milltina"
    make_project(project)
    package = tmp_path / "Dress_Milltina.zip"
    material_zip = tmp_path / "Dress_MaterialPack.zip"
    with zipfile.ZipFile(package, mode="w") as archive:
        archive.writestr("Dress_Milltina.unitypackage", b"SECRET_DRESS_PACKAGE")
    with zipfile.ZipFile(material_zip, mode="w") as archive:
        archive.writestr("lilToon_1.7.3.unitypackage", b"SECRET_SHADER_PACKAGE")
        archive.writestr("Dress_Textures.unitypackage", b"SECRET_MATERIAL_PACKAGE")

    result = build_outfit_import_plan(package, project_path=project, base_avatar_name="Milltina")
    queue = result["plan"]["source"]["importQueue"]

    assert [item["role"] for item in queue] == ["support", "support", "target"]
    assert {item["path"] for item in queue[:2]} == {"Dress_Textures.unitypackage", "lilToon_1.7.3.unitypackage"}


def test_installed_shader_dependency_support_package_is_skipped_from_zip_queue(tmp_path: Path) -> None:
    project = tmp_path / "milltina"
    make_project(project)
    (project / "Packages" / "jp.lilxyzw.liltoon").mkdir()
    package = tmp_path / "Milltina_Slingshot_Swimsuit.zip"
    material_zip = tmp_path / "Slingshot_Material_and_textures.zip"
    with zipfile.ZipFile(package, mode="w") as archive:
        archive.writestr("Milltina Slingshot Swimsuit.unitypackage", b"SECRET_DRESS_PACKAGE")
    with zipfile.ZipFile(material_zip, mode="w") as archive:
        archive.writestr("lilToon_1.7.3.unitypackage", b"SECRET_SHADER_PACKAGE")
        archive.writestr("Slingshot textures.unitypackage", b"SECRET_MATERIAL_PACKAGE")

    result = build_outfit_import_plan(package, project_path=project, base_avatar_name="Milltina")
    package_order = result["dependencyPreflight"]["packageOrder"]
    queue = result["plan"]["source"]["importQueue"]
    skipped = package_order["skippedInstalledSupportPackages"]

    assert result["plan"]["kind"] == "unitypackage_import_sequence"
    assert result["plan"]["readyToApply"] is True
    assert [item["path"] for item in queue] == ["Slingshot textures.unitypackage", "Milltina Slingshot Swimsuit.unitypackage"]
    assert skipped[0]["path"] == "lilToon_1.7.3.unitypackage"
    assert skipped[0]["skipReason"] == "already_installed_dependency"
    assert skipped[0]["dependencyId"] == "liltoon"


def test_sibling_material_package_is_queued_before_direct_outfit(tmp_path: Path) -> None:
    project = tmp_path / "milltina"
    make_project(project)
    package = tmp_path / "Dress_Milltina.unitypackage"
    material_package = tmp_path / "Dress_Materials.unitypackage"
    make_unitypackage(package)
    make_unitypackage(material_package)

    result = build_outfit_import_plan(package, project_path=project, base_avatar_name="Milltina")
    queue = result["plan"]["source"]["importQueue"]

    assert result["plan"]["kind"] == "unitypackage_import_sequence"
    assert result["plan"]["readyToApply"] is True
    assert [Path(item.get("actualPackagePath", "")).name for item in queue] == ["Dress_Materials.unitypackage", "Dress_Milltina.unitypackage"]


def test_avatar_compatibility_mismatch_blocks_import_request(tmp_path: Path) -> None:
    project = tmp_path / "milltina"
    make_project(project)
    package = tmp_path / "Dress_Manuka.unitypackage"
    make_unitypackage(package)

    result = build_outfit_import_plan(package, project_path=project, base_avatar_name="Milltina")
    compatibility = result["dependencyPreflight"]["compatibility"]

    assert result["plan"]["readyToApply"] is False
    assert result["plan"]["writeTarget"] == ""
    assert compatibility["status"] == "mismatch"
    assert compatibility["blockingBeforeImport"] is True


def test_avatar_compatibility_does_not_match_short_alias_inside_words(tmp_path: Path) -> None:
    project = tmp_path / "milltina"
    make_project(project)
    package = tmp_path / "GenericDress.unitypackage"
    with tarfile.open(package, mode="w:gz") as archive:
        write_tar_member(archive, "0001/pathname", b"Assets/Outfits/Textures/emission.png")
        write_tar_member(archive, "0001/asset", b"SECRET_TEXTURE_PAYLOAD")

    result = build_outfit_import_plan(package, project_path=project, base_avatar_name="Milltina")
    compatibility = result["dependencyPreflight"]["compatibility"]

    assert compatibility["status"] == "unknown"
    assert "sio" not in compatibility["detectedAvatarNames"]


def test_zip_container_without_unitypackage_needs_manual_review(tmp_path: Path) -> None:
    package = tmp_path / "BoothProduct.zip"
    with zipfile.ZipFile(package, mode="w") as archive:
        archive.writestr("Product/Readme.txt", b"NO_UNITY_PACKAGE")

    result = build_outfit_import_plan(package)
    plan = result["plan"]

    assert result["ok"] is False
    assert plan["kind"] == "manual_review"
    assert plan["readyToApply"] is False
    assert plan["writeTarget"] == ""
    assert any("No importable UnityPackage" in warning or "No UnityPackage" in warning for warning in result["warnings"])
    assert "NO_UNITY_PACKAGE" not in json.dumps(result, ensure_ascii=False)


def test_loose_prefab_plan_targets_assets_folder_without_file_contents(tmp_path: Path) -> None:
    folder = tmp_path / "LooseOutfit"
    (folder / "Textures").mkdir(parents=True)
    (folder / "Dress.prefab").write_text("SECRET_PREFAB_TEXT", encoding="utf-8")
    (folder / "Textures" / "body.png").write_bytes(b"SECRET_TEXTURE_BYTES")

    result = build_outfit_import_plan(folder, target_folder="Assets/VRCForge/ImportedOutfits/Dress")
    rendered = json.dumps(result, ensure_ascii=False)
    plan = result["plan"]

    assert result["ok"] is True
    assert plan["kind"] == "loose_prefab_copy"
    assert plan["readyToApply"] is True
    assert plan["writeTarget"] == "vrcforge_import_outfit_package"
    assert "Assets/VRCForge/ImportedOutfits/Dress/Dress.prefab" in plan["expectedAssetPaths"]
    assert "SECRET_PREFAB_TEXT" not in rendered
    assert "SECRET_TEXTURE_BYTES" not in rendered

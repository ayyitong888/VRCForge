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


def make_project(root: Path) -> None:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
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


def test_zip_container_plan_requires_extract_before_apply(tmp_path: Path) -> None:
    package = tmp_path / "BoothProduct.zip"
    with zipfile.ZipFile(package, mode="w") as archive:
        archive.writestr("Product/Dress.unitypackage", b"SECRET_NESTED_PACKAGE_PAYLOAD")

    result = build_outfit_import_plan(package)
    plan = result["plan"]

    assert result["ok"] is True
    assert plan["kind"] == "unitypackage_container_manual_extract"
    assert plan["readyToApply"] is False
    assert plan["writeTarget"] == ""
    assert any("extract it first" in warning for warning in result["warnings"])
    assert "SECRET_NESTED_PACKAGE_PAYLOAD" not in json.dumps(result, ensure_ascii=False)


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

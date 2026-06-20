from __future__ import annotations

import json
import tarfile
import zipfile
from pathlib import Path

from outfit_package_inspector import inspect_outfit_package


def write_tar_member(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, fileobj=__import__("io").BytesIO(data))


def make_unitypackage(path: Path) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        write_tar_member(archive, "0001/pathname", b"Assets/Outfits/Dress.prefab")
        write_tar_member(archive, "0001/asset", b"SECRET_PREFAB_PAYLOAD_SHOULD_NOT_APPEAR")
        write_tar_member(archive, "0002/pathname", b"Assets/Outfits/Textures/body.png")
        write_tar_member(archive, "0002/asset", b"SECRET_TEXTURE_PAYLOAD_SHOULD_NOT_APPEAR")


def test_unitypackage_inspection_reads_pathnames_not_asset_payload(tmp_path: Path) -> None:
    package = tmp_path / "dress.unitypackage"
    make_unitypackage(package)

    result = inspect_outfit_package(package)
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is True
    assert result["schema"] == "vrcforge.outfit_package_inspection.v1"
    assert result["source"]["type"] == "unitypackage"
    assert result["summary"]["unityPackageCount"] == 1
    assert result["summary"]["prefabCandidateCount"] == 1
    assert result["summary"]["textureCount"] == 1
    assert result["privacy"]["readsAssetBinaryContents"] is False
    assert result["warnings"] == []
    assert "Assets/Outfits/Dress.prefab" in rendered
    assert "SECRET_PREFAB_PAYLOAD_SHOULD_NOT_APPEAR" not in rendered
    assert "SECRET_TEXTURE_PAYLOAD_SHOULD_NOT_APPEAR" not in rendered


def test_booth_zip_lists_nested_unitypackage_without_reading_payload(tmp_path: Path) -> None:
    package = tmp_path / "product.zip"
    with zipfile.ZipFile(package, mode="w") as archive:
        archive.writestr("Product/Dress.unitypackage", b"SECRET_NESTED_PACKAGE_PAYLOAD")
        archive.writestr("Product/preview.png", b"SECRET_PREVIEW_BYTES")
        archive.writestr("readme.txt", "manual")

    result = inspect_outfit_package(package)
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is True
    assert result["source"]["type"] == "zip"
    assert result["summary"]["unityPackageCount"] == 1
    assert result["summary"]["textureCount"] == 1
    assert result["unityPackages"][0]["path"] == "Product/Dress.unitypackage"
    assert "SECRET_NESTED_PACKAGE_PAYLOAD" not in rendered
    assert "SECRET_PREVIEW_BYTES" not in rendered


def test_zip_inspection_skips_unsafe_and_duplicate_entries(tmp_path: Path) -> None:
    package = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(package, mode="w") as archive:
        archive.writestr("../escape.prefab", "bad")
        archive.writestr("C:/absolute.prefab", "bad")
        archive.writestr("Product/Dress.prefab", "ok")
        archive.writestr("product/dress.prefab", "duplicate")

    result = inspect_outfit_package(package)

    assert result["ok"] is True
    assert result["summary"]["unsafeEntryCount"] == 2
    assert result["summary"]["duplicateEntryCount"] == 1
    assert result["summary"]["prefabCandidateCount"] == 1
    assert result["prefabCandidates"][0]["path"] == "Product/Dress.prefab"
    assert any("unsafe archive" in warning for warning in result["warnings"])
    assert any("duplicate archive" in warning for warning in result["warnings"])


def test_loose_prefab_texture_folder_reports_candidates_without_file_contents(tmp_path: Path) -> None:
    folder = tmp_path / "LooseOutfit"
    (folder / "Textures").mkdir(parents=True)
    (folder / "Materials").mkdir()
    (folder / "Dress.prefab").write_text("SECRET_PREFAB_TEXT_SHOULD_NOT_APPEAR", encoding="utf-8")
    (folder / "Textures" / "body.png").write_bytes(b"SECRET_PNG_BYTES_SHOULD_NOT_APPEAR")
    (folder / "Materials" / "body.mat").write_text("SECRET_MAT_TEXT_SHOULD_NOT_APPEAR", encoding="utf-8")
    (folder / "model.fbx").write_bytes(b"SECRET_FBX_BYTES_SHOULD_NOT_APPEAR")

    result = inspect_outfit_package(folder)
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is True
    assert result["source"]["type"] == "folder"
    assert result["summary"]["prefabCandidateCount"] == 1
    assert result["summary"]["textureCount"] == 1
    assert result["summary"]["materialCount"] == 1
    assert result["summary"]["modelCount"] == 1
    assert result["summary"]["importPlanKind"] == "loose_prefab_assets"
    assert "Loose prefab workflow requires explicit user confirmation" in result["warnings"][0]
    assert "SECRET_PREFAB_TEXT_SHOULD_NOT_APPEAR" not in rendered
    assert "SECRET_PNG_BYTES_SHOULD_NOT_APPEAR" not in rendered
    assert "SECRET_MAT_TEXT_SHOULD_NOT_APPEAR" not in rendered
    assert "SECRET_FBX_BYTES_SHOULD_NOT_APPEAR" not in rendered

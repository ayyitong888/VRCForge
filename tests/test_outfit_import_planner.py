from __future__ import annotations

import json
import tarfile
import zipfile
from pathlib import Path

import outfit_import_planner as planner
from outfit_import_planner import (
    build_outfit_import_plan,
    build_post_import_outfit_validation,
    detect_magenta_materials,
)


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


# --- Fix #2: post-import magenta / missing-shader validation -----------------


def test_detect_magenta_flags_missing_and_error_shaders() -> None:
    inventory = {
        "materials": [
            {"material_id": "m_ok", "renderer_path": "Body", "shader_name": "lilToon"},
            {"material_id": "m_missing", "renderer_path": "Dress", "shader_name": ""},
            {"material_id": "m_err", "renderer_path": "Hair", "shader_name": "Hidden/InternalErrorShader"},
        ]
    }

    magenta = detect_magenta_materials(inventory)
    reasons = {item["materialId"]: item["reason"] for item in magenta}

    assert set(reasons) == {"m_missing", "m_err"}
    assert reasons["m_missing"] == "missing_shader_reference"
    assert reasons["m_err"] == "internal_error_shader"


def test_post_import_validation_blocks_on_magenta_with_remediation() -> None:
    inventory = {"materials": [{"material_id": "m_missing", "renderer_path": "Dress", "shader_name": ""}]}

    report = build_post_import_outfit_validation(inventory, base_avatar_name="Milltina")

    assert report["schema"] == planner.POST_IMPORT_VALIDATION_SCHEMA
    assert report["status"] == "magenta_detected"
    assert report["blocking"] is True
    assert report["magentaCount"] == 1
    assert report["affectedRenderers"] == ["Dress"]
    assert report["remediation"]
    assert any("before" in step.lower() for step in report["remediation"])


def test_post_import_validation_passes_on_healthy_materials() -> None:
    inventory = {"materials": [{"material_id": "m_ok", "renderer_path": "Body", "shader_name": "Poiyomi/Toon"}]}

    report = build_post_import_outfit_validation(inventory)

    assert report["status"] == "ok"
    assert report["blocking"] is False
    assert report["magentaCount"] == 0
    assert report["remediation"] == []


# --- Fix #3: externalized / extensible avatar alias table --------------------


def _reset_alias_cache() -> None:
    planner._AVATAR_ALIAS_CACHE = None
    planner._AVATAR_ALIAS_CACHE_KEY = None


def test_avatar_alias_override_extends_builtin_table(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "aliases.json"
    override.write_text(json.dumps({"novaria": ["novaria"]}), encoding="utf-8")
    monkeypatch.setenv(planner.AVATAR_ALIAS_OVERRIDE_ENV, str(override))
    _reset_alias_cache()
    try:
        table = planner.avatar_compatibility_aliases()
        assert "milltina" in table  # builtin defaults still present
        assert "novaria" in table  # override merged in
        detected = planner.detect_avatar_aliases(["Assets/Outfits/Novaria_Dress.prefab"])
        assert "novaria" in detected
    finally:
        _reset_alias_cache()


def test_avatar_alias_override_accepts_wrapped_form(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "aliases.json"
    override.write_text(json.dumps({"avatars": {"novaria": ["novaria"]}}), encoding="utf-8")
    monkeypatch.setenv(planner.AVATAR_ALIAS_OVERRIDE_ENV, str(override))
    _reset_alias_cache()
    try:
        assert "novaria" in planner.avatar_compatibility_aliases()
    finally:
        _reset_alias_cache()


def test_avatar_alias_override_malformed_file_is_ignored(tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv(planner.AVATAR_ALIAS_OVERRIDE_ENV, str(bad))
    _reset_alias_cache()
    try:
        table = planner.avatar_compatibility_aliases()  # must not raise
        assert table["milltina"]
        assert "novaria" not in table
    finally:
        _reset_alias_cache()

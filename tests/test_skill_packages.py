from __future__ import annotations

import base64
import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from skill_packages import (
    LOCK_NAME,
    PUBLIC_KEY_NAME,
    SIGNATURE_NAME,
    ManifestValidationError,
    PackageIntegrityError,
    PackageSecurityError,
    PackageSignatureError,
    PackageUpdateError,
    SkillPackageError,
    SkillPackageService,
    canonical_json_bytes,
)


def make_skill_source(
    root: Path,
    *,
    skill_id: str = "com.example.avatar-helper",
    version: str = "1.0.0",
    author: str = "Example Author",
    content: str = "Inspect the avatar and produce a safe plan.",
    permissions: list[str] | None = None,
) -> Path:
    source = root / f"source-{version.replace('.', '-').replace('+', '-')}-{len(list(root.glob('source-*')))}"
    (source / "detectors").mkdir(parents=True)
    (source / "workflows").mkdir()
    (source / "actions").mkdir()
    manifest = {
        "id": skill_id,
        "name": "Avatar Helper",
        "version": version,
        "author": author,
        "description": "A package-service acceptance fixture.",
        "min_vrcforge_version": "0.3.0",
        "permissions": permissions or ["read_project", "unity_scan_scene"],
        "entrypoints": {
            "detect": "detectors/detect.json",
            "plan": "workflows/plan.md",
            "apply": "actions/apply.json",
        },
    }
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (source / "detectors" / "detect.json").write_text('{"kind":"avatar"}', encoding="utf-8")
    (source / "workflows" / "plan.md").write_text(content, encoding="utf-8")
    (source / "actions" / "apply.json").write_text('{"tool":"vrcforge_avatar_scan"}', encoding="utf-8")
    (source / "README.md").write_text("# Avatar Helper\n", encoding="utf-8")
    return source


def archive_files(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as archive:
        return {info.filename: archive.read(info) for info in archive.infolist() if not info.is_dir()}


def rewrite_archive(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def export_release(
    service: SkillPackageService,
    source: Path,
    output: Path,
    private_key_pem: bytes,
) -> Path:
    return service.export_release(source, output, private_key_pem).package_path


def test_read_only_avatar_audit_example_exports_and_preflights(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "examples" / "skill-packages" / "read-only-avatar-audit"
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.9.0-beta")

    package = service.export_dev(source, tmp_path / "read-only-avatar-audit.vsk").package_path
    preview = service.preflight_import(package).as_dict()

    assert preview["manifest"]["id"] == "community.examples.read-only-avatar-audit"
    assert preview["manifest"]["entrypoints"]["workflow"] == "workflows/read-only-avatar-audit.json"
    assert preview["risk_level"] == "low"
    assert preview["governance"]["importAllowed"] is True
    assert preview["dryRun"]["willWrite"] is False
    assert "read_project" in preview["permissions"]


def test_signed_release_roundtrip_filters_assets_and_installs_atomically(tmp_path: Path) -> None:
    store = tmp_path / "store"
    service = SkillPackageService(store, vrcforge_version="0.5.1-beta")
    source = make_skill_source(
        tmp_path,
        permissions=["read_project", "unity_modify_materials", "execute_shell"],
    )
    (source / ".env").write_text("API_KEY=do-not-package", encoding="utf-8")
    (source / "avatar.fbx").write_bytes(b"paid-avatar")
    (source / "texture.png").write_bytes(b"not-a-real-png")
    (source / "private.pem").write_text("-----BEGIN PRIVATE KEY-----\nsecret", encoding="utf-8")
    (source / "ordinary-config.json").write_text(
        '{"api_key":"test-only-secret-value-123456789"}',
        encoding="utf-8",
    )
    key_pair = service.generate_signing_keypair()

    exported = service.export_release(source, tmp_path / "avatar-helper.vsk", key_pair.private_key_pem)

    assert exported.signature_status == "signed"
    assert exported.signer_fingerprint == key_pair.fingerprint
    assert set(exported.excluded_files) >= {
        ".env",
        "avatar.fbx",
        "texture.png",
        "private.pem",
        "ordinary-config.json",
    }
    files = archive_files(exported.package_path)
    assert ".env" not in files
    assert "avatar.fbx" not in files
    assert LOCK_NAME in files and SIGNATURE_NAME in files and PUBLIC_KEY_NAME in files
    assert files[LOCK_NAME] == canonical_json_bytes(json.loads(files[LOCK_NAME]))

    preview = service.preflight_import(exported.package_path)
    assert preview.signature_status == "signed"
    assert preview.signer_fingerprint == key_pair.fingerprint
    assert preview.risk_level == "high"
    assert preview.permission_tiers["low"] == ("read_project",)
    assert preview.permission_tiers["medium"] == ("unity_modify_materials",)
    assert preview.permission_tiers["high"] == ("execute_shell",)
    assert preview.update_action == "new"

    installed = service.install(exported.package_path, source="unit-test")
    assert installed.changed is True
    assert installed.installed_path == store / "com.example.avatar-helper" / "versions" / "1.0.0"
    assert (installed.installed_path / "workflows" / "plan.md").is_file()
    registry = json.loads((store / "registry.json").read_text(encoding="utf-8"))
    entry = registry["skills"]["com.example.avatar-helper"]
    assert entry["version"] == "1.0.0"
    assert entry["author"] == "Example Author"
    assert entry["signature_status"] == "signed"
    assert entry["signer_fingerprint"] == key_pair.fingerprint
    assert entry["source"] == "unit-test"
    assert not list(store.glob(".registry.json.*.tmp"))


def test_signed_package_trust_is_separate_from_verification(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    key_pair = service.generate_signing_keypair()
    package = export_release(
        service,
        make_skill_source(tmp_path, permissions=["read_project"]),
        tmp_path / "signed.vsk",
        key_pair.private_key_pem,
    )

    preview = service.preflight_import(package).as_dict()

    assert preview["signature_status"] == "signed"
    assert preview["governance"]["signatureVerified"] is True
    assert preview["governance"]["verified"] is False
    assert preview["governance"]["signerTrustStatus"] == "untrusted"
    assert preview["governance"]["importAllowed"] is True
    assert preview["governance"]["safeMode"]["defaultEnabled"] is False

    trusted = service.trust_signer(key_pair.fingerprint, reason="local test signer")
    trusted_preview = service.preflight_import(package).as_dict()

    assert trusted["governance"]["trusted_signers"][key_pair.fingerprint]["reason"] == "local test signer"
    assert trusted_preview["governance"]["signerTrustStatus"] == "trusted"
    assert trusted_preview["governance"]["verified"] is False
    assert trusted_preview["governance"]["safeMode"]["defaultEnabled"] is True
    assert service.load_registry()["audit"][-1]["event"] == "skill_package_signer_trusted"


def test_revoked_signer_blocks_import_and_enable(tmp_path: Path) -> None:
    key_pair = SkillPackageService.generate_signing_keypair()
    build_service = SkillPackageService(tmp_path / "build-store", vrcforge_version="0.5.1")
    package = export_release(
        build_service,
        make_skill_source(tmp_path, permissions=["read_project"]),
        tmp_path / "signed.vsk",
        key_pair.private_key_pem,
    )
    blocked_import = SkillPackageService(tmp_path / "blocked-import", vrcforge_version="0.5.1")

    blocked_import.revoke_signer(key_pair.fingerprint, reason="compromised")
    preview = blocked_import.preflight_import(package).as_dict()

    assert preview["governance"]["signerTrustStatus"] == "revoked"
    assert preview["governance"]["importAllowed"] is False
    assert any("compromised" in item for item in preview["governance"]["blockingReasons"])
    with pytest.raises(PackageSecurityError, match="revoked"):
        blocked_import.install(package)

    installed_service = SkillPackageService(tmp_path / "installed-store", vrcforge_version="0.5.1")
    installed_service.install(package)
    installed_service.set_enabled("com.example.avatar-helper", False)
    revoked = installed_service.revoke_signer(key_pair.fingerprint, reason="compromised")

    assert revoked["disabledSkillIds"] == []
    with pytest.raises(PackageSecurityError, match="revoked"):
        installed_service.set_enabled("com.example.avatar-helper", True)
    assert installed_service.load_registry()["audit"][-1]["event"] == "skill_package_enable_blocked"


def test_blocklisted_package_blocks_import_and_enable(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    package = service.export_dev(make_skill_source(tmp_path), tmp_path / "dev.vsk").package_path
    preflight = service.preflight_import(package).as_dict()

    service.block_package(package_id="com.example.avatar-helper", reason="known bad recipe")
    blocked = service.preflight_import(package).as_dict()

    assert preflight["governance"]["importAllowed"] is True
    assert blocked["governance"]["importAllowed"] is False
    assert any("known bad recipe" in item for item in blocked["governance"]["blockingReasons"])
    with pytest.raises(PackageSecurityError, match="blocklisted"):
        service.install(package)

    installed_service = SkillPackageService(tmp_path / "installed-store", vrcforge_version="0.5.1")
    installed_service.install(package)
    installed_service.set_enabled("com.example.avatar-helper", False)
    disabled = installed_service.block_package(package_id="com.example.avatar-helper", reason="known bad recipe")

    assert disabled["disabledSkillIds"] == []
    with pytest.raises(PackageSecurityError, match="blocklisted"):
        installed_service.set_enabled("com.example.avatar-helper", True)


def test_safe_mode_disables_risky_imports_and_blocks_enable(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    risky_source = make_skill_source(tmp_path, permissions=["read_project", "unity_modify_materials"])
    package = service.export_dev(risky_source, tmp_path / "risky.vsk").package_path

    service.set_safe_mode(True, reason="test lockdown")
    preview = service.preflight_import(package).as_dict()
    installed = service.install(package)

    assert preview["governance"]["importAllowed"] is True
    assert preview["governance"]["enableAllowed"] is False
    assert preview["governance"]["safeMode"]["defaultEnabled"] is False
    assert preview["dryRun"]["willWrite"] is False
    assert preview["dryRun"]["wouldEnable"] is False
    assert installed.registry_entry["enabled"] is False
    assert installed.registry_entry["governance"]["safe_mode_disabled"] is True
    with pytest.raises(PackageSecurityError, match="safe mode"):
        service.set_enabled("com.example.avatar-helper", True)


def test_set_enabled_and_uninstall_package_keep_registry_and_metadata_in_sync(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    package = service.export_dev(make_skill_source(tmp_path), tmp_path / "helper.vsk").package_path
    service.install(package)
    service.set_enabled("com.example.avatar-helper", True)

    disabled = service.set_enabled("com.example.avatar-helper", False)

    assert disabled.changed is True
    assert disabled.registry_entry["enabled"] is False
    registry = service.load_registry()
    assert registry["skills"]["com.example.avatar-helper"]["enabled"] is False
    installed_metadata = json.loads((tmp_path / "store" / "com.example.avatar-helper" / "installed.json").read_text(encoding="utf-8"))
    assert installed_metadata["enabled"] is False
    assert installed_metadata["versions"] == ["1.0.0"]

    disabled_again = service.set_enabled("com.example.avatar-helper", False)
    assert disabled_again.changed is False

    removed = service.uninstall("com.example.avatar-helper")

    assert removed.changed is True
    assert removed.removed_versions == ("1.0.0",)
    assert removed.manifest["id"] == "com.example.avatar-helper"
    assert service.list_installed() == []
    assert not (tmp_path / "store" / "com.example.avatar-helper").exists()
    with pytest.raises(SkillPackageError, match="not installed"):
        service.set_enabled("com.example.avatar-helper", True)


def test_unsigned_dev_roundtrip_has_lock_but_no_signature(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    source = make_skill_source(tmp_path)
    exported = service.export_dev(source, tmp_path / "dev-package")

    assert exported.package_path.suffix == ".vsk"
    assert exported.signature_status == "dev"
    files = archive_files(exported.package_path)
    assert LOCK_NAME in files
    assert SIGNATURE_NAME not in files
    assert PUBLIC_KEY_NAME not in files
    preview = service.inspect_package(exported.package_path)
    assert preview.signature_status == "dev"
    assert preview.signer_fingerprint is None
    installed = service.install(exported.package_path)
    assert installed.registry_entry["signature_status"] == "dev"
    assert installed.registry_entry["enabled"] is False


def test_tampered_payload_and_undeclared_files_are_rejected(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    source = make_skill_source(tmp_path)
    key = service.generate_signing_keypair()
    package = export_release(service, source, tmp_path / "signed.vsk", key.private_key_pem)

    tampered = tmp_path / "tampered.vsk"
    files = archive_files(package)
    files["workflows/plan.md"] = b"tampered after signing"
    rewrite_archive(tampered, files)
    with pytest.raises(PackageIntegrityError, match="SHA-256 mismatch"):
        service.inspect_package(tampered)

    undeclared = tmp_path / "undeclared.vsk"
    files = archive_files(package)
    files["prompts/hidden.md"] = b"not in lock"
    rewrite_archive(undeclared, files)
    with pytest.raises(PackageIntegrityError, match="undeclared"):
        service.inspect_package(undeclared)


def test_signature_tamper_and_partial_signature_metadata_are_rejected(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    source = make_skill_source(tmp_path)
    key = service.generate_signing_keypair()
    package = export_release(service, source, tmp_path / "signed.vsk", key.private_key_pem)

    files = archive_files(package)
    files[SIGNATURE_NAME] = base64.b64encode(b"x" * 64)
    invalid_signature = tmp_path / "invalid-signature.vsk"
    rewrite_archive(invalid_signature, files)
    with pytest.raises(PackageSignatureError, match="verification failed"):
        service.inspect_package(invalid_signature)

    files = archive_files(package)
    files.pop(PUBLIC_KEY_NAME)
    partial = tmp_path / "partial-signature.vsk"
    rewrite_archive(partial, files)
    with pytest.raises(PackageSignatureError, match="both exist"):
        service.inspect_package(partial)


def test_export_requires_valid_manifest_semver_and_existing_entrypoints(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    source = make_skill_source(tmp_path)
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "1.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="semantic version"):
        service.export_dev(source, tmp_path / "bad.vsk")

    manifest["version"] = "1.0.0"
    manifest["entrypoints"]["plan"] = "workflows/missing.md"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="Entrypoint is missing"):
        service.export_dev(source, tmp_path / "missing.vsk")


def test_manifest_rejects_empty_reverse_domain_segments(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    source = make_skill_source(tmp_path)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["id"] = "com..takeover"
    with pytest.raises(ManifestValidationError, match="reverse-domain"):
        service.validate_manifest(manifest)


def test_manifest_secret_material_is_not_exported(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    source = make_skill_source(tmp_path)
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["api_key"] = "test-only-secret-token-123456789"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageSecurityError, match="manifest.json contains secret"):
        service.export_dev(source, tmp_path / "leak.vsk")


@pytest.mark.parametrize(
    "member",
    ["../escape", "/absolute", "C:/drive", "..\\backslash", "\\\\server\\share"],
)
def test_archive_traversal_absolute_drive_and_backslash_paths_are_rejected(
    tmp_path: Path,
    member: str,
) -> None:
    package = tmp_path / "unsafe.vsk"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(member, b"unsafe")
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    with pytest.raises(PackageSecurityError):
        service.inspect_package(package)


def test_zip_symlink_and_case_insensitive_duplicates_are_rejected(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    symlink_package = tmp_path / "symlink.vsk"
    with zipfile.ZipFile(symlink_package, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../outside")
    with pytest.raises(PackageSecurityError, match="Symlink"):
        service.inspect_package(symlink_package)

    duplicate_package = tmp_path / "duplicate.vsk"
    with zipfile.ZipFile(duplicate_package, "w") as archive:
        archive.writestr("README.md", b"one")
        archive.writestr("readme.md", b"two")
    with pytest.raises(PackageSecurityError, match="Duplicate"):
        service.inspect_package(duplicate_package)

    collision_package = tmp_path / "file-child-collision.vsk"
    with zipfile.ZipFile(collision_package, "w") as archive:
        archive.writestr("actions", b"file")
        archive.writestr("actions/apply.json", b"child")
    with pytest.raises(PackageSecurityError, match="conflicts with a child"):
        service.inspect_package(collision_package)


def test_zip_directory_metadata_and_case_insensitive_parent_collisions_are_rejected(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")

    directory_without_slash = tmp_path / "directory-mode-no-slash.vsk"
    with zipfile.ZipFile(directory_without_slash, "w") as archive:
        info = zipfile.ZipInfo("folder")
        info.create_system = 3
        info.external_attr = (stat.S_IFDIR | 0o755) << 16
        archive.writestr(info, b"")
    with pytest.raises(PackageSecurityError, match="must end with '/'"):
        service.inspect_package(directory_without_slash)

    case_parent_collision = tmp_path / "case-parent-collision.vsk"
    with zipfile.ZipFile(case_parent_collision, "w") as archive:
        archive.writestr("Actions/apply.json", b"child")
        archive.writestr("actions", b"file")
    with pytest.raises(PackageSecurityError, match="conflicts with a child"):
        service.inspect_package(case_parent_collision)


def test_zip_file_count_size_and_compression_ratio_limits_are_enforced(tmp_path: Path) -> None:
    count_package = tmp_path / "count.vsk"
    with zipfile.ZipFile(count_package, "w") as archive:
        for index in range(2):
            archive.writestr(f"file-{index}", b"x")
    count_service = SkillPackageService(tmp_path / "count-store", vrcforge_version="0.5.1", max_file_count=1)
    with pytest.raises(PackageSecurityError, match="file-count"):
        count_service.inspect_package(count_package)

    size_package = tmp_path / "size.vsk"
    with zipfile.ZipFile(size_package, "w") as archive:
        archive.writestr("large", b"x" * 11)
    size_service = SkillPackageService(tmp_path / "size-store", vrcforge_version="0.5.1", max_file_size=10)
    with pytest.raises(PackageSecurityError, match="size limit"):
        size_service.inspect_package(size_package)

    ratio_package = tmp_path / "ratio.vsk"
    with zipfile.ZipFile(ratio_package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("repeated", b"A" * 20_000)
    ratio_service = SkillPackageService(
        tmp_path / "ratio-store",
        vrcforge_version="0.5.1",
        max_compression_ratio=10,
    )
    with pytest.raises(PackageSecurityError, match="compression-ratio"):
        ratio_service.inspect_package(ratio_package)


def test_same_signer_updates_different_signer_and_unsigned_takeover_are_blocked(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    key_a = service.generate_signing_keypair()
    key_b = service.generate_signing_keypair()
    version_1 = make_skill_source(tmp_path, version="1.0.0")
    package_1 = export_release(service, version_1, tmp_path / "1.0.0.vsk", key_a.private_key_pem)
    service.install(package_1)

    version_11 = make_skill_source(tmp_path, version="1.1.0", content="safe update")
    package_11 = export_release(service, version_11, tmp_path / "1.1.0.vsk", key_a.private_key_pem)
    assert service.preflight_import(package_11).update_action == "update"
    service.install(package_11)
    assert service.load_registry()["skills"]["com.example.avatar-helper"]["version"] == "1.1.0"

    hostile_source = make_skill_source(tmp_path, version="1.2.0", content="hostile signer")
    hostile_package = export_release(service, hostile_source, tmp_path / "hostile.vsk", key_b.private_key_pem)
    with pytest.raises(PackageUpdateError, match="fingerprint"):
        service.preflight_import(hostile_package)

    unsigned_package = service.export_dev(hostile_source, tmp_path / "unsigned.vsk").package_path
    with pytest.raises(PackageUpdateError, match="cannot overwrite"):
        service.preflight_import(unsigned_package)

    renamed_author_source = make_skill_source(
        tmp_path,
        version="1.2.0",
        author="Different Author Identity",
        content="same signer but wrong author identity",
    )
    renamed_author_package = export_release(
        service,
        renamed_author_source,
        tmp_path / "renamed-author.vsk",
        key_a.private_key_pem,
    )
    with pytest.raises(PackageUpdateError, match="Author identity"):
        service.preflight_import(renamed_author_package)
    assert service.load_registry()["skills"]["com.example.avatar-helper"]["version"] == "1.1.0"


def test_unsigned_install_can_be_pinned_by_first_signed_update(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    source_1 = make_skill_source(tmp_path, version="1.0.0")
    service.install(service.export_dev(source_1, tmp_path / "dev.vsk").package_path)

    key = service.generate_signing_keypair()
    source_11 = make_skill_source(tmp_path, version="1.1.0")
    signed = export_release(service, source_11, tmp_path / "signed.vsk", key.private_key_pem)
    service.install(signed)
    entry = service.load_registry()["skills"]["com.example.avatar-helper"]
    assert entry["signature_status"] == "signed"
    assert entry["signer_fingerprint"] == key.fingerprint

    source_12 = make_skill_source(tmp_path, version="1.2.0")
    unsigned = service.export_dev(source_12, tmp_path / "unsigned-again.vsk").package_path
    with pytest.raises(PackageUpdateError, match="cannot overwrite"):
        service.install(unsigned)


def test_signed_update_recovers_legacy_author_identity_from_locked_manifest(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    key = service.generate_signing_keypair()
    package_1 = export_release(
        service,
        make_skill_source(tmp_path, version="1.0.0", author="Stable Author ID"),
        tmp_path / "legacy-author-one.vsk",
        key.private_key_pem,
    )
    service.install(package_1)

    registry_path = tmp_path / "store" / "registry.json"
    installed_path = tmp_path / "store" / "com.example.avatar-helper" / "installed.json"
    for metadata_path in (registry_path, installed_path):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        entry = metadata["skills"]["com.example.avatar-helper"] if metadata_path == registry_path else metadata
        entry.pop("author", None)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    package_2 = export_release(
        service,
        make_skill_source(tmp_path, version="1.1.0", author="Stable Author ID"),
        tmp_path / "legacy-author-two.vsk",
        key.private_key_pem,
    )
    result = service.install(package_2)

    assert result.registry_entry["author"] == "Stable Author ID"
    assert service.load_registry()["skills"]["com.example.avatar-helper"]["version"] == "1.1.0"


def test_downgrade_requires_dev_override_and_same_signer(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    key = service.generate_signing_keypair()
    source_1 = make_skill_source(tmp_path, version="1.0.0")
    source_2 = make_skill_source(tmp_path, version="2.0.0")
    package_1 = export_release(service, source_1, tmp_path / "one.vsk", key.private_key_pem)
    package_2 = export_release(service, source_2, tmp_path / "two.vsk", key.private_key_pem)
    service.install(package_1)
    service.install(package_2)

    with pytest.raises(PackageUpdateError, match="downgrade"):
        service.preflight_import(package_1)
    assert service.preflight_import(package_1, allow_downgrade=True, dev_mode=True).update_action == "downgrade"
    result = service.install(package_1, allow_downgrade=True, dev_mode=True)
    assert result.registry_entry["version"] == "1.0.0"


def test_same_version_with_different_contents_is_immutable(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    key = service.generate_signing_keypair()
    first = make_skill_source(tmp_path, version="1.0.0", content="first")
    second = make_skill_source(tmp_path, version="1.0.0", content="changed without version bump")
    first_package = export_release(service, first, tmp_path / "first.vsk", key.private_key_pem)
    second_package = export_release(service, second, tmp_path / "second.vsk", key.private_key_pem)
    service.install(first_package)
    with pytest.raises(PackageUpdateError, match="immutable"):
        service.preflight_import(second_package)


def test_noncanonical_lock_and_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    source = make_skill_source(tmp_path)
    package = service.export_dev(source, tmp_path / "dev.vsk").package_path
    files = archive_files(package)
    lock = json.loads(files[LOCK_NAME])
    files[LOCK_NAME] = json.dumps(lock, indent=2).encode("utf-8")
    noncanonical = tmp_path / "noncanonical.vsk"
    rewrite_archive(noncanonical, files)
    with pytest.raises(PackageIntegrityError, match="not canonical"):
        service.inspect_package(noncanonical)

    manifest = service.validate_manifest(json.loads((source / "manifest.json").read_text(encoding="utf-8")))
    duplicate_manifest = canonical_json_bytes(manifest)[:-1] + b',"id":"com.evil.takeover"}'
    files = archive_files(package)
    lock = json.loads(files[LOCK_NAME])
    lock["files"]["manifest.json"] = __import__("hashlib").sha256(duplicate_manifest).hexdigest()
    files["manifest.json"] = duplicate_manifest
    files[LOCK_NAME] = canonical_json_bytes(lock)
    duplicate_json = tmp_path / "duplicate-json.vsk"
    rewrite_archive(duplicate_json, files)
    with pytest.raises(PackageIntegrityError, match="Duplicate JSON key"):
        service.inspect_package(duplicate_json)


def test_import_rejects_manifest_secret_material_even_when_locked(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    source = make_skill_source(tmp_path)
    package = service.export_dev(source, tmp_path / "dev.vsk").package_path
    files = archive_files(package)
    manifest = json.loads(files["manifest.json"])
    manifest["api_key"] = "test-only-secret-token-123456789"
    manifest_bytes = canonical_json_bytes(manifest)
    lock = json.loads(files[LOCK_NAME])
    lock["files"]["manifest.json"] = hashlib.sha256(manifest_bytes).hexdigest()
    files["manifest.json"] = manifest_bytes
    files[LOCK_NAME] = canonical_json_bytes(lock)
    tampered = tmp_path / "secret-manifest.vsk"
    rewrite_archive(tampered, files)

    with pytest.raises(PackageSecurityError, match="manifest.json contains secret"):
        service.inspect_package(tampered)


def test_export_rejects_symlink_source_root_and_entrypoint_symlink(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    source = make_skill_source(tmp_path)
    source_link = tmp_path / "source-link"
    try:
        source_link.symlink_to(source, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable in this environment: {exc}")

    with pytest.raises(SkillPackageError, match="symlink"):
        service.export_dev(source_link, tmp_path / "source-link.vsk")

    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    plan_path = source / "workflows" / "plan.md"
    plan_path.unlink()
    plan_path.symlink_to(source / "README.md")

    with pytest.raises(ManifestValidationError, match="symlinks"):
        service.validate_manifest(manifest, package_root=source)


def test_install_registry_write_failure_rolls_back_installed_metadata_and_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    key = service.generate_signing_keypair()
    package_1 = export_release(
        service,
        make_skill_source(tmp_path, version="1.0.0"),
        tmp_path / "one.vsk",
        key.private_key_pem,
    )
    service.install(package_1)
    skill_root = tmp_path / "store" / "com.example.avatar-helper"
    installed_path = skill_root / "installed.json"
    old_installed = installed_path.read_bytes()

    package_2 = export_release(
        service,
        make_skill_source(tmp_path, version="1.1.0", content="registry failure update"),
        tmp_path / "two.vsk",
        key.private_key_pem,
    )
    original_atomic_write_json = SkillPackageService._atomic_write_json

    def fail_registry_write(path: Path, value: object) -> None:
        if Path(path) == service.registry_path:
            raise OSError("simulated registry write failure")
        original_atomic_write_json(path, value)

    monkeypatch.setattr(SkillPackageService, "_atomic_write_json", staticmethod(fail_registry_write))

    with pytest.raises(OSError, match="registry write failure"):
        service.install(package_2)

    assert installed_path.read_bytes() == old_installed
    assert not (skill_root / "versions" / "1.1.0").exists()
    assert service.load_registry()["skills"]["com.example.avatar-helper"]["version"] == "1.0.0"


def test_registry_and_installed_metadata_are_validated(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.5.1")
    package = service.export_dev(make_skill_source(tmp_path), tmp_path / "dev.vsk").package_path
    service.install(package)

    registry = json.loads((tmp_path / "store" / "registry.json").read_text(encoding="utf-8"))
    registry["skills"]["com.example.avatar-helper"]["lock_sha256"] = "not-a-sha"
    (tmp_path / "store" / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(PackageIntegrityError, match="lock SHA-256"):
        service.load_registry()

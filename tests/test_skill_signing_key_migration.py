from __future__ import annotations

import base64
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from skill_packages import SkillPackageService
from skill_signing_key_migration import (
    PRIVATE_KEY_FILENAME,
    PUBLIC_KEY_FILENAME,
    RECOVERY_SCHEMA,
    SkillSigningKeyMigrationError,
    SkillSigningKeyMigrationService,
)


PASSPHRASE = "correct horse battery staple"


def make_owner(
    root: Path,
    *,
    create_key: bool = False,
    designate: bool = True,
    publisher: str = "VRCForge",
) -> tuple[SkillPackageService, SkillSigningKeyMigrationService, object]:
    service = SkillPackageService(root / "skill-packages", vrcforge_version="1.7.9")
    owner = SkillSigningKeyMigrationService(service, root / "signing")
    pair = None
    if create_key:
        pair = service.generate_signing_keypair()
        service.save_signing_keypair(pair, owner.private_key_path, owner.public_key_path)
        if designate:
            service.designate_official_signer(pair.fingerprint, publisher=publisher)
    return service, owner, pair


def test_status_does_not_generate_an_official_signing_key(tmp_path: Path) -> None:
    _, owner, _ = make_owner(tmp_path / "empty")

    assert owner.status() == {
        "ok": True,
        "key": {
            "exists": False,
            "fingerprint": "",
            "publisher": "",
            "publicKeyPath": str(tmp_path / "empty" / "signing" / PUBLIC_KEY_FILENAME),
        },
    }
    assert not owner.private_key_path.exists()
    assert not owner.public_key_path.exists()


def test_export_is_encrypted_and_contains_no_password_or_plaintext_private_key(
    tmp_path: Path,
) -> None:
    _, owner, pair = make_owner(tmp_path / "source", create_key=True, publisher="VRCForge Studio")
    destination = tmp_path / "portable-recovery.vrcforge-key.json"

    result = owner.export_backup(destination, PASSPHRASE)
    raw = destination.read_bytes()
    document = json.loads(raw)

    assert result == {"ok": True, "backupPath": str(destination), "fingerprint": pair.fingerprint}
    assert document["schema"] == RECOVERY_SCHEMA
    assert document["fingerprint"] == pair.fingerprint
    assert document["publisher"] == "VRCForge Studio"
    assert document["publicKey"] == base64.b64encode(pair.public_key).decode("ascii")
    assert b"-----BEGIN ENCRYPTED PRIVATE KEY-----" in raw
    assert b"-----BEGIN PRIVATE KEY-----" not in raw
    assert pair.private_key_pem not in raw
    assert PASSPHRASE.encode() not in raw
    assert owner.status()["key"] == {
        "exists": True,
        "fingerprint": pair.fingerprint,
        "publisher": "VRCForge Studio",
        "publicKeyPath": str(owner.public_key_path),
    }


def test_export_requires_explicitly_designated_official_identity(tmp_path: Path) -> None:
    _, owner, _ = make_owner(tmp_path / "source", create_key=True, designate=False)

    with pytest.raises(SkillSigningKeyMigrationError, match="explicitly designated"):
        owner.export_backup(tmp_path / "backup.json", PASSPHRASE)

    assert not (tmp_path / "backup.json").exists()


def test_export_rejects_short_passphrase_and_existing_backup(tmp_path: Path) -> None:
    _, owner, _ = make_owner(tmp_path / "source", create_key=True)
    destination = tmp_path / "backup.json"

    with pytest.raises(SkillSigningKeyMigrationError, match="at least eight"):
        owner.export_backup(destination, "short")
    assert not destination.exists()

    destination.write_text("preserve me", encoding="utf-8")
    with pytest.raises(SkillSigningKeyMigrationError, match="cannot be overwritten"):
        owner.export_backup(destination, PASSPHRASE)
    assert destination.read_text(encoding="utf-8") == "preserve me"


def test_wrong_passphrase_does_not_restore_or_register_any_key(tmp_path: Path) -> None:
    _, source, _ = make_owner(tmp_path / "source", create_key=True)
    destination = tmp_path / "backup.json"
    source.export_backup(destination, PASSPHRASE)
    target_service, target, _ = make_owner(tmp_path / "target")

    with pytest.raises(SkillSigningKeyMigrationError, match="passphrase is incorrect"):
        target.import_backup(destination, "incorrect passphrase")

    assert not target.private_key_path.exists()
    assert not target.public_key_path.exists()
    assert target_service.load_registry()["governance"]["official_signers"] == {}


def test_roundtrip_restores_same_identity_and_registers_new_machine_official(
    tmp_path: Path,
) -> None:
    _, source, pair = make_owner(tmp_path / "source", create_key=True, publisher="VRCForge Studio")
    destination = tmp_path / "backup.json"
    source.export_backup(destination, PASSPHRASE)
    target_service, target, _ = make_owner(tmp_path / "target")

    result = target.import_backup(destination, PASSPHRASE)
    governance = target_service.load_registry()["governance"]

    assert result == {"ok": True, "fingerprint": pair.fingerprint, "publisher": "VRCForge Studio"}
    assert target.private_key_path.name == PRIVATE_KEY_FILENAME
    assert target.public_key_path.name == PUBLIC_KEY_FILENAME
    assert target.public_key_path.read_bytes() == base64.b64encode(pair.public_key)
    assert governance["official_signers"][pair.fingerprint]["publisher"] == "VRCForge Studio"
    assert pair.fingerprint in governance["trusted_signers"]
    assert target.status()["key"]["fingerprint"] == pair.fingerprint


@pytest.mark.parametrize("field", ["fingerprint", "publicKey"])
def test_tampered_public_metadata_is_rejected(tmp_path: Path, field: str) -> None:
    _, source, _ = make_owner(tmp_path / "source", create_key=True)
    destination = tmp_path / "backup.json"
    source.export_backup(destination, PASSPHRASE)
    document = json.loads(destination.read_text(encoding="utf-8"))
    other_pair = SkillPackageService.generate_signing_keypair()
    document[field] = (
        other_pair.fingerprint
        if field == "fingerprint"
        else base64.b64encode(other_pair.public_key).decode("ascii")
    )
    destination.write_text(json.dumps(document), encoding="utf-8")
    _, target, _ = make_owner(tmp_path / "target")

    with pytest.raises(SkillSigningKeyMigrationError, match="identity do not match"):
        target.import_backup(destination, PASSPHRASE)

    assert not target.private_key_path.exists()
    assert not target.public_key_path.exists()


def test_different_existing_identity_requires_explicit_replacement(tmp_path: Path) -> None:
    _, source, replacement_pair = make_owner(tmp_path / "source", create_key=True)
    destination = tmp_path / "backup.json"
    source.export_backup(destination, PASSPHRASE)
    target_service, target, existing_pair = make_owner(tmp_path / "target", create_key=True)
    original_private = target.private_key_path.read_bytes()

    with pytest.raises(SkillSigningKeyMigrationError, match="explicit replacement"):
        target.import_backup(destination, PASSPHRASE)

    assert target.private_key_path.read_bytes() == original_private
    assert target.status()["key"]["fingerprint"] == existing_pair.fingerprint
    assert replacement_pair.fingerprint not in target_service.load_registry()["governance"]["official_signers"]

    result = target.import_backup(destination, PASSPHRASE, replace=True)

    assert result["fingerprint"] == replacement_pair.fingerprint
    assert target.status()["key"]["fingerprint"] == replacement_pair.fingerprint


def test_revoked_identity_cannot_be_restored(tmp_path: Path) -> None:
    _, source, pair = make_owner(tmp_path / "source", create_key=True)
    destination = tmp_path / "backup.json"
    source.export_backup(destination, PASSPHRASE)
    target_service, target, _ = make_owner(tmp_path / "target")
    target_service.revoke_signer(pair.fingerprint, reason="compromised")

    with pytest.raises(SkillSigningKeyMigrationError, match="revoked signer"):
        target.import_backup(destination, PASSPHRASE)

    assert not target.private_key_path.exists()


def test_dashboard_routes_use_expected_paths_aliases_and_redact_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dashboard_server

    paths = {route.path for route in dashboard_server.app.routes}
    assert "/api/app/skill-packages/official-key" in paths
    assert "/api/app/skill-packages/official-key/export" in paths
    assert "/api/app/skill-packages/official-key/import" in paths

    source_root = tmp_path / "source"
    source_service, _, pair = make_owner(source_root, create_key=True, publisher="VRCForge Studio")
    monkeypatch.setattr(dashboard_server, "USER_DATA_DIR", source_root)
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: source_service)
    monkeypatch.setattr(
        dashboard_server,
        "AGENT_GATEWAY",
        SimpleNamespace(
            local_state_write_guard=lambda: nullcontext(),
            skills=SimpleNamespace(write_lock=nullcontext()),
        ),
    )

    assert dashboard_server.app_official_skill_signing_key_status()["key"]["fingerprint"] == pair.fingerprint
    backup = tmp_path / "route-backup.json"
    export_request = dashboard_server.OfficialSkillSigningKeyExportRequest.model_validate(
        {"outputPath": str(backup), "passphrase": PASSPHRASE}
    )
    assert PASSPHRASE not in repr(export_request)
    exported = dashboard_server.app_export_official_skill_signing_key(export_request)
    assert exported == {"ok": True, "backupPath": str(backup), "fingerprint": pair.fingerprint}

    target_root = tmp_path / "target"
    target_service, _, _ = make_owner(target_root)
    monkeypatch.setattr(dashboard_server, "USER_DATA_DIR", target_root)
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: target_service)
    import_request = dashboard_server.OfficialSkillSigningKeyImportRequest.model_validate(
        {"backupPath": str(backup), "passphrase": PASSPHRASE, "replace": False}
    )
    assert PASSPHRASE not in repr(import_request)
    imported = dashboard_server.app_import_official_skill_signing_key(import_request)
    assert imported == {"ok": True, "fingerprint": pair.fingerprint, "publisher": "VRCForge Studio"}

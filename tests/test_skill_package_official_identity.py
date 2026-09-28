from __future__ import annotations

import json
from pathlib import Path

import pytest

from skill_packages import PackageSecurityError, SkillPackageService


def skill_source(
    root: Path,
    *,
    skill_id: str = "com.example.claimed-official",
    author: str = "VRCForge",
) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "workflows").mkdir()
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "id": skill_id,
                "name": "Claimed Official Skill",
                "skill_name": "claimed-official-skill",
                "version": "1.0.0",
                "author": author,
                "description": "Identity must come only from its registered signer.",
                "min_vrcforge_version": "0.3.0",
                "permissions": ["read_project"],
                "entrypoints": {
                    "skill": "SKILL.md",
                    "workflow": "workflows/plan.md",
                },
            }
        ),
        encoding="utf-8",
    )
    (source / "SKILL.md").write_text("Inspect before writing.\n", encoding="utf-8")
    (source / "workflows" / "plan.md").write_text("Read the avatar first.\n", encoding="utf-8")
    return source


def signed_install(
    root: Path,
    *,
    trusted: bool = False,
    official: bool = False,
    author: str = "VRCForge",
) -> tuple[SkillPackageService, object, Path]:
    service = SkillPackageService(root / "store", vrcforge_version="1.7.9")
    signer = service.generate_signing_keypair()
    if official:
        service.designate_official_signer(signer.fingerprint, reason="Project release signer")
    elif trusted:
        service.trust_signer(signer.fingerprint, reason="Trusted community author")
    package = service.export_release(
        skill_source(root, author=author),
        root / "signed.vsk",
        signer.private_key_pem,
    ).package_path
    service.install(package, source="official-identity-test")
    return service, signer, package


def test_trusted_signed_package_and_spoofed_author_are_not_official(tmp_path: Path) -> None:
    service, signer, _package = signed_install(tmp_path, trusted=True, author="VRCForge")

    installed = service.list_installed()[0]

    assert installed["author"] == "VRCForge"
    assert installed["signature_status"] == "signed"
    assert installed["signer_fingerprint"] == signer.fingerprint
    assert installed["signer_trust_status"] == "trusted"
    assert installed["official"] is False
    assert installed["officialPublisher"] is None
    assert installed["verified"] is False


def test_designated_official_signer_is_trusted_persisted_and_cryptographically_verified(
    tmp_path: Path,
) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="1.7.9")
    signer = service.generate_signing_keypair()

    designation = service.designate_official_signer(
        signer.fingerprint,
        reason="Explicit project-owned release signer",
        publisher="VRCForge",
    )
    persisted = json.loads(service.registry_path.read_text(encoding="utf-8"))

    assert designation["changed"] is True
    assert signer.fingerprint in persisted["governance"]["official_signers"]
    assert signer.fingerprint in persisted["governance"]["trusted_signers"]
    assert persisted["governance"]["official_signers"][signer.fingerprint]["publisher"] == "VRCForge"
    assert persisted["audit"][-1]["event"] == "skill_package_signer_designated_official"

    package = service.export_release(
        skill_source(tmp_path, author="Any author text"),
        tmp_path / "official.vsk",
        signer.private_key_pem,
    ).package_path
    service.install(package, source="official-identity-test")
    installed = service.list_installed()[0]

    assert installed["author"] == "Any author text"
    assert installed["official"] is True
    assert installed["officialPublisher"] == "VRCForge"
    assert installed["verified"] is True


def test_designation_after_install_updates_live_official_identity(tmp_path: Path) -> None:
    service, signer, _package = signed_install(tmp_path, trusted=True)
    assert service.list_installed()[0]["official"] is False

    service.designate_official_signer(signer.fingerprint, reason="Explicit promotion")

    assert service.list_installed()[0]["official"] is True
    assert service.list_installed()[0]["officialPublisher"] == "VRCForge"


def test_revoked_official_signer_loses_designation_and_cannot_be_redesignated(
    tmp_path: Path,
) -> None:
    service, signer, _package = signed_install(tmp_path, official=True)
    assert service.list_installed()[0]["official"] is True

    revoked = service.revoke_signer(signer.fingerprint, reason="Compromised official key")
    installed = service.list_installed()[0]

    assert signer.fingerprint not in revoked["governance"]["official_signers"]
    assert installed["signer_trust_status"] == "revoked"
    assert installed["official"] is False
    assert installed["officialPublisher"] is None
    assert installed["verified"] is False
    with pytest.raises(PackageSecurityError, match="revoked"):
        service.designate_official_signer(signer.fingerprint)


def test_tampered_installed_package_loses_official_status_despite_registry_identity(
    tmp_path: Path,
) -> None:
    service, signer, _package = signed_install(tmp_path, official=True)
    installed_root = (
        tmp_path / "store" / "com.example.claimed-official" / "versions" / "1.0.0"
    )
    (installed_root / "workflows" / "plan.md").write_text(
        "Tampered after the original signed import.\n", encoding="utf-8"
    )

    installed = service.list_installed()[0]

    assert installed["signer_fingerprint"] == signer.fingerprint
    assert installed["signature_status"] == "signed"
    assert installed["official"] is False
    assert installed["officialPublisher"] is None
    assert installed["verified"] is False


def test_unsigned_package_with_spoofed_official_author_is_never_official(
    tmp_path: Path,
) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="1.7.9")
    legitimate_signer = service.generate_signing_keypair()
    service.designate_official_signer(legitimate_signer.fingerprint)
    package = service.export_dev(
        skill_source(tmp_path, author="VRCForge"), tmp_path / "unsigned.vsk"
    ).package_path
    service.install(package, source="spoofed-author")

    installed = service.list_installed()[0]

    assert installed["author"] == "VRCForge"
    assert installed["signature_status"] == "dev"
    assert installed["official"] is False
    assert installed["officialPublisher"] is None
    assert installed["verified"] is False


def test_preview_governance_does_not_treat_spoofed_author_as_official(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="1.7.9")
    signer = service.generate_signing_keypair()
    service.trust_signer(signer.fingerprint, reason="Trusted, not official")
    package = service.export_release(
        skill_source(tmp_path, author="ayyitong888"),
        tmp_path / "spoofed-author.vsk",
        signer.private_key_pem,
    ).package_path

    governance = service.preflight_import(package).as_dict()["governance"]

    assert governance["signatureVerified"] is True
    assert governance["signerTrustStatus"] == "trusted"
    assert governance["official"] is False
    assert governance["officialPublisher"] is None
    assert governance["importAllowed"] is True


def test_preview_governance_requires_explicit_official_designation(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="1.7.9")
    signer = service.generate_signing_keypair()
    service.trust_signer(signer.fingerprint, reason="Trusted community signer")
    package = service.export_release(
        skill_source(tmp_path, author="VRCForge"),
        tmp_path / "trusted-only.vsk",
        signer.private_key_pem,
    ).package_path

    governance = service.preflight_import(package).as_dict()["governance"]

    assert governance["signerTrustStatus"] == "trusted"
    assert governance["official"] is False
    assert governance["officialPublisher"] is None
    assert governance["importAllowed"] is True


def test_preview_governance_hides_publisher_for_revoked_signer(tmp_path: Path) -> None:
    service, signer, package = signed_install(tmp_path, official=True, author="Any author")
    service.revoke_signer(signer.fingerprint, reason="Compromised")

    governance = service.preflight_import(package).as_dict()["governance"]

    assert governance["signatureVerified"] is True
    assert governance["signerTrustStatus"] == "revoked"
    assert governance["official"] is False
    assert governance["officialPublisher"] is None
    assert governance["importAllowed"] is False


def test_preview_governance_exposes_explicit_designated_publisher(tmp_path: Path) -> None:
    service = SkillPackageService(tmp_path / "store", vrcforge_version="1.7.9")
    signer = service.generate_signing_keypair()
    service.designate_official_signer(
        signer.fingerprint,
        reason="Explicit official release signer",
        publisher="ayyitong888",
    )
    package = service.export_release(
        skill_source(tmp_path, author="Unrelated manifest author"),
        tmp_path / "designated.vsk",
        signer.private_key_pem,
    ).package_path

    governance = service.preflight_import(package).as_dict()["governance"]

    assert governance["signatureVerified"] is True
    assert governance["signerTrustStatus"] == "trusted"
    assert governance["official"] is True
    assert governance["officialPublisher"] == "ayyitong888"
    assert governance["importAllowed"] is True


def test_signed_display_name_change_preserves_installed_author_identity(tmp_path: Path) -> None:
    service, signer, _ = signed_install(tmp_path, official=True, author="VRCForge")
    service.designate_official_signer(signer.fingerprint, publisher="ayyitong888")
    manifest_path = tmp_path / "source" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(version="1.0.1", author_display_name="ayyitong888")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    package = service.export_release(
        tmp_path / "source", tmp_path / "display-name-upgrade.vsk", signer.private_key_pem,
    ).package_path

    preview = service.preflight_import(package)

    assert preview.manifest["author"] == "VRCForge"
    assert preview.manifest["author_display_name"] == "ayyitong888"
    assert preview.signer_fingerprint == signer.fingerprint
    assert preview.governance["officialPublisher"] == "ayyitong888"
    assert preview.governance["importAllowed"] is True

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_gateway import RUNTIME_SKILL_SUPPORT_MAX_FILES, parse_skill_markdown
from skill_packages import SigningKeyPair, SkillPackageService
from skill_signing_key_migration import SkillSigningKeyMigrationService


ARTIFACT_ROOT = ROOT / "LocalBuilds" / "VRCForge_OfficialWorkflows_20260826"
APP_DATA = Path(os.environ["LOCALAPPDATA"]) / "VRCForge" / "agentic-app"
SIGNING_ROOT = APP_DATA / "signing"
PRIVATE_KEY_PATH = SIGNING_ROOT / "official-skill-signing.pem"
PUBLIC_KEY_PATH = SIGNING_ROOT / "official-skill-signing.pub"
STORE = APP_DATA / "skill-packages"
PACKAGES = (
    (
        "com.vrcforge.workflows.avatar_head_transplant",
        ROOT / "examples" / "skill-packages" / "vrcforge-avatar-head-transplant",
        "vrcforge-avatar-head-transplant-official.vsk",
    ),
    (
        "com.vrcforge.workflows.avatar_part_transplant",
        ROOT / "examples" / "skill-packages" / "vrcforge-avatar-part-transplant",
        "vrcforge-avatar-part-transplant-official.vsk",
    ),
    ("com.vrcforge.skills.avatar_wardrobe", ROOT / "artifacts" / "skills" / "vrcforge-avatar-wardrobe", "vrcforge-avatar-wardrobe-official.vsk"),
    ("com.vrcforge.skills.avatar_hairstyle", ROOT / "artifacts" / "skills" / "vrcforge-avatar-hairstyle", "vrcforge-avatar-hairstyle-official.vsk"),
    ("com.vrcforge.skills.avatar_accessory_switch", ROOT / "artifacts" / "skills" / "vrcforge-avatar-accessory-switch", "vrcforge-avatar-accessory-switch-official.vsk"),
    ("com.vrcforge.skills.avatar_expression_menu", ROOT / "artifacts" / "skills" / "vrcforge-avatar-expression-menu", "vrcforge-avatar-expression-menu-official.vsk"),
    ("com.vrcforge.skills.avatar_animation", ROOT / "artifacts" / "skills" / "vrcforge-avatar-animation", "vrcforge-avatar-animation-official.vsk"),
    ("com.vrcforge.skills.avatar_breast_physics_audit", ROOT / "artifacts" / "skills" / "vrcforge-avatar-breast-physics-audit", "vrcforge-avatar-breast-physics-audit-official.vsk"),
    ("com.vrcforge.skills.avatar_audit", ROOT / "artifacts" / "skills" / "vrcforge-avatar-audit", "vrcforge-avatar-audit-official.vsk"),
)
LEGACY_PACKAGE_ID = "community.personal.avatar-authoring-workflow"
PACKAGE_TITLES = {
    "com.vrcforge.workflows.avatar_head_transplant": "VRChat 换头",
    "com.vrcforge.workflows.avatar_part_transplant": "VRChat 配件移植",
    "com.vrcforge.skills.avatar_wardrobe": "VRChat 衣柜制作",
    "com.vrcforge.skills.avatar_hairstyle": "VRChat 发型切换",
    "com.vrcforge.skills.avatar_accessory_switch": "VRChat 饰品安装与开关",
    "com.vrcforge.skills.avatar_expression_menu": "VRChat 菜单制作",
    "com.vrcforge.skills.avatar_animation": "VRChat 动画与体型补偿",
    "com.vrcforge.skills.avatar_breast_physics_audit": "VRChat 胸部动态骨骼检查",
    "com.vrcforge.skills.avatar_audit": "VRChat Avatar 检查与验收",
}


def signer() -> SigningKeyPair:
    if PRIVATE_KEY_PATH.is_file():
        private_pem = PRIVATE_KEY_PATH.read_bytes()
        private = serialization.load_pem_private_key(private_pem, password=None)
        if not isinstance(private, Ed25519PrivateKey):
            raise TypeError("The existing official signing key is not Ed25519.")
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        pair = SigningKeyPair(private_pem, public, hashlib.sha256(public).hexdigest())
        if not PUBLIC_KEY_PATH.is_file():
            SkillPackageService.save_signing_keypair(pair, PRIVATE_KEY_PATH, PUBLIC_KEY_PATH)
        return pair

    pair = SkillPackageService.generate_signing_keypair()
    SkillPackageService.save_signing_keypair(pair, PRIVATE_KEY_PATH, PUBLIC_KEY_PATH)
    return pair


def prepare() -> dict[str, object]:
    pair = signer()
    package_root = ARTIFACT_ROOT / "packages"
    package_root.mkdir(parents=True, exist_ok=True)
    service = SkillPackageService(ARTIFACT_ROOT / "signing-build-store", vrcforge_version="1.7.9")
    packages: list[dict[str, object]] = []
    for package_id, source, filename in PACKAGES:
        source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        service.validate_manifest(source_manifest, package_root=source)
        workflow_relative = str(source_manifest.get("entrypoints", {}).get("workflow") or "")
        skill = parse_skill_markdown(source / "SKILL.md")
        support_files = skill.get("supportFiles", [])
        if (
            source_manifest.get("execution") != "agentic"
            or source_manifest.get("name") != PACKAGE_TITLES[package_id]
            or skill.get("title") != PACKAGE_TITLES[package_id]
            or not workflow_relative
            or workflow_relative not in support_files
            or len(support_files) > RUNTIME_SKILL_SUPPORT_MAX_FILES
            or "executionPlan" in source_manifest.get("entrypoints", {})
        ):
            raise RuntimeError(f"Agentic official Skill source verification failed: {package_id}")
        source_workflow_bytes = (source / workflow_relative).read_bytes()
        source_workflow = json.loads(source_workflow_bytes)
        write_steps = [step for step in source_workflow.get("steps", []) if step.get("writes") is True]
        read_only = skill.get("permissionMode") == "read_only"
        if (
            source_workflow.get("schema") != "vrcforge.skill-package.workflow.v1"
            or not source_workflow.get("steps")
            or not all(
                step.get("tool") in skill.get("allowedTools", [])
                for step in source_workflow["steps"]
            )
            or (read_only and bool(write_steps))
            or (not read_only and not write_steps)
            or any(step.get("runtimeApprovalRequired") is not True for step in write_steps)
            or (not read_only and source_workflow.get("approval", {}).get("required") is not True)
            or (not read_only and source_workflow.get("checkpoint", {}).get("required") is not True)
            or (
                not read_only
                and source_workflow.get("rollback", {}).get("requiresSeparateApproval") is not True
            )
        ):
            raise RuntimeError(f"Agentic official Skill workflow is unsafe: {package_id}")
        output = package_root / filename
        exported = service.export_release(source, output, PRIVATE_KEY_PATH)
        preview = service.inspect_package(output)
        if (
            preview.manifest["id"] != package_id
            or preview.manifest["version"] != "1.0.1"
            or preview.manifest.get("execution") != "agentic"
            or preview.manifest.get("name") != PACKAGE_TITLES[package_id]
            or preview.signature_status != "signed"
            or preview.signer_fingerprint != pair.fingerprint
        ):
            raise RuntimeError(f"Signed package verification failed: {package_id}")
        with zipfile.ZipFile(output, "r") as archive:
            signed_workflow_bytes = archive.read(workflow_relative)
            lock = json.loads(archive.read("skill.lock.json"))
        workflow_sha256 = hashlib.sha256(signed_workflow_bytes).hexdigest()
        if (
            signed_workflow_bytes != source_workflow_bytes
            or lock.get("files", {}).get(workflow_relative) != workflow_sha256
        ):
            raise RuntimeError(f"Signed community-workflow lock verification failed: {package_id}")
        packages.append(
            {
                "id": package_id,
                "name": preview.manifest["name"],
                "version": preview.manifest["version"],
                "path": str(output),
                "sha256": preview.package_sha256,
                "signerFingerprint": pair.fingerprint,
                "execution": "agentic",
                "workflowSha256": workflow_sha256,
                "workflowStepCount": len(source_workflow["steps"]),
                "supportFileCount": len(support_files),
                "readOnly": read_only,
            }
        )
    return {
        "ok": True,
        "phase": "prepare",
        "fingerprint": pair.fingerprint,
        "publicKeyPath": str(PUBLIC_KEY_PATH),
        "privateKeyStoredLocally": PRIVATE_KEY_PATH.is_file(),
        "packages": packages,
    }


def request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    token = (APP_DATA / "config" / "app-session-token").read_text(encoding="utf-8").strip()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    http_request = urllib.request.Request(
        "http://127.0.0.1:8757" + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": "tauri://localhost",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(http_request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} returned {error.code}: {detail[:700]}") from error


def install() -> dict[str, object]:
    pair = signer()
    service = SkillPackageService(STORE, vrcforge_version="1.7.9")
    service.designate_official_signer(
        pair.fingerprint,
        reason="Explicitly designated by the VRCForge project developer",
        publisher="VRCForge",
    )
    existing = {
        str(item["id"]): item
        for item in request("GET", "/api/app/skill-packages").get("installed", [])
    }
    reports: list[dict[str, object]] = []
    for package_id, _source, filename in PACKAGES:
        output = ARTIFACT_ROOT / "packages" / filename
        if package_id in existing:
            request(
                "DELETE",
                "/api/app/skill-packages/" + urllib.parse.quote(package_id, safe=""),
                {"removeProjectedSkill": True},
            )
        result = request(
            "POST", "/api/app/skill-packages/import", {"packagePath": str(output)}
        )
        if not result.get("ok"):
            raise RuntimeError(f"Package import failed: {package_id}")
        request(
            "PUT",
            "/api/app/skill-packages/" + urllib.parse.quote(package_id, safe=""),
            {"enabled": True, "syncProjectedSkill": True},
        )
        reports.append({"id": package_id, "path": str(output), "enabled": True})
    if LEGACY_PACKAGE_ID in existing:
        removed = request(
            "DELETE",
            "/api/app/skill-packages/" + urllib.parse.quote(LEGACY_PACKAGE_ID, safe=""),
            {"removeProjectedSkill": True},
        )
        if not removed.get("ok"):
            raise RuntimeError("Legacy whole-avatar Skill could not be removed safely.")
    installed = request("GET", "/api/app/skill-packages")
    selected = [
        item
        for item in installed.get("installed", [])
        if item.get("id") in {package_id for package_id, _, _ in PACKAGES}
    ]
    if len(selected) != len(PACKAGES):
        raise RuntimeError("One or more official workflow packages were not installed.")
    if any(item.get("id") == LEGACY_PACKAGE_ID for item in installed.get("installed", [])):
        raise RuntimeError("The legacy whole-avatar Skill is still installed.")
    for entry in selected:
        if (
            entry.get("official") is not True
            or entry.get("name") != PACKAGE_TITLES[str(entry.get("id"))]
            or entry.get("officialPublisher") != "VRCForge"
            or entry.get("signature_status") != "signed"
            or entry.get("signer_fingerprint") != pair.fingerprint
            or entry.get("version") != "1.0.1"
            or entry.get("enabled") is not True
            or (entry.get("execution") or entry.get("executionMode")) != "agentic"
            or entry.get("runtimeEnforced") is True
        ):
            raise RuntimeError(f"Official workflow readback failed: {entry.get('id')}")
    projected = {
        str(item.get("name") or ""): item
        for item in request("GET", "/api/app/skills").get("skills", [])
    }
    for package_id, source, _filename in PACKAGES:
        skill_name = str(json.loads((source / "manifest.json").read_text(encoding="utf-8"))["skill_name"])
        skill = projected.get(skill_name, {})
        if (
            not skill
            or skill.get("available") is not True
            or skill.get("enabled") is not True
            or len(skill.get("supportFiles") or []) > RUNTIME_SKILL_SUPPORT_MAX_FILES
        ):
            raise RuntimeError(f"Official Skill runtime projection is unavailable: {package_id}")
    key = request("GET", "/api/app/skill-packages/official-key").get("key", {})
    if not key.get("exists") or key.get("fingerprint") != pair.fingerprint:
        raise RuntimeError("Official signing key status did not match the installed signer.")
    with tempfile.TemporaryDirectory(prefix="vrcforge-key-migration-") as temp_root:
        recovery_path = Path(temp_root) / "official-skill-signing.vrcforge-key"
        recovery_password = secrets.token_urlsafe(24)
        backup = request(
            "POST",
            "/api/app/skill-packages/official-key/export",
            {"outputPath": str(recovery_path), "passphrase": recovery_password},
        )
        document = json.loads(recovery_path.read_text(encoding="utf-8"))
        if (
            backup.get("fingerprint") != pair.fingerprint
            or "BEGIN ENCRYPTED PRIVATE KEY" not in document.get("encryptedPrivateKeyPem", "")
            or recovery_password in recovery_path.read_text(encoding="utf-8")
        ):
            raise RuntimeError("Encrypted official signing-key backup verification failed.")
        destination_service = SkillPackageService(
            Path(temp_root) / "destination-store", vrcforge_version="1.7.9"
        )
        migrated = SkillSigningKeyMigrationService(
            destination_service, Path(temp_root) / "destination-signing"
        ).import_backup(recovery_path, recovery_password)
        if (
            migrated.get("fingerprint") != pair.fingerprint
            or pair.fingerprint
            not in destination_service.load_registry()["governance"]["official_signers"]
        ):
            raise RuntimeError("Official signing identity did not survive migration.")
    return {
        "ok": True,
        "phase": "install",
        "fingerprint": pair.fingerprint,
        "officialPublisher": "VRCForge",
        "keyMigrationAvailable": True,
        "keyMigrationRoundTripVerified": True,
        "packages": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "version": item.get("version"),
                "signature": item.get("signature_status"),
                "official": item.get("official"),
                "enabled": item.get("enabled"),
                "execution": item.get("execution") or item.get("executionMode"),
                "projectedAvailable": projected.get(
                    str(
                        json.loads((source / "manifest.json").read_text(encoding="utf-8"))["skill_name"]
                    ),
                    {},
                ).get("available"),
            }
            for item in selected
            for package_id, source, _filename in PACKAGES
            if item.get("id") == package_id
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "install"))
    arguments = parser.parse_args()
    payload = prepare() if arguments.phase == "prepare" else install()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

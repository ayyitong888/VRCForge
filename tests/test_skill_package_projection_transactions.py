from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import dashboard_server
from agent_gateway import (
    AgentGateway,
    AgentGatewayError,
    PROJECTED_SKILL_STATE_NAME,
    RUNTIME_SKILL_SUPPORT_MAX_FILE_BYTES,
)
from skill_packages import SkillPackageError, SkillPackageService


def _write_package_source(
    root: Path,
    *,
    package_id: str,
    skill_name: str,
    entrypoints: dict[str, str] | None = None,
    support_files: tuple[str, ...] = ("workflows/workflow.json",),
    permissions: tuple[str, ...] = ("read_project",),
) -> Path:
    source = root / "source"
    source.mkdir(parents=True)
    package_entrypoints = entrypoints or {
        "skill": "SKILL.md",
        "workflow": "workflows/workflow.json",
    }
    manifest = {
        "id": package_id,
        "name": "Projection Transaction Fixture",
        "skill_name": skill_name,
        "version": "1.0.0",
        "author": "VRCForge Tests",
        "description": "Projection transaction and runtime audit fixture.",
        "min_vrcforge_version": "0.0.0",
        "permissions": list(permissions),
        "entrypoints": package_entrypoints,
    }
    (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    support_lines = ["support-files:", *(f"  - {relative}" for relative in support_files)] if support_files else []
    (source / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {skill_name}",
                "title: Projection Transaction Fixture",
                "permission-mode: read_only",
                "risk-level: low",
                "allowed-tools:",
                "  - vrcforge_health",
                "entrypoint-tool: vrcforge_health",
                *support_lines,
                "---",
                "Run one read-only health entrypoint.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for relative in support_files:
        support = source / relative
        support.parent.mkdir(parents=True, exist_ok=True)
        support.write_text(json.dumps({"steps": [{"tool": "vrcforge_health"}]}), encoding="utf-8")
    return source


def test_projection_rejects_support_entrypoint_that_overwrites_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installed = tmp_path / "installed"
    (installed / "docs").mkdir(parents=True)
    (installed / "docs" / "main.md").write_text(
        "---\nname: benign-name\nsupport-files:\n  - SKILL.md\n---\nBenign instructions.\n",
        encoding="utf-8",
    )
    (installed / "SKILL.md").write_text("---\nname: overwritten-name\n---\nUnexpected instructions.\n", encoding="utf-8")
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    old_projection = gateway.user_skills_dir / "benign-name" / "SKILL.md"
    old_projection.parent.mkdir(parents=True)
    old_projection.write_text("old projection remains intact\n", encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)

    with pytest.raises(SkillPackageError, match="cannot overwrite reserved"):
        dashboard_server._project_installed_skill(
            installed,
            {
                "id": "community.tests.projection-collision",
                "skill_name": "benign-name",
                "entrypoints": {"skill": "docs/main.md", "workflow": "SKILL.md"},
            },
        )

    assert old_projection.read_text(encoding="utf-8") == "old projection remains intact\n"


def test_import_projection_failure_restores_registry_version_and_old_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_package_source(
        tmp_path,
        package_id="community.tests.projection-rollback",
        skill_name="projection-rollback",
        entrypoints={"skill": "SKILL.md"},
        support_files=("workflows/not-an-entrypoint.json",),
    )
    build_service = SkillPackageService(tmp_path / "build-store", vrcforge_version="0.0.0")
    package = build_service.export_dev(source, tmp_path / "rollback.vsk").package_path
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    old_projection = gateway.user_skills_dir / "projection-rollback" / "SKILL.md"
    old_projection.parent.mkdir(parents=True)
    old_projection.write_text("old projection remains intact\n", encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: service)

    with pytest.raises(SkillPackageError, match="must also be declared as manifest entrypoints"):
        dashboard_server.import_skill_package_sync({"packagePath": str(package)})

    assert service.list_installed() == []
    assert not (service.skill_store / "community.tests.projection-rollback").exists()
    assert old_projection.read_text(encoding="utf-8") == "old projection remains intact\n"


def test_package_enable_toggle_preserves_signed_projection_and_audit_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_package_source(
        tmp_path,
        package_id="community.tests.projection-toggle",
        skill_name="projection-toggle",
    )
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(source, tmp_path / "toggle.vsk", key_pair.private_key_pem).package_path
    service.trust_signer(key_pair.fingerprint)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.register_tool("vrcforge_health", "Read runtime health.", "read/debug", lambda _params: {"ok": True})
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: service)

    imported = dashboard_server.import_skill_package_sync({"packagePath": str(package)})
    projected_skill = Path(imported["projectedSkill"]["path"])
    original_bytes = projected_skill.read_bytes()
    initial_context = gateway._runtime_skill_package_audit_context(  # noqa: SLF001 - regression checks attribution.
        gateway._find_registry_skill("projection-toggle")  # noqa: SLF001
    )

    dashboard_server.set_skill_package_enabled_sync(
        {"skillPackageId": "community.tests.projection-toggle", "enabled": False}
    )
    assert gateway._find_registry_skill("projection-toggle")["enabled"] is False  # noqa: SLF001
    dashboard_server.set_skill_package_enabled_sync(
        {"skillPackageId": "community.tests.projection-toggle", "enabled": True}
    )
    final_skill = gateway._find_registry_skill("projection-toggle")  # noqa: SLF001
    final_context = gateway._runtime_skill_package_audit_context(final_skill)  # noqa: SLF001

    assert projected_skill.read_bytes() == original_bytes
    assert (projected_skill.parent / PROJECTED_SKILL_STATE_NAME).is_file()
    assert initial_context["signerFingerprint"] == key_pair.fingerprint
    assert final_context == initial_context

    gateway.create_user_skill({"name": "unrelated-user-skill", "instructions": "Keep package bytes intact."})
    assert projected_skill.read_bytes() == original_bytes


def test_package_enable_projection_failure_restores_registry_and_projection_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_package_source(
        tmp_path,
        package_id="community.tests.projection-toggle-rollback",
        skill_name="projection-toggle-rollback",
    )
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(source, tmp_path / "toggle-rollback.vsk", key_pair.private_key_pem).package_path
    service.trust_signer(key_pair.fingerprint)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: service)
    imported = dashboard_server.import_skill_package_sync({"packagePath": str(package)})
    projected_skill = Path(imported["projectedSkill"]["path"])
    state_path = projected_skill.parent / PROJECTED_SKILL_STATE_NAME
    installed_path = service.skill_store / "community.tests.projection-toggle-rollback" / "installed.json"
    original_registry = service.registry_path.read_bytes()
    original_installed = installed_path.read_bytes()
    original_state = state_path.read_bytes()

    def fail_state_write(_target_dir: Path, _enabled: bool) -> Path:
        raise OSError("injected projected state failure")

    monkeypatch.setattr(dashboard_server, "_write_projected_skill_state", fail_state_write)
    with pytest.raises(OSError, match="injected projected state failure"):
        dashboard_server.set_skill_package_enabled_sync(
            {"skillPackageId": "community.tests.projection-toggle-rollback", "enabled": False}
        )

    assert service.registry_path.read_bytes() == original_registry
    assert installed_path.read_bytes() == original_installed
    assert state_path.read_bytes() == original_state
    assert service.list_installed()[0]["enabled"] is True
    assert gateway._find_registry_skill("projection-toggle-rollback")["enabled"] is True  # noqa: SLF001


def test_safe_mode_projection_failure_restores_all_registry_and_projection_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    key_pair = service.generate_signing_keypair()
    service.trust_signer(key_pair.fingerprint)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: service)
    state_paths: list[Path] = []
    installed_paths: list[Path] = []
    for suffix in ("one", "two"):
        package_id = f"community.tests.safe-mode-{suffix}"
        skill_name = f"safe-mode-{suffix}"
        source = _write_package_source(
            tmp_path / suffix,
            package_id=package_id,
            skill_name=skill_name,
            permissions=("execute_shell",),
        )
        package = service.export_release(
            source,
            tmp_path / f"safe-mode-{suffix}.vsk",
            key_pair.private_key_pem,
        ).package_path
        imported = dashboard_server.import_skill_package_sync({"packagePath": str(package)})
        projected_skill = Path(imported["projectedSkill"]["path"])
        state_paths.append(projected_skill.parent / PROJECTED_SKILL_STATE_NAME)
        installed_paths.append(service.skill_store / package_id / "installed.json")

    original_registry = service.registry_path.read_bytes()
    original_installed = {path: path.read_bytes() for path in installed_paths}
    original_states = {path: path.read_bytes() for path in state_paths}
    original_write_state = dashboard_server._write_projected_skill_state
    write_count = 0

    def fail_second_state_write(target_dir: Path, enabled: bool) -> Path:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("injected safe-mode projection failure")
        return original_write_state(target_dir, enabled)

    monkeypatch.setattr(dashboard_server, "_write_projected_skill_state", fail_second_state_write)
    with pytest.raises(OSError, match="injected safe-mode projection failure"):
        dashboard_server.set_skill_package_safe_mode_sync({"enabled": True, "reason": "test rollback"})

    assert service.registry_path.read_bytes() == original_registry
    assert all(path.read_bytes() == original_installed[path] for path in installed_paths)
    assert all(path.read_bytes() == original_states[path] for path in state_paths)
    assert service.load_registry()["governance"]["safe_mode"]["enabled"] is False
    assert all(item["enabled"] is True for item in service.list_installed())


@pytest.mark.parametrize("operation", ["revoke", "block"])
def test_governance_projection_failure_restores_registry_and_projection_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    package_id = f"community.tests.governance-{operation}-rollback"
    skill_name = f"governance-{operation}-rollback"
    source = _write_package_source(tmp_path, package_id=package_id, skill_name=skill_name)
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(source, tmp_path / f"{operation}.vsk", key_pair.private_key_pem).package_path
    service.trust_signer(key_pair.fingerprint)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: service)
    imported = dashboard_server.import_skill_package_sync({"packagePath": str(package)})
    state_path = Path(imported["projectedSkill"]["path"]).parent / PROJECTED_SKILL_STATE_NAME
    installed_path = service.skill_store / package_id / "installed.json"
    original_registry = service.registry_path.read_bytes()
    original_installed = installed_path.read_bytes()
    original_state = state_path.read_bytes()

    def fail_state_write(_target_dir: Path, _enabled: bool) -> Path:
        raise OSError(f"injected {operation} projection failure")

    monkeypatch.setattr(dashboard_server, "_write_projected_skill_state", fail_state_write)
    with pytest.raises(OSError, match=f"injected {operation} projection failure"):
        if operation == "revoke":
            dashboard_server.revoke_skill_package_signer_sync(
                {"signerFingerprint": key_pair.fingerprint, "reason": "test rollback"}
            )
        else:
            dashboard_server.block_skill_package_sync(
                {"packageId": package_id, "reason": "test rollback"}
            )

    assert service.registry_path.read_bytes() == original_registry
    assert installed_path.read_bytes() == original_installed
    assert state_path.read_bytes() == original_state
    assert service.list_installed()[0]["enabled"] is True
    assert gateway._find_registry_skill(skill_name)["enabled"] is True  # noqa: SLF001


def test_uninstall_projection_failure_restores_package_tree_registry_and_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_id = "community.tests.uninstall-rollback"
    skill_name = "uninstall-rollback"
    source = _write_package_source(tmp_path, package_id=package_id, skill_name=skill_name)
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(source, tmp_path / "uninstall.vsk", key_pair.private_key_pem).package_path
    service.trust_signer(key_pair.fingerprint)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: service)
    imported = dashboard_server.import_skill_package_sync({"packagePath": str(package)})
    package_root = service.skill_store / package_id
    projection_root = Path(imported["projectedSkill"]["path"]).parent

    def tree_bytes(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    original_registry = service.registry_path.read_bytes()
    original_package_tree = tree_bytes(package_root)
    original_projection_tree = tree_bytes(projection_root)
    original_projection_transaction = dashboard_server._delete_projected_skill_transaction

    @contextmanager
    def fail_after_projection_isolated(manifest: dict[str, object]):
        with original_projection_transaction(manifest) as projected:
            assert not projection_root.exists()
            raise OSError("injected uninstall projection failure")
            yield projected

    monkeypatch.setattr(
        dashboard_server,
        "_delete_projected_skill_transaction",
        fail_after_projection_isolated,
    )
    with pytest.raises(OSError, match="injected uninstall projection failure"):
        dashboard_server.uninstall_skill_package_sync({"skillPackageId": package_id})

    assert service.registry_path.read_bytes() == original_registry
    assert tree_bytes(package_root) == original_package_tree
    assert tree_bytes(projection_root) == original_projection_tree
    assert service.list_installed()[0]["id"] == package_id
    assert not (service.skill_store / ".uninstall-staging").exists()


def test_trust_signer_write_runs_inside_shared_package_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    class TrackingLock:
        active = False

        def __enter__(self) -> None:
            self.active = True

        def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
            self.active = False

    lock = TrackingLock()

    class TrustService:
        def trust_signer(self, fingerprint: str, *, reason: str | None = None) -> dict[str, object]:
            assert lock.active
            return {"ok": True, "fingerprint": fingerprint, "reason": reason}

    monkeypatch.setattr(dashboard_server, "SKILL_PACKAGE_WRITE_LOCK", lock)
    monkeypatch.setattr(dashboard_server, "skill_package_service", TrustService)

    result = dashboard_server.trust_skill_package_signer_sync(
        {"signerFingerprint": "a" * 64, "reason": "lock regression"}
    )

    assert result["signer"]["ok"] is True
    assert lock.active is False


def test_runtime_support_limit_is_checked_before_file_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    skill_root = gateway.user_skills_dir / "bounded-support"
    skill_root.mkdir(parents=True)
    skill_file = skill_root / "SKILL.md"
    skill_file.write_text("---\nname: bounded-support\n---\nBounded support.\n", encoding="utf-8")
    support = skill_root / "oversized.txt"
    with support.open("wb") as stream:
        stream.truncate(RUNTIME_SKILL_SUPPORT_MAX_FILE_BYTES + 1)
    read_calls: list[Path] = []
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path.resolve() == support.resolve():
            read_calls.append(path)
            raise AssertionError("oversized support must be rejected before read_bytes")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    with pytest.raises(AgentGatewayError, match="exceeds the .*byte limit"):
        gateway._load_runtime_skill_support_files(  # noqa: SLF001 - focused bounded-read regression.
            {"supportFiles": ["oversized.txt"], "storagePath": str(skill_file)}
        )
    assert read_calls == []


def test_runtime_audit_rejects_oversized_installed_file_before_hash_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_package_source(
        tmp_path,
        package_id="community.tests.audit-size",
        skill_name="audit-size",
    )
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(source, tmp_path / "audit-size.vsk", key_pair.private_key_pem).package_path
    service.trust_signer(key_pair.fingerprint)
    installed = service.install(package)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    dashboard_server._project_installed_skill(installed.installed_path, installed.preview.manifest)
    projected_skill = gateway.user_skills_dir / "audit-size" / "SKILL.md"
    oversized = installed.installed_path / "workflows" / "workflow.json"
    with oversized.open("wb") as stream:
        stream.truncate(service.max_file_size + 1)
    read_calls: list[Path] = []
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path.resolve() == oversized.resolve():
            read_calls.append(path)
            raise AssertionError("oversized installed file must be rejected before read_bytes")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    context = service.runtime_audit_context(
        "audit-size",
        projected_skill,
        ["workflows/workflow.json"],
    )

    assert context == {}
    assert read_calls == []

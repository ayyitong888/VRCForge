from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

import dashboard_server
from agent_gateway import (
    AgentGateway,
    AgentGatewayError,
    PROJECTED_SKILL_STATE_MAX_BYTES,
    PROJECTED_SKILL_STATE_NAME,
    PROJECTED_SKILL_STATE_SCHEMA,
    RUNTIME_SKILL_SUPPORT_MAX_FILE_BYTES,
)
from skill_packages import (
    LOCK_NAME,
    PackageIntegrityError,
    PackageUpdateError,
    SkillPackageError,
    SkillPackageService,
    canonical_json_bytes,
)
from skill_package_governance import (
    SkillPackageGovernancePorts,
    SkillPackageGovernanceService,
)
from skill_package_controller import SkillPackageController
from skill_package_projection import (
    SkillPackageProjectionPorts,
    SkillPackageProjectionService,
)


def _projection(gateway: AgentGateway) -> SkillPackageProjectionService:
    return SkillPackageProjectionService(
        SkillPackageProjectionPorts(
            user_skills_dir=lambda: gateway.skills.user_skills_dir,
            user_skill_lock=gateway.skills.write_lock,
            find_user_skill=gateway.skills.find_user_skill,
            validate_projection_name=gateway.skills.validate_projection_name,
            make_conflict_error=lambda message: AgentGatewayError(
                message,
                status_code=409,
            ),
            parse_skill=dashboard_server.parse_skill_markdown,
            parse_error_types=(AgentGatewayError,),
            installed_package_candidates=lambda _package_id: (),
            state_name=PROJECTED_SKILL_STATE_NAME,
            state_schema=PROJECTED_SKILL_STATE_SCHEMA,
            state_max_bytes=PROJECTED_SKILL_STATE_MAX_BYTES,
        )
    )


def _controller(
    service: SkillPackageService,
    projection: SkillPackageProjectionService,
) -> SkillPackageController:
    return SkillPackageController(
        replace(
            dashboard_server.SKILL_PACKAGE_CONTROLLER._ports,  # noqa: SLF001 - local typed transaction fixture.
            make_service=lambda: service,
            project_installed_skill=lambda installed, manifest, enabled: (
                projection.project_installed(
                    installed,
                    manifest,
                    enabled=enabled,
                )
            ),
            set_projected_skill_enabled=lambda manifest, enabled: (
                projection.set_enabled_batch([manifest], enabled)[0]
            ),
            delete_projected_skill=projection.delete_transaction,
        )
    )


def _governance(
    service: SkillPackageService,
    projection: SkillPackageProjectionService,
) -> SkillPackageGovernanceService:
    return SkillPackageGovernanceService(
        replace(
            dashboard_server.SKILL_PACKAGE_GOVERNANCE._ports,  # noqa: SLF001 - local typed transaction fixture.
            make_service=lambda: service,
            disable_projected_skills=lambda manifests: (
                projection.set_enabled_batch(manifests, False)
            ),
        )
    )


def _runtime_snapshot(gateway: AgentGateway, skill_name: str):
    with gateway._skill_package_write_lock:  # noqa: SLF001 - mirror the production package -> user lock order.
        snapshot = gateway.skills.prepare_runtime_skill(
            skill_name,
            gateway.ensure_config(),
            gateway._runtime_skill_package_audit_context_locked,  # noqa: SLF001 - exact production capture callback.
        )
    assert snapshot is not None
    return snapshot


def _write_package_source(
    root: Path,
    *,
    package_id: str,
    skill_name: str,
    version: str = "1.0.0",
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
        "version": version,
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


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (
            ("dir", b"") if path.is_dir() else ("file", path.read_bytes())
        )
        for path in root.rglob("*")
    }


def test_projection_rejects_support_entrypoint_that_overwrites_skill(tmp_path: Path) -> None:
    installed = tmp_path / "installed"
    (installed / "docs").mkdir(parents=True)
    (installed / "docs" / "main.md").write_text(
        "---\nname: benign-name\nsupport-files:\n  - SKILL.md\n---\nBenign instructions.\n",
        encoding="utf-8",
    )
    (installed / "SKILL.md").write_text("---\nname: overwritten-name\n---\nUnexpected instructions.\n", encoding="utf-8")
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    projection = _projection(gateway)
    old_installed = tmp_path / "old-installed"
    old_installed.mkdir()
    old_installed.joinpath("SKILL.md").write_text(
        "---\nname: benign-name\n---\nOld projection remains intact.\n",
        encoding="utf-8",
    )
    owner_manifest = {
        "id": "community.tests.projection-collision",
        "skill_name": "benign-name",
        "entrypoints": {"skill": "SKILL.md"},
    }
    projected = projection.project_installed(old_installed, owner_manifest)
    assert projected is not None
    old_projection = Path(projected["path"])
    original_projection = old_projection.read_bytes()

    with pytest.raises(SkillPackageError, match="cannot overwrite reserved"):
        projection.project_installed(
            installed,
            {
                "id": "community.tests.projection-collision",
                "skill_name": "benign-name",
                "entrypoints": {"skill": "docs/main.md", "workflow": "SKILL.md"},
            },
        )

    assert old_projection.read_bytes() == original_projection


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
    projection = _projection(gateway)
    old_installed = tmp_path / "old-installed"
    old_installed.mkdir()
    old_installed.joinpath("SKILL.md").write_text(
        "---\nname: projection-rollback\n---\nOld projection remains intact.\n",
        encoding="utf-8",
    )
    projected = projection.project_installed(
        old_installed,
        {
            "id": "community.tests.projection-rollback",
            "skill_name": "projection-rollback",
            "entrypoints": {"skill": "SKILL.md"},
        },
    )
    assert projected is not None
    old_projection = Path(projected["path"])
    original_projection = old_projection.read_bytes()
    controller = _controller(service, projection)

    with pytest.raises(SkillPackageError, match="must also be declared as manifest entrypoints"):
        controller.import_package(
            {"packagePath": str(package)}
        )

    assert service.list_installed() == []
    assert not (service.skill_store / "community.tests.projection-rollback").exists()
    assert old_projection.read_bytes() == original_projection


def test_package_update_rejects_skill_name_rename_without_tree_changes(
    tmp_path: Path,
) -> None:
    package_id = "community.tests.rename-owner"
    service = SkillPackageService(
        tmp_path / "skill-packages",
        vrcforge_version="0.0.0",
    )
    old_source = _write_package_source(
        tmp_path / "old",
        package_id=package_id,
        skill_name="old-skill-name",
        version="1.0.0",
    )
    old_package = service.export_dev(
        old_source,
        tmp_path / "old.vsk",
    ).package_path
    gateway = AgentGateway(
        tmp_path / "config" / "agent_gateway.json",
        tmp_path / "audit",
    )
    projection = _projection(gateway)
    controller = _controller(service, projection)
    imported = controller.import_package({"packagePath": str(old_package)})
    old_projection = Path(imported["projectedSkill"]["path"]).parent

    new_source = _write_package_source(
        tmp_path / "new",
        package_id=package_id,
        skill_name="new-skill-name",
        version="2.0.0",
    )
    new_package = service.export_dev(
        new_source,
        tmp_path / "new.vsk",
    ).package_path
    package_before = _tree_snapshot(service.skill_store)
    projection_before = _tree_snapshot(gateway.skills.user_skills_dir)

    with pytest.raises(
        PackageUpdateError,
        match="Projected skill name cannot change",
    ):
        controller.import_package({"packagePath": str(new_package)})

    assert _tree_snapshot(service.skill_store) == package_before
    assert _tree_snapshot(gateway.skills.user_skills_dir) == projection_before
    assert old_projection.is_dir()
    assert not (gateway.skills.user_skills_dir / "new-skill-name").exists()


def test_projection_candidates_exclude_tampered_versions_and_update_fails_closed(
    tmp_path: Path,
) -> None:
    package_id = "community.tests.locked-migration"
    service = SkillPackageService(
        tmp_path / "skill-packages",
        vrcforge_version="0.0.0",
    )
    for version in ("1.0.0", "2.0.0"):
        source = _write_package_source(
            tmp_path / version,
            package_id=package_id,
            skill_name="locked-migration",
            version=version,
        )
        package = service.export_dev(
            source,
            tmp_path / f"locked-migration-{version}.vsk",
        ).package_path
        service.install(package, source="locked-migration-test")

    versions_root = service.skill_store / package_id / "versions"
    retained_skill = versions_root / "1.0.0" / "SKILL.md"
    retained_skill.write_text(
        "---\nname: locked-migration\n---\nTampered retained version.\n",
        encoding="utf-8",
    )
    retained_lock_path = versions_root / "1.0.0" / LOCK_NAME
    retained_lock = json.loads(retained_lock_path.read_text(encoding="utf-8"))
    retained_lock["files"]["SKILL.md"] = hashlib.sha256(
        retained_skill.read_bytes()
    ).hexdigest()
    retained_lock_path.write_bytes(canonical_json_bytes(retained_lock))
    candidates = service.projection_candidates(package_id)
    assert [manifest["version"] for _root, manifest in candidates] == ["2.0.0"]

    (versions_root / "2.0.0" / "SKILL.md").write_text(
        "---\nname: locked-migration\n---\nTampered current version.\n",
        encoding="utf-8",
    )
    source = _write_package_source(
        tmp_path / "3.0.0",
        package_id=package_id,
        skill_name="locked-migration",
        version="3.0.0",
    )
    package = service.export_dev(
        source,
        tmp_path / "locked-migration-3.0.0.vsk",
    ).package_path
    before = _tree_snapshot(service.skill_store)

    with pytest.raises(PackageIntegrityError):
        service.install(package, source="locked-migration-test")

    assert _tree_snapshot(service.skill_store) == before


def test_installed_projection_candidate_entry_cap_fails_before_descent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SkillPackageService(
        tmp_path / "skill-packages",
        vrcforge_version="0.0.0",
        max_file_count=1,
    )
    version_root = (
        service.skill_store
        / "community.tests.entry-cap"
        / "versions"
        / "1.0.0"
    )
    for index in range(19):
        (version_root / f"directory-{index:02d}").mkdir(parents=True)

    original_iterdir = Path.iterdir

    def guarded_iterdir(path: Path):
        if path != version_root:
            raise AssertionError("verifier descended after the entry cap was exceeded")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    with pytest.raises(PackageIntegrityError, match="too many entries"):
        service._verified_installed_projection_candidate(  # noqa: SLF001 - bounded verifier regression.
            "community.tests.entry-cap",
            "1.0.0",
        )


def test_package_enable_toggle_preserves_signed_projection_and_audit_identity(
    tmp_path: Path,
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
    controller = _controller(service, _projection(gateway))

    imported = controller.import_package(
        {"packagePath": str(package)}
    )
    projected_skill = Path(imported["projectedSkill"]["path"])
    original_bytes = projected_skill.read_bytes()
    initial_context = _runtime_snapshot(gateway, "projection-toggle").package_audit_context

    controller.set_enabled(
        {"skillPackageId": "community.tests.projection-toggle", "enabled": False}
    )
    assert _runtime_snapshot(gateway, "projection-toggle").skill["enabled"] is False
    controller.set_enabled(
        {"skillPackageId": "community.tests.projection-toggle", "enabled": True}
    )
    final_context = _runtime_snapshot(gateway, "projection-toggle").package_audit_context

    assert projected_skill.read_bytes() == original_bytes
    assert (projected_skill.parent / PROJECTED_SKILL_STATE_NAME).is_file()
    assert initial_context["signerFingerprint"] == key_pair.fingerprint
    assert final_context == initial_context

    gateway.skills.create_user_skill({"name": "unrelated-user-skill", "instructions": "Keep package bytes intact."})
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
    projection = _projection(gateway)
    controller = _controller(service, projection)
    imported = controller.import_package(
        {"packagePath": str(package)}
    )
    projected_skill = Path(imported["projectedSkill"]["path"])
    state_path = projected_skill.parent / PROJECTED_SKILL_STATE_NAME
    installed_path = service.skill_store / "community.tests.projection-toggle-rollback" / "installed.json"
    original_registry = service.registry_path.read_bytes()
    original_installed = installed_path.read_bytes()
    original_state = state_path.read_bytes()

    def fail_state_write(
        _owner: SkillPackageProjectionService,
        _target_dir: Path,
        _enabled: bool,
        _package_id: str,
        _projection_digest: str,
    ) -> Path:
        raise OSError("injected projected state failure")

    monkeypatch.setattr(
        SkillPackageProjectionService,
        "_write_state",
        fail_state_write,
    )
    with pytest.raises(OSError, match="injected projected state failure"):
        controller.set_enabled(
            {"skillPackageId": "community.tests.projection-toggle-rollback", "enabled": False}
        )

    assert service.registry_path.read_bytes() == original_registry
    assert installed_path.read_bytes() == original_installed
    assert state_path.read_bytes() == original_state
    assert service.list_installed()[0]["enabled"] is True
    assert _runtime_snapshot(gateway, "projection-toggle-rollback").skill["enabled"] is True


def test_safe_mode_projection_failure_restores_all_registry_and_projection_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SkillPackageService(tmp_path / "skill-packages", vrcforge_version="0.0.0")
    key_pair = service.generate_signing_keypair()
    service.trust_signer(key_pair.fingerprint)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    projection = _projection(gateway)
    controller = _controller(service, projection)
    governance = _governance(service, projection)
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
        imported = controller.import_package(
            {"packagePath": str(package)}
        )
        projected_skill = Path(imported["projectedSkill"]["path"])
        state_paths.append(projected_skill.parent / PROJECTED_SKILL_STATE_NAME)
        installed_paths.append(service.skill_store / package_id / "installed.json")

    original_registry = service.registry_path.read_bytes()
    original_installed = {path: path.read_bytes() for path in installed_paths}
    original_states = {path: path.read_bytes() for path in state_paths}
    original_write_state = SkillPackageProjectionService._write_state
    write_count = 0

    def fail_second_state_write(
        owner: SkillPackageProjectionService,
        target_dir: Path,
        enabled: bool,
        package_id: str,
        projection_digest: str,
    ) -> Path:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("injected safe-mode projection failure")
        return original_write_state(
            owner,
            target_dir,
            enabled,
            package_id,
            projection_digest,
        )

    monkeypatch.setattr(
        SkillPackageProjectionService,
        "_write_state",
        fail_second_state_write,
    )
    with pytest.raises(OSError, match="injected safe-mode projection failure"):
        governance.set_safe_mode(
            {"enabled": True, "reason": "test rollback"}
        )

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
    projection = _projection(gateway)
    controller = _controller(service, projection)
    governance = _governance(service, projection)
    imported = controller.import_package(
        {"packagePath": str(package)}
    )
    state_path = Path(imported["projectedSkill"]["path"]).parent / PROJECTED_SKILL_STATE_NAME
    installed_path = service.skill_store / package_id / "installed.json"
    original_registry = service.registry_path.read_bytes()
    original_installed = installed_path.read_bytes()
    original_state = state_path.read_bytes()

    def fail_state_write(
        _owner: SkillPackageProjectionService,
        _target_dir: Path,
        _enabled: bool,
        _package_id: str,
        _projection_digest: str,
    ) -> Path:
        raise OSError(f"injected {operation} projection failure")

    monkeypatch.setattr(
        SkillPackageProjectionService,
        "_write_state",
        fail_state_write,
    )
    with pytest.raises(OSError, match=f"injected {operation} projection failure"):
        if operation == "revoke":
            governance.revoke_signer(
                {"signerFingerprint": key_pair.fingerprint, "reason": "test rollback"}
            )
        else:
            governance.block_package(
                {"packageId": package_id, "reason": "test rollback"}
            )

    assert service.registry_path.read_bytes() == original_registry
    assert installed_path.read_bytes() == original_installed
    assert state_path.read_bytes() == original_state
    assert service.list_installed()[0]["enabled"] is True
    assert _runtime_snapshot(gateway, skill_name).skill["enabled"] is True


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
    projection = _projection(gateway)
    controller = _controller(service, projection)
    imported = controller.import_package(
        {"packagePath": str(package)}
    )
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
    original_projection_transaction = projection.delete_transaction

    @contextmanager
    def fail_after_projection_isolated(manifest: dict[str, object]):
        with original_projection_transaction(manifest) as projected:
            assert not projection_root.exists()
            raise OSError("injected uninstall projection failure")
            yield projected

    failing_controller = SkillPackageController(
        replace(
            controller._ports,  # noqa: SLF001 - local typed rollback fixture.
            delete_projected_skill=fail_after_projection_isolated,
        )
    )
    with pytest.raises(OSError, match="injected uninstall projection failure"):
        failing_controller.uninstall({"skillPackageId": package_id})

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

    owner = SkillPackageGovernanceService(
        SkillPackageGovernancePorts(
            make_service=TrustService,
            write_lock=lock,
            disable_projected_skills=lambda _manifests: pytest.fail(
                "trust must not update projections"
            ),
        )
    )

    result = owner.trust_signer(
        {"signerFingerprint": "a" * 64, "reason": "lock regression"}
    )

    assert result["signer"]["ok"] is True
    assert lock.active is False


def test_runtime_support_limit_is_checked_before_file_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    skill_root = gateway.skills.user_skills_dir / "bounded-support"
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
        gateway.skills.load_runtime_skill_support_files(
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
    _projection(gateway).project_installed(
        installed.installed_path,
        installed.preview.manifest,
    )
    projected_skill = gateway.skills.user_skills_dir / "audit-size" / "SKILL.md"
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

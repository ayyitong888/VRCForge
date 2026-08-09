from __future__ import annotations

import ast
import hashlib
import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable

import pytest

import dashboard_server
import skill_package_projection as projection_module
from agent_gateway import (
    AgentGatewayError,
    PROJECTED_SKILL_STATE_MAX_BYTES,
    PROJECTED_SKILL_STATE_NAME,
    PROJECTED_SKILL_STATE_SCHEMA,
)
from skill_package_projection import (
    SkillPackageProjectionPorts,
    SkillPackageProjectionService,
)
from skill_packages import SkillPackageError, SkillPackageService


ROOT = Path(__file__).parents[1]
LEGACY_ROOTS = {
    "_skill_projection_path_is_link_like",
    "_resolve_skill_projection_source",
    "_copy_projected_skill_file",
    "_write_projected_skill_state",
    "_capture_projected_skill_state",
    "_restore_projected_skill_state",
    "_set_projected_skills_enabled",
    "_project_installed_skill",
    "_projected_skill_name",
    "_set_projected_skill_enabled",
    "_delete_projected_skill_transaction",
}
LEGACY_PROJECTED_SKILL_STATE_SCHEMA = "vrcforge.projected-skill-state.v1"
V2_PROJECTED_SKILL_STATE_SCHEMA = "vrcforge.projected-skill-state.v2"


def _projection(
    root: Path,
    *,
    find_user_skill: Callable[[str], dict[str, Any] | None] | None = None,
    validate_projection_name: Callable[[str], None] | None = None,
    parse_skill: Callable[[Path], dict[str, Any]] | None = None,
    user_skill_lock: AbstractContextManager[object] | None = None,
    installed_package_candidates: Callable[
        [str],
        tuple[tuple[Path, dict[str, Any]], ...],
    ]
    | None = None,
) -> SkillPackageProjectionService:
    return SkillPackageProjectionService(
        SkillPackageProjectionPorts(
            user_skills_dir=lambda: root,
            user_skill_lock=user_skill_lock or nullcontext(),
            find_user_skill=find_user_skill or (lambda name: {"name": name}),
            validate_projection_name=validate_projection_name or (lambda _name: None),
            make_conflict_error=lambda message: AgentGatewayError(
                message,
                status_code=409,
            ),
            parse_skill=parse_skill or dashboard_server.parse_skill_markdown,
            parse_error_types=(AgentGatewayError,),
            installed_package_candidates=(
                installed_package_candidates or (lambda _package_id: ())
            ),
            state_name=PROJECTED_SKILL_STATE_NAME,
            state_schema=PROJECTED_SKILL_STATE_SCHEMA,
            state_max_bytes=PROJECTED_SKILL_STATE_MAX_BYTES,
        )
    )


def _installed(
    root: Path,
    *,
    name: str = "projection-test",
    instructions: str = "Read project state.",
) -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\n---\n{instructions}\n",
        encoding="utf-8",
    )
    return root


def _manifest(
    skill_name: str,
    *,
    package_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": package_id or f"community.tests.{skill_name}",
        "skill_name": skill_name,
        "entrypoints": {"skill": "SKILL.md"},
    }


def _write_owned_projection(
    service: SkillPackageProjectionService,
    target: Path,
    package_id: str,
    *,
    enabled: bool = True,
) -> None:
    service._write_state(  # noqa: SLF001 - exact owner-state fixture.
        target,
        enabled,
        package_id,
        service._projection_digest(target),  # noqa: SLF001 - owner digest contract.
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    return {
        path.relative_to(root).as_posix(): (
            ("dir", b"") if path.is_dir() else ("file", path.read_bytes())
        )
        for path in root.rglob("*")
    }


def _legacy_projection_digest(target_dir: Path) -> str:
    digest = hashlib.sha256()
    for source in sorted(
        (
            path
            for path in target_dir.rglob("*")
            if path.is_file() and path.name != PROJECTED_SKILL_STATE_NAME
        ),
        key=lambda item: item.relative_to(target_dir).as_posix().casefold(),
    ):
        digest.update(source.relative_to(target_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _write_legacy_projection(
    skills_root: Path,
    installed: Path,
    skill_name: str,
    *,
    enabled: bool,
) -> Path:
    target = skills_root / skill_name
    target.mkdir(parents=True)
    target.joinpath("SKILL.md").write_bytes(installed.joinpath("SKILL.md").read_bytes())
    target.joinpath(PROJECTED_SKILL_STATE_NAME).write_text(
        json.dumps(
            {
                "enabled": enabled,
                "schema": LEGACY_PROJECTED_SKILL_STATE_SCHEMA,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def test_projection_owner_has_four_typed_public_operations_and_no_host_seam() -> None:
    source = (ROOT / "skill_package_projection.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "SkillPackageProjectionService"
    )
    public = {
        node.name
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }

    assert public == {
        "resolve_source",
        "set_enabled_batch",
        "project_installed",
        "delete_transaction",
    }
    assert SkillPackageProjectionService.__slots__ == ("_ports",)
    assert [field.name for field in fields(SkillPackageProjectionPorts)] == [
        "user_skills_dir",
        "user_skill_lock",
        "find_user_skill",
        "validate_projection_name",
        "make_conflict_error",
        "parse_skill",
        "parse_error_types",
        "installed_package_candidates",
        "state_name",
        "state_schema",
        "state_max_bytes",
    ]
    for forbidden in (
        "_host",
        "__getattr__",
        "sys.modules",
        "AgentGateway",
        "SKILL_PACKAGE_WRITE_LOCK",
        "register_write_handler",
        "register_tool",
    ):
        assert forbidden not in source


def test_dashboard_binds_one_projection_owner_and_removes_all_legacy_roots() -> None:
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    bindings = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
    }
    functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }

    assert "SKILL_PACKAGE_PROJECTION" in bindings
    assert "_SKILL_PACKAGE_PROJECTION" not in bindings
    assert not LEGACY_ROOTS.intersection(functions)
    ports = dashboard_server.SKILL_PACKAGE_PROJECTION._ports  # noqa: SLF001
    assert ports.user_skills_dir.__defaults__ == (
        dashboard_server.AGENT_GATEWAY.skills,
    )
    assert ports.user_skill_lock is dashboard_server.AGENT_GATEWAY.skills.write_lock
    assert ports.find_user_skill.__defaults__ == (
        dashboard_server.AGENT_GATEWAY.skills,
    )
    assert ports.validate_projection_name.__self__ is dashboard_server.AGENT_GATEWAY.skills
    assert ports.parse_skill is dashboard_server.parse_skill_markdown
    assert ports.parse_error_types == (dashboard_server.AgentGatewayError,)
    assert isinstance(
        ports.installed_package_candidates.__self__,
        SkillPackageService,
    )
    assert ports.installed_package_candidates.__func__ is (
        SkillPackageService.projection_candidates
    )
    assert ports.installed_package_candidates.__self__.skill_store == (
        dashboard_server.skill_package_service().skill_store
    )
    assert ports.state_name == PROJECTED_SKILL_STATE_NAME
    assert ports.state_schema == PROJECTED_SKILL_STATE_SCHEMA
    assert ports.state_max_bytes == PROJECTED_SKILL_STATE_MAX_BYTES

    controller_ports = dashboard_server.SKILL_PACKAGE_CONTROLLER._ports  # noqa: SLF001
    assert controller_ports.project_installed_skill.__defaults__ == (
        dashboard_server.SKILL_PACKAGE_PROJECTION,
    )
    assert controller_ports.set_projected_skill_enabled.__defaults__ == (
        dashboard_server.SKILL_PACKAGE_PROJECTION,
    )
    assert controller_ports.delete_projected_skill.__self__ is (
        dashboard_server.SKILL_PACKAGE_PROJECTION
    )
    governance_ports = dashboard_server.SKILL_PACKAGE_GOVERNANCE._ports  # noqa: SLF001
    assert governance_ports.disable_projected_skills.__defaults__ == (
        dashboard_server.SKILL_PACKAGE_PROJECTION,
    )


def test_v2_digest_length_prefixes_separate_legacy_file_boundary_collision(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first.joinpath("a").write_bytes(b"X")
    first.joinpath("b").write_bytes(b"Y")
    second.joinpath("a").write_bytes(b"X\0b\0Y")
    service = _projection(tmp_path / "skills")

    assert _legacy_projection_digest(first) == _legacy_projection_digest(second)
    assert service._projection_digest(first) != service._projection_digest(second)  # noqa: SLF001
    assert PROJECTED_SKILL_STATE_SCHEMA == V2_PROJECTED_SKILL_STATE_SCHEMA


def test_v2_digest_includes_empty_directories(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first.joinpath("SKILL.md").write_bytes(b"same\n")
    second.joinpath("SKILL.md").write_bytes(b"same\n")
    second.joinpath("empty").mkdir()
    service = _projection(tmp_path / "skills")

    assert _legacy_projection_digest(first) == _legacy_projection_digest(second)
    assert service._projection_digest(first) != service._projection_digest(second)  # noqa: SLF001


def test_projection_digest_stops_before_descending_after_entry_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "capped"
    target.mkdir()
    for name in ("one", "two", "three"):
        (target / name).mkdir()
    service = _projection(tmp_path / "skills")
    original_link_check = SkillPackageProjectionService._path_is_link_like

    def reject_descent(path: Path) -> bool:
        if path.parent != target and path != target:
            raise AssertionError("digest descended after the entry cap was exceeded")
        return original_link_check(path)

    monkeypatch.setattr(projection_module, "PROJECTION_MAX_ENTRIES", 2)
    monkeypatch.setattr(
        SkillPackageProjectionService,
        "_path_is_link_like",
        staticmethod(reject_descent),
    )

    with pytest.raises(AgentGatewayError, match="too many filesystem entries"):
        service._projection_digest(target)  # noqa: SLF001 - bounded digest gate.


def test_batch_rejects_linked_later_parent_before_any_state_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = tmp_path / "skills"
    first = skills_root / "first"
    second = skills_root / "second"
    service = _projection(skills_root)
    for target in (first, second):
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            f"---\nname: {target.name}\n---\nRead only.\n",
            encoding="utf-8",
        )
        _write_owned_projection(
            service,
            target,
            f"community.tests.{target.name}",
        )
    original_first = (first / PROJECTED_SKILL_STATE_NAME).read_bytes()
    writes: list[Path] = []
    original_link_check = SkillPackageProjectionService._path_is_link_like

    def simulated_link(path: Path) -> bool:
        return path == second or original_link_check(path)

    def track_write(
        _self: SkillPackageProjectionService,
        target: Path,
        _enabled: bool,
        _package_id: str,
        _projection_digest: str,
    ) -> Path:
        writes.append(target)
        raise AssertionError("capture must fail before writes")

    monkeypatch.setattr(
        SkillPackageProjectionService,
        "_path_is_link_like",
        staticmethod(simulated_link),
    )
    monkeypatch.setattr(SkillPackageProjectionService, "_write_state", track_write)

    with pytest.raises(RuntimeError, match="not a safe directory: second"):
        service.set_enabled_batch(
            [_manifest("first"), _manifest("second")],
            False,
        )

    assert writes == []
    assert (first / PROJECTED_SKILL_STATE_NAME).read_bytes() == original_first


def test_project_rejects_dangling_link_target_before_parse_or_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    target = skills_root / "dangling"
    installed = _installed(tmp_path / "installed", name="dangling")
    real_lexists = projection_module.os.path.lexists
    real_link_check = SkillPackageProjectionService._path_is_link_like
    parse_calls: list[Path] = []
    find_calls: list[str] = []

    monkeypatch.setattr(
        projection_module.os.path,
        "lexists",
        lambda path: path == target or real_lexists(path),
    )
    monkeypatch.setattr(
        SkillPackageProjectionService,
        "_path_is_link_like",
        staticmethod(
            lambda path: path == target or real_link_check(path)
        ),
    )
    service = _projection(
        skills_root,
        parse_skill=lambda path: parse_calls.append(path) or {"supportFiles": []},
        find_user_skill=lambda name: find_calls.append(name) or None,
    )

    with pytest.raises(RuntimeError, match="symlinked skill directory"):
        service.project_installed(
            installed,
            _manifest("dangling"),
        )

    assert parse_calls == []
    assert find_calls == []
    assert not target.exists()


def test_reserved_projection_name_fails_before_parse_or_staging(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    installed = _installed(tmp_path / "installed", name="know-yourself")
    parse_calls: list[Path] = []
    find_calls: list[str] = []

    def reject(name: str) -> None:
        assert name == "know-yourself"
        raise AgentGatewayError("reserved projection name", status_code=409)

    service = _projection(
        skills_root,
        validate_projection_name=reject,
        parse_skill=lambda path: parse_calls.append(path) or {"supportFiles": []},
        find_user_skill=lambda name: find_calls.append(name) or None,
    )

    with pytest.raises(AgentGatewayError, match="reserved projection name") as error:
        service.project_installed(
            installed,
            _manifest("know-yourself"),
        )

    assert error.value.status_code == 409
    assert parse_calls == []
    assert find_calls == []
    assert not skills_root.exists()


def test_projection_rejects_parsed_name_mismatch_before_publish(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    installed = _installed(tmp_path / "installed", name="parsed-name")
    service = _projection(skills_root)

    with pytest.raises(
        SkillPackageError,
        match="name must match the projected skill name",
    ):
        service.project_installed(installed, _manifest("manifest-name"))

    assert not skills_root.exists()


def test_projection_rejects_manual_user_skill_collision_without_writes(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    target = skills_root / "manual-collision"
    target.mkdir(parents=True)
    target.joinpath("SKILL.md").write_text(
        "---\nname: manual-collision\n---\nManual user instructions.\n",
        encoding="utf-8",
    )
    before = _tree_bytes(skills_root)
    installed = _installed(tmp_path / "installed", name="manual-collision")
    service = _projection(skills_root)

    with pytest.raises(
        AgentGatewayError,
        match="not owned by this package",
    ) as error:
        service.project_installed(installed, _manifest("manual-collision"))

    assert error.value.status_code == 409
    assert _tree_bytes(skills_root) == before


def test_package_b_cannot_replace_package_a_projection(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    service = _projection(skills_root)
    package_a = _manifest(
        "shared-projection",
        package_id="community.tests.package-a",
    )
    package_b = _manifest(
        "shared-projection",
        package_id="community.tests.package-b",
    )
    service.project_installed(
        _installed(
            tmp_path / "installed-a",
            name="shared-projection",
            instructions="Owned by A.",
        ),
        package_a,
    )
    target = skills_root / "shared-projection"
    state_path = target / PROJECTED_SKILL_STATE_NAME
    original_state = state_path.read_bytes()
    before = _tree_snapshot(skills_root)

    with pytest.raises(
        AgentGatewayError,
        match="belongs to another owner",
    ) as error:
        service.project_installed(
            _installed(
                tmp_path / "installed-b",
                name="shared-projection",
                instructions="Owned by B.",
            ),
            package_b,
        )

    assert error.value.status_code == 409
    assert _tree_snapshot(skills_root) == before
    assert state_path.read_bytes() == original_state


def test_same_owner_can_upgrade_an_unmodified_projection(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    service = _projection(skills_root)
    manifest = _manifest(
        "owned-upgrade",
        package_id="community.tests.owned-upgrade",
    )
    service.project_installed(
        _installed(
            tmp_path / "installed-v1",
            name="owned-upgrade",
            instructions="Version one.",
        ),
        manifest,
    )

    result = service.project_installed(
        _installed(
            tmp_path / "installed-v2",
            name="owned-upgrade",
            instructions="Version two.",
        ),
        manifest,
    )

    target = skills_root / "owned-upgrade"
    state = json.loads(
        target.joinpath(PROJECTED_SKILL_STATE_NAME).read_text(encoding="utf-8")
    )
    assert result is not None
    assert "Version two." in target.joinpath("SKILL.md").read_text(encoding="utf-8")
    assert state["packageId"] == "community.tests.owned-upgrade"
    assert state["projectionDigest"] == service._projection_digest(target)  # noqa: SLF001


def test_matching_installed_package_atomically_migrates_v1_state_and_preserves_enabled(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    installed = _installed(
        tmp_path / "installed",
        name="legacy-owned",
        instructions="Exact installed projection.",
    )
    target = _write_legacy_projection(
        skills_root,
        installed,
        "legacy-owned",
        enabled=False,
    )
    old_state = target.joinpath(PROJECTED_SKILL_STATE_NAME).read_bytes()
    manifest = _manifest(
        "legacy-owned",
        package_id="community.tests.legacy-owned",
    )
    service = _projection(
        skills_root,
        installed_package_candidates=lambda package_id: (
            ((installed, manifest),)
            if package_id == "community.tests.legacy-owned"
            else ()
        ),
    )

    result = service.project_installed(
        installed,
        manifest,
        enabled=True,
    )

    state_path = target / PROJECTED_SKILL_STATE_NAME
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert result is not None
    assert result["enabled"] is False
    assert state_path.read_bytes() != old_state
    assert state == {
        "enabled": False,
        "packageId": "community.tests.legacy-owned",
        "projectionDigest": service._projection_digest(target),  # noqa: SLF001
        "schema": V2_PROJECTED_SKILL_STATE_SCHEMA,
    }
    assert not list(target.glob(f".{PROJECTED_SKILL_STATE_NAME}.*.tmp"))


def test_v1_migration_publish_failure_restores_exact_legacy_state(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    installed = _installed(
        tmp_path / "installed",
        name="legacy-rollback",
        instructions="Exact installed projection.",
    )
    target = _write_legacy_projection(
        skills_root,
        installed,
        "legacy-rollback",
        enabled=False,
    )
    before = _tree_snapshot(skills_root)
    original_state = target.joinpath(PROJECTED_SKILL_STATE_NAME).read_bytes()
    manifest = _manifest(
        "legacy-rollback",
        package_id="community.tests.legacy-rollback",
    )
    service = _projection(
        skills_root,
        find_user_skill=lambda _name: (_ for _ in ()).throw(
            OSError("injected post-migration lookup failure")
        ),
        installed_package_candidates=lambda package_id: (
            ((installed, manifest),)
            if package_id == "community.tests.legacy-rollback"
            else ()
        ),
    )

    with pytest.raises(OSError, match="post-migration lookup failure"):
        service.project_installed(
            installed,
            manifest,
        )

    assert _tree_snapshot(skills_root) == before
    assert target.joinpath(PROJECTED_SKILL_STATE_NAME).read_bytes() == original_state


@pytest.mark.parametrize(
    "mismatch",
    ["modified-projection", "extra-directory", "wrong-installed-source"],
)
def test_v1_migration_rejects_non_exact_projection_and_preserves_tree(
    tmp_path: Path,
    mismatch: str,
) -> None:
    skills_root = tmp_path / "skills"
    installed = _installed(
        tmp_path / "installed",
        name="legacy-mismatch",
        instructions="Expected installed projection.",
    )
    target = _write_legacy_projection(
        skills_root,
        installed,
        "legacy-mismatch",
        enabled=False,
    )
    migration_source = installed
    if mismatch == "modified-projection":
        target.joinpath("SKILL.md").write_text(
            "---\nname: legacy-mismatch\n---\nModified after projection.\n",
            encoding="utf-8",
        )
    elif mismatch == "extra-directory":
        target.joinpath("unexpected-empty-directory").mkdir()
    else:
        migration_source = _installed(
            tmp_path / "wrong-installed",
            name="legacy-mismatch",
            instructions="Wrong installed package source.",
        )
    before = _tree_snapshot(skills_root)
    original_state = target.joinpath(PROJECTED_SKILL_STATE_NAME).read_bytes()
    manifest = _manifest(
        "legacy-mismatch",
        package_id="community.tests.legacy-mismatch",
    )
    service = _projection(
        skills_root,
        installed_package_candidates=lambda package_id: (
            ((migration_source, manifest),)
            if package_id == "community.tests.legacy-mismatch"
            else ()
        ),
    )

    with pytest.raises(AgentGatewayError) as error:
        service.project_installed(
            migration_source,
            manifest,
        )

    assert error.value.status_code == 409
    assert _tree_snapshot(skills_root) == before
    assert target.joinpath(PROJECTED_SKILL_STATE_NAME).read_bytes() == original_state


def test_same_owner_cannot_replace_a_modified_projection(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    service = _projection(skills_root)
    manifest = _manifest(
        "modified-projection",
        package_id="community.tests.modified-projection",
    )
    service.project_installed(
        _installed(
            tmp_path / "installed-v1",
            name="modified-projection",
            instructions="Package version.",
        ),
        manifest,
    )
    target = skills_root / "modified-projection"
    target.joinpath("SKILL.md").write_text(
        "---\nname: modified-projection\n---\nManual modification.\n",
        encoding="utf-8",
    )
    before = _tree_bytes(skills_root)

    with pytest.raises(
        AgentGatewayError,
        match="modified outside its owning package",
    ) as error:
        service.project_installed(
            _installed(
                tmp_path / "installed-v2",
                name="modified-projection",
                instructions="Package upgrade.",
            ),
            manifest,
        )

    assert error.value.status_code == 409
    assert _tree_bytes(skills_root) == before


@pytest.mark.parametrize("operation", ["toggle", "delete"])
def test_package_a_cannot_toggle_or_delete_package_b_projection(
    tmp_path: Path,
    operation: str,
) -> None:
    skills_root = tmp_path / "skills"
    service = _projection(skills_root)
    package_b = _manifest(
        "owned-by-b",
        package_id="community.tests.package-b",
    )
    service.project_installed(
        _installed(tmp_path / "installed-b", name="owned-by-b"),
        package_b,
    )
    before = _tree_bytes(skills_root)
    package_a = _manifest(
        "owned-by-b",
        package_id="community.tests.package-a",
    )

    with pytest.raises(
        AgentGatewayError,
        match="belongs to another owner",
    ) as error:
        if operation == "toggle":
            service.set_enabled_batch([package_a], False)
        else:
            with service.delete_transaction(package_a):
                pytest.fail("foreign owner must not isolate the projection")

    assert error.value.status_code == 409
    assert _tree_bytes(skills_root) == before


@pytest.mark.parametrize("has_previous", [False, True])
def test_runtime_lookup_failure_restores_fresh_or_previous_projection(
    tmp_path: Path,
    has_previous: bool,
) -> None:
    skills_root = tmp_path / "skills"
    target = skills_root / "projection-test"
    manifest = _manifest(
        "projection-test",
        package_id="community.tests.projection",
    )
    previous: dict[str, bytes] | None = None
    service: SkillPackageProjectionService
    if has_previous:
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            "---\nname: projection-test\n---\nPrevious projection.\n",
            encoding="utf-8",
        )
        (target / "old-support.json").write_text("{}\n", encoding="utf-8")
    installed = _installed(tmp_path / "installed")

    def fail_lookup(_name: str) -> dict[str, Any] | None:
        raise OSError("injected runtime lookup failure")

    service = _projection(skills_root, find_user_skill=fail_lookup)
    if has_previous:
        _write_owned_projection(
            service,
            target,
            "community.tests.projection",
        )
        previous = _tree_bytes(target)

    with pytest.raises(OSError, match="injected runtime lookup failure"):
        service.project_installed(installed, manifest)

    if previous is None:
        assert not target.exists()
    else:
        assert _tree_bytes(target) == previous
    staging = skills_root / ".package-projection-staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_lookup_rollback_falls_back_to_delete_new_then_restores_old(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = tmp_path / "skills"
    target = skills_root / "projection-test"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: projection-test\n---\nPrevious projection.\n",
        encoding="utf-8",
    )
    installed = _installed(tmp_path / "installed")
    real_replace = projection_module.os.replace

    def replace_with_one_rollback_failure(source: Path, destination: Path) -> None:
        if source == target and destination.name.endswith(".new"):
            raise OSError("injected new-target isolation failure")
        real_replace(source, destination)

    monkeypatch.setattr(projection_module.os, "replace", replace_with_one_rollback_failure)
    service = _projection(
        skills_root,
        find_user_skill=lambda _name: (_ for _ in ()).throw(
            OSError("injected runtime lookup failure")
        ),
    )
    _write_owned_projection(
        service,
        target,
        "community.tests.projection-test",
    )
    previous = _tree_bytes(target)

    with pytest.raises(OSError, match="injected runtime lookup failure"):
        service.project_installed(installed, _manifest("projection-test"))

    assert _tree_bytes(target) == previous
    staging = skills_root / ".package-projection-staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_lookup_rollback_failure_preserves_old_backup_and_reports_its_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = tmp_path / "skills"
    target = skills_root / "projection-test"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: projection-test\n---\nPrevious projection.\n",
        encoding="utf-8",
    )
    installed = _installed(tmp_path / "installed")
    real_replace = projection_module.os.replace
    real_rmtree = projection_module.shutil.rmtree

    def fail_new_target_isolation(source: Path, destination: Path) -> None:
        if source == target and destination.name.endswith(".new"):
            raise OSError("injected new-target isolation failure")
        real_replace(source, destination)

    def fail_new_target_delete(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == target:
            raise OSError("injected new-target delete failure")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(projection_module.os, "replace", fail_new_target_isolation)
    monkeypatch.setattr(projection_module.shutil, "rmtree", fail_new_target_delete)
    service = _projection(
        skills_root,
        find_user_skill=lambda _name: (_ for _ in ()).throw(
            OSError("injected runtime lookup failure")
        ),
    )
    _write_owned_projection(
        service,
        target,
        "community.tests.projection-test",
    )
    previous = _tree_bytes(target)

    with pytest.raises(RuntimeError, match="recovery data remains at:") as error:
        service.project_installed(installed, _manifest("projection-test"))

    staging = skills_root / ".package-projection-staging"
    backups = list(staging.glob("projection-test.*.old"))
    assert len(backups) == 1
    assert _tree_bytes(backups[0]) == previous
    assert str(backups[0]) in str(error.value)
    assert target.is_dir()


def test_lookup_restore_failure_preserves_backup_after_new_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = tmp_path / "skills"
    target = skills_root / "projection-test"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: projection-test\n---\nPrevious projection.\n",
        encoding="utf-8",
    )
    installed = _installed(tmp_path / "installed")
    real_replace = projection_module.os.replace

    def fail_backup_restore(source: Path, destination: Path) -> None:
        if source.name.endswith(".old") and destination == target:
            raise OSError("injected old-backup restore failure")
        real_replace(source, destination)

    monkeypatch.setattr(projection_module.os, "replace", fail_backup_restore)
    service = _projection(
        skills_root,
        find_user_skill=lambda _name: (_ for _ in ()).throw(
            OSError("injected runtime lookup failure")
        ),
    )
    _write_owned_projection(
        service,
        target,
        "community.tests.projection-test",
    )
    previous = _tree_bytes(target)

    with pytest.raises(RuntimeError, match="recovery data remains at:") as error:
        service.project_installed(installed, _manifest("projection-test"))

    staging = skills_root / ".package-projection-staging"
    backups = list(staging.glob("projection-test.*.old"))
    assert len(backups) == 1
    assert _tree_bytes(backups[0]) == previous
    assert str(backups[0]) in str(error.value)
    assert not target.exists()
    assert not list(staging.glob("projection-test.*.new"))


def test_delete_transaction_restores_isolated_projection_on_caller_failure(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    target = skills_root / "delete-me"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: delete-me\n---\nKeep on rollback.\n",
        encoding="utf-8",
    )
    service = _projection(skills_root)
    _write_owned_projection(service, target, "community.tests.delete-me")
    previous = _tree_bytes(target)

    with pytest.raises(RuntimeError, match="caller failure"):
        with service.delete_transaction(_manifest("delete-me")) as result:
            assert result["deleted"] == "delete-me"
            assert not target.exists()
            raise RuntimeError("caller failure")

    assert _tree_bytes(target) == previous


def test_projection_writes_hold_borrowed_user_lock_but_resolve_is_read_only(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class TrackingLock:
        active = False

        def __enter__(self) -> object:
            assert self.active is False
            self.active = True
            events.append("enter")
            return self

        def __exit__(self, *_args: object) -> None:
            assert self.active is True
            self.active = False
            events.append("exit")

    lock = TrackingLock()
    skills_root = tmp_path / "skills"
    target = skills_root / "locked"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: locked\n---\nRead only.\n",
        encoding="utf-8",
    )
    installed = _installed(tmp_path / "installed", name="project-lock")

    def find(name: str) -> dict[str, Any]:
        assert lock.active is True
        return {"name": name}

    service = _projection(
        skills_root,
        find_user_skill=find,
        user_skill_lock=lock,
    )
    _write_owned_projection(service, target, "community.tests.locked")

    service.resolve_source(installed, "SKILL.md", label="skill entrypoint")
    assert events == []
    service.project_installed(
        installed,
        _manifest("project-lock"),
    )
    assert events == ["enter", "exit"]
    service.set_enabled_batch([_manifest("locked")], False)
    assert events == ["enter", "exit", "enter", "exit"]
    with service.delete_transaction(_manifest("locked")):
        assert lock.active is True
    assert events == [
        "enter",
        "exit",
        "enter",
        "exit",
        "enter",
        "exit",
    ]

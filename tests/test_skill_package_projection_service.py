from __future__ import annotations

import ast
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


def _projection(
    root: Path,
    *,
    find_user_skill: Callable[[str], dict[str, Any] | None] | None = None,
    parse_skill: Callable[[Path], dict[str, Any]] | None = None,
    user_skill_lock: AbstractContextManager[object] | None = None,
) -> SkillPackageProjectionService:
    return SkillPackageProjectionService(
        SkillPackageProjectionPorts(
            user_skills_dir=lambda: root,
            user_skill_lock=user_skill_lock or nullcontext(),
            find_user_skill=find_user_skill or (lambda name: {"name": name}),
            parse_skill=parse_skill or (lambda _path: {"supportFiles": []}),
            parse_error_types=(AgentGatewayError,),
            state_name=PROJECTED_SKILL_STATE_NAME,
            state_schema=PROJECTED_SKILL_STATE_SCHEMA,
            state_max_bytes=PROJECTED_SKILL_STATE_MAX_BYTES,
        )
    )


def _installed(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: projection-test\n---\nRead project state.\n",
        encoding="utf-8",
    )
    return root


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


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
        "parse_skill",
        "parse_error_types",
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
        dashboard_server.AGENT_GATEWAY,
    )
    assert ports.user_skill_lock is dashboard_server.AGENT_GATEWAY.user_skill_lock
    assert ports.find_user_skill.__defaults__ == (
        dashboard_server.AGENT_GATEWAY,
    )
    assert ports.parse_skill is dashboard_server.parse_skill_markdown
    assert ports.parse_error_types == (dashboard_server.AgentGatewayError,)
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


def test_batch_rejects_linked_later_parent_before_any_state_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_root = tmp_path / "skills"
    first = skills_root / "first"
    second = skills_root / "second"
    for target in (first, second):
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("read only\n", encoding="utf-8")
        (target / PROJECTED_SKILL_STATE_NAME).write_text(
            json.dumps(
                {
                    "enabled": True,
                    "schema": PROJECTED_SKILL_STATE_SCHEMA,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    original_first = (first / PROJECTED_SKILL_STATE_NAME).read_bytes()
    service = _projection(skills_root)
    writes: list[Path] = []
    original_link_check = SkillPackageProjectionService._path_is_link_like

    def simulated_link(path: Path) -> bool:
        return path == second or original_link_check(path)

    def track_write(_self: SkillPackageProjectionService, target: Path, _enabled: bool) -> Path:
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
            [{"skill_name": "first"}, {"skill_name": "second"}],
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
    installed = _installed(tmp_path / "installed")
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
            {
                "skill_name": "dangling",
                "entrypoints": {"skill": "SKILL.md"},
            },
        )

    assert parse_calls == []
    assert find_calls == []
    assert not target.exists()


@pytest.mark.parametrize("has_previous", [False, True])
def test_runtime_lookup_failure_restores_fresh_or_previous_projection(
    tmp_path: Path,
    has_previous: bool,
) -> None:
    skills_root = tmp_path / "skills"
    target = skills_root / "projection-test"
    previous: dict[str, bytes] | None = None
    if has_previous:
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("previous projection\n", encoding="utf-8")
        (target / "old-support.json").write_text("{}\n", encoding="utf-8")
        previous = _tree_bytes(target)
    installed = _installed(tmp_path / "installed")

    def fail_lookup(_name: str) -> dict[str, Any] | None:
        raise OSError("injected runtime lookup failure")

    service = _projection(skills_root, find_user_skill=fail_lookup)

    with pytest.raises(OSError, match="injected runtime lookup failure"):
        service.project_installed(
            installed,
            {
                "id": "community.tests.projection",
                "skill_name": "projection-test",
                "entrypoints": {"skill": "SKILL.md"},
            },
        )

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
    (target / "SKILL.md").write_text("previous projection\n", encoding="utf-8")
    previous = _tree_bytes(target)
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

    with pytest.raises(OSError, match="injected runtime lookup failure"):
        service.project_installed(
            installed,
            {
                "skill_name": "projection-test",
                "entrypoints": {"skill": "SKILL.md"},
            },
        )

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
    (target / "SKILL.md").write_text("previous projection\n", encoding="utf-8")
    previous = _tree_bytes(target)
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

    with pytest.raises(RuntimeError, match="recovery data remains at:") as error:
        service.project_installed(
            installed,
            {
                "skill_name": "projection-test",
                "entrypoints": {"skill": "SKILL.md"},
            },
        )

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
    (target / "SKILL.md").write_text("previous projection\n", encoding="utf-8")
    previous = _tree_bytes(target)
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

    with pytest.raises(RuntimeError, match="recovery data remains at:") as error:
        service.project_installed(
            installed,
            {
                "skill_name": "projection-test",
                "entrypoints": {"skill": "SKILL.md"},
            },
        )

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
    (target / "SKILL.md").write_text("keep on rollback\n", encoding="utf-8")
    previous = _tree_bytes(target)
    service = _projection(skills_root)

    with pytest.raises(RuntimeError, match="caller failure"):
        with service.delete_transaction({"skill_name": "delete-me"}) as result:
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
    (target / "SKILL.md").write_text("read only\n", encoding="utf-8")
    (target / PROJECTED_SKILL_STATE_NAME).write_text(
        json.dumps(
            {"enabled": True, "schema": PROJECTED_SKILL_STATE_SCHEMA}
        )
        + "\n",
        encoding="utf-8",
    )
    installed = _installed(tmp_path / "installed")

    def find(name: str) -> dict[str, Any]:
        assert lock.active is True
        return {"name": name}

    service = _projection(
        skills_root,
        find_user_skill=find,
        user_skill_lock=lock,
    )

    service.resolve_source(installed, "SKILL.md", label="skill entrypoint")
    assert events == []
    service.project_installed(
        installed,
        {
            "skill_name": "project-lock",
            "entrypoints": {"skill": "SKILL.md"},
        },
    )
    assert events == ["enter", "exit"]
    service.set_enabled_batch([{"skill_name": "locked"}], False)
    assert events == ["enter", "exit", "enter", "exit"]
    with service.delete_transaction({"skill_name": "locked"}):
        assert lock.active is True
    assert events == [
        "enter",
        "exit",
        "enter",
        "exit",
        "enter",
        "exit",
    ]

from __future__ import annotations

import ast
import tempfile
import threading
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import pytest

import agent_skill_registry
from agent_gateway import AgentGateway, AgentGatewayConfig, AgentGatewayError, render_skill_markdown
from agent_skill_registry import (
    AgentSkillRegistryPorts,
    AgentSkillRegistryService,
    SkillToolDescriptor,
    SkillWriteHandlerDescriptor,
)


REPO_ROOT = Path(__file__).parents[1]
OLD_GATEWAY_METHODS = {
    "build_skill_registry",
    "check_skill_registry",
    "create_user_skill",
    "update_user_skill",
    "delete_user_skill",
    "_builtin_skill_definitions",
    "_skill_from_builtin_group",
    "_skill_from_tool",
    "_skill_from_write_handler",
    "_skill_dependency_visible",
    "user_skills_dir",
    "_load_user_skills",
    "_load_projected_skill_state",
    "_find_user_skill",
    "_save_user_skills",
    "_save_user_skill",
    "_normalize_user_skill",
    "_ensure_user_skill_can_use_id",
    "_decorate_skill_validation",
    "_validate_skill",
    "_load_runtime_skill_support_files",
}


def _gateway(root: Path) -> AgentGateway:
    return AgentGateway(root / "config.json", root / "audit")


def _service(
    root: Path,
    *,
    lock: threading.RLock | None = None,
    tools: tuple[SkillToolDescriptor, ...] = (),
    handlers: tuple[SkillWriteHandlerDescriptor, ...] = (),
    append_audit: Callable[[dict[str, object]], None] | None = None,
    local_state_write_guard: Callable[[], AbstractContextManager[object]] | None = None,
) -> tuple[AgentSkillRegistryService, list[dict[str, object]]]:
    config = AgentGatewayConfig(enabled=True)
    audits: list[dict[str, object]] = []
    service = AgentSkillRegistryService(
        AgentSkillRegistryPorts(
            config_path=lambda: root / "config.json",
            ensure_config=lambda: config,
            list_tools=lambda: tools,
            list_write_handlers=lambda: handlers,
            tool_visible=lambda name, _config: any(tool.name == name for tool in tools),
            write_handler_visible=lambda name, _config: any(handler.name == name for handler in handlers),
            computer_use_model_invocable=lambda _config: False,
            append_audit=append_audit or (lambda entry: audits.append(dict(entry))),
            user_skill_lock=lock or threading.RLock(),
            local_state_write_guard=local_state_write_guard or (lambda: nullcontext()),
        )
    )
    return service, audits


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    return {
        path.relative_to(root).as_posix(): (
            ("dir", b"") if path.is_dir() else ("file", path.read_bytes())
        )
        for path in root.rglob("*")
    }


def test_service_has_only_typed_ports_and_production_metadata_has_no_handlers() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        gateway.register_tool("read-one", "Read one.", "read/debug", lambda _params: {"ok": True})
        gateway.approval_transactions.register_write_handler("write-one", "Write one.", "medium", lambda _params: {"ok": True})

        assert AgentSkillRegistryService.__slots__ == ("_ports",)
        assert not hasattr(gateway.skills, "_host")
        assert not hasattr(AgentSkillRegistryService, "__getattr__")
        assert gateway.skills.write_lock is gateway.skills._ports.user_skill_lock  # noqa: SLF001
        tool = next(item for item in gateway.skills._ports.list_tools() if item.name == "read-one")  # noqa: SLF001
        handler = next(item for item in gateway.skills._ports.list_write_handlers() if item.name == "write-one")  # noqa: SLF001
        assert isinstance(tool, SkillToolDescriptor)
        assert isinstance(handler, SkillWriteHandlerDescriptor)
        assert not hasattr(tool, "handler")
        assert not hasattr(handler, "handler")


def test_dynamic_config_path_and_exact_lock_are_preserved() -> None:
    with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
        gateway = _gateway(Path(first_dir))
        lock = gateway.skills.write_lock
        assert gateway.skills.user_skills_dir == Path(first_dir) / "skills"

        gateway.configure_paths(Path(second_dir) / "config" / "agent_gateway.json", Path(second_dir) / "audit")

        assert gateway.skills.user_skills_dir == Path(second_dir) / "skills"
        assert gateway.skills.write_lock is lock


def test_crud_preserves_lock_order_audit_and_envelope() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        service, audits = _service(root)

        created = service.create_user_skill({"name": "avatar-review", "instructions": "Inspect safely."})
        updated = service.update_user_skill("avatar-review", {"title": "Avatar Review"})
        deleted = service.delete_user_skill("avatar-review")

        assert created["skill"]["name"] == "avatar-review"
        assert updated["skill"]["title"] == "Avatar Review"
        assert deleted["deleted"] == "avatar-review"
        assert [entry["event"] for entry in audits] == [
            "user_skill_created",
            "user_skill_updated",
            "user_skill_deleted",
        ]


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_canonical_and_alias_duplicate_blocks_all_crud_without_writes(
    tmp_path: Path,
    operation: str,
) -> None:
    service, audits = _service(tmp_path)
    service.create_user_skill({"name": "victim", "instructions": "canonical"})
    alias_dir = tmp_path / "skills" / "victim!!!"
    alias_dir.mkdir()
    alias_dir.joinpath("SKILL.md").write_text(
        render_skill_markdown({"name": "victim", "instructions": "alias"}),
        encoding="utf-8",
    )
    before = _tree_snapshot(tmp_path)
    audit_count = len(audits)

    with pytest.raises(AgentGatewayError) as error:
        if operation == "create":
            service.create_user_skill({"name": "victim", "instructions": "new"})
        elif operation == "update":
            service.update_user_skill("victim", {"instructions": "new"})
        else:
            service.delete_user_skill("victim")

    assert error.value.status_code == 409
    assert _tree_snapshot(tmp_path) == before
    assert len(audits) == audit_count


@pytest.mark.parametrize("failure", ["build", "audit"])
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_crud_build_or_audit_failure_restores_exact_tree(
    tmp_path: Path,
    failure: str,
    operation: str,
) -> None:
    seed, _seed_audits = _service(tmp_path)
    if operation != "create":
        seed.create_user_skill({"name": "rollback-skill", "instructions": "old"})
    before = _tree_snapshot(tmp_path)

    def fail_audit(_entry: dict[str, object]) -> None:
        raise OSError("injected audit failure")

    service, audits = _service(
        tmp_path,
        append_audit=fail_audit if failure == "audit" else None,
    )

    def mutate() -> object:
        if operation == "create":
            return service.create_user_skill(
                {"name": "rollback-skill", "instructions": "new"}
            )
        if operation == "update":
            return service.update_user_skill(
                "rollback-skill",
                {"instructions": "new"},
            )
        return service.delete_user_skill("rollback-skill")

    if failure == "build":
        failure_context = patch.object(
            AgentSkillRegistryService,
            "_build_skill_registry",
            side_effect=OSError("injected build failure"),
        )
    else:
        failure_context = nullcontext()
    with failure_context, pytest.raises(OSError, match=f"injected {failure} failure"):
        mutate()

    assert _tree_snapshot(tmp_path) == before
    assert audits == []


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_active_recovery_guard_runs_before_user_lock_and_leaves_zero_writes(
    tmp_path: Path,
    operation: str,
) -> None:
    skill_dir = tmp_path / "skills" / "guarded-skill"
    if operation != "create":
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").write_text(
            render_skill_markdown(
                {"name": "guarded-skill", "instructions": "unchanged"}
            ),
            encoding="utf-8",
        )
    before = _tree_snapshot(tmp_path)
    events: list[str] = []

    class TrackingLock:
        def __enter__(self) -> object:
            events.append("user-lock-enter")
            return self

        def __exit__(self, *_args: object) -> None:
            events.append("user-lock-exit")

    @contextmanager
    def reject_active_recovery():
        events.append("guard-enter")
        raise AgentGatewayError("checkpoint recovery is active", status_code=409)
        yield  # pragma: no cover - the guard must fail before the user lock.

    service, audits = _service(
        tmp_path,
        lock=TrackingLock(),  # type: ignore[arg-type]
        local_state_write_guard=reject_active_recovery,
    )

    with pytest.raises(AgentGatewayError, match="recovery is active") as error:
        if operation == "create":
            service.create_user_skill(
                {"name": "guarded-skill", "instructions": "new"}
            )
        elif operation == "update":
            service.update_user_skill(
                "guarded-skill",
                {"instructions": "new"},
            )
        else:
            service.delete_user_skill("guarded-skill")

    assert error.value.status_code == 409
    assert events == ["guard-enter"]
    assert _tree_snapshot(tmp_path) == before
    assert audits == []


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_atomic_update_failure_preserves_old_bytes_and_cleans_temporary_files(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        service, audits = _service(root)
        service.create_user_skill({"name": "atomic-skill", "instructions": "old"})
        skill_file = root / "skills" / "atomic-skill" / "SKILL.md"
        original = skill_file.read_bytes()
        audit_count = len(audits)

        if failure == "replace":
            monkeypatch.setattr(agent_skill_registry.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace failed")))
        else:
            real_named_temporary_file = agent_skill_registry.tempfile.NamedTemporaryFile

            class PartialWriter:
                def __init__(self, wrapped: object) -> None:
                    self._wrapped = wrapped
                    self.name = wrapped.name  # type: ignore[attr-defined]

                def __enter__(self) -> "PartialWriter":
                    self._wrapped.__enter__()  # type: ignore[attr-defined]
                    return self

                def __exit__(self, *args: object) -> object:
                    return self._wrapped.__exit__(*args)  # type: ignore[attr-defined]

                def write(self, value: str) -> int:
                    self._wrapped.write(value[:4])  # type: ignore[attr-defined]
                    raise OSError("write failed")

                def flush(self) -> None:
                    self._wrapped.flush()  # type: ignore[attr-defined]

                def fileno(self) -> int:
                    return self._wrapped.fileno()  # type: ignore[attr-defined]

            monkeypatch.setattr(
                agent_skill_registry.tempfile,
                "NamedTemporaryFile",
                lambda *args, **kwargs: PartialWriter(real_named_temporary_file(*args, **kwargs)),
            )

        with pytest.raises(OSError, match=f"{failure} failed"):
            service.update_user_skill("atomic-skill", {"instructions": "new"})

        assert skill_file.read_bytes() == original
        assert list(skill_file.parent.glob(".SKILL.md.*.tmp")) == []
        assert len(audits) == audit_count


def test_linked_root_and_dangling_skill_file_fail_before_write(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        service, audits = _service(root)
        skills_root = root / "skills"
        skills_root.mkdir()
        monkeypatch.setattr(agent_skill_registry, "_path_is_link_like", lambda path: path == skills_root)

        with pytest.raises(AgentGatewayError, match="regular non-link directory"):
            service.create_user_skill({"name": "blocked-root"})
        assert list(skills_root.iterdir()) == []
        assert audits == []

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        service, audits = _service(root)
        skill_dir = root / "skills" / "dangling-target"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        real_lexists = agent_skill_registry.os.path.lexists
        monkeypatch.setattr(
            agent_skill_registry.os.path,
            "lexists",
            lambda path: True if Path(path) == skill_file else real_lexists(path),
        )
        monkeypatch.setattr(agent_skill_registry, "_path_is_link_like", lambda path: Path(path) == skill_file)

        with pytest.raises(AgentGatewayError, match="already exists") as error:
            service.create_user_skill({"name": "dangling-target"})
        assert error.value.status_code == 409
        assert list(skill_dir.iterdir()) == []
        assert audits == []


def test_atomic_publish_rechecks_target_after_temporary_write(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        service, audits = _service(root)
        service.create_user_skill({"name": "publish-race", "instructions": "old"})
        skill_file = root / "skills" / "publish-race" / "SKILL.md"
        original = skill_file.read_bytes()
        audit_count = len(audits)
        monkeypatch.setattr(
            agent_skill_registry,
            "_path_is_link_like",
            lambda path: (
                Path(path) == skill_file
                and bool(list(skill_file.parent.glob(".SKILL.md.*.tmp")))
            ),
        )

        with pytest.raises(AgentGatewayError, match="unsafe user skill file"):
            service.update_user_skill("publish-race", {"instructions": "new"})

        assert skill_file.read_bytes() == original
        assert list(skill_file.parent.glob(".SKILL.md.*.tmp")) == []
        assert len(audits) == audit_count


def test_delete_isolation_failure_leaves_no_staging_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, audits = _service(tmp_path)
    service.create_user_skill({"name": "isolation-failure", "instructions": "keep"})
    skill_dir = tmp_path / "skills" / "isolation-failure"
    skill_file = skill_dir / "SKILL.md"
    original = skill_file.read_bytes()
    audit_count = len(audits)
    real_replace = agent_skill_registry.os.replace

    def fail_initial_isolation(source: object, destination: object) -> None:
        if Path(source) == skill_dir:
            raise OSError("isolation failed")
        real_replace(source, destination)

    monkeypatch.setattr(agent_skill_registry.os, "replace", fail_initial_isolation)

    with pytest.raises(OSError, match="isolation failed"):
        service.delete_user_skill("isolation-failure")

    assert skill_file.read_bytes() == original
    assert not (tmp_path / ".skill-registry-staging").exists()
    assert len(audits) == audit_count


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_crud_cannot_mutate_package_projected_skill(
    tmp_path: Path,
    operation: str,
) -> None:
    service, audits = _service(tmp_path)
    service.create_user_skill({"name": "package-owned", "instructions": "owned"})
    skill_dir = tmp_path / "skills" / "package-owned"
    state_path = skill_dir / agent_skill_registry.PROJECTED_SKILL_STATE_NAME
    state_path.write_text(
        '{"enabled":true,"packageId":"community.tests.owner",'
        '"projectionDigest":"' + ("0" * 64) + '",'
        '"schema":"vrcforge.projected-skill-state.v2"}\n',
        encoding="utf-8",
    )
    before = {
        path.relative_to(skill_dir).as_posix(): path.read_bytes()
        for path in skill_dir.rglob("*")
        if path.is_file()
    }
    audit_count = len(audits)

    with pytest.raises(AgentGatewayError, match="Skill Package Manager") as error:
        if operation == "update":
            service.update_user_skill("package-owned", {"instructions": "changed"})
        else:
            service.delete_user_skill("package-owned")

    assert error.value.status_code == 409
    assert {
        path.relative_to(skill_dir).as_posix(): path.read_bytes()
        for path in skill_dir.rglob("*")
        if path.is_file()
    } == before
    assert len(audits) == audit_count


def test_public_reads_block_on_exact_writer_lock_and_reenter() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        lock = threading.RLock()
        service, _audits = _service(Path(temp_dir), lock=lock)
        started = threading.Event()
        finished = threading.Event()

        def read_registry() -> None:
            started.set()
            service.build_skill_registry()
            finished.set()

        with lock:
            worker = threading.Thread(target=read_registry)
            worker.start()
            assert started.wait(1)
            assert not finished.wait(0.05)
            assert service.find_user_skill("missing") is None
        assert finished.wait(1)
        worker.join(timeout=1)


def test_linked_skill_directory_never_loads_external_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _audits = _service(tmp_path / "app")
    skills_root = tmp_path / "app" / "skills"
    skills_root.mkdir(parents=True)
    linked = skills_root / "linked-skill"
    linked.mkdir()
    linked.joinpath("SKILL.md").write_text(
        render_skill_markdown(
            {
                "name": "linked-skill",
                "instructions": "EXTERNAL SECRET TEXT",
            }
        ),
        encoding="utf-8",
    )
    parse_calls: list[Path] = []
    real_link_check = agent_skill_registry._path_is_link_like  # noqa: SLF001
    monkeypatch.setattr(
        agent_skill_registry,
        "_path_is_link_like",
        lambda path: Path(path) == linked or real_link_check(Path(path)),
    )
    monkeypatch.setattr(
        agent_skill_registry,
        "parse_skill_markdown",
        lambda path, **_kwargs: parse_calls.append(Path(path))
        or pytest.fail("linked child manifest must not be parsed"),
    )

    registry = service.build_skill_registry()
    serialized = str(registry)
    rows = [item for item in registry["skills"] if item.get("source") == "user"]

    assert "EXTERNAL SECRET TEXT" not in serialized
    assert len(rows) == 1
    assert rows[0]["name"] == "linked-skill"
    assert rows[0]["available"] is False
    assert rows[0]["validation"]["status"] == "error"
    assert parse_calls == []


def test_oversized_manifest_is_rejected_before_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _audits = _service(tmp_path)
    skill_dir = tmp_path / "skills" / "oversized"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_bytes(
        b"x" * (agent_skill_registry.USER_SKILL_MANIFEST_MAX_BYTES + 1)
    )
    parse_calls: list[Path] = []
    monkeypatch.setattr(
        agent_skill_registry,
        "parse_skill_markdown",
        lambda path, **_kwargs: parse_calls.append(Path(path))
        or pytest.fail("oversized manifest must not reach the parser"),
    )

    registry = service.build_skill_registry()
    row = next(
        item for item in registry["skills"] if item.get("source") == "user"
    )

    assert parse_calls == []
    assert row["name"] == "oversized"
    assert row["available"] is False
    assert row["validation"]["status"] == "error"
    assert "size limit" in row["loadError"]


def test_builtin_group_names_are_reserved_for_crud_projection_and_existing_rows() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        service, _audits = _service(root)
        with pytest.raises(AgentGatewayError, match="conflicts with a builtin tool") as create_error:
            service.create_user_skill({"name": "know-yourself"})
        assert create_error.value.status_code == 409
        with pytest.raises(AgentGatewayError, match="conflicts with a builtin tool"):
            service.validate_projection_name("know-yourself")

        skill_dir = root / "skills" / "know-yourself"
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").write_text(
            render_skill_markdown({"name": "know-yourself", "instructions": "legacy"}),
            encoding="utf-8",
        )
        row = next(item for item in service.build_skill_registry()["skills"] if item.get("source") == "user")
        assert row["name"] == "know-yourself"
        assert row["available"] is False
        assert row["validation"]["status"] == "error"


def test_manifest_name_mismatch_cannot_update_or_delete_another_directory(tmp_path: Path) -> None:
    service, _audits = _service(tmp_path)
    owner_dir = tmp_path / "skills" / "owner-dir"
    owner_dir.mkdir(parents=True)
    owner_dir.joinpath("SKILL.md").write_text(
        render_skill_markdown(
            {
                "name": "victim",
                "instructions": "This manifest does not own the victim directory.",
            }
        ),
        encoding="utf-8",
    )
    victim_dir = tmp_path / "skills" / "victim"
    victim_dir.mkdir()
    sentinel = victim_dir / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")

    registry = service.build_skill_registry()
    owner_row = next(
        item
        for item in registry["skills"]
        if item.get("source") == "user" and item.get("name") == "owner-dir"
    )
    assert owner_row["available"] is False
    assert owner_row["validation"]["status"] == "error"

    with pytest.raises(AgentGatewayError) as update_error:
        service.update_user_skill("victim", {"instructions": "overwrite"})
    with pytest.raises(AgentGatewayError) as delete_error:
        service.delete_user_skill("victim")

    assert update_error.value.status_code == 404
    assert delete_error.value.status_code == 404
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert owner_dir.joinpath("SKILL.md").is_file()


def test_planning_registry_and_check_contract_are_preserved() -> None:
    tool = SkillToolDescriptor("read-one", "Read one.", "read/debug", False, False, False)
    writer = SkillWriteHandlerDescriptor("write-one", "Write one.", "medium", False)
    with tempfile.TemporaryDirectory() as temp_dir:
        service, _audits = _service(Path(temp_dir), tools=(tool,), handlers=(writer,))
        registry = service.build_skill_registry(exposure_layer="planning")
        checked = service.check_skill_registry(exposure_layer="planning")

        assert registry["schema"] == "vrcforge.skills.v1"
        assert registry["exposureLayer"] == "planning"
        assert all(not skill.get("write") for skill in registry["skills"])
        assert any(skill["name"] == "read-one" for skill in registry["skills"])
        assert all(skill["name"] != "write-one" for skill in registry["skills"])
        assert checked["schema"] == "vrcforge.skills.check.v1"
        assert checked["count"] == registry["count"]


def test_gateway_old_facades_and_host_proxy_are_gone() -> None:
    gateway_tree = ast.parse((REPO_ROOT / "agent_gateway.py").read_text(encoding="utf-8-sig"))
    gateway_class = next(
        node
        for node in gateway_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AgentGateway"
    )
    gateway_methods = {
        node.name for node in gateway_class.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    service_source = (REPO_ROOT / "agent_skill_registry.py").read_text(encoding="utf-8-sig")

    assert OLD_GATEWAY_METHODS.isdisjoint(gateway_methods)
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "_skill_registry"
        for node in ast.walk(gateway_tree)
    )
    assert "_host" not in service_source
    assert "__getattr__" not in service_source
    assert "_impl_" not in service_source
    assert "execute_runtime_skill" not in service_source
    assert "ToolHandler" not in service_source


def test_gateway_skill_registry_size_budget() -> None:
    source = (REPO_ROOT / "agent_gateway.py").read_bytes()
    assert len(source) <= 476_574
    assert source.count(b"\n") <= 10_422

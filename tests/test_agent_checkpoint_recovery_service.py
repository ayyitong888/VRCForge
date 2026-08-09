from __future__ import annotations

import ast
import os
import re
import tempfile
import threading
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

import agent_checkpoint_recovery
from agent_checkpoint_recovery import AgentCheckpointRecoveryService
from agent_gateway import AgentGateway


REPO_ROOT = Path(__file__).parents[1]
AGENT_GATEWAY_MAX_BYTES = 578_597
AGENT_GATEWAY_MAX_LF_LINES = 12_340


def _gateway(root: Path) -> AgentGateway:
    return AgentGateway(root / "config.json", root / "audit")


def _bind_transaction_test_locks(
    gateway: AgentGateway,
    *,
    storage_lock: object | None = None,
    user_lock: object | None = None,
) -> None:
    if storage_lock is not None:
        gateway._checkpoint_storage_lock = storage_lock  # noqa: SLF001 - exact lock fixture.
        gateway.checkpoint_recovery._ports = replace(  # noqa: SLF001
            gateway.checkpoint_recovery._ports,  # noqa: SLF001
            state=replace(
                gateway.checkpoint_recovery._ports.state,  # noqa: SLF001
                checkpoint_storage_lock=storage_lock,
            ),
        )
        gateway.approval_transactions._ports = replace(  # noqa: SLF001
            gateway.approval_transactions._ports,  # noqa: SLF001
            state=replace(
                gateway.approval_transactions._ports.state,  # noqa: SLF001
                checkpoint_storage_lock=storage_lock,
            ),
        )
    if user_lock is not None:
        gateway.skills._ports = replace(  # noqa: SLF001
            gateway.skills._ports,  # noqa: SLF001
            user_skill_lock=user_lock,
        )
        gateway.checkpoint_recovery._ports = replace(  # noqa: SLF001
            gateway.checkpoint_recovery._ports,  # noqa: SLF001
            skills=replace(gateway.checkpoint_recovery._ports.skills, write_lock=user_lock),  # noqa: SLF001
        )
        gateway.approval_transactions._ports = replace(  # noqa: SLF001
            gateway.approval_transactions._ports,  # noqa: SLF001
            skills=replace(gateway.approval_transactions._ports.skills, write_lock=user_lock),  # noqa: SLF001
        )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _create_persisted_local_state_checkpoint(
    gateway: AgentGateway,
    checkpoint_id: str,
) -> dict[str, object]:
    return gateway.checkpoint_recovery._create_local_state_checkpoint(
        {
            "schema": "vrcforge.checkpoint.v1",
            "id": checkpoint_id,
            "createdAt": "2026-08-09T00:00:00+00:00",
            "targetTool": "vrcforge_import_skill_package",
            "status": "unavailable",
        }
    )


def _local_state_restore_fixture(
    root: Path,
    checkpoint_id: str,
) -> tuple[AgentGateway, dict[str, object], dict[str, Path], dict[str, dict[str, bytes]]]:
    gateway = _gateway(root)
    roots = gateway.checkpoint_recovery._local_state_checkpoint_roots()
    package_file = roots["skill-packages"] / "sample" / "installed.json"
    skill_file = roots["skills"] / "sample" / "SKILL.md"
    package_file.parent.mkdir(parents=True)
    skill_file.parent.mkdir(parents=True)
    package_file.write_bytes(b'{"version": "checkpoint"}\n')
    skill_file.write_bytes(b"checkpoint skill\n")
    checkpoint = _create_persisted_local_state_checkpoint(gateway, checkpoint_id)
    assert checkpoint["ok"] is True

    package_file.write_bytes(b'{"version": "live"}\n')
    skill_file.write_bytes(b"live skill\n")
    (package_file.parent / "live-only.bin").write_bytes(b"live package bytes\x00")
    (skill_file.parent / "live-only.bin").write_bytes(b"live skill bytes\x00")
    live = {scope: _tree_bytes(path) for scope, path in roots.items()}
    return gateway, checkpoint, roots, live


def _restore_workspaces(roots: dict[str, Path]) -> list[Path]:
    return sorted(
        path
        for root in roots.values()
        for path in root.parent.glob(f".{root.name}.vrcforge-restore-*")
    )


class _TrackingLock:
    def __init__(self, name: str, events: list[str]) -> None:
        self._lock = threading.RLock()
        self._name = name
        self._events = events
        self.depth = 0

    def __enter__(self) -> object:
        self._lock.acquire()
        self.depth += 1
        self._events.append(f"{self._name}:enter")
        return self

    def __exit__(self, *_args: object) -> None:
        self._events.append(f"{self._name}:exit")
        self.depth -= 1
        self._lock.release()


def _preview_local_state_digest(
    gateway: AgentGateway,
    checkpoint: dict[str, object],
) -> str:
    preview = gateway.checkpoint_recovery.preview_restore_checkpoint(
        {"checkpointId": str(checkpoint["id"])}
    )
    assert preview["ok"] is True
    digest = str(preview["currentStateDigest"])
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    return digest


def _preview_local_state_digest_direct(
    gateway: AgentGateway,
    checkpoint: dict[str, object],
) -> str:
    preview = gateway.checkpoint_recovery._preview_local_state_checkpoint(checkpoint)
    assert preview["ok"] is True
    digest = str(preview["currentStateDigest"])
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    return digest


def _register_checkpoint_restore_handler(gateway: AgentGateway) -> None:
    gateway.approval_transactions.register_write_handler(
        "vrcforge_restore_checkpoint",
        "Restore a frozen checkpoint state.",
        "high",
        gateway.checkpoint_recovery.restore_checkpoint,
    )


def _class_definition(path: Path, class_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )


def test_checkpoint_recovery_service_owns_no_second_runtime_or_lock() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        service = gateway.checkpoint_recovery

        assert isinstance(service, AgentCheckpointRecoveryService)
        assert not hasattr(service, "_host")
        assert "__getattr__" not in AgentCheckpointRecoveryService.__dict__
        assert service._ports.state.checkpoint_storage_lock is gateway._checkpoint_storage_lock
        assert service._ports.state.skill_package_write_lock is gateway._skill_package_write_lock
        assert service._ports.skills.write_lock is gateway.skills.write_lock


def test_local_state_checkpoint_uses_package_then_exact_user_lock() -> None:
    events: list[str] = []

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        package_lock = _TrackingLock("package", events)
        user_lock = _TrackingLock("user", events)
        gateway = AgentGateway(
            root / "config" / "agent_gateway.json",
            root / "audit",
            skill_package_write_lock=package_lock,
        )
        _bind_transaction_test_locks(gateway, user_lock=user_lock)
        skill_file = gateway.skills.user_skills_dir / "sample" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("sample\n", encoding="utf-8")

        checkpoint = gateway.checkpoint_recovery._create_local_state_checkpoint({"id": "lock-order"})
        assert checkpoint["ok"] is True
        assert events[:4] == [
            "package:enter",
            "user:enter",
            "user:exit",
            "package:exit",
        ]

        events.clear()
        preview = gateway.checkpoint_recovery._preview_local_state_checkpoint(checkpoint)
        assert preview["ok"] is True
        digest = str(preview["currentStateDigest"])
        assert events == [
            "package:enter",
            "user:enter",
            "user:exit",
            "package:exit",
        ]

        events.clear()
        assert gateway.checkpoint_recovery._restore_local_state_checkpoint(checkpoint, digest)["ok"] is True
        assert events == [
            "package:enter",
            "user:enter",
            "user:exit",
            "package:exit",
        ]


def test_local_state_approved_write_holds_storage_package_and_user_locks_through_handler() -> None:
    events: list[str] = []
    handler_lock_depths: list[tuple[int, int, int]] = []

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        storage_lock = _TrackingLock("storage", events)
        package_lock = _TrackingLock("package", events)
        user_lock = _TrackingLock("user", events)
        gateway = AgentGateway(
            root / "config" / "agent_gateway.json",
            root / "audit",
            skill_package_write_lock=package_lock,
        )
        _bind_transaction_test_locks(
            gateway,
            storage_lock=storage_lock,
            user_lock=user_lock,
        )

        def approved_handler(_arguments: dict[str, object]) -> dict[str, object]:
            events.append("handler")
            handler_lock_depths.append(
                (storage_lock.depth, package_lock.depth, user_lock.depth)
            )
            return {"ok": True, "changed": False}

        gateway.approval_transactions.register_write_handler(
            "vrcforge_import_skill_package",
            "Import an isolated skill package fixture.",
            "medium",
            approved_handler,
        )
        request = gateway.approval_transactions.create_apply_request(
            {
                "target_tool": "vrcforge_import_skill_package",
                "arguments": {"packagePath": "fixture.vsk"},
            }
        )
        gateway.approval_transactions.approve(request["approval"]["id"])

        applied = gateway.approval_transactions.apply_approved(
            {"approval_id": request["approval"]["id"]}
        )

        assert applied["ok"] is True
        assert applied["checkpoint"]["strategy"] == "local_state_archive"
        assert handler_lock_depths == [(1, 1, 1)]
        assert events[:3] == ["storage:enter", "package:enter", "user:enter"]
        assert events[-3:] == ["user:exit", "package:exit", "storage:exit"]
        assert storage_lock.depth == package_lock.depth == user_lock.depth == 0


def test_local_state_approved_write_without_shared_package_lock_fails_closed() -> None:
    handler_calls: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        gateway.approval_transactions.register_write_handler(
            "vrcforge_import_skill_package",
            "Import an isolated skill package fixture.",
            "medium",
            lambda arguments: handler_calls.append(arguments) or {"ok": True},
        )
        request = gateway.approval_transactions.create_apply_request(
            {
                "target_tool": "vrcforge_import_skill_package",
                "arguments": {"packagePath": "fixture.vsk"},
            }
        )
        gateway.approval_transactions.approve(request["approval"]["id"])

        applied = gateway.approval_transactions.apply_approved(
            {"approval_id": request["approval"]["id"]}
        )

        assert applied["ok"] is False
        assert applied["status"] == "failed"
        assert (
            applied["error"]
            == "Local-state approved writes require the shared skill-package lock."
        )
        assert handler_calls == []
        assert "checkpoint" not in applied


def test_local_state_checkpoint_rejects_linked_root_and_nested_path_before_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        gateway = _gateway(root)
        skill_file = gateway.skills.user_skills_dir / "sample" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("before\n", encoding="utf-8")
        checkpoint = gateway.checkpoint_recovery._create_local_state_checkpoint({"id": "safe-state"})
        assert checkpoint["ok"] is True
        skill_file.write_text("after\n", encoding="utf-8")

        real_link_check = agent_checkpoint_recovery._path_is_link_like
        linked_root = gateway.skills.user_skills_dir
        monkeypatch.setattr(
            agent_checkpoint_recovery,
            "_path_is_link_like",
            lambda path, _linked_root=linked_root, _real_link_check=real_link_check: (
                Path(path) == _linked_root or _real_link_check(path)
            ),
        )

        preview = gateway.checkpoint_recovery._preview_local_state_checkpoint(checkpoint)
        restored = gateway.checkpoint_recovery._restore_local_state_checkpoint(checkpoint, "0" * 64)
        blocked = gateway.checkpoint_recovery._create_local_state_checkpoint({"id": "linked-state"})

        assert preview["ok"] is False
        assert restored["ok"] is False
        assert blocked["ok"] is False
        assert "regular directory" in restored["error"]
        assert skill_file.read_text(encoding="utf-8") == "after\n"

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        gateway = _gateway(root)
        nested = gateway.skills.user_skills_dir / "linked-child"
        nested.mkdir(parents=True)
        (nested / "outside.txt").write_text("outside\n", encoding="utf-8")
        real_link_check = agent_checkpoint_recovery._path_is_link_like
        monkeypatch.setattr(
            agent_checkpoint_recovery,
            "_path_is_link_like",
            lambda path, _nested=nested, _real_link_check=real_link_check: (
                Path(path) == _nested or _real_link_check(path)
            ),
        )

        blocked = gateway.checkpoint_recovery._create_local_state_checkpoint({"id": "nested-link"})

        assert blocked["ok"] is False
        assert "linked local state path" in blocked["error"]


def test_corrupt_local_state_archive_does_not_change_live_roots() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway, checkpoint, roots, live = _local_state_restore_fixture(
            Path(temp_dir),
            "corrupt-archive",
        )
        archive_path = Path(str(checkpoint["archivePath"]))
        archive_bytes = bytearray(archive_path.read_bytes())
        with zipfile.ZipFile(archive_path, "r") as archive:
            member = next(info for info in archive.infolist() if not info.is_dir())
        digest = _preview_local_state_digest_direct(gateway, checkpoint)
        header_offset = member.header_offset
        name_size = int.from_bytes(archive_bytes[header_offset + 26 : header_offset + 28], "little")
        extra_size = int.from_bytes(archive_bytes[header_offset + 28 : header_offset + 30], "little")
        payload_offset = header_offset + 30 + name_size + extra_size
        archive_bytes[payload_offset] ^= 0xFF
        archive_path.write_bytes(archive_bytes)

        result = gateway.checkpoint_recovery._restore_local_state_checkpoint(checkpoint, digest)

        assert result["ok"] is False
        assert "restore failed" in str(result["error"]).lower()
        assert {scope: _tree_bytes(path) for scope, path in roots.items()} == live
        assert _restore_workspaces(roots) == []


def test_local_state_staging_failure_does_not_change_live_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway, checkpoint, roots, live = _local_state_restore_fixture(
            Path(temp_dir),
            "stage-failure",
        )
        digest = _preview_local_state_digest_direct(gateway, checkpoint)

        def fail_stage_copy(source: object, destination: object, **_kwargs: object) -> None:
            destination.write(source.read(1))
            raise OSError("injected local state staging failure")

        monkeypatch.setattr(
            agent_checkpoint_recovery.shutil,
            "copyfileobj",
            fail_stage_copy,
        )

        result = gateway.checkpoint_recovery._restore_local_state_checkpoint(checkpoint, digest)

        assert result["ok"] is False
        assert "injected local state staging failure" in str(result["error"])
        assert {scope: _tree_bytes(path) for scope, path in roots.items()} == live
        assert _restore_workspaces(roots) == []


def test_local_state_restore_publishes_both_staged_roots_and_cleans_workspaces() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway, checkpoint, roots, _live = _local_state_restore_fixture(
            Path(temp_dir),
            "successful-two-root-restore",
        )
        digest = _preview_local_state_digest_direct(gateway, checkpoint)

        result = gateway.checkpoint_recovery._restore_local_state_checkpoint(checkpoint, digest)

        assert result["ok"] is True
        assert _tree_bytes(roots["skill-packages"]) == {
            "sample/installed.json": b'{"version": "checkpoint"}\n',
        }
        assert _tree_bytes(roots["skills"]) == {
            "sample/SKILL.md": b"checkpoint skill\n",
        }
        assert _restore_workspaces(roots) == []


def test_second_local_state_root_publish_failure_rolls_back_both_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway, checkpoint, roots, live = _local_state_restore_fixture(
            Path(temp_dir),
            "second-publish-failure",
        )
        digest = _preview_local_state_digest_direct(gateway, checkpoint)
        real_replace = agent_checkpoint_recovery.os.replace

        def fail_second_publish(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                destination_path == roots["skills"]
                and source_path.name.endswith(".staged")
            ):
                raise OSError("injected second local state publish failure")
            real_replace(source, destination)

        monkeypatch.setattr(agent_checkpoint_recovery.os, "replace", fail_second_publish)

        result = gateway.checkpoint_recovery._restore_local_state_checkpoint(checkpoint, digest)

        assert result["ok"] is False
        assert "injected second local state publish failure" in str(result["error"])
        assert {scope: _tree_bytes(path) for scope, path in roots.items()} == live
        assert _restore_workspaces(roots) == []


def test_local_state_rollback_failure_preserves_named_backup_recovery_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway, checkpoint, roots, live = _local_state_restore_fixture(
            Path(temp_dir),
            "rollback-failure",
        )
        digest = _preview_local_state_digest_direct(gateway, checkpoint)
        real_replace = agent_checkpoint_recovery.os.replace
        publish_failed = False

        def fail_publish_and_first_rollback(
            source: os.PathLike[str],
            destination: os.PathLike[str],
        ) -> None:
            nonlocal publish_failed
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                destination_path == roots["skills"]
                and source_path.name.endswith(".staged")
            ):
                publish_failed = True
                raise OSError("injected second local state publish failure")
            if (
                publish_failed
                and destination_path == roots["skill-packages"]
                and source_path.name.endswith(".backup")
            ):
                raise OSError("injected local state rollback failure")
            real_replace(source, destination)

        monkeypatch.setattr(
            agent_checkpoint_recovery.os,
            "replace",
            fail_publish_and_first_rollback,
        )

        result = gateway.checkpoint_recovery._restore_local_state_checkpoint(checkpoint, digest)

        assert result["ok"] is False
        assert "recovery data remains at" in str(result["error"])
        recovery_paths = [Path(path) for path in result["recoveryPaths"]]
        assert len(recovery_paths) == 1
        assert recovery_paths[0].name.endswith(".backup")
        assert recovery_paths[0].is_dir()
        assert _tree_bytes(recovery_paths[0]) == live["skill-packages"]
        assert not roots["skill-packages"].exists()
        assert _tree_bytes(roots["skills"]) == live["skills"]
        assert _restore_workspaces(roots) == recovery_paths


def test_local_state_preview_digest_is_stable_and_covers_exact_tree_identity() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        checkpoint = _create_persisted_local_state_checkpoint(
            gateway,
            "digest-state-components",
        )
        assert checkpoint["ok"] is True
        roots = gateway.checkpoint_recovery._local_state_checkpoint_roots()

        absent_digest = _preview_local_state_digest(gateway, checkpoint)
        assert _preview_local_state_digest(gateway, checkpoint) == absent_digest

        package_root = roots["skill-packages"]
        package_root.mkdir(parents=True)
        empty_root_digest = _preview_local_state_digest(gateway, checkpoint)
        (package_root / "empty-directory").mkdir()
        empty_directory_digest = _preview_local_state_digest(gateway, checkpoint)
        state_file = package_root / "same-size.bin"
        state_file.write_bytes(b"aa")
        first_crc_digest = _preview_local_state_digest(gateway, checkpoint)
        state_file.write_bytes(b"bb")
        second_crc_digest = _preview_local_state_digest(gateway, checkpoint)
        renamed = package_root / "renamed.bin"
        state_file.rename(renamed)
        renamed_digest = _preview_local_state_digest(gateway, checkpoint)
        renamed.write_bytes(b"longer")
        resized_digest = _preview_local_state_digest(gateway, checkpoint)

        assert len(
            {
                absent_digest,
                empty_root_digest,
                empty_directory_digest,
                first_crc_digest,
                second_crc_digest,
                renamed_digest,
                resized_digest,
            }
        ) == 7


def test_local_state_checkpoint_restores_empty_directories_exactly() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        roots = gateway.checkpoint_recovery._local_state_checkpoint_roots()
        empty_directory = roots["skills"] / "empty-skill" / "support"
        empty_directory.mkdir(parents=True)
        checkpoint = _create_persisted_local_state_checkpoint(
            gateway,
            "empty-directory-restore",
        )
        assert checkpoint["ok"] is True

        empty_directory.rmdir()
        empty_directory.parent.rmdir()
        digest = _preview_local_state_digest_direct(gateway, checkpoint)
        result = gateway.checkpoint_recovery._restore_local_state_checkpoint(checkpoint, digest)

        assert result["ok"] is True
        assert empty_directory.is_dir()
        assert _restore_workspaces(roots) == []


@pytest.mark.parametrize(
    "supplied_digest",
    [None, "0" * 63, "g" * 64],
    ids=["missing", "wrong-length", "non-hex"],
)
def test_local_state_restore_rejects_missing_or_malformed_digest_without_writes(
    supplied_digest: str | None,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway, checkpoint, roots, live = _local_state_restore_fixture(
            Path(temp_dir),
            f"invalid-digest-{supplied_digest is None}",
        )
        params: dict[str, object] = {
            "checkpointId": checkpoint["id"],
            "confirmRestore": True,
        }
        if supplied_digest is not None:
            params["currentStateDigest"] = supplied_digest

        result = gateway.checkpoint_recovery.restore_checkpoint(params)

        assert result["ok"] is False
        assert "currentStateDigest" in str(result["error"])
        assert {scope: _tree_bytes(path) for scope, path in roots.items()} == live
        assert _restore_workspaces(roots) == []


@pytest.mark.parametrize("changed_scope", ["skill-packages", "skills"])
def test_local_state_approved_restore_rejects_each_root_drift_after_preview(
    changed_scope: str,
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway, checkpoint, roots, _live = _local_state_restore_fixture(
            Path(temp_dir),
            f"approval-drift-{changed_scope}",
        )
        _register_checkpoint_restore_handler(gateway)
        digest = _preview_local_state_digest(gateway, checkpoint)
        request = gateway.approval_transactions.create_apply_request(
            {
                "target_tool": "vrcforge_restore_checkpoint",
                "arguments": {
                    "checkpointId": checkpoint["id"],
                    "confirmRestore": True,
                    "currentStateDigest": digest,
                },
            }
        )
        assert request["approval"]["arguments"]["currentStateDigest"] == digest
        gateway.approval_transactions.approve(request["approval"]["id"])
        drift_file = roots[changed_scope] / "approval-drift.bin"
        drift_file.write_bytes(f"changed {changed_scope}".encode("utf-8"))
        drifted = {scope: _tree_bytes(path) for scope, path in roots.items()}

        applied = gateway.approval_transactions.apply_approved(
            {"approval_id": request["approval"]["id"]}
        )

        assert applied["ok"] is False
        assert "currentStateDigest" in str(applied["error"])
        assert {scope: _tree_bytes(path) for scope, path in roots.items()} == drifted
        assert _restore_workspaces(roots) == []


def test_local_state_restore_with_matching_preview_digest_restores_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway, checkpoint, roots, _live = _local_state_restore_fixture(
            Path(temp_dir),
            "matching-preview-digest",
        )
        digest = _preview_local_state_digest(gateway, checkpoint)

        result = gateway.checkpoint_recovery.restore_checkpoint(
            {
                "checkpointId": checkpoint["id"],
                "confirmRestore": True,
                "currentStateDigest": digest,
            }
        )

        assert result["ok"] is True
        assert _tree_bytes(roots["skill-packages"]) == {
            "sample/installed.json": b'{"version": "checkpoint"}\n',
        }
        assert _tree_bytes(roots["skills"]) == {
            "sample/SKILL.md": b"checkpoint skill\n",
        }
        assert _restore_workspaces(roots) == []


def test_checkpoint_recovery_internal_calls_stay_inside_the_owner() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        with gateway._checkpoint_storage_lock:
            expected = gateway.checkpoint_recovery._list_checkpoints_locked(None)
        assert gateway.checkpoint_recovery.list_checkpoints() == expected


def test_checkpoint_recovery_hooks_remain_late_bound_after_construction() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))

        def prepare(_project_root: Path) -> dict[str, str]:
            return {"status": "prepared"}

        def reload(_project_root: Path, _prepared: dict[str, object]) -> dict[str, str]:
            return {"status": "reloaded"}

        gateway.checkpoint_recovery.checkpoint_restore_prepare_handler = prepare
        gateway.checkpoint_recovery.checkpoint_restore_handler = reload

        assert gateway.checkpoint_recovery.checkpoint_restore_prepare_handler is prepare
        assert gateway.checkpoint_recovery.checkpoint_restore_handler is reload


def test_checkpoint_recovery_owner_retires_gateway_facades_and_impl_names() -> None:
    gateway_class = _class_definition(REPO_ROOT / "agent_gateway.py", "AgentGateway")
    service_class = _class_definition(
        REPO_ROOT / "agent_checkpoint_recovery.py",
        "AgentCheckpointRecoveryService",
    )
    gateway_methods = {
        node.name: node
        for node in gateway_class.body
        if isinstance(node, ast.FunctionDef)
    }
    owner_methods = {
        node.name: node
        for node in service_class.body
        if isinstance(node, ast.FunctionDef)
    }
    representative_owner_methods = {
        "list_checkpoints", "inspect_checkpoint_storage", "repair_checkpoint_storage",
        "prune_checkpoint_archives", "list_interrupted_apply_recoveries",
        "resolve_interrupted_apply_recovery", "list_adjustment_checkpoints",
        "preview_restore_checkpoint", "restore_checkpoint", "_create_archive_checkpoint",
        "_create_local_state_checkpoint", "_restore_local_state_checkpoint",
        "_append_checkpoint", "_active_apply_recoveries",
    }

    assert representative_owner_methods <= owner_methods.keys()
    assert representative_owner_methods.isdisjoint(gateway_methods)
    assert all(not name.startswith("_impl_") for name in owner_methods)


def test_agent_gateway_facade_respects_the_monotonic_1_5_size_budget() -> None:
    source = (REPO_ROOT / "agent_gateway.py").read_bytes()

    assert len(source) <= AGENT_GATEWAY_MAX_BYTES
    assert source.count(b"\n") <= AGENT_GATEWAY_MAX_LF_LINES

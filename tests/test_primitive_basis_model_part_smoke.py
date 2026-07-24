from __future__ import annotations

import hashlib
import json
import os
import socket
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import smoke_primitive_basis_model_part as smoke


def write_package(root: Path, package_id: str, version: str) -> Path:
    package = root / package_id
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": package_id, "version": version}),
        encoding="utf-8",
    )
    return package


def test_safe_zip_extraction_rejects_parent_escape(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.txt", "no")

    with pytest.raises(smoke.PackagedModelPartSmokeError, match="unsafe path"):
        smoke._extract_safe_zip(archive, tmp_path / "output")

    assert not (tmp_path / "outside.txt").exists()


def test_release_input_copy_uses_one_stable_source_handle(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "release.zip"
    source.write_bytes(b"fixed-release-bytes")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    destination_root = tmp_path / "private" / "release-inputs"
    destination_root.mkdir(parents=True)
    destination = destination_root / source.name

    original_copy = smoke._copy_open_file

    def mutate_after_copy(source_stream, target_stream, digest) -> None:
        original_copy(source_stream, target_stream, digest)
        source.write_bytes(b"changed-release-bytes")

    monkeypatch.setattr(smoke, "_copy_open_file", mutate_after_copy)

    with pytest.raises(
        smoke.PackagedModelPartSmokeError, match="changed during copying"
    ):
        smoke._copy_stable_release_input(source, destination, expected)

    assert not destination.exists()


def test_release_input_copy_materializes_manifest_bound_private_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "release.unitypackage"
    source.write_bytes(b"fixed-package")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    destination_root = tmp_path / "private" / "release-inputs"
    destination_root.mkdir(parents=True)
    destination = destination_root / source.name

    copied_digest = smoke._copy_stable_release_input(source, destination, expected)

    assert copied_digest == expected
    assert destination.read_bytes() == source.read_bytes()


def test_release_input_copy_rejects_existing_or_linked_targets(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "release.unitypackage"
    source.write_bytes(b"fixed-package")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    destination_root = tmp_path / "private" / "release-inputs"
    destination_root.mkdir(parents=True)
    destination = destination_root / source.name
    destination.write_bytes(b"pre-existing")

    with pytest.raises(smoke.PackagedModelPartSmokeError, match="already exists"):
        smoke._copy_stable_release_input(source, destination, expected)
    assert destination.read_bytes() == b"pre-existing"

    destination.unlink()
    monkeypatch.setattr(
        smoke,
        "_is_reparse_point",
        lambda path: Path(path) == destination_root,
    )
    with pytest.raises(smoke.PackagedModelPartSmokeError, match="target is invalid"):
        smoke._copy_stable_release_input(source, destination, expected)
    assert not destination.exists()


def test_release_input_copy_rejects_linked_source(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "release.zip"
    source.write_bytes(b"fixed-release")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    destination_root = tmp_path / "private" / "release-inputs"
    destination_root.mkdir(parents=True)
    destination = destination_root / source.name
    monkeypatch.setattr(
        smoke,
        "_is_reparse_point",
        lambda path: Path(path) == source,
    )

    with pytest.raises(smoke.PackagedModelPartSmokeError, match="source is invalid"):
        smoke._copy_stable_release_input(source, destination, expected)

    assert not destination.exists()


def test_release_input_copy_rejects_hard_linked_source(tmp_path: Path) -> None:
    source = tmp_path / "release.zip"
    source.write_bytes(b"fixed-release")
    os.link(source, tmp_path / "release-alias.zip")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    destination_root = tmp_path / "private" / "release-inputs"
    destination_root.mkdir(parents=True)
    destination = destination_root / source.name

    with pytest.raises(smoke.PackagedModelPartSmokeError, match="source is invalid"):
        smoke._copy_stable_release_input(source, destination, expected)

    assert not destination.exists()


def test_release_input_copy_rejects_source_that_becomes_linked(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "release.zip"
    source.write_bytes(b"fixed-release")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    destination_root = tmp_path / "private" / "release-inputs"
    destination_root.mkdir(parents=True)
    destination = destination_root / source.name
    source_checks = 0

    def reparse_after_open(path: Path) -> bool:
        nonlocal source_checks
        if Path(path) != source:
            return False
        source_checks += 1
        return source_checks > 1

    monkeypatch.setattr(smoke, "_is_reparse_point", reparse_after_open)

    with pytest.raises(
        smoke.PackagedModelPartSmokeError, match="changed during copying"
    ):
        smoke._copy_stable_release_input(source, destination, expected)

    assert not destination.exists()


def test_release_input_copy_rejects_target_that_becomes_hard_linked(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "release.zip"
    source.write_bytes(b"fixed-release")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    destination_root = tmp_path / "private" / "release-inputs"
    destination_root.mkdir(parents=True)
    destination = destination_root / source.name
    alias = tmp_path / "copied-alias.zip"
    original_copy = smoke._copy_open_file

    def hard_link_after_copy(source_stream, target_stream, digest) -> None:
        original_copy(source_stream, target_stream, digest)
        os.link(destination, alias)

    monkeypatch.setattr(smoke, "_copy_open_file", hard_link_after_copy)

    with pytest.raises(
        smoke.PackagedModelPartSmokeError,
        match="private release input changed during copying",
    ):
        smoke._copy_stable_release_input(source, destination, expected)

    assert destination.exists()
    assert alias.exists()


def test_backend_tree_digest_binds_internal_payload(tmp_path: Path) -> None:
    backend_root = tmp_path / "backend"
    internal_root = backend_root / "_internal"
    internal_root.mkdir(parents=True)
    (backend_root / "vrcforge_backend.exe").write_bytes(b"launcher")
    internal = internal_root / "runtime.bin"
    internal.write_bytes(b"runtime-a")

    first = smoke._tree_digest(backend_root)
    internal.write_bytes(b"runtime-b")
    second = smoke._tree_digest(backend_root)
    source = Path(smoke.__file__).read_text(encoding="utf-8")

    assert second != first
    assert '"backendTreeDigest": backend_tree_digest' in source
    assert '"backendTreeDigest": prepared.backend_tree_digest' in source


def test_live_runner_accepts_only_exact_strict_or_strict_evidence_policy() -> None:
    assert smoke._accepted_evidence_build_policy(
        {
            "mode": "strict",
            "releaseEligible": True,
            "allowDirty": False,
            "allowUnpushed": False,
            "allowVersionMismatch": False,
        }
    )
    assert smoke._accepted_evidence_build_policy(
        {
            "mode": "strict-evidence",
            "releaseEligible": False,
            "evidenceEligible": True,
            "allowDirty": False,
            "allowUnpushed": False,
            "allowVersionMismatch": False,
        }
    )
    for mutation in (
        {"mode": "local", "releaseEligible": False},
        {
            "mode": "strict-evidence",
            "releaseEligible": True,
            "evidenceEligible": True,
            "allowDirty": False,
            "allowUnpushed": False,
            "allowVersionMismatch": False,
        },
        {
            "mode": "strict-evidence",
            "releaseEligible": False,
            "evidenceEligible": True,
            "allowDirty": True,
            "allowUnpushed": False,
            "allowVersionMismatch": False,
        },
    ):
        assert not smoke._accepted_evidence_build_policy(mutation)


def test_desktop_launch_rejects_backend_internal_drift(tmp_path: Path) -> None:
    backend_root = tmp_path / "backend"
    internal_root = backend_root / "_internal"
    internal_root.mkdir(parents=True)
    backend = backend_root / "vrcforge_backend.exe"
    backend.write_bytes(b"launcher")
    internal = internal_root / "runtime.bin"
    internal.write_bytes(b"runtime-a")
    expected_tree_digest = smoke._tree_digest(backend_root)
    internal.write_bytes(b"runtime-b")

    runner = object.__new__(smoke.PackagedModelPartSmoke)
    runner.prepared = SimpleNamespace(
        backend_executable=backend,
        backend_tree_digest=expected_tree_digest,
    )

    with pytest.raises(smoke.PackagedModelPartSmokeError, match="backend tree changed"):
        runner._launch_desktop()


def test_external_package_set_and_versions_are_exact(tmp_path: Path) -> None:
    roots = [
        write_package(tmp_path, package_id, version)
        for package_id, version in smoke.REQUIRED_EXTERNAL_PACKAGES.items()
    ]

    resolved = smoke._resolve_external_packages([str(path) for path in roots])

    assert {item.package_id: item.version for item in resolved} == (
        smoke.REQUIRED_EXTERNAL_PACKAGES
    )
    with pytest.raises(smoke.PackagedModelPartSmokeError, match="set is not exact"):
        smoke._resolve_external_packages([str(path) for path in roots[:-1]])


def test_port_probe_errors_are_not_treated_as_release(monkeypatch) -> None:
    class IndeterminateSocket:
        def setsockopt(self, *_args) -> None:
            return None

        def bind(self, _address) -> None:
            error = OSError("probe denied")
            error.winerror = 5
            raise error

        def close(self) -> None:
            return None

    monkeypatch.setattr(socket, "socket", lambda *_args: IndeterminateSocket())

    with pytest.raises(smoke.PackagedModelPartSmokeError, match="could not be verified"):
        smoke._port_released(smoke.APP_PORT, "app")


def test_isolated_secret_cleanup_is_verified(tmp_path: Path) -> None:
    runner = object.__new__(smoke.PackagedModelPartSmoke)
    runner.private_root = tmp_path / "private"
    runner.private_root.mkdir()
    runner.app_token = "temporary-token"
    runner.secrets_removed = False
    for name in ("config", "user-data", "webview"):
        path = runner.private_root / name
        path.mkdir()
        (path / "secret.txt").write_text("temporary", encoding="utf-8")

    runner._remove_isolated_secrets()

    assert runner.app_token == ""
    assert runner.secrets_removed is True
    assert all(not (runner.private_root / name).exists() for name in ("config", "user-data", "webview"))


def test_runner_uses_private_start_switch_and_actual_token_directory() -> None:
    source = Path(smoke.__file__).read_text(encoding="utf-8")
    server_source = (
        Path(smoke.__file__).parents[1]
        / "third_party"
        / "com.coplaydev.unity-mcp"
        / "Editor"
        / "Services"
        / "ServerManagementService.cs"
    ).read_text(encoding="utf-8")

    assert '[str(prepared.desktop_executable), "--primitive-live-stdin"]' in source
    assert 'user_data_root / "config" / "app-session-token"' in source
    assert 'config_root / "app-session-token"' not in source
    assert '"vrc_reload_primitive_basis_fixture"' in source
    assert '"packagedUnityToolTreeDigest": packaged_unity_tool_tree_digest' in source
    assert '"runtimeUnityToolTreeDigest": runtime_unity_tool_tree_digest' in source
    assert '_tree_digest(project_root / "Assets" / "VRCForge" / "Editor")' in source
    assert "ignore_errors=True" not in source
    assert "_primitiveBasisServerProcess = System.Diagnostics.Process.Start(startInfo)" in server_source
    assert "StopPrimitiveBasisServerProcess()" in server_source
    assert "ProcessWindowStyle.Hidden" in server_source


def test_cleanup_continues_after_one_owned_process_wait_fails(monkeypatch) -> None:
    events: list[str] = []

    class FailingWaitProcess:
        pid = 101

        def __init__(self) -> None:
            self.running = True
            self.wait_calls = 0

        def poll(self):
            return None if self.running else 0

        def wait(self, *, timeout: float):
            self.wait_calls += 1
            events.append(f"unity-wait-{timeout:g}")
            if self.wait_calls == 1:
                raise RuntimeError("synthetic wait failure")
            self.running = False
            return 0

        def terminate(self) -> None:
            events.append("unity-terminate")

        def kill(self) -> None:
            events.append("unity-kill")
            self.running = False

    class NormalProcess:
        pid = 202

        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

        def wait(self, *, timeout: float):
            events.append(f"desktop-wait-{timeout:g}")
            self.running = False
            return 0

        def terminate(self) -> None:
            events.append("desktop-terminate")

        def kill(self) -> None:
            events.append("desktop-kill")
            self.running = False

    class LogHandle:
        def close(self) -> None:
            events.append("log-close")

    runner = object.__new__(smoke.PackagedModelPartSmoke)
    runner.unity_process = FailingWaitProcess()
    runner.desktop_process = NormalProcess()
    runner.desktop_log_handle = LogHandle()
    runner.app_port_released = False
    runner.bridge_port_released = False
    runner.desktop_clean = False

    monkeypatch.setattr(
        smoke,
        "_post_close_to_process_windows",
        lambda pid: events.append(f"close-{pid}") or True,
    )
    monkeypatch.setattr(
        smoke,
        "_wait_for_port_released",
        lambda port, label, *, timeout: events.append(f"port-{port}-{label}-{timeout:g}"),
    )

    with pytest.raises(smoke.PackagedModelPartSmokeError, match="wait failed"):
        runner._cleanup_owned_processes()

    assert "unity-terminate" in events
    assert "desktop-wait-20" in events
    assert "log-close" in events
    assert f"port-{smoke.APP_PORT}-app-20" in events
    assert f"port-{smoke.BRIDGE_PORT}-fixture bridge-20" in events
    assert runner.unity_process is None
    assert runner.desktop_process is None
    assert runner.desktop_log_handle is None
    assert runner.app_port_released is True
    assert runner.bridge_port_released is True
    assert runner.desktop_clean is False


def test_deterministic_unity_metas_stabilize_runtime_tool_tree(tmp_path: Path) -> None:
    editor_root = tmp_path / "Assets" / "VRCForge" / "Editor"
    nested = editor_root / "Nested"
    nested.mkdir(parents=True)
    (editor_root / "Tool.cs").write_text("internal class Tool {}\n", encoding="utf-8")
    (nested / "Helper.cs").write_text(
        "internal class Helper {}\n", encoding="utf-8"
    )

    smoke._materialize_deterministic_unity_metas(editor_root)
    first = smoke._tree_digest(editor_root)
    root_meta = editor_root.with_name("Editor.meta").read_bytes()
    generated = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.meta"))
    smoke._materialize_deterministic_unity_metas(editor_root)

    assert smoke._tree_digest(editor_root) == first
    assert editor_root.with_name("Editor.meta").read_bytes() == root_meta
    assert generated == [
        "Assets/VRCForge/Editor.meta",
        "Assets/VRCForge/Editor/Nested.meta",
        "Assets/VRCForge/Editor/Nested/Helper.cs.meta",
        "Assets/VRCForge/Editor/Tool.cs.meta",
    ]

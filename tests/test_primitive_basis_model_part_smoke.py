from __future__ import annotations

import json
import socket
import zipfile
from pathlib import Path

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

import subprocess

import pytest

import dashboard_server as server
from test_external_mcp_apply_recovery import _gateway, _project


TARGET = "vrcforge_install_vpm_package"


def create_package_checkpoint(gateway, root):
    gateway._write_handlers[TARGET] = server.AGENT_GATEWAY._write_handlers[TARGET]
    return gateway.approval_transactions._create_pre_write_checkpoint(
        {"targetTool": TARGET}, {"projectPath": str(root)}
    )


def test_cold_vpm_install_archives_ignored_packages_and_restores_new_files(tmp_path, monkeypatch):
    root = _project(tmp_path)
    gateway = _gateway(tmp_path)
    package = root / "Packages/embedded"
    package.mkdir()
    package_manifest = package / "package.json"
    package_manifest.write_bytes(b'{"version":"1.0.0"}')
    lock = root / "Packages/vpm-manifest.json"
    lock.write_bytes(b'{"locked":{}}')
    (root / ".gitignore").write_text("Packages/embedded/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "Baseline"], cwd=root, check=True)
    monkeypatch.setattr(server, "list_running_unity_processes", lambda **kwargs: [])
    gateway.approval_transactions.checkpoint_prepare_handler = server.prepare_unity_checkpoint_sync

    before = create_package_checkpoint(gateway, root)
    assert before["ok"] and before["strategy"] == "archive"
    assert before["archiveScopeReason"] == "host_write_to_closed_project"
    assert "unityPrepare" not in before
    lock.write_bytes(b'{"locked":{"new":"1.0.0"}}')
    package_manifest.write_bytes(b'{"version":"2.0.0"}')
    added = root / "Packages/new"
    added.mkdir()
    (added / "package.json").write_bytes(b"new")
    (root / "Packages/packages-lock.json").write_bytes(b"{}")

    restored = gateway.checkpoint_recovery._restore_archive_checkpoint(before)
    assert restored["ok"], restored
    assert lock.read_bytes() == b'{"locked":{}}'
    assert package_manifest.read_bytes() == b'{"version":"1.0.0"}'
    assert not added.exists()
    assert not (root / "Packages/packages-lock.json").exists()


@pytest.mark.parametrize("visible", ["open", "unknown"])
def test_vpm_install_preserves_live_checkpoint_requirements(tmp_path, monkeypatch, visible):
    root = _project(tmp_path)
    gateway = _gateway(tmp_path)

    def processes(*, require_discovery_evidence):
        assert require_discovery_evidence
        if visible == "unknown":
            raise OSError("Process table unavailable")
        return [{"commandLine": f'Unity.exe -projectPath "{root}"'}]

    monkeypatch.setattr(server, "list_running_unity_processes", processes)
    calls = []

    def prepare(project, *, checkpoint_files=None):
        calls.append((project, checkpoint_files))
        return {"ok": False, "blocking": True, "code": "checkpoint_core_capability_unavailable", "error": "Editor unavailable"}

    gateway.approval_transactions.checkpoint_prepare_handler = prepare
    result = create_package_checkpoint(gateway, root)
    assert calls == [(root, None)]
    assert not result["ok"] and result["code"] == "checkpoint_core_capability_unavailable"

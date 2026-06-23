from __future__ import annotations

import importlib
from pathlib import Path


def test_find_vrcforge_executable_prefers_current_packaged_root(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    current_root = tmp_path / "Current VRCForge"
    backend = current_root / "backend" / "vrcforge_backend.exe"
    desktop = current_root / "VRCForge.exe"
    old_program_files = tmp_path / "Program Files"
    old_desktop = old_program_files / "VRCForge" / "VRCForge.exe"
    backend.parent.mkdir(parents=True)
    old_desktop.parent.mkdir(parents=True)
    backend.write_text("backend", encoding="utf-8")
    desktop.write_text("desktop", encoding="utf-8")
    old_desktop.write_text("old desktop", encoding="utf-8")

    monkeypatch.delenv("VRCFORGE_EXE", raising=False)
    monkeypatch.setenv("ProgramFiles", str(old_program_files))
    monkeypatch.setenv("ProgramFiles(x86)", "")
    monkeypatch.setattr(module.sys, "executable", str(backend))
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)

    assert module.find_vrcforge_executable() == desktop.resolve()


def test_preflight_does_not_launch_runtime_when_start_disabled(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")
    config = tmp_path / "agent_gateway.json"
    config.write_text('{"token":"test-token","enabled":true,"allow_write_requests":true}', encoding="utf-8")
    bridge = module.VRCForgeBridge(
        base_url="http://127.0.0.1:8757",
        config_path=config,
        timeout_seconds=0.1,
        start_runtime=False,
    )

    monkeypatch.setattr(bridge, "runtime_port_open", lambda: False)

    def fail_launch() -> dict[str, object]:
        raise AssertionError("try_launch_runtime should not be called when start_runtime is false")

    def offline_request(*args, **kwargs) -> dict[str, object]:
        raise RuntimeError("runtime offline")

    monkeypatch.setattr(bridge, "try_launch_runtime", fail_launch)
    monkeypatch.setattr(bridge, "request_json", offline_request)

    report = bridge.preflight()

    assert report["ok"] is False
    assert "launch" not in report
    assert report["error"] == "runtime offline"


def test_stdio_bridge_start_runtime_is_explicit_opt_in() -> None:
    module = importlib.import_module("tools.vrcforge_agent_mcp_stdio")

    assert module.parse_args([]).start_runtime is False
    assert module.parse_args(["--start-runtime"]).start_runtime is True
    parsed = module.parse_args(["--start-runtime", "--no-start"])
    assert parsed.start_runtime is True
    assert parsed.no_start is True

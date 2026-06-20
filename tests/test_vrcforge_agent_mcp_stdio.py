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

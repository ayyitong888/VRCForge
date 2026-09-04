from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
INSTALLER = ROOT / "tools" / "install-unity-project.ps1"


def test_plugin_upgrade_preserves_project_generated_assets(tmp_path: Path) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required for the Windows Unity installer contract")

    source = tmp_path / "source" / "VRCForge"
    source.mkdir(parents=True)
    (source / "new-plugin.txt").write_text("new", encoding="utf-8")

    project = tmp_path / "project"
    old_plugin = project / "Assets" / "VRCForge"
    generated = old_plugin / "Generated"
    generated.mkdir(parents=True)
    (old_plugin / "old-plugin.txt").write_text("old", encoding="utf-8")
    (generated / "UserMaterial.mat").write_text("user-material", encoding="utf-8")
    (generated / "UserMaterial.mat.meta").write_text("guid: keep-me", encoding="utf-8")
    (old_plugin / "Generated.meta").write_text("guid: keep-folder", encoding="utf-8")
    (project / "Packages").mkdir()
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings").mkdir()
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n", encoding="utf-8"
    )

    command = (
        f"& '{INSTALLER}' -ProjectPath '{project}' "
        f"-SourceAssetsPath '{source}'"
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert (old_plugin / "new-plugin.txt").read_text(encoding="utf-8") == "new"
    assert (old_plugin / "Generated" / "UserMaterial.mat").read_text(encoding="utf-8") == "user-material"
    assert (old_plugin / "Generated" / "UserMaterial.mat.meta").read_text(encoding="utf-8") == "guid: keep-me"
    assert (old_plugin / "Generated.meta").read_text(encoding="utf-8") == "guid: keep-folder"
    backups = list((project / ".vrcforge" / "backups").glob("VRCForge_*"))
    assert len(backups) == 1
    assert (backups[0] / "old-plugin.txt").read_text(encoding="utf-8") == "old"
    assert "Preserved project-generated assets at:" in completed.stdout

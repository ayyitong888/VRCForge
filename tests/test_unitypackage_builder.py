from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


def test_unitypackage_builder_does_not_write_asset_for_folders(tmp_path: Path) -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is required to run build_unitypackage.ps1")

    repo_root = Path(__file__).resolve().parents[1]
    source = tmp_path / "Assets" / "VRCForge"
    editor = source / "Editor"
    editor.mkdir(parents=True)
    (editor / "ExampleTool.cs").write_text("// example\n", encoding="utf-8")
    output = tmp_path / "VRCForge.unitypackage"

    subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repo_root / "packaging" / "build_unitypackage.ps1"),
            "-SourceAssetsPath",
            str(source),
            "-OutputPath",
            str(output),
        ],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
    )

    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    with tarfile.open(output, mode="r:gz") as archive:
        archive.extractall(unpacked)

    folder_entries = []
    file_entries = []
    for entry_dir in [path for path in unpacked.iterdir() if path.is_dir()]:
        pathname = entry_dir / "pathname"
        meta = entry_dir / "asset.meta"
        if not pathname.exists() or not meta.exists():
            continue
        meta_text = meta.read_text(encoding="utf-8")
        if "folderAsset: yes" in meta_text:
            folder_entries.append(entry_dir)
        elif pathname.read_text(encoding="utf-8").strip().endswith("ExampleTool.cs"):
            file_entries.append(entry_dir)

    assert folder_entries
    assert file_entries
    assert all(not (entry / "asset").exists() for entry in folder_entries)
    assert all((entry / "asset").is_file() for entry in file_entries)

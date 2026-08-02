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
    # Deliberately outside the checkout: release packaging builds from staging.
    source = tmp_path / "release-staging" / "unity_plugin" / "Assets" / "VRCForge"
    editor = source / "Editor"
    editor.mkdir(parents=True)
    (editor / "ExampleTool.cs").write_text("// example\n", encoding="utf-8")
    (editor / "ExampleTool.cs.meta").write_text("fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    (editor / "Ignored.meta").write_text("not an asset\n", encoding="utf-8")
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
        assert meta.read_bytes().endswith(b"\n"), pathname.read_text(encoding="utf-8")
        meta_text = meta.read_text(encoding="utf-8")
        if "folderAsset: yes" in meta_text:
            folder_entries.append(entry_dir)
        elif pathname.read_text(encoding="utf-8").strip().endswith("ExampleTool.cs"):
            file_entries.append(entry_dir)

    assert folder_entries
    assert file_entries
    assert all(not (entry / "asset").exists() for entry in folder_entries)
    assert all((entry / "asset").is_file() for entry in file_entries)
    pathnames = [
        (entry / "pathname").read_text(encoding="utf-8").strip()
        for entry in unpacked.iterdir()
        if entry.is_dir() and (entry / "pathname").exists()
    ]
    assert not any(pathname.endswith(".meta") for pathname in pathnames)
    assert "Assets/VRCForge" in pathnames
    assert "Assets/VRCForge/Editor" in pathnames
    assert "Assets/VRCForge/Editor/ExampleTool.cs" in pathnames
    assert all("release-staging" not in pathname for pathname in pathnames)
    assert all("unity_plugin" not in pathname for pathname in pathnames)
    assert not any("coplay" in pathname.lower() or "mcpforunity" in pathname.lower() for pathname in pathnames)
    example_entry = next(
        entry
        for entry in file_entries
        if (entry / "pathname").read_text(encoding="utf-8").strip()
        == "Assets/VRCForge/Editor/ExampleTool.cs"
    )
    assert "guid: 0123456789abcdef0123456789abcdef" in (example_entry / "asset.meta").read_text(encoding="utf-8")


def test_real_unitypackage_bundles_first_party_core_and_all_product_sources(tmp_path: Path) -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is required to run build_unitypackage.ps1")
    repo_root = Path(__file__).resolve().parents[1]
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
            str(repo_root / "Assets" / "VRCForge"),
            "-OutputPath",
            str(output),
        ],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
    )
    with tarfile.open(output, mode="r:gz") as archive:
        packaged_paths = {
            archive.extractfile(member).read().decode("utf-8").strip()
            for member in archive.getmembers()
            if member.isfile() and member.name.endswith("/pathname")
        }
    source_cs = {
        "Assets/VRCForge/" + path.relative_to(repo_root / "Assets" / "VRCForge").as_posix()
        for path in (repo_root / "Assets" / "VRCForge").rglob("*.cs")
    }
    assert source_cs <= packaged_paths
    assert {
        "Assets/VRCForge/Core/MCP/VRCForgeToolAttribute.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeParameterAttribute.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeApprovedObjectReceipt.cs",
        "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
        "Assets/VRCForge/Editor/MCP/VRCForgeMcpToolContract.cs",
        "Assets/VRCForge/Editor/McpBridgeBootstrap.cs",
    } <= packaged_paths
    assert not any("coplay" in path.lower() or "mcpforunity" in path.lower() for path in packaged_paths)

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


PUBLISHED_1_3_6_COMMON_GUIDS = {
    "Assets/VRCForge": "e5a9931c984c4fa0bffe353deb4f5b32",
    "Assets/VRCForge/Editor": "e953c1262ab344c09d874ea224a23ce8",
    "Assets/VRCForge/Editor/AssetTools.cs": "816f91d7d3084d9fa255c3cd7ccf045a",
    "Assets/VRCForge/Editor/AvatarControlScanner.cs": "714baa66aa8444c7a2d85c06df8cb0d1",
    "Assets/VRCForge/Editor/AvatarParameterScanner.cs": "4a578085f27d47f786fa2ea72a0ce5d1",
    "Assets/VRCForge/Editor/AvatarParameterWriter.cs": "99f00b6651cc4ed5a0aef8fa5bac687c",
    "Assets/VRCForge/Editor/AvatarPerformanceTool.cs": "9205a9bfbfee4e3e851eeb66c4375d9c",
    "Assets/VRCForge/Editor/BlendshapeApplier.cs": "5a231b6ae5e1486aa849c75b64a56561",
    "Assets/VRCForge/Editor/BlendshapeExporter.cs": "90c10f2d2d6143fc8d0a86f9cf63b0c8",
    "Assets/VRCForge/Editor/CheckpointRecoveryTool.cs": "718290bc8caa4f2d9b8e4845ab0099e9",
    "Assets/VRCForge/Editor/ClothingFxAuthor.cs": "0ed1eebb3a084b2b8b5a15f69e1d3741",
    "Assets/VRCForge/Editor/CompileErrorReader.cs": "770b47b04f2845c2b940204500c45f59",
    "Assets/VRCForge/Editor/ComponentTools.cs": "90166391211949a796e53983f9c032e8",
    "Assets/VRCForge/Editor/ConsoleTools.cs": "c286dd9b198048e4a1dc0b2da7bbf4a9",
    "Assets/VRCForge/Editor/GameObjectTools.cs": "5288f78afd8e4d6cae8873199e6aad60",
    "Assets/VRCForge/Editor/Generic": "c95c9f47527049fcafc757042103caee",
    "Assets/VRCForge/Editor/Generic/UnityAssetPrefabCrud.cs": "9ddec2d706af4784bd9236c89c4ad263",
    "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs": "5962232c1c484a5a8e2be7466450fcec",
    "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs": "43c1a4b531504acd9961e50e46ae3472",
    "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs": "4606369cb045466a89a544b1a2b8a272",
    "Assets/VRCForge/Editor/Generic/UnityGameObjectCrud.cs": "9840237206a64e8bb7360a086df0aaec",
    "Assets/VRCForge/Editor/MAComponentWriter.cs": "ad341c384a27432196bced52fe633371",
    "Assets/VRCForge/Editor/MaterialTuningApplier.cs": "7cf23143450547e49085e10ec70d0d90",
    "Assets/VRCForge/Editor/McpBridgeBootstrap.cs": "a9954f97ccf74dfbb14eb1adf88df02d",
    "Assets/VRCForge/Editor/OutfitPackageImporter.cs": "4979eb968a0e49808701998d3cc9f0a6",
    "Assets/VRCForge/Editor/PrefabTools.cs": "da602fa305224f098b12881af1a3dbfa",
    "Assets/VRCForge/Editor/SceneViewCaptureTool.cs": "68de91a0bd1e4ff3aae42d6c3e4f90d1",
    "Assets/VRCForge/Editor/SetupOutfitTool.cs": "cbfd8fa2fb524827ad442dc5da532a10",
    "Assets/VRCForge/Editor/ShaderMaterialAdapters.cs": "12dc51ae3c74432ab745b25833f13e09",
    "Assets/VRCForge/Editor/ShaderMaterialScanner.cs": "2c464309fd6146bdbc888f0c4e0ecc47",
    "Assets/VRCForge/Editor/VRCForgeOutputPathGuard.cs": "994397c7e2b642bda74a3dadf3b6d848",
    "Assets/VRCForge/Editor/VrmExporter.cs": "3beff9c9d2604525b9e9c869ca5b91a7",
    "Assets/VRCForge/Editor/WardrobeManagerWriter.cs": "2e2239f7cb1d429289ba79708c1cf506",
    "Assets/VRCForge/Editor/WardrobeOutfitPartWriter.cs": "d738fda955854c42875af52632b4c823",
    "Assets/VRCForge/Editor/WardrobeOutfitWriter.cs": "2f9bc0a8cac747498ca8ca6d9026c180",
    "Assets/VRCForge/Editor/WardrobeScanner.cs": "d40e2e47dca649189684b81c11ce248a",
}

RETIRED_GUIDS = {
    "1d2ac338c0b461cafc0ca7b6871e6304",
    "e3ea79e5b45092c05901a8e6a0230cf6",
    "9ef91b08379b1e2120da076139d37484",
    "65a86f7265c22863a08d7def00521c50",
    "8b2e3c74998c4021f894bb52f364203e",
    "38d1e11cad40830a19c7b4b3e8f0d418",
    "fe99b2166dd28b6ee9efae0066c039cf",
}

FROZEN_SOURCE_META_GUIDS = {
    "Assets/VRCForge/Editor/MCP/VRCForgeMcpTrustedRelease.cs": "3e93945bf8684c27886af32aacb460ea",
    "Assets/VRCForge/Editor/PrimitiveBasisFixtureInspector.cs": "80b878673ab4475ebea84c182651852f",
    "Assets/VRCForge/Editor/PrimitiveBasisLiveGuard.cs": "990e1e58a5aa4913bb734a9cdd7eea3a",
}

RELEASE_PAIRING_ASSET_GUIDS = {
    "Assets/VRCForge/Editor/MCP/VRCForgeMcpTrustedRelease.json": "2b1fe687d6f68a50dab7b4a3bd4e2c25",
}

DOCUMENTATION_PATHS = {
    "Assets/VRCForge/Documentation",
    "Assets/VRCForge/Documentation/README.txt",
    "Assets/VRCForge/Documentation/LICENSE-GPL-3.0.txt",
    "Assets/VRCForge/Documentation/NOTICE.txt",
    "Assets/VRCForge/Documentation/USER_MANUAL.txt",
    "Assets/VRCForge/Documentation/DEPENDENCIES.txt",
}

EXCLUDED_PACKAGE_ROOTS = (
    "Assets/VRCForge/Runtime",
    "Assets/VRCForge/Runtime/AvatarEncryption",
    "Assets/VRCForge/Generated",
)

GUID_MANIFEST_SHA256 = "6bb92d68a99d648e3179a8de74320c8ae89b333a1a56105588d5edfde6734cbe"


def test_non_editor_csharp_cannot_leak_unityeditor_references() -> None:
    """Runtime/Core assets must remain compilable without UnityEditor.dll.

    A .unitypackage import compiles Core files in runtime-capable assemblies;
    an unguarded UnityEditor reference can therefore fail player compilation
    and block an SDK avatar build.  Editor-owned code is intentionally excluded
    because its assembly already targets UnityEditor.
    """
    repo_root = Path(__file__).resolve().parents[1]
    source_root = repo_root / "Assets" / "VRCForge"
    leaked = []
    reference_pattern = re.compile(r"\b(?:UnityEditor|EditorUtility|GlobalObjectId)\b")
    for source_path in source_root.rglob("*.cs"):
        relative = source_path.relative_to(source_root).as_posix()
        if "/Editor/" in f"/{relative}" or relative.startswith("Editor/"):
            continue
        text = source_path.read_text(encoding="utf-8-sig")
        # Remove comments before checking references so documentation does not
        # accidentally become a compile contract.
        code = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        code = re.sub(r"//[^\r\n]*", "", code)
        if not reference_pattern.search(code):
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or not re.match(r"^#if\s+UNITY_EDITOR$", lines[0]):
            leaked.append(relative)
            continue
        if lines[-1] != "#endif":
            leaked.append(relative)
    assert leaked == [], f"UnityEditor references escaped the Editor guard: {leaked}"


def test_public_guid_manifest_pins_the_published_1_3_6_common_paths() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "packaging" / "unitypackage_guid_manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == GUID_MANIFEST_SHA256
    manifest = json.loads(manifest_bytes.decode("utf-8"))

    assert manifest["schema"] == "vrcforge.unitypackage-guid-manifest.v1"
    entries = manifest["entries"]
    entry_map = {entry["path"]: entry["guid"] for entry in entries}
    assert len(entries) == 73
    assert {path: entry_map[path] for path in PUBLISHED_1_3_6_COMMON_GUIDS} == PUBLISHED_1_3_6_COMMON_GUIDS
    assert {path: entry_map[path] for path in FROZEN_SOURCE_META_GUIDS} == FROZEN_SOURCE_META_GUIDS
    assert {path: entry_map[path] for path in RELEASE_PAIRING_ASSET_GUIDS} == RELEASE_PAIRING_ASSET_GUIDS
    assert not any(
        path == excluded or path.startswith(f"{excluded}/")
        for path in entry_map
        for excluded in EXCLUDED_PACKAGE_ROOTS
    )
    assert len({entry["guid"] for entry in entries}) == len(entries)
    assert not RETIRED_GUIDS.intersection(entry["guid"] for entry in entries)


def test_unitypackage_builder_does_not_write_asset_for_folders(tmp_path: Path) -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is required to run build_unitypackage.ps1")

    repo_root = Path(__file__).resolve().parents[1]
    # Deliberately outside the checkout: release packaging builds from staging.
    source = tmp_path / "release-staging" / "unity_plugin" / "Assets" / "VRCForge"
    shutil.copytree(repo_root / "Assets" / "VRCForge", source)
    empty_private = source / "Runtime" / "EmptyPrivate"
    empty_private.mkdir(parents=True)
    (source / "Generated").mkdir()
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
        elif pathname.read_text(encoding="utf-8").strip().endswith("UnityGameObjectCrud.cs"):
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
    assert "Assets/VRCForge/Editor/Generic" in pathnames
    assert "Assets/VRCForge/Editor/Generic/UnityGameObjectCrud.cs" in pathnames
    assert "Assets/VRCForge/Runtime" not in pathnames
    assert "Assets/VRCForge/Runtime/EmptyPrivate" not in pathnames
    assert "Assets/VRCForge/Generated" not in pathnames
    assert all("release-staging" not in pathname for pathname in pathnames)
    assert all("unity_plugin" not in pathname for pathname in pathnames)
    assert not any("coplay" in pathname.lower() or "mcpforunity" in pathname.lower() for pathname in pathnames)
    example_entry = next(
        entry
        for entry in file_entries
        if (entry / "pathname").read_text(encoding="utf-8").strip()
        == "Assets/VRCForge/Editor/Generic/UnityGameObjectCrud.cs"
    )
    assert f"guid: {PUBLISHED_1_3_6_COMMON_GUIDS['Assets/VRCForge/Editor/Generic/UnityGameObjectCrud.cs']}" in (
        example_entry / "asset.meta"
    ).read_text(encoding="utf-8")


def _stable_unity_guid(pathname: str) -> str:
    return hashlib.sha256(f"vrcforge.unitypackage.v1/{pathname}".encode()).hexdigest()[:32]


def _minimal_complete_manifest() -> dict[str, object]:
    paths = {
        "Assets/VRCForge",
        "Assets/VRCForge/Editor",
        "Assets/VRCForge/Editor/Example.cs",
        *DOCUMENTATION_PATHS,
    }
    return {
        "schema": "vrcforge.unitypackage-guid-manifest.v1",
        "entries": [
            {"path": path, "guid": _stable_unity_guid(path)}
            for path in sorted(paths)
        ],
    }


def _run_builder_with_isolated_guid_manifest(
    tmp_path: Path,
    manifest: dict[str, object] | None,
    *,
    editor_meta_guid: str | None = None,
    generated_poison: bool = False,
) -> subprocess.CompletedProcess[str]:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is required to run build_unitypackage.ps1")
    repo_root = Path(__file__).resolve().parents[1]
    isolated_root = tmp_path / "isolated-repo"
    packaging_root = isolated_root / "packaging"
    packaging_root.mkdir(parents=True)
    shutil.copy2(repo_root / "packaging" / "build_unitypackage.ps1", packaging_root / "build_unitypackage.ps1")
    if manifest is not None:
        (packaging_root / "unitypackage_guid_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    for document in ("README.md", "LICENSE", "NOTICE", "USER_MANUAL.md", "DEPENDENCIES.md"):
        shutil.copy2(repo_root / document, isolated_root / document)
    source = isolated_root / "release-staging" / "Assets" / "VRCForge"
    editor = source / "Editor"
    editor.mkdir(parents=True)
    (editor / "Example.cs").write_text("// example\n", encoding="utf-8")
    if generated_poison:
        generated = source / "Generated"
        generated.mkdir()
        (generated / "Poison.cs").write_text("// must never ship\n", encoding="utf-8")
    if editor_meta_guid is not None:
        (source / "Editor.meta").write_text(
            f"fileFormatVersion: 2\nguid: {editor_meta_guid}\nfolderAsset: yes\n",
            encoding="utf-8",
        )
    output = tmp_path / "VRCForge.unitypackage"
    return subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(packaging_root / "build_unitypackage.ps1"),
            "-SourceAssetsPath",
            str(source),
            "-OutputPath",
            str(output),
        ],
        cwd=isolated_root,
        check=False,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    ("manifest", "expected_error"),
    [
        (None, "guid manifest is missing"),
        (
            {"schema": "vrcforge.unitypackage-guid-manifest.v1", "entries": [{"path": "Assets/VRCForge"}]},
            "entry is missing path or guid",
        ),
        (
            {
                "schema": "vrcforge.unitypackage-guid-manifest.v1",
                "entries": [
                    {"path": "Assets/VRCForge", "guid": "0123456789abcdef0123456789abcdef"},
                    {"path": "Assets/VRCForge", "guid": "fedcba9876543210fedcba9876543210"},
                ],
            },
            "duplicate path",
        ),
        (
            {
                "schema": "vrcforge.unitypackage-guid-manifest.v1",
                "entries": [
                    {"path": "Assets/VRCForge", "guid": "0123456789abcdef0123456789abcdef"},
                    {"path": "Assets/VRCForge/Editor", "guid": "0123456789abcdef0123456789abcdef"},
                ],
            },
            "duplicate guid",
        ),
        (
            {
                "schema": "vrcforge.unitypackage-guid-manifest.v1",
                "entries": [
                    {"path": "Assets/VRCForge", "guid": "1d2ac338c0b461cafc0ca7b6871e6304"},
                ],
            },
            "retired guid",
        ),
    ],
)
def test_builder_rejects_missing_duplicate_or_retired_public_guid_manifest_entries(
    tmp_path: Path,
    manifest: dict[str, object] | None,
    expected_error: str,
) -> None:
    result = _run_builder_with_isolated_guid_manifest(tmp_path, manifest)

    assert result.returncode != 0
    assert expected_error in (result.stdout + result.stderr).lower()
    assert not (tmp_path / "VRCForge.unitypackage").exists()


def test_builder_rejects_source_meta_that_overrides_a_published_guid(tmp_path: Path) -> None:
    manifest = {
        "schema": "vrcforge.unitypackage-guid-manifest.v1",
        "entries": [
            {"path": "Assets/VRCForge/Editor", "guid": "0123456789abcdef0123456789abcdef"},
        ],
    }
    result = _run_builder_with_isolated_guid_manifest(
        tmp_path,
        manifest,
        editor_meta_guid="fedcba9876543210fedcba9876543210",
    )

    assert result.returncode != 0
    assert "published guid drift" in (result.stdout + result.stderr).lower()
    assert not (tmp_path / "VRCForge.unitypackage").exists()


def test_builder_rejects_nonempty_generated_tree(tmp_path: Path) -> None:
    result = _run_builder_with_isolated_guid_manifest(
        tmp_path,
        _minimal_complete_manifest(),
        generated_poison=True,
    )

    assert result.returncode != 0
    assert "generated is runtime output" in (result.stdout + result.stderr).lower()
    assert not (tmp_path / "VRCForge.unitypackage").exists()


def test_builder_rejects_emitted_path_missing_from_complete_manifest(tmp_path: Path) -> None:
    manifest = _minimal_complete_manifest()
    manifest["entries"] = [
        entry
        for entry in manifest["entries"]
        if entry["path"] != "Assets/VRCForge/Editor/Example.cs"
    ]
    result = _run_builder_with_isolated_guid_manifest(tmp_path, manifest)

    assert result.returncode != 0
    assert "emitted pathname is absent from guid manifest" in (result.stdout + result.stderr).lower()
    assert not (tmp_path / "VRCForge.unitypackage").exists()


def test_builder_rejects_complete_manifest_path_that_was_not_emitted(tmp_path: Path) -> None:
    manifest = _minimal_complete_manifest()
    missing_path = "Assets/VRCForge/Editor/Missing.cs"
    manifest["entries"].append({"path": missing_path, "guid": _stable_unity_guid(missing_path)})
    result = _run_builder_with_isolated_guid_manifest(tmp_path, manifest)

    assert result.returncode != 0
    assert "guid manifest pathname was not emitted" in (result.stdout + result.stderr).lower()
    assert not (tmp_path / "VRCForge.unitypackage").exists()


def _read_unitypackage_inventory(package_path: Path) -> tuple[dict[str, str], set[str], set[str]]:
    with tarfile.open(package_path, mode="r:gz") as archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        entry_paths = {
            member_name.rsplit("/", 1)[0]: archive.extractfile(member).read().decode("utf-8").strip()
            for member_name, member in members.items()
            if member_name.endswith("/pathname")
        }
        packaged_guids = {}
        file_paths = set()
        directory_paths = set()
        for entry, pathname in entry_paths.items():
            meta_text = archive.extractfile(members[f"{entry}/asset.meta"]).read().decode("utf-8")
            matches = re.findall(r"(?m)^guid:\s*([0-9a-fA-F]{32})\s*$", meta_text)
            assert len(matches) == 1, pathname
            assert Path(entry).name.lower() == matches[0].lower(), pathname
            packaged_guids[pathname] = matches[0].lower()
            if f"{entry}/asset" in members:
                file_paths.add(pathname)
            else:
                directory_paths.add(pathname)
    return packaged_guids, file_paths, directory_paths


def test_real_unitypackage_bundles_first_party_core_and_all_product_sources(tmp_path: Path) -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is required to run build_unitypackage.ps1")
    repo_root = Path(__file__).resolve().parents[1]
    outputs = [tmp_path / "VRCForge-first.unitypackage", tmp_path / "VRCForge-second.unitypackage"]
    for output in outputs:
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
    packaged_guids, file_paths, directory_paths = _read_unitypackage_inventory(outputs[0])
    repeated_guids, repeated_files, repeated_directories = _read_unitypackage_inventory(outputs[1])
    assert (packaged_guids, file_paths, directory_paths) == (
        repeated_guids,
        repeated_files,
        repeated_directories,
    )
    packaged_paths = set(packaged_guids)
    manifest = json.loads((repo_root / "packaging" / "unitypackage_guid_manifest.json").read_text(encoding="utf-8"))
    manifest_guids = {entry["path"]: entry["guid"] for entry in manifest["entries"]}
    assert packaged_guids == manifest_guids
    assert len(packaged_paths) == 73
    assert len(file_paths) == 66
    assert len(directory_paths) == 7
    assert not any(
        path == excluded or path.startswith(f"{excluded}/")
        for path in packaged_paths
        for excluded in EXCLUDED_PACKAGE_ROOTS
    )
    assert len(packaged_guids) == len(set(packaged_guids.values()))
    assert not RETIRED_GUIDS.intersection(packaged_guids.values())
    assert packaged_guids.keys() >= PUBLISHED_1_3_6_COMMON_GUIDS.keys()
    assert {
        path: packaged_guids[path] for path in PUBLISHED_1_3_6_COMMON_GUIDS
    } == PUBLISHED_1_3_6_COMMON_GUIDS
    assert {path: packaged_guids[path] for path in FROZEN_SOURCE_META_GUIDS} == FROZEN_SOURCE_META_GUIDS
    assert {path: packaged_guids[path] for path in RELEASE_PAIRING_ASSET_GUIDS} == RELEASE_PAIRING_ASSET_GUIDS
    receipt_path = "Assets/VRCForge/Core/MCP/VRCForgeApprovedObjectReceipt.cs"
    assert packaged_guids[receipt_path] == "c03999e57815100961016fab067f9c2b"
    source_cs = {
        "Assets/VRCForge/" + path.relative_to(repo_root / "Assets" / "VRCForge").as_posix()
        for path in (repo_root / "Assets" / "VRCForge").rglob("*.cs")
    }
    assert source_cs <= packaged_paths
    assert {
        "Assets/VRCForge/Core/MCP/VRCForgeCommandAttribute.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeInputAttribute.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeToolRegistry.cs",
        "Assets/VRCForge/Core/MCP/VRCForgeApprovedObjectReceipt.cs",
        "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs",
        "Assets/VRCForge/Editor/MCP/VRCForgeMcpSourceMigration.cs",
        "Assets/VRCForge/Editor/MCP/VRCForgeMcpToolContract.cs",
        "Assets/VRCForge/Editor/McpBridgeBootstrap.cs",
        "Assets/VRCForge/Editor/VRCForgeUninstaller.cs",
        "Assets/VRCForge/Documentation",
        "Assets/VRCForge/Documentation/README.txt",
        "Assets/VRCForge/Documentation/LICENSE-GPL-3.0.txt",
        "Assets/VRCForge/Documentation/NOTICE.txt",
        "Assets/VRCForge/Documentation/USER_MANUAL.txt",
        "Assets/VRCForge/Documentation/DEPENDENCIES.txt",
    } <= packaged_paths
    assert not any("packages/com.coplaydev" in path.lower() or "mcpforunity" in path.lower() for path in packaged_paths)
    assert not any("coplay" in path.lower() for path in packaged_paths)

    migration_source = (
        repo_root / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpSourceMigration.cs"
    ).read_text(encoding="utf-8-sig")
    forbidden_pattern = re.compile(
        r"coplay|gamelovers|mcpforunity|2025-11-25|tcp-length-prefixed-jsonrpc|"
        r"VRCForgeToolAttribute|VRCForgeParameterAttribute|VRCForgeResponse|ThirdPartyNotices",
        re.IGNORECASE,
    )
    assert forbidden_pattern.search(migration_source) is None

    builder_source = (repo_root / "packaging" / "build_unitypackage.ps1").read_text(encoding="utf-8-sig")
    assert '"com.coplaydev.unity-mcp"' in builder_source
    assert '"com.gamelovers.unity-mcp"' in builder_source
    assert "third-party conflict detector allowlist drifted" in builder_source
    assert "conflict detector contains non-allowlisted MCP residue" in builder_source

    with tarfile.open(outputs[0], mode="r:gz") as archive:
        pathname_members = {
            archive.extractfile(member).read().decode("utf-8").strip(): member.name.rsplit("/", 1)[0]
            for member in archive.getmembers()
            if member.isfile() and member.name.endswith("/pathname")
        }
        archive_names = set(archive.getnames())
        receipt_entry = pathname_members[receipt_path]
        receipt_source = archive.extractfile(f"{receipt_entry}/asset").read().decode("utf-8-sig")
        receipt_lines = [line.strip() for line in receipt_source.splitlines() if line.strip()]
        assert receipt_lines[0] == "#if UNITY_EDITOR"
        assert receipt_lines[-1] == "#endif"
        assert "EditorUtility" in receipt_source
        assert "GlobalObjectId" in receipt_source
        for packaged_path, entry in pathname_members.items():
            for entry_file_name in ("asset", "asset.meta"):
                member_name = f"{entry}/{entry_file_name}"
                if member_name not in archive_names:
                    continue
                asset_text = archive.extractfile(member_name).read().decode("latin-1")
                if entry_file_name == "asset" and packaged_path == "Assets/VRCForge/Editor/McpBridgeBootstrap.cs":
                    for allowed_package_id in ("com.coplaydev.unity-mcp", "com.gamelovers.unity-mcp"):
                        assert asset_text.count(allowed_package_id) == 1
                        asset_text = asset_text.replace(allowed_package_id, "")
                assert forbidden_pattern.search(asset_text) is None, f"{packaged_path}/{entry_file_name}"
        documentation_sources = {
            "Assets/VRCForge/Documentation/README.txt": repo_root / "README.md",
            "Assets/VRCForge/Documentation/LICENSE-GPL-3.0.txt": repo_root / "LICENSE",
            "Assets/VRCForge/Documentation/NOTICE.txt": repo_root / "NOTICE",
            "Assets/VRCForge/Documentation/USER_MANUAL.txt": repo_root / "USER_MANUAL.md",
            "Assets/VRCForge/Documentation/DEPENDENCIES.txt": repo_root / "DEPENDENCIES.md",
        }
        for packaged_path, source_path in documentation_sources.items():
            entry = pathname_members[packaged_path]
            assert archive.extractfile(f"{entry}/asset").read() == source_path.read_bytes()
            meta_text = archive.extractfile(f"{entry}/asset.meta").read().decode("utf-8")
            expected_guid = hashlib.sha256(
                f"vrcforge.unitypackage.v1/{packaged_path}".encode("utf-8")
            ).hexdigest()[:32]
            assert "TextScriptImporter:" in meta_text
            assert f"guid: {expected_guid}" in meta_text
            assert meta_text.endswith("\n")


@pytest.mark.parametrize(
    ("meta_guids", "expected_error"),
    [
        (
            [
                "0123456789abcdef0123456789abcdef\n"
                "guid: fedcba9876543210fedcba9876543210",
            ],
            "missing or malformed guid",
        ),
        (
            [
                "0123456789abcdef0123456789abcdef",
                "0123456789abcdef0123456789abcdef",
            ],
            "duplicate guid",
        ),
        (
            ["1d2ac338c0b461cafc0ca7b6871e6304"],
            "retired guid",
        ),
    ],
)
def test_unitypackage_builder_rejects_duplicate_or_retired_source_guids(
    tmp_path: Path,
    meta_guids: list[str],
    expected_error: str,
) -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is required to run build_unitypackage.ps1")
    repo_root = Path(__file__).resolve().parents[1]
    source = tmp_path / "release-staging" / "unity_plugin" / "Assets" / "VRCForge"
    editor = source / "Editor"
    editor.mkdir(parents=True)
    for index, guid in enumerate(meta_guids):
        asset = editor / f"Example{index}.cs"
        asset.write_text("// example\n", encoding="utf-8")
        asset.with_suffix(".cs.meta").write_text(
            f"fileFormatVersion: 2\nguid: {guid}\n",
            encoding="utf-8",
        )
    output = tmp_path / "VRCForge.unitypackage"

    result = subprocess.run(
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
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert expected_error in (result.stdout + result.stderr).lower()
    assert not output.exists()

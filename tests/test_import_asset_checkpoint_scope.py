"""Authoritative rollback footprints for sealed project-asset imports."""
from __future__ import annotations

from copy import deepcopy
import io
from pathlib import Path
import tarfile

import pytest

from prepared_file_imports import capture_regular_file
from prepared_loose_outfit_import import prepare_loose_outfit_import
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    install_prepared_calls,
)
from unity_import_checkpoint import prepare_import_asset_checkpoint


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    for folder in ("Assets", "Packages", "ProjectSettings"):
        (root / folder).mkdir(parents=True)
    return root


def _refresh(project: Path) -> tuple[str, dict]:
    return (
        "vrc_refresh_asset_database",
        {
            "projectPath": str(project),
            "resolvePackages": False,
            "packageResolveTimeoutSeconds": 120,
        },
    )


def _prepare(project: Path, arguments: dict) -> dict:
    return prepare_import_asset_checkpoint(
        project,
        arguments,
        verify_project=lambda identity: Path(identity["projectPath"]),
    )


def _loose_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    project = _project(tmp_path)
    source = tmp_path / "Source"
    (source / "Textures").mkdir(parents=True)
    (source / "Dress.prefab").write_text("prefab", encoding="utf-8")
    (source / "Textures/body.png").write_bytes(b"png")
    loose = prepare_loose_outfit_import(
        source_root=source,
        project_root=project,
        target_folder="Assets/Imports/New",
    )
    plan = {"kind": "loose_prefab_copy"}
    evidence = {
        "kind": "loose_prefab_copy",
        "plan": plan,
        "projectIdentity": {"projectPath": str(project)},
        "loosePlan": loose,
    }
    return project, source, install_prepared_calls({}, [_refresh(project)], evidence)


def _tar_member(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(data)
    archive.addfile(member, io.BytesIO(data))


def _unitypackage(path: Path, asset_paths: list[str]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for index, asset_path in enumerate(asset_paths):
            prefix = f"entry-{index}"
            _tar_member(archive, f"{prefix}/pathname", asset_path.encode("utf-8"))
            _tar_member(archive, f"{prefix}/asset", f"payload-{index}".encode("utf-8"))
            _tar_member(archive, f"{prefix}/asset.meta", b"fileFormatVersion: 2\n")
    return path


def _package_fixture(project: Path, packages: list[Path], *, kind: str) -> dict:
    queue = []
    calls = []
    for package in packages:
        identity, digest = capture_regular_file(package, label="Import package")
        identity["sha256"] = digest
        queue.append({"identity": identity})
        calls.append((
            "vrc_import_unitypackage",
            {
                "projectPath": str(project),
                "unityPackagePath": str(package),
                "expectedSha256": digest,
                "expectedSize": identity["size"],
                "expectedAssetPaths": [],
                "interactive": False,
            },
        ))
    calls.append(_refresh(project))
    evidence = {
        "kind": kind,
        "plan": {"kind": kind},
        "projectIdentity": {"projectPath": str(project)},
        "queue": queue,
    }
    return install_prepared_calls({}, calls, evidence)


def test_loose_import_derives_only_new_files_metadata_and_new_parent_metadata(tmp_path: Path) -> None:
    project, _source, arguments = _loose_fixture(tmp_path)
    arguments.update({
        "archiveFiles": ["Assets/Forged.asset"],
        "assetFootprint": {"archiveFiles": ["Assets/Forged.asset"]},
    })
    result = _prepare(project, arguments)

    assert result["ok"] is True, result
    assert result["archiveScopeReason"] == "sealed_import_complete_asset_footprint"
    assert result["assetFootprint"] == {
        "schema": "vrcforge.asset_checkpoint_footprint.v1",
        "archiveFiles": [],
        "archiveAbsentFiles": [
            "Assets/Imports.meta",
            "Assets/Imports/New.meta",
            "Assets/Imports/New/Dress.prefab",
            "Assets/Imports/New/Dress.prefab.meta",
            "Assets/Imports/New/Textures.meta",
            "Assets/Imports/New/Textures/body.png",
            "Assets/Imports/New/Textures/body.png.meta",
        ],
        "archiveAbsentDirectories": [
            "Assets/Imports",
            "Assets/Imports/New",
            "Assets/Imports/New/Textures",
        ],
    }


def test_loose_import_source_hash_drift_fails_closed(tmp_path: Path) -> None:
    project, source, arguments = _loose_fixture(tmp_path)
    (source / "Dress.prefab").write_text("changed", encoding="utf-8")
    result = _prepare(project, arguments)
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert "drifted" in result["error"]


def test_loose_import_rejects_destination_that_appeared_after_approval(tmp_path: Path) -> None:
    project, _source, arguments = _loose_fixture(tmp_path)
    destination = project / "Assets/Imports/New/Dress.prefab"
    destination.parent.mkdir(parents=True)
    destination.write_text("foreign", encoding="utf-8")
    result = _prepare(project, arguments)
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert "appeared" in result["error"]


def test_loose_import_rejects_valid_seal_with_wrong_core_call(tmp_path: Path) -> None:
    project, _source, arguments = _loose_fixture(tmp_path)
    evidence = deepcopy(arguments[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY]["evidence"])
    forged = install_prepared_calls({}, [("vrc_refresh_asset_database", {
        **_refresh(project)[1], "resolvePackages": True,
    })], evidence)
    result = _prepare(project, forged)
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert "sealed Core calls changed" in result["error"]


def test_package_sequence_scans_every_dependency_queue_asset(tmp_path: Path) -> None:
    project = _project(tmp_path)
    support = _unitypackage(tmp_path / "Support.unitypackage", ["Assets/Support/Base.mat"])
    target = _unitypackage(
        tmp_path / "Target.unitypackage",
        ["Assets/Outfits/Dress.prefab", "Assets/Outfits/Textures/body.png"],
    )
    result = _prepare(
        project,
        _package_fixture(project, [support, target], kind="unitypackage_import_sequence"),
    )
    assert result["ok"] is True, result
    assert result["assetFootprint"]["archiveFiles"] == []
    assert result["assetFootprint"]["archiveAbsentFiles"] == [
        "Assets/Outfits.meta",
        "Assets/Outfits/Dress.prefab",
        "Assets/Outfits/Dress.prefab.meta",
        "Assets/Outfits/Textures.meta",
        "Assets/Outfits/Textures/body.png",
        "Assets/Outfits/Textures/body.png.meta",
        "Assets/Support.meta",
        "Assets/Support/Base.mat",
        "Assets/Support/Base.mat.meta",
    ]
    assert result["assetFootprint"]["archiveAbsentDirectories"] == [
        "Assets/Outfits", "Assets/Outfits/Textures", "Assets/Support",
    ]


def test_package_mixed_existing_and_new_destinations_are_partitioned(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "Assets/Outfits").mkdir()
    (project / "Assets/Outfits/Dress.prefab").write_text("old", encoding="utf-8")
    (project / "Assets/Outfits/Dress.prefab.meta").write_text("old-meta", encoding="utf-8")
    package = _unitypackage(
        tmp_path / "Mixed.unitypackage",
        ["Assets/Outfits/Dress.prefab", "Assets/Outfits/New.mat"],
    )
    result = _prepare(project, _package_fixture(project, [package], kind="unitypackage_import"))
    assert result["ok"] is True, result
    assert result["assetFootprint"]["archiveFiles"] == [
        "Assets/Outfits/Dress.prefab", "Assets/Outfits/Dress.prefab.meta",
    ]
    assert result["assetFootprint"]["archiveAbsentFiles"] == [
        "Assets/Outfits/New.mat", "Assets/Outfits/New.mat.meta",
    ]
    assert result["assetFootprint"]["archiveAbsentDirectories"] == []


def test_package_overwrite_only_archives_existing_asset_and_meta(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "Assets/Outfits").mkdir()
    for name in ("Dress.prefab", "Dress.prefab.meta"):
        (project / f"Assets/Outfits/{name}").write_text(name, encoding="utf-8")
    package = _unitypackage(tmp_path / "Overwrite.unitypackage", ["Assets/Outfits/Dress.prefab"])
    result = _prepare(project, _package_fixture(project, [package], kind="unitypackage_import"))
    assert result["ok"] is True, result
    assert result["assetFootprint"]["archiveFiles"] == [
        "Assets/Outfits/Dress.prefab", "Assets/Outfits/Dress.prefab.meta",
    ]
    assert result["assetFootprint"]["archiveAbsentFiles"] == []
    assert result["assetFootprint"]["archiveAbsentDirectories"] == []


@pytest.mark.parametrize(
    "asset_paths,error_text",
    [
        (["Packages/com.example/file.asset"], "canonical Assets/ destinations"),
        (["Assets/Outfits/Dress.prefab", "Assets/outfits/dress.prefab"], "duplicate asset paths"),
        (["Assets/Outfits/Editor/Setup.cs"], "code-bearing package"),
    ],
)
def test_package_rejects_outside_duplicate_or_code_bearing_paths(
    tmp_path: Path, asset_paths: list[str], error_text: str
) -> None:
    project = _project(tmp_path)
    package = _unitypackage(tmp_path / "Rejected.unitypackage", asset_paths)
    result = _prepare(project, _package_fixture(project, [package], kind="unitypackage_import"))
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert error_text in result["error"]


def test_package_rejects_tampered_call_seal(tmp_path: Path) -> None:
    project = _project(tmp_path)
    package = _unitypackage(tmp_path / "Dress.unitypackage", ["Assets/Outfits/Dress.prefab"])
    arguments = _package_fixture(project, [package], kind="unitypackage_import")
    arguments[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY]["calls"][0]["arguments"]["expectedSize"] += 1
    result = _prepare(project, arguments)
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert "digest is invalid" in result["error"]

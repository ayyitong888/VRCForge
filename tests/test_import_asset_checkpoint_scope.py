"""Authoritative rollback footprints for sealed project-asset imports."""
from __future__ import annotations

from copy import deepcopy
import io
import os
from pathlib import Path
import tarfile
import zipfile

import pytest

from prepared_file_imports import capture_regular_file
from prepared_loose_outfit_import import prepare_loose_outfit_import
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
    install_prepared_calls,
)
from unity_import_checkpoint import prepare_import_asset_checkpoint
from prepared_archive_imports import prepare_zip_member_materialization


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


def _loose_code_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    project, source, _ = _loose_fixture(tmp_path)
    (source / "Editor.asmdef").write_text('{"name":"Editor"}', encoding="utf-8")
    loose = prepare_loose_outfit_import(
        source_root=source,
        project_root=project,
        target_folder="Assets/Imports/New",
    )
    evidence = {
        "kind": "loose_prefab_copy",
        "plan": {"kind": "loose_prefab_copy"},
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


def _with_code_bearing_risk_facts(arguments: dict) -> dict:
    evidence = deepcopy(arguments[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY]["evidence"])
    evidence["plan"]["riskFacts"] = {
        "codeBearingPackage": True,
        "fullProjectCheckpointRequired": True,
        "checkpointScope": "full_project",
        "recommendedRiskLevel": "high",
        "mediumEligible": False,
        "reasonCodes": ["code_bearing_package_requires_full_project_checkpoint"],
    }
    return install_prepared_calls(
        {}, build_prepared_execution_plan(arguments), evidence
    )


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


def test_loose_code_bearing_import_uses_full_project_checkpoint_scope(tmp_path: Path) -> None:
    project, _source, arguments = _loose_code_fixture(tmp_path)
    prepared: list[object] = []

    result = prepare_import_asset_checkpoint(
        project,
        _with_code_bearing_risk_facts(arguments),
        verify_project=lambda identity: Path(identity["projectPath"]),
        prepare=lambda _root, *, checkpoint_files=None: prepared.append(checkpoint_files) or {"ok": True},
    )

    assert result["ok"] is True, result
    assert result["checkpointScope"] == "full_project"
    assert result["codeBearingPaths"] == ["Assets/Imports/New/Editor.asmdef"]
    assert result["archiveScopeReason"] == "code_bearing_package_full_project"
    assert prepared == [None]


def test_loose_code_bearing_prepare_failure_does_not_fallback_to_asset_scope(tmp_path: Path) -> None:
    project, _source, arguments = _loose_code_fixture(tmp_path)
    prepared: list[object] = []

    result = prepare_import_asset_checkpoint(
        project,
        _with_code_bearing_risk_facts(arguments),
        verify_project=lambda identity: Path(identity["projectPath"]),
        prepare=lambda _root, *, checkpoint_files=None: prepared.append(checkpoint_files) or {
            "ok": False,
            "blocking": True,
            "code": "unity_checkpoint_prepare_failed",
            "error": "full project snapshot unavailable",
        },
    )

    assert result["ok"] is False
    assert result["code"] == "unity_checkpoint_prepare_failed"
    assert prepared == [None]
    assert "archiveScopeReason" not in result


def test_loose_code_bearing_import_rejects_legacy_plan_before_full_project_prepare(tmp_path: Path) -> None:
    project, _source, arguments = _loose_code_fixture(tmp_path)
    prepared: list[object] = []

    result = prepare_import_asset_checkpoint(
        project,
        arguments,
        verify_project=lambda identity: Path(identity["projectPath"]),
        prepare=lambda _root, *, checkpoint_files=None: prepared.append(checkpoint_files) or {"ok": True},
    )

    assert result["ok"] is False
    assert result["code"] == "checkpoint_scope_unresolved"
    assert "predates code-bearing" in result["error"]
    assert prepared == []


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
    ],
)
def test_package_rejects_outside_or_duplicate_paths(
    tmp_path: Path, asset_paths: list[str], error_text: str
) -> None:
    project = _project(tmp_path)
    package = _unitypackage(tmp_path / "Rejected.unitypackage", asset_paths)
    result = _prepare(project, _package_fixture(project, [package], kind="unitypackage_import"))
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert error_text in result["error"]


def test_code_bearing_package_uses_full_project_checkpoint_scope(tmp_path: Path) -> None:
    project = _project(tmp_path)
    package = _unitypackage(
        tmp_path / "Scripted.unitypackage",
        ["Assets/Outfits/Dress.prefab", "Assets/Outfits/Editor/Setup.cs"],
    )
    prepared: list[object] = []

    result = prepare_import_asset_checkpoint(
        project,
        _with_code_bearing_risk_facts(
            _package_fixture(project, [package], kind="unitypackage_import")
        ),
        verify_project=lambda identity: Path(identity["projectPath"]),
        prepare=lambda _root, *, checkpoint_files=None: prepared.append(checkpoint_files) or {"ok": True},
    )

    assert result["ok"] is True, result
    assert result["checkpointScope"] == "full_project"
    assert result["archiveScopeReason"] == "code_bearing_package_full_project"
    assert prepared == [None]


def test_code_bearing_full_project_prepare_failure_does_not_fallback_to_asset_scope(tmp_path: Path) -> None:
    project = _project(tmp_path)
    package = _unitypackage(
        tmp_path / "Scripted.unitypackage",
        ["Assets/Outfits/Dress.prefab", "Assets/Outfits/Editor/Setup.asmdef"],
    )
    prepared: list[object] = []

    result = prepare_import_asset_checkpoint(
        project,
        _with_code_bearing_risk_facts(
            _package_fixture(project, [package], kind="unitypackage_import")
        ),
        verify_project=lambda identity: Path(identity["projectPath"]),
        prepare=lambda _root, *, checkpoint_files=None: prepared.append(checkpoint_files) or {
            "ok": False,
            "blocking": True,
            "code": "unity_checkpoint_prepare_failed",
            "error": "full project snapshot unavailable",
        },
    )

    assert result["ok"] is False
    assert result["code"] == "unity_checkpoint_prepare_failed"
    assert prepared == [None]
    assert "archiveScopeReason" not in result


def test_code_bearing_package_rejects_legacy_plan_before_full_project_prepare(tmp_path: Path) -> None:
    project = _project(tmp_path)
    package = _unitypackage(
        tmp_path / "LegacyScripted.unitypackage",
        ["Assets/Outfits/Editor/Setup.asmdef"],
    )
    prepared: list[object] = []

    result = prepare_import_asset_checkpoint(
        project,
        _package_fixture(project, [package], kind="unitypackage_import"),
        verify_project=lambda identity: Path(identity["projectPath"]),
        prepare=lambda _root, *, checkpoint_files=None: prepared.append(checkpoint_files) or {"ok": True},
    )

    assert result["ok"] is False
    assert result["code"] == "checkpoint_scope_unresolved"
    assert "predates code-bearing" in result["error"]
    assert prepared == []


def test_package_rejects_tampered_call_seal(tmp_path: Path) -> None:
    project = _project(tmp_path)
    package = _unitypackage(tmp_path / "Dress.unitypackage", ["Assets/Outfits/Dress.prefab"])
    arguments = _package_fixture(project, [package], kind="unitypackage_import")
    arguments[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY]["calls"][0]["arguments"]["expectedSize"] += 1
    result = _prepare(project, arguments)
    assert result["ok"] is False and result["code"] == "checkpoint_scope_unresolved"
    assert "digest is invalid" in result["error"]


def _nested_package_fixture(
    tmp_path: Path, *, package_bytes: bytes | None = None, companion_members: bool = False
):
    project = _project(tmp_path)
    package = _unitypackage(tmp_path / "Dress.unitypackage", ["Assets/Outfits/Dress.prefab"])
    if package_bytes is not None:
        package.write_bytes(package_bytes)
    arguments = _package_fixture(project, [package], kind="unitypackage_import_sequence")
    source = tmp_path / "Clothes.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(package, "Nested/Dress.unitypackage")
        if companion_members:
            archive.writestr("Nested/Readme.txt", b"selected-only import companion")
            archive.writestr("Nested/preview.png", b"not an actual image")
    temp_parent = tmp_path / "staging"
    temp_parent.mkdir()
    facts = prepare_zip_member_materialization(
        source=source, temp_parent=temp_parent,
        selected_members=[{"path": "Nested/Dress.unitypackage", "targetName": "prepared.unitypackage"}],
    )
    seal = arguments[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY]
    evidence = deepcopy(seal["evidence"])
    selected = facts["selected"][0]
    identity = {"path": str(temp_parent / selected["targetName"]),
                "sha256": selected["sha256"], "size": selected["size"]}
    evidence["queue"] = [{"identity": identity, "materializationIndex": 0}]
    evidence["materializations"] = [facts]
    calls = deepcopy(seal["calls"])
    calls[0]["arguments"]["unityPackagePath"] = identity["path"]
    return project, source, temp_parent, calls, evidence


def test_nested_package_checkpoint_reads_sealed_member_before_materialization(tmp_path: Path) -> None:
    project, _source, staging, calls, evidence = _nested_package_fixture(tmp_path)
    assert list(staging.iterdir()) == []
    result = _prepare(project, install_prepared_calls({}, calls, evidence))
    assert result["ok"] is True, result
    assert result["assetFootprint"]["archiveAbsentFiles"] == [
        "Assets/Outfits.meta", "Assets/Outfits/Dress.prefab", "Assets/Outfits/Dress.prefab.meta",
    ]
    assert list(staging.iterdir()) == []  # Checkpoint inspection owns no persistent staging file.
    assert list((project / "Assets").iterdir()) == []


def test_nested_package_checkpoint_ignores_unselected_outer_zip_companions(tmp_path: Path) -> None:
    project, _source, staging, calls, evidence = _nested_package_fixture(
        tmp_path, companion_members=True
    )
    result = _prepare(project, install_prepared_calls({}, calls, evidence))
    assert result["ok"] is True, result
    assert list(staging.iterdir()) == []
    assert list((project / "Assets").iterdir()) == []


@pytest.mark.parametrize("drift", ["source", "queue_path", "queue_hash", "member", "index", "appeared"])
def test_nested_package_checkpoint_rejects_source_or_binding_drift(tmp_path: Path, drift: str) -> None:
    project, source, staging, calls, evidence = _nested_package_fixture(tmp_path)
    if drift == "source":
        source.write_bytes(b"changed archive")
    elif drift == "queue_path":
        evidence["queue"][0]["identity"]["path"] = str(staging / "another.unitypackage")
    elif drift == "queue_hash":
        evidence["queue"][0]["identity"]["sha256"] = "a" * 64
    elif drift == "member":
        evidence["materializations"][0]["selected"][0]["path"] = "Other.unitypackage"
    elif drift == "index":
        evidence["queue"][0]["materializationIndex"] = 100
    else:
        (staging / "prepared.unitypackage").write_bytes(b"foreign")
    result = _prepare(project, install_prepared_calls({}, calls, evidence))
    assert result["ok"] is False and result["mutationStarted"] is False
    assert list((project / "Assets").iterdir()) == []
    assert list(staging.iterdir()) == ([staging / "prepared.unitypackage"] if drift == "appeared" else [])


@pytest.mark.parametrize("valid_tar", [True, False])
def test_nested_package_disk_spool_cleans_up_on_success_and_parse_failure(tmp_path: Path, valid_tar: bool) -> None:
    payload = os.urandom(9 * 1024 * 1024)
    if valid_tar:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            _tar_member(archive, "entry/pathname", b"Assets/Outfits/Texture.png")
            _tar_member(archive, "entry/asset.meta", b"fileFormatVersion: 2\n")
            _tar_member(archive, "entry/asset", payload)
        payload = buffer.getvalue()
    assert len(payload) > 8 * 1024 * 1024
    project, _source, staging, calls, evidence = _nested_package_fixture(tmp_path, package_bytes=payload)
    result = _prepare(project, install_prepared_calls({}, calls, evidence))
    assert result["ok"] is valid_tar, result
    assert list(staging.iterdir()) == []
    assert list((project / "Assets").iterdir()) == []

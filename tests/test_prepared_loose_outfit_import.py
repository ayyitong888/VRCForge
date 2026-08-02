from __future__ import annotations

from pathlib import Path
import os

import pytest

from prepared_file_imports import (
    capture_regular_file,
    cleanup_owned_import,
    copy_approved_file_create_new,
    prepare_project_asset_target,
)
from prepared_loose_outfit_import import execute_loose_outfit_import, prepare_loose_outfit_import


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    return project


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "Source"
    (source / "nested").mkdir(parents=True)
    (source / "Dress.prefab").write_bytes(b"prefab")
    (source / "Dress.prefab.meta").write_bytes(b"meta")
    (source / "nested" / "Texture.png").write_bytes(b"png")
    (source / "ignored.txt").write_text("ignored", encoding="utf-8")
    return source


def test_prepare_and_execute_create_new_manifest_with_meta(tmp_path: Path) -> None:
    project, source = _project(tmp_path), _source(tmp_path)
    plan = prepare_loose_outfit_import(source_root=source, project_root=project, target_folder="Assets/VRCForge/Imported")
    result = execute_loose_outfit_import(plan)
    assert result["copiedFileCount"] == 3
    assert (project / "Assets/VRCForge/Imported/Dress.prefab").read_bytes() == b"prefab"
    assert (project / "Assets/VRCForge/Imported/Dress.prefab.meta").read_bytes() == b"meta"
    assert (project / "Assets/VRCForge/Imported/nested/Texture.png").read_bytes() == b"png"


def test_source_content_or_manifest_drift_blocks_before_output(tmp_path: Path) -> None:
    project, source = _project(tmp_path), _source(tmp_path)
    plan = prepare_loose_outfit_import(source_root=source, project_root=project, target_folder="Assets/VRCForge/Imported")
    (source / "Dress.prefab").write_bytes(b"changed")
    with pytest.raises(ValueError, match="drifted"):
        execute_loose_outfit_import(plan)
    assert not (project / "Assets/VRCForge/Imported").exists()


def test_foreign_target_race_is_not_deleted(tmp_path: Path) -> None:
    project, source = _project(tmp_path), _source(tmp_path)
    plan = prepare_loose_outfit_import(source_root=source, project_root=project, target_folder="Assets/VRCForge/Imported")
    foreign = project / "Assets/VRCForge/Imported/Dress.prefab"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"foreign")
    with pytest.raises(ValueError, match="appeared"):
        execute_loose_outfit_import(plan)
    assert foreign.read_bytes() == b"foreign"


def test_partial_failure_cleans_only_owned_outputs(tmp_path: Path) -> None:
    project, source = _project(tmp_path), _source(tmp_path)
    plan = prepare_loose_outfit_import(source_root=source, project_root=project, target_folder="Assets/VRCForge/Imported")
    # The first sorted source is copied, then this later target appears as foreign.
    foreign = project / "Assets/VRCForge/Imported/nested/Texture.png"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"foreign")
    with pytest.raises(ValueError, match="appeared"):
        execute_loose_outfit_import(plan)
    assert foreign.read_bytes() == b"foreign"
    assert not (project / "Assets/VRCForge/Imported/Dress.prefab").exists()


def test_size_and_file_limits_and_reparse_source_are_rejected(tmp_path: Path) -> None:
    project, source = _project(tmp_path), _source(tmp_path)
    with pytest.raises(ValueError, match="file-count"):
        prepare_loose_outfit_import(source_root=source, project_root=project, target_folder="Assets/Imported", max_files=1)
    with pytest.raises(ValueError, match="total-size"):
        prepare_loose_outfit_import(source_root=source, project_root=project, target_folder="Assets/Imported", max_total_bytes=1)
    link = source / "link"
    try:
        link.symlink_to(source / "nested", target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this Windows test host.")
    with pytest.raises(ValueError, match="symlink or reparse"):
        prepare_loose_outfit_import(source_root=source, project_root=project, target_folder="Assets/Imported")


def test_owned_copy_cleanup_refuses_same_path_foreign_replacement(tmp_path: Path) -> None:
    project, source = _project(tmp_path), _source(tmp_path)
    source_file = source / "Dress.prefab"
    identity, digest = capture_regular_file(source_file, label="Test source")
    target_plan = prepare_project_asset_target(project, "Assets/VRCForge/Imported", "Dress.prefab")
    target, copied_digest, ownership = copy_approved_file_create_new(
        source_identity=identity,
        source_sha256=digest,
        project_identity=target_plan["project"],
        assets_identity=target_plan["assets"],
        parent_identities=target_plan["parentIdentities"],
        absent_parent_relative_paths=target_plan["absentParentRelativePaths"],
        target_relative_path=target_plan["targetRelativePath"],
    )
    assert copied_digest == digest
    target.unlink()
    target.write_bytes(b"foreign")

    error = cleanup_owned_import(target, ownership)

    assert "refused foreign or modified replacement" in error
    assert target.read_bytes() == b"foreign"


def test_owned_copy_cleanup_deletes_only_unchanged_owned_output(tmp_path: Path) -> None:
    project, source = _project(tmp_path), _source(tmp_path)
    source_file = source / "Dress.prefab"
    identity, digest = capture_regular_file(source_file, label="Test source")
    target_plan = prepare_project_asset_target(project, "Assets/VRCForge/Imported", "Dress.prefab")
    target, _copied_digest, ownership = copy_approved_file_create_new(
        source_identity=identity,
        source_sha256=digest,
        project_identity=target_plan["project"],
        assets_identity=target_plan["assets"],
        parent_identities=target_plan["parentIdentities"],
        absent_parent_relative_paths=target_plan["absentParentRelativePaths"],
        target_relative_path=target_plan["targetRelativePath"],
    )

    assert cleanup_owned_import(target, ownership) == ""
    assert not target.exists()
    assert not (project / "Assets/VRCForge/Imported").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle sharing semantics")
def test_copy_holds_target_parent_against_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import windows_import_handles

    project, source = _project(tmp_path), _source(tmp_path)
    source_file = source / "Dress.prefab"
    identity, digest = capture_regular_file(source_file, label="Test source")
    target_plan = prepare_project_asset_target(project, "Assets/VRCForge/Imported", "Dress.prefab")
    target = project / target_plan["targetRelativePath"]
    original_create = windows_import_handles._create_target_handle
    replacement_blocked = False

    def create_after_replacement_probe(path: Path) -> int:
        nonlocal replacement_blocked
        try:
            path.parent.rename(path.parent.with_name("ForeignReplacement"))
        except OSError:
            replacement_blocked = True
        return original_create(path)

    monkeypatch.setattr(windows_import_handles, "_create_target_handle", create_after_replacement_probe)
    copied_target, _copied_digest, ownership = copy_approved_file_create_new(
        source_identity=identity,
        source_sha256=digest,
        project_identity=target_plan["project"],
        assets_identity=target_plan["assets"],
        parent_identities=target_plan["parentIdentities"],
        absent_parent_relative_paths=target_plan["absentParentRelativePaths"],
        target_relative_path=target_plan["targetRelativePath"],
    )
    assert replacement_blocked is True
    assert cleanup_owned_import(copied_target, ownership) == ""


@pytest.mark.skipif(os.name != "nt", reason="Windows handle sharing semantics")
def test_cleanup_holds_target_handle_against_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import windows_import_handles

    project, source = _project(tmp_path), _source(tmp_path)
    source_file = source / "Dress.prefab"
    identity, digest = capture_regular_file(source_file, label="Test source")
    target_plan = prepare_project_asset_target(project, "Assets/VRCForge/Imported", "Dress.prefab")
    target, _copied_digest, ownership = copy_approved_file_create_new(
        source_identity=identity,
        source_sha256=digest,
        project_identity=target_plan["project"],
        assets_identity=target_plan["assets"],
        parent_identities=target_plan["parentIdentities"],
        absent_parent_relative_paths=target_plan["absentParentRelativePaths"],
        target_relative_path=target_plan["targetRelativePath"],
    )
    original_mark_delete = windows_import_handles._mark_delete
    replacement_blocked = False

    def mark_after_replacement_probe(handle: int, *, label: str) -> None:
        nonlocal replacement_blocked
        if label == "import target":
            try:
                target.unlink()
            except OSError:
                replacement_blocked = True
        original_mark_delete(handle, label=label)

    monkeypatch.setattr(windows_import_handles, "_mark_delete", mark_after_replacement_probe)
    assert cleanup_owned_import(target, ownership) == ""
    assert replacement_blocked is True
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle sharing semantics")
def test_parent_open_failure_retains_unproven_path_without_retrying_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import windows_import_handles

    project, source = _project(tmp_path), _source(tmp_path)
    source_file = source / "Dress.prefab"
    identity, digest = capture_regular_file(source_file, label="Test source")
    target_plan = prepare_project_asset_target(
        project,
        "Assets/VRCForge/Imported",
        "Dress.prefab",
    )
    original_open = windows_import_handles._open_directory
    failed_once = False

    def fail_second_created_parent_once(path: Path, *, delete_access: bool) -> int:
        nonlocal failed_once
        if delete_access and path.name == "Imported" and not failed_once:
            failed_once = True
            raise windows_import_handles.WindowsImportHandleError("injected directory-open failure")
        return original_open(path, delete_access=delete_access)

    monkeypatch.setattr(windows_import_handles, "_open_directory", fail_second_created_parent_once)
    with pytest.raises(
        windows_import_handles.WindowsImportHandleError,
        match="handle-bound cleanup also failed",
    ):
        copy_approved_file_create_new(
            source_identity=identity,
            source_sha256=digest,
            project_identity=target_plan["project"],
            assets_identity=target_plan["assets"],
            parent_identities=target_plan["parentIdentities"],
            absent_parent_relative_paths=target_plan["absentParentRelativePaths"],
            target_relative_path=target_plan["targetRelativePath"],
        )

    assert failed_once is True
    assert (project / "Assets/VRCForge/Imported").is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle sharing semantics")
def test_parent_identity_failure_uses_original_handle_for_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import windows_import_handles

    project, source = _project(tmp_path), _source(tmp_path)
    source_file = source / "Dress.prefab"
    identity, digest = capture_regular_file(source_file, label="Test source")
    target_plan = prepare_project_asset_target(
        project,
        "Assets/VRCForge/Imported",
        "Dress.prefab",
    )
    original_identity = windows_import_handles._identity
    failed_once = False

    def fail_created_parent_identity_once(path: Path, *, directory: bool = False):
        nonlocal failed_once
        if directory and path.name == "Imported" and not failed_once:
            failed_once = True
            raise windows_import_handles.WindowsImportHandleError("injected identity failure")
        return original_identity(path, directory=directory)

    monkeypatch.setattr(windows_import_handles, "_identity", fail_created_parent_identity_once)
    with pytest.raises(windows_import_handles.WindowsImportHandleError, match="injected identity failure"):
        copy_approved_file_create_new(
            source_identity=identity,
            source_sha256=digest,
            project_identity=target_plan["project"],
            assets_identity=target_plan["assets"],
            parent_identities=target_plan["parentIdentities"],
            absent_parent_relative_paths=target_plan["absentParentRelativePaths"],
            target_relative_path=target_plan["targetRelativePath"],
        )

    assert failed_once is True
    assert not (project / "Assets/VRCForge").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle sharing semantics")
def test_cleanup_removes_owned_directories_when_target_is_already_missing(tmp_path: Path) -> None:
    project, source = _project(tmp_path), _source(tmp_path)
    source_file = source / "Dress.prefab"
    identity, digest = capture_regular_file(source_file, label="Test source")
    target_plan = prepare_project_asset_target(
        project,
        "Assets/VRCForge/Imported",
        "Dress.prefab",
    )
    target, _copied_digest, ownership = copy_approved_file_create_new(
        source_identity=identity,
        source_sha256=digest,
        project_identity=target_plan["project"],
        assets_identity=target_plan["assets"],
        parent_identities=target_plan["parentIdentities"],
        absent_parent_relative_paths=target_plan["absentParentRelativePaths"],
        target_relative_path=target_plan["targetRelativePath"],
    )
    target.unlink()

    assert cleanup_owned_import(target, ownership) == ""
    assert not (project / "Assets/VRCForge").exists()

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import prepared_archive_imports as archive_imports


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "UnityProject"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)


def _facts(tmp_path: Path) -> tuple[Path, Path, dict]:
    project = _project(tmp_path)
    source = tmp_path / "source.zip"
    _zip(source, {"Folder/a.prefab": b"prefab", "Folder/note.txt": b"note"})
    facts = archive_imports.prepare_zip_extract(
        source=source,
        project_root=project,
        target_folder="Assets/VRCForge/Imports",
        target_root_name="pack_1234",
    )
    return project, source, facts


def test_zip_prepare_seals_central_directory_and_member_hashes(tmp_path: Path) -> None:
    _project, _source, facts = _facts(tmp_path)
    assert facts["schema"] == "vrcforge.prepared-zip-extract.v1"
    assert [entry["path"] for entry in facts["manifest"]] == ["Folder/a.prefab", "Folder/note.txt"]
    assert all({"crc", "compressedSize", "size", "sha256"} <= set(entry) for entry in facts["manifest"])


def test_zip_target_root_race_is_zero_overwrite(tmp_path: Path) -> None:
    project, _source, facts = _facts(tmp_path)
    root = project / facts["targetRootRelativePath"]
    root.mkdir(parents=True)
    marker = root / "marker.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="appeared after approval"):
        archive_imports.execute_zip_extract(facts)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_zip_source_drift_makes_zero_project_write(tmp_path: Path) -> None:
    project, source, facts = _facts(tmp_path)
    _zip(source, {"Folder/a.prefab": b"changed"})
    with pytest.raises(ValueError, match="drifted"):
        archive_imports.execute_zip_extract(facts)
    assert not (project / "Assets" / "VRCForge").exists()


def test_zip_parent_identity_drift_is_rejected_before_create(tmp_path: Path) -> None:
    project = _project(tmp_path)
    parent = project / "Assets" / "VRCForge" / "Imports"
    parent.mkdir(parents=True)
    source = tmp_path / "source.zip"
    _zip(source, {"a.prefab": b"a"})
    facts = archive_imports.prepare_zip_extract(
        source=source, project_root=project, target_folder="Assets/VRCForge/Imports", target_root_name="pack",
    )
    moved = parent.with_name("Imports-old")
    parent.rename(moved)
    parent.mkdir()
    with pytest.raises(ValueError, match="identity drifted"):
        archive_imports.execute_zip_extract(facts)
    assert not (parent / "pack").exists()


def test_zip_partial_failure_cleans_only_owned_outputs(monkeypatch, tmp_path: Path) -> None:
    project, _source, facts = _facts(tmp_path)
    original_open = zipfile.ZipFile.open
    calls = 0

    def flaky_open(self, name, mode="r", *args, **kwargs):
        nonlocal calls
        if mode == "r":
            calls += 1
            # Preparation has already run; fail the second execution member.
            if calls > 3:
                raise OSError("simulated read failure")
        return original_open(self, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", flaky_open)
    with pytest.raises(OSError, match="simulated read failure"):
        archive_imports.execute_zip_extract(facts)
    assert not (project / "Assets" / "VRCForge" / "Imports" / "pack_1234").exists()


def test_zip_rejects_unsafe_and_duplicate_members(tmp_path: Path) -> None:
    project = _project(tmp_path)
    for name in ("../escape.prefab", "C:/escape.prefab"):
        source = tmp_path / f"bad-{len(name)}.zip"
        _zip(source, {name: b"bad"})
        with pytest.raises(ValueError, match="unsafe"):
            archive_imports.prepare_zip_extract(source=source, project_root=project, target_folder="Assets/Imports", target_root_name="bad")
    duplicate = tmp_path / "duplicate.zip"
    _zip(duplicate, {"A.prefab": b"a", "a.prefab": b"b"})
    with pytest.raises(ValueError, match="duplicate"):
        archive_imports.prepare_zip_extract(source=duplicate, project_root=project, target_folder="Assets/Imports", target_root_name="bad")

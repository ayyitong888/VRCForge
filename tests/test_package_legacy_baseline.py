from __future__ import annotations

import io
import tarfile
import zipfile
import package_legacy_baseline as legacy_module
from pathlib import Path

from package_legacy_baseline import (
    create_legacy_preservation_snapshot,
    verify_legacy_baseline,
    verify_legacy_preservation_snapshot,
)


def test_unitypackage_directory_pathname_binds_root_meta(tmp_path: Path) -> None:
    archive = tmp_path / "directory.unitypackage"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data in (
            ("root/pathname", b"Assets/lilToon"),
            ("root/asset.meta", b"folderAsset: yes\n"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    root = tmp_path / "Assets/lilToon"
    root.mkdir(parents=True)
    meta = root.with_name("lilToon.meta")
    meta.write_bytes(b"folderAsset: yes\n")
    assert verify_legacy_baseline(archive, root)["ok"]
    meta.write_bytes(b"folderAsset: yes\nguid: user-change\n")
    result = verify_legacy_baseline(archive, root)
    assert not result["ok"]
    assert result["modified"] == ["../lilToon.meta"]


def _unitypackage(path: Path, payload: bytes = b"shader") -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in (
            ("abc/pathname", b"Assets/lilToon/Editor/lilConstants.cs\n"),
            ("abc/asset", payload),
            ("abc/asset.meta", b"fileFormatVersion: 2\n"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_legacy_baseline_accepts_exact_tree_and_reports_modified_unknown(tmp_path: Path) -> None:
    archive = tmp_path / "legacy.unitypackage"
    _unitypackage(archive)
    root = tmp_path / "Assets" / "lilToon"
    (root / "Editor").mkdir(parents=True)
    (root / "Editor" / "lilConstants.cs").write_bytes(b"shader")
    (root / "Editor" / "lilConstants.cs.meta").write_bytes(b"fileFormatVersion: 2\n")

    result = verify_legacy_baseline(archive, root)
    assert result["ok"] is True
    assert result["missing"] == []
    assert result["unknown"] == []
    assert result["modified"] == []

    (root / "user.txt").write_text("keep", encoding="utf-8")
    changed = verify_legacy_baseline(archive, root)
    assert changed["ok"] is False
    assert changed["unknown"] == ["user.txt"]


def test_legacy_preservation_snapshot_is_atomic_and_covers_root_meta(tmp_path: Path) -> None:
    root = tmp_path / "Assets" / "lilToon"
    (root / "Shader" / "Includes").mkdir(parents=True)
    (root / "Shader" / "Includes" / "custom.hlsl").write_bytes(b"custom")
    (root / "Editor" ).mkdir()
    root.with_name("lilToon.meta").write_bytes(b"guid: root\n")
    destination = tmp_path / ".vrcforge" / "package-backups" / "legacy.zip"
    destination.parent.mkdir(parents=True)

    receipt = create_legacy_preservation_snapshot(root, destination)
    assert receipt["ok"] is True
    assert receipt["entries"] == 2
    assert verify_legacy_preservation_snapshot(destination)["ok"] is True

    tampered = destination.with_name("tampered.zip")
    with zipfile.ZipFile(destination) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"tampered" if name.endswith("custom.hlsl") else source.read(name))
    assert verify_legacy_preservation_snapshot(tampered)["ok"] is False


def test_legacy_preservation_snapshot_never_overwrites_existing_target(tmp_path: Path) -> None:
    root = tmp_path / "Assets" / "lilToon"
    root.mkdir(parents=True)
    destination = tmp_path / "backup.zip"
    destination.write_bytes(b"keep")
    try:
        create_legacy_preservation_snapshot(root, destination)
    except ValueError as exc:
        assert "destination must be new" in str(exc)
    else:
        raise AssertionError("existing snapshot target was overwritten")
    assert destination.read_bytes() == b"keep"


def test_legacy_preservation_snapshot_rejects_source_tree_change(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "Assets" / "lilToon"
    root.mkdir(parents=True)
    (root / "original.txt").write_bytes(b"original")
    destination = tmp_path / "backup.zip"
    real_capture = legacy_module.capture_regular_file
    calls = 0

    def capture(path, *, label):
        nonlocal calls
        result = real_capture(path, label=label)
        if label == "Legacy Assets file" and calls == 0:
            (root / "created-during-copy.txt").write_bytes(b"new")
        calls += 1
        return result

    monkeypatch.setattr(legacy_module, "capture_regular_file", capture)
    try:
        legacy_module.create_legacy_preservation_snapshot(root, destination)
    except ValueError as exc:
        assert "tree changed" in str(exc)
    else:
        raise AssertionError("source mutation was accepted")
    assert not destination.exists()


def test_legacy_snapshot_supports_pre_1980_imported_files(tmp_path):
    import os
    root=tmp_path/'Assets'/'legacy';root.mkdir(parents=True)
    f=root/'old.shader';f.write_bytes(b'original');os.utime(f,(1,1))
    receipt=create_legacy_preservation_snapshot(root,tmp_path/'backup.zip')
    assert receipt['ok']
    assert verify_legacy_preservation_snapshot(tmp_path/'backup.zip')['ok']
    assert f.read_bytes()==b'original'

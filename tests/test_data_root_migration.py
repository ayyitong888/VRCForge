from __future__ import annotations

import json
from pathlib import Path

from data_root_migration import (
    LEGACY_RUNTIME_ITEMS,
    MARKER_NAME,
    migrate_legacy_runtime_data,
)


def build_legacy_root(root: Path) -> None:
    for name in ("logs", "artifacts", "backups", "project-indexes", "signing"):
        (root / name).mkdir(parents=True)
        (root / name / "keep.txt").write_text(name, encoding="utf-8")
    (root / "project-cache.json").write_text("{}", encoding="utf-8")
    # native-history lives inside artifacts and must move with it, wholesale.
    native = root / "artifacts" / "native-history"
    native.mkdir(parents=True)
    (native / "64hex-64hex.json").write_text("history", encoding="utf-8")


def read_marker(root: Path) -> dict:
    return json.loads((root / MARKER_NAME).read_text(encoding="utf-8"))


def test_migrates_all_known_items_wholesale(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    build_legacy_root(old)
    new.mkdir()

    report = migrate_legacy_runtime_data(old, new)

    assert report["alreadyMigrated"] is False
    assert report["dryRun"] is False
    assert report["moved"] == list(LEGACY_RUNTIME_ITEMS)
    assert report["skipped"] == []
    assert report["preserved"] == []
    assert report["markerWritten"] is True
    for name in LEGACY_RUNTIME_ITEMS:
        assert (new / name).exists()
        assert not (old / name).exists()
    # native-history travelled inside artifacts with no special-casing.
    assert (new / "artifacts" / "native-history" / "64hex-64hex.json").exists()
    assert read_marker(new) == {"schema": 1, "migratedFrom": str(old)}


def test_existing_target_is_skipped_never_merged(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    build_legacy_root(old)
    (new / "backups").mkdir(parents=True)
    (new / "backups" / "existing.txt").write_text("newer", encoding="utf-8")

    report = migrate_legacy_runtime_data(old, new)

    assert report["skipped"] == ["backups"]
    assert "backups" not in report["moved"]
    # source stays put, target keeps only its own content: no merge, no overwrite.
    assert (old / "backups" / "keep.txt").exists()
    assert (new / "backups" / "existing.txt").exists()
    assert not (new / "backups" / "keep.txt").exists()
    # every other known item still migrated.
    for name in ("logs", "artifacts", "project-indexes", "signing", "project-cache.json"):
        assert (new / name).exists()
    assert report["markerWritten"] is True
    assert read_marker(new) == {"schema": 1, "migratedFrom": str(old)}


def test_missing_source_with_existing_target_is_not_skipped(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    (old / "logs").mkdir(parents=True)
    (new / "backups").mkdir(parents=True)

    report = migrate_legacy_runtime_data(old, new)

    assert report["moved"] == ["logs"]
    assert report["skipped"] == []
    assert (new / "backups").exists()


def test_unknown_top_level_entries_preserved_and_reported(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    build_legacy_root(old)
    (old / "mystery-dir").mkdir()
    (old / "mystery-dir" / "x.txt").write_text("x", encoding="utf-8")
    (old / "mystery-file.txt").write_text("y", encoding="utf-8")

    report = migrate_legacy_runtime_data(old, new)

    assert sorted(report["preserved"]) == ["mystery-dir", "mystery-file.txt"]
    assert (old / "mystery-dir" / "x.txt").exists()
    assert (old / "mystery-file.txt").exists()
    assert (new / "logs").exists()


def test_marker_makes_later_runs_idempotent(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    build_legacy_root(old)

    first = migrate_legacy_runtime_data(old, new)
    assert first["moved"] == list(LEGACY_RUNTIME_ITEMS)

    second = migrate_legacy_runtime_data(old, new)
    assert second == {"alreadyMigrated": True}
    # a dry run against an already-migrated root short-circuits too.
    third = migrate_legacy_runtime_data(old, new, dry_run=True)
    assert third == {"alreadyMigrated": True}
    assert (new / "logs" / "keep.txt").exists()


def test_dry_run_computes_without_side_effects(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    build_legacy_root(old)
    (old / "unknown.txt").write_text("u", encoding="utf-8")

    report = migrate_legacy_runtime_data(old, new, dry_run=True)

    assert report["dryRun"] is True
    assert report["moved"] == list(LEGACY_RUNTIME_ITEMS)
    assert report["skipped"] == []
    assert sorted(report["preserved"]) == ["unknown.txt"]
    assert report["markerWritten"] is False
    assert not new.exists()
    for name in LEGACY_RUNTIME_ITEMS:
        assert (old / name).exists()
    assert (old / "unknown.txt").exists()


def test_dry_run_reports_skips_without_moving(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    build_legacy_root(old)
    (new / "signing").mkdir(parents=True)

    report = migrate_legacy_runtime_data(old, new, dry_run=True)

    assert report["skipped"] == ["signing"]
    assert (old / "signing" / "keep.txt").exists()
    assert not (new / "logs").exists()
    assert report["markerWritten"] is False
    assert not (new / MARKER_NAME).exists()


def test_missing_old_root_is_noop_without_marker(tmp_path: Path) -> None:
    old = tmp_path / "absent"
    new = tmp_path / "new"
    new.mkdir()

    report = migrate_legacy_runtime_data(old, new)

    assert report["moved"] == []
    assert report["skipped"] == []
    assert report["preserved"] == []
    assert report["markerWritten"] is False
    assert not (new / MARKER_NAME).exists()

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import prepared_archive_imports as archive_imports


def _zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)


def _facts(tmp_path: Path) -> tuple[Path, Path, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "upload.zip"
    _zip(source, {"nested/a.unitypackage": b"first", "b.unitypackage": b"second"})
    temp = tmp_path / "sealed-temp"
    temp.mkdir()
    facts = archive_imports.prepare_zip_member_materialization(
        source=source,
        temp_parent=temp,
        selected_members=[
            {"path": "nested/a.unitypackage", "targetName": "queue-01.unitypackage"},
            {"path": "b.unitypackage", "targetName": "queue-02.unitypackage"},
        ],
    )
    return source, temp, facts


def test_selected_members_seal_raw_normalized_manifest_and_queue_order(tmp_path: Path) -> None:
    _source, _temp, facts = _facts(tmp_path)
    assert facts["schema"] == "vrcforge.prepared-zip-materialization.v1"
    assert [item["path"] for item in facts["selected"]] == ["nested/a.unitypackage", "b.unitypackage"]
    assert [item["zipPath"] for item in facts["selected"]] == ["nested/a.unitypackage", "b.unitypackage"]
    assert all({"size", "sha256", "compressedSize", "compressionRatio"} <= set(item) for item in facts["selected"])


def test_materialize_exact_create_new_files_in_selected_order_and_explicit_cleanup(tmp_path: Path) -> None:
    _source, temp, facts = _facts(tmp_path)
    receipt = archive_imports.execute_zip_member_materialization(facts)
    assert [Path(item).name for item in receipt["files"]] == ["queue-01.unitypackage", "queue-02.unitypackage"]
    assert (temp / "queue-01.unitypackage").read_bytes() == b"first"
    assert (temp / "queue-02.unitypackage").read_bytes() == b"second"
    assert archive_imports.cleanup_owned_zip_materialization(receipt) == ""
    assert not list(temp.iterdir())


def test_source_drift_and_target_race_write_nothing_and_keep_foreign_file(tmp_path: Path) -> None:
    source, temp, facts = _facts(tmp_path)
    source.write_bytes(b"not an archive")
    with pytest.raises(ValueError, match="drifted"):
        archive_imports.execute_zip_member_materialization(facts)
    assert not list(temp.iterdir())

    _source, temp, facts = _facts(tmp_path / "race")
    foreign = temp / "queue-01.unitypackage"
    foreign.write_bytes(b"foreign")
    with pytest.raises(ValueError, match="appeared after approval"):
        archive_imports.execute_zip_member_materialization(facts)
    assert foreign.read_bytes() == b"foreign"


def test_ratio_and_tampered_selected_facts_are_rejected_before_output(tmp_path: Path) -> None:
    source = tmp_path / "ratio.zip"
    _zip(source, {"huge.unitypackage": b"0" * 200_000})
    temp = tmp_path / "temp"
    temp.mkdir()
    with pytest.raises(ValueError, match="compression ratio"):
        archive_imports.prepare_zip_member_materialization(
            source=source, temp_parent=temp,
            selected_members=[{"path": "huge.unitypackage", "targetName": "only.unitypackage"}],
        )

    _source, temp, facts = _facts(tmp_path / "tamper")
    facts["selected"][0]["compressionRatio"] = 0.0
    with pytest.raises(ValueError, match="matches the manifest"):
        archive_imports.execute_zip_member_materialization(facts)
    assert not list(temp.iterdir())


def test_second_member_failure_cleans_only_owned_first_output(monkeypatch, tmp_path: Path) -> None:
    _source, temp, facts = _facts(tmp_path)
    original_open = zipfile.ZipFile.open
    reads = 0

    def fail_second(self, name, mode="r", *args, **kwargs):
        nonlocal reads
        if mode == "r":
            reads += 1
            # Two manifest reads, then first materialization, then second.
            if reads == 4:
                raise OSError("second member read failed")
        return original_open(self, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", fail_second)
    with pytest.raises(OSError, match="second member read failed"):
        archive_imports.execute_zip_member_materialization(facts)
    assert not list(temp.iterdir())


def test_cleanup_refuses_foreign_directory_without_escaping(tmp_path: Path) -> None:
    _source, temp, facts = _facts(tmp_path)
    receipt = archive_imports.execute_zip_member_materialization(facts)
    foreign = temp / "queue-02.unitypackage"
    foreign.unlink()
    foreign.mkdir()

    error = archive_imports.cleanup_owned_zip_materialization(receipt)

    assert "refused an unverified node" in error
    assert foreign.is_dir()

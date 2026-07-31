from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "packaging" / "bridge_target_manifest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bridge_target_manifest", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


manifest_tool = _load_module()


def _write_tree(root: Path) -> None:
    (root / "runtime" / "data").mkdir(parents=True)
    (root / "runtime" / "data" / "z.bin").write_bytes(b"zeta")
    (root / "launcher.exe").write_bytes(b"launcher")
    (root / "runtime" / "a.json").write_text("{}", encoding="utf-8")


def _manifest_for_paths(*paths: str) -> dict:
    directories = sorted(
        {
            "/".join(path.split("/")[:index])
            for path in paths
            for index in range(1, len(path.split("/")))
            if "/".join(path.split("/")[:index])
        }
    )
    files = [
        {
            "path": path,
            "length": index + 1,
            "sha256": hashlib.sha256(path.encode("utf-8")).hexdigest(),
        }
        for index, path in enumerate(paths)
    ]
    byte_count = sum(item["length"] for item in files)
    return {
        "schema": manifest_tool.MANIFEST_SCHEMA,
        "algorithm": "sha256",
        "directoryCount": len(directories),
        "directories": directories,
        "entryCount": len(files),
        "byteCount": byte_count,
        "files": files,
        "treeDigest": manifest_tool.compute_tree_digest(files, directories),
    }


def test_build_is_deterministic_sorted_and_canonical(tmp_path: Path) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)

    first = manifest_tool.build_manifest(tree)
    second = manifest_tool.build_manifest(tree)

    assert first == second
    assert [item["path"] for item in first["files"]] == [
        "launcher.exe",
        "runtime/a.json",
        "runtime/data/z.bin",
    ]
    assert first["entryCount"] == 3
    assert first["directories"] == ["runtime", "runtime/data"]
    assert first["directoryCount"] == 2
    assert first["byteCount"] == len(b"launcher") + len(b"{}") + len(b"zeta")
    assert first["files"][0] == {
        "path": "launcher.exe",
        "length": len(b"launcher"),
        "sha256": hashlib.sha256(b"launcher").hexdigest(),
    }
    assert first["treeDigest"] == manifest_tool.compute_tree_digest(
        first["files"], first["directories"]
    )
    assert manifest_tool.canonical_json_bytes(first) == manifest_tool.canonical_json_bytes(
        json.loads(manifest_tool.canonical_json_bytes(first).decode("utf-8"))
    )
    assert b"\\\\" not in manifest_tool.canonical_json_bytes(first)


def test_write_and_default_cli_verify_are_read_only_and_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    manifest_path = tmp_path / "bridge-target-manifest.json"

    assert (
        manifest_tool.main(
            ["--tree", str(tree), "--manifest", str(manifest_path), "--build"]
        )
        == 0
    )
    build_output = capsys.readouterr().out
    original = manifest_path.read_bytes()
    original_stat = manifest_path.stat()

    assert manifest_tool.main(["--tree", str(tree), "--manifest", str(manifest_path)]) == 0
    verify_output = capsys.readouterr().out

    assert manifest_path.read_bytes() == original
    assert manifest_path.stat().st_mtime_ns == original_stat.st_mtime_ns
    for output in (build_output, verify_output):
        payload = json.loads(output)
        assert payload["ok"] is True
        assert payload["entryCount"] == 3
        assert str(tmp_path) not in output
        assert "tree" not in payload
        assert "manifest" not in payload


def test_manifest_must_stay_outside_hashed_tree(tmp_path: Path) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    inside = tree / "bridge-target-manifest.json"

    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="outside"):
        manifest_tool.write_manifest(tree, inside)

    outside = tmp_path / "bridge-target-manifest.json"
    manifest_tool.write_manifest(tree, outside)
    inside.write_bytes(outside.read_bytes())
    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="outside"):
        manifest_tool.verify_manifest(tree, inside)


@pytest.mark.parametrize(
    "change",
    ["missing", "missing_empty_directory", "extra", "extra_empty_directory", "tampered"],
)
def test_verify_rejects_tree_drift(tmp_path: Path, change: str) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    if change == "missing_empty_directory":
        (tree / "runtime" / "empty").mkdir()
    manifest_path = tmp_path / "bridge-target-manifest.json"
    manifest_tool.write_manifest(tree, manifest_path)

    if change == "missing":
        (tree / "runtime" / "a.json").unlink()
    elif change == "missing_empty_directory":
        (tree / "runtime" / "empty").rmdir()
    elif change == "extra":
        (tree / "runtime" / "extra.bin").write_bytes(b"extra")
    elif change == "extra_empty_directory":
        (tree / "runtime" / "empty").mkdir()
    else:
        (tree / "launcher.exe").write_bytes(b"changed")

    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="does not match"):
        manifest_tool.verify_manifest(tree, manifest_path)


@pytest.mark.parametrize(
    "path",
    [
        "/rooted.bin",
        "C:/rooted.bin",
        "//server/share.bin",
        ".",
        "../escape.bin",
        "folder/./leaf.bin",
        "folder/../leaf.bin",
        "folder\\leaf.bin",
        "CON",
        "folder/lpt1.txt",
        "folder/file.bin:stream",
        "folder/invalid?.bin",
        "folder/trailing. ",
    ],
)
def test_relative_path_contract_rejects_unsafe_or_noncanonical_paths(path: str) -> None:
    with pytest.raises(manifest_tool.BridgeTargetManifestError):
        manifest_tool.validate_manifest_document(_manifest_for_paths(path))


@pytest.mark.parametrize(
    "paths",
    [
        ("Runtime/Core.bin", "runtime/core.bin"),
        ("caf\u00e9.bin", "cafe\u0301.bin"),
    ],
)
def test_manifest_rejects_casefold_and_unicode_normalization_collisions(
    paths: tuple[str, str]
) -> None:
    with pytest.raises(
        manifest_tool.BridgeTargetManifestError, match="collision|normalization"
    ):
        manifest_tool.validate_manifest_document(_manifest_for_paths(*paths))


def test_build_rejects_link_or_reparse_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    unsafe = tree / "runtime" / "a.json"
    original = manifest_tool._is_link_or_reparse
    monkeypatch.setattr(
        manifest_tool,
        "_is_link_or_reparse",
        lambda path, metadata=None: Path(path) == unsafe or original(path, metadata),
    )

    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="link or reparse"):
        manifest_tool.build_manifest(tree)


def test_build_rejects_a_real_symlink_when_supported(tmp_path: Path) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    link = tree / "launcher-link.exe"
    try:
        link.symlink_to(tree / "launcher.exe")
    except OSError:
        pytest.skip("local symlink creation is unavailable")

    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="link or reparse"):
        manifest_tool.build_manifest(tree)


def test_build_rejects_hard_links(tmp_path: Path) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    os.link(tree / "launcher.exe", tree / "launcher-copy.exe")

    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="exactly one link"):
        manifest_tool.build_manifest(tree)


def test_build_rejects_non_regular_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    unsafe = tree / "launcher.exe"
    original = manifest_tool._lstat

    def fake_lstat(path: Path):
        value = original(path)
        if Path(path) != unsafe:
            return value
        return SimpleNamespace(
            st_mode=stat.S_IFIFO,
            st_file_attributes=0,
            st_nlink=1,
            st_dev=value.st_dev,
            st_ino=value.st_ino,
            st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns,
        )

    monkeypatch.setattr(manifest_tool, "_lstat", fake_lstat)
    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="regular file"):
        manifest_tool.build_manifest(tree)


def test_build_rejects_alternate_data_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    unsafe = tree / "launcher.exe"
    monkeypatch.setattr(
        manifest_tool,
        "_has_alternate_data_stream",
        lambda path: Path(path) == unsafe,
    )

    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="alternate data stream"):
        manifest_tool.build_manifest(tree)


def test_manifest_rejects_unsorted_duplicate_and_malformed_rows() -> None:
    unsorted = _manifest_for_paths("b.bin", "a.bin")
    duplicate = _manifest_for_paths("a.bin", "a.bin")
    malformed = _manifest_for_paths("a.bin")
    malformed["files"][0]["length"] = True

    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="sorted"):
        manifest_tool.validate_manifest_document(unsorted)
    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="collision"):
        manifest_tool.validate_manifest_document(duplicate)
    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="length"):
        manifest_tool.validate_manifest_document(malformed)


def test_manifest_reader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    manifest_path = tmp_path / "bridge-target-manifest.json"
    manifest_path.write_text(
        '{"schema":"a","schema":"b"}',
        encoding="utf-8",
    )

    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="duplicate"):
        manifest_tool.verify_manifest(tree, manifest_path)


def test_manifest_reader_requires_exact_canonical_json_bytes(tmp_path: Path) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    manifest_path = tmp_path / "bridge-target-manifest.json"
    document = manifest_tool.build_manifest(tree)
    manifest_path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="canonical"):
        manifest_tool.verify_manifest(tree, manifest_path)


def test_manifest_target_cannot_be_an_alternate_stream(tmp_path: Path) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    stream_target = tmp_path / "bridge-target-manifest.json:shadow"

    with pytest.raises(
        manifest_tool.BridgeTargetManifestError, match="alternate data stream"
    ):
        manifest_tool.write_manifest(tree, stream_target)


@pytest.mark.skipif(os.name != "nt", reason="named streams are Windows-specific")
def test_build_rejects_a_real_named_stream_when_supported(tmp_path: Path) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    launcher = tree / "launcher.exe"
    try:
        Path(f"{launcher}:probe").write_bytes(b"hidden")
    except OSError:
        pytest.skip("the temporary volume does not support named streams")

    with pytest.raises(manifest_tool.BridgeTargetManifestError, match="alternate data stream"):
        manifest_tool.build_manifest(tree)


def test_cli_failure_does_not_echo_absolute_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = tmp_path / "bridge"
    _write_tree(tree)
    manifest_path = tmp_path / "missing.json"

    assert manifest_tool.main(["--tree", str(tree), "--manifest", str(manifest_path)]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["ok"] is False
    assert str(tmp_path) not in captured.err

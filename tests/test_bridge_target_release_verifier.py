from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path
from typing import Callable

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_TOOL_PATH = REPO_ROOT / "packaging" / "bridge_target_manifest.py"
VERIFIER_PATH = REPO_ROOT / "packaging" / "verify_bridge_target_release.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


manifest_tool = _load_module("bridge_target_manifest_for_release_test", MANIFEST_TOOL_PATH)
release_verifier = _load_module("bridge_target_release_verifier", VERIFIER_PATH)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture(tmp_path: Path) -> dict[str, object]:
    tree = tmp_path / "bridge-tree"
    (tree / "_internal" / "empty").mkdir(parents=True)
    (tree / "vrcforge_bridge_target.exe").write_bytes(b"fixed-launcher")
    (tree / "_internal" / "main.py").write_bytes(b"fixed-connector")

    tree_manifest_path = tmp_path / "bridge-target-manifest.json"
    tree_document = manifest_tool.write_manifest(tree, tree_manifest_path)
    tree_manifest_bytes = tree_manifest_path.read_bytes()
    runtime_binding = {
        "schema": "vrcforge.bridge_target_runtime.v1",
        "runtimeRelativeRoot": "bridge_target",
        "executableRelativePath": "bridge_target/vrcforge_bridge_target.exe",
        "executableSha256": hashlib.sha256(b"fixed-launcher").hexdigest(),
        "manifestRelativePath": "bridge-target-manifest.json",
        "manifestSha256": hashlib.sha256(tree_manifest_bytes).hexdigest(),
        "treeDigest": tree_document["treeDigest"],
        "directoryCount": tree_document["directoryCount"],
        "entryCount": tree_document["entryCount"],
        "byteCount": tree_document["byteCount"],
        "candidatePayloadIncluded": True,
        "strictSourceBound": True,
        "verifiedAfterBuild": True,
    }
    payload_integrity = {
        "schema": "vrcforge.payload-integrity.v1",
        "version": "1.4.0",
        "files": {},
        "bridgeTargetRuntime": runtime_binding,
    }

    payload_zip = tmp_path / "VRCForge_Windows_x64_1.4.0.zip"
    with zipfile.ZipFile(payload_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("VERSION", b"1.4.0\n")
        archive.writestr("bridge_target/", b"")
        for relative_directory in tree_document["directories"]:
            archive.writestr(f"bridge_target/{relative_directory}/", b"")
        for row in tree_document["files"]:
            archive.write(tree / row["path"], f"bridge_target/{row['path']}")
        archive.writestr("bridge-target-manifest.json", tree_manifest_bytes)
        archive.writestr("payload-integrity.json", _canonical_json(payload_integrity))

    release_manifest_path = tmp_path / "release-manifest.json"
    release_document = {
        "version": "1.4.0",
        "commit": "a" * 40,
        "buildPolicy": {
            "mode": "strict",
            "releaseEligible": True,
            "allowDirty": False,
            "allowUnpushed": False,
            "allowVersionMismatch": False,
        },
        "artifacts": [{"name": payload_zip.name, "sha256": _sha256(payload_zip)}],
        "bridgeTargetRuntime": runtime_binding,
    }
    release_manifest_path.write_bytes(_canonical_json(release_document))
    return {
        "treeDocument": tree_document,
        "runtimeBinding": runtime_binding,
        "payloadZip": payload_zip,
        "releaseManifest": release_manifest_path,
    }


def _rewrite_archive(
    path: Path,
    transform: Callable[[list[tuple[zipfile.ZipInfo, bytes]]], list[tuple[zipfile.ZipInfo, bytes]]],
) -> None:
    with zipfile.ZipFile(path, "r") as source:
        entries = [(item, source.read(item)) for item in source.infolist()]
    rewritten = transform(entries)
    temporary = path.with_suffix(".rewrite.zip")
    with zipfile.ZipFile(temporary, "w") as destination:
        for info, content in rewritten:
            destination.writestr(info, content)
    os.replace(temporary, path)


def _replace_member(path: Path, member_name: str, content: bytes) -> None:
    def transform(entries: list[tuple[zipfile.ZipInfo, bytes]]):
        return [
            (info, content if info.filename == member_name else current)
            for info, current in entries
        ]

    _rewrite_archive(path, transform)


def _drop_member(path: Path, member_name: str) -> None:
    _rewrite_archive(
        path,
        lambda entries: [item for item in entries if item[0].filename != member_name],
    )


def _append_member(path: Path, info: zipfile.ZipInfo | str, content: bytes = b"") -> None:
    archive_info = zipfile.ZipInfo(info) if isinstance(info, str) else info
    _rewrite_archive(path, lambda entries: [*entries, (archive_info, content)])


def _read_json_member(path: Path, member_name: str) -> dict[str, object]:
    with zipfile.ZipFile(path, "r") as archive:
        return json.loads(archive.read(member_name).decode("utf-8"))


def _rebind_outer_zip_hash(fixture: dict[str, object]) -> None:
    payload_zip = fixture["payloadZip"]
    release_path = fixture["releaseManifest"]
    assert isinstance(payload_zip, Path) and isinstance(release_path, Path)
    release_document = json.loads(release_path.read_text(encoding="utf-8"))
    release_document["artifacts"][0]["sha256"] = _sha256(payload_zip)
    release_path.write_bytes(_canonical_json(release_document))


def _verify(fixture: dict[str, object]) -> dict[str, object]:
    release_path = fixture["releaseManifest"]
    payload_zip = fixture["payloadZip"]
    assert isinstance(release_path, Path) and isinstance(payload_zip, Path)
    return release_verifier.verify_release_bridge_target(release_path, payload_zip)


def test_verifier_recomputes_archive_tree_and_is_read_only(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    release_path = fixture["releaseManifest"]
    payload_zip = fixture["payloadZip"]
    tree_document = fixture["treeDocument"]
    runtime_binding = fixture["runtimeBinding"]
    assert isinstance(release_path, Path) and isinstance(payload_zip, Path)
    assert isinstance(tree_document, dict) and isinstance(runtime_binding, dict)
    before_release = release_path.stat()
    before_zip = payload_zip.stat()

    receipt = _verify(fixture)

    assert receipt == {
        "ok": True,
        "schema": "vrcforge.bridge_target_release_verification.v1",
        "payloadSha256": _sha256(payload_zip),
        "manifestSha256": runtime_binding["manifestSha256"],
        "executableSha256": runtime_binding["executableSha256"],
        "treeDigest": tree_document["treeDigest"],
        "directoryCount": tree_document["directoryCount"],
        "entryCount": tree_document["entryCount"],
        "byteCount": tree_document["byteCount"],
        "verifiedFromArchive": True,
    }
    assert release_path.stat().st_mtime_ns == before_release.st_mtime_ns
    assert payload_zip.stat().st_mtime_ns == before_zip.st_mtime_ns


def test_cli_success_returns_only_archive_derived_summary(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    release_path = fixture["releaseManifest"]
    payload_zip = fixture["payloadZip"]
    assert isinstance(release_path, Path) and isinstance(payload_zip, Path)

    completed = subprocess.run(
        [
            sys.executable,
            str(VERIFIER_PATH),
            "--release-manifest",
            str(release_path),
            "--payload-zip",
            str(payload_zip),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["ok"] is True
    assert receipt["verifiedFromArchive"] is True
    assert str(tmp_path) not in completed.stdout


def test_verifier_accepts_strict_evidence_readback_but_rejects_relaxed_builds(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    release_path = fixture["releaseManifest"]
    assert isinstance(release_path, Path)
    document = json.loads(release_path.read_text(encoding="utf-8"))
    document["buildPolicy"] = {
        "mode": "strict-evidence",
        "releaseEligible": False,
        "evidenceEligible": True,
        "allowDirty": False,
        "allowUnpushed": False,
        "allowVersionMismatch": False,
    }
    release_path.write_bytes(_canonical_json(document))
    assert _verify(fixture)["verifiedFromArchive"] is True

    document["buildPolicy"]["allowDirty"] = True
    release_path.write_bytes(_canonical_json(document))
    with pytest.raises(
        release_verifier.BridgeTargetReleaseVerificationError,
        match="strict",
    ):
        _verify(fixture)


@pytest.mark.parametrize(
    "mutation",
    ["tampered_file", "missing_file", "extra_file", "missing_empty_dir", "extra_empty_dir"],
)
def test_verifier_rejects_archive_tree_drift_even_when_outer_hash_is_rebound(
    tmp_path: Path, mutation: str
) -> None:
    fixture = _write_fixture(tmp_path)
    payload_zip = fixture["payloadZip"]
    assert isinstance(payload_zip, Path)
    if mutation == "tampered_file":
        _replace_member(
            payload_zip,
            "bridge_target/vrcforge_bridge_target.exe",
            b"tampered-launcher",
        )
    elif mutation == "missing_file":
        _drop_member(payload_zip, "bridge_target/_internal/main.py")
    elif mutation == "extra_file":
        _append_member(payload_zip, "bridge_target/_internal/extra.bin", b"extra")
    elif mutation == "missing_empty_dir":
        _drop_member(payload_zip, "bridge_target/_internal/empty/")
    else:
        _append_member(payload_zip, "bridge_target/_internal/other-empty/", b"")
    _rebind_outer_zip_hash(fixture)

    with pytest.raises(
        release_verifier.BridgeTargetReleaseVerificationError,
        match="tree|manifest",
    ):
        _verify(fixture)


@pytest.mark.parametrize("binding_location", ["release", "payload"])
def test_verifier_cross_binds_release_and_payload_runtime_records(
    tmp_path: Path, binding_location: str
) -> None:
    fixture = _write_fixture(tmp_path)
    release_path = fixture["releaseManifest"]
    payload_zip = fixture["payloadZip"]
    assert isinstance(release_path, Path) and isinstance(payload_zip, Path)

    if binding_location == "release":
        document = json.loads(release_path.read_text(encoding="utf-8"))
        document["bridgeTargetRuntime"]["treeDigest"] = "0" * 64
        release_path.write_bytes(_canonical_json(document))
    else:
        document = _read_json_member(payload_zip, "payload-integrity.json")
        document["bridgeTargetRuntime"]["treeDigest"] = "0" * 64
        _replace_member(payload_zip, "payload-integrity.json", _canonical_json(document))
        _rebind_outer_zip_hash(fixture)

    with pytest.raises(
        release_verifier.BridgeTargetReleaseVerificationError,
        match="binding|digest",
    ):
        _verify(fixture)


def test_verifier_recomputes_the_executable_digest_from_archive_bytes(
    tmp_path: Path,
) -> None:
    fixture = _write_fixture(tmp_path)
    release_path = fixture["releaseManifest"]
    payload_zip = fixture["payloadZip"]
    assert isinstance(release_path, Path) and isinstance(payload_zip, Path)

    wrong_digest = "1" * 64
    release_document = json.loads(release_path.read_text(encoding="utf-8"))
    release_document["bridgeTargetRuntime"]["executableSha256"] = wrong_digest
    release_path.write_bytes(_canonical_json(release_document))
    payload_document = _read_json_member(payload_zip, "payload-integrity.json")
    payload_document["bridgeTargetRuntime"]["executableSha256"] = wrong_digest
    _replace_member(payload_zip, "payload-integrity.json", _canonical_json(payload_document))
    _rebind_outer_zip_hash(fixture)

    with pytest.raises(
        release_verifier.BridgeTargetReleaseVerificationError,
        match="executable digest",
    ):
        _verify(fixture)


@pytest.mark.parametrize("member_name", ["../escape.bin", "bridge_target/../escape.bin"])
def test_verifier_rejects_unsafe_archive_member_names(
    tmp_path: Path, member_name: str
) -> None:
    fixture = _write_fixture(tmp_path)
    payload_zip = fixture["payloadZip"]
    assert isinstance(payload_zip, Path)
    _append_member(payload_zip, member_name, b"unsafe")
    _rebind_outer_zip_hash(fixture)

    with pytest.raises(
        release_verifier.BridgeTargetReleaseVerificationError,
        match="archive member",
    ):
        _verify(fixture)


def test_verifier_rejects_duplicate_and_casefold_colliding_members(tmp_path: Path) -> None:
    for index, member_name in enumerate((
        "bridge_target/_internal/main.py",
        "bridge_target/_INTERNAL/main.py",
    )):
        fixture = _write_fixture(tmp_path / str(index))
        payload_zip = fixture["payloadZip"]
        assert isinstance(payload_zip, Path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            _append_member(payload_zip, member_name, b"collision")
        _rebind_outer_zip_hash(fixture)
        with pytest.raises(
            release_verifier.BridgeTargetReleaseVerificationError,
            match="collision|duplicate",
        ):
            _verify(fixture)


def test_verifier_rejects_symlink_members(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    payload_zip = fixture["payloadZip"]
    assert isinstance(payload_zip, Path)
    link = zipfile.ZipInfo("bridge_target/_internal/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    _append_member(payload_zip, link, b"main.py")
    _rebind_outer_zip_hash(fixture)

    with pytest.raises(
        release_verifier.BridgeTargetReleaseVerificationError,
        match="link|regular",
    ):
        _verify(fixture)


def test_verifier_rejects_data_hidden_in_directory_members(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    payload_zip = fixture["payloadZip"]
    assert isinstance(payload_zip, Path)
    _replace_member(payload_zip, "bridge_target/_internal/empty/", b"hidden")
    _rebind_outer_zip_hash(fixture)

    with pytest.raises(
        release_verifier.BridgeTargetReleaseVerificationError,
        match="directory member",
    ):
        _verify(fixture)


def test_verifier_rejects_outer_payload_digest_mismatch(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    payload_zip = fixture["payloadZip"]
    assert isinstance(payload_zip, Path)
    _append_member(payload_zip, "unrelated.bin", b"changes-outer-digest")

    with pytest.raises(
        release_verifier.BridgeTargetReleaseVerificationError,
        match="payload digest",
    ):
        _verify(fixture)


def test_verifier_rejects_noncanonical_inner_tree_manifest(tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path)
    payload_zip = fixture["payloadZip"]
    tree_document = fixture["treeDocument"]
    assert isinstance(payload_zip, Path) and isinstance(tree_document, dict)
    _replace_member(
        payload_zip,
        "bridge-target-manifest.json",
        json.dumps(tree_document, indent=2).encode("utf-8"),
    )
    _rebind_outer_zip_hash(fixture)

    with pytest.raises(
        release_verifier.BridgeTargetReleaseVerificationError,
        match="manifest",
    ):
        _verify(fixture)


def test_cli_failure_is_redacted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing_manifest = tmp_path / "private" / "release-manifest.json"
    missing_zip = tmp_path / "private" / "payload.zip"
    assert (
        release_verifier.main(
            [
                "--release-manifest",
                str(missing_manifest),
                "--payload-zip",
                str(missing_zip),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["ok"] is False
    assert str(tmp_path) not in captured.err

from __future__ import annotations

import importlib.util
import json
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile

from compression import zstd
import pytest


def load_packager():
    path = Path(__file__).resolve().parents[1] / "setup" / "build_payload.py"
    spec = importlib.util.spec_from_file_location("setup_payload_packager", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def application(tmp_path: Path) -> tuple[Path, Path]:
    app = tmp_path / "app"
    (app / "backend").mkdir(parents=True)
    (app / "VRCForge.exe").write_bytes(b"application fixture")
    (app / "backend" / "vrcforge_backend.exe").write_bytes(b"backend fixture")
    native = tmp_path / "setup.exe"
    native.write_bytes(b"native executable fixture")
    return app, native


def test_offline_and_web_share_the_same_installable_archive(tmp_path: Path):
    package = load_packager()
    app, native = application(tmp_path)
    offline, web, payload = (tmp_path / name for name in ("offline.exe", "web.exe", "payload.tar.zst"))
    url = "https://example.test/VRCForge.tar.zst"
    result = package.build(app, native, offline, payload_output=payload, web_output=web, web_url=url)

    footer_size = struct.calcsize("<QQ") + len(package.MAGIC)
    assert offline.read_bytes()[native.stat().st_size : -footer_size] == payload.read_bytes()
    with zstd.ZstdFile(payload, "rb") as compressed, tarfile.open(fileobj=compressed, mode="r|") as archive:
        actual = {entry.name: archive.extractfile(entry).read() for entry in archive if entry.isfile()}
    assert actual == {"VRCForge.exe": b"application fixture", "backend/vrcforge_backend.exe": b"backend fixture"}
    web_bytes = web.read_bytes()
    metadata_size, app_bytes = struct.unpack("<QQ", web_bytes[-footer_size : -len(package.REMOTE_MAGIC)])
    metadata = json.loads(web_bytes[-footer_size - metadata_size : -footer_size])
    assert metadata == {"url": url, "payloadBytes": payload.stat().st_size, "appBytes": result["appBytes"]}
    assert app_bytes == result["appBytes"]
    assert package.executable_size(offline) == package.executable_size(web) == native.stat().st_size


def test_failed_packaging_preserves_preexisting_output(tmp_path: Path):
    package = load_packager()
    app, native = application(tmp_path)
    output = tmp_path / "offline.exe"
    output.write_bytes(b"previous build")
    with pytest.raises(FileExistsError):
        package.build(app, native, output)
    assert output.read_bytes() == b"previous build"


@pytest.mark.parametrize("change", [None, "url", "length", "digest"])
def test_publisher_accepts_only_the_matching_versioned_web_payload(tmp_path: Path, change):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required to execute the Windows publisher's input checks")
    root = Path(__file__).resolve().parents[1]
    source = (root / "packaging" / "publish_release.ps1").read_text(encoding="utf-8-sig")
    # Execute the production input checks only. Do not run the publisher, stage
    # uploads, access GitHub, or duplicate its validation predicates in this test.
    helper = source[source.index("function Get-RequiredProperty {") : source.index("function Get-StreamSha256 {")]
    web_check = source[source.index('    $webPayload = Get-RequiredProperty') : source.index('    $stagingRoot = ', source.index('    $webPayload = Get-RequiredProperty'))]
    entry_start = source.index('    $payloadManifestEntry = ')
    entry_check = source[entry_start : source.index('    foreach ($artifact in $artifacts', entry_start)]
    payload = tmp_path / "VRCForge_Windows_x64_1.10.3.tar.zst"
    payload.write_bytes(b"release payload fixture")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    manifest = {"webPayload": {
        "downloadUrl": "https://github.com/ayyitong888/VRCForge/releases/download/v1.10.3/VRCForge_Windows_x64_1.10.3.tar.zst",
        "format": "tar.zst", "length": payload.stat().st_size, "sha256": digest},
        "artifacts": [{"name": payload.name, "sha256": digest}]}
    if change == "url":
        manifest["webPayload"]["downloadUrl"] = "https://example.test/another-release.tar.zst"
    elif change == "length":
        manifest["webPayload"]["length"] += 1
    elif change == "digest":
        manifest["webPayload"]["sha256"] = "0" * 64
    metadata = tmp_path / "manifest.json"
    metadata.write_text(json.dumps(manifest), encoding="utf-8")
    script = tmp_path / "publisher-input-check.ps1"
    script.write_text("param($Metadata, $Payload)\n$ErrorActionPreference='Stop'\n"
                      "$Version='1.10.3'\n$webPayloadArchive=$Payload\n"
                      "$manifest=Get-Content -LiteralPath $Metadata -Raw | ConvertFrom-Json\n"
                      "$manifestArtifacts=@($manifest.artifacts)\n" + helper + web_check + entry_check,
                      encoding="utf-8-sig")
    result = subprocess.run([powershell, "-NoProfile", "-File", str(script), str(metadata), str(payload)],
                            capture_output=True)
    assert (result.returncode == 0) == (change is None), result.stdout + result.stderr

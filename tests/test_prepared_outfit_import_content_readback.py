import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from prepared_file_imports import capture_regular_file
from prepared_outfit_import_workflow_service import verify_unitypackage_asset_content


def _package(path: Path, asset_path: str, content: bytes) -> None:
    with tarfile.open(path, "w") as archive:
        for name, data in (("guid/pathname", asset_path.encode()), ("guid/asset", content)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_import_content_readback_compares_package_asset_and_persisted_target(tmp_path: Path):
    package = tmp_path / "candidate.unitypackage"
    project = tmp_path / "Project"
    target = project / "Assets" / "Shaders" / "candidate.hlsl"
    target.parent.mkdir(parents=True)
    content = b"candidate shader\n"
    target.write_bytes(content)
    _package(package, "Assets/Shaders/candidate.hlsl", content)

    result = verify_unitypackage_asset_content(
        package, project, ["Assets/Shaders/candidate.hlsl"],
        lambda path, label: capture_regular_file(path, label=label),
    )

    assert result["verified"] is True
    assert result["checked"] == 1
    assert result["assets"][0]["packageSha256"] == hashlib.sha256(content).hexdigest()
    assert result["assets"][0]["targetSha256"] == result["assets"][0]["packageSha256"]


def test_import_content_readback_fails_closed_on_target_content_drift(tmp_path: Path):
    package = tmp_path / "candidate.unitypackage"
    project = tmp_path / "Project"
    target = project / "Assets" / "Shaders" / "candidate.hlsl"
    target.parent.mkdir(parents=True)
    _package(package, "Assets/Shaders/candidate.hlsl", b"package content\n")
    target.write_bytes(b"old content\n")

    with pytest.raises(RuntimeError, match="content readback mismatch"):
        verify_unitypackage_asset_content(
            package, project, ["Assets/Shaders/candidate.hlsl"],
            lambda path, label: capture_regular_file(path, label=label),
        )


def test_import_content_readback_accepts_folder_asset_meta_without_asset_payload(tmp_path: Path):
    package = tmp_path / "folder.unitypackage"
    project = tmp_path / "Project"
    (project / "Assets" / "Outfits" / "Folder").mkdir(parents=True)
    with tarfile.open(package, "w") as archive:
        for name, data in (("folder/pathname", b"Assets/Outfits/Folder"), ("folder/asset.meta", b"fileFormatVersion: 2\nfolderAsset: yes\n")):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    result = verify_unitypackage_asset_content(
        package, project, ["Assets/Outfits/Folder"],
        lambda path, label: capture_regular_file(path, label=label),
        lambda path, label: {"path": str(path)},
    )
    assert result["verified"] is True
    assert result["assets"] == [{"assetPath": "Assets/Outfits/Folder", "kind": "folder"}]

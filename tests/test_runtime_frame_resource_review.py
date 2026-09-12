"""Independent real PNG / resource-registry checks; no Unity or network calls."""
import base64
import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from mcp_resource_registry import McpResourceRegistry, McpResourceError
from runtime_frame_resources import publish_verified_frames


def fixture(tmp_path):
    root = tmp_path / "runtime-observations"
    job = "1" * 32
    directory = root / job
    directory.mkdir(parents=True)
    frames = []
    for index in range(2):
        path = directory / f"{index:03d}.png"
        Image.new("RGB", (128, 128), (12 + index, 34, 56)).save(path)
        frames.append({"imagePath": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "sampleIndex": -1 if index == 0 else 1, "unityFrame": 10 + index, "actualElapsedSeconds": index * .5})
    receipt = {"schema": "vrcforge.runtime_observation.v1", "status": "completed", "verified": True, "readback": {},
        "jobId": job, "avatarPath": "Root/ActualAvatar", "coreIdentity": "core-123", "width": 128, "height": 128, "frames": frames}
    target = {"editor": {"unityPid": 123}, "avatar": {"exactHierarchyPath": "Root/ActualAvatar"}}
    registry = McpResourceRegistry(tmp_path / "resources")
    return root, receipt, target, registry


def test_png_resource_bytes_pixels_and_recorded_identity_survive_reopen(tmp_path):
    root, receipt, target, registry = fixture(tmp_path)
    frames = publish_verified_frames(registry, receipt, root, target)
    reopened = McpResourceRegistry(tmp_path / "resources")
    result = reopened.read(frames[1]["resourceUri"])
    content = result["contents"][0]
    decoded = base64.b64decode(content["blob"], validate=True)
    assert decoded == Path(receipt["frames"][1]["imagePath"]).read_bytes()
    assert hashlib.sha256(decoded).hexdigest() == content["_meta"]["sha256"]
    assert content["mimeType"] == "image/png" and content["_meta"]["historical"] is True
    assert content["_meta"]["identity"]["executionTarget"] == target
    assert content["_meta"]["identity"]["avatarPath"] == "Root/ActualAvatar"
    with Image.open(io.BytesIO(decoded)) as image:
        assert image.size == (128, 128) and image.getpixel((0, 0)) == (13, 34, 56)


def test_replaced_png_is_rejected_before_blob_is_returned(tmp_path):
    root, receipt, target, registry = fixture(tmp_path)
    frames = publish_verified_frames(registry, receipt, root, target)
    Image.new("RGB", (128, 128), (255, 0, 0)).save(receipt["frames"][1]["imagePath"])
    with pytest.raises(ValueError, match="drifted"):
        registry.read(frames[1]["resourceUri"])


def test_outside_final_frame_rejects_whole_publication(tmp_path):
    root, receipt, target, registry = fixture(tmp_path)
    outside = tmp_path / "outside.png"
    Image.new("RGB", (128, 128), (9, 9, 9)).save(outside)
    receipt["frames"][1].update(imagePath=str(outside), sha256=hashlib.sha256(outside.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="escaped"):
        publish_verified_frames(registry, receipt, root, target)
    assert registry.list()["resources"] == []


def test_unknown_job_uri_cannot_select_another_captured_job(tmp_path):
    root, receipt, target, registry = fixture(tmp_path)
    frames = publish_verified_frames(registry, receipt, root, target)
    unknown = frames[0]["resourceUri"].replace("1" * 32, "2" * 32)
    with pytest.raises(McpResourceError, match="not previously captured"):
        registry.read(unknown)


def test_source_size_growth_is_bounded_before_read(tmp_path):
    root, receipt, target, registry = fixture(tmp_path)
    frames = publish_verified_frames(registry, receipt, root, target)
    Path(receipt["frames"][0]["imagePath"]).write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="bound"):
        registry.read(frames[0]["resourceUri"])

"""PNG resources for explicitly published, verified runtime observation frames only."""
from __future__ import annotations
import base64
import hashlib
import io
import os
import re
import stat
from pathlib import Path
from PIL import Image

RESOURCE_TYPE = "runtime_observation_frame"
MAX_FRAME_BYTES = 1024 * 1024


def _checked_bytes(data):
    if data.get("mimeType") != "image/png" or not re.fullmatch(r"[0-9a-f]{32}", str(data.get("jobId", ""))):
        raise ValueError("Invalid published frame identity.")
    index = data.get("fileIndex")
    if type(index) is not int or not 0 <= index <= 32:
        raise ValueError("Invalid published frame index.")
    root = Path(data["artifactRoot"])
    expected = root / data["jobId"] / f"{index:03d}.png"
    path = Path(data["imagePath"])
    if not root.is_absolute() or path != expected or path.resolve() != expected.absolute():
        raise ValueError("Published frame path escaped its managed job directory.")
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise ValueError("Published frame path contains a link.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("sha256", ""))):
        raise ValueError("Published frame hash missing.")
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        if before.st_size <= 0 or before.st_size > MAX_FRAME_BYTES:
            raise ValueError("Published frame exceeds the single-frame resource bound.")
        content = source.read(MAX_FRAME_BYTES + 1)
        after = os.fstat(source.fileno())
    current = path.stat()
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if identity(before) != identity(after) or identity(after) != identity(current) or hashlib.sha256(content).hexdigest() != data["sha256"]:
        raise ValueError("Published frame content or file identity drifted.")
    with Image.open(io.BytesIO(content)) as image:
        if image.format != "PNG" or image.size != (data["width"], data["height"]):
            raise ValueError("Published frame PNG dimensions differ.")
        image.verify()
    return content


def publish_verified_frames(registry, receipt, artifact_root, execution_target):
    if receipt.get("schema") != "vrcforge.runtime_observation.v1" or receipt.get("status") != "completed" or receipt.get("verified") is not True or not isinstance(receipt.get("readback"), dict):
        raise ValueError("Only independently verified observation receipts may publish frame resources.")
    frames = receipt.get("frames")
    if not isinstance(frames, list) or not 2 <= len(frames) <= 33:
        raise ValueError("Verified observation frames missing or unbounded.")
    identity = {"executionTarget": execution_target or {}, "avatarPath": receipt["avatarPath"], "coreIdentity": receipt.get("coreIdentity"), "jobId": receipt["jobId"]}
    sealed = []
    for index, frame in enumerate(frames):
        data = {"jobId": receipt["jobId"], "fileIndex": index, "artifactRoot": str(Path(artifact_root).resolve()),
            "imagePath": frame["imagePath"], "sha256": frame["sha256"], "mimeType": "image/png", "width": receipt["width"], "height": receipt["height"],
            "sampleIndex": frame["sampleIndex"], "unityFrame": frame["unityFrame"], "actualElapsedSeconds": frame["actualElapsedSeconds"]}
        if "rendererProbes" in frame:
            data["rendererProbes"] = frame["rendererProbes"]
        _checked_bytes(data)  # Preflight every referenced file before publishing any frame.
        sealed.append(data)
    entries = [dict(base_uri=f"vrcforge://runtime-observation/{receipt['jobId']}/frames/{data['fileIndex']}",
        name=f"Runtime observation {receipt['jobId']} frame {data['fileIndex']}", resource_type=RESOURCE_TYPE, data=data,
        identity=identity, source_mode="captured_runtime_frame", refresh_rule="Historical captured frame; start a new observation for new evidence. Reading never mutates Unity.",
        description="One verified PNG frame from the recorded Avatar/Core identity; historical evidence, not a live view.")
        for data in sealed]
    records = registry.publish_many(entries)
    return [{**frame, "resourceUri": record["uri"], "mimeType": "image/png"} for frame, record in zip(frames, records)]


def read_published_frame(envelope):
    data = envelope["data"]
    content = _checked_bytes(data)
    return {"contents": [{"uri": envelope["uri"], "mimeType": "image/png", "blob": base64.b64encode(content).decode("ascii"),
        "_meta": {"identity": envelope["identity"], "sha256": data["sha256"], "capturedAt": envelope["capturedAt"], "stale": envelope["stale"], "historical": True,
            **({"rendererProbes": data["rendererProbes"]} if "rendererProbes" in data else {})}}]}

import hashlib
import json
from pathlib import Path

from PIL import Image

from mcp_resource_registry import McpResourceRegistry
from runtime_frame_resources import publish_verified_frames
from runtime_observation import prepare_request, validate_result


def _receipt(tmp_path):
    request = prepare_request({
        "avatarPath": "Root/Avatar", "parameterName": "Wardrobe", "value": 5,
        "durationSeconds": 1.0, "frameCount": 2, "width": 128, "height": 128,
        "cameraPosition": {"x": 0, "y": 1, "z": 3},
        "targetPosition": {"x": 0, "y": 1, "z": 0},
        "upVector": {"x": 0, "y": 1, "z": 0}, "fieldOfView": 40,
        "rendererProbes": [{
            "rendererPath": "Root/Avatar/Body", "materialIndex": 0,
            "propertyNames": ["_DissolveParams"],
            "blendShapeNames": ["Smile", "Blink"],
        }],
    }, tmp_path / "artifacts")
    root = Path(request["outputDirectory"])
    root.mkdir(parents=True)
    frames = []
    weights = ((0.0, 10.0), (25.0, 40.0), (75.0, 80.0))
    for index, sample in enumerate((-1, 0, 1)):
        path = root / f"{index:03d}.png"
        Image.new("RGB", (128, 128), color=(index, 0, 0)).save(path)
        probes = [{
            "rendererPath": "Root/Avatar/Body", "materialIndex": 0,
            "rendererComponentIndex": 0,
            "properties": [{"name": "_DissolveParams", "valueSource": "material", "value": [index, 0, 0, 0]}],
            "blendShapes": [
                {"name": "Smile", "index": 3, "weight": weights[index][0]},
                {"name": "Blink", "index": 7, "weight": weights[index][1]},
            ],
        }]
        frames.append({"sampleIndex": sample, "unityFrame": index + 1,
                       "actualElapsedSeconds": index * .25, "states": [{"stateHash": 123 + index}],
                       "rendererProbes": probes, "imagePath": str(path),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    receipt = {"schema": "vrcforge.runtime_observation.v1", "jobId": request["jobId"],
               "avatarPath": request["avatarPath"], "parameterName": request["parameterName"],
               "requestedValue": request["value"], "status": "completed", "verified": True,
               "width": request["width"], "height": request["height"],
               "requestedFrameCount": 2, "sampledFrameCount": 2, "undersampled": False,
               "frames": frames, "rendererProbes": request["rendererProbes"]}
    return request, validate_result(receipt, request)


def test_blendshape_evidence_survives_verified_resource_persistence_and_pagination(tmp_path):
    request, receipt = _receipt(tmp_path)
    registry = McpResourceRegistry(tmp_path / "registry")
    frames = publish_verified_frames(registry, receipt, tmp_path / "artifacts" / "runtime-observations",
                                     {"project": {"root": "source-project"}})
    # Reopen the same on-disk store to prove persistence across registry instances.
    registry = McpResourceRegistry(tmp_path / "registry")

    expected = [[0.0, 10.0], [25.0, 40.0], [75.0, 80.0]]
    page = registry.list(page_size=1)
    seen = []
    while True:
        for resource in page["resources"]:
            seen.append((resource["uri"], registry.read(resource["uri"])["contents"][0]["_meta"]))
        if "nextCursor" not in page:
            break
        page = registry.list(cursor=page["nextCursor"], page_size=1)

    assert len(seen) == 3
    # Resource reads preserve the frame's probe payload; verify order through the
    # published frame URI/file index, rather than relying on pagination order.
    readbacks = []
    for frame in frames:
        meta = registry.read(frame["resourceUri"])["contents"][0]["_meta"]
        probe = meta["rendererProbes"][0]
        readbacks.append([(shape["name"], shape["index"], shape["weight"]) for shape in probe["blendShapes"]])
    assert readbacks == [[("Smile", 3, expected[i][0]), ("Blink", 7, expected[i][1])] for i in range(3)]
    assert all(meta["historical"] is True for _, meta in seen)
    for uri, meta in seen:
        index = int(uri.rsplit("/", 1)[1].split("?", 1)[0])
        probe = meta["rendererProbes"][0]
        assert {key: probe[key] for key in ("rendererPath", "materialIndex", "rendererComponentIndex")} == {
            key: request["rendererProbes"][0].get(key, 0) for key in ("rendererPath", "materialIndex", "rendererComponentIndex")
        }
        assert [shape["weight"] for shape in probe["blendShapes"]] == expected[index]

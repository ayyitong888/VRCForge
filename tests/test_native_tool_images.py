import base64
import hashlib
import io
import json

from PIL import Image

from native_request_composition import attach_native_request_composition
from native_tool_images import bind_tool_images, collect_tool_images


def _png(size=(8, 6), color=(24, 80, 160, 255)):
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


def _image_content(png, *, uri=""):
    image = {
        "type": "image",
        "data": base64.b64encode(png).decode("ascii"),
        "mimeType": "image/png",
    }
    if uri:
        image["uri"] = uri
    return image


def _successful_step(tool, result, *, action_id="action-1"):
    return {
        "tool": tool,
        "status": "succeeded",
        "actionId": action_id,
        "outcome": {"status": "succeeded"},
        "result": result,
    }


def test_retains_distinct_recent_mcp_images_as_typed_provider_content():
    older = _png(color=(0, 0, 0, 255))
    latest = _png(color=(255, 0, 0, 255))
    steps = [
        _successful_step("capture_old", {"content": [_image_content(older)]}, action_id="old"),
        _successful_step("capture_latest", {"content": [_image_content(latest)]}, action_id="latest"),
    ]

    content = collect_tool_images(steps, resource_reader=None, project_path="D:/Project")

    assert len(content) == 4
    assert "capture_old" in content[0]["text"]
    assert content[1]["image_url"]["url"].endswith(base64.b64encode(older).decode("ascii"))
    assert content[2]["type"] == "text"
    assert "capture_latest" in content[2]["text"]
    assert "latest" in content[2]["text"]
    digest = hashlib.sha256(latest).hexdigest()
    assert digest in content[2]["text"]
    assert content[3] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64," + base64.b64encode(latest).decode("ascii")},
    }
    assert base64.b64encode(latest).decode("ascii") not in content[0]["text"]


def test_reads_only_exact_texture_preview_descriptor_through_injected_reader():
    png = _png()
    source_sha = "a" * 64
    image_sha = hashlib.sha256(png).hexdigest()
    preview = {
        "schema": "vrcforge.texture_asset_preview.v1",
        "assetPath": "Assets/Outfits/dress.png",
        "assetGuid": "b" * 32,
        "sourceSha256": source_sha,
        "sha256": image_sha,
        "mimeType": "image/png",
        "width": 8,
        "height": 6,
        "resourceUri": "vrcforge://texture-preview/item?revision=1&store=abc",
    }
    calls = []

    def read_resource(received_preview, project_path):
        calls.append((received_preview, project_path))
        return {"contents": [{
            "uri": preview["resourceUri"],
            "mimeType": "image/png",
            "blob": base64.b64encode(png).decode("ascii"),
            "_meta": {"sha256": image_sha, "sourceSha256": source_sha},
        }]}

    content = collect_tool_images(
        [_successful_step("vrcforge_get_asset_info", {"preview": preview})],
        resource_reader=read_resource,
        project_path="D:/Project",
    )

    assert len(calls) == 1
    assert calls[0] == (preview, "D:/Project")
    assert content[0]["type"] == "text"
    assert preview["resourceUri"] in content[0]["text"]
    assert preview["sha256"] in content[0]["text"]
    assert content[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def test_preview_resource_failure_returns_text_only_unavailable_observation():
    preview = {
        "schema": "vrcforge.texture_asset_preview.v1",
        "assetPath": "Assets/Outfits/dress.png",
        "assetGuid": "b" * 32,
        "sourceSha256": "a" * 64,
        "sha256": "c" * 64,
        "mimeType": "image/png",
        "width": 8,
        "height": 6,
        "resourceUri": "vrcforge://texture-preview/item?revision=1&store=abc",
    }

    content = collect_tool_images(
        [_successful_step("vrcforge_get_asset_info", {"preview": preview})],
        resource_reader=lambda *_: (_ for _ in ()).throw(ValueError("wrong session")),
        project_path="D:/Project",
    )

    assert len(content) == 1
    assert "image unavailable" in content[0]["text"].lower()
    assert "wrong session" not in content[0]["text"]
    assert "base64" not in content[0]["text"].lower()


def _capture_preview(uri, png):
    return {
        "schema": "vrcforge.scene_capture_preview.v1",
        "captureId": "capture-123",
        "resourceUri": uri,
        "mimeType": "image/png",
        "sourceSha256": hashlib.sha256(png).hexdigest(),
        "sha256": hashlib.sha256(png).hexdigest(),
        "width": 8,
        "height": 6,
    }


def test_single_screenshot_preview_uses_only_exact_tool_and_verified_resource():
    png = _png(color=(10, 20, 30, 255))
    preview = _capture_preview("vrcforge://scene-capture/one?revision=1", png)
    calls = []

    def read_resource(received_preview, project_path):
        calls.append((received_preview, project_path))
        return {"contents": [{
            "uri": preview["resourceUri"],
            "mimeType": "image/png",
            "blob": base64.b64encode(png).decode("ascii"),
            "_meta": {"sha256": preview["sha256"], "sourceSha256": preview["sourceSha256"]},
        }]}

    wrong_tool = _successful_step("arbitrary_tool", {"preview": preview})
    assert collect_tool_images([wrong_tool], read_resource, "D:/Project") == []
    assert calls == []

    content = collect_tool_images(
        [_successful_step("vrcforge_capture_screenshot", {"preview": preview})],
        read_resource,
        "D:/Project",
    )
    assert len(calls) == 1
    assert calls[0] == (preview, "D:/Project")
    assert preview["resourceUri"] in content[0]["text"]
    assert preview["sha256"] in content[0]["text"]
    assert content[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def test_multi_screenshot_keeps_distinct_angles_together():
    old_png = _png(color=(0, 0, 0, 255))
    latest_png = _png(color=(250, 120, 30, 255))
    old_preview = _capture_preview("vrcforge://scene-capture/old?revision=1", old_png)
    latest_preview = _capture_preview("vrcforge://scene-capture/latest?revision=1", latest_png)
    calls = []

    def read_resource(preview, _project_path):
        calls.append(preview["resourceUri"])
        png = latest_png if preview is latest_preview else old_png
        return {"contents": [{
            "uri": preview["resourceUri"],
            "mimeType": "image/png",
            "blob": base64.b64encode(png).decode("ascii"),
            "_meta": {"sha256": preview["sha256"], "sourceSha256": preview["sourceSha256"]},
        }]}

    content = collect_tool_images(
        [_successful_step("vrcforge_capture_multi_screenshot", {
            "captures": [{"angle": "front", "preview": old_preview}, {"angle": "back", "preview": latest_preview}],
        })],
        read_resource,
        "D:/Project",
    )

    assert calls == [latest_preview["resourceUri"], old_preview["resourceUri"]]
    assert old_preview["resourceUri"] in content[0]["text"]
    assert content[1]["image_url"]["url"].endswith(base64.b64encode(old_png).decode("ascii"))
    assert latest_preview["resourceUri"] in content[2]["text"]
    assert content[3]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(latest_png).decode("ascii")


def test_capture_preview_rejects_wrong_schema_and_resource_hash():
    png = _png()
    preview = _capture_preview("vrcforge://scene-capture/hash?revision=1", png)
    bad_schema = {**preview, "schema": "vrcforge.texture_asset_preview.v1"}
    calls = []

    def read_resource(value, _project_path):
        calls.append(value)
        return {"contents": [{
            "uri": value["resourceUri"],
            "mimeType": "image/png",
            "blob": base64.b64encode(png).decode("ascii"),
            "_meta": {"sha256": "0" * 64, "sourceSha256": preview["sourceSha256"]},
        }]}

    wrong_schema = collect_tool_images(
        [_successful_step("vrcforge_capture_screenshot", {"preview": bad_schema})],
        read_resource,
        "D:/Project",
    )
    assert len(wrong_schema) == 1 and "image unavailable" in wrong_schema[0]["text"].lower()
    assert calls == []

    wrong_hash = collect_tool_images(
        [_successful_step("vrcforge_capture_screenshot", {"preview": preview})],
        read_resource,
        "D:/Project",
    )
    assert len(wrong_hash) == 1 and "image unavailable" in wrong_hash[0]["text"].lower()


def test_rejects_mime_hash_dimensions_and_oversized_mcp_images():
    png = _png()
    encoded = base64.b64encode(png).decode("ascii")
    bad_sha = {"type": "image", "data": encoded, "mimeType": "image/png", "_meta": {"sha256": "0" * 64}}
    bad_dimensions = {"type": "image", "data": encoded, "mimeType": "image/png", "width": 2048, "height": 6}
    bad_mime = {"type": "image", "data": encoded, "mimeType": "image/jpeg"}

    for item in (bad_sha, bad_dimensions, bad_mime):
        content = collect_tool_images(
            [_successful_step("capture", {"content": [item]})], None, "D:/Project"
        )
        assert len(content) == 1 and "image unavailable" in content[0]["text"].lower()

    oversized = b"x" * (1024 * 1024 + 1)
    content = collect_tool_images(
        [_successful_step("capture", {"content": [_image_content(oversized)]})], None, "D:/Project"
    )
    assert len(content) == 1 and "image unavailable" in content[0]["text"].lower()


def test_ignores_failed_steps_and_does_not_search_user_or_old_history():
    png = _png()
    failed = _successful_step("capture", {"content": [_image_content(png)]})
    failed["status"] = "failed"
    assert collect_tool_images([failed], None, "D:/Project") == []
    unknown_status = _successful_step("capture", {"content": [_image_content(png)]})
    unknown_status["status"] = "queued"
    unknown_status["outcome"] = {}
    assert collect_tool_images([unknown_status], None, "D:/Project") == []
    assert collect_tool_images([], None, "D:/Project") == []


def test_binds_idempotently_before_final_system_without_mutating_history_or_sidecar():
    png = _png()
    content = collect_tool_images(
        [_successful_step("capture", {"content": [_image_content(png)]})], None, "D:/Project"
    )
    original = attach_native_request_composition({
        "instructions": "instructions",
        "messages": [
            {"role": "user", "content": "ask"},
            {"role": "system", "content": "Current runtime state (data): {}"},
        ],
        "tools": [],
    })
    original_messages = json.loads(json.dumps(original["messages"]))

    bound = bind_tool_images(original, content)
    rebound = bind_tool_images(bound, content)

    assert original["messages"] == original_messages
    assert len(bound["messages"]) == 3
    assert bound["messages"] == rebound["messages"]
    assert bound["messages"][-1] == original_messages[-1]
    assert bound.get("_diagnosticComposition") == original.get("_diagnosticComposition")
    assert bound.get("_diagnosticOrigin") == original.get("_diagnosticOrigin")
    assert bound["messages"][1]["content"][1]["type"] == "image_url"
    assert "data:image/png;base64" not in json.dumps(original)
    cleared = bind_tool_images(bound, [])
    assert len(cleared["messages"]) == 2


def test_rejected_image_reports_specific_field_and_correction_without_raw_payload():
    png = _png()
    item = _image_content(png)
    item["mimeType"] = "image/svg+xml"
    content = collect_tool_images([_successful_step("capture", {"content": [item]})], None, "D:/Project")
    assert len(content) == 1
    assert "mimeType is unsupported" in content[0]["text"]
    assert "image/png" in content[0]["text"] and "image/jpeg" in content[0]["text"]
    assert item["data"] not in content[0]["text"]
    item["mimeType"] = "image/png"
    assert collect_tool_images([_successful_step("capture", {"content": [item]})], None, "D:/Project")[1]["type"] == "image_url"


def test_bad_image_hash_keeps_exact_reason_and_does_not_use_older_visual_evidence():
    item = _image_content(_png())
    item["sha256"] = "0" * 64
    content = collect_tool_images([_successful_step("capture", {"content": [item]})], None, "D:/Project")
    assert len(content) == 1 and "sha256 does not match image bytes" in content[0]["text"]
    assert "without guessing or replacing the hash" in content[0]["text"]

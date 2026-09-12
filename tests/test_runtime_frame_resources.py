import base64
import io
from pathlib import Path
import pytest
from PIL import Image
from mcp_resource_registry import McpResourceRegistry
from runtime_frame_resources import publish_verified_frames
from test_runtime_observation import complete_receipt
from runtime_observation import validate_result


def published(tmp_path):
    request, receipt = complete_receipt(tmp_path/"artifacts")
    receipt.update(width=request["width"], height=request["height"])
    receipt=validate_result(receipt,request)
    registry=McpResourceRegistry(tmp_path/"registry")
    frames=publish_verified_frames(registry,receipt,tmp_path/"artifacts/runtime-observations",{"project":{"root":"source-project"}})
    return registry,frames,receipt


def test_frame_resource_is_standard_png_blob(tmp_path):
    registry,frames,_=published(tmp_path)
    from agent_mcp_standard import McpStandardRouter
    router=McpStandardRouter(lambda: [],lambda name,args: {},resource_read=registry.read,resource_list=lambda args:registry.list())
    router.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"frame-agent","version":"1"}}})
    response=router.handle({"jsonrpc":"2.0","id":2,"method":"resources/read","params":{"uri":frames[0]["resourceUri"]}})
    contents=response["result"]["contents"]
    assert len(contents)==1
    assert contents[0]["mimeType"]=="image/png"
    assert "text" not in contents[0]
    with Image.open(io.BytesIO(base64.b64decode(contents[0]["blob"]))) as image:
        assert image.size==(128,128)
    assert contents[0]["_meta"]["identity"]["executionTarget"]["project"]["root"]=="source-project"
    assert contents[0]["_meta"]["historical"] is True
    assert registry.list()["resources"][0]["mimeType"]=="image/png"


def test_modified_frame_rejected_after_registration(tmp_path):
    registry,frames,_=published(tmp_path)
    Path(frames[0]["imagePath"]).write_bytes(b"changed")
    with pytest.raises(ValueError,match="drifted"):
        registry.read(frames[0]["resourceUri"])


def test_arbitrary_uri_cannot_read_file(tmp_path):
    registry,frames,_=published(tmp_path)
    with pytest.raises(ValueError):registry.read("file:///etc/passwd")
    with pytest.raises(ValueError):registry.read("vrcforge://runtime-observation/other/frames/0?revision=1")


def test_outside_job_path_rejected_before_any_publish(tmp_path):
    request,receipt=complete_receipt(tmp_path/"artifacts")
    receipt.update(width=128,height=128)
    receipt=validate_result(receipt,request)
    receipt["frames"][-1]["imagePath"]=str(tmp_path/"outside.png")
    registry=McpResourceRegistry(tmp_path/"registry")
    with pytest.raises(ValueError):publish_verified_frames(registry,receipt,tmp_path/"artifacts/runtime-observations",{})
    assert registry.list()["resources"]==[]


def test_existing_resource_survives_registry_reload_with_original_identity(tmp_path):
    registry,frames,_=published(tmp_path)
    loaded=McpResourceRegistry(tmp_path/"registry")
    assert loaded.read(frames[0]["resourceUri"])["contents"][0]["_meta"]["identity"]["avatarPath"]=="Root/Avatar"


def test_unverified_receipt_cannot_publish(tmp_path):
    registry,frames,receipt=published(tmp_path)
    receipt["verified"]=False
    with pytest.raises(ValueError):publish_verified_frames(registry,receipt,tmp_path/"artifacts/runtime-observations",{})

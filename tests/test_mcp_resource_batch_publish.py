import json
import threading
from pathlib import Path
import pytest
from mcp_resource_registry import McpResourceRegistry


def spec(index):
    return {"base_uri":f"vrcforge://batch/{index}","name":str(index),"resource_type":"test","data":{"index":index},
            "identity":{"projectId":"exact"},"source_mode":"captured","refresh_rule":"Historical"}


def test_real_registry_many_saves_once_and_old_publish_is_compatible(tmp_path,monkeypatch):
    registry=McpResourceRegistry(tmp_path)
    original=registry._persist_locked;calls=[]
    def persist():calls.append(1);original()
    monkeypatch.setattr(registry,"_persist_locked",persist)
    records=registry.publish_many([spec(i) for i in range(88)])
    assert len(records)==88 and len(calls)==1
    before=registry.generation
    assert registry.publish(**spec(0))==records[0] and registry.generation==before and len(calls)==1
    next_record=registry.publish(**{**spec(0),"data":{"index":99}})
    assert next_record["revision"]==2 and len(calls)==2
    loaded=McpResourceRegistry(tmp_path)
    assert loaded.read(records[0]["uri"])["structuredContent"]["data"]=={"index":0}
    assert loaded.read(next_record["uri"])["structuredContent"]["data"]=={"index":99}


def test_batch_preflight_and_commit_failure_leave_original_index(tmp_path,monkeypatch):
    registry=McpResourceRegistry(tmp_path)
    first=registry.publish(**spec(0));before=(tmp_path/"registry-v2.sqlite3").read_bytes();generation=registry.generation
    with pytest.raises(ValueError):registry.publish_many([spec(1),{**spec(2),"base_uri":"file://forbidden"}])
    assert registry.generation==generation and (tmp_path/"registry-v2.sqlite3").read_bytes()==before
    def fail():raise OSError("commit fault")
    monkeypatch.setattr(registry,"_persist_locked",fail)
    with pytest.raises(OSError,match="commit fault"):registry.publish_many([spec(1),spec(2)])
    assert registry.generation==generation and (tmp_path/"registry-v2.sqlite3").read_bytes()==before
    assert registry.list()["resources"][0]["uri"]==first["uri"] and len(registry.list()["resources"])==1


def test_batch_lock_preserves_concurrent_single_publish(tmp_path,monkeypatch):
    registry=McpResourceRegistry(tmp_path)
    original=registry._persist_locked;entered=threading.Event();release=threading.Event();errors=[]
    def persist():
        if threading.current_thread().name=="batch":
            entered.set();assert release.wait(5)
        original()
    monkeypatch.setattr(registry,"_persist_locked",persist)
    def run(callback):
        try:callback()
        except Exception as exc:errors.append(exc)
    batch=threading.Thread(name="batch",target=lambda:run(lambda:registry.publish_many([spec(i) for i in range(20)])))
    single=threading.Thread(target=lambda:run(lambda:registry.publish(**spec(99))))
    batch.start();assert entered.wait(5);single.start();release.set();batch.join(5);single.join(5)
    assert not batch.is_alive() and not single.is_alive() and not errors
    assert len(McpResourceRegistry(tmp_path).list()["resources"])==21


def test_domain_validator_failure_discards_entire_staged_batch(tmp_path):
    registry=McpResourceRegistry(tmp_path)
    registry.publish(**spec(0));before=(tmp_path/"registry-v2.sqlite3").read_bytes();generation=registry.generation
    def reject(records):
        assert len(records)==2
        raise ValueError("domain identity changed")
    with pytest.raises(ValueError,match="identity changed"):
        registry.publish_many([spec(1),spec(2)],validate_records=reject)
    assert registry.generation==generation and (tmp_path/"registry-v2.sqlite3").read_bytes()==before
    assert len(registry.list()["resources"])==1


def _gateway_target(tmp_path):
    return {
        "schema": "vrcforge.execution_target.v1", "scope": "object",
        "namespace": "vrcforge://projects/p/scenes/s/objects/o",
        "project": {"projectId": "p", "root": str(tmp_path)},
        "editor": {"unityPid": 1, "processStartTime": "t", "coreInstanceId": "c"},
        "scene": {"guid": "s", "revision": "1", "digest": "d"},
        "object": {"globalObjectId": "o", "exactHierarchyPath": "Root/Object"},
    }


def test_gateway_tool_result_target_batch_persists_once_and_preserves_order(tmp_path, monkeypatch):
    from agent_gateway import AgentGateway
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    calls = []
    original = gateway._mcp_resources._persist_locked
    monkeypatch.setattr(gateway._mcp_resources, "_persist_locked", lambda: (calls.append(1), original())[1])
    target = _gateway_target(tmp_path)
    result = {"ok": True, "operationId": "op", "executionTarget": target, "result": {"value": 1}}
    receipt = gateway.publish_mcp_tool_result_resource("vrc_get_asset_info", {}, result, source_mode="test")
    resources = gateway._mcp_resources.list(page_size=10)["resources"]
    assert len(calls) == 1
    assert [item["_meta"]["resourceType"] for item in resources] == [
        "session_identity_lock", "unity_snapshot", "operation_receipt"
    ]
    assert all(
        gateway._mcp_resources.read(item["uri"])["structuredContent"]["data"]["sourceOperationId"] == "op"
        for item in resources if item["_meta"]["resourceType"] == "unity_snapshot"
    )
    assert result["operationResource"] == receipt["uri"]
    assert all(item["_meta"]["identity"]["objectGlobalObjectId"] == "o" for item in resources)


def test_gateway_gesture_manager_result_persists_five_resources_once(tmp_path, monkeypatch):
    from agent_gateway import AgentGateway
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    calls = []; original = gateway._mcp_resources._persist_locked
    monkeypatch.setattr(gateway._mcp_resources, "_persist_locked", lambda: (calls.append(1), original())[1])
    target = _gateway_target(tmp_path)
    result = {"ok": True, "operationId": "gm-op", "executionTarget": target, "result": {"value": 1}}
    gateway.publish_mcp_tool_result_resource("vrc_gesture_manager_set_parameter", {}, result, source_mode="test")
    resources = gateway._mcp_resources.list(page_size=10)["resources"]
    assert len(calls) == 1 and len(resources) == 5
    assert [item["_meta"]["resourceType"] for item in resources] == [
        "session_identity_lock", "gm_runtime", "control_graph", "unity_snapshot", "operation_receipt"
    ]
    assert all(
        gateway._mcp_resources.read(item["uri"])["structuredContent"]["data"]["sourceOperationId"] == "gm-op"
        for item in resources if item["_meta"]["resourceType"] in {"control_graph", "gm_runtime", "unity_snapshot"}
    )
    assert result["resources"]["operationReceiptUri"].startswith("vrcforge://operation/gm-op/receipt")


def test_gateway_batch_failure_keeps_result_and_registry_unchanged(tmp_path, monkeypatch):
    from agent_gateway import AgentGateway
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    before = (gateway._mcp_resources.generation, gateway._mcp_resources.list(page_size=500))
    target = _gateway_target(tmp_path)
    result = {"ok": True, "operationId": "failed-op", "executionTarget": target, "result": {"value": 1}}
    def fail_persist():
        raise OSError("persist fault")
    monkeypatch.setattr(gateway._mcp_resources, "_persist_locked", fail_persist)
    with pytest.raises(OSError, match="persist fault"):
        gateway.publish_mcp_tool_result_resource("vrc_get_asset_info", {}, result, source_mode="test")
    assert "operationResource" not in result and "resources" not in result
    assert (gateway._mcp_resources.generation, gateway._mcp_resources.list(page_size=500)) == before


def test_real_state_and_png_producers_each_persist_once(tmp_path,monkeypatch):
    from runtime_state_resources import project_receipt
    from runtime_frame_resources import publish_verified_frames
    from test_runtime_observation import complete_receipt
    from runtime_observation import validate_result
    registry=McpResourceRegistry(tmp_path/"store")
    calls=[];original=registry._persist_locked
    def persist():calls.append(1);original()
    monkeypatch.setattr(registry,"_persist_locked",persist)
    request,raw=complete_receipt(tmp_path/"artifacts")
    raw.update(width=request["width"],height=request["height"])
    raw=validate_result(raw,request)
    publish_verified_frames(registry,raw,tmp_path/"artifacts/runtime-observations",{})
    assert len(calls)==1
    raw["frames"]=[{**raw["frames"][0],"sampleIndex":i-1,"unityFrame":i+1,
                     "states":[{"stateHash":1,"layerIndex":j} for j in range(120)]} for i in range(11)]
    project_receipt(registry,raw,{})
    assert len(calls)==2
    assert len([r for r in registry.list(page_size=500)["resources"] if r["_meta"]["resourceType"]=="runtime_observation_state_page"])==88

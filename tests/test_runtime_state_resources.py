import copy
import json
import os
from pathlib import Path
import pytest
from mcp_resource_registry import McpResourceRegistry

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = Path(os.environ["VRCFORGE_REGRESSION_ARCHIVE_ROOT"]) if os.environ.get("VRCFORGE_REGRESSION_ARCHIVE_ROOT") else ROOT / ".tmp" / "regression-archives"


def live_shape():
    path=ARCHIVE_ROOT / "176.json"
    if not path.exists():
        pytest.skip("local recorded live evidence is unavailable")
    return json.loads(path.read_text(encoding="utf-8-sig"))["results"][4]["structuredContent"]


def test_live_176_default_projection_and_standard_page_roundtrip(tmp_path):
    from runtime_state_resources import project_receipt
    from agent_mcp_standard import McpStandardRouter
    source=live_shape(); original=copy.deepcopy(source)
    registry=McpResourceRegistry(tmp_path/"store")
    target={"project":{"projectId":"captured-project"},"avatar":{"path":"FinalAvatar"}}
    value=source["result"]
    completed=value["completionVerification"]
    value["completionVerification"]=project_receipt(registry,completed,target)
    result=project_receipt(registry,value,target)
    assert len(json.dumps(result,ensure_ascii=False).encode())<30000
    assert "states" not in result["frames"][0]
    router=McpStandardRouter(lambda:[],lambda n,a:{},resource_read=registry.read)
    router.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"states","version":"1"}}})
    for index, frame in enumerate(result["completionVerification"]["frames"]):
        assert frame["stateCount"]==120 and frame["statesInline"] is False
        states=[];uri=frame["statesResourceUri"]
        while uri:
            response=router.handle({"jsonrpc":"2.0","id":2,"method":"resources/read","params":{"uri":uri}})
            assert "error" not in response,response
            envelope=json.loads(response["result"]["contents"][0]["text"])
            data=envelope["data"]
            assert data["offset"]==len(states) and data["totalCount"]==120
            assert 0<data["actualCount"]<=16 and data["actualCount"]==len(data["states"])
            assert envelope["identity"]["executionTarget"]==target
            assert len(response["result"]["contents"][0]["text"].encode())<=32768
            states.extend(data["states"]);uri=data["nextUri"]
        assert states==original["result"]["completionVerification"]["frames"][index]["states"]
    inline=project_receipt(registry,original["result"],target,state_detail="inline")
    assert inline==original["result"]


def receipt(states=None):
    return {"schema":"vrcforge.runtime_observation.v1","jobId":"a"*32,"avatarPath":"Root/Avatar","coreIdentity":"fixed-core",
            "status":"pending","verified":False,"frames":[{"sampleIndex":-1,"unityFrame":42,"actualElapsedSeconds":0,
            "states":states or [{"controllerIndex":0,"layerIndex":i,"stateHash":1} for i in range(20)]}]}


def test_byte_bound_offsets_unknown_drift_and_restart(tmp_path):
    from runtime_state_resources import project_receipt
    registry=McpResourceRegistry(tmp_path/"store")
    states=[{"layerIndex":i,"clips":"x"*6000} for i in range(10)]
    raw=receipt(states); projected=project_receipt(registry,raw,{})
    uri=projected["frames"][0]["statesResourceUri"]
    loaded=McpResourceRegistry(tmp_path/"store")
    page=json.loads(loaded.read(uri)["contents"][0]["text"])["data"]
    assert 0<page["actualCount"]<16
    following=json.loads(loaded.read(page["nextUri"])["contents"][0]["text"])["data"]
    assert following["offset"]==page["actualCount"]
    with pytest.raises(ValueError):loaded.read(uri.replace("offset=0","offset=1"))
    with pytest.raises(ValueError):loaded.read(uri.replace("a"*32,"b"*32))
    changed=copy.deepcopy(raw);changed["frames"][0]["states"][0]["clips"]="changed"
    with pytest.raises(ValueError,match="changed"):project_receipt(loaded,changed,{})
    loaded._records[uri]["data"]["states"][0]["clips"]="tampered"
    with pytest.raises(ValueError,match="changed"):loaded.read(uri)


def test_preflight_large_last_row_publishes_nothing(tmp_path):
    from runtime_state_resources import project_receipt
    registry=McpResourceRegistry(tmp_path/"store")
    raw=receipt([{"stateHash":1},{"stateHash":2,"oversized":"x"*40000}])
    before=copy.deepcopy(raw)
    with pytest.raises(ValueError,match="One captured state"):project_receipt(registry,raw,{})
    assert registry.list()["resources"]==[] and raw==before


def test_historical_resource_does_not_replace_or_promote_pending(tmp_path):
    import runtime_state_resources as domain
    registry=McpResourceRegistry(tmp_path/"store")
    raw=receipt(); first=domain.project_receipt(registry,raw,{})
    assert first["verified"] is False and first["status"]=="pending"
    uri=first["frames"][0]["statesResourceUri"]
    captured=registry.read(uri); generation=registry.generation
    assert domain.project_receipt(registry,raw,{})==first and registry.read(uri)==captured
    assert registry.generation==generation
    assert "expiresAt" not in json.loads(captured["contents"][0]["text"])["data"]
    registry._records.pop(uri)
    with pytest.raises(ValueError):registry.read(uri)


def test_actual_gateway_finalizer_default_is_compact_and_complete(tmp_path,monkeypatch):
    import hashlib
    from PIL import Image
    import dashboard_server as ds
    from test_mcp_write_transaction_contract import _gateway
    source=live_shape()["result"]
    completed=copy.deepcopy(source["completionVerification"])
    output=tmp_path/"png";output.mkdir()
    for index,frame in enumerate(completed["frames"]):
        image=output/f"{index:03d}.png"
        Image.new("RGB",(completed["width"],completed["height"]),(index,20,30)).save(image)
        frame["imagePath"]=str(image);frame["sha256"]=hashlib.sha256(image.read_bytes()).hexdigest()
    args={"jobId":completed["jobId"],"avatarPath":completed["avatarPath"],"parameterName":completed["parameterName"],
          "value":completed["requestedValue"],"durationSeconds":completed["durationSeconds"],"frameCount":completed["requestedFrameCount"],
          "width":completed["width"],"height":completed["height"],"outputDirectory":str(output)}
    gateway=_gateway(tmp_path/"gateway")
    monkeypatch.setattr(ds,"AGENT_GATEWAY",gateway)
    monkeypatch.setattr(ds,"runtime_observation_status_raw",lambda _:completed)
    initial=copy.deepcopy(source);initial.pop("completionVerification")
    name="vrcforge_contract_runtime_observation"
    gateway.approval_transactions.register_write_handler(name,"Observation","high",lambda _:copy.deepcopy(initial),
        verification_finalize_handler=ds.runtime_observation_finalize,pre_write_checkpoint_required=False)
    gateway.register_external_mcp_unity_tool(name,"avatar")
    proposal=gateway.call_external_mcp_tool(name,args)
    result=gateway.call_external_mcp_tool(name,{**args,"confirmation":{**proposal["confirmation"],"decision":"approve"}})
    assert result["ok"] is True,result
    assert len(json.dumps(result,ensure_ascii=False).encode())<35000
    evidence=result["result"]["completionVerification"]
    assert "states" not in result["result"]["frames"][0]
    for actual,expected in zip(evidence["frames"],completed["frames"]):
        states=[];uri=actual["statesResourceUri"]
        while uri:
            data=json.loads(gateway.read_mcp_resource(uri)["contents"][0]["text"])["data"]
            states+=data["states"];uri=data["nextUri"]
        assert states==expected["states"]
    gateway.publish_mcp_tool_result_resource(name,args,result,source_mode="external_agent")
    stored=gateway.read_mcp_resource(result["operationResource"])["structuredContent"]["data"]["result"]
    assert stored["result"]["completionVerification"]==evidence


def test_public_mode_schema_and_prepare_do_not_forward_projection_to_core(tmp_path):
    import dashboard_server as ds
    import runtime_observation as domain
    from test_runtime_observation import args
    prepared=domain.prepare_request(args(),tmp_path)
    assert prepared["stateDetail"]=="resource" and "stateDetail" not in domain.PUBLIC_KEYS
    assert domain.prepare_request({**args(),"stateDetail":"inline"},tmp_path)["stateDetail"]=="inline"
    with pytest.raises(ValueError,match="stateDetail"):domain.prepare_request({**args(),"stateDetail":"empty"},tmp_path)
    tools={item["name"]:item for item in ds.AGENT_GATEWAY.build_external_mcp_tools("execution",["*"])}
    for name in ("vrcforge_start_runtime_observation","vrcforge_get_runtime_observation"):
        schema=tools[name]["inputSchema"]["properties"]["stateDetail"]
        assert schema["default"]=="resource" and schema["enum"]==["resource","inline"]
    selection = tools["vrcforge_get_runtime_observation"]["inputSchema"]["properties"]["stateSelection"]
    assert selection["type"] == "object" and selection["required"] == ["layerName"]
    assert selection["additionalProperties"] is False
    assert selection["properties"]["layerName"]["maxLength"] == 128


def test_actual_798_inline_layer_selection_keeps_source_receipt_and_selects_one_row(tmp_path):
    from runtime_state_resources import project_receipt
    archive = ARCHIVE_ROOT / "coverage-runtime" / "798-default-third-inline-resume.json"
    if not archive.exists():
        pytest.skip("actual 798 inline archive is unavailable")
    source = json.loads(archive.read_text(encoding="utf-8"))["terminalExpanded"]
    original = copy.deepcopy(source)
    projected = project_receipt(McpResourceRegistry(tmp_path / "store"), source, {}, state_detail="inline",
                                state_selection={"layerName": "Locomotion"})
    assert source == original
    assert projected["stateSelection"]["status"] == "selected"
    assert projected["stateSelection"]["complete"] is True
    assert projected["stateSelection"]["sourceStateCountPerFrame"] == [len(frame["states"]) for frame in source["frames"]]
    assert projected["stateSelection"]["selectedStateCountPerFrame"] == [1] * 33
    assert projected["stateSelection"]["selectedIdentity"] == {"controllerIndex": 0, "layerIndex": 0, "layerName": "Locomotion"}
    assert projected["stateSelectionStatus"] == "selected"
    assert len(projected["frames"]) == 33
    assert all(len(frame["states"]) == 1 and frame["states"][0]["layerName"] == "Locomotion" for frame in projected["frames"])
    assert all(frame["sourceStateCount"] == len(source_frame["states"])
               and frame["selectedStateCount"] == 1
               and "selectedStateHash" in frame
               for frame, source_frame in zip(projected["frames"], source["frames"]))
    assert [frame["imagePath"] for frame in projected["frames"]] == [frame["imagePath"] for frame in source["frames"]]


def test_inline_layer_selection_rejects_duplicate_or_identity_drift(tmp_path):
    from runtime_state_resources import StateSelectionError, project_receipt
    registry = McpResourceRegistry(tmp_path / "store")
    raw = receipt([{"controllerIndex": 0, "layerIndex": 0, "layerName": "Target"},
                   {"controllerIndex": 1, "layerIndex": 0, "layerName": "Target"}])
    with pytest.raises(StateSelectionError) as duplicate:
        project_receipt(registry, raw, {}, state_detail="inline", state_selection={"layerName": "Target"})
    assert duplicate.value.code == "state_selection_ambiguous"
    raw = receipt([{"controllerIndex": 0, "layerIndex": 0, "layerName": "Target"}])
    raw["frames"].append({"sampleIndex": 0, "unityFrame": 43, "actualElapsedSeconds": 1,
                           "states": [{"controllerIndex": 1, "layerIndex": 0, "layerName": "Target"}]})
    with pytest.raises(StateSelectionError) as drift:
        project_receipt(registry, raw, {}, state_detail="inline", state_selection={"layerName": "Target"})
    assert drift.value.code == "state_selection_identity_drift"


def test_inline_layer_selection_keeps_source_hash_separate_from_selected_digest(tmp_path):
    from runtime_state_resources import _hash, project_receipt
    raw = receipt([{"controllerIndex": 0, "layerIndex": 0, "layerName": "Target", "stateHash": 9},
                   {"controllerIndex": 1, "layerIndex": 0, "layerName": "Other", "stateHash": 8}])
    raw["frames"][0]["stateSetHash"] = _hash(raw["frames"][0]["states"])
    source_hash = raw["frames"][0]["stateSetHash"]
    projected = project_receipt(McpResourceRegistry(tmp_path / "store"), raw, {}, state_detail="inline",
                                state_selection={"layerName": "Target"})
    assert projected["frames"][0]["stateSetHash"] == source_hash
    assert projected["frames"][0]["selectedStateHash"] != source_hash
    assert projected["stateSelection"]["selectedStateSetHash"]


def test_resource_layer_selection_is_rejected_instead_of_silently_ignored(tmp_path):
    from runtime_state_resources import StateSelectionError, project_receipt
    with pytest.raises(StateSelectionError) as error:
        project_receipt(McpResourceRegistry(tmp_path / "store"), receipt(), {},
                        state_selection={"layerName": "Target"})
    assert error.value.code == "state_selection_requires_inline"


def test_public_mcp_read_projection_exposes_selector_error_without_mutation(tmp_path):
    from agent_gateway import create_agent_mcp_app
    from runtime_state_resources import StateSelectionError
    from test_agent_gateway_integrity import _external_gateway, _external_mcp_call
    gateway = _external_gateway(tmp_path / "gateway")

    def reject(_arguments):
        raise StateSelectionError("state_selection_ambiguous", "Layer selector is ambiguous.",
                                  ({"controllerIndex": 1, "layerIndex": 0, "layerName": "Target"},))

    name = "vrcforge_runtime_observation_selector_fixture"
    gateway.register_tool(name, "Selector error fixture.", "unity", reject)
    gateway.register_external_mcp_unity_tool(name, "diagnostics")
    response = _external_mcp_call(create_agent_mcp_app(gateway), "tools/call",
                                  {"name": name, "arguments": {}},
                                  bearer=gateway.ensure_config().token, result_mode="full")
    result = response["result"]["structuredContent"]
    assert result["ok"] is False
    assert result["errorDetails"]["errorCode"] == "state_selection_ambiguous"
    assert result["errorDetails"]["details"]["candidates"] == [{"controllerIndex": 1, "layerIndex": 0, "layerName": "Target"}]
    assert result["errorDetails"]["mutationStarted"] is False
    assert result["errorDetails"]["retryable"] is False
    assert result["errorDetails"]["commitState"] == "not_started"


@pytest.mark.parametrize("frames", [pytest.param("__missing__"), None, []])
def test_runtime_status_preserves_core_unavailable_error_before_state_selection(monkeypatch, frames):
    import dashboard_server as ds

    core_error = {
        "ok": False,
        "status": "failed",
        "errorCode": "runtime_observation_unavailable",
        "error": "Observation job unavailable in this Editor lifetime.",
        "mutationStarted": False,
        "commitState": "not_started",
        "readbackState": "failed",
    }
    raw_error = dict(core_error)
    if frames != "__missing__":
        raw_error["frames"] = frames
    monkeypatch.setattr(ds, "runtime_observation_status_raw", lambda _args: dict(raw_error))

    result = ds.runtime_observation_status_sync({
        "jobId": "98e79e18d2434ef7b5bfc6afa2ffa6fc",
        "avatarPath": "VRCForge_Composition_Workspace/FinalAvatar",
        "stateDetail": "inline",
        "stateSelection": {"layerName": "衣柜"},
    })

    assert result == raw_error


def test_validation_precedes_projection_and_publication(tmp_path,monkeypatch):
    import dashboard_server as ds
    from test_mcp_write_transaction_contract import _gateway
    gateway=_gateway(tmp_path/"gateway")
    monkeypatch.setattr(ds,"AGENT_GATEWAY",gateway)
    invalid=receipt();invalid.update(status="completed",verified=True,requestedFrameCount=2)
    invalid["frames"]=[]
    monkeypatch.setattr(ds,"runtime_observation_status_raw",lambda _:invalid)
    with pytest.raises(ValueError,match="frame evidence"):
        ds.runtime_observation_finalize({"jobId":invalid["jobId"],"avatarPath":invalid["avatarPath"],"durationSeconds":.5,"frameCount":2},{},{"ok":True})
    assert gateway._mcp_resources.list()["resources"]==[]

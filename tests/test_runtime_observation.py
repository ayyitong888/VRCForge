def test_observation_validation_and_contract_exist():
    import runtime_observation as domain
    assert callable(domain.prepare_request)
    assert callable(domain.validate_result)


import pytest
import runtime_observation as domain

def args():
    return {"avatarPath":"Root/Avatar", "parameterName":"Test", "value":1, "durationSeconds":.5, "frameCount":8, "width":256, "height":256, "cameraPosition":{"x":0,"y":1,"z":3}, "targetPosition":{"x":0,"y":1,"z":0}, "upVector":{"x":0,"y":1,"z":0}, "fieldOfView":40}

@pytest.mark.parametrize("patch", [{"durationSeconds":0}, {"durationSeconds":11}, {"frameCount":33}, {"value":float("nan")}, {"width":512,"height":512,"frameCount":32}, {"jobId":"owned"}, {"outputDirectory":"elsewhere"}])
def test_invalid_limits_reject_before_job(patch,tmp_path):
    with pytest.raises(ValueError): domain.prepare_request({**args(),**patch},tmp_path)

def test_prepared_job_paths_are_unique_and_managed(tmp_path):
    a=domain.prepare_request(args(),tmp_path);b=domain.prepare_request(args(),tmp_path)
    assert a["jobId"] != b["jobId"]
    assert a["outputDirectory"] == str(tmp_path/"runtime-observations"/a["jobId"])

def test_parameter_steps_are_optional_bounded_and_strictly_monotonic(tmp_path):
    request = domain.prepare_request({**args(), "parameterSteps": [
        {"timeSeconds": .1, "parameterName": "A", "value": 0},
        {"timeSeconds": .4, "parameterName": "B", "value": 1.5},
    ]}, tmp_path)
    assert request["parameterSteps"][1]["parameterName"] == "B"
    for steps in ([{"timeSeconds": .1, "parameterName": "A", "value": 0}, {"timeSeconds": .1, "parameterName": "B", "value": 1}],
                   [{"timeSeconds": .6, "parameterName": "A", "value": 0}],
                   [{"timeSeconds": .1, "parameterName": "", "value": 0}],
                   [{"timeSeconds": .1, "parameterName": "A", "value": float("nan")}],
                   [{"timeSeconds": .1, "parameterName": "A", "value": 0, "extra": 1}]):
        with pytest.raises(ValueError):
            domain.prepare_request({**args(), "parameterSteps": steps}, tmp_path)


def pending_receipt(request):
    return {"schema": "vrcforge.runtime_observation.v1", "jobId": request["jobId"],
            "status": "pending", "avatarPath": request["avatarPath"],
            "parameterName": request["parameterName"], "requestedValue": request["value"],
            "durationSeconds": request["durationSeconds"], "width": request["width"],
            "height": request["height"], "requestedFrameCount": request["frameCount"],
            "mutationStarted": True, "verified": False, "commitState": "pending",
            "coreIdentity": "projectId|instanceId|processId|processStartTime",
            "avatarInstanceId": 38458, "animatorInstanceId": -56512, "frames": []}


def test_pending_start_acknowledgement_is_strict(tmp_path):
    request = domain.prepare_request(args(), tmp_path)
    receipt = pending_receipt(request)
    assert domain.validate_start_result(receipt, request) is receipt
    for key, value in (("jobId", "other"), ("parameterName", "Other"), ("requestedValue", 2), ("coreIdentity", "")):
        changed = {**receipt, key: value}
        with pytest.raises(ValueError):
            domain.validate_start_result(changed, request)
    with pytest.raises(ValueError):
        domain.validate_start_result({**receipt, "schema": "wrong"}, request)
    for key, value in (("ok", False), ("success", False), ("isError", True)):
        with pytest.raises(ValueError):
            domain.validate_start_result({**receipt, key: value}, request)


def test_real_357_pending_start_fixture_accepts_signed_unity_instance_ids():
    import json
    from pathlib import Path

    source = Path(__file__).parent / "fixtures" / "runtime_observation_pending_357.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    request = {"jobId": payload["jobId"], "avatarPath": payload["avatarPath"],
               "parameterName": payload["parameterName"], "value": payload["requestedValue"],
               "durationSeconds": payload["durationSeconds"], "frameCount": payload["requestedFrameCount"],
               "width": payload["width"], "height": payload["height"]}
    assert payload["animatorInstanceId"] == -56512
    assert domain.validate_start_result(payload, request) is payload

def test_empty_animator_state_cannot_verify(tmp_path):
    r=domain.prepare_request(args(),tmp_path)
    with pytest.raises(ValueError):domain.validate_result({"schema":"vrcforge.runtime_observation.v1","jobId":r["jobId"],"avatarPath":r["avatarPath"],"status":"completed","verified":True,"frames":[{"states":[]}, {"states":[]}]},r)

def test_partial_remains_unverified(tmp_path):
    r=domain.prepare_request(args(),tmp_path)
    assert domain.validate_result({"schema":"vrcforge.runtime_observation.v1","jobId":r["jobId"],"avatarPath":r["avatarPath"],"status":"partial","verified":False},r)["ok"] is False

def test_completed_step_receipts_must_match_requested_steps(tmp_path):
    request, receipt = complete_receipt(tmp_path)
    request["parameterSteps"] = [{"timeSeconds": .1, "parameterName": "A", "value": 1}]
    receipt["stepReceipts"] = [{"timeSeconds": .1, "parameterName": "A", "requestedValue": 1, "actualElapsedSeconds": .25, "unityFrame": 3, "beforeValue": 0, "afterValue": 1, "status": "applied"}]
    assert domain.validate_result(receipt, request)["ok"] is True
    receipt["stepReceipts"][0]["parameterName"] = "B"
    with pytest.raises(ValueError): domain.validate_result(receipt, request)

def test_registered_public_contract():
    import dashboard_server as ds
    from unity_mcp_tool_contract import EXPECTED_TOOL_NAMES,READ_ONLY_TOOL_NAMES
    from mcp_tool_descriptor import identity_scope
    assert "vrc_get_runtime_observation" in READ_ONLY_TOOL_NAMES
    assert "vrc_start_runtime_observation" in EXPECTED_TOOL_NAMES
    assert identity_scope("vrcforge_start_runtime_observation",write=True)=="avatar"
    h=ds.AGENT_GATEWAY._write_handlers["vrcforge_start_runtime_observation"]
    assert h.pre_write_checkpoint_required is False
    assert h.requires_approved_execution_context is True
    assert h.verification_finalize_handler is ds.runtime_observation_finalize


def test_public_catalog_has_both_bounded_tools():
    import dashboard_server as ds
    tools=ds.AGENT_GATEWAY.build_external_mcp_tools("execution", ["*"])
    by_name={tool["name"]:tool for tool in tools}
    for name in ("vrcforge_start_runtime_observation", "vrcforge_get_runtime_observation"):
        assert name in by_name
        assert by_name[name]["inputSchema"]["properties"]["avatarPath"]["type"]=="string"
    assert by_name["vrcforge_start_runtime_observation"]["inputSchema"]["properties"]["durationSeconds"]["maximum"]==10
    steps = by_name["vrcforge_start_runtime_observation"]["inputSchema"]["properties"]["parameterSteps"]
    assert steps["maxItems"] == 64 and set(steps["items"]["required"]) == {"timeSeconds", "parameterName", "value"}

def test_prepared_handler_keeps_parameter_steps(monkeypatch):
    import dashboard_server as ds
    requested = {**args(), "parameterSteps": [{"timeSeconds": .1, "parameterName": "Other", "value": 1}]}
    prepared, _ = ds.prepare_runtime_observation_request(requested, None)
    assert prepared["parameterSteps"] == requested["parameterSteps"]


def complete_receipt(tmp_path):
    import hashlib
    from pathlib import Path
    from PIL import Image
    r=domain.prepare_request({**args(),"frameCount":2,"width":128,"height":128},tmp_path)
    root=Path(r["outputDirectory"]);root.mkdir(parents=True)
    frames=[]
    for i,sample in enumerate((-1,0,1)):
        path=root/f"{i:03d}.png";Image.new("RGB",(128,128)).save(path)
        frames.append({"sampleIndex":sample,"unityFrame":i+1,"actualElapsedSeconds":i*.25,"states":[{"stateHash":123}],"imagePath":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    return r,{"schema":"vrcforge.runtime_observation.v1","jobId":r["jobId"],"avatarPath":r["avatarPath"],"parameterName":r["parameterName"],"requestedValue":1,"status":"completed","verified":True,"requestedFrameCount":2,"sampledFrameCount":2,"undersampled":False,"frames":frames}


def test_real_png_receipt_readback(tmp_path):
    request,receipt=complete_receipt(tmp_path)
    assert domain.validate_result(receipt,request)["readback"]["frameCount"]==3

@pytest.mark.parametrize("case", ["parameter", "value", "baseline", "duplicate", "same_unity_frame", "modified_png"])
def test_receipt_contradictions_rejected(tmp_path,case):
    request,receipt=complete_receipt(tmp_path)
    if case=="parameter":receipt["parameterName"]="Other"
    if case=="value":receipt["requestedValue"]=2
    if case=="baseline":receipt["frames"][0]["sampleIndex"]=0
    if case=="duplicate":receipt["frames"][2]["sampleIndex"]=0
    if case=="same_unity_frame":receipt["frames"][2]["unityFrame"]=2
    if case=="modified_png":
        from pathlib import Path
        Path(receipt["frames"][0]["imagePath"]).write_bytes(b"changed")
    with pytest.raises(ValueError):domain.validate_result(receipt,request)

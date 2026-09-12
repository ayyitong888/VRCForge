"""Independent runtime observation review: execute production logic, narrow API seams."""
import json
import ast
import os
from pathlib import Path
import shutil
import subprocess
import hashlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = Path(os.environ["VRCFORGE_REGRESSION_ARCHIVE_ROOT"]) if os.environ.get("VRCFORGE_REGRESSION_ARCHIVE_ROOT") else ROOT / ".tmp" / "regression-archives"
FROZEN29 = ARCHIVE_ROOT / "candidate" / "hotfix29-source"


def test_frozen29_transaction_method_is_the_old_pending_blocking_path():
    if not (FROZEN29 / "agent_approval_transactions.py").is_file():
        pytest.skip("optional regression archive is unavailable")
    source = (FROZEN29 / "agent_approval_transactions.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_call_write_handler"]
    assert len(methods) == 1
    method = ast.get_source_segment(source, methods[0]) or ""
    assert "verification_finalize_handler" in method
    assert "observation_pending" not in method


def test_actual_finalizer_and_resource_keep_observation_frames(tmp_path, monkeypatch):
    import dashboard_server as ds
    import runtime_observation as domain
    from PIL import Image
    from test_mcp_write_transaction_contract import _gateway
    from test_runtime_observation import args
    request = domain.prepare_request({**args(), "frameCount": 2, "stateDetail": "inline"}, tmp_path)
    output = Path(request["outputDirectory"])
    output.mkdir(parents=True)
    frames = []
    for index, (sample, elapsed) in enumerate([(-1, 0.), (0, .01), (1, .5)]):
        path = output / f"{index:03d}.png"
        Image.new("RGB", (request["width"], request["height"]), (index, 20, 30)).save(path)
        frames.append({"sampleIndex": sample, "actualElapsedSeconds": elapsed,
            "requestedElapsedSeconds": -1 if sample < 0 else sample * .5,
            "unityFrame": index + 1, "parameterValue": 0 if sample < 0 else 1,
            "parameterType": "Float", "states": [{"controllerIndex": 0, "layerIndex": 0, "stateHash": 123, "normalizedTime": elapsed, "inTransition": False, "nextStateHash": 0}],
            "imagePath": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    completed = {"schema": "vrcforge.runtime_observation.v1", "jobId": request["jobId"], "avatarPath": request["avatarPath"],
        "status": "completed", "verified": True, "committed": True, "mutationStarted": True, "commitState": "runtime_observed",
        "parameterName": request["parameterName"], "requestedValue": request["value"], "beforeValue": 0,
        "width": request["width"], "height": request["height"], "durationSeconds": .5,
        "requestedFrameCount": 2, "sampledFrameCount": 2, "undersampled": False, "coverageStatus": "requested_samples_observed", "frames": frames}
    polls = []
    monkeypatch.setattr(ds, "runtime_observation_status_raw", lambda values: polls.append(values["jobId"]) or completed)
    gateway = _gateway(tmp_path / "gateway")
    name = "vrcforge_contract_runtime_observation"
    triggers = []
    gateway.approval_transactions.register_write_handler(name, "Observation", "high",
        lambda values: triggers.append(values["jobId"]) or {"ok": True, "status": "pending", "jobId": values["jobId"], "mutationStarted": True},
        verification_finalize_handler=ds.runtime_observation_finalize, pre_write_checkpoint_required=False)
    gateway.register_external_mcp_unity_tool(name, "avatar")
    proposal = gateway.call_external_mcp_tool(name, request)
    result = gateway.call_external_mcp_tool(name, {**request, "confirmation": {**proposal["confirmation"], "decision": "approve"}})
    assert triggers == polls == [request["jobId"]]
    assert result["ok"] is True, result
    actual = result["result"]["completionVerification"]
    assert actual["frames"] == frames and actual["readback"]["artifactHashes"] == [frame["sha256"] for frame in frames]
    gateway.publish_mcp_tool_result_resource(name, request, result, source_mode="external_agent")
    stored = gateway.read_mcp_resource(result["operationResource"])["structuredContent"]["data"]["result"]
    assert stored["result"]["completionVerification"] == actual


def test_timeout_queries_same_job_without_restarting(monkeypatch):
    import dashboard_server as ds
    calls = []
    times = iter([0, 0, 100])
    monkeypatch.setattr(ds.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(ds.time, "sleep", lambda _: None)
    monkeypatch.setattr(ds, "runtime_observation_start_sync", lambda args: pytest.fail("timeout must not restart"))
    def status(args):
        calls.append((args["jobId"], args["avatarPath"]))
        return {"status": "pending"}
    monkeypatch.setattr(ds, "runtime_observation_status_raw", status)
    result = ds.runtime_observation_finalize(
        {"durationSeconds": .5, "jobId": "fixed-job", "avatarPath": "Root/Avatar"}, {}, {"ok": True, "status": "pending"}
    )
    assert calls == [("fixed-job", "Root/Avatar")]
    assert result["status"] == "pending" and result["retryable"] is False
    assert result["jobId"] == "fixed-job" and result["verified"] is False


def test_error_start_receipt_does_not_poll_or_retry(monkeypatch):
    import dashboard_server as ds
    monkeypatch.setattr(ds, "runtime_observation_status_raw", lambda args: pytest.fail("rejected start must not poll"))
    initial = {"ok": False, "error": "preflight rejected", "mutationStarted": False}
    assert ds.runtime_observation_finalize({}, {}, initial) is initial


def test_pending_runtime_observation_releases_project_lock_without_finalize(tmp_path):
    from test_mcp_write_transaction_contract import _gateway

    gateway = _gateway(tmp_path / "gateway")
    project = tmp_path / "UnityProject"
    project.mkdir()
    finalized = []
    name = "vrcforge_start_runtime_observation"
    from approved_unity_execution import current_approved_unity_execution
    def approved_pending_handler(args):
        plan = current_approved_unity_execution()
        claim = plan.claim("vrc_start_runtime_observation", dict(args), args["projectRoot"])
        claim.complete()
        return {
            "schema": "vrcforge.runtime_observation.v1", "ok": True,
            "jobId": args["jobId"], "avatarPath": args["avatarPath"],
            "status": "pending", "verified": False,
            "mutationStarted": True, "commitState": "pending",
        }
    gateway.approval_transactions.register_write_handler(
        name,
        "Runtime observation",
        "medium",
        approved_pending_handler,
        verification_finalize_handler=lambda *_args: finalized.append(True) or pytest.fail("pending start must not finalize"),
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda args: [("vrc_start_runtime_observation", dict(args))],
        pre_write_checkpoint_required=False,
    )
    arguments = {
        "projectRoot": str(project),
        "avatarPath": "Root/Avatar",
        "jobId": "pending-job",
    }
    request = gateway.approval_transactions.create_apply_request(
        {"target_tool": name, "arguments": arguments}
    )
    gateway.approval_transactions.approve(request["approval"]["id"])
    result = gateway.approval_transactions.apply_approved(
        {"approval_id": request["approval"]["id"]}
    )
    assert result["ok"] is True and result["status"] == "pending"
    assert result["result"]["status"] == "pending"
    assert result["readbackState"] == "pending" and result["commitState"] == "pending"
    assert finalized == []
    lock = gateway.approval_transactions._project_write_locks[
        gateway.approval_transactions._project_lock_key(project)
    ]
    assert lock.locked() is False


@pytest.mark.parametrize("mode", ["completed", "failed", "exit"])
def test_observation_transaction_releases_project_lock_on_terminal_paths(tmp_path, mode):
    from test_mcp_write_transaction_contract import _gateway

    gateway = _gateway(tmp_path / mode)
    project = tmp_path / mode / "UnityProject"
    project.mkdir(parents=True)
    name = f"vrcforge_contract_observation_{mode}"

    def handler(_args):
        if mode == "exit":
            raise RuntimeError("sampling exit")
        if mode == "failed":
            return {"ok": False, "status": "failed", "error": "sampling failed"}
        return {"ok": True, "status": "completed", "verified": True,
                "mutationStarted": True, "mutationApplied": True,
                "commitState": "committed", "readback": {"jobId": mode}}

    gateway.approval_transactions.register_write_handler(
        name, "Observation terminal path", "medium", handler,
        pre_write_checkpoint_required=False,
    )
    request = gateway.approval_transactions.create_apply_request(
        {"target_tool": name, "arguments": {"projectRoot": str(project)}}
    )
    gateway.approval_transactions.approve(request["approval"]["id"])
    result = gateway.approval_transactions.apply_approved(
        {"approval_id": request["approval"]["id"]}
    )
    assert result["ok"] is (mode == "completed")
    lock = gateway.approval_transactions._project_write_locks[
        gateway.approval_transactions._project_lock_key(project)
    ]
    assert lock.locked() is False


def test_public_external_observation_pending_allows_same_project_write(tmp_path):
    from test_mcp_write_transaction_contract import _gateway

    gateway = _gateway(tmp_path / "gateway")
    project = tmp_path / "UnityProject"
    project.mkdir()
    target = {"schema": "vrcforge.execution_target.v1", "scope": "avatar",
              "project": {"root": str(project), "projectId": "project"},
              "editor": {"unityPid": 1, "processStartTime": "start", "coreInstanceId": "core"},
              "scene": {"guid": "scene", "revision": "1", "digest": "digest"},
              "avatar": {"globalObjectId": "avatar", "exactHierarchyPath": "Root/Avatar"},
              "namespace": "vrcforge://projects/project/scenes/scene/avatars/avatar"}
    gateway._validate_external_mcp_execution_target = lambda *_args, **_kwargs: target
    start = "vrcforge_start_runtime_observation"
    setter = "vrcforge_gesture_manager_set_parameter"
    gateway.approval_transactions.register_write_handler(
        start, "Runtime observation", "medium",
        lambda args: {"schema": "vrcforge.runtime_observation.v1", "ok": True,
                      "jobId": args["jobId"], "avatarPath": args["avatarPath"],
                      "status": "pending", "verified": False,
                      "mutationStarted": True, "commitState": "pending"},
        verification_finalize_handler=lambda *_args: pytest.fail("pending public start must not finalize"),
        pre_write_checkpoint_required=False,
    )
    gateway.approval_transactions.register_write_handler(
        setter, "Parameter setter", "medium",
        lambda _args: {"ok": True, "status": "completed", "mutationStarted": True,
                       "mutationApplied": True, "commitState": "runtime_applied",
                       "verified": True, "readback": {"afterValue": 0}},
        pre_write_checkpoint_required=False,
    )
    gateway.register_external_mcp_unity_tool(start, "avatar")
    gateway.register_external_mcp_unity_tool(setter, "avatar")
    common = {"projectRoot": str(project), "executionTarget": target}
    pending = gateway.call_external_mcp_tool(
        start, {**common, "jobId": "pending-job", "avatarPath": "Root/Avatar"}
    )
    assert pending["ok"] is True and pending["status"] == "pending"
    applied = gateway.call_external_mcp_tool(
        setter, {**common, "avatarPath": "Root/Avatar", "parameterName": "衣柜", "value": 0}
    )
    assert applied["ok"] is True and applied["result"]["status"] == "completed"


def test_status_partial_is_not_projected_as_success(monkeypatch):
    import contextlib
    import dashboard_server as ds

    partial = {"schema": "vrcforge.runtime_observation.v1", "jobId": "job",
               "avatarPath": "Root/Avatar", "status": "partial", "verified": False,
               "mutationStarted": True, "commitState": "partial", "error": "play_mode_ended"}
    monkeypatch.setattr(ds, "load_dashboard_settings", lambda _request: {})
    monkeypatch.setattr(ds, "build_agent_connection_request", lambda _args: {})
    monkeypatch.setattr(ds, "invoke_unity_mcp", lambda *_args, **_kwargs: partial)
    monkeypatch.setattr(ds, "extract_tool_result_payload", lambda value: value)
    monkeypatch.setattr(ds, "bound_editor_readback", lambda _args: contextlib.nullcontext())
    result = ds.runtime_observation_status_raw({"jobId": "job", "avatarPath": "Root/Avatar"})
    assert result["ok"] is False and result["verified"] is False
    assert result["error"] == "play_mode_ended" and result["readbackState"] == "failed"


def test_public_status_completed_passes_core_parameter_steps_to_validator(monkeypatch):
    import contextlib
    import dashboard_server as ds
    import runtime_observation as domain
    seen = {}
    completed = {"schema": "vrcforge.runtime_observation.v1", "jobId": "job", "avatarPath": "Root/Avatar",
                 "status": "completed", "verified": True, "requestedFrameCount": 2,
                 "width": 128, "height": 128, "parameterSteps": [{"timeSeconds": .1, "parameterName": "A", "value": 1}],
                 "stepReceipts": []}
    def validate(payload, request):
        seen.update(request)
        return payload
    monkeypatch.setattr(ds, "load_dashboard_settings", lambda _request: {})
    monkeypatch.setattr(ds, "build_agent_connection_request", lambda _args: {})
    monkeypatch.setattr(ds, "invoke_unity_mcp", lambda *_args, **_kwargs: completed)
    monkeypatch.setattr(ds, "extract_tool_result_payload", lambda value: value)
    monkeypatch.setattr(ds, "bound_editor_readback", lambda _args: contextlib.nullcontext())
    monkeypatch.setattr(domain, "validate_result", validate)
    result = ds.runtime_observation_status_raw({"jobId": "job", "avatarPath": "Root/Avatar"})
    assert result["status"] == "completed" and seen["parameterSteps"] == completed["parameterSteps"]


def test_real_graph_selector_only_walks_requested_animator_outputs(tmp_path):
    from test_curve_fx_authoring_runtime_contract import method
    base = Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))
    if not compilers or not refs:
        pytest.skip(".NET 8 SDK required")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    source = (ROOT / "Assets/VRCForge/Editor/RuntimeObservationTool.cs").read_text(encoding="utf-8")
    selected = method(source, "internal static List<AnimatorControllerPlayable> Controllers")
    seam = r'''
using System;using System.Collections.Generic;using System.Linq;
using UnityEngine;using UnityEngine.Playables;using UnityEngine.Animations;
namespace UnityEngine {public class Animator {}}
namespace UnityEngine.Playables {
 public class Node {public int Layers;public bool Controller;public List<Playable> Inputs=new List<Playable>();}
 public struct Playable {
  public Node N;public bool IsValid()=>N!=null;public Type GetPlayableType()=>N.Controller?typeof(AnimatorControllerPlayable):typeof(Playable);
  public int GetInputCount()=>N.Inputs.Count;public Playable GetInput(int i)=>N.Inputs[i];
 }
 public struct PlayableOutput {
  public Animator Target;public Playable Source;public bool Animation;
  public Type GetPlayableOutputType()=>Animation?typeof(AnimationPlayableOutput):typeof(PlayableOutput);
  public Playable GetSourcePlayable()=>Source;
 }
 public class PlayableGraph {
  public bool Valid=true;public List<PlayableOutput> Outputs=new List<PlayableOutput>();
  public bool IsValid()=>Valid;public int GetOutputCount()=>Outputs.Count;public PlayableOutput GetOutput(int i)=>Outputs[i];
 }
}
namespace UnityEngine.Animations {
 public struct AnimatorControllerPlayable {
  public Node N;public int GetLayerCount()=>N.Layers;
  public static explicit operator AnimatorControllerPlayable(Playable p)=>new AnimatorControllerPlayable{N=p.N};
 }
 public struct AnimationPlayableOutput {
  public Animator Target;public Animator GetTarget()=>Target;
  public static explicit operator AnimationPlayableOutput(PlayableOutput p)=>new AnimationPlayableOutput{Target=p.Target};
 }
}
namespace UnityEditor.Playables {public static class Utility {public static List<PlayableGraph> Graphs=new List<PlayableGraph>();public static List<PlayableGraph> GetAllGraphs()=>Graphs;}}
'''
    runner = r'''
static int Main(){
 var intended=new Animator();var other=new Animator();
 var right=new Node{Controller=true,Layers=3};var wrong=new Node{Controller=true,Layers=4};
 var mixer=new Node();mixer.Inputs.Add(new Playable{N=right});mixer.Inputs.Add(new Playable{N=mixer});
 var graph=new PlayableGraph();graph.Outputs.Add(new PlayableOutput{Target=other,Source=new Playable{N=wrong},Animation=true});
 graph.Outputs.Add(new PlayableOutput{Target=intended,Source=new Playable{N=mixer},Animation=true});
 graph.Outputs.Add(new PlayableOutput{Target=intended,Source=new Playable{N=wrong},Animation=false});
 UnityEditor.Playables.Utility.Graphs.Add(graph);
 var actual=Controllers(intended);if(actual.Count!=1||actual[0].N!=right)throw new Exception("wrong animator output or duplicate cycle entered");
 Console.WriteLine("PASS only actual Animator output, valid controller, cycle de-duplicated");
 var second=new Node{Controller=true,Layers=2};mixer.Inputs.Add(new Playable{N=second});
 for(int i=0;i<600;i++)right.Inputs.Add(new Playable{N=new Node()});
 actual=Controllers(intended);
 if(actual.Count!=2||actual[0].N!=right||actual[1].N!=second)throw new Exception("controller subtree pruning lost sibling controller");
 Console.WriteLine("PASS large controller internals skipped and sibling controller retained");
 bool rejected=false;try{Controllers(new Animator());}catch(InvalidOperationException){rejected=true;}
 if(!rejected)throw new Exception("empty exact target graph accepted");Console.WriteLine("PASS unrelated Animator rejected");
 right.Layers=257;rejected=false;try{Controllers(intended);}catch(InvalidOperationException){rejected=true;}
 if(!rejected)throw new Exception("unbounded layers accepted");Console.WriteLine("PASS layer bound enforced");
 right.Layers=3;mixer.Inputs.Clear();
 for(int i=0;i<512;i++)mixer.Inputs.Add(new Playable{N=new Node()});
 rejected=false;try{Controllers(intended);}catch(InvalidOperationException ex){rejected=ex.Message.Contains("actualCount=513")&&ex.Message.Contains("limit=512");}
 if(!rejected)throw new Exception("external traversal bound or precise count diagnostic missing");
 Console.WriteLine("PASS external node bound preserves actualCount and limit");return 0;
}
'''
    cs = tmp_path / "GraphProbe.cs"
    cs.write_text(seam + "class Probe {" + selected + runner + "}", encoding="utf-8")
    dll = tmp_path / "GraphProbe.dll"
    dotnet = str(base / "dotnet.exe")
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")] + [str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    (tmp_path / "GraphProbe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    result = subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == 5

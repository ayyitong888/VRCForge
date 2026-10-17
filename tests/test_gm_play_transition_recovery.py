"""Production coordinator replay; Unity identity observations are explicit inputs.

This is an offline protocol regression, not a Unity domain-reload acceptance.
Set VRCFORGE_GM_REPLAY_DIR to a retained runtime evidence directory to replay
the actual pre/post-Play identities instead of the small synthetic identities.
"""
import ast
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import agent_approval_transactions as transactions
from execution_target import execution_target_digest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "Assets/VRCForge/Editor/GestureManagerRuntimeTool.cs"


def identities():
    evidence = os.environ.get("VRCFORGE_GM_REPLAY_DIR")
    if evidence:
        directory = Path(evidence)
        start = json.loads((directory / "enter-bound.json").read_text(encoding="utf-8"))
        recovery = start["outcome"]["recovery"]
        current = json.loads((directory / "initial0.request.json").read_text(encoding="utf-8"))["arguments"]["executionTarget"]
        status = json.loads((directory / "status-after-enter.json").read_text(encoding="utf-8"))["result"]
        return recovery, current, status
    source = {"schema": "vrcforge.execution_target.v1", "scope": "avatar",
              "namespace": "source", "project": {"root": "C:/Project", "projectId": "project"},
              "editor": {"unityPid": 10, "processStartTime": "20", "coreInstanceId": "old"},
              "scene": {"assetPath": "Assets/Main.unity", "guid": "scene", "digest": "digest"},
              "avatar": {"globalObjectId": "source-avatar", "exactHierarchyPath": "Avatar"}}
    current = copy.deepcopy(source)
    current["editor"]["coreInstanceId"] = "new"
    current["avatar"]["globalObjectId"] = "runtime-avatar"
    current["namespace"] = "runtime"
    recovery = {"id": "incident", "targetTool": "vrcforge_gesture_manager_enter_play_mode",
                "status": "applying", "resolution": "write_pending", "avatarPath": "Avatar",
                "projectRoot": "C:/Project", "executionTarget": source,
                "resultSummary": {"enterPlayModePending": True, "isPlayMode": False,
                                  "moduleConnected": False, "commitState": "enter_play_mode_requested"}}
    status = {"ok": True, "isPlayMode": True, "enterPlayModePending": False,
              "managers": [{"managerPath": "GM", "avatarPath": "Avatar", "moduleConnected": True}]}
    return recovery, current, status


STUBS = r'''
using System; using System.IO; using System.Linq; using System.Collections.Generic;
using Newtonsoft.Json.Linq;
public class GameObject { public string Id; public static implicit operator bool(GameObject x) => x != null; }
public static class GlobalObjectId { public static string GetGlobalObjectIdSlow(GameObject x) => x.Id; }
public enum PlayModeStateChange { EnteredPlayMode, EnteredEditMode }
public static class EditorApplication {
 public static bool isPlaying; public static event Action<PlayModeStateChange> playModeStateChanged;
 public static Action delayCall;
 public static void Enter() { isPlaying=true; playModeStateChanged?.Invoke(PlayModeStateChange.EnteredPlayMode); var a=delayCall; delayCall=null; a?.Invoke(); }
}
public static class SessionState {
 static Dictionary<string,string> values=new Dictionary<string,string>();
 public static string GetString(string k,string d)=>values.ContainsKey(k)?values[k]:d;
 public static void SetString(string k,string v)=>values[k]=v;
 public static bool GetBool(string k,bool d)=>GetString(k,d.ToString())=="True";
 public static void SetBool(string k,bool v)=>SetString(k,v.ToString());
 public static void EraseString(string k)=>values.Remove(k);
}
public class VRCForgeToolResult { public object Payload; public bool IsSuccessful=true; }
public static class ExecutionTargetInspector {
 public static JObject Target;
 public static object HandleCommand(JObject p) {
  if((string)p["avatarGlobalObjectId"]!=(string)Target["avatar"]["globalObjectId"]) throw new Exception("wrong object");
  return new VRCForgeToolResult {Payload=Target};
 }
}
public static class GestureManagerRuntimeBridge {
 public class ManagerBinding { public object Module=new object(); public GameObject Avatar; public string ManagerPath; public string AvatarPath; }
 public static ManagerBinding Binding;
 public static bool TryConnect(string m,string a,out ManagerBinding b,out string e) {b=Binding;e="";return b.ManagerPath==m && b.AvatarPath==a;}
}
'''
MAIN = r'''
public class Probe {
 public static void Main(string[] args) {
  var input=JObject.Parse(File.ReadAllText(args[0]));
  var source=(JObject)input["source"]; var current=(JObject)input["current"];
  var path=(string)source["avatar"]["exactHierarchyPath"]; var manager=(string)input["manager"];
  ExecutionTargetInspector.Target=source;
  var id=GestureManagerPlayModeCoordinator.Prepare(manager,path,new GameObject {Id=(string)source["avatar"]["globalObjectId"]});
  ExecutionTargetInspector.Target=current;
  var binding=new GestureManagerRuntimeBridge.ManagerBinding {ManagerPath=manager,AvatarPath=path,Avatar=new GameObject {Id=(string)current["avatar"]["globalObjectId"]}};
  GestureManagerRuntimeBridge.Binding=binding;
  EditorApplication.Enter();
  Console.WriteLine(new JObject { ["id"]=id, ["pending"]=GestureManagerPlayModeCoordinator.Pending,
   ["error"]=GestureManagerPlayModeCoordinator.LastError,
   ["transition"]=GestureManagerPlayModeCoordinator.ReadTransition(new[]{binding}) }.ToString(Newtonsoft.Json.Formatting.None));
 }
}
'''


def compile_coordinator(output):
    dotnet = shutil.which("dotnet")
    root = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((root / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((root / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs:
        pytest.skip(".NET compiler and reference pack unavailable")
    compiler = compilers[-1]
    dependency = compiler.parents[2] / "Newtonsoft.Json.dll"
    source = CORE.read_text(encoding="utf-8")
    start = source.index("internal static class GestureManagerPlayModeCoordinator")
    end = source.index("\n    [VRCForgeCommand(", start)
    program = STUBS + source[start:end] + MAIN
    output.mkdir(parents=True, exist_ok=True)
    cs = output / "Probe.cs"
    cs.write_text(program, encoding="utf-8")
    dll = output / "Probe.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{item}" for item in refs[-1].glob("*.dll")]
    command += [f"-r:{dependency}", str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(dependency, output / dependency.name)
    (output / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}))
    return dotnet, dll


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    return compile_coordinator(tmp_path_factory.mktemp("gm-transition-core"))


def run_core(compiled, tmp_path, source, current, manager):
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"source": source, "current": current, "manager": manager}))
    result = subprocess.run([compiled[0], str(compiled[1]), str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def reconcile(recovery, current, status, monkeypatch, method=None):
    saved = []
    project_key = lambda value: os.path.normcase(str(Path(value).resolve()))
    service = SimpleNamespace(_project_lock_key=project_key,
        _entry_project_key=lambda value: project_key(value["projectRoot"]),
        _ports=SimpleNamespace(checkpoint=SimpleNamespace(active_apply_recoveries=lambda: [recovery])),
        _finish_apply_recovery=lambda value, **kw: saved.append(kw) or kw)
    # Only the OS/process authority is replaced in this offline replay. The
    # Core transition and product reconciliation methods execute unchanged.
    monkeypatch.setattr(transactions, "validate_runtime_execution_target", lambda target, **kw: target)
    method = method or transactions.AgentApprovalTransactionService.reconcile_gesture_manager_pending_recovery
    result = method(service, {"projectPath": recovery["projectRoot"], "avatarPath": recovery["avatarPath"],
                              "executionTarget": current}, status)
    return result


def test_real_coordinator_receipt_closes_reloaded_identity(compiled, tmp_path, monkeypatch):
    recovery, current, status = identities()
    assert not reconcile(recovery, current, status, monkeypatch)  # Old evidence has no continuity proof.
    produced = run_core(compiled, tmp_path, recovery["executionTarget"], current, status["managers"][0]["managerPath"])
    assert produced["pending"] is False and not produced["error"]
    recovery["resultSummary"]["playModeTransitionId"] = produced["id"]
    status["playModeTransition"] = produced["transition"]
    status["managers"][0]["avatarGlobalObjectId"] = current["avatar"]["globalObjectId"]
    assert reconcile(recovery, current, status, monkeypatch)
    before = os.environ.get("VRCFORGE_GM_BEFORE_SOURCE")
    if before:
        tree = ast.parse(Path(before).read_text(encoding="utf-8"))
        cls = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "AgentApprovalTransactionService")
        method = next(item for item in cls.body if isinstance(item, ast.FunctionDef) and item.name == "reconcile_gesture_manager_pending_recovery")
        scope = dict(vars(transactions))
        exec(compile(ast.Module(body=[method], type_ignores=[]), before, "exec"), scope)
        assert not reconcile(recovery, current, status, monkeypatch, scope[method.name])
    (tmp_path / "replay.json").write_text(json.dumps({"core": produced, "sourceDigest": execution_target_digest(recovery["executionTarget"]), "currentDigest": execution_target_digest(current)}, indent=2))


@pytest.mark.parametrize("section,key", [("project", "projectId"), ("editor", "unityPid"), ("editor", "processStartTime"), ("scene", "guid"), ("avatar", "exactHierarchyPath")])
def test_core_rejects_unrelated_transition(compiled, tmp_path, section, key):
    recovery, current, status = identities()
    current[section][key] = "different"
    produced = run_core(compiled, tmp_path, recovery["executionTarget"], current, status["managers"][0]["managerPath"])
    assert produced["transition"] is None


@pytest.mark.parametrize("field,value", [("enterPlayModePending", True), ("isPlayMode", False), ("enterPlayModeErrorCode", "failed"), ("ok", False)])
def test_backend_rejects_nonterminal_readback(compiled, tmp_path, monkeypatch, field, value):
    recovery, current, status = identities()
    produced = run_core(compiled, tmp_path, recovery["executionTarget"], current, status["managers"][0]["managerPath"])
    recovery["resultSummary"]["playModeTransitionId"] = produced["id"]
    status["playModeTransition"] = produced["transition"]
    status["managers"][0]["avatarGlobalObjectId"] = current["avatar"]["globalObjectId"]
    status[field] = value
    assert not reconcile(recovery, current, status, monkeypatch)


@pytest.mark.parametrize("change", ["transition", "source", "manager", "disconnected", "object", "authority"])
def test_backend_rejects_unrelated_completion(compiled, tmp_path, monkeypatch, change):
    recovery, current, status = identities()
    produced = run_core(compiled, tmp_path, recovery["executionTarget"], current, status["managers"][0]["managerPath"])
    recovery["resultSummary"]["playModeTransitionId"] = produced["id"]
    status["playModeTransition"] = produced["transition"]
    status["managers"][0]["avatarGlobalObjectId"] = current["avatar"]["globalObjectId"]
    if change == "transition":
        recovery["resultSummary"]["playModeTransitionId"] = "another-transition"
    elif change == "source":
        recovery["executionTarget"]["avatar"]["globalObjectId"] = "another-source"
    elif change == "manager":
        status["managers"][0]["managerPath"] = "AnotherManager"
    elif change == "disconnected":
        status["managers"][0]["moduleConnected"] = False
    elif change == "object":
        status["managers"][0]["avatarGlobalObjectId"] = "another-runtime-object"
    else:
        # A changed process descriptor cannot be hidden by a valid old receipt.
        def denied(*args, **kwargs):
            raise transactions.ExecutionTargetError("editor_identity_mismatch", "changed")
        saved = []
        monkeypatch.setattr(transactions, "validate_runtime_execution_target", denied)
        key = lambda value: os.path.normcase(str(Path(value).resolve()))
        service = SimpleNamespace(_project_lock_key=key, _entry_project_key=lambda row: key(row["projectRoot"]),
            _ports=SimpleNamespace(checkpoint=SimpleNamespace(active_apply_recoveries=lambda: [recovery])),
            _finish_apply_recovery=lambda *args, **kwargs: saved.append(kwargs))
        assert transactions.AgentApprovalTransactionService.reconcile_gesture_manager_pending_recovery(service,
            {"projectPath": recovery["projectRoot"], "avatarPath": recovery["avatarPath"]}, status) == []
        assert not saved
        return
    assert not reconcile(recovery, current, status, monkeypatch)

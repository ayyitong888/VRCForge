"""Run the production FX graph collector against small Unity API stubs."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _method(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


STUBS = r'''using System; using System.Linq; using System.Collections.Generic;
namespace UnityEngine {
 public class Object { public string name; }
 public class Motion:Object {}
 public class AnimationClip:Motion { public float length; public float frameRate; }
 public class BlendTree:Motion { public ChildMotion[] children=new ChildMotion[0]; }
 public struct ChildMotion { public Motion motion; }
 public class AnimatorState:Object { public Motion motion; public float speed; public bool writeDefaultValues; public AnimatorStateTransition[] transitions=new AnimatorStateTransition[0]; }
 public class AnimatorStateMachine:Object {
  public ChildAnimatorState[] states=new ChildAnimatorState[0]; public ChildAnimatorStateMachine[] stateMachines=new ChildAnimatorStateMachine[0];
  public AnimatorState defaultState; public AnimatorTransition[] entryTransitions=new AnimatorTransition[0]; public AnimatorStateTransition[] anyStateTransitions=new AnimatorStateTransition[0];
  public Dictionary<AnimatorStateMachine,AnimatorTransition[]> machineTransitionsByTarget=new Dictionary<AnimatorStateMachine,AnimatorTransition[]>();
  public AnimatorTransition[] GetStateMachineTransitions(AnimatorStateMachine target)=>machineTransitionsByTarget.ContainsKey(target)?machineTransitionsByTarget[target]:new AnimatorTransition[0];
 }
 public struct ChildAnimatorState { public AnimatorState state; }
 public struct ChildAnimatorStateMachine { public AnimatorStateMachine stateMachine; }
 public class AnimatorTransitionBase:Object { public AnimatorState destinationState; public AnimatorStateMachine destinationStateMachine; public AnimatorCondition[] conditions=new AnimatorCondition[0]; public bool isExit,mute,solo; }
 public class AnimatorStateTransition:AnimatorTransitionBase { public bool hasExitTime; public float exitTime,duration; public bool canTransitionToSelf,orderedInterruption; public TransitionInterruptionSource interruptionSource; }
 public class AnimatorTransition:AnimatorTransitionBase {}
 public struct AnimatorCondition { public string parameter; public AnimatorConditionMode mode; public float threshold; }
 public enum AnimatorConditionMode { If, Equals, NotEqual, Greater, Less }
 public enum TransitionInterruptionSource { None, Source, Destination, SourceThenDestination, DestinationThenSource }
}
namespace UnityEditor { using UnityEngine; public static class AssetDatabase { public static string GetAssetPath(Object value)=>value == null ? "" : "Assets/" + value.name + ".anim"; } }
'''


RUNNER = r'''
class Probe {
 static int failures; static void Check(bool ok,string label){Console.WriteLine((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 static AnimatorStateTransition Edge(AnimatorState destination, bool exit=false)=>new AnimatorStateTransition{destinationState=destination,isExit=exit,conditions=new[]{new AnimatorCondition{parameter="衣柜",mode=AnimatorConditionMode.Equals,threshold=1}}};
 public static int Main(){
  var dupA=new AnimatorState{name="Dup"}; var dupB=new AnimatorState{name="Dup"}; var outA=new AnimatorState{name="Out"}; var outB=new AnimatorState{name="Out"};
  var a=new AnimatorStateMachine{name="A",states=new[]{new ChildAnimatorState{state=dupA},new ChildAnimatorState{state=outA}},defaultState=dupA};
  var b=new AnimatorStateMachine{name="B",states=new[]{new ChildAnimatorState{state=dupB},new ChildAnimatorState{state=outB}},defaultState=dupB};
  var cross=Edge(outB); cross.mute=true; cross.solo=true; dupA.transitions=new[]{Edge(outA),Edge(null,true),cross}; dupB.transitions=new[]{Edge(outB)};
  var root=new AnimatorStateMachine{name="Root",stateMachines=new[]{new ChildAnimatorStateMachine{stateMachine=a},new ChildAnimatorStateMachine{stateMachine=b}}};
  root.entryTransitions=new[]{new AnimatorTransition{destinationStateMachine=a}};
  root.machineTransitionsByTarget[a]=new[]{new AnimatorTransition{destinationStateMachine=b}};
  var states=new List<StateItem>(); var transitions=new List<TransitionItem>(); var machines=new List<StateMachineItem>(); var entries=new List<TransitionItem>(); var machineEdges=new List<TransitionItem>();
  var statePaths=new Dictionary<AnimatorState,string>(); var machinePaths=new Dictionary<AnimatorStateMachine,string>(); BuildStatePathIndex(root,"",statePaths,machinePaths);
  ScanStateMachine(root,"FX","",states,transitions,machines,entries,machineEdges,statePaths,machinePaths,new Dictionary<string,ClipItem>(),new HashSet<string>());
  Check(states.Any(x=>x.state_path=="A/Dup")&&states.Any(x=>x.state_path=="B/Dup"),"same-name states retain complete paths");
  Check(machines.Any(x=>x.state_machine_path=="A"&&x.default_state_path=="A/Dup")&&machines.Any(x=>x.state_machine_path=="B"&&x.default_state_path=="B/Dup"),"default states retain machine paths");
  Check(entries.Count==1&&entries[0].transition_kind=="entry"&&entries[0].to_state_machine_path=="A","entry edge is separately observable");
  Check(machineEdges.Count==1&&machineEdges[0].from_state_machine_path=="A"&&machineEdges[0].to_state_machine_path=="B"&&machineEdges[0].transition_kind=="state_machine","machine edge keeps its child source path");
  Check(transitions.Count==4&&transitions.Any(x=>x.is_exit&&x.transition_kind=="state"),"exit edge retains legacy transition count and exit flag");
  Check(transitions.Any(x=>x.from_state_path=="A/Dup"&&x.to_state_path=="A/Out"),"state edge resolves exact source and destination paths");
  Check(transitions.Any(x=>x.from_state_path=="A/Dup"&&x.to_state_path=="B/Out"&&x.mute&&x.solo),"cross-machine state edge and transition flags are observable");
  return failures;
 }
}
'''


def test_production_fx_graph_scan_runtime(tmp_path: Path) -> None:
    dotnet_root = Path(os.environ.get("DOTNET_ROOT") or (Path.home() / "AppData/Local/Programs/dotnet8"))
    dotnet = dotnet_root / "dotnet.exe"
    compilers = sorted((dotnet_root / "sdk").glob("8.*/Roslyn/bincore/csc.dll"))
    refs = sorted((dotnet_root / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))
    if not dotnet.exists() or not compilers or not refs:
        pytest.skip(".NET 8 SDK/reference pack required")
    source = (ROOT / "Assets/VRCForge/Editor/ComponentTools.cs").read_text(encoding="utf-8-sig")
    selected = "\n".join(_method(source, signature) for signature in (
        "private static void ScanStateMachine", "private static void BuildStatePathIndex",
        "private static TransitionItem BuildTransitionItem", "private static string FormatInterruptionSource",
        "private static List<AnimationClip> ReadMotionClips"))
    classes = r'''class StateItem { public string name,state_path,motion_name,motion_type; public float speed; public bool write_default_values; public List<string> clip_paths; }
class StateMachineItem { public string name,state_machine_path,default_state,default_state_path; }
    class TransitionItem { public string layer,from_state,to_state,to_state_machine,from_state_path,from_state_machine_path,to_state_path,to_state_machine_path,transition_kind,interruption_source; public bool is_exit,mute,solo,has_exit_time,can_transition_to_self,ordered_interruption; public float exit_time,duration; public List<ConditionItem> conditions; }
class ConditionItem { public string parameter,mode; public float threshold; }
class ClipItem { public string name,asset_path; public float length,frame_rate; public List<string> used_by_states; }
'''
    program = "using UnityEngine; using UnityEditor;" + STUBS + "class Probe {" + classes + selected + RUNNER.split("class Probe {", 1)[1]
    cs = tmp_path / "Probe.cs"; cs.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [str(dotnet), str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{path}" for path in refs[-1].glob("*.dll")] + [str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"net8.0","framework":{"name":"Microsoft.NETCore.App","version":"8.0.0"}}}), encoding="utf-8")
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == 7, result.stdout

"""Execute the production WardrobeScanner graph collectors against explicit Unity stubs."""
from __future__ import annotations

import json
import os
import shutil
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


STUBS = r'''using System; using System.Linq; using System.Collections.Generic; using UnityEngine; using UnityEditor;
namespace UnityEngine { public class Object { public string name; } public class GameObject:Object {} public class AnimationClip:Object {}
public class AnimatorState:Object { public Object motion; public AnimatorStateTransition[] transitions=new AnimatorStateTransition[0]; public bool writeDefaultValues; }
public class AnimatorStateTransition:Object { public AnimatorState destinationState; public AnimatorCondition[] conditions; }
public struct AnimatorCondition { public AnimatorConditionMode mode; public string parameter; public float threshold; }
public enum AnimatorConditionMode { If,Equals,NotEqual,Greater,Less }
public struct ChildAnimatorState { public AnimatorState state; } public struct ChildAnimatorStateMachine { public AnimatorStateMachine stateMachine; }
public class AnimatorStateMachine:Object { public ChildAnimatorState[] states=new ChildAnimatorState[0]; public ChildAnimatorStateMachine[] stateMachines=new ChildAnimatorStateMachine[0]; public AnimatorStateTransition[] anyStateTransitions=new AnimatorStateTransition[0]; }
public static class Mathf { public static int RoundToInt(float value)=>(int)Math.Round(value); public static float Clamp01(float value)=>value<0?0:value>1?1:value; } }
namespace UnityEditor { using UnityEngine; public static class AssetDatabase { public static string GetAssetPath(Object value)=>value==null?"":"clip"; }
public struct EditorCurveBinding { public Type type; public string propertyName,path; } public struct Keyframe { public float value; }
public class AnimationCurve { public Keyframe[] keys=new Keyframe[0]; public int length=>keys.Length; }
public static class AnimationUtility { public static EditorCurveBinding[] GetCurveBindings(AnimationClip clip)=>new EditorCurveBinding[0]; public static AnimationCurve GetEditorCurve(AnimationClip clip,EditorCurveBinding binding)=>null; } }
'''


RUNNER = r'''
class Probe { class ParamInfo { public string name; public int defaultValue; public bool saved,networkSynced; } class MenuToggle { public string menuName,menuPath,parameterName,controlType; public int value; }
class FxLayerInfo { public string name,defaultStateName; public List<FxState> states; public List<AnyStateEquals> anyStateEquals,stateEquals; }
class FxState { public string name,statePath,motionName,clipPath; public bool writeDefaults; public List<string> onObjects,offObjects; }
class AnyStateEquals { public string stateName,destinationStatePath,parameter,sourceStateName,sourceStatePath,transitionType; public int value,conditionCount; public List<string> conditionParameters; }
static int failures; static void Check(bool ok,string label){Console.WriteLine((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
static AnimatorCondition Eq(string p,int v){return new AnimatorCondition{mode=AnimatorConditionMode.Equals,parameter=p,threshold=v};}
public static int Main(){
 var a=new AnimatorState{name="Dup"}; var b=new AnimatorState{name="Dup"}; var d1=new AnimatorState{name="Out1"}; var d2=new AnimatorState{name="Out2"}; var d3=new AnimatorState{name="Out3"};
 var m1=new AnimatorStateMachine{name="A",states=new[]{new ChildAnimatorState{state=a},new ChildAnimatorState{state=d1}}}; var m2=new AnimatorStateMachine{name="B",states=new[]{new ChildAnimatorState{state=b},new ChildAnimatorState{state=d2}}};
 var root=new AnimatorStateMachine{name="Root",stateMachines=new[]{new ChildAnimatorStateMachine{stateMachine=m1},new ChildAnimatorStateMachine{stateMachine=m2}}};
 var states=new List<FxState>(); CollectStates(root,states,"root"); Check(states.Count==4&&states.Select(x=>x.statePath).Distinct().Count()==4,"duplicate state names use unique paths");
 a.transitions=new[]{new AnimatorStateTransition{destinationState=d1,conditions=new[]{Eq("衣柜",1)}}}; b.transitions=new[]{new AnimatorStateTransition{destinationState=d2,conditions=new[]{Eq("衣柜",2)}}};
 var ordinary=new List<AnyStateEquals>(); CollectStateEquals(root,ordinary,"root"); Check(ordinary.Count==2&&ordinary.All(x=>x.transitionType=="state")&&ordinary.Select(x=>x.destinationStatePath).Distinct().Count()==2,"ordinary transitions preserve exact destinations");
 m1.states=new[]{new ChildAnimatorState{state=a},new ChildAnimatorState{state=d1},new ChildAnimatorState{state=d3}}; a.transitions=new[]{new AnimatorStateTransition{destinationState=d1,conditions=new[]{Eq("衣柜",5)}},new AnimatorStateTransition{destinationState=d3,conditions=new[]{Eq("衣柜",5)}}}; ordinary=new List<AnyStateEquals>(); CollectStateEquals(root,ordinary,"root"); Check(ordinary.Count(x=>x.value==5)==2&&ordinary.Where(x=>x.value==5).Select(x=>x.destinationStatePath).Distinct().Count()==2,"same value keeps multiple destinations");
 b.transitions=new[]{new AnimatorStateTransition{destinationState=d1,conditions=new[]{Eq("衣柜",6)}}}; ordinary=new List<AnyStateEquals>(); CollectStateEquals(root,ordinary,"root"); Check(ordinary.Any(x=>x.value==6&&string.IsNullOrWhiteSpace(x.destinationStatePath)),"cross-machine destination stays unresolved");
 a.transitions=new[]{new AnimatorStateTransition{destinationState=d1,conditions=new[]{Eq("衣柜",3),Eq("Other",1)}}}; ordinary=new List<AnyStateEquals>(); CollectStateEquals(root,ordinary,"root"); var and=ordinary.First(x=>x.value==3); Check(and.conditionCount==2&&and.conditionParameters.Contains("Other"),"AND conditions remain attached");
 m2.anyStateTransitions=new[]{new AnimatorStateTransition{destinationState=d2,conditions=new[]{Eq("衣柜",4)}}}; var any=new List<AnyStateEquals>(); CollectAnyStateEquals(root,any,"root"); Check(any.Count==1&&any[0].transitionType=="any_state"&&any[0].destinationStatePath.EndsWith("/Out2"),"nested AnyState remains recognized");
 var p=new ParamInfo{name="衣柜"}; var layer=new FxLayerInfo{name="Wardrobe",states=new List<FxState>{new FxState{name="Out1",statePath="s1",onObjects=new List<string>{"shirt"},offObjects=new List<string>()},new FxState{name="Out2",statePath="s2",onObjects=new List<string>{"coat"},offObjects=new List<string>()}},anyStateEquals=new List<AnyStateEquals>(),stateEquals=new List<AnyStateEquals>{new AnyStateEquals{parameter="衣柜",value=1,destinationStatePath="s1",stateName="Out1",transitionType="state",conditionCount=1},new AnyStateEquals{parameter="衣柜",value=2,destinationStatePath="s2",stateName="Out2",transitionType="state",conditionCount=1}}}; var strict=new List<object>(); var loose=new List<object>(); var ranked=BuildWardrobes(new List<ParamInfo>{p},new List<MenuToggle>{new MenuToggle{parameterName="衣柜",value=1,menuName="One"},new MenuToggle{parameterName="衣柜",value=2,menuName="Two"}},new List<FxLayerInfo>{layer},new List<object>(),loose); Check(ranked.Count==1,"ordinary two-value wardrobe remains strict");
 layer.stateEquals[0].conditionCount=2; var andCandidates=new List<object>(); ranked=BuildWardrobes(new List<ParamInfo>{p},new List<MenuToggle>{new MenuToggle{parameterName="衣柜",value=1,menuName="One"},new MenuToggle{parameterName="衣柜",value=2,menuName="Two"}},new List<FxLayerInfo>{layer},andCandidates,new List<object>()); Check(ranked.Count==0&&andCandidates.Count==1,"AND-gated wardrobe cannot be strict"); layer.stateEquals[0].conditionCount=1;
 layer.states.Add(new FxState{name="Out3",statePath="s3",onObjects=new List<string>{"dress"},offObjects=new List<string>()}); layer.stateEquals.Add(new AnyStateEquals{parameter="衣柜",value=1,destinationStatePath="s3",stateName="Out3",transitionType="state",conditionCount=1}); loose=new List<object>(); var candidates=new List<object>(); ranked=BuildWardrobes(new List<ParamInfo>{p},new List<MenuToggle>{new MenuToggle{parameterName="衣柜",value=1,menuName="One"},new MenuToggle{parameterName="衣柜",value=2,menuName="Two"}},new List<FxLayerInfo>{layer},candidates,loose); var candidate=(dynamic)candidates[0]; var controls=(System.Collections.IEnumerable)candidate.controls; object first=null; foreach(var item in controls){first=item;break;} var fxCandidates=(System.Collections.IEnumerable)first.GetType().GetProperty("fxCandidates").GetValue(first); int count=0; foreach(var item in fxCandidates)count++; Check(ranked.Count==0&&count==2,"same-value destinations become candidate with all clips");
 layer.stateEquals.RemoveAt(2); layer.stateEquals.Add(new AnyStateEquals{parameter="衣柜",value=2,destinationStatePath="",stateName="Missing",transitionType="state",conditionCount=1}); loose=new List<object>(); candidates=new List<object>(); ranked=BuildWardrobes(new List<ParamInfo>{p},new List<MenuToggle>{new MenuToggle{parameterName="衣柜",value=1,menuName="One"},new MenuToggle{parameterName="衣柜",value=2,menuName="Two"}},new List<FxLayerInfo>{layer},candidates,loose); Check(ranked.Count==0,"unresolved destination cannot be strict"); return failures; }
}
'''


def test_production_wardrobe_collectors_runtime(tmp_path: Path) -> None:
    base = Path(os.environ.get("DOTNET_ROOT") or (Path.home() / "AppData/Local/Programs/dotnet8"))
    dotnet = base / "dotnet.exe"
    compilers = sorted((base / "sdk").glob("8.*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))
    if not dotnet.exists() or not compilers or not refs:
        pytest.skip(".NET 8 SDK/reference pack required")
    source = (ROOT / "Assets/VRCForge/Editor/WardrobeScanner.cs").read_text(encoding="utf-8-sig")
    selected = "\n".join(_method(source, signature) for signature in (
        "private static string BuildStatePath", "private static string BuildMachinePath",
        "private static string FindStatePath", "private static void CollectStates",
        "private static List<string> ConditionParameters", "private static void CollectAnyStateEquals",
        "private static void CollectStateEquals", "private static void ReadClipToggles", "private static List<object> BuildWardrobes",
        "private static bool HasWardrobeKeyword", "private static bool LooksLikeDisableOnlyControl"))
    selected = source[source.index("private static readonly string[] WardrobeKeywords"):source.index("private static bool HasWardrobeKeyword")] + selected
    program = STUBS + "class Probe {" + selected + RUNNER.split("class Probe {", 1)[1]
    cs = tmp_path / "Probe.cs"; cs.write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [str(dotnet), str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{path}" for path in refs[-1].glob("*.dll")] + [str(cs)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"net8.0","framework":{"name":"Microsoft.NETCore.App","version":"8.0.0"}}}), encoding="utf-8")
    result = subprocess.run([str(dotnet), str(dll)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == 10

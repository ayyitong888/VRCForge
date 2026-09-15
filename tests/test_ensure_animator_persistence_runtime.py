from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"


def _class_source(raw: str, marker: str) -> str:
    start = raw.index(marker)
    brace = raw.index("{", start)
    depth = 0
    quoted = False
    escaped = False
    verbatim = False
    for pos in range(brace, len(raw)):
        ch = raw[pos]
        if quoted:
            if verbatim and ch == '"' and pos + 1 < len(raw) and raw[pos + 1] == '"':
                continue
            if escaped:
                escaped = False
            elif not verbatim and ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
            continue
        if ch == '"':
            quoted = True
            verbatim = pos > 0 and raw[pos - 1] == '@'
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0: return raw[start:pos + 1]
    raise AssertionError("unterminated class")


def test_ensure_animator_persistence_runtime(tmp_path: Path) -> None:
    dotnet = shutil.which("dotnet")
    pf = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    compilers = sorted((pf / "dotnet/sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((pf / "dotnet/packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    ref = os.environ.get("VRCFORGE_ANIMATOR_GIT_REF", "")
    if ref:
        got = subprocess.run(["git", "show", f"{ref}:Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert got.returncode == 0, got.stderr
        raw = got.stdout
    else: raw = SOURCE.read_text(encoding="utf-8-sig")
    product = "namespace VRCForge.Editor { using System;using System.IO;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;using UnityEngine;using UnityEditor;using UnityEditor.Animations;using VRC.SDK3.Avatars.Components;" + _class_source(raw, "public static class EnsureAnimatorStateTool") + "}"
    stubs = r'''

namespace VRCForge.Core.MCP { public sealed class VRCForgeCommandAttribute:System.Attribute{public VRCForgeCommandAttribute(string toolId){} public string Summary{get;set;}} public sealed class VRCForgeInputAttribute:System.Attribute{public VRCForgeInputAttribute(string d){} public bool IsRequired{get;set;}} public static class VRCForgeToolResult{public static object Failed(string m,object p=null)=>new JObject{{"ok",false},{"status","failed"},{"message",m},{"payload",p==null?null:JObject.FromObject(p)}};public static object Completed(string m,object p)=>new JObject{{"ok",true},{"status","completed"},{"message",m},{"payload",JObject.FromObject(p)}};} }
namespace UnityEngine { public class Object{public string name="";public bool dirty;public int GetInstanceID()=>GetHashCode();} public class Scene{public bool isDirty;public bool isLoaded=true;public string path="Assets/Main.unity";public bool IsValid()=>true;} public class GameObject:Object{public Scene scene=new Scene();} public class Transform:Object{public Transform parent;} public class AnimationClip:UnityEditor.Animations.Motion{} public static class Mathf{public static bool Approximately(float a,float b)=>a==b;} }
namespace UnityEditor { using UnityEngine; using UnityEditor.Animations; public enum ImportAssetOptions{ForceSynchronousImport=1,ForceUpdate=2} public static class EditorUtility{public static bool IsDirty(Object o)=>o!=null&&o.dirty;public static bool IsPersistent(Object o)=>false;public static void SetDirty(Object o){if(o!=null)o.dirty=true;}} public struct GlobalObjectId{public ulong targetObjectId;public static GlobalObjectId GetGlobalObjectIdSlow(Object o)=>new GlobalObjectId{targetObjectId=1};} public static class Undo{public static void RegisterCompleteObjectUndo(Object o,string n){} public static void RegisterCompleteObjectUndo(Object[] o,string n){} public static void RegisterCreatedObjectUndo(Object o,string n){}public static void FlushUndoRecordObjects(){}} public static class Resources{public static VRC.SDK3.Avatars.Components.VRCAvatarDescriptor[] Items=Array.Empty<VRC.SDK3.Avatars.Components.VRCAvatarDescriptor>();public static T[] FindObjectsOfTypeAll<T>()=>Items as T[]??Array.Empty<T>();} public static class AssetDatabase{public static Dictionary<string,Object> Assets=new Dictionary<string,Object>();public static bool SaveFailure;public static bool ReturnNull;public static bool Mismatch;public static int Saves,GlobalSaves;public static bool Refreshed;public static HashSet<string> Imported=new HashSet<string>();public static string GetAssetPath(Object o)=>Assets.FirstOrDefault(x=>ReferenceEquals(x.Value,o)).Key??"";public static void CreateAsset(Object o,string p){Assets[p]=o;}public static Object[] LoadAllAssetsAtPath(string p)=>Assets.TryGetValue(p,out var o)?new[]{o}:Array.Empty<Object>();public static T LoadAssetAtPath<T>(string p) where T:Object=>ReturnNull&&typeof(T)==typeof(AnimationClip)&&(Imported.Contains(p)||Refreshed)?null:(Assets.TryGetValue(p,out var o)?o as T:null);public static void SaveAssetIfDirty(Object o){Saves++;if(SaveFailure)throw new InvalidOperationException("save failed");o.dirty=false;}public static void SaveAssets(){GlobalSaves++;foreach(var a in Assets.Values)a.dirty=false;SaveAssetIfDirty(Assets.Values.FirstOrDefault());}public static void Refresh(){Refreshed=true;}public static void ImportAsset(string p,ImportAssetOptions o){Imported.Add(p);}public static string AssetPathToGUID(string p)=>"guid:"+p;public static bool TryGetGUIDAndLocalFileIdentifier(Object o,out string g,out long id){g=AssetPathToGUID(GetAssetPath(o));id=1;return true;}} }
namespace UnityEditor.SceneManagement { using UnityEngine; public static class EditorSceneManager { public static bool Fail;public static int Saves; public static bool SaveScene(Scene s){Saves++;if(Fail)return false;s.isDirty=false;return true;}public static void MarkSceneDirty(Scene s){s.isDirty=true;} } }
namespace UnityEditor.Animations { using UnityEngine; public enum AnimatorControllerParameterType{Float,Int,Bool,Trigger} public enum AnimatorConditionMode{If,IfNot,Greater,Less,Equals,NotEqual} public class AnimatorControllerParameter{public string name;public AnimatorControllerParameterType type;} public class AnimatorCondition{public AnimatorConditionMode mode;public string parameter;public float threshold;} public class AnimatorStateTransition{public AnimatorState destinationState;public AnimatorCondition[] conditions;public bool hasExitTime,canTransitionToSelf;public float exitTime,duration;public void AddCondition(AnimatorConditionMode m,float t,string p){conditions=new[]{new AnimatorCondition{mode=m,threshold=t,parameter=p}};}} public class ChildAnimatorState{public AnimatorState state;} public class ChildAnimatorStateMachine{public AnimatorStateMachine stateMachine;} public class AnimatorStateMachine:Object{public ChildAnimatorState[] states=Array.Empty<ChildAnimatorState>();public ChildAnimatorStateMachine[] stateMachines=Array.Empty<ChildAnimatorStateMachine>();public AnimatorStateTransition[] anyStateTransitions=Array.Empty<AnimatorStateTransition>();public AnimatorState AddState(string n){var s=new AnimatorState{name=n};states=states.Concat(new[]{new ChildAnimatorState{state=s}}).ToArray();return s;}public AnimatorStateTransition AddAnyStateTransition(AnimatorState s){var t=new AnimatorStateTransition{destinationState=s};anyStateTransitions=anyStateTransitions.Concat(new[]{t}).ToArray();return t;}} public class AnimatorState:Object{public bool writeDefaultValues;public Motion motion;} public class Motion:Object{} public class AnimatorControllerLayer{public string name;public float defaultWeight;public AnimatorStateMachine stateMachine=new AnimatorStateMachine();} public class AnimatorController:Object{public AnimatorControllerParameter[] parameters=Array.Empty<AnimatorControllerParameter>();public AnimatorControllerLayer[] layers=Array.Empty<AnimatorControllerLayer>();public void AddParameter(string n,AnimatorControllerParameterType t){parameters=parameters.Concat(new[]{new AnimatorControllerParameter{name=n,type=t}}).ToArray();}public void AddLayer(string n){layers=layers.Concat(new[]{new AnimatorControllerLayer{name=n}}).ToArray();}public static AnimatorController CreateAnimatorControllerAtPath(string p){var c=new AnimatorController{name=Path.GetFileNameWithoutExtension(p)};UnityEditor.AssetDatabase.CreateAsset(c,p);return c;}} }
namespace VRC.SDK3.Avatars.Components { using UnityEngine; using UnityEditor.Animations; public class VRCAvatarDescriptor:Object{public GameObject gameObject=new GameObject();public Transform transform=new Transform();public string name="Avatar";public CustomAnimLayer[] baseAnimationLayers=Array.Empty<CustomAnimLayer>();public enum AnimLayerType{FX}public struct CustomAnimLayer{public AnimLayerType type;public bool isDefault;public AnimatorController animatorController;}} }
namespace VRCForge.Editor { using UnityEngine;using UnityEditor;using UnityEditor.Animations;using VRC.SDK3.Avatars.Components; public static class SceneObjectCopyCore{public sealed class Evidence{public string Guid="guid";public sealed class FileData{public string Digest="digest";}public FileData File=new FileData();}public static Evidence ReadStableAssetEvidence(string p,string l)=>new Evidence{Guid=AssetDatabase.AssetPathToGUID(p)};public static Evidence ReadStableAssetEvidence(string p,string l,Action<string,string> f)=>ReadStableAssetEvidence(p,l);} public static class GeneratedAssetPaths{public const string Controllers="Controllers",Animations="Animations";public static string ResolveDirectory(string v,string a,string c,bool categorizeExplicit)=>"Assets/Generated/"+c;public static string UniqueAssetPath(string p)=>p;public static string ValidateNewAssetPath(string p)=>p;} public static class AvatarAuthoringCrudCore{public static VRCAvatarDescriptor ResolveAvatarDescriptor(string p)=>Resources.Items[0];public static AnimatorController GetFxController(VRCAvatarDescriptor d){foreach(var l in d.baseAnimationLayers)if(l.animatorController!=null)return l.animatorController;return null;}public static AnimatorController EnsureFxController(VRCAvatarDescriptor d,string dir,string p){var c=GetFxController(d);if(c!=null)return c;c=AnimatorController.CreateAnimatorControllerAtPath(p);d.baseAnimationLayers=new[]{new VRCAvatarDescriptor.CustomAnimLayer{type=VRCAvatarDescriptor.AnimLayerType.FX,animatorController=c}};return c;}public static AnimatorControllerParameterType ParseAnimatorParameterType(string v)=>AnimatorControllerParameterType.Int;public static AnimatorConditionMode ParseConditionMode(string v)=>AnimatorConditionMode.Equals;public static string GetTransformPath(Transform t)=>"Avatar";public static string NormalizeAssetDir(string v,string a,string c)=>"Assets/Generated/"+c;public static string Sanitize(string v,string f)=>f;public static void EnsureAssetFolder(string p){} } public static class ManageFxAnimatorTool{public static int Describes;public static JObject DescribeForBatch(AnimatorController c){Describes++;return new JObject{{"mismatch",AssetDatabase.Mismatch&&Describes>1},{"layers",c.layers.Length},{"parameters",c.parameters.Length}};}} public static class WriteAnimationCurveTool{internal sealed class AssetEditRecovery{public static int Restores;public static bool RestoreResult=true;public void Capture(string p){}public void Begin(){}public void Complete(){}public bool Restore(){Restores++;return RestoreResult;}}} }

'''
    runner = r'''

class Probe {
 static int failures;
 static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
 static JObject Run(bool preview=false,bool saveFailure=false,bool mismatch=false,bool missingClip=false,bool dirty=false,bool restore=true,bool newController=false,bool sceneFailure=false){
  AssetDatabase.Saves=0;AssetDatabase.GlobalSaves=0;AssetDatabase.Refreshed=false;AssetDatabase.Imported.Clear();
  AssetDatabase.SaveFailure=saveFailure;AssetDatabase.Mismatch=mismatch;AssetDatabase.ReturnNull=missingClip;
  VRCForge.Editor.ManageFxAnimatorTool.Describes=0;
  VRCForge.Editor.WriteAnimationCurveTool.AssetEditRecovery.Restores=0;
  VRCForge.Editor.WriteAnimationCurveTool.AssetEditRecovery.RestoreResult=restore;
  UnityEditor.SceneManagement.EditorSceneManager.Saves=0;UnityEditor.SceneManagement.EditorSceneManager.Fail=sceneFailure;
  var controller=new AnimatorController{name="FX",dirty=dirty,layers=new[]{new AnimatorControllerLayer{name="Layer"}}};
  var avatar=new VRCAvatarDescriptor{name="Avatar",transform=new Transform{name="Avatar"},baseAnimationLayers=newController?Array.Empty<VRCAvatarDescriptor.CustomAnimLayer>():new[]{new VRCAvatarDescriptor.CustomAnimLayer{type=VRCAvatarDescriptor.AnimLayerType.FX,animatorController=controller}}};
  UnityEditor.Resources.Items=new[]{avatar};AssetDatabase.Assets.Clear();
  if(!newController)AssetDatabase.Assets["Assets/FX.controller"]=controller;
  AssetDatabase.Assets["Assets/Unrelated.anim"]=new AnimationClip{dirty=true};
  return JObject.FromObject(VRCForge.Editor.EnsureAnimatorStateTool.HandleCommand(new JObject{{"avatarPath","Avatar"},{"layerName","Layer"},{"stateName","State"},{"parameterName","Mode"},{"preview",preview}}));
 }
 static bool RolledBack(JObject result)=>!(bool)result["ok"]&&(string)result["payload"]["commitState"]=="rolled_back"&&VRCForge.Editor.WriteAnimationCurveTool.AssetEditRecovery.Restores==1;
 public static int Main(){
  var preview=Run(preview:true);Check((bool)preview["ok"]&&AssetDatabase.Saves==0,"preview does not save");
  var success=Run();Check((bool)success["ok"]&&(bool?)success["payload"]["persistedReadback"]==true&&AssetDatabase.GlobalSaves==0&&AssetDatabase.Assets["Assets/Unrelated.anim"].dirty,"success saves only target assets");
  var save=Run(saveFailure:true);Check(RolledBack(save),"save failure invokes compensation");
  var mismatch=Run(mismatch:true);Check(RolledBack(mismatch)&&((string)mismatch["message"]).Contains("differs"),"persisted snapshot mismatch fails and compensates");
  var clip=Run(missingClip:true);Check(RolledBack(clip)&&((string)clip["message"]).Contains("clip readback"),"missing persisted clip fails top level");
  var dirty=Run(dirty:true);Check(!(bool)dirty["ok"]&&AssetDatabase.Saves==0&&VRCForge.Editor.WriteAnimationCurveTool.AssetEditRecovery.Restores==0,"dirty controller rejected before mutation");
  var incomplete=Run(saveFailure:true,restore:false);Check(!(bool)incomplete["ok"]&&(string)incomplete["payload"]["commitState"]=="unknown"&&(bool?)incomplete["payload"]["checkpointRecoveryRequired"]==true,"incomplete compensation remains unknown");
  var scene=Run(newController:true,sceneFailure:true);Check(RolledBack(scene)&&UnityEditor.SceneManagement.EditorSceneManager.Saves==1&&((string)scene["message"]).Contains("reference"),"new controller scene save failure compensates");
  var assigned=Run(newController:true);Check((bool)assigned["ok"]&&UnityEditor.SceneManagement.EditorSceneManager.Saves==1,"new controller saves scene reference");
  return failures==0?0:1;
 }
}

'''
    cs=tmp_path/"Probe.cs";cs.write_text("using System;using System.IO;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;using VRCForge.Core.MCP;using UnityEngine;using UnityEditor;using UnityEditor.Animations;using VRC.SDK3.Avatars.Components;"+stubs+product+runner,encoding="utf-8");dll=tmp_path/"Probe.dll";cmd=[dotnet,str(compilers[-1]),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{compilers[-1].parents[2]/'Newtonsoft.Json.dll'}",str(cs)];built=subprocess.run(cmd,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stdout+built.stderr;shutil.copy2(compilers[-1].parents[2]/"Newtonsoft.Json.dll",tmp_path/"Newtonsoft.Json.dll");(tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8");run=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30);assert run.returncode==0,run.stdout+run.stderr;assert run.stdout.count("PASS ")==9,run.stdout

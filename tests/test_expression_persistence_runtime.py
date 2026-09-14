"""Actual menu builder/planner regression; fake asset database only, no Unity writes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method
ROOT=Path(__file__).resolve().parents[1]


def test_expression_persistence_rejects_corrupt_readback_and_scopes_saves(tmp_path):
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1]; newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"

    source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs").read_text(encoding="utf-8")
    helper=source.split("internal static class ExpressionWritePersistence",1)[1].split("internal static class AvatarAuthoringCrudCore",1)[0]
    helper="internal static class ExpressionWritePersistence"+helper
    stubs=r'''
namespace UnityEngine {
 public class Object {public string path;public string json="{}";public bool dirty;}
 public class Transform {public string path="Avatar";}
 public class GameObject {public Scene scene;public T[] GetComponentsInChildren<T>(bool b){return new T[0];}}
 public class Scene {public string path="Assets/Main.unity";public bool isLoaded=true,isDirty;public bool IsValid()=>true;public GameObject[] GetRootGameObjects()=>new GameObject[0];}
}
namespace UnityEditor.SceneManagement {public static class EditorSceneManager {
 public static void MarkSceneDirty(UnityEngine.Scene s){} public static bool SaveScene(UnityEngine.Scene s)=>true;
 
}}
class VRCAvatarDescriptor {public UnityEngine.GameObject gameObject;public UnityEngine.Transform transform;public VRCExpressionsMenu expressionsMenu;public UnityEngine.Object expressionParameters;}
class VRCExpressionsMenu:UnityEngine.Object {public List<Control> controls=new List<Control>();public class Control{public VRCExpressionsMenu subMenu;}}
static class AvatarAuthoringCrudCore {public static string GetTransformPath(UnityEngine.Transform t)=>t.path;}
class GlobalObjectId {public ulong targetObjectId=1;public static GlobalObjectId GetGlobalObjectIdSlow(object o)=>new GlobalObjectId();}
static class Undo {public static void FlushUndoRecordObjects(){}}
[Flags] enum ImportAssetOptions{ForceSynchronousImport=1,ForceUpdate=2}
static class EditorUtility {public static void SetDirty(UnityEngine.Object a){a.dirty=true;}public static bool IsDirty(UnityEngine.Object a)=>a.dirty;}
static class EditorJsonUtility {public static string ToJson(UnityEngine.Object a)=>a.json;}
static class AssetDatabase {
 public static Dictionary<string,UnityEngine.Object> live=new Dictionary<string,UnityEngine.Object>();
 public static Dictionary<string,string> disk=new Dictionary<string,string>();
 public static List<string> saved=new List<string>();public static string corruptPath=null;public static string wrongGuidPath=null;
 public static bool TryGetGUIDAndLocalFileIdentifier(UnityEngine.Object o,out string guid,out long id){guid="guid:"+o.path;id=11400000;return true;}
 public static string GetAssetPath(UnityEngine.Object a)=>a?.path??"";
 public static string AssetPathToGUID(string p)=>"guid:"+p;
 public static T LoadAssetAtPath<T>(string p) where T:UnityEngine.Object=>live.TryGetValue(p,out var a)?a as T:null;
 public static void SaveAssetIfDirty(UnityEngine.Object a){if(a.dirty){disk[a.path]=a.json;saved.Add(a.path);a.dirty=false;}}
 public static void ImportAsset(string p,ImportAssetOptions flags){live[p].json=p==corruptPath?"{\"wrong\":true}":disk[p];}
}
static class SceneObjectCopyCore {
 public class FileInfo {public string Digest="digest";}public class Evidence {public string Guid;public FileInfo File=new FileInfo();}
 public static Evidence ReadStableAssetEvidence(string p,string label,Action<string,string> probe=null)=>new Evidence{Guid=p==AssetDatabase.wrongGuidPath?"wrong":"guid:"+p};
}
'''
    runner=r'''
class Probe {
 static int failures; static void Check(bool b,string n){Console.WriteLine((b?"PASS ":"FAIL ")+n);if(!b)failures++;}
 static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException){return true;}}
 static UnityEngine.Object Make(string p,string j){var o=new UnityEngine.Object{path=p,json=j};AssetDatabase.live[p]=o;AssetDatabase.disk[p]=j;return o;}
 public static int Main(){
  var target=Make("Assets/P.asset","{\"parameters\":[1]}");var neighbor=Make("Assets/Unrelated.mat","{}");neighbor.dirty=true;
  var before=ExpressionWritePersistence.Capture(target);target.json="{\"parameters\":[2]}";
  var receipt=JObject.FromObject(ExpressionWritePersistence.SaveAndVerify(before,target,null,false,false));
  Check(AssetDatabase.saved.SequenceEqual(new[]{target.path})&&neighbor.dirty&&AssetDatabase.disk[neighbor.path]=="{}","only intended asset saved, unrelated dirty asset untouched");
  Check((bool)receipt["persisted"]&&((JArray)receipt["assets"]).Count==1&&(string)receipt["assets"][0]["assetGuid"]=="guid:"+target.path,"verified persisted receipt includes exact GUID/hash state");
  before=ExpressionWritePersistence.Capture(target);target.json="{\"parameters\":[3]}";AssetDatabase.corruptPath=target.path;
  Check(Reject(()=>ExpressionWritePersistence.SaveAndVerify(before,target,null,false,false)),"corrupt imported disk state rejects instead of verified success");
  AssetDatabase.corruptPath=null;target.json="{}";before=ExpressionWritePersistence.Capture(target);AssetDatabase.wrongGuidPath=target.path;
  Check(Reject(()=>ExpressionWritePersistence.SaveAndVerify(before,target,null,false,false)),"changed GUID rejects");AssetDatabase.wrongGuidPath=null;
  var child=new VRCExpressionsMenu{path="Assets/Child.asset",json="{}"};var root=new VRCExpressionsMenu{path="Assets/Root.asset",json="{}"};root.controls.Add(new VRCExpressionsMenu.Control{subMenu=child});
  AssetDatabase.live[root.path]=root;AssetDatabase.live[child.path]=child;AssetDatabase.disk[root.path]="{}";AssetDatabase.disk[child.path]="{}";
  before=ExpressionWritePersistence.Capture(root);root.json="{\"linkChanged\":true}";child.json="{\"name\":\"new\"}";AssetDatabase.saved.Clear();
  ExpressionWritePersistence.SaveAndVerify(before,root,null,false,true,child);
  Check(AssetDatabase.saved.Count==2&&AssetDatabase.saved.Contains(root.path)&&AssetDatabase.saved.Contains(child.path),"changed parent and target submenu both saved");
  before=ExpressionWritePersistence.Capture(root);AssetDatabase.saved.Clear();ExpressionWritePersistence.SaveAndVerify(before,root,null,false,true,child);
  Check(AssetDatabase.saved.SequenceEqual(new[]{child.path}),"no-op still independently verifies exact target, not unrelated root");
  var d=new VRCAvatarDescriptor{gameObject=new UnityEngine.GameObject{scene=new UnityEngine.Scene{isDirty=true}}};
  Check(Reject(()=>ExpressionWritePersistence.RequireCleanSceneForNewReference(d,true)),"new reference rejects dirty scene before mutation");
  ExpressionWritePersistence.RequireCleanSceneForNewReference(d,false);Check(true,"existing asset edit need not save dirty scene");
  var guid=new string('a',32);
  var yaml="%YAML 1.1\n--- !u!114 &42\nMonoBehaviour:\n  expressionsMenu: {fileID: 11400000, guid: "+guid+",\n    type: 2}\n  expressionParameters: {fileID: 0}\n--- !u!114 &43\nMonoBehaviour:\n  expressionsMenu: {fileID: 0}\n";
  var reference=ExpressionWritePersistence.ReadDescriptorReference(yaml,42,"expressionsMenu");
  Check((long)reference["fileID"]==11400000&&(string)reference["guid"]==guid,"serialized exact fileID and multiline asset reference read");
  Check((long)ExpressionWritePersistence.ReadDescriptorReference(yaml,43,"expressionsMenu")["fileID"]==0,"other descriptor independently selected, no reference fallback");
  Check(Reject(()=>ExpressionWritePersistence.ReadDescriptorReference(yaml,44,"expressionsMenu")),"missing exact descriptor rejects");
  Check(Reject(()=>ExpressionWritePersistence.ReadDescriptorReference(yaml+yaml,42,"expressionsMenu")),"duplicate exact descriptor rejects");
  Check(Reject(()=>ExpressionWritePersistence.ReadDescriptorReference(yaml.Replace("&42\n","&42 stripped\n"),42,"expressionsMenu")),"prefab stripped descriptor rejects before unsupported reference write");
  return failures==0?0:1;
 }
}
'''
    program="using System;using System.IO;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;"+stubs+helper+runner
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==13

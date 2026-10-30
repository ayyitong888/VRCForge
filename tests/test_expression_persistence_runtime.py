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
class VRCAvatarDescriptor {public string name="Avatar";public UnityEngine.GameObject gameObject;public UnityEngine.Transform transform;public VRCExpressionsMenu expressionsMenu;public UnityEngine.Object expressionParameters;}
class VRCExpressionsMenu:UnityEngine.Object {public List<Control> controls=new List<Control>();public class Control{public enum ControlType{Toggle,SubMenu} public string name;public ControlType type;public VRCExpressionsMenu subMenu;}}
static class AvatarPrimitiveCrudCore {public static string GetTransformPath(UnityEngine.Transform t)=>t.path;public static string NormalizePath(string p)=>p;public static void EnsureAssetFolder(string p){}}
static class AvatarAuthoringCrudCore {public static string GetTransformPath(UnityEngine.Transform t)=>t.path;}
class GlobalObjectId {public ulong targetObjectId=1;public static GlobalObjectId GetGlobalObjectIdSlow(object o)=>new GlobalObjectId();}
static class Undo {public static void FlushUndoRecordObjects(){}public static void RegisterCompleteObjectUndo(object a,string s){}public static void RegisterCreatedObjectUndo(object a,string s){}}
[Flags] enum ImportAssetOptions{ForceSynchronousImport=1,ForceUpdate=2}
static class EditorUtility {public static void SetDirty(UnityEngine.Object a){a.dirty=true;}public static bool IsDirty(UnityEngine.Object a)=>a.dirty;}
static class EditorJsonUtility {public static string ToJson(UnityEngine.Object a){var menu=a as VRCExpressionsMenu;return menu==null?a.json:Newtonsoft.Json.JsonConvert.SerializeObject(new {json=a.json,controls=menu.controls.Select(c=>new {name=c.name,type=c.type.ToString(),subMenu=c.subMenu?.path})});}}
static class AssetDatabase {
 public static Dictionary<string,UnityEngine.Object> live=new Dictionary<string,UnityEngine.Object>();
 public static Dictionary<string,string> disk=new Dictionary<string,string>();
 public static List<string> saved=new List<string>();public static string corruptPath=null;public static string wrongGuidPath=null;
 public static bool TryGetGUIDAndLocalFileIdentifier(UnityEngine.Object o,out string guid,out long id){guid="guid:"+o.path;id=11400000;return true;}
 public static string GetAssetPath(UnityEngine.Object a)=>a?.path??"";
 public static string AssetPathToGUID(string p)=>"guid:"+p;
 public static T LoadAssetAtPath<T>(string p) where T:UnityEngine.Object=>live.TryGetValue(p,out var a)?a as T:null;
 public static void CreateAsset(UnityEngine.Object a,string p){a.path=p;live[p]=a;disk[p]=EditorJsonUtility.ToJson(a);}
 public static void SaveAssetIfDirty(UnityEngine.Object a){if(a.dirty){disk[a.path]=EditorJsonUtility.ToJson(a);saved.Add(a.path);a.dirty=false;}}
 public static void ImportAsset(string p,ImportAssetOptions flags){if(!(live[p] is VRCExpressionsMenu))live[p].json=p==corruptPath?"{\"wrong\":true}":disk[p];}
}
static class SceneObjectCopyCore {
 public class FileInfo {public string Digest="digest";}public class Evidence {public string Guid;public FileInfo File=new FileInfo();}
 public static Evidence ReadStableAssetEvidence(string p,string label,Action<string,string> probe=null)=>new Evidence{Guid=p==AssetDatabase.wrongGuidPath?"wrong":"guid:"+p};
}
static class ScriptableObject {public static T CreateInstance<T>() where T:new()=>new T();}
'''
    runner=r'''
class Probe {
 static int failures; static void Check(bool b,string n){Console.WriteLine((b?"PASS ":"FAIL ")+n);if(!b)failures++;}
 static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException){return true;}}
 static UnityEngine.Object Make(string p,string j){var o=new UnityEngine.Object{path=p,json=j};AssetDatabase.live[p]=o;AssetDatabase.disk[p]=j;return o;}
 private static VRCExpressionsMenu.Control BuildControl(JObject p,string assetDir,VRCExpressionsMenu.Control existing,Queue<string> plannedMenuPaths)=>new VRCExpressionsMenu.Control{name=p["controlName"]?.ToString(),type=VRCExpressionsMenu.Control.ControlType.SubMenu,subMenu=AssetDatabase.LoadAssetAtPath<VRCExpressionsMenu>(p["subMenuAssetPath"]?.ToString())};
 private static void Reorder(VRCExpressionsMenu menu,JObject p){}
 private static int ResolveControlIndex(VRCExpressionsMenu menu,JObject p)=>0;
__MENU_METHODS__
__MENU_WRITE_WRAPPER__
 public static int Main(){
  var target=Make("Assets/P.asset","{\"parameters\":[1]}");var neighbor=Make("Assets/Unrelated.mat","{}");neighbor.dirty=true;
  var before=ExpressionWritePersistence.Capture(target);target.json="{\"parameters\":[2]}";
  var receipt=JObject.FromObject(ExpressionWritePersistence.SaveAndVerify(before,target,null,false,false,new[]{target.path}));
  Check(AssetDatabase.saved.SequenceEqual(new[]{target.path})&&neighbor.dirty&&AssetDatabase.disk[neighbor.path]=="{}","only intended asset saved, unrelated dirty asset untouched");
  Check((bool)receipt["persisted"]&&((JArray)receipt["assets"]).Count==1&&(string)receipt["assets"][0]["assetGuid"]=="guid:"+target.path,"verified persisted receipt includes exact GUID/hash state");
  before=ExpressionWritePersistence.Capture(target);target.json="{\"parameters\":[3]}";AssetDatabase.corruptPath=target.path;
  Check(Reject(()=>ExpressionWritePersistence.SaveAndVerify(before,target,null,false,false,new[]{target.path})),"corrupt imported disk state rejects instead of verified success");
  AssetDatabase.corruptPath=null;target.json="{}";before=ExpressionWritePersistence.Capture(target);AssetDatabase.wrongGuidPath=target.path;
  Check(Reject(()=>ExpressionWritePersistence.SaveAndVerify(before,target,null,false,false,new[]{target.path})),"changed GUID rejects");AssetDatabase.wrongGuidPath=null;
  var child=new VRCExpressionsMenu{path="Assets/Child.asset",json="{}"};var root=new VRCExpressionsMenu{path="Assets/Root.asset",json="{}"};root.controls.Add(new VRCExpressionsMenu.Control{subMenu=child});
  AssetDatabase.live[root.path]=root;AssetDatabase.live[child.path]=child;AssetDatabase.disk[root.path]="{}";AssetDatabase.disk[child.path]="{}";
  before=ExpressionWritePersistence.Capture(root);root.json="{\"linkChanged\":true}";child.json="{\"name\":\"new\"}";AssetDatabase.saved.Clear();
  ExpressionWritePersistence.SaveAndVerify(before,root,null,false,true,new[]{root.path,child.path},child);
  Check(AssetDatabase.saved.Count==2&&AssetDatabase.saved.Contains(root.path)&&AssetDatabase.saved.Contains(child.path),"changed parent and target submenu both saved");
  before=ExpressionWritePersistence.Capture(root);AssetDatabase.saved.Clear();ExpressionWritePersistence.SaveAndVerify(before,root,null,false,true,new[]{root.path,child.path},child);
  Check(AssetDatabase.saved.SequenceEqual(new[]{child.path}),"no-op still independently verifies exact target, not unrelated root");
  var actualRoot=new VRCExpressionsMenu{path="Assets/ActualRoot.asset"};var actualTarget=new VRCExpressionsMenu{path="Assets/Wardrobe.asset"};var actualLinked=new VRCExpressionsMenu{path="Assets/Accessories.asset"};
  actualRoot.controls.Add(new VRCExpressionsMenu.Control{name="Wardrobe",type=VRCExpressionsMenu.Control.ControlType.SubMenu,subMenu=actualTarget});
  AssetDatabase.live[actualRoot.path]=actualRoot;AssetDatabase.live[actualTarget.path]=actualTarget;AssetDatabase.live[actualLinked.path]=actualLinked;
  AssetDatabase.disk[actualRoot.path]=EditorJsonUtility.ToJson(actualRoot);AssetDatabase.disk[actualTarget.path]=EditorJsonUtility.ToJson(actualTarget);AssetDatabase.disk[actualLinked.path]=EditorJsonUtility.ToJson(actualLinked);
  before=ExpressionWritePersistence.Capture(actualRoot);
  AssetDatabase.saved.Clear();
  var actualResolved=ReplayMenuWrite(actualRoot,"Wardrobe",JObject.Parse(@"{'action':'create','controlName':'Accessories','subMenuAssetPath':'Assets/Accessories.asset'}"),"Assets/Menus",new List<string>(),new[]{actualRoot.path,actualTarget.path},null,false,before);
  Check(AssetDatabase.saved.SequenceEqual(new[]{actualTarget.path})&&AssetDatabase.disk[actualTarget.path]==EditorJsonUtility.ToJson(actualTarget)&&!EditorUtility.IsDirty(actualRoot)&&!EditorUtility.IsDirty(actualTarget),"actual handler submenu create persists target and leaves unchanged root clean");
  var directRoot=new VRCExpressionsMenu{path="Assets/DirectRoot.asset"};var directLinked=new VRCExpressionsMenu{path="Assets/DirectLinked.asset"};
  AssetDatabase.live[directRoot.path]=directRoot;AssetDatabase.live[directLinked.path]=directLinked;AssetDatabase.disk[directRoot.path]=EditorJsonUtility.ToJson(directRoot);AssetDatabase.disk[directLinked.path]=EditorJsonUtility.ToJson(directLinked);
  before=ExpressionWritePersistence.Capture(directRoot);AssetDatabase.saved.Clear();
  ReplayMenuWrite(directRoot,"",JObject.Parse(@"{'action':'create','controlName':'Direct','subMenuAssetPath':'Assets/DirectLinked.asset'}"),"Assets/Menus",new List<string>(),new[]{directRoot.path},null,false,before);
  Check(AssetDatabase.saved.SequenceEqual(new[]{directRoot.path})&&!EditorUtility.IsDirty(directRoot),"actual handler direct root edit persists and leaves root clean");
  var newParentRoot=new VRCExpressionsMenu{path="Assets/NewParentRoot.asset"};
  AssetDatabase.live[newParentRoot.path]=newParentRoot;AssetDatabase.disk[newParentRoot.path]=EditorJsonUtility.ToJson(newParentRoot);
  before=ExpressionWritePersistence.Capture(newParentRoot);AssetDatabase.saved.Clear();
  var actualNewParent=ReplayMenuWrite(newParentRoot,"NewParent",JObject.Parse(@"{'action':'create','controlName':'ChildControl','subMenuAssetPath':'Assets/DirectLinked.asset'}"),"Assets/Menus",new List<string>{"Assets/Menus/NewParent.asset"},new[]{newParentRoot.path,"Assets/Menus/NewParent.asset"},null,false,before);
  Check(AssetDatabase.saved.OrderBy(p=>p).SequenceEqual(new[]{newParentRoot.path,"Assets/Menus/NewParent.asset"}.OrderBy(p=>p))&&!EditorUtility.IsDirty(newParentRoot)&&!EditorUtility.IsDirty(actualNewParent),"actual handler new parent link persists root and new submenu");
  var linked=new VRCExpressionsMenu{path="Assets/AuthorMenu.asset",json="{\"unsaved\":true}",dirty=true};
  var linkRoot=new VRCExpressionsMenu{path="Assets/IndependentMenu.asset",json="{}"};
  AssetDatabase.live[linked.path]=linked;AssetDatabase.disk[linked.path]="{}";
  AssetDatabase.live[linkRoot.path]=linkRoot;AssetDatabase.disk[linkRoot.path]="{}";
  before=ExpressionWritePersistence.Capture(linkRoot);
  linkRoot.controls.Add(new VRCExpressionsMenu.Control{subMenu=linked});linkRoot.json="{\"linked\":true}";
  AssetDatabase.saved.Clear();ExpressionWritePersistence.SaveAndVerify(before,linkRoot,null,false,true,new[]{linkRoot.path},linkRoot);
  Check(AssetDatabase.saved.SequenceEqual(new[]{linkRoot.path})&&linked.dirty&&AssetDatabase.disk[linked.path]=="{}","new reference never saves an existing external menu or its dirty edits");
  before=ExpressionWritePersistence.Capture(linkRoot);AssetDatabase.saved.Clear();
  ExpressionWritePersistence.SaveAndVerify(before,linkRoot,null,false,true,new[]{linkRoot.path},linkRoot);
  Check(AssetDatabase.saved.SequenceEqual(new[]{linkRoot.path})&&linked.dirty,"existing unchanged dirty reference remains unsaved");
  before=ExpressionWritePersistence.Capture(linkRoot);linked.json="{\"unexpectedEdit\":true}";AssetDatabase.saved.Clear();
  Check(Reject(()=>ExpressionWritePersistence.SaveAndVerify(before,linkRoot,null,false,true,new[]{linkRoot.path},linkRoot))&&AssetDatabase.saved.Count==0,"existing referenced object changed outside plan rejects before any save");
  before=ExpressionWritePersistence.Capture(linkRoot);AssetDatabase.saved.Clear();
  Check(Reject(()=>ExpressionWritePersistence.SaveAndVerify(before,linkRoot,null,false,true,new[]{linkRoot.path},linked))&&AssetDatabase.saved.Count==0,"explicit target outside write plan rejects before any save");
  Check(Reject(()=>ExpressionWritePersistence.SaveAndVerify(before,linkRoot,null,false,true,new[]{linked.path},linked))&&AssetDatabase.saved.Count==0,"root outside write plan rejects before any save");
  var newParent=new VRCExpressionsMenu{path="Assets/NewParent.asset",json="{\"child\":true}"};
  var newLeaf=new VRCExpressionsMenu{path="Assets/NewLeaf.asset",json="{}"};
  AssetDatabase.live[newParent.path]=newParent;AssetDatabase.disk[newParent.path]="{}";
  AssetDatabase.live[newLeaf.path]=newLeaf;AssetDatabase.disk[newLeaf.path]="{}";
  before=ExpressionWritePersistence.Capture(linkRoot);linkRoot.json="{\"newParent\":true}";
  linkRoot.controls.Add(new VRCExpressionsMenu.Control{subMenu=newParent});newParent.controls.Add(new VRCExpressionsMenu.Control{subMenu=newLeaf});
  newLeaf.controls.Add(new VRCExpressionsMenu.Control{subMenu=linked});
  AssetDatabase.saved.Clear();ExpressionWritePersistence.SaveAndVerify(before,linkRoot,null,false,true,new[]{linkRoot.path,newParent.path,newLeaf.path},newLeaf);
  Check(AssetDatabase.saved.OrderBy(x=>x).SequenceEqual(new[]{linkRoot.path,newParent.path,newLeaf.path}.OrderBy(x=>x))&&linked.dirty,"all planned newly created levels saved while external reference remains untouched");
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
  d.gameObject.scene.isDirty=false;d.transform=new UnityEngine.Transform();
  var plan=MenuPlanProbe.Plan(null,d);
  Check(((JArray)plan["plannedAssetPaths"]).Values<string>().Contains(d.gameObject.scene.path),"new root plan checkpoints descriptor scene before assigning reference");
  plan=MenuPlanProbe.Plan(linkRoot,d);
  Check(((JArray)plan["plannedAssetPaths"]).Values<string>().SequenceEqual(new[]{linkRoot.path}),"existing root plan keeps newly referenced external menu outside exact write scope");
  AssetDatabase.saved.Clear();
  Check(Reject(()=>ExpressionWritePersistence.SaveAndVerify(before,linkRoot,d,true,true,new[]{linkRoot.path},linkRoot))&&AssetDatabase.saved.Count==0,"unplanned descriptor scene rejects before asset saves");
  return failures==0?0:1;
 }
}
'''
    menu_source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8").split("public static class ManageExpressionMenuTool",1)[1].split("public static class ManageFxAnimatorTool",1)[0]
    handler=menu_source.split("public static object HandleCommand(JObject @params)",1)[1].split("catch (Exception ex)",1)[0]
    menu_methods=method(menu_source,"private static VRCExpressionsMenu ResolveMenu")+method(menu_source,"private static void Apply(string action, VRCExpressionsMenu menu")
    write_start="var plannedMenuPaths = new Queue<string>(newMenuAssetPaths);"
    write_end="var rootAssetPath = AssetDatabase.GetAssetPath(root);"
    menu_write_block=handler.split(write_start,1)[1].split(write_end,1)[0]
    menu_write_wrapper="static VRCExpressionsMenu ReplayMenuWrite(VRCExpressionsMenu root,string normalizedMenuPath,JObject parameters,string assetDir,List<string> newMenuAssetPaths,string[] actualPlanAssetPaths,VRCAvatarDescriptor descriptor,bool rootWasMissing,Dictionary<string,JToken> persistenceBefore){var @params=parameters;var action=@params[\"action\"]?.ToString()??\"\";"+write_start+menu_write_block+"return target;}"
    plan_probe="static class MenuPlanProbe {"+method(menu_source,"private static object BuildPlan")+method(menu_source,"private static VRCExpressionsMenu TryResolveMenu")+'public static JObject Plan(VRCExpressionsMenu root, VRCAvatarDescriptor d)=>JObject.FromObject(BuildPlan("create",d,root,new JObject(),"Assets",root?.path??"Assets/NewRoot.asset",new List<string>()));}'
    program="using System;using System.IO;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;"+stubs+helper+plan_probe+runner.replace("__MENU_METHODS__",menu_methods).replace("__MENU_WRITE_WRAPPER__",menu_write_wrapper)
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==25

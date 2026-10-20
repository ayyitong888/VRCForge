"""Compile and execute the actual Core handler offline; never loads/writes Unity."""
import json, os, shutil, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_reparent_handler_exact_sibling_order_and_rollback(tmp_path):
    source_root=Path(os.environ.get('VRCFORGE_SIBLING_SOURCE_ROOT',str(ROOT)))
    source=(source_root/'Assets/VRCForge/Editor/Generic/UnityGameObjectCrud.cs').read_text(encoding='utf-8-sig')
    start=source.index('    public static class ReparentGameObjectTool')
    end=source.index('    [VRCForgeCommand(',start)
    handler=source[start:end]
    program=r'''
using System; using System.Collections.Generic; using System.Linq; using Newtonsoft.Json; using Newtonsoft.Json.Linq;
class VRCForgeInputAttribute:Attribute {public VRCForgeInputAttribute(string s){} public bool IsRequired{get;set;} }
struct Vector3 { public int value; public static Vector3 zero=>new Vector3(); public static Vector3 one=>new Vector3{value=1}; }
struct Quaternion {public int value; public static Quaternion identity=>new Quaternion{value=1};}
class Scene {public string path="Assets/Main.unity";public int handle=1; public List<Transform> roots=new List<Transform>(); public int rootCount=>roots.Count;}
class GameObject {public string name;public Transform transform; public Scene scene;public GameObject PrefabRoot;public GameObject(string n,Scene s,Transform p=null){name=n;scene=s;transform=new Transform{gameObject=this,localScale=Vector3.one,localRotation=Quaternion.identity};transform.SetParent(p,false);} }
class Transform {
 public GameObject gameObject;public string name=>gameObject.name; public Transform parent;public List<Transform> children=new List<Transform>();public int childCount=>children.Count;
 public Vector3 localPosition,localScale; public Quaternion localRotation;
 public List<Transform> Siblings=>parent==null?gameObject.scene.roots:parent.children;
 public int GetSiblingIndex()=>Siblings.IndexOf(this);
 public void SetParent(Transform p,bool keep){if(parent==p&&Siblings.Contains(this))return;Siblings.Remove(this);parent=p;Siblings.Add(this);}
 public void SetSiblingIndex(int i){var list=Siblings;list.Remove(this);list.Insert(Math.Max(0,Math.Min(i,list.Count)),this);}
 public bool IsChildOf(Transform other){for(var x=parent;x!=null;x=x.parent)if(x==other)return true;return false;}
}
class SavedSceneSnapshot {public string Path,Guid="scene-guid",MetaDigest="meta-digest",MetaIdentity="meta-id",FileDigest,FileIdentity;public int Handle=1;public Scene Scene;}
static class ComponentCrudCore {
 public static Dictionary<string,GameObject> Objects=new Dictionary<string,GameObject>();
 public static GameObject ResolveGameObject(string p){return Objects.Values.Single(x=>GetHierarchyPath(x.transform)==p);}
 public static string NormalizePath(string p)=>(p??"").Trim('/');
 public static string GetHierarchyPath(Transform t)=>t.parent==null?t.name:GetHierarchyPath(t.parent)+"/"+t.name;
}
static class SceneObjectCopyCore {
 public static Scene Current;public static bool CorruptReadback,UnchangedDigest;public static string Initial;
 public static string Digest()=>string.Join("|",Current.roots.Select(t=>t.name+":"+string.Join(",",t.children.Select(c=>c.name))));
 public static SavedSceneSnapshot ResolveSavedScene(string path,string label)=>new SavedSceneSnapshot{Path=path,Scene=Current,FileDigest=UnchangedDigest?Initial:Digest(),FileIdentity=UnchangedDigest?Initial:Digest()};
 public static GameObject ResolveUniqueGameObject(Scene s,string path,string label){var x=ComponentCrudCore.ResolveGameObject(path);if(CorruptReadback&&label=="reparented object"){CorruptReadback=false;x.transform.SetSiblingIndex(1);}return x;}
}
static class PrefabUtility {public static GameObject GetNearestPrefabInstanceRoot(GameObject g)=>g.PrefabRoot;}
static class Undo {public static int ParentCalls,OrderCalls; public static void SetTransformParent(Transform t,Transform p,bool keep,string label){ParentCalls++;t.SetParent(p,keep);}public static void SetSiblingIndex(Transform t,int i,string label){OrderCalls++;t.SetSiblingIndex(i);} public static void FlushUndoRecordObjects(){} }
static class EditorUtility {public static void SetDirty(object x){} }
static class EditorSceneManager {public static bool FailNextSave;public static int Saves;public static void MarkSceneDirty(Scene s){}public static bool SaveScene(Scene s){Saves++;if(FailNextSave){FailNextSave=false;return false;}return true;} }
static class VRCForgeToolResult {
 public static object Completed(string m,object p)=>new {ok=true,summary=m,data=p};
 public static object Failed(string m)=>new {ok=false,summary=m};
 public static object FailedWithCode(string code,string m,object p)=>new {ok=false,code,summary=m,data=p};
}
''' + handler + r'''
class Probe {
 static GameObject avatar,a,b,c,group;static Scene scene;
 static void Setup(){scene=new Scene();ComponentCrudCore.Objects.Clear();avatar=Add("Avatar");a=Add("A",avatar.transform);b=Add("B",avatar.transform);c=Add("C",avatar.transform);group=Add("Group");SceneObjectCopyCore.Current=scene;SceneObjectCopyCore.CorruptReadback=false;SceneObjectCopyCore.UnchangedDigest=false;SceneObjectCopyCore.Initial=SceneObjectCopyCore.Digest();Undo.ParentCalls=Undo.OrderCalls=EditorSceneManager.Saves=0;EditorSceneManager.FailNextSave=false;}
 static GameObject Add(string n,Transform p=null){var g=new GameObject(n,scene,p);ComponentCrudCore.Objects[n]=g;return g;}
 static JObject Call(string path,string parent,int? index,bool preview=false){var p=new JObject{["gameObjectPath"]=path,["newParentPath"]=parent,["preview"]=preview};if(index.HasValue)p["siblingIndex"]=index.Value;return JObject.FromObject(ReparentGameObjectTool.HandleCommand(p));}
 static void Require(bool yes,string message){if(!yes)throw new Exception(message);}
 static string Order()=>string.Join(",",avatar.transform.children.Select(x=>x.name));
 static void Main(){
  Setup();var beforePath=ComponentCrudCore.GetHierarchyPath(c.transform);var local=c.transform.localScale.value;
  var result=Call("Avatar/C","Avatar",0);Require((bool)result["ok"]&&Order()=="C,A,B","same-parent precise sort failed");Require(ComponentCrudCore.GetHierarchyPath(c.transform)==beforePath&&c.transform.localScale.value==local&&Undo.ParentCalls==0,"same-parent sort changed hierarchy path or transform");Require((int)result["data"]["siblingIndex"]==0&&Undo.OrderCalls==1,"missing exact index readback/undo");
  Setup();result=Call("Avatar/C","Avatar",0,true);Require((bool)result["ok"]&&Order()=="A,B,C"&&Undo.OrderCalls==0&&EditorSceneManager.Saves==0,"preview mutated hierarchy");
  foreach(var index in new[]{-1,3}){Setup();result=Call("Avatar/C","Avatar",index);Require(!(bool)result["ok"]&&Order()=="A,B,C"&&Undo.OrderCalls==0&&Undo.ParentCalls==0,"invalid index mutated hierarchy");}
  Setup();result=Call("Avatar/A","Group",0);Require((bool)result["ok"]&&group.transform.children[0]==a.transform,"move+index failed");
  Setup();result=Call("Avatar/A","",2);Require((bool)result["ok"]&&a.transform.parent==null&&a.transform.GetSiblingIndex()==2,"root destination range failed");
  Setup();result=Call("Avatar/A","Group",null);Require((bool)result["ok"]&&a.transform.parent==group.transform&&Undo.OrderCalls==0,"legacy no-index move changed");
  Setup();EditorSceneManager.FailNextSave=true;result=Call("Avatar/C","Avatar",0);Require(!(bool)result["ok"]&&(bool)result["data"]["restored"]&&Order()=="A,B,C","save failure did not restore exact sibling order");
  Setup();SceneObjectCopyCore.CorruptReadback=true;result=Call("Avatar/C","Avatar",0);Require(!(bool)result["ok"]&&(string)result["code"]=="reparent_persisted_readback_mismatch"&&Order()=="A,B,C","incorrect persisted index was accepted or not restored");
  Setup();SceneObjectCopyCore.UnchangedDigest=true;result=Call("Avatar/C","Avatar",0);Require(!(bool)result["ok"]&&Order()=="A,B,C","unchanged scene digest accepted changed sibling order");
  Setup();c.PrefabRoot=avatar;result=Call("Avatar/C","Avatar",0);Require(!(bool)result["ok"]&&(string)result["code"]=="prefab_instance_child_reparent_not_supported"&&Order()=="A,B,C","prefab restriction bypassed");
  Console.WriteLine("production-handler: same-parent/path/transform/undo/preview/range/move/root/legacy/save-failure/readback-failure/digest/prefab passed");
 }
}
'''
    raw_path=os.environ.get('VRCFORGE_SIBLING_RAW_READBACK')
    if raw_path:
        retained=json.loads(Path(raw_path).read_text(encoding='utf-8'))
        original=max(retained['readbacks'],key=lambda r:len(r['children']))
        names=[c['name'] for c in original['children']]
        assert len(names)>2 and len(set(names))==len(names)
        raw_code='Setup();avatar.name='+json.dumps(original['path'],ensure_ascii=True)+';avatar.transform.children.Clear();ComponentCrudCore.Objects.Clear();ComponentCrudCore.Objects[avatar.name]=avatar;'
        raw_code+='foreach(var name in JsonConvert.DeserializeObject<string[]>('+json.dumps(json.dumps(names,ensure_ascii=True))+')) Add(name,avatar.transform);'
        raw_code+='var pathsBefore=avatar.transform.children.Select(t=>ComponentCrudCore.GetHierarchyPath(t)).OrderBy(x=>x).ToArray();var originalOrder=avatar.transform.children.Select(t=>t.name).ToArray();var moved=avatar.transform.children.Last();'
        raw_code+='result=Call(ComponentCrudCore.GetHierarchyPath(moved),avatar.name,0);Require((bool)result["ok"]&&moved.GetSiblingIndex()==0,"original 29-child readback was not reordered");'
        raw_code+='Require(pathsBefore.SequenceEqual(avatar.transform.children.Select(t=>ComponentCrudCore.GetHierarchyPath(t)).OrderBy(x=>x)),"original paths changed");Require(avatar.transform.children.Skip(1).Select(t=>t.name).SequenceEqual(originalOrder.Take(originalOrder.Length-1)),"unselected sibling relative order changed");Console.WriteLine("original-raw-children:'+str(len(names))+' passed");'
        program=program.replace('  Setup();var beforePath','{JObject rawResult;'+raw_code.replace('result','rawResult')+'}\n  Setup();var beforePath')
    base=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'dotnet'
    compiler=sorted((base/'sdk').glob('*/Roslyn/bincore/csc.dll'))[-1]
    refs=sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))[-1]
    newtonsoft=compiler.parents[2]/'Newtonsoft.Json.dll';dotnet=shutil.which('dotnet')
    (tmp_path/'Probe.cs').write_text(program,encoding='utf-8')
    built=subprocess.run([dotnet,str(compiler),'-nologo','-target:exe','-nostdlib+',f'-out:{tmp_path/"Probe.dll"}',f'-r:{newtonsoft}',*[f'-r:{p}' for p in refs.glob('*.dll')],str(tmp_path/'Probe.cs')],capture_output=True,text=True,timeout=60)
    assert built.returncode==0,built.stdout+built.stderr
    shutil.copy2(newtonsoft,tmp_path/'Newtonsoft.Json.dll')
    (tmp_path/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'netcoreapp3.1','framework':{'name':'Microsoft.NETCore.App','version':'3.1.0'}}}),encoding='utf-8')
    ran=subprocess.run([dotnet,str(tmp_path/'Probe.dll')],capture_output=True,text=True,timeout=30)
    assert ran.returncode==0,ran.stdout+ran.stderr
    print(ran.stdout.strip())

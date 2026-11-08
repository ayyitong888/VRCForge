"""Run the production scan proof against a controller with an orphaned subasset."""
from pathlib import Path

from test_curve_fx_authoring_runtime_contract import method
from test_fx_authoritative_state_preview import compile_probe


def test_explicit_controller_proof_watches_reachable_objects_and_file_dependencies(tmp_path):
    journal = Path(__file__).resolve().parents[1] / "Assets/VRCForge/Editor/MCP/ScanScopeJournal.cs"
    source = journal.read_text(encoding="utf-8-sig")
    methods = "\n".join(
        method(source, signature)
        for signature in (
            "private sealed class Scope",
            "private sealed class Watch",
            "private static bool IsControllerOnlyAnimation(",
            "private static Scope Measure(",
            "private static string[] Roots(",
            "internal static JObject Validate(",
        )
        if signature in source
    )
    program = r'''
using System; using System.Collections.Generic; using System.IO; using System.Linq;
using Newtonsoft.Json.Linq;

class Object {
 public string id, name, path, revision="initial";
 public List<SerializedProperty> references=new List<SerializedProperty>();
}
class Component:Object { public GameObject gameObject; }
class Transform:Component { public IEnumerator<Transform> GetEnumerator()=>Enumerable.Empty<Transform>().GetEnumerator(); }
class GameObject:Object {
 public Transform transform=new Transform();
 public Scene scene=new Scene();
 public T[] GetComponents<T>()=>Array.Empty<T>();
}
class Scene { public bool IsValid()=>true; }
class AnimatorController:Object { public bool hasClips=true; }
class AnimationClip:Object {}
namespace VRC.SDK3.Avatars.Components { class VRCAvatarDescriptor:global::Component { public Transform transform; } }
static class Resources {
 public static Object[] SceneObjects=Array.Empty<Object>();
 public static T[] FindObjectsOfTypeAll<T>()=>SceneObjects.OfType<T>().ToArray();
}
static class AssetTools {
 public static List<AnimationClip> ReadControllerClips(AnimatorController controller)
  =>controller.hasClips?new List<AnimationClip>{new AnimationClip()}:new List<AnimationClip>();
}
static class ShaderMaterialScanner {
 public static Transform[] ResolveAvatarRoots(string path)=>throw new Exception("unexpected material scope");
}
static class AvatarPrimitiveCrudCore { public static string GetTransformPath(Transform transform)=>"Avatar"; }
class Mesh:Object {} class Shader:Object {} class Texture:Object {} class MonoScript:Object {}
class Material:Object { public Shader shader; }
class Renderer:Component { public Material[] sharedMaterials=Array.Empty<Material>(); }
static class RendererComponentIdentity {
 internal sealed class Evidence { public string rendererPath, componentId, scenePath; public int sceneHandle; }
 public static Evidence Create(Renderer renderer)=>throw new Exception("unexpected material scope");
}
static class MaterialInventoryIdentity {
 public static string CreateMaterialId(string path,int slot,string material,string shader)=>throw new Exception("unexpected material scope");
}
static class AssetDatabase {
 public static readonly Dictionary<string,Object[]> Subassets=new Dictionary<string,Object[]>();
 public static readonly Dictionary<string,string[]> Dependencies=new Dictionary<string,string[]>();
 public static string GetAssetPath(Object obj)=>obj.path;
 public static string[] GetDependencies(string path,bool recurse)=>Dependencies.TryGetValue(path,out var paths)?paths:new[]{path};
 public static Object LoadMainAssetAtPath(string path)=>LoadAllAssetsAtPath(path).FirstOrDefault();
 public static Object[] LoadAllAssetsAtPath(string path)=>Subassets.TryGetValue(path,out var objects)?objects:Array.Empty<Object>();
}
static class EditorUtility {
 public static bool IsPersistent(Object obj)=>!(obj is Component) && !(obj is GameObject);
 public static int GetDirtyCount(Object obj)=>0;
}
enum SerializedPropertyType { ObjectReference }
class SerializedProperty {
 readonly IEnumerator<SerializedProperty> iterator;
 public SerializedProperty() {}
 public SerializedProperty(IEnumerable<SerializedProperty> refs){iterator=refs.GetEnumerator();}
 public bool Next(bool enterChildren)=>iterator.MoveNext();
 public string name=>"m_Motion";
 public string propertyPath=>"m_Childs.Array.data[0].m_Motion";
 public SerializedPropertyType propertyType=>SerializedPropertyType.ObjectReference;
 public Object target;
 public int instanceId;
 public Object objectReferenceValue=>iterator.Current.target;
 public int objectReferenceInstanceIDValue=>iterator.Current.instanceId;
}
class SerializedObject:IDisposable {
 readonly Object target;
 public SerializedObject(Object value){target=value;}
 public SerializedProperty GetIterator()=>new SerializedProperty(target.references);
 public void Dispose(){}
}

class Probe {
 const string ControllerPath="Assets/FX.controller", ClipPath="Assets/Ear.anim", FileOnlyPath="Assets/Import.fbx";
 const int MaxObjects=2048;
 class State {
  public long epoch,sceneEpoch;
  public Dictionary<string,Watch> watches=new Dictionary<string,Watch>();
  public Dictionary<string,long> paths=new Dictionary<string,long>();
  public Dictionary<string,Scope> scopes=new Dictionary<string,Scope>();
 }
 static readonly State state=new State();
 static readonly AnimatorController Controller=new AnimatorController{id="controller",path=ControllerPath};
 static readonly GameObject Avatar=new GameObject{id="avatar"};
 static readonly Object Active=new Object{id="active",path=ControllerPath};
 static readonly Object Orphan=new Object{id="orphan",path=ControllerPath};
 static readonly AnimationClip Clip=new AnimationClip{id="clip",path=ClipPath};
 static readonly Dictionary<string,string> FileRevisions=new Dictionary<string,string>();
 static void Observe(){}
 static Object Resolve(string id)=>new Object[]{Controller,Avatar,Clip}.FirstOrDefault(o=>o.id==id);
 static string Identity(Object target)=>target.id;
 static bool AssetPath(string path)=>path?.StartsWith("Assets/")==true;
 static string FileHash(string path,ref long budget,bool metadataOnly=false)=>FileRevisions.TryGetValue(path,out var revision)?revision:"disk";
 static string MemoryHash(Object target)=>target.revision;
 static JObject Receipt(bool valid,string reason)=>new JObject{["valid"]=valid,["reason"]=reason};
 static JArray ObjectDiagnostics(Scope saved,Scope current)=>new JArray();
 static string Hash(string value)=>value;
 static IEnumerable<Object> MaterialScopeTargets(string[] roots)=>throw new Exception("unexpected material scope");
 static HashSet<string> ReadMaterialSelectionIds(JObject selector)=>throw new Exception("unexpected material scope");
 static bool MaterialIdSelected(HashSet<string> ids,string id)=>false;
 static string MaterialSelectorDigest(IEnumerable<string> ids)=>"";
 static bool MaterialReferenceRelevant(Object owner,string path,Object referenced,bool textures)=>false;
 static IEnumerable<Texture> MaterialTextureDependencies(Material material,bool textures)=>Array.Empty<Texture>();
 JOURNAL_METHODS

 static JObject Selector(string tool,bool explicitController=true)=>new JObject {
  ["scanTool"]=tool, ["arguments"]=new JObject {["controllerPath"]=explicitController?ControllerPath:""}
 };
 static Scope MeasureOrNull(JObject selector) {
  try {return Measure(Roots(selector),selector);}
  catch(InvalidDataException ex) when(ex.Message=="unresolved_scope_reference"){return null;}
 }
 static void Save(Scope measured,JObject selector) {
  measured.selector=selector; state.scopes["saved"]=measured;
 }
 static JObject Read()=>Validate(new JObject{["token"]="saved"});
 static int failures;
 static void Check(bool ok,string label) {
  Console.WriteLine((ok?"PASS ":"FAIL ")+label);
  if(!ok) failures++;
 }
 public static int Main() {
  Resources.SceneObjects=new Object[]{new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor{gameObject=Avatar}};
  Controller.references.Add(new SerializedProperty{target=Active,instanceId=1});
  Active.references.Add(new SerializedProperty{target=Clip,instanceId=2});
  Orphan.references.Add(new SerializedProperty{instanceId=3});
  AssetDatabase.Dependencies[ControllerPath]=new[]{ControllerPath,ClipPath,FileOnlyPath};
  AssetDatabase.Subassets[ControllerPath]=new[]{Controller,Active,Orphan};
  AssetDatabase.Subassets[ClipPath]=new[]{Clip};
  var fx=Selector("vrcforge_scan_fx_animator");
  var bindings=Selector("vrcforge_scan_animation_bindings");
  var fxScope=MeasureOrNull(fx);
  var bindingsScope=MeasureOrNull(bindings);
  Check(fxScope!=null && fxScope.objects.Keys.ToHashSet().SetEquals(new[]{"controller","active","clip"}),
   "explicit FX scope follows reachable motions even when a dormant BlendTree references missing assets");
  Check(bindingsScope!=null && bindingsScope.objects.Keys.ToHashSet().SetEquals(new[]{"controller","active","clip"}),
   "controller-selected animation bindings follow the same reachable graph");
  Check(fxScope!=null && fxScope.files.ContainsKey(FileOnlyPath),
   "file-only importer dependencies remain part of the proof");
  var fileExpired=false;
  var clipExpired=false;
  if(fxScope!=null) {
   Save(fxScope,fx);
   FileRevisions[FileOnlyPath+".meta"]="changed";
   fileExpired=!(bool)Read()["valid"];
   FileRevisions.Clear();
   Clip.revision="edited in memory";
   clipExpired=!(bool)Read()["valid"];
  }
  Check(fileExpired,"file dependency metadata changes invalidate the proof");
  Check(clipExpired,"reachable clip memory changes invalidate the proof");
  var fxWithoutController=Selector("vrcforge_scan_fx_animator",false);
  fxWithoutController["assetPaths"]=new JArray(ControllerPath);
  Check(MeasureOrNull(Selector("vrcforge_scan_wardrobe"))==null && MeasureOrNull(fxWithoutController)==null,
   "generic scopes keep their existing dependency expansion");
  var mixedClips=Selector("vrcforge_scan_animation_bindings");
  mixedClips["arguments"]["clipPaths"]=new JArray(ClipPath);
  var mixedAvatar=Selector("vrcforge_scan_animation_bindings");
  mixedAvatar["arguments"]["avatarPath"]="Avatar";
  Check(MeasureOrNull(mixedClips)==null && MeasureOrNull(mixedAvatar)==null,
   "mixed animation selectors keep their existing dependency expansion");
  Active.references.Add(new SerializedProperty{instanceId=4});
  Check(MeasureOrNull(fx)==null && MeasureOrNull(bindings)==null,
   "a reachable unresolved motion still blocks proof");
  Controller.hasClips=false;
  var fallbackRejected=false;
  try { Roots(bindings); }
  catch(InvalidDataException ex) when(ex.Message=="animation_controller_scope_unavailable"){fallbackRejected=true;}
  Check(fallbackRejected,"empty animation controller cannot certify the avatar fallback");
  return failures==0?0:1;
 }
}
'''.replace("JOURNAL_METHODS", methods)
    compile_probe(tmp_path, program, 9)

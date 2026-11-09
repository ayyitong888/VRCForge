"""Execute the production scope validator when reported texture membership changes."""
from pathlib import Path
from test_curve_fx_authoring_runtime_contract import method
from test_fx_authoritative_state_preview import compile_probe


def test_saved_scope_rejects_added_removed_or_changed_texture_dependencies(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "Assets/VRCForge/Editor/MCP/ScanScopeJournal.cs").read_text(encoding="utf-8-sig")
    selected = method(source, "internal static JObject Validate(")
    program = r'''
using System; using System.Collections.Generic; using System.Linq; using Newtonsoft.Json.Linq;
class Asset { public string path; }
static class AssetDatabase {public static string GetAssetPath(Asset item)=>item.path;}
class Scope {
 public int epoch,sceneEpoch; public bool scene;
 public string[] roots=new[]{"avatar"}; public JObject selector=new JObject();
 public Dictionary<string,string> objects=new Dictionary<string,string>();
 public Dictionary<string,string> files=new Dictionary<string,string>();
}
class State {public int epoch,sceneEpoch; public Dictionary<string,Scope> scopes=new Dictionary<string,Scope>();}
class Probe {
 static State state=new State(); static Scope measured;
 static void Observe(){} static string[] Roots(JObject selector)=>new[]{"avatar"};
 static Scope Measure(string[] roots,JObject selector)=>measured;
 static Asset Resolve(string id)=>new Asset{path="Assets/"+id};
 static JObject Receipt(bool valid,string reason)=>new JObject{["valid"]=valid,["reason"]=reason};
 static JArray ObjectDiagnostics(Scope oldScope,Scope newScope)=>new JArray();
 SELECTED
 static int failures;
 static void Check(bool ok,string label){Console.WriteLine((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 static Scope Graph(bool texture,string hash="same"){
  var graph=new Scope(); graph.objects["body.mat"]="material"; graph.files["Assets/body.mat"]="yaml";
  if(texture){graph.objects["normal.png"]="texture";graph.files["Assets/normal.png"]=hash;}
  return graph;
 }
 static JObject Read()=>Validate(new JObject{["token"]="saved"});
 public static int Main(){
  state.scopes["saved"]=Graph(false); measured=Graph(false);
  Check((bool)Read()["valid"],"same missing-slot graph stays valid");
  measured=Graph(true);
  Check(!(bool)Read()["valid"],"missing GUID resolves without material YAML change: invalid");
  state.scopes["saved"]=Graph(true); measured=Graph(false);
  Check(!(bool)Read()["valid"],"reported texture becomes missing: invalid");
  measured=Graph(true,"changed");
  Check(!(bool)Read()["valid"],"reported texture file content changes: invalid");
  measured=Graph(true); measured.objects["normal.png"]="new revision";
  Check(!(bool)Read()["valid"],"texture memory/revision changes: invalid");
  measured=Graph(true);
  Check((bool)Read()["valid"],"unchanged complete texture graph remains valid");
  return failures==0?0:1;
 }
}
'''.replace("SELECTED", selected)
    compile_probe(tmp_path, program, 6)


def test_texture_observation_hashes_scanner_metadata_without_pixel_serialization(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "Assets/VRCForge/Editor/MCP/ScanScopeJournal.cs").read_text(encoding="utf-8-sig")
    selected = method(source, "internal static JObject TextureObservation(")
    program = r'''
using System; using Newtonsoft.Json.Linq;
class Texture {public string name="Body";public int width=4096,height=4096; public long bytes=67108864;}
class AssetImporter {public static AssetImporter GetAtPath(string path)=>new TextureImporter();}
class TextureImporter:AssetImporter {public string textureType="NormalMap";public int maxTextureSize=2048;}
static class AssetDatabase {public static string GetAssetPath(Texture texture)=>"Assets/Body.png";}
namespace UnityEngine.Profiling {static class Profiler {public static long GetRuntimeMemorySizeLong(Texture texture)=>texture.bytes;}}
class Probe {
 SELECTED
 static int failures;
 static void Check(bool ok,string label){Console.WriteLine((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 public static int Main(){
  var texture=new Texture(); var data=TextureObservation(texture);
  Check((string)data["name"]=="Body" && (int)data["width"]==4096 && (int)data["height"]==4096,"scanner name and dimensions covered");
  Check((long)data["bytes"]==67108864 && (string)data["importerType"]=="NormalMap" && (int)data["importerMaxTextureSize"]==2048,"scanner memory and importer fields covered");
  Check(data.ToString().Length<1024,"large texture produces bounded metadata without pixels");
  texture.width=2048; var changed=TextureObservation(texture);
  Check(!JToken.DeepEquals(data,changed),"observable texture changes affect hash input");
  return failures==0?0:1;
 }
}
'''.replace("SELECTED", selected)
    compile_probe(tmp_path, program, 4)
    assert "TextureObservation(texture).ToString(Formatting.None)" in source


def test_material_scope_remeasures_renderer_membership_without_watching_bones(tmp_path):
    editor = Path(__file__).resolve().parents[1] / "Assets/VRCForge/Editor"
    journal = (editor / "MCP/ScanScopeJournal.cs").read_text(encoding="utf-8-sig")
    scanner = (editor / "ShaderMaterialScanner.cs").read_text(encoding="utf-8-sig")
    # Let this probe compile against the old walker too, so its oversized-scope
    # failure is demonstrated by Measure/Validate rather than a missing helper.
    scanner_methods = "\n".join(
        method(scanner, signature)
        for signature in (
            "internal static Renderer[] QueryRenderers(",
            "internal static MeshFilter QueryMeshFilter(",
            "private static bool IsSceneObject(",
            "private static Transform FindAvatarRoot(",
        )
        if signature in scanner
    )
    journal_methods = "\n".join(
        method(journal, signature)
        for signature in (
            "private sealed class Scope",
            "private sealed class Watch",
            "private static IEnumerable<Object> MaterialScopeTargets(",
            "private static bool IsControllerOnlyAnimation(",
            "private static Scope Measure(",
            "internal static JObject Validate(",
            "private static HashSet<string> ReadMaterialSelectionIds(",
            "internal static bool MaterialIdSelected(",
            "internal static string MaterialSelectorDigest(",
            "internal static bool MaterialReferenceRelevant(",
            "internal static IEnumerable<Texture> MaterialTextureDependencies(",
            "private static string Hash(",
        )
        if signature in journal
    )
    identity = (editor / "RendererComponentIdentity.cs").read_text(encoding="utf-8-sig")
    renderer_index = method(identity, "internal static int GetComponentIndex(")
    program = r'''
using System; using System.Collections; using System.Collections.Generic; using System.IO;
using System.Linq; using System.Security.Cryptography; using System.Text; using Newtonsoft.Json.Linq;
class Object { public string id, name; }
class Scene { public bool isLoaded=true; public int handle=1; public string path="Assets/Fixture.unity"; public bool IsValid()=>true; }
class GameObject : Object {
 public Scene scene=new Scene();
 public Transform transform;
 public List<Component> components=new List<Component>();
 public GameObject(string value, Transform parent=null) {
  id=name=value;
  transform=new Transform { id=value+":transform", name=value, owner=this };
  transform.transform=transform; transform.Reparent(parent); components.Add(transform);
 }
 public T Add<T>(string identity) where T:Component,new() {
  var c=new T { id=identity, name=name, transform=transform }; components.Add(c); return c;
 }
 public T[] GetComponents<T>() where T:Component => components.OfType<T>().ToArray();
 public Component[] GetComponents(Type type)=>components.Where(type.IsInstanceOfType).ToArray();
}
class Component : Object {
 public Transform transform;
 public GameObject gameObject=>transform.owner;
 public T GetComponent<T>() where T:Component=>gameObject.GetComponents<T>().FirstOrDefault();
}
class Transform : Component,IEnumerable<Transform> {
 public GameObject owner;
 public Transform parent;
 public Transform root=>parent?.root??this;
 public string Path=>parent==null ? name : parent.Path+"/"+name;
 public List<Transform> children=new List<Transform>();
 public void Reparent(Transform next) { parent?.children.Remove(this); parent=next; next?.children.Add(this); }
 public Component GetComponent(Type type)=>owner.components.FirstOrDefault(type.IsInstanceOfType);
 public T[] GetComponentsInChildren<T>(bool includeInactive) where T:Component
  =>owner.GetComponents<T>().Concat(children.SelectMany(c=>c.GetComponentsInChildren<T>(includeInactive))).ToArray();
 public IEnumerator<Transform> GetEnumerator()=>children.GetEnumerator();
 IEnumerator IEnumerable.GetEnumerator()=>GetEnumerator();
}
class Renderer : Component { public Material[] sharedMaterials=Array.Empty<Material>(); }
class SkinnedMeshRenderer : Renderer { public Mesh sharedMesh; }
class MeshFilter : Component { public Mesh sharedMesh; }
class Animator : Component {} class Descriptor : Component {} class Mesh : Object {}
class Shader : Object {} class MonoScript : Object {}
class Texture : Object {}
class Material : Object {
 public Shader shader;
 public string[] GetTexturePropertyNames()=>Array.Empty<string>();
 public Texture GetTexture(string name)=>null;
}
static class ShaderMaterialScanner {
 SCANNER_METHODS
 static Type FindType(string name)=>typeof(Descriptor);
}
static class AvatarPrimitiveCrudCore {
 public static string GetTransformPath(Transform transform)=>transform.Path;
}
static class RendererComponentIdentity {
 public static int Calls;
 RENDERER_INDEX
 internal sealed class Evidence { public string rendererPath, componentId, scenePath; public int sceneHandle; }
 public static Evidence Create(Renderer renderer){
  Calls++;
  return new Evidence{rendererPath=renderer.transform.Path, componentId=renderer.id+"@"+renderer.transform.Path
   +Array.IndexOf(renderer.gameObject.GetComponents(renderer.GetType()),renderer),
   scenePath=renderer.gameObject.scene.path, sceneHandle=renderer.gameObject.scene.handle};
 }
}
static class MaterialInventoryIdentity {
 public static string CreateMaterialId(string path,int slot,string material,string shader)=>path+":"+slot+":"+material+":"+shader;
}
static class AssetDatabase {
 public static string GetAssetPath(Object obj)=>"";
 public static string[] GetDependencies(string path,bool recurse)=>Array.Empty<string>();
 public static Object[] LoadAllAssetsAtPath(string path)=>Array.Empty<Object>();
}
static class EditorUtility {
 public static int GetDirtyCount(Object obj)=>0;
 public static bool IsPersistent(Object obj)=>false;
}
enum SerializedPropertyType { ObjectReference }
class SerializedObject : IDisposable {
 public SerializedObject(Object target){}
 public SerializedProperty GetIterator()=>new SerializedProperty();
 public void Dispose(){}
}
class SerializedProperty {
 public bool Next(bool enterChildren)=>false;
 public string name,propertyPath;
 public Object objectReferenceValue;
 public int objectReferenceInstanceIDValue;
 public SerializedPropertyType propertyType;
}
class Probe {
 const int MaxObjects=2048;
 static readonly GameObject Avatar=new GameObject("avatar");
 static readonly JObject Selector=new JObject{["scanTool"]="vrcforge_scan_avatar_materials"};
 class State {
  public long epoch, sceneEpoch;
  public Dictionary<string,Watch> watches=new Dictionary<string,Watch>();
  public Dictionary<string,long> paths=new Dictionary<string,long>();
  public Dictionary<string,Scope> scopes=new Dictionary<string,Scope>();
 }
 static readonly State state=new State();
 static void Observe(){}
 static string[] Roots(JObject selector)=>new[]{"avatar"};
 static Object Resolve(string id)=>id=="avatar"?Avatar:null;
 static string Identity(Object target)=>target.id;
 static bool AssetPath(string path)=>false;
 static string FileHash(string path,ref long budget,bool metadataOnly=false)=>throw new Exception("unexpected asset path");
 static string MemoryHash(Object target)=>Hash(target is Component c?c.transform.Path:target.name);
 static JObject Receipt(bool valid,string reason)=>new JObject{["valid"]=valid,["reason"]=reason};
 static JArray ObjectDiagnostics(Scope saved,Scope current)=>new JArray();
 JOURNAL_METHODS
 static int failures;
 static void Check(bool ok,string label){Console.WriteLine((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 static void Save(){
  var scope=Measure(Roots(Selector),Selector); scope.selector=Selector; state.scopes["saved"]=scope;
 }
 static JObject Read()=>Validate(new JObject{["token"]="saved"});
 static bool Valid()=>(bool)Read()["valid"];
 public static int Main(){
  Avatar.Add<Descriptor>("avatar:descriptor");
  var body=new GameObject("body",Avatar.transform);
  body.Add<Renderer>("body:renderer");
  var other=new GameObject("other",Avatar.transform);
  Save();
  var nested=new GameObject("nested",Avatar.transform);
  nested.Add<Descriptor>("nested:descriptor");
  nested.Add<Renderer>("nested:renderer");
  Check(Valid(),"separate nested avatar renderer stays outside the query");
  Save();
  var anotherRenderer=other.Add<Renderer>("other:renderer");
  Check(!Valid(),"adding a renderer to an existing child invalidates the old scope");
  Save();
  other.components.Remove(anotherRenderer);
  Check(!Valid(),"removing a renderer invalidates the old scope");
  Save();
  body.transform.Reparent(other.transform);
  Check(!Valid(),"reparenting the same renderer changes its path");
  Save();
  body.transform.Reparent(nested.transform);
  Check(!Valid(),"reparenting across avatar ownership changes membership");
  body.transform.Reparent(Avatar.transform);
  Save();
  body.scene.path="Assets/Renamed.unity";
  Check(!Valid(),"scene rename invalidates otherwise unchanged renderer");
  Save();
  body.scene.handle++;
  Check(!Valid(),"same-path renderer in a different scene instance invalidates the old scope");
  var sibling=body.Add<Renderer>("body:second-renderer");
  Save();
  body.components.Remove(sibling);
  body.components.Insert(0,sibling);
  Check(!Valid(),"same-type renderer reorder changes the reported index");
  Save();
  body.Add<MeshFilter>("body:filter");
  Check(!Valid(),"adding the renderer's mesh filter invalidates the old scope");
  Save();
  for(var i=0;i<2100;i++) new GameObject("bone"+i,Avatar.transform);
  var expanded=Read();
  Check((bool)expanded["valid"],"over 2048 unrelated bone nodes leave the renderer scope valid: "+(string)expanded["reason"]);
  Check(RendererComponentIdentity.Calls==0,"scope validation does not build a second renderer identity");
  return failures==0?0:1;
 }
}
'''.replace("SCANNER_METHODS", scanner_methods).replace("JOURNAL_METHODS", journal_methods).replace("RENDERER_INDEX", renderer_index)
    compile_probe(tmp_path, program, 11)

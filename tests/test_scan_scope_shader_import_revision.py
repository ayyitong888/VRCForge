"""Exercise the journal's shader fingerprint without serializing a compiled shader."""
from pathlib import Path

from test_curve_fx_authoring_runtime_contract import method
from test_fx_authoritative_state_preview import compile_probe


def test_shader_uses_live_declarations_without_serializing_compiled_variants(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "Assets/VRCForge/Editor/MCP/ScanScopeJournal.cs").read_text(
        encoding="utf-8-sig"
    )
    observation = "private static JObject ShaderObservation("
    program = r'''
using System; using System.IO; using System.Linq; using System.Text; using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
class Object { public string name, state; }
enum ShaderPropertyType { Float, Range, Int, Color, Vector, Texture }
class Shader : Object {
 public string propertyName="_Mode";
 public ShaderPropertyType type=ShaderPropertyType.Float;
 public float value=1;
 public int renderQueue=2000;
 public struct Vector { public float x,y,z,w; }
 public struct Keyword { public string name; }
 public struct KeywordSpace { public Keyword[] keywords; }
 public KeywordSpace keywordSpace=new KeywordSpace { keywords=new[]{new Keyword { name="FEATURE" }} };
 public int GetPropertyCount() => 1;
 public string GetPropertyName(int index) => propertyName;
 public ShaderPropertyType GetPropertyType(int index) => type;
 public float GetPropertyDefaultFloatValue(int index) => value;
 public int GetPropertyDefaultIntValue(int index) => (int)value;
 public Vector GetPropertyDefaultVectorValue(int index) => new Vector { x=value };
 public string GetPropertyTextureDefaultName(int index) => "white";
}
class Texture : Object {}
class SkinnedMeshRenderer : Object { public Object sharedMesh; public object transform; }
class MeshFilter : Object { public Object sharedMesh; }
class GameObject : Object { public object transform; }
class Component : Object { public object transform; }
class AnimationClip : Object {}
static class EditorJsonUtility { public static string ToJson(Object value) => value.state; }
static class AvatarPrimitiveCrudCore { public static string GetTransformPath(object t) => ""; }
static class AnimationUtility {
 public struct Binding { public string path, propertyName; public Type type; }
 public struct Key { public float time; public Object value; }
 public static Binding[] GetCurveBindings(AnimationClip clip) => new Binding[0];
 public static Binding[] GetObjectReferenceCurveBindings(AnimationClip clip) => new Binding[0];
 public static object GetEditorCurve(AnimationClip clip, Binding binding) => null;
 public static Key[] GetObjectReferenceCurve(AnimationClip clip, Binding binding) => new Key[0];
}
class Probe {
 const int MaxStateBytes = 4 * 1024 * 1024;
 static string Hash(string text) => text;
 static string Identity(Object target) => target.name;
 static JObject TextureObservation(Texture texture) => new JObject();
 MEMORY_HASH
 SHADER_OBSERVATION
 static int failures;
 static void Check(bool ok,string label){ Console.WriteLine((ok?"PASS ":"FAIL ")+label); if(!ok) failures++; }
 static bool Bounded(Object target) {
  try { MemoryHash(target); return false; }
  catch(InvalidDataException ex) { return ex.Message == "object_too_large"; }
 }
 public static int Main(){
  var shader = new Shader { name="Example/Heavy", state=new string('x',MaxStateBytes+1) };
  string before=null;
  try { before=MemoryHash(shader); } catch(InvalidDataException) {}
  Check(before!=null && before.Length<1024,"large compiled shader uses small live declaration");
  shader.state=new string('y',MaxStateBytes+1);
  Check(before!=null && MemoryHash(shader)==before,"compiled-only changes do not affect the declaration");
  shader.value=2;
  Check(before!=null && MemoryHash(shader)!=before,"changed property default invalidates the declaration");
  shader.value=1; shader.type=ShaderPropertyType.Int;
  Check(before!=null && MemoryHash(shader)!=before,"changed property type invalidates the declaration");
  shader.type=ShaderPropertyType.Float; shader.keywordSpace.keywords[0].name="RENAMED";
  Check(before!=null && MemoryHash(shader)!=before,"changed keyword invalidates the declaration");
  Check(Bounded(new Object { state=shader.state }),"other oversized object stays bounded");
  return failures==0?0:1;
 }
}
'''.replace("MEMORY_HASH", method(source, "private static string MemoryHash(")).replace(
        "SHADER_OBSERVATION", method(source, observation) if observation in source else ""
    )
    compile_probe(tmp_path, program, 6)

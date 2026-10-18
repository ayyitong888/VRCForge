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

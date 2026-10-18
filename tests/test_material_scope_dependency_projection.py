"""Execute Core dependency selection, including unresolved relevant references."""
from pathlib import Path
from test_curve_fx_authoring_runtime_contract import method
from test_fx_authoritative_state_preview import compile_probe


def test_material_scope_uses_only_the_inventory_dependency_graph(tmp_path):
    path = Path(__file__).resolve().parents[1] / "Assets/VRCForge/Editor/MCP/ScanScopeJournal.cs"
    source = path.read_text(encoding="utf-8-sig")
    selected = method(source, "internal static bool MaterialReferenceRelevant(")
    program = r'''
using System;
class Object {} class Renderer:Object {} class MeshFilter:Object {} class Material:Object {} class Texture:Object {} class Menu:Object {}
class Probe {
 SELECTED
 static int failures;
 static void Check(bool ok,string label){Console.WriteLine((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 public static int Main(){
  Check(!MaterialReferenceRelevant(new Menu(),"Parameters",null,false),"unresolved menu is not a material dependency");
  Check(MaterialReferenceRelevant(new Renderer(),"m_Materials.Array.data[0]",null,false),"unresolved material stays a required dependency");
  Check(MaterialReferenceRelevant(new Renderer(),"m_Mesh",null,false),"mesh assignment remains covered");
  Check(!MaterialReferenceRelevant(new Renderer(),"m_ProbeAnchor",null,false),"unread probe dependency excluded");
  Check(MaterialReferenceRelevant(new Material(),"m_Shader",null,false),"shader remains required");
  Check(MaterialReferenceRelevant(new Material(),"m_Parent",new Material(),false),"material inheritance remains covered");
  Check(!MaterialReferenceRelevant(new Material(),"m_SavedProperties.m_TexEnvs.Array.data[0].second.m_Texture",new Texture(),false),"texture content excluded only when not read");
  Check(!MaterialReferenceRelevant(new Material(),"m_SavedProperties.m_TexEnvs.Array.data[0].second.m_Texture",null,true),"raw missing slot is not a texture reported by the inventory reader");
  return failures==0?0:1;
 }
}
'''.replace("SELECTED", selected)
    compile_probe(tmp_path, program, 8)
    assert "Measure(saved.roots, saved.selector)" in source


def test_material_texture_dependencies_use_actual_resolved_reader_values(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "Assets/VRCForge/Editor/MCP/ScanScopeJournal.cs").read_text(encoding="utf-8-sig")
    selected = method(source, "internal static IEnumerable<Texture> MaterialTextureDependencies(")
    program = r'''
using System; using System.Collections.Generic; using System.Linq;
class Texture {}
class Material {
 public Dictionary<string,Texture> values=new Dictionary<string,Texture>();
 public string[] GetTexturePropertyNames()=>values.Keys.ToArray();
 public Texture GetTexture(string name)=>values[name];
}
class Probe {
 SELECTED
 static int failures;
 static void Check(bool ok,string label){Console.WriteLine((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 public static int Main(){
  var material=new Material(); var body=new Texture(); var normal=new Texture();
  material.values["_MainTex"]=body; material.values["_BumpMap"]=null;
  var before=MaterialTextureDependencies(material,true).ToArray();
  Check(before.Length==1 && before[0]==body,"missing serialized BumpMap does not block reported texture");
  Check(!MaterialTextureDependencies(material,false).Any(),"explicit texture opt-out remains empty");
  Check(!MaterialTextureDependencies(null,true).Any(),"null material has no texture dependencies");
  material.values["_BumpMap"]=normal;
  var resolved=MaterialTextureDependencies(material,true).ToArray();
  Check(resolved.Length==2 && resolved.Contains(normal),"newly resolvable texture enters measured graph");
  material.values["_MainTex"]=null;
  var missing=MaterialTextureDependencies(material,true).ToArray();
  Check(missing.Length==1 && missing[0]==normal,"removed texture leaves measured graph");
  Check(!new HashSet<Texture>(resolved).SetEquals(missing),"resolved-to-missing membership changes");
  return failures==0?0:1;
 }
}
'''.replace("SELECTED", selected)
    compile_probe(tmp_path, program, 6)
    assert "MaterialTextureDependencies(material, includeTextures)" in source

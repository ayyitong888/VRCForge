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
  Check(MaterialReferenceRelevant(new Material(),"m_SavedProperties.m_TexEnvs.Array.data[0].second.m_Texture",null,true),"requested unresolved texture remains required");
  return failures==0?0:1;
 }
}
'''.replace("SELECTED", selected)
    compile_probe(tmp_path, program, 8)
    assert "Measure(saved.roots, saved.selector)" in source

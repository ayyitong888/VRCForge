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


def test_selected_material_scope_tracks_selector_identity_without_unselected_dependencies(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = (root / "Assets/VRCForge/Editor/MCP/ScanScopeJournal.cs").read_text(encoding="utf-8-sig")
    identity_source = (root / "Assets/VRCForge/Editor/RendererComponentIdentity.cs").read_text(encoding="utf-8-sig")
    selected = method(source, "internal static bool MaterialIdSelected(")
    selector_digest = method(source, "internal static string MaterialSelectorDigest(")
    hash_method = method(source, "private static string Hash(")
    material_id = method(identity_source, "internal static string CreateMaterialId(")
    stable_id = method(identity_source, "private static string StableId(")
    program = r'''
using System; using System.Collections.Generic; using System.Linq; using System.Security.Cryptography; using System.Text;
static class MaterialInventoryIdentity {
 MATERIAL_ID
 STABLE_ID
}
class Probe {
 SELECTED
 SELECTOR_DIGEST
 HASH
 static int failures;
 static void Check(bool ok,string label){Console.WriteLine((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 public static int Main(){
  var selectedId=MaterialInventoryIdentity.CreateMaterialId("Avatar/Body",2,"BodyMat","Avatar/BodyShader");
  var otherId=MaterialInventoryIdentity.CreateMaterialId("Avatar/Hair",0,"HairMat","Avatar/HairShader");
  var selected=new HashSet<string>(StringComparer.OrdinalIgnoreCase){selectedId};
  Check(MaterialIdSelected(selected,selectedId.ToUpperInvariant()),"selected ID uses the inventory identity and case-insensitive selector");
  Check(!MaterialIdSelected(selected,otherId),"unselected slot does not enter the selected dependency closure");
  Check(MaterialIdSelected(null,otherId),"unfiltered inventory retains all material dependencies");
  Check(MaterialSelectorDigest(new[]{selectedId,otherId})==MaterialSelectorDigest(new[]{otherId,selectedId}),"selector identity digest is order independent");
  var renamedId=MaterialInventoryIdentity.CreateMaterialId("Avatar/Hair",0,"HairMat","ChangedShader");
  Check(MaterialSelectorDigest(new[]{selectedId,otherId})!=MaterialSelectorDigest(new[]{selectedId,renamedId}),"unselected name or shader identity changes invalidate selector proof");
  return failures==0?0:1;
 }
}
'''.replace("MATERIAL_ID", material_id).replace("STABLE_ID", stable_id).replace("SELECTED", selected).replace("SELECTOR_DIGEST", selector_digest).replace("HASH", hash_method)
    compile_probe(tmp_path, program, 5)
    assert "ReadMaterialSelectionIds(selector)" in source
    assert "RendererComponentIdentity.Create(renderer)" in source
    assert "MaterialInventoryIdentity.CreateMaterialId(" in source
    assert "if (MaterialIdSelected(selectedMaterialIds, materialId)) pending.Enqueue(assignedMaterial);" in source
    assert 'property.propertyPath.StartsWith("m_Materials", StringComparison.Ordinal)) continue;' in source
    assert "foreach (var texture in MaterialTextureDependencies(material, includeTextures)) pending.Enqueue(texture);" in source
    assert "MaterialSelectorDigest(pair.Value)" in source

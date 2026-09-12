from pathlib import Path
import json
import os
import shutil
import subprocess
import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "Assets/VRCForge/Editor/Generic/UnityMaterialVariantFlattenTool.cs").read_text(encoding="utf-8")


def test_flatten_tool_is_single_asset_approval_bound_primitive():
    assert 'toolId: "vrc_flatten_material_variant"' in SOURCE
    assert "Access = VRCForgeCommandAccess.RequiresApproval" in SOURCE
    for field in ("assetPath", "expectedGuid", "expectedDependencyHash", "expectedFileDigest", "preview"):
        assert f"public string {field}" in SOURCE or f"public bool? {field}" in SOURCE
    assert 'path.StartsWith("Assets/"' in SOURCE
    assert "target.parent = null" in SOURCE


def test_flatten_requires_variant_parent_and_reports_no_change_preview_and_persisted_readback():
    assert "Material is already independent; no change was required." in SOURCE
    assert "Material Variant has no resolvable parent" in SOURCE
    assert "Material Variant flatten preview verified; no files were changed." in SOURCE
    assert "AssetDatabase.SaveAssetIfDirty(target);" in SOURCE
    assert "AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport)" in SOURCE
    assert "readback.parent != null" in SOURCE
    assert "EquivalentEffectiveState(before, after)" in SOURCE
    assert '"committed", "persisted", "verified", "flattened"' in SOURCE


def test_flatten_snapshots_generic_shader_properties_and_render_state_without_property_table():
    for token in (
        "GetPropertyCount", "GetPropertyType", "GetFloat", "GetInteger", "GetColor", "GetVector",
        "GetTexture", "GetTextureScale", "GetTextureOffset", "shaderKeywords",
        "renderQueue", "enableInstancing", "doubleSidedGI", "globalIlluminationFlags",
    ):
        assert token in SOURCE
    assert "dissolve" not in SOURCE.casefold()
    assert "File.Replace" in SOURCE
    assert "ParentGuid" in SOURCE
    assert "chain.isVariant && chain.parent == null" in SOURCE


# Execute the actual complete command; only Unity APIs are controlled doubles.
# Real file restoration executes. This does not prove Unity inheritance/save behavior.
STUBS = r'''
namespace VRCForge.Core.MCP {
 public enum VRCForgeCommandAccess { RequiresApproval }
 public class VRCForgeCommandAttribute:Attribute { public VRCForgeCommandAttribute(string toolId){} public string Summary{get;set;} public VRCForgeCommandAccess Access{get;set;} }
 public class VRCForgeInputAttribute:Attribute {public VRCForgeInputAttribute(string s){}public bool IsRequired{get;set;}}
 public class VRCForgeToolResult {
  public bool Ok;public string Message;public object Payload;
  public static object Completed(string m,object p=null)=>new VRCForgeToolResult{Ok=true,Message=m,Payload=p};
  public static object Failed(string m,object p=null)=>new VRCForgeToolResult{Message=m,Payload=p};
 }
}
namespace UnityEngine.Rendering {public enum ShaderPropertyType{Float,Range,Int,Color,Vector,Texture}}
namespace UnityEngine {
 public class Object {public string name;public bool Dirty;public int GetInstanceID()=>1;}
 public struct Hash128 {public string Text;public override string ToString()=>Text;}
 public struct Vector2 {public float x,y;}public struct Vector4 {public float x,y,z,w;}public struct Color {public float r,g,b,a;}
 public class Texture:Object{}
 public class Shader:Object {public int GetPropertyCount()=>0;public string GetPropertyName(int i)=>"";public Rendering.ShaderPropertyType GetPropertyType(int i)=>Rendering.ShaderPropertyType.Float;}
 public class Material:Object {
  public Material parent;public bool isVariant=>parent!=null;public Shader shader;
  public int renderQueue=2000,globalIlluminationFlags;public bool enableInstancing,doubleSidedGI;public string[] shaderKeywords=new[]{"ACTIVE"};
  public float GetFloat(string n)=>.25f;public int GetInteger(string n)=>7;public Color GetColor(string n)=>new Color();public Vector4 GetVector(string n)=>new Vector4();
  public Texture GetTexture(string n)=>null;public Vector2 GetTextureScale(string n)=>new Vector2();public Vector2 GetTextureOffset(string n)=>new Vector2();
 }
}
namespace UnityEditor {
 using UnityEngine;
 using Object=UnityEngine.Object;
 public enum ImportAssetOptions{ForceSynchronousImport}
 public static class EditorUtility {public static bool IsDirty(Object o)=>o.Dirty;public static void SetDirty(Object o){o.Dirty=true;}}
 public static class Undo {
  public static void IncrementCurrentGroup(){}public static int GetCurrentGroup()=>1;public static void SetCurrentGroupName(string s){}
  public static void RegisterCompleteObjectUndo(Object o,string s){}public static void CollapseUndoOperations(int i){}public static void FlushUndoRecordObjects(){}
  public static void RevertAllDownToGroup(int i){AssetDatabase.Target.parent=AssetDatabase.Parent;AssetDatabase.Target.renderQueue=2000;}
 }
 public static class AssetDatabase {
  public static Material Target,Parent,Unrelated;public static Shader Shader=new Shader{name="Generic"};
  public static string TargetGuid=new string('a',32),Fault="";public static int Imports,GlobalSaves,TargetSaves;
  public const string TargetPath="Assets/Target.mat",ParentPath="Assets/Parent.mat";
  public static T LoadAssetAtPath<T>(string p) where T:class=>(p==TargetPath?Target:p==ParentPath?Parent:null) as T;
  public static string GetAssetPath(Object o)=>o==Target?TargetPath:o==Parent?ParentPath:o==Shader?"Assets/Generic.shader":"";
  public static string AssetPathToGUID(string p)=>p==TargetPath?TargetGuid:"guid-"+p;
  public static Hash128 GetAssetDependencyHash(string p)=>new Hash128{Text=Convert.ToHexString(MD5.HashData(System.IO.File.ReadAllBytes(p))).ToLowerInvariant()};
  public static void SaveAssetIfDirty(Object o){TargetSaves++;System.IO.File.WriteAllText(TargetPath,"flat");o.Dirty=false;}
  public static void SaveAssets(){GlobalSaves++;Unrelated.Dirty=false;}
  public static void ImportAsset(string p,ImportAssetOptions opts){
   Imports++;Target.parent=System.IO.File.ReadAllText(p)=="variant"?Parent:null;Target.renderQueue=2000;Target.Dirty=false;
   if(Imports==1){if(Fault=="guid")TargetGuid="changed-guid";if(Fault=="meta")System.IO.File.WriteAllText(p+".meta","changed-meta");if(Fault=="effective")Target.renderQueue=2222;}
  }
 }
}
namespace VRCForge.Editor {
 public class FileEvidence {public string Digest;}public class StableAssetEvidence {public string Guid;public FileEvidence File,Meta;}
 public static class SceneObjectCopyCore {
  public static string ToAbsoluteAssetPath(string p){var full=System.IO.Path.GetFullPath(p);if(!full.StartsWith(System.IO.Directory.GetCurrentDirectory()+System.IO.Path.DirectorySeparatorChar))throw new InvalidOperationException("escape");return full;}
  public static StableAssetEvidence ReadStableAssetEvidence(string p,string label)=>new StableAssetEvidence{Guid=UnityEditor.AssetDatabase.AssetPathToGUID(p),File=new FileEvidence{Digest=Digest(p)},Meta=new FileEvidence{Digest=Digest(p+".meta")}};
  private static string Digest(string p){using(var s=SHA256.Create())return Convert.ToHexString(s.ComputeHash(System.IO.File.ReadAllBytes(p))).ToLowerInvariant();}
 }
}
public static class Probe {
 public static int Main(string[] args){
  System.IO.Directory.CreateDirectory("Assets");
  UnityEditor.AssetDatabase.Parent=new UnityEngine.Material{name="parent",shader=UnityEditor.AssetDatabase.Shader};
  UnityEditor.AssetDatabase.Target=new UnityEngine.Material{name="target",shader=UnityEditor.AssetDatabase.Shader,parent=UnityEditor.AssetDatabase.Parent};
  UnityEditor.AssetDatabase.Unrelated=new UnityEngine.Material{name="unrelated",Dirty=true};
  System.IO.File.WriteAllText("Assets/Target.mat","variant");System.IO.File.WriteAllText("Assets/Target.mat.meta","original-meta");
  System.IO.File.WriteAllText("Assets/Parent.mat","parent");System.IO.File.WriteAllText("Assets/Parent.mat.meta","parent-meta");
  var request=new JObject{["assetPath"]="Assets/Target.mat",["preview"]=true};
  var preview=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.UnityMaterialVariantFlattenTool.HandleCommand(request);
  var plan=JObject.FromObject(preview.Payload);request["preview"]=false;request["expectedGuid"]=plan["guid"];request["expectedDependencyHash"]=plan["dependencyHash"];request["expectedFileDigest"]=plan["fileDigest"];
  UnityEditor.AssetDatabase.Fault=args[0];if(args[0]=="stale"){UnityEditor.AssetDatabase.Target.parent=null;request["expectedGuid"]="stale";}
  var r=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.UnityMaterialVariantFlattenTool.HandleCommand(request);
  var again=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.UnityMaterialVariantFlattenTool.HandleCommand(new JObject{["assetPath"]="Assets/Target.mat",["preview"]=true});
  Console.WriteLine(new JObject{["ok"]=r.Ok,["message"]=r.Message,["previewPayload"]=plan,["repeatPreviewPayload"]=JObject.FromObject(again.Payload),["payload"]=JObject.FromObject(r.Payload),["globalSaves"]=UnityEditor.AssetDatabase.GlobalSaves,["targetSaves"]=UnityEditor.AssetDatabase.TargetSaves,["unrelatedDirty"]=UnityEditor.AssetDatabase.Unrelated.Dirty,["file"]=System.IO.File.ReadAllText("Assets/Target.mat"),["meta"]=System.IO.File.ReadAllText("Assets/Target.mat.meta")}.ToString(Newtonsoft.Json.Formatting.None));return 0;
 }
}
'''


@pytest.fixture(scope="module")
def compiled_flatten(tmp_path_factory):
    roots = [Path(os.environ.get("DOTNET_ROOT", "__missing__")),
             Path.home() / "AppData/Local/Microsoft/dotnet",
             Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"]
    selected = next(((r, sorted((r / "sdk").glob("8.*/Roslyn/bincore/csc.dll")),
                      sorted((r / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0")))
                     for r in roots if list((r / "sdk").glob("8.*/Roslyn/bincore/csc.dll"))
                     and list((r / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))), None)
    if selected is None:
        pytest.skip("Local SDK8 compiler and net8.0 reference pack required")
    root, compilers, refs = selected
    dotnet = root / ("dotnet.exe" if os.name == "nt" else "dotnet")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    folder = tmp_path_factory.mktemp("flatten-sdk8")
    cs = folder / "Probe.cs"
    cs.write_text(SOURCE + "\n" + STUBS, encoding="utf-8")
    dll = folder / "Probe.dll"
    command = [str(dotnet), str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")]
    command += [f"-r:{newtonsoft}", str(cs)]
    # Finite test-owned child, no listener/auth; closed stdin and captured pipes.
    compiled = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(newtonsoft, folder / "Newtonsoft.Json.dll")
    (folder / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}), encoding="utf-8")
    return dotnet, dll


@pytest.mark.parametrize("case", ["success", "receipt", "guid", "meta", "effective", "stale"])
def test_actual_command_save_readback_and_restore(compiled_flatten, tmp_path, case):
    dotnet, dll = compiled_flatten
    result = subprocess.run([str(dotnet), str(dll), case], cwd=tmp_path, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    result = json.loads(result.stdout)
    payload = result["payload"]
    if case in {"success", "receipt"}:
        assert result["ok"] is True
        if case == "success":
            assert result["globalSaves"] == 0 and result["unrelatedDirty"] is True
        assert result["targetSaves"] == 1
        assert payload["parent"] == "" and payload["isVariant"] is False
        assert payload["readback"]["verified"] is True
        assert payload["readback"]["parent"] == "" and payload["readback"]["isVariant"] is False
        assert payload["readback"]["guid"] == "a" * 32
        assert payload["committed"] is True and payload["persistedReadback"] is True
    elif case in {"guid", "meta"}:
        assert result["ok"] is False
        assert payload["restored"] is False and payload["commitState"] == "unknown"
        assert payload["checkpointRecoveryRequired"] is True
    elif case == "effective":
        assert result["ok"] is False and result["file"] == "variant"
        assert result["meta"] == "original-meta" and payload["restored"] is True
        assert payload["commitState"] == "rolled_back" and payload["checkpointRecoveryRequired"] is False
    else:
        assert result["ok"] is False and result["targetSaves"] == 0
        assert payload["mutationStarted"] is False


def test_actual_csharp_receipts_cross_python_authoritative_boundary(compiled_flatten, tmp_path):
    from copy import deepcopy
    from authoritative_unity_writes import prepare_authoritative_unity_write, validate_authoritative_unity_write_result, AuthoritativeUnityWriteError
    dotnet, dll = compiled_flatten
    proc = subprocess.run([str(dotnet), str(dll), "success"], cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    receipts = json.loads(proc.stdout)
    assert receipts["ok"] is True
    request = {"toolName": "vrc_flatten_material_variant", "projectPath": str(tmp_path), "arguments": {"assetPath": "Assets/Target.mat"}}
    calls = []
    canonical, preview = prepare_authoritative_unity_write(request, None, lambda name, args: calls.append((name, args)) or receipts["previewPayload"])
    assert calls[0][0] == request["toolName"] and calls[0][1]["preview"] is True
    assert preview["effectiveSnapshot"] == receipts["payload"]["after"]
    actual = validate_authoritative_unity_write_result(canonical, receipts["payload"])
    assert actual["persistedReadback"] is True and actual["isVariant"] is False
    assert actual["fileDigest"] != canonical["arguments"]["expectedFileDigest"]
    repeat, repeat_preview = prepare_authoritative_unity_write(request, None, lambda *args: receipts["repeatPreviewPayload"])
    assert repeat_preview["variant"] is False
    assert validate_authoritative_unity_write_result(repeat, receipts["repeatPreviewPayload"])["commitState"] == "not_started"
    for path, value in [("guid", "f" * 32), ("isVariant", True), ("verified", False), ("pending", True), ("ok", False)]:
        bad = deepcopy(actual); bad[path] = value
        with pytest.raises(AuthoritativeUnityWriteError):
            validate_authoritative_unity_write_result(canonical, bad)
    bad = deepcopy(actual); bad["readback"]["effectiveState"]["renderQueue"] = -99
    with pytest.raises(AuthoritativeUnityWriteError):
        validate_authoritative_unity_write_result(canonical, bad)
    bad = deepcopy(actual); bad["readback"]["guid"] = "f" * 32
    with pytest.raises(AuthoritativeUnityWriteError):
        validate_authoritative_unity_write_result(canonical, bad)

from pathlib import Path
from copy import deepcopy
import json
import pytest
import material_shader_assignment as assignment

ROOT = Path(__file__).resolve().parents[1]
REQUEST = {'projectPath': 'D:/Project', 'materialAssetPath': 'Assets/Target.mat', 'keywordChanges': [{'keyword': 'NEW', 'enabled': True}]}

def test_existing_public_wrapper_retains_keyword_only_request():
    wrapper = assignment.build_wrapper_arguments(REQUEST)
    assert wrapper['arguments'] == {k: REQUEST[k] for k in ('materialAssetPath', 'keywordChanges')}
    assert assignment.build_preview_arguments(wrapper['arguments'])['keywordChanges'] == REQUEST['keywordChanges']

import os
import shutil
import subprocess
from test_material_variant_flatten_contract import STUBS as BASE_STUBS
SOURCE = 'using System.Security.Cryptography;\n' + (ROOT / 'Assets/VRCForge/Editor/Generic/UnityMaterialKeywordEdit.cs').read_text(encoding='utf-8')
STUBS = BASE_STUBS[:BASE_STUBS.index('public static class Probe')]
STUBS = STUBS.replace('public enum ShaderPropertyType', 'public struct LocalKeyword { public string name; public bool isValid; } public struct LocalKeywordSpace { public LocalKeyword FindKeyword(string n)=>new LocalKeyword{name=n,isValid=n=="NEW" || n=="ACTIVE"}; } public enum ShaderPropertyType')
STUBS = STUBS.replace('public int GetPropertyCount()=>0;', 'public Rendering.LocalKeywordSpace keywordSpace=>new Rendering.LocalKeywordSpace(); public int GetPropertyCount()=>3;').replace('GetPropertyName(int i)=>""', 'GetPropertyName(int i)=>"_Value"')
STUBS = STUBS.replace('public float GetFloat(string n)=>.25f;', 'public float Value=.25f; public float GetFloat(string n)=>Value; public void SetKeyword(Rendering.LocalKeyword k,bool enabled){var set=new HashSet<string>(shaderKeywords);if(enabled)set.Add(k.name);else set.Remove(k.name);shaderKeywords=set.OrderBy(x=>x,StringComparer.Ordinal).ToArray();}')
STUBS = STUBS.replace('"guid-"+p', "new string('b',32)")
STUBS = STUBS.replace('public static void SaveAssetIfDirty(Object o){TargetSaves++;System.IO.File.WriteAllText(TargetPath,"flat");o.Dirty=false;}', 'public static bool TryGetGUIDAndLocalFileIdentifier(Object o,out string guid,out long id){guid=new string((char)98,32);id=4800000;return true;} public static void SaveAssetIfDirty(Object o){TargetSaves++;System.IO.File.WriteAllText(TargetPath,string.Join(",",Target.shaderKeywords));o.Dirty=false;if(Fault=="save")throw new Exception("save fault");}')
STUBS = STUBS.replace('Imports++;Target.parent=System.IO.File.ReadAllText(p)=="variant"?Parent:null;Target.renderQueue=2000;Target.Dirty=false;', 'Imports++;Target.parent=null;Target.shaderKeywords=System.IO.File.ReadAllText(p).Split((char)44);Target.Value=.25f;Target.renderQueue=2000;Target.Dirty=false;')
STUBS = STUBS.replace('namespace VRCForge.Editor {', 'namespace VRCForge.Editor { public static class MaterialShaderTool { public static bool MatchesCurrentProject(string p)=>p=="Project"; }')
STUBS = STUBS.replace('Rendering.ShaderPropertyType GetPropertyType(int i)=>Rendering.ShaderPropertyType.Float;', 'Rendering.ShaderPropertyType GetPropertyType(int i)=>i==0?Rendering.ShaderPropertyType.Float:i==1?Rendering.ShaderPropertyType.Int:Rendering.ShaderPropertyType.Texture;')
STUBS = STUBS.replace('public Texture GetTexture(string n)=>null;', 'public Texture GetTexture(string n)=>new Texture{name="Texture"};').replace('o==Shader?"Assets/Generic.shader":"";', 'o==Shader?"Assets/Generic.shader":o is Texture?"Assets/Texture.png":"";')
STUBS += r'''public static class Probe {
 public static int Main(string[] argv){
  System.IO.Directory.CreateDirectory("Assets");
  UnityEditor.AssetDatabase.Target=new UnityEngine.Material{name="target",shader=UnityEditor.AssetDatabase.Shader};
  UnityEditor.AssetDatabase.Parent=new UnityEngine.Material{name="parent",shader=UnityEditor.AssetDatabase.Shader};
  UnityEditor.AssetDatabase.Unrelated=new UnityEngine.Material{name="unrelated",Dirty=true};
  System.IO.File.WriteAllText("Assets/Target.mat","ACTIVE");System.IO.File.WriteAllText("Assets/Target.mat.meta","original-meta");System.IO.File.WriteAllText("Assets/Generic.shader","shader");
  var args=new JObject{["materialAssetPath"]="Assets/Target.mat",["expectedProjectPath"]="Project",["preview"]=true,["keywordChanges"]=new JArray(new JObject{["keyword"]="NEW",["enabled"]=true})};
  if(argv[0]=="nochange")args["keywordChanges"][0]["keyword"]="ACTIVE";
  var p=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.UnityMaterialKeywordEdit.HandleCommand(args);var preview=(JObject)p.Payload;
  args["expectedKeywordEvidence"]=preview["before"].DeepClone();args["preview"]=false;
  UnityEditor.AssetDatabase.Fault=argv[0];
  if(argv[0]=="stale"){args["expectedKeywordEvidence"]["fileDigest"]="stale";args["keywordChanges"][0]["keyword"]="ACTIVE";}
  if(argv[0]=="invalid")args["keywordChanges"][0]["keyword"]="UNDECLARED";
  if(argv[0]=="dirty")UnityEditor.AssetDatabase.Target.Dirty=true;
  if(argv[0]=="variant")UnityEditor.AssetDatabase.Target.parent=UnityEditor.AssetDatabase.Parent;
  if(argv[0]=="shaderstale")System.IO.File.WriteAllText("Assets/Generic.shader","different");
  var r=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.UnityMaterialKeywordEdit.HandleCommand(args);
  Console.WriteLine(new JObject{["ok"]=r.Ok,["message"]=r.Message,["previewPayload"]=preview,["payload"]=JObject.FromObject(r.Payload),["globalSaves"]=UnityEditor.AssetDatabase.GlobalSaves,["targetSaves"]=UnityEditor.AssetDatabase.TargetSaves,["unrelatedDirty"]=UnityEditor.AssetDatabase.Unrelated.Dirty,["file"]=System.IO.File.ReadAllText("Assets/Target.mat"),["tempCount"]=System.IO.Directory.GetFiles("Assets","*.tmp").Length}.ToString(Newtonsoft.Json.Formatting.None));return 0;
 }
}
'''
@pytest.fixture(scope="module")
def compiled_keyword(tmp_path_factory):
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
    folder = tmp_path_factory.mktemp("keyword-sdk8")
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



def run_command(compiled_keyword, tmp_path, case):
    dotnet, dll = compiled_keyword
    result = subprocess.run([str(dotnet), str(dll), case], cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)

@pytest.mark.parametrize('case', ['success', 'nochange', 'stale', 'invalid', 'dirty', 'variant', 'shaderstale', 'effective', 'guid', 'meta', 'save'])
def test_actual_csharp_command(compiled_keyword, tmp_path, case):
    result = run_command(compiled_keyword, tmp_path, case)
    p = result['payload']
    assert result['globalSaves'] == 0 and result['unrelatedDirty'] is True
    assert result['tempCount'] == 0
    if case in ('success', 'nochange'):
        assert result['ok'] is True, result
        assert p['persistedReadback'] is True and p['committed'] is True
        assert result['targetSaves'] == (case == 'success')
        assert p['readback']['state']['keywords'] == (['ACTIVE', 'NEW'] if case == 'success' else ['ACTIVE'])
    elif case in ('guid', 'meta', 'save'):
        assert result['ok'] is False and p['commitState'] == 'unknown'
        assert p['checkpointRecoveryRequired'] is True
    elif case == 'effective':
        assert result['ok'] is False and p['commitState'] == 'rolled_back', result
        assert result['file'] == 'ACTIVE'
    else:
        assert result['ok'] is False and p['mutationStarted'] is False
        assert result['targetSaves'] == 0


def test_real_core_payload_through_authoritative_boundary(compiled_keyword, tmp_path):
    from authoritative_unity_writes import prepare_authoritative_unity_write, validate_authoritative_unity_write_result
    result = run_command(compiled_keyword, tmp_path, 'success')
    wrapper = assignment.build_wrapper_arguments(dict(REQUEST, projectPath=str(tmp_path)))
    canonical, approval = prepare_authoritative_unity_write(wrapper, None, lambda name, args: result['previewPayload'])
    assert canonical['arguments']['expectedKeywordEvidence'] == result['previewPayload']['before']
    assert canonical['arguments']['expectedProjectPath'] == str(tmp_path)
    validated = validate_authoritative_unity_write_result(canonical, result['payload'])
    assert validated['readback']['state']['keywords'] == ['ACTIVE', 'NEW']
    bad = deepcopy(result['payload']); bad['readback']['state']['renderQueue'] = 2222
    with pytest.raises(ValueError):
        validate_authoritative_unity_write_result(canonical, bad)


def test_keyword_bind_rejects_conflicting_project_identity():
    import material_keyword_edit as keyword
    payload = {"schema": keyword.SCHEMA, "ok": True, "preview": True, "verified": True, "mutationStarted": False, "committed": False, "materialAssetPath": "Assets/Target.mat", "keywordChanges": [{"keyword": "NEW", "enabled": True}], "before": {"materialAssetPath": "Assets/Target.mat", "guid": "a" * 32, "fileDigest": "b" * 64, "metaDigest": "c" * 64, "state": {"keywords": [], "isVariant": False, "parent": "", "shader": {"name": "S", "path": "Assets/S.shader", "guid": "d" * 32, "localId": 1}, "properties": []}}, "after": {"keywords": ["NEW"], "isVariant": False, "parent": "", "shader": {"name": "S", "path": "Assets/S.shader", "guid": "d" * 32, "localId": 1}, "properties": []}, "wouldChange": True}
    with pytest.raises(ValueError, match="expectedProjectPath"):
        keyword.bind({"projectPath": "D:/Project", "arguments": {"materialAssetPath": "Assets/Target.mat", "keywordChanges": [{"keyword": "NEW", "enabled": True}], "expectedProjectPath": "D:/Other"}}, payload)


def test_keyword_bind_preserves_missing_project_and_normalizes_separators():
    import material_keyword_edit as keyword
    before = {"materialAssetPath": "Assets/Target.mat", "guid": "a" * 32, "fileDigest": "b" * 64, "metaDigest": "c" * 64, "state": {"keywords": [], "isVariant": False, "parent": "", "shader": {"name": "S", "path": "Assets/S.shader", "guid": "d" * 32, "localId": 1, "dependencyHash": "e" * 32}, "properties": []}}
    payload = {"schema": keyword.SCHEMA, "ok": True, "preview": True, "verified": True, "mutationStarted": False, "committed": False, "materialAssetPath": "Assets/Target.mat", "keywordChanges": [{"keyword": "NEW", "enabled": True}], "before": before, "after": {**before["state"], "keywords": ["NEW"]}, "wouldChange": True}
    canonical, _ = keyword.bind({"projectPath": "D:/Project", "arguments": {"materialAssetPath": "Assets/Target.mat", "keywordChanges": [{"keyword": "NEW", "enabled": True}]}}, payload)
    assert canonical["arguments"]["expectedProjectPath"] == "D:/Project"
    canonical, _ = keyword.bind({"projectPath": "D:/Project", "arguments": {"materialAssetPath": "Assets/Target.mat", "keywordChanges": [{"keyword": "NEW", "enabled": True}], "expectedProjectPath": "D:\\Project"}}, payload)
    assert canonical["arguments"]["expectedProjectPath"] == "D:\\Project"
    canonical, _ = keyword.bind({"arguments": {"materialAssetPath": "Assets/Target.mat", "keywordChanges": [{"keyword": "NEW", "enabled": True}], "expectedProjectPath": "D:\\Project"}}, payload)
    assert canonical["arguments"]["expectedProjectPath"] == "D:\\Project"

@pytest.mark.parametrize('extra', [{'shaderName': 'Other'}, {'assignments': []}, {'slotIndex': 0}])
def test_keyword_mode_rejects_mixed_requests(extra):
    with pytest.raises(ValueError):
        assignment.build_wrapper_arguments(dict(REQUEST, **extra))

def test_public_schema_accepts_keyword_only_and_legacy_shader_modes():
    import jsonschema
    from unity_shared_input_schemas import MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA as schema
    jsonschema.validate(REQUEST, schema)
    jsonschema.validate({'projectPath': 'P', 'materialAssetPath': 'Assets/A.mat', 'shaderName': 'A'}, schema)
    jsonschema.validate({'projectPath': 'P', 'rendererPath': 'Avatar/Body', 'slotIndex': 0, 'shaderName': 'A'}, schema)
    jsonschema.validate({'projectPath': 'P', 'assignments': [{'materialAssetPath': 'Assets/A.mat', 'shaderName': 'A'}]}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(dict(REQUEST, shaderName='Other'), schema)

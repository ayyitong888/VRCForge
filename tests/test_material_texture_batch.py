from copy import deepcopy
import pytest
import material_texture_assignment as m

ROWS = [{'materialAssetPath':'Assets/A.mat','propertyName':'_Mask','textureAssetPath':'Assets/New.png'}, {'materialAssetPath':'Assets/B.mat','propertyName':'_Mask','textureAssetPath':'Assets/New.png'}]

def test_batch_preview_uses_batch_core_envelope():
    result=m.build_preview_arguments({'assignments':ROWS})
    assert result == {'assignments':ROWS,'preview':True}

import json, os, shutil, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'Assets/VRCForge/Editor/Generic/UnityMaterialTextureBatch.cs').read_text(encoding='utf-8')
STUBS=r'''
namespace VRCForge.Core.MCP {public class VRCForgeToolResult {public bool Ok;public object Payload;public string Message;public static object Completed(string m,object p)=>new VRCForgeToolResult{Ok=true,Payload=p,Message=m};public static object FailedWithCode(string c,string m,object p)=>new VRCForgeToolResult{Payload=p,Message=m};}}
namespace UnityEngine {
 public class Object{public string Path;public bool Dirty;}
 public class Shader:Object{}
 public class Texture:Object{} public class Texture2D:Texture{}
 public struct Vector2 {public float x,y;public Vector2(float a,float b){x=a;y=b;}}
 public class Material:Object{public Material parent;public bool isVariant=>parent!=null;public Shader shader=new Shader{Path="Assets/S.shader"};public int Queue=2000;public Dictionary<string,Texture> Props=new Dictionary<string,Texture>();public bool HasProperty(string p)=>p=="_Mask"||p=="_Other";public string[] GetTexturePropertyNames()=>new[]{"_Mask","_Other"};public Texture GetTexture(string p)=>Props.ContainsKey(p)?Props[p]:null;public void SetTexture(string p,Texture t){Props[p]=t;}
 public Vector2 Scale=new Vector2(1,1),Offset=new Vector2(0,0);public Vector2 GetTextureScale(string p)=>Scale;public Vector2 GetTextureOffset(string p)=>Offset;public void SetTextureScale(string p,Vector2 v){Scale=v;}public void SetTextureOffset(string p,Vector2 v){Offset=v;if(UnityEditor.AssetDatabase.Fault=="transform_setter"&&Path=="Assets/B.mat")throw new Exception("setter failure after mutation");}}
 public struct Hash128 {public string Value;public override string ToString()=>Value;}
 public static class Application{public static string dataPath=>System.IO.Path.Combine(System.IO.Directory.GetCurrentDirectory(),"Assets");}
}
namespace UnityEditor {
 using UnityEngine;
 [Flags]public enum ImportAssetOptions{ForceSynchronousImport=1,ForceUpdate=2}
 public static class EditorUtility{public static bool IsDirty(UnityEngine.Object o)=>o.Dirty;public static void SetDirty(UnityEngine.Object o){o.Dirty=true;}}
 public static class EditorJsonUtility{public static string ToJson(Material m)=>new JObject{["queue"]=m.Queue,["mask"]=m.GetTexture("_Mask")?.Path??"",["other"]=m.GetTexture("_Other")?.Path??"",["scale"]=new JArray(m.Scale.x,m.Scale.y),["offset"]=new JArray(m.Offset.x,m.Offset.y)}.ToString(Formatting.None);}
 public static class AssetDatabase{
  public static Dictionary<string,Material> Mats=new Dictionary<string,Material>();public static Dictionary<string,Texture2D> Tex=new Dictionary<string,Texture2D>();public static string Fault="";public static int Saves,Globals,Imports;public static bool UnrelatedDirty=true;
  public static T LoadAssetAtPath<T>(string p) where T:class=>(Mats.ContainsKey(p)?(object)Mats[p]:Tex.ContainsKey(p)?Tex[p]:null) as T;
  public static string GetAssetPath(UnityEngine.Object o)=>o?.Path??"";
  public static string AssetPathToGUID(string p)=>p=="Assets/A.mat"?new string('a',32):p=="Assets/B.mat"?new string('b',32):new string('c',32);
  public static bool TryGetGUIDAndLocalFileIdentifier(UnityEngine.Object o,out string guid,out long id){guid=AssetPathToGUID(o.Path);id=2800000;return true;}
  public static Hash128 GetAssetDependencyHash(string p)=>new Hash128{Value=VRCForge.Editor.MaterialShaderTool.ComputeFileSha256(p)};
  public static void SaveAssetIfDirty(UnityEngine.Object o){Saves++;if(Fault=="save"&&Saves==2)throw new Exception("second save failure");File.WriteAllText(o.Path,EditorJsonUtility.ToJson((Material)o));o.Dirty=false;}
  public static void SaveAssets(){Globals++;UnrelatedDirty=false;foreach(var m in Mats.Values)SaveAssetIfDirty(m);}
  public static void Refresh(ImportAssetOptions o){foreach(var p in Mats.Keys.ToArray())ImportAsset(p,o);}
  public static void ImportAsset(string p,ImportAssetOptions opts){Imports++;var j=JObject.Parse(File.ReadAllText(p));var m=Mats[p];m.Queue=(int)j["queue"];m.Props["_Mask"]=Tex[j["mask"].ToString()];m.Props["_Other"]=Tex[j["other"].ToString()];m.Scale=new Vector2((float)j["scale"][0],(float)j["scale"][1]);m.Offset=new Vector2((float)j["offset"][0],(float)j["offset"][1]);m.Dirty=false;if(Fault=="meta"&&Imports==1)File.WriteAllText(p+".meta","foreign");}
 }
}
namespace VRCForge.Editor {
 public class FileEvidence{public string Digest;}public class StableAssetEvidence{public string Guid;public FileEvidence File,Meta;}
 public static class SceneObjectCopyCore{public static StableAssetEvidence ReadStableAssetEvidence(string p,string l)=>new StableAssetEvidence{Guid=UnityEditor.AssetDatabase.AssetPathToGUID(p),File=new FileEvidence{Digest=MaterialShaderTool.ComputeFileSha256(p)},Meta=new FileEvidence{Digest=MaterialShaderTool.ComputeFileSha256(p+".meta")}};}
 public static class MaterialShaderTool{
  public class MaterialAssetEvidence{public string assetGuid,fileDigest,filePath;}
  public static bool MatchesCurrentProject(string p)=>p==Directory.GetCurrentDirectory();public static void EnsureNoReparseBoundary(string r,string p){}
  public static string ComputeFileSha256(string p){using(var h=System.Security.Cryptography.SHA256.Create())return BitConverter.ToString(h.ComputeHash(File.ReadAllBytes(p))).Replace("-","").ToLowerInvariant();}
  public static MaterialAssetEvidence InspectWritableMaterialAsset(UnityEngine.Material m){if(m.Dirty)throw new Exception("dirty");return new MaterialAssetEvidence{assetGuid=UnityEditor.AssetDatabase.AssetPathToGUID(m.Path),fileDigest=ComputeFileSha256(m.Path),filePath=Path.GetFullPath(m.Path)};}
 }
}
public static class Probe{
 public static int Main(string[] argv){
  Directory.CreateDirectory("Assets");File.WriteAllText("Assets/S.shader","shader");
  foreach(var p in new[]{"Assets/Old.png","Assets/New.png"}){UnityEditor.AssetDatabase.Tex[p]=new UnityEngine.Texture2D{Path=p};File.WriteAllText(p,p);}
  foreach(var p in new[]{"Assets/A.mat","Assets/B.mat"}){var m=new UnityEngine.Material{Path=p};m.Props["_Mask"]=UnityEditor.AssetDatabase.Tex["Assets/Old.png"];m.Props["_Other"]=UnityEditor.AssetDatabase.Tex["Assets/Old.png"];UnityEditor.AssetDatabase.Mats[p]=m;File.WriteAllText(p,UnityEditor.EditorJsonUtility.ToJson(m));File.WriteAllText(p+".meta",p);}
  var rows=new JArray();foreach(var p in new[]{"Assets/A.mat","Assets/B.mat"})rows.Add(new JObject{["materialAssetPath"]=p,["propertyName"]="_Mask",["textureAssetPath"]=argv[0]=="nochange"?"Assets/Old.png":"Assets/New.png"});
  if(argv[0]=="same")rows.Add(new JObject{["materialAssetPath"]="Assets/A.mat",["propertyName"]="_Other",["textureAssetPath"]="Assets/New.png"});
  if(argv[0]=="invalid")rows[1]["propertyName"]="_Missing";
  var args=new JObject{["assignments"]=rows,["expectedProjectPath"]=Directory.GetCurrentDirectory(),["preview"]=true};
  if(argv[0].StartsWith("transform")) {
   foreach(JObject row in rows){row["textureAssetPath"]="Assets/Old.png";row["textureScale"]=new JObject{["x"]=argv[0]=="transform_nochange"?1:.5,["y"]=argv[0]=="transform_nochange"?1:.75};row["textureOffset"]=new JObject{["x"]=argv[0]=="transform_nochange"?0:.125,["y"]=argv[0]=="transform_nochange"?0:-.25};}
   if(argv[0]=="transform_invalid")rows[1]["textureScale"]["x"]=double.PositiveInfinity;
   if(argv[0]=="transform_scale")foreach(JObject row in rows)row.Remove("textureOffset");
   if(argv[0]=="transform_offset")foreach(JObject row in rows)row.Remove("textureScale");
   if(argv[0]=="transform_decimal")foreach(JObject row in rows)row["textureScale"]["x"]=.1;
   if(argv[0]=="transform_single"){args=(JObject)rows[0].DeepClone();args["expectedProjectPath"]=Directory.GetCurrentDirectory();args["preview"]=true;}
  }
  var pre=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.UnityMaterialTextureBatch.HandleCommand(args);var pp=JObject.FromObject(pre.Payload);
  args["preview"]=false;args["expectedBatchPlan"]=new JObject{["assignments"]=pp["assignments"]?.DeepClone()};
  if(argv[0]=="stale")File.WriteAllText("Assets/B.mat.meta","stale");
  if(argv[0]=="transform_stale")UnityEditor.AssetDatabase.Mats["Assets/B.mat"].Scale=new UnityEngine.Vector2(2,2);
  UnityEditor.AssetDatabase.Fault=argv[0];
  if(argv[0]=="transform_save")UnityEditor.AssetDatabase.Fault="save";
  var r=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.UnityMaterialTextureBatch.HandleCommand(args);
  Console.WriteLine(new JObject{["ok"]=r.Ok,["message"]=r.Message,["previewPayload"]=pp,["payload"]=JObject.FromObject(r.Payload),["saves"]=UnityEditor.AssetDatabase.Saves,["globals"]=UnityEditor.AssetDatabase.Globals,["unrelatedDirty"]=UnityEditor.AssetDatabase.UnrelatedDirty,["a"]=JObject.Parse(File.ReadAllText("Assets/A.mat")),["b"]=JObject.Parse(File.ReadAllText("Assets/B.mat")),["memoryA"]=JObject.Parse(UnityEditor.EditorJsonUtility.ToJson(UnityEditor.AssetDatabase.Mats["Assets/A.mat"])),["memoryB"]=JObject.Parse(UnityEditor.EditorJsonUtility.ToJson(UnityEditor.AssetDatabase.Mats["Assets/B.mat"]))}.ToString(Formatting.None));return 0;
 }
}
'''
@pytest.fixture(scope="module")
def compiled_texture(tmp_path_factory):
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
    folder = tmp_path_factory.mktemp("texture-sdk8")
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




def run_batch(compiled_texture,tmp_path,case):
    dotnet,dll=compiled_texture
    result=subprocess.run([str(dotnet),str(dll),case],cwd=tmp_path,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads(result.stdout)

@pytest.mark.parametrize('case',['success','nochange','same','invalid','stale','save','meta'])
def test_actual_complete_batch(compiled_texture,tmp_path,case):
    result=run_batch(compiled_texture,tmp_path,case);payload=result['payload']
    assert result['globals']==0 and result['unrelatedDirty'] is True
    if case in ('success','same','nochange'):
        assert result['ok'] is True,result
        assert result['saves']==(0 if case=='nochange' else 2)
        assert payload['persistedReadback'] is True
        assert result['a']['mask']==('Assets/Old.png' if case=='nochange' else 'Assets/New.png')
        assert result['a']['other']==('Assets/New.png' if case=='same' else 'Assets/Old.png')
    elif case=='save':
        assert result['ok'] is False and payload['commitState']=='rolled_back',result
        for key in ('a','b','memoryA','memoryB'):assert result[key]['mask']=='Assets/Old.png'
    elif case=='meta':
        assert result['ok'] is False and payload['checkpointRecoveryRequired'] is True
    else:
        assert result['ok'] is False and payload['mutationStarted'] is False and result['saves']==0

def test_actual_payload_crosses_authoritative_preflight_and_apply(compiled_texture,tmp_path):
    from authoritative_unity_writes import prepare_authoritative_unity_write,validate_authoritative_unity_write_result
    result=run_batch(compiled_texture,tmp_path,'success')
    request=m.build_wrapper_arguments({'projectPath':str(tmp_path),'assignments':ROWS})
    canonical,preview=prepare_authoritative_unity_write(request,None,lambda name,args:result['previewPayload'])
    assert canonical['arguments']['expectedBatchPlan']['assignments']==result['previewPayload']['assignments']
    assert validate_authoritative_unity_write_result(canonical,result['payload'])['committed'] is True
    for mutate in ('count','guid','order'):
        bad=deepcopy(result['payload'])
        if mutate=='count':bad['readback'].pop()
        elif mutate=='guid':bad['readback'][0]['materialAssetGuid']='d'*32
        else:bad['readback'].reverse()
        with pytest.raises(ValueError):validate_authoritative_unity_write_result(canonical,bad)

def test_public_batch_and_legacy_schema():
    import jsonschema
    from unity_shared_input_schemas import MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA as schema
    jsonschema.validate({'projectPath':'Project','assignments':ROWS},schema)
    jsonschema.validate(dict(ROWS[0],projectPath='Project'),schema)
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(dict(ROWS[0],projectPath='Project',assignments=ROWS),schema)

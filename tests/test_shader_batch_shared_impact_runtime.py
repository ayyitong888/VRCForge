"""Production shader batch orchestration and impact collectors on narrow Unity seams."""
import json, os, shutil, subprocess
from pathlib import Path
import pytest
from test_curve_fx_authoring_runtime_contract import method
ROOT=Path(__file__).resolve().parents[1]
def dotnet8_runtime():
    base=Path(os.environ.get('DOTNET_ROOT') or (Path.home()/'AppData'/'Local'/'Microsoft'/'dotnet'))
    exe=base/'dotnet.exe'
    return (exe if exe.is_file() else None),base

def test_batch_impact_dependency_reads_once(tmp_path):
    dotnet,base=dotnet8_runtime(); pytest.skip('user .NET 8 runtime is unavailable') if dotnet is None else None
    sdks=sorted((base/'sdk').glob('8.*/Roslyn/bincore/csc.dll')); refs_list=sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('8.*/ref/net8.0'))
    pytest.skip('user .NET 8 SDK/reference pack is unavailable') if not sdks or not refs_list else None
    sdk=sdks[-1]; refs=refs_list[-1]
    dll_json=sdk.parents[2]/'Newtonsoft.Json.dll'
    src=(ROOT/'Assets/VRCForge/Editor/MaterialShaderTool.cs').read_text(encoding='utf-8')
    batch=(ROOT/'Assets/VRCForge/Editor/Generic/UnityMaterialShaderBatch.cs').read_text(encoding='utf-8')
    signatures=['private static SharedMaterialImpactResult BuildSharedMaterialImpact(', 'private static string ComputeImpactPartitionDigest(', 'private static string ComputeImpactCommitment(', 'private static void AppendDigestField(', 'private sealed class RendererSlotImpact', 'private sealed class SharedMaterialImpact\n', 'private sealed class SharedMaterialImpactResult']
    selected=[]
    for sig in signatures:
        if sig not in src: sig=sig.replace('private sealed','internal sealed')
        selected.append(method(src,sig))
    for sig in ['internal sealed class BatchSharedMaterialImpact','internal static BatchSharedMaterialImpact BuildBatchSharedMaterialImpact(', 'internal static SharedMaterialImpactResult ResolveSharedMaterialImpact(', 'private static Dictionary<string, SharedMaterialImpactResult> CollectSharedMaterialImpacts(', 'private static SharedMaterialImpactResult FinalizeSharedMaterialImpact(']:
        if sig in src:selected.append(method(src,sig))
    for sig in ['internal static JObject PreflightShaderAssignment(']:
        if sig in src:selected.append(method(src,sig))
    seam=r'''
namespace UnityEngine {
 public enum HideFlags {HideAndDontSave=61}
 public class Object {public static int Destroyed;public static void DestroyImmediate(Material m){Destroyed++;}}
 public class Material {public string Path;public Material parent;public bool MissingParent,IgnoreShader,ThrowShader;public bool isVariant=>parent!=null||MissingParent;public HideFlags hideFlags;public bool Probe;public static int Writes;private Shader own=new Shader();public Shader shader{get=>parent!=null?parent.shader:own;set{if(!Probe)Writes++;if(ThrowShader)throw new Exception("setter");if(parent==null&&!IgnoreShader)own=value;}}public Material(){}public Material(Material m){Path=m.Path;parent=m.parent;own=m.own;IgnoreShader=m.IgnoreShader;ThrowShader=m.ThrowShader;Probe=true;}}
 public class Shader {public string name="Old";}
 public class Renderer {public string Id;public Material[] sharedMaterials;}
 public static class Resources {public static int Scans;public static T[] FindObjectsOfTypeAll<T>() {Scans++;return UnityEditor.AssetDatabase.Renderers.Cast<T>().ToArray();}}
}
namespace UnityEditor {
 using UnityEngine;
 [Flags]public enum ImportAssetOptions{ForceUpdate=1,ForceSynchronousImport=2}
 public static class EditorJsonUtility {public static string ToJson(Material m)=>m.Path;public static void FromJsonOverwrite(string json,Material m){}}
 public static class EditorUtility {public static bool IsDirty(Material m)=>false;public static void SetDirty(Material m){}}
 public static class AssetDatabase {
  public static Dictionary<string,Material> Mats=new Dictionary<string,Material>();public static Renderer[] Renderers;
  public static Dictionary<string,string[]> Deps=new Dictionary<string,string[]>();public static int Reads,Finds;public static bool ThrowDeps;public static string DriftGuid;
  public static string[] FindAssets(string q,string[] roots){Finds++;return Deps.Keys.ToArray();}
  public static string GUIDToAssetPath(string s)=>s;
  public static string[] GetDependencies(string p,bool recursive){Reads++;if(ThrowDeps)throw new Exception("unknown dependency");return Deps[p];}
  public static string AssetPathToGUID(string p)=>p==DriftGuid?"changed":p;
  public static T LoadAssetAtPath<T>(string p) where T:class=>Mats[p] as T;
  public static string GetAssetPath(Shader s)=>"";public static string GetAssetPath(Material m)=>m==null?"":m.Path;
  public static void ImportAsset(string p,ImportAssetOptions o){throw new Exception("preview imported");}
  public static void SaveAssets(){throw new Exception("preview saved");}
  public static void SaveAssetIfDirty(Material m){}
 }
}
namespace VRCForge.Core.MCP {
 public class VRCForgeToolResult {public bool IsSuccessful;public object Payload;public string Message;
 public static object Completed(string s,object p)=>new VRCForgeToolResult{IsSuccessful=true,Payload=p,Message=s};
 public static object FailedWithCode(string c,string s,object p)=>new VRCForgeToolResult{IsSuccessful=false,Payload=p,Message=s};}
}
namespace VRCForge.Editor {
 public class StableAssetEvidence {public string Guid;public FileEvidence File=new FileEvidence(),Meta=new FileEvidence();}
 public class FileEvidence {public string Digest;}
 public static class SceneObjectCopyCore {public static string ToAbsoluteAssetPath(string p)=>p;public static StableAssetEvidence ReadStableAssetEvidence(string p,string s)=>new StableAssetEvidence();public static bool StableAssetEvidenceMatches(StableAssetEvidence a,StableAssetEvidence b,bool x)=>true;}
 public class Identity {public string scenePath="Assets/Main.unity",sceneGuid="scene",rendererPath,componentId,componentType="Renderer";public int sceneHandle=1,componentIndex=0;}
 public static class RendererComponentIdentity {public static Identity Create(Renderer r)=>new Identity{rendererPath=r.Id,componentId=r.Id};}
 public static class MaterialShaderTool {
  private const int MaxDependencyCandidates=4096,MaxImpactItems=128;
  internal class MaterialAssetEvidence {public string assetPath,assetGuid,fileDigest="file";}
  internal static JObject CaptureMaterialRenderState(Material m)=>new JObject();
  internal static void RestoreMaterialRenderState(Material m,JObject e){}
  internal static void VerifyMaterialRenderState(Material m,JObject e){}
  internal static string NormalizeOptionalAssetPath(string p,bool b)=>p;
  internal static string NormalizeResolvedShaderAssetPath(string p)=>p;
  internal static bool MatchesCurrentProject(string p)=>p=="project";
  internal static Shader ResolveShader(string n,string p)=>new Shader{name=n};
  internal static MaterialAssetEvidence InspectWritableMaterialAsset(Material m)=>new MaterialAssetEvidence{assetPath=m.Path,assetGuid=AssetDatabase.AssetPathToGUID(m.Path)};
  private static bool IsSceneObject(Renderer r)=>r!=null;
  internal static object HandleCommand(JObject q){var m=AssetDatabase.LoadAssetAtPath<Material>(q["materialAssetPath"].Value<string>());return Payload(q,m,BuildSharedMaterialImpact(m,m.Path));}
  // OPTIONAL_CONTEXT_SINGLE
  private static object Payload(JObject q,Material m,SharedMaterialImpactResult impact)=>VRCForgeToolResult.Completed("preview",new{verified=true,preview=true,materialAssetPath=m.Path,materialAssetGuid=AssetDatabase.AssetPathToGUID(m.Path),materialFileDigestBefore="file",beforeShader="Old",requestedShader=q["shaderName"].Value<string>(),sharedImpact=impact.impact,sharedImpactDigest=impact.digest,sharedImpactDisplayDigest=impact.displayDigest,sharedImpactTailDigest=impact.tailDigest});
  // ACTUAL_IMPACT_METHODS
 }
 public class Runner {
  static int failures;
  static void Check(bool b,string n){Console.WriteLine((b?"PASS ":"FAIL ")+n);if(!b)failures++;}
  static JObject Request()=>new JObject{["assignments"]=new JArray(AssetDatabase.Mats.Keys.Select(p=>new JObject{["materialAssetPath"]=p,["shaderName"]="New"})),["preview"]=true,["expectedProjectPath"]="project"};
  public static int Main(){
   for(var i=0;i<45;i++){var p="Assets/M"+i+".mat";AssetDatabase.Mats[p]=new Material{Path=p};}
   AssetDatabase.Renderers=new[]{new Renderer{Id="A",sharedMaterials=new[]{AssetDatabase.Mats["Assets/M0.mat"],AssetDatabase.Mats["Assets/M1.mat"],AssetDatabase.Mats["Assets/M0.mat"]}},new Renderer{Id="B",sharedMaterials=new Material[]{null}}};
   AssetDatabase.Deps["Assets/A.prefab"]=new[]{"ASSETS/M0.MAT"};AssetDatabase.Deps["Assets/B.unity"]=new[]{"Assets/M1.mat"};AssetDatabase.Deps["Assets/C.prefab"]=new string[0];
   var result=(VRCForgeToolResult)UnityMaterialShaderBatch.HandleCommand(Request());var payload=JObject.FromObject(result.Payload);
   Check(result.IsSuccessful&&payload["assignments"].Count()==45,"45 verified preview rows");
   Check(AssetDatabase.Reads==3&&AssetDatabase.Finds==2&&Resources.Scans==1,"each dependency once and one renderer scan");
   var a=payload["assignments"][0];var b=payload["assignments"][1];
   Check(a["sharedImpact"]["loadedRendererSlotCount"].Value<int>()==2&&a["sharedImpact"]["dependentAssetCount"].Value<int>()==1&&a["sharedImpact"]["loadedRendererSlots"][1]["slotIndex"].Value<int>()==2&&b["sharedImpact"]["loadedRendererSlots"][0]["slotIndex"].Value<int>()==1,"material and slot identity partition");
   Check(a["sharedImpactDigest"].Value<string>()=="07100159b7801ed67c7f91ca34d14b85a389bf0beb3474c4ad5b8d5f295b60eb"&&b["sharedImpactDigest"].Value<string>()=="9469bcfe81a5f04b10d815a7fa9563aac35ff5dd042bafab207eb9480f75a22c","original per-material digest byte equivalence");
   Check(a["sharedImpact"]["dependencyQueryCount"].Value<int>()==3,"visible actual dependency query count");
   var single=(VRCForgeToolResult)MaterialShaderTool.HandleCommand(new JObject{["materialAssetPath"]="Assets/M0.mat",["shaderName"]="New"});Check(JObject.FromObject(single.Payload)["sharedImpactDigest"].Value<string>()==a["sharedImpactDigest"].Value<string>(),"single and batch commitment equivalence");
   var context=MaterialShaderTool.BuildBatchSharedMaterialImpact((JArray)Request()["assignments"]);
   AssetDatabase.DriftGuid="Assets/M0.mat";var rejected=false;try{MaterialShaderTool.ResolveSharedMaterialImpact(AssetDatabase.Mats["Assets/M0.mat"],MaterialShaderTool.InspectWritableMaterialAsset(AssetDatabase.Mats["Assets/M0.mat"]),context);}catch{rejected=true;}Check(rejected,"GUID drift rejects reused impact");AssetDatabase.DriftGuid=null;
   rejected=false;try{MaterialShaderTool.ResolveSharedMaterialImpact(AssetDatabase.Mats["Assets/M1.mat"],MaterialShaderTool.InspectWritableMaterialAsset(AssetDatabase.Mats["Assets/M0.mat"]),context);}catch{rejected=true;}Check(rejected,"cross-material impact rejects");
   var last=AssetDatabase.Mats["Assets/M44.mat"];last.parent=AssetDatabase.Mats["Assets/M43.mat"];var writes=Material.Writes;
   result=(VRCForgeToolResult)UnityMaterialShaderBatch.HandleCommand(Request());Check(!result.IsSuccessful&&Material.Writes==writes,"last variant preview rejected before any real setter");
   var apply=Request();apply["preview"]=false;apply["expectedAssignments"]=payload["assignments"].DeepClone();apply["expectedPreviewDigest"]=payload["previewDigest"];
   result=(VRCForgeToolResult)UnityMaterialShaderBatch.HandleCommand(apply);var failed=JObject.FromObject(result.Payload);
   Check(!result.IsSuccessful&&failed["mutationStarted"].Value<bool>()==false&&Material.Writes==writes&&failed["failureDetails"]["blockedAssignments"][0]["rowIndex"].Value<int>()==44,"late variant apply gives zero writes and exact blocked row");
   Check(failed["failureDetails"]["blockedAssignments"][0]["parentMaterialAssetPath"].Value<string>()=="Assets/M43.mat","parent identity exposed");
   Check(MaterialShaderTool.PreflightShaderAssignment(last,last.shader)==null,"variant no-op remains supported");
   var planned=new Dictionary<Material,Shader>{{last.parent,new Shader{name="Other"}}};Check(MaterialShaderTool.PreflightShaderAssignment(last,last.shader,planned)["reason"].Value<string>()=="batch_ancestor_shader_conflict","ancestor mutation conflict rejected");
   last.parent=null;last.MissingParent=true;Check(MaterialShaderTool.PreflightShaderAssignment(last,last.shader)["reason"].Value<string>()=="material_variant_parent_missing","missing parent fails closed");last.MissingParent=false;
   var destroyed=UnityEngine.Object.Destroyed;last.IgnoreShader=true;Check(MaterialShaderTool.PreflightShaderAssignment(last,new Shader())["reason"].Value<string>()=="shader_setter_did_not_apply"&&UnityEngine.Object.Destroyed==destroyed+1&&Material.Writes==writes,"ignored clone setter rejected and disposed without real writes");last.IgnoreShader=false;
   destroyed=UnityEngine.Object.Destroyed;last.ThrowShader=true;Check(MaterialShaderTool.PreflightShaderAssignment(last,new Shader())["reason"].Value<string>()=="shader_setter_preflight_failed"&&UnityEngine.Object.Destroyed==destroyed+1&&Material.Writes==writes,"throwing clone setter disposed without real writes");last.ThrowShader=false;

   AssetDatabase.ThrowDeps=true;result=(VRCForgeToolResult)UnityMaterialShaderBatch.HandleCommand(Request());Check(!result.IsSuccessful,"unknown dependency scan fails closed");AssetDatabase.ThrowDeps=false;
   AssetDatabase.Deps.Clear();for(var i=0;i<4097;i++)AssetDatabase.Deps["Assets/P"+i+".prefab"]=new string[0];var reads=AssetDatabase.Reads;result=(VRCForgeToolResult)UnityMaterialShaderBatch.HandleCommand(Request());Check(!result.IsSuccessful&&AssetDatabase.Reads==reads,"candidate limit before recursive reads");
   return failures==0?0:1;
  }
 }
}
'''
    if 'internal sealed class BatchSharedMaterialImpact' in src:
        seam=seam.replace('// OPTIONAL_CONTEXT_SINGLE','internal static object HandleSingleCommand(JObject q,BatchSharedMaterialImpact context){var m=AssetDatabase.LoadAssetAtPath<Material>(q["materialAssetPath"].Value<string>());return Payload(q,m,ResolveSharedMaterialImpact(m,InspectWritableMaterialAsset(m),context));}')
    seam=seam.replace('// ACTUAL_IMPACT_METHODS','\n'.join(selected))
    program='using System;using System.IO;using System.Linq;using System.Text;using System.Security.Cryptography;using System.Collections.Generic;using System.Globalization;using Newtonsoft.Json;using Newtonsoft.Json.Linq;using UnityEditor;using UnityEngine;using VRCForge.Core.MCP;\n'+seam+'\n'+batch.replace('using System;','',1).replace('using System.Collections.Generic;','',1).replace('using System.IO;','',1).replace('using System.Linq;','',1).replace('using System.Security.Cryptography;','',1).replace('using System.Text;','',1).replace('using Newtonsoft.Json;','',1).replace('using Newtonsoft.Json.Linq;','',1).replace('using UnityEditor;','',1).replace('using UnityEngine;','',1).replace('using VRCForge.Core.MCP;','',1)
    cs=tmp_path/'Probe.cs';cs.write_text(program,encoding='utf-8');dll=tmp_path/'Probe.dll'
    built=subprocess.run([dotnet,str(sdk),'-nologo','-target:exe','-nostdlib+','-langversion:8.0',f'-out:{dll}',*[f'-r:{p}' for p in refs.glob('*.dll')],f'-r:{dll_json}',str(cs)],capture_output=True,text=True,timeout=60)
    assert built.returncode==0,built.stdout+built.stderr
    shutil.copyfile(dll_json,tmp_path/'Newtonsoft.Json.dll');(tmp_path/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'net8.0','framework':{'name':'Microsoft.NETCore.App','version':'8.0.0'}}}))
    run=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr

"""Execute production scalar command with explicit Unity storage seams; real file rollback."""
import json
import os
import shutil
import subprocess
from pathlib import Path
import pytest
import material_shader_assignment as assignment
from test_material_keyword_edit import STUBS as KEYWORD_STUBS
ROOT=Path(__file__).resolve().parents[1]
STUBS=KEYWORD_STUBS[:KEYWORD_STUBS.index('public static class Probe')]
STUBS=STUBS.replace('GetPropertyCount()=>3', 'GetPropertyCount()=>6').replace('GetPropertyName(int i)=>"_Value"', 'GetPropertyName(int i)=>new[]{"_Value","_Range","_Int","_Color","_Vector","_Texture"}[i]')
STUBS=STUBS.replace('GetPropertyType(int i)=>i==0?Rendering.ShaderPropertyType.Float:i==1?Rendering.ShaderPropertyType.Int:Rendering.ShaderPropertyType.Texture;', 'GetPropertyType(int i)=>new[]{Rendering.ShaderPropertyType.Float,Rendering.ShaderPropertyType.Range,Rendering.ShaderPropertyType.Int,Rendering.ShaderPropertyType.Color,Rendering.ShaderPropertyType.Vector,Rendering.ShaderPropertyType.Texture}[i]; public int FindPropertyIndex(string n)=>Array.IndexOf(new[]{"_Value","_Range","_Int","_Color","_Vector","_Texture"},n); public Vector2 GetPropertyRangeLimits(int i)=>new Vector2{x=0,y=1};public string[] GetPropertyAttributes(int i)=>new[]{"Display"};public int renderQueue=>2450;')
STUBS=STUBS.replace('public int renderQueue=2000,globalIlluminationFlags;', 'public int rawRenderQueue=2000;public int renderQueue{get=>rawRenderQueue==-1?shader.renderQueue:rawRenderQueue;set=>rawRenderQueue=value;}public int globalIlluminationFlags;')
STUBS=STUBS.replace('public float GetFloat(string n)=>Value;', 'public float RangeValue=.5f;public int IntValue=7;public float GetFloat(string n)=>n=="_Range"?RangeValue:Value;public void SetFloat(string n,float v){if(UnityEditor.AssetDatabase.Fault=="set")throw new Exception("set fault");if(n=="_Range")RangeValue=v;else Value=v;}public void SetInteger(string n,int v){IntValue=v;}')
STUBS=STUBS.replace('GetInteger(string n)=>7', 'GetInteger(string n)=>IntValue')
STUBS=STUBS.replace('System.IO.File.WriteAllText(TargetPath,string.Join(",",Target.shaderKeywords));', 'System.IO.File.WriteAllText(TargetPath,string.Join("|",Target.Value.ToString("R",System.Globalization.CultureInfo.InvariantCulture),Target.RangeValue.ToString("R",System.Globalization.CultureInfo.InvariantCulture),Target.IntValue.ToString(),Target.rawRenderQueue.ToString()));')
STUBS=STUBS.replace('Target.shaderKeywords=System.IO.File.ReadAllText(p).Split((char)44);Target.Value=.25f;Target.renderQueue=2000;', 'var data=System.IO.File.ReadAllText(p).Split((char)124);Target.Value=float.Parse(data[0],System.Globalization.CultureInfo.InvariantCulture);Target.RangeValue=float.Parse(data[1],System.Globalization.CultureInfo.InvariantCulture);Target.IntValue=int.Parse(data[2]);Target.renderQueue=int.Parse(data[3]);Target.shaderKeywords=new[]{"ACTIVE"};')
STUBS=STUBS.replace('if(Fault=="effective")Target.renderQueue=2222;', 'if(Fault=="effective")Target.renderQueue=2222;if(Fault=="keyworddrift")Target.shaderKeywords=new[]{"BAD"};')
STUBS=STUBS.replace('public static bool MatchesCurrentProject(string p)=>p=="Project";', 'public static bool MatchesCurrentProject(string p)=>p=="Project";public static object InspectWritableMaterialAsset(UnityEngine.Material m)=>new object();public class Impact {public object impact=new {scope="test"};public string digest=UnityEditor.AssetDatabase.Fault=="impactstale"?"changed":"sealed",displayDigest="display",tailDigest="tail";}public static Impact ResolveSharedMaterialImpact(UnityEngine.Material m,object e,object c)=>new Impact();')
# The dependency-query seam supplies a valid empty result; production receipts remain untouched.
SCALAR_IMPACT={"scope":"loaded_scene_renderers_and_project_scene_prefab_dependencies","dependencyCandidateCount":0,
               "loadedRendererSlotCount":0,"loadedRendererSlots":[],"dependentAssetCount":0,"dependentAssets":[],"listsTruncated":False}
SCALAR_DISPLAY=assignment.compute_shared_impact_digest(SCALAR_IMPACT)
SCALAR_TAIL=assignment.compute_shared_impact_tail_digest(SCALAR_IMPACT,slots=[],assets=[])
SCALAR_DIGEST=assignment.compute_shared_impact_commitment(SCALAR_IMPACT,display_digest=SCALAR_DISPLAY,tail_digest=SCALAR_TAIL)
STUBS=STUBS.replace('new {scope="test"}', 'JObject.Parse(@"'+json.dumps(SCALAR_IMPACT).replace('"','""')+'")')
STUBS=STUBS.replace('"sealed"','"'+SCALAR_DIGEST+'"').replace('displayDigest="display"','displayDigest="'+SCALAR_DISPLAY+'"').replace('tailDigest="tail"','tailDigest="'+SCALAR_TAIL+'"')
STUBS=STUBS.replace('public static class EditorUtility', 'public enum SerializedPropertyType{Integer}public class SerializedProperty:IDisposable{public SerializedPropertyType propertyType=>SerializedPropertyType.Integer;public int intValue;public void Dispose(){}} public class SerializedObject:IDisposable{Material target;public SerializedObject(Material m){target=m;}public SerializedProperty FindProperty(string n)=>new SerializedProperty{intValue=target.rawRenderQueue};public void Dispose(){}} public static class EditorUtility')
RUNNER=r'''
public static class Probe {
 public static int Main(string[] argv){
  System.IO.Directory.CreateDirectory("Assets");UnityEditor.AssetDatabase.Target=new UnityEngine.Material{name="target",shader=UnityEditor.AssetDatabase.Shader};
  UnityEditor.AssetDatabase.Parent=new UnityEngine.Material{name="parent",shader=UnityEditor.AssetDatabase.Shader};UnityEditor.AssetDatabase.Unrelated=new UnityEngine.Material{name="unrelated",Dirty=true};
  System.IO.File.WriteAllText("Assets/Target.mat","0.25|0.5|7|2000");System.IO.File.WriteAllText("Assets/Target.mat.meta","original-meta");System.IO.File.WriteAllText("Assets/Generic.shader","shader");
  var rows=new JArray(new JObject{["propertyName"]="_Value",["value"]=.1},new JObject{["propertyName"]="_Range",["value"]=1},new JObject{["propertyName"]="_Int",["value"]=10});
  var args=new JObject{["materialAssetPath"]="Assets/Target.mat",["expectedProjectPath"]="Project",["preview"]=true,["propertyChanges"]=rows,["renderQueue"]=-1};
  if(argv[0]=="nochange"){rows[0]["value"]=.25;rows[1]["value"]=.5;rows[2]["value"]=7;args["renderQueue"]=2000;}
  var p=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.MaterialScalarPropertyEdit.HandleCommand(args);var preview=JObject.FromObject(p.Payload);
  if(!p.Ok){Console.WriteLine(new JObject{["ok"]=false,["stage"]="preview",["message"]=p.Message}.ToString());return 0;}
  args["expectedPropertyEvidence"]=preview["before"].DeepClone();args["preview"]=false;UnityEditor.AssetDatabase.Fault=argv[0];
  if(argv[0]=="stale")args["expectedPropertyEvidence"]["fileDigest"]="stale";
  if(argv[0]=="invalid")rows[0]["propertyName"]="_Missing";
  if(argv[0]=="duplicate")rows[1]["propertyName"]="_Value";
  if(argv[0]=="range")rows[1]["value"]=1.01;
  if(argv[0]=="fraction")rows[2]["value"]=10.5;
  if(argv[0]=="intoverflow")rows[2]["value"]=2147483648L;
  if(argv[0]=="overflow")rows[0]["value"]=1e100;
  if(argv[0]=="nan")rows[0]["value"]=double.NaN;
  if(argv[0]=="bool")rows[0]["value"]=true;
  if(argv[0]=="vector")rows[0]["propertyName"]="_Vector";
  if(argv[0]=="color")rows[0]["propertyName"]="_Color";
  if(argv[0]=="texture")rows[0]["propertyName"]="_Texture";
  if(argv[0]=="empty")args["propertyChanges"]=new JArray();
  if(argv[0]=="max")args["propertyChanges"]=new JArray(Enumerable.Range(0,33).Select(i=>rows[0].DeepClone()));
  if(argv[0]=="queue")args["renderQueue"]=5001;
  if(argv[0]=="queuefraction")args["renderQueue"]=2.5;
  if(argv[0]=="mixed")args["keywordChanges"]=new JArray();
  if(argv[0]=="dirty")UnityEditor.AssetDatabase.Target.Dirty=true;
  if(argv[0]=="variant")UnityEditor.AssetDatabase.Target.parent=UnityEditor.AssetDatabase.Parent;
  if(argv[0]=="shaderstale")System.IO.File.WriteAllText("Assets/Generic.shader","different");
  var r=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.MaterialScalarPropertyEdit.HandleCommand(args);
  Console.WriteLine(new JObject{["ok"]=r.Ok,["message"]=r.Message,["previewPayload"]=preview,["payload"]=JObject.FromObject(r.Payload),["globalSaves"]=UnityEditor.AssetDatabase.GlobalSaves,["targetSaves"]=UnityEditor.AssetDatabase.TargetSaves,["unrelatedDirty"]=UnityEditor.AssetDatabase.Unrelated.Dirty,["file"]=System.IO.File.ReadAllText("Assets/Target.mat"),["tempCount"]=System.IO.Directory.GetFiles("Assets","*.tmp").Length}.ToString(Newtonsoft.Json.Formatting.None));return 0;
 }
}
'''
@pytest.fixture(scope="module")
def compiled_scalar(tmp_path_factory):
    base=Path(os.environ.get("DOTNET_ROOT",str(Path.home()/"AppData/Local/Microsoft/dotnet")))
    compiler=sorted((base/"sdk").glob("8.*/Roslyn/bincore/csc.dll"))[-1];refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))[-1]
    newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll";folder=tmp_path_factory.mktemp("scalar-sdk8");cs=folder/"Probe.cs";dll=folder/"Probe.dll"
    source='using System.Security.Cryptography;\n'+(ROOT/'Assets/VRCForge/Editor/MaterialScalarPropertyEdit.cs').read_text(encoding='utf-8')+'\n'+(ROOT/'Assets/VRCForge/Editor/Generic/UnityMaterialKeywordEdit.cs').read_text(encoding='utf-8')
    # Both production files retain namespace-scoped code; merge duplicate header imports once.
    lines=source.splitlines();usings=[];body=[]
    for line in lines:
        if line.startswith('using '):
            if line not in usings:usings.append(line)
        else:body.append(line)
    cs.write_text('\n'.join(usings+body)+'\n'+STUBS+RUNNER,encoding='utf-8')
    command=[str(base/'dotnet.exe'),str(compiler),'-nologo','-target:exe','-langversion:8.0',f'-out:{dll}',*[f'-r:{p}' for p in refs.glob('*.dll')],f'-r:{newtonsoft}',str(cs)]
    result=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    shutil.copy2(newtonsoft,folder/'Newtonsoft.Json.dll');(folder/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'net8.0','framework':{'name':'Microsoft.NETCore.App','version':'8.0.0'}}}))
    return base/'dotnet.exe',dll

def run_scalar(compiled_scalar,tmp_path,case):
    result=subprocess.run([str(compiled_scalar[0]),str(compiled_scalar[1]),case],cwd=tmp_path,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads(result.stdout)

@pytest.mark.parametrize('case',['success','nochange','stale','invalid','duplicate','range','fraction','intoverflow','overflow','nan','bool','vector','color','texture','empty','max','queue','queuefraction','mixed','dirty','variant','shaderstale','impactstale','set','effective','keyworddrift','guid','meta','save'])
def test_actual_scalar_edit(compiled_scalar,tmp_path,case):
    result=run_scalar(compiled_scalar,tmp_path,case);p=result.get('payload',{})
    assert result.get('stage')!='preview',result
    assert result['globalSaves']==0 and result['unrelatedDirty'] is True and result['tempCount']==0
    if case in ('success','nochange'):
        assert result['ok'] is True,result
        assert p['persistedReadback'] is True and p['committed'] is True
        state=p['readback']['state'];assert state['keywords']==['ACTIVE']
        assert state['rawRenderQueue']==(-1 if case=='success' else 2000)
        assert state['renderQueue']==(2450 if case=='success' else 2000)
        assert state['properties'][2]['value']==(10 if case=='success' else 7)
        assert state['properties'][1]['range']==[0,1]
        assert result['targetSaves']==(case=='success')
    elif case in ('set','effective','keyworddrift'):
        assert result['ok'] is False and p['commitState']=='rolled_back',result
        assert result['file']=='0.25|0.5|7|2000'
    elif case in ('guid','meta','save'):
        assert result['ok'] is False and p['commitState']=='unknown' and p['checkpointRecoveryRequired'] is True,result
    else:
        assert result['ok'] is False and p['mutationStarted'] is False and result['targetSaves']==0,result

def test_scalar_command_compiles_against_real_unity_2022_3_apis(tmp_path):
    """Compile actual commands, no Unity process/native execution."""
    managed=Path('E:/unity/Unity 2022.3.22f1/Editor/Data/Managed/UnityEngine')
    if not managed.exists():pytest.skip('Unity API reference assemblies unavailable')
    base=Path(os.environ.get('DOTNET_ROOT',str(Path.home()/'AppData/Local/Microsoft/dotnet')))
    compiler=sorted((base/'sdk').glob('8.*/Roslyn/bincore/csc.dll'))[-1];refs=sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('8.*/ref/net8.0'))[-1]
    source=(ROOT/'Assets/VRCForge/Editor/MaterialScalarPropertyEdit.cs').read_text(encoding='utf-8')+'\n'+(ROOT/'Assets/VRCForge/Editor/Generic/UnityMaterialKeywordEdit.cs').read_text(encoding='utf-8')
    lines=source.splitlines();usings=[];body=[]
    for line in lines:
        if line.startswith('using '):
            if line not in usings:usings.append(line)
        else:body.append(line)
    seams=STUBS[:STUBS.index('namespace UnityEngine.Rendering')]+r'''
namespace VRCForge.Editor {
 public class FileEvidence {public string Digest;}public class StableAssetEvidence {public string Guid;public FileEvidence File,Meta;}
 public static class SceneObjectCopyCore {public static string ToAbsoluteAssetPath(string p)=>p;public static StableAssetEvidence ReadStableAssetEvidence(string p,string l)=>null;}
 public static class MaterialShaderTool {
 public static bool MatchesCurrentProject(string p)=>true;public static object InspectWritableMaterialAsset(UnityEngine.Material m)=>null;
 public class Impact {public object impact;public string digest,displayDigest,tailDigest;}
 public static Impact ResolveSharedMaterialImpact(UnityEngine.Material m,object e,object c)=>null;
 }
}
'''
    cs=tmp_path/'UnityApi.cs';cs.write_text('\n'.join(usings+body)+'\n'+seams,encoding='utf-8')
    command=[str(base/'dotnet.exe'),str(compiler),'-nologo','-target:library',f'-out:{tmp_path/"UnityApi.dll"}',*[f'-r:{p}' for p in refs.glob('*.dll')],*[f'-r:{managed/p}' for p in ('UnityEngine.CoreModule.dll','UnityEditor.CoreModule.dll')],f'-r:{compiler.parents[2]/"Newtonsoft.Json.dll"}',str(cs)]
    result=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr

"""Production selection/authorization code executed with deterministic Unity read seams."""
import json
import os
import shutil
import subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'Assets/VRCForge/Editor/AnimationBindingReadSelection.cs'
STUBS=r"""
namespace UnityEngine {
 public class Object {public string name;public string assetPath;}
 public class AnimationClip:Object {}
 public class GameObject:Object {}
 public class Renderer:Object {}
 public struct Hash128 {public override string ToString()=>"unchanged";}
 public struct Keyframe {public float time,value,inTangent,outTangent,inWeight,outWeight;public int weightedMode;}
 public class AnimationCurve {public int length=>7;public int preWrapMode=>0;public int postWrapMode=>1;public Keyframe this[int i]=>new Keyframe{time=i*.1f,value=i,inTangent=2,outTangent=3,inWeight=.1f,outWeight=.2f,weightedMode=1};}
}
namespace UnityEditor {
 public struct EditorCurveBinding {public string path,propertyName;public Type type;}
 public struct ObjectReferenceKeyframe {public float time;public UnityEngine.Object value;}
 public static class AssetDatabase {public static bool TryGetGUIDAndLocalFileIdentifier(UnityEngine.Object o,out string guid,out long id){guid="guid";id=long.Parse(o.name.Replace("pair",""));return true;}public static string GetAssetPath(UnityEngine.Object o)=>o.assetPath;public static UnityEngine.Hash128 GetAssetDependencyHash(string p)=>new UnityEngine.Hash128();}
 public static class AnimationUtility {
 public static int CurveReads,ReferenceReads;
 public static EditorCurveBinding[] GetCurveBindings(UnityEngine.AnimationClip c)=>Enumerable.Range(0,160).Select(i=>new EditorCurveBinding{path=i==0?"bra":i==1?"panty":i==2?"":"cloth/"+i,propertyName=i<3?"m_IsActive":"material._Value",type=typeof(UnityEngine.GameObject)}).ToArray();
 public static EditorCurveBinding[] GetObjectReferenceCurveBindings(UnityEngine.AnimationClip c)=>new[]{new EditorCurveBinding{path="bra",propertyName="m_Materials.Array.data[0]",type=typeof(UnityEngine.Renderer)}};
 public static UnityEngine.AnimationCurve GetEditorCurve(UnityEngine.AnimationClip c,EditorCurveBinding b){CurveReads++;return new UnityEngine.AnimationCurve();}
 public static ObjectReferenceKeyframe[] GetObjectReferenceCurve(UnityEngine.AnimationClip c,EditorCurveBinding b){ReferenceReads++;return Enumerable.Range(0,3).Select(i=>new ObjectReferenceKeyframe{time=i,value=new UnityEngine.Object{name="material"+i,assetPath="Assets/material"+i+".mat"}}).ToArray();}
 }
}
"""
RUNNER=r"""
public class Probe {
 public static int Main(string[] argv){
 var args=JObject.Parse(argv[0]);
 if(argv.Length>1){Console.WriteLine(Gate.Check(args));return 0;}
 try {
 var clips=Enumerable.Range(0,32).Select(i=>new UnityEngine.AnimationClip{name="pair"+i,assetPath="Assets/Generated/Animation/"+i.ToString("D2")+new string('x',90)+".anim"}).ToList();
 var result=VRCForge.Editor.AnimationBindingReadSelection.Build(args,()=>clips);
 Console.WriteLine(new JObject{["ok"]=true,["result"]=result,["curveReads"]=UnityEditor.AnimationUtility.CurveReads,["referenceReads"]=UnityEditor.AnimationUtility.ReferenceReads}.ToString(Newtonsoft.Json.Formatting.None));
 }catch(Exception e){Console.WriteLine(new JObject{["ok"]=false,["error"]=e.Message,["curveReads"]=UnityEditor.AnimationUtility.CurveReads}.ToString(Newtonsoft.Json.Formatting.None));}return 0;
 }
}
"""
def sdk():
 base=Path(os.environ.get('DOTNET_ROOT',str(Path.home()/'AppData/Local/Microsoft/dotnet')))
 compiler=sorted((base/'sdk').glob('8.*/Roslyn/bincore/csc.dll'))[-1]
 refs=sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('8.*/ref/net8.0'))[-1]
 return base,compiler,refs

def gate_source():
 source=(ROOT/'Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs').read_text(encoding='utf-8')
 start=source.index('            if (string.Equals(toolName, "vrc_scan_animation_bindings"')
 end=source.index('            if (string.Equals(toolName, "vrc_scan_avatar_performance"',start)
 helpers=source[source.index('        private static bool HasExactKeys'):source.index('        private static bool HasAllowedPreviewRequest')]
 # Only the basic value validators are dependencies of this unchanged read-lane branch.
 helpers=helpers[:helpers.index('        private static',helpers.index('private static bool HasStringArray')+30)] if '        private static' in helpers[helpers.index('private static bool HasStringArray')+30:] else helpers
 return 'public static class Gate {public static bool Check(JObject arguments){var toolName="vrc_scan_animation_bindings";'+source[start:end]+'return false;}'+helpers+'}'

@pytest.fixture(scope='module')
def compiled_selection(tmp_path_factory):
 base,compiler,refs=sdk();folder=tmp_path_factory.mktemp('animation-selection');cs=folder/'Probe.cs';dll=folder/'Probe.dll'
 cs.write_text(SOURCE.read_text(encoding='utf-8')+'\n'+STUBS+gate_source()+RUNNER,encoding='utf-8')
 newtonsoft=compiler.parents[2]/'Newtonsoft.Json.dll'
 result=subprocess.run([str(base/'dotnet.exe'),str(compiler),'-nologo','-target:exe','-langversion:8.0',f'-out:{dll}',*[f'-r:{p}' for p in refs.glob('*.dll')],f'-r:{newtonsoft}',str(cs)],capture_output=True,text=True,timeout=60)
 assert result.returncode==0,result.stdout+result.stderr
 shutil.copy2(newtonsoft,folder/'Newtonsoft.Json.dll');(folder/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'net8.0','framework':{'name':'Microsoft.NETCore.App','version':'8.0.0'}}}))
 return base/'dotnet.exe',dll

def run(compiled_selection,args,gate=False):
 r=subprocess.run([str(p) for p in compiled_selection]+[json.dumps(args)]+(['gate'] if gate else []),capture_output=True,text=True,timeout=30)
 assert r.returncode==0,r.stdout+r.stderr
 return r.stdout.strip()=='True' if gate else json.loads(r.stdout)

def test_summary_has_no_curve_read_and_fits_agent_budget(compiled_selection):
 r=run(compiled_selection,{'bindingView':'summary'});p=r['result']
 assert r['curveReads']==r['referenceReads']==0
 assert p['summary']['totalBindingCount']==32*161 and len(p['clips'])==32
 assert not p['bindings'] and p['paging']['keyDetailsOmitted']
 assert len(json.dumps(p,ensure_ascii=False))<12000

def test_exact_selection_pages_and_preserves_all_keys(compiled_selection):
 args={'bindingView':'details','bindingSelectors':[{'path':'bra','propertyName':'m_IsActive'},{'path':'panty','propertyName':'m_IsActive'}],'maxKeysPerBinding':16}
 rows=[]
 while True:
  r=run(compiled_selection,args);p=r['result'];rows+=p['bindings']
  assert r['curveReads']==len(p['bindings']) and r['referenceReads']==0
  assert len(json.dumps(p,ensure_ascii=False))<8*1024*1024
  if not p['paging']['nextRequest']:break
  args=p['paging']['nextRequest']
 assert len(rows)==64 and len({(x['clip_path'],x['path']) for x in rows})==64
 assert all(len(x['keys'])==7 and x['keys'][3]['value']==3 and x['keys'][3]['inTangent']==2 for x in rows)

def test_total_budget_and_key_continuation_are_complete(compiled_selection):
 args={'bindingView':'details','bindingSelectors':[{'path':'bra','propertyName':'m_IsActive'}],'maxTotalKeys':3}
 first=run(compiled_selection,args)['result'];row=first['bindings'][0]
 assert first['summary']['returnedKeyCount']==3 and row['omittedKeyCount']==4 and not first['paging']['allSelectedDetailsReturned']
 values=[k['value'] for k in row['keys']]
 while row.get('nextKeyRequest'):
  request={**args,**row['nextKeyRequest']};row=run(compiled_selection,request)['result']['bindings'][0];values.extend(k['value'] for k in row['keys'])
 assert values==list(range(7))
 assert first['paging']['nextRequest']['bindingOffset']==1

def test_index_root_and_object_reference_read(compiled_selection):
 r=run(compiled_selection,{'bindingView':'index','bindingSelectors':[{'path':''}],'bindingLimit':1})
 row=r['result']['bindings'][0];assert row['path']=='' and row['keyframe_count']==7 and 'keys' not in row
 r=run(compiled_selection,{'bindingView':'details','bindingSelectors':[{'path':'bra','componentType':'Renderer'}],'bindingLimit':1})
 row=r['result']['bindings'][0];assert row['object_reference_key_count']==3 and row['object_reference_keys'][1]['asset_path']=='Assets/material1.mat'
 assert r['curveReads']==0

def test_zero_matches_stale_and_bad_input(compiled_selection):
 r=run(compiled_selection,{'bindingView':'details','bindingSelectors':[{'path':'Bra'}]})
 assert r['result']['selection']['matchStatus']=='no_matches' and r['result']['summary']['matchedBindingCount']==0 and r['curveReads']==0
 r=run(compiled_selection,{'bindingView':'details','expectedSnapshotDigest':'a'*64})
 assert not r['ok'] and r['curveReads']==0 and 'snapshot changed' in r['error']

@pytest.mark.parametrize('extra',[{'bindingView':'bad'},{'bindingSelectors':[{}]},{'bindingSelectors':[{'componentType':'Renderer'}]},{'bindingSelectors':[{'path':'bra','prefix':True}]},{'bindingSelectors':[{'path':'bra'}]*33},{'bindingLimit':0},{'maxTotalKeys':4097},{'keyOffset':-1},{'bindingOffset':True},{'expectedSnapshotDigest':'bad'},{'outputPath':'Assets/out.json'},{'refreshAssets':True},{'unknown':1}])
def test_real_core_read_gate_rejects_unsafe_or_invalid(compiled_selection,extra):
 args={'avatarPath':'Avatar','outputPath':'','controllerPath':'','clipPaths':[],'includeAllProjectClips':False,'includeBindingDetails':True,'maxClips':32,'maxKeysPerBinding':16,'refreshAssets':False,'bindingView':'summary',**extra}
 assert not run(compiled_selection,args,True)

def test_real_core_read_gate_retains_legacy_and_selected(compiled_selection):
 args={'avatarPath':'Avatar','outputPath':'','controllerPath':'','clipPaths':[],'includeAllProjectClips':False,'includeBindingDetails':True,'maxClips':32,'maxKeysPerBinding':16,'refreshAssets':False}
 assert run(compiled_selection,args,True)
 assert run(compiled_selection,{**args,'bindingView':'summary'},True)
 assert run(compiled_selection,{**args,'bindingSelectors':[{'path':''}]},True)

def test_real_unity_api_compile(tmp_path):
 managed=Path('E:/unity/Unity 2022.3.22f1/Editor/Data/Managed/UnityEngine')
 if not managed.exists():pytest.skip('Unity references unavailable')
 base,compiler,refs=sdk()
 result=subprocess.run([str(base/'dotnet.exe'),str(compiler),'-nologo','-target:library',f'-out:{tmp_path/"Api.dll"}',*[f'-r:{p}' for p in refs.glob('*.dll')],*[f'-r:{managed/p}' for p in ('UnityEngine.CoreModule.dll','UnityEditor.CoreModule.dll','UnityEngine.AnimationModule.dll')],f'-r:{compiler.parents[2]/"Newtonsoft.Json.dll"}',str(SOURCE)],capture_output=True,text=True,timeout=60)
 assert result.returncode==0,result.stdout+result.stderr


def test_public_schema_and_gateway_preserve_selection_fields(monkeypatch):
 import dashboard_server as dashboard
 from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
 import jsonschema
 args={'clipPaths':['Assets/Test.anim'],'bindingView':'details','bindingSelectors':[{'path':'bra','propertyName':'m_IsActive','componentType':'GameObject'}],'bindingOffset':2,'bindingLimit':4,'clipOffset':0,'keyOffset':3,'maxTotalKeys':64,'expectedSnapshotDigest':'a'*64}
 jsonschema.validate(args,UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_scan_animation_bindings'])
 seen=[]
 monkeypatch.setattr(dashboard,'run_unity_artifact_scan_sync',lambda *a, **kwargs:seen.append(a) or {'ok':True})
 dashboard.scan_animation_bindings_sync(args)
 for name,value in args.items():assert seen[0][3][name]==value
 dashboard.scan_animation_bindings_sync({'clipPaths':['Assets/Test.anim']})
 assert set(seen[1][3])=={'controllerPath','clipPaths','includeAllProjectClips','maxClips','includeBindingDetails','maxKeysPerBinding','refreshAssets'}
 assert seen[1][3]['includeBindingDetails'] is False
 for invalid in ({'bindingSelectors':[{}]},{'bindingSelectors':[{'path':'bra','wildcard':True}]},{'bindingSelectors':[{'path':'bra'}]*33},{'maxTotalKeys':4097},{'bindingLimit':257}):
  with pytest.raises(jsonschema.ValidationError):jsonschema.validate(invalid,UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_scan_animation_bindings'])


def test_public_animation_continuations_validate_without_internal_handler_fields(monkeypatch):
    import dashboard_server as dashboard
    from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
    import jsonschema

    payload = {
        'ok': True,
        'paging': {'nextRequest': {
            'clipPaths': ['Assets/Test.anim'], 'bindingView': 'details',
            'bindingOffset': 1, 'outputPath': '', 'refreshAssets': False,
        }},
        'bindings': [{'nextKeyRequest': {
            'clipPaths': ['Assets/Test.anim'], 'keyOffset': 1,
            'outputPath': '', 'refreshAssets': False,
        }}],
    }

    def fake_scan(*args, **kwargs):
        return kwargs['payload_transform'](payload)

    monkeypatch.setattr(dashboard, 'run_unity_artifact_scan_sync', fake_scan)
    result = dashboard.scan_animation_bindings_sync({'clipPaths': ['Assets/Test.anim']})
    schema = UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_scan_animation_bindings']
    jsonschema.validate(result['paging']['nextRequest'], schema)
    jsonschema.validate(result['bindings'][0]['nextKeyRequest'], schema)
    assert 'outputPath' not in result['paging']['nextRequest']
    assert 'refreshAssets' not in result['paging']['nextRequest']
    assert 'outputPath' not in result['bindings'][0]['nextKeyRequest']
    assert 'refreshAssets' not in result['bindings'][0]['nextKeyRequest']

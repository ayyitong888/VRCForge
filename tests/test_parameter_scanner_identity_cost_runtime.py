"""Real parameter scanner resolver/read/cost functions; narrow native discovery and SDK DTO seams."""
from pathlib import Path
import os,subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method
from test_constraint_conversion_scope_runtime import run
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('scenario',['identity','cost'])
def test_parameter_scanner_identity_and_synced_cost(tmp_path,scenario):
    path='Assets/VRCForge/Editor/AvatarParameterScanner.cs';ref=os.environ.get('VRCFORGE_PARAMETER_SCANNER_GIT_REF')
    s=subprocess.check_output(['git','show',f'{ref}:{path}'],cwd=ROOT,text=True,encoding='utf-8') if ref else (ROOT/path).read_text(encoding='utf-8-sig')
    markers=['private static Component ResolveAvatarDescriptor','private static List<ParameterItem> ReadParameters','private static object GetMemberValue','private static Type FindType','private static string GetTransformPath','private static string NormalizePath','private static bool IsSceneComponent','private static float ToFloat','private static bool ToBool']
    bodies='\n'.join(method(s,m) for m in markers)
    start=s.index('                var totalCost =');cost=s[start:s.index('                var mergedParameterUsage',start)]
    run(tmp_path,r'''
using System;using System.Linq;using System.Collections;using System.Collections.Generic;using System.Globalization;using System.Text.Json;
class Scene{public bool isLoaded=true;public bool IsValid()=>true;}class GameObject{public Scene scene=new Scene();}class Transform{public string name;public Transform parent;}
class Component{public string name;public Transform transform;public GameObject gameObject=new GameObject();}
class Param{public string name,valueType;public bool saved,networkSynced;public float defaultValue;}class ParamAsset{public List<Param> parameters=new List<Param>();}
namespace VRC.SDK3.Avatars.Components{class VRCAvatarDescriptor:Component{public ParamAsset expressionParameters=new ParamAsset();}}
static class Resources{public static List<Component> items=new List<Component>();public static Component[] FindObjectsOfTypeAll(Type t)=>items.Where(t.IsInstanceOfType).ToArray();}static class EditorUtility{public static bool IsPersistent(object c)=>false;}
class Probe{BODIES
class ParameterItem{public string name,valueType;public bool saved,networkSynced;public float defaultValue;}
static void Main(){var a=new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor{name="Avatar",transform=new Transform{name="Avatar"}};var b=new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor{name="Avatar",transform=new Transform{name="Avatar"}};a.expressionParameters.parameters.Add(new Param{name="WrongAvatarOnly",valueType="Bool",networkSynced=true});b.expressionParameters.parameters.Add(new Param{name="ExpectedAvatarOnly",valueType="Bool",networkSynced=true});Resources.items.Add(a);Resources.items.Add(b);if(TEST_IDENTITY){foreach(var selector in new[]{"Avatar",""}){bool rejected=false;try{ResolveAvatarDescriptor(selector);}catch(InvalidOperationException){rejected=true;}if(!rejected)throw new Exception("ambiguous avatar selected first");}Resources.items.Remove(a);if(ResolveAvatarDescriptor("avatar")!=b||ResolveAvatarDescriptor("")!=b)throw new Exception("unique selection regressed");return;}
Resources.items.Clear();Resources.items.Add(b);b.expressionParameters.parameters.Clear();for(int i=0;i<40;i++)b.expressionParameters.parameters.Add(new Param{name="Local"+i,valueType="Float",networkSynced=false});b.expressionParameters.parameters.Add(new Param{name="SyncedBool",valueType="Bool",networkSynced=true});var parameters=ReadParameters(b);
COST
if(totalCost!=1)throw new Exception("unsynced parameters charged synced bits: "+totalCost);
Console.WriteLine(JsonSerializer.Serialize(new{avatarPath="Avatar",inspectionStage="ndmf_parameter_introspection",sourceDescriptorUsage=new{totalParameters=parameters.Count,totalBitUsage=totalCost,parameterNames=parameters.Select(p=>new{p.name,p.valueType,p.networkSynced,p.saved,p.defaultValue})},mergedParameterUsage=new{available=false},suggestions=new object[0]}));}}
'''.replace('BODIES',bodies).replace('COST',cost).replace('TEST_IDENTITY','true' if scenario=='identity' else 'false'))

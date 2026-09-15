"""Compile actual descriptor preview/layer functions; native SDK/JSON seams only."""
import os,subprocess
from pathlib import Path
import pytest
from test_curve_fx_authoring_runtime_contract import method
from test_constraint_conversion_scope_runtime import run
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('preview',[True,False])
def test_descriptor_rejects_invalid_layer_before_any_layer_update(tmp_path,preview):
    path='Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs';ref=os.environ.get('VRCFORGE_DESCRIPTOR_LAYER_GIT_REF')
    raw=subprocess.check_output(['git','show',f'{ref}:{path}'],cwd=ROOT,text=True,encoding='utf-8') if ref else (ROOT/path).read_text(encoding='utf-8-sig')
    source=raw[raw.index('public static class WriteAvatarDescriptorTool'):]
    markers=['private static DescriptorPlan BuildPlan','private static VRCAvatarDescriptor.CustomAnimLayer[] ApplyLayers','private static T ParseEnum']
    if 'private static void ValidateLayerTypes' in source:markers.append('private static void ValidateLayerTypes')
    bodies='\n'.join(method(source,m) for m in markers)
    run(tmp_path,r'''using System;using System.Linq;using System.Collections;using System.Collections.Generic;
class Token {public object value;public T Value<T>()=>(T)value;public override string ToString()=>value.ToString();}
class JObject:Token {public Dictionary<string,Token> data=new Dictionary<string,Token>();public Token this[string k]=>data.ContainsKey(k)?data[k]:null;}
class JArray:Token,IEnumerable<Token> {public List<Token> items=new List<Token>();public IEnumerator<Token> GetEnumerator()=>items.GetEnumerator();IEnumerator IEnumerable.GetEnumerator()=>GetEnumerator();}
class RuntimeAnimatorController {}
class VRCAvatarDescriptor {public object transform;public string name="Avatar";public enum AnimLayerType{Base,Additive,Gesture,Action,FX,Sitting,TPose,IKPose};public struct CustomAnimLayer{public AnimLayerType type;public bool isDefault;public RuntimeAnimatorController animatorController;}}
class AvatarPrimitiveCrudCore{public static string GetTransformPath(object t)=>"Avatar";public static VRCAvatarDescriptor ResolveAvatarDescriptor(string p)=>new VRCAvatarDescriptor();}
class Probe {BODIES
class DescriptorPlan{public string avatarPath,avatarName,eyeLookSettingsSourceAvatarPath;public List<string> changedFields;}
static int loads;static T LoadAssetOrNull<T>(string p) where T:class{loads++;return null;}
static JObject Layer(string type)=>new JObject{data=new Dictionary<string,Token>{{"type",new Token{value=type}},{"isDefault",new Token{value=true}},{"controllerPath",new Token{value=""}}}};
static void Check(bool ok,string m){if(!ok)throw new Exception(m);}
static void Invoke(JArray updates){if(PREVIEW)BuildPlan(new VRCAvatarDescriptor(),new JObject{data=new Dictionary<string,Token>{{"baseAnimationLayers",updates}}});else ApplyLayers(new[]{new VRCAvatarDescriptor.CustomAnimLayer{type=VRCAvatarDescriptor.AnimLayerType.FX,isDefault=false}},updates);}
static void Main(){foreach(var bad in new[]{"TypoLayer","999",""}){loads=0;var rejected=false;try{Invoke(new JArray{items=new List<Token>{Layer("FX"),Layer(bad)}});}catch(InvalidOperationException){rejected=true;}Check(rejected,"invalid layer accepted: "+bad);Check(loads==0,"earlier layer processed before later invalid type");}
Invoke(new JArray{items=new List<Token>{Layer("fX")}});
var valid=ApplyLayers(new[]{new VRCAvatarDescriptor.CustomAnimLayer{type=VRCAvatarDescriptor.AnimLayerType.FX,isDefault=false}},new JArray{items=new List<Token>{Layer("fx")}});Check(valid[0].isDefault,"explicit default/case-insensitive semantics changed");
if(PREVIEW){var rejected=false;try{BuildPlan(new VRCAvatarDescriptor(),new JObject{data=new Dictionary<string,Token>{{"specialAnimationLayers",new JArray{items=new List<Token>{Layer("Oops")}}}}});}catch(InvalidOperationException){rejected=true;}Check(rejected,"special layer preview skipped validation");}
}}
'''.replace('BODIES',bodies).replace('PREVIEW','true' if preview else 'false'))

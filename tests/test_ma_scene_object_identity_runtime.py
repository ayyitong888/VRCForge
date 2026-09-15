"""Exact MA resolver compiled against narrow loaded-hierarchy seams; no Unity execution."""
from pathlib import Path
import os
import subprocess
from test_curve_fx_authoring_runtime_contract import method
from test_constraint_conversion_scope_runtime import run
ROOT = Path(__file__).resolve().parents[1]

def test_ma_scene_object_requires_unique_exact_qualified_path(tmp_path):
    path = 'Assets/VRCForge/Editor/MAComponentWriter.cs'
    ref = os.environ.get('VRCFORGE_MA_IDENTITY_GIT_REF')
    source = subprocess.check_output(['git','show',f'{ref}:{path}'],cwd=ROOT,text=True,encoding='utf-8') if ref else (ROOT/path).read_text(encoding='utf-8-sig')
    bodies = '\n'.join(method(source,s) for s in ['private static GameObject ResolveSceneObject','private static string GetTransformPath','private static string NormalizePath'])
    run(tmp_path, r'''using System;using System.Linq;using System.Collections.Generic;
class GameObject {public Transform transform;public string name;}
class Transform {public string name;public Transform parent;public GameObject gameObject;public bool valid=true;
public bool IsChildOf(Transform root){for(var t=parent;t!=null;t=t.parent)if(t==root)return true;return false;}
public Transform Find(string path)=>Resources.items.FirstOrDefault(t=>t.IsChildOf(this)&&Probe.Path(t)==Probe.Path(this)+"/"+path);}
static class Resources {public static List<Transform> items=new List<Transform>();public static T[] FindObjectsOfTypeAll<T>()=>items.Cast<T>().ToArray();}
class Probe { BODIES
public static string Path(Transform t)=>GetTransformPath(t);
static bool IsSceneComponent(Transform t)=>t.valid;
static Transform Add(string name,Transform parent=null){var t=new Transform{name=name,parent=parent};t.gameObject=new GameObject{name=name,transform=t};Resources.items.Add(t);return t;}
static void Check(bool yes,string label){if(!yes)throw new Exception(label);}
static bool Rejected(string path,Transform root=null){try{return ResolveSceneObject(path,root)==null;}catch(InvalidOperationException){return true;}}
static void Main(){
var other=Add("OtherAvatar");var hat=Add("Hat",other);
Check(Rejected("RequestedAvatar/Hat"),"qualified missing path selected unrelated unique leaf");
Check(ResolveSceneObject("Hat",null)==hat.gameObject,"unique leaf regressed");
Check(ResolveSceneObject("OtherAvatar/Hat",null)==hat.gameObject,"exact path regressed");
var duplicateRoot=Add("OtherAvatar");var duplicate=Add("Hat",duplicateRoot);
Check(Rejected("OtherAvatar/Hat"),"duplicate exact path selected first");Check(Rejected("Hat"),"duplicate leaf selected first");
duplicate.valid=false;Check(ResolveSceneObject("OtherAvatar/Hat",null)==hat.gameObject,"invalid scene candidate counted");
Resources.items.Clear();var container=Add("Container");var avatar=Add("Avatar",container);hat=Add("Hat",avatar);
Check(ResolveSceneObject("Hat",avatar)==hat.gameObject,"relative path regressed");
Check(ResolveSceneObject("Avatar/Hat",avatar)==hat.gameObject,"root prefix regressed");
Check(ResolveSceneObject("Container/Avatar/Hat",avatar)==hat.gameObject,"nested full path regressed");
Check(ResolveSceneObject("Avatar",avatar)==avatar.gameObject,"root selector regressed");
var same=Add("Hat",avatar);Check(Rejected("Hat",avatar),"duplicate sibling selected first");
}}
'''.replace('BODIES',bodies))

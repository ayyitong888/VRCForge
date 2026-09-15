"""Actual preflight and binding statements plus real helpers, not a whole Core handler."""
from pathlib import Path
import os,subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method
from test_constraint_conversion_scope_runtime import run
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize("scenario", ["binding", "menu"])
def test_clothing_preflight_relative_bindings_and_menu_capacity(tmp_path, scenario):
    path='Assets/VRCForge/Editor/ClothingFxAuthor.cs';ref=os.environ.get('VRCFORGE_CLOTHING_BINDING_GIT_REF')
    raw=subprocess.check_output(['git','show',f'{ref}:{path}'],cwd=ROOT,text=True,encoding='utf-8') if ref else (ROOT/path).read_text(encoding='utf-8-sig')
    markers=['private static string FirstNonEmpty','private static string SanitizeName','private static string GetTransformPath','private static string NormalizePath','private static void EnsureMenuToggle']
    preflight=''
    if 'private static Dictionary<JObject, string> PreflightItems' in raw:
        markers+=['private static Dictionary<JObject, string> PreflightItems','private static string ResolveRelativeObjectPath']
        preflight=next(l.strip() for l in raw.splitlines() if 'var resolvedPaths = PreflightItems' in l)
    bodies='\n'.join(method(raw,m) for m in markers)
    binding=next(l.strip() for l in raw.splitlines() if 'var binding = new EditorCurveBinding' in l)
    run(tmp_path,r'''using System;using System.Linq;using System.Collections.Generic;
class JObject{public Dictionary<string,string> data=new Dictionary<string,string>();public string this[string k]=>data.ContainsKey(k)?data[k]:null;}class JArray:List<JObject>{}
class GameObject{}class EditorCurveBinding{public string path,propertyName;public Type type;}
class Transform{public string name;public Transform parent;public List<Transform> children=new List<Transform>();public T[] GetComponentsInChildren<T>(bool b)=>new[]{this}.Concat(children.SelectMany(c=>c.GetComponentsInChildren<Transform>(b))).Cast<T>().ToArray();}
class Descriptor{public Transform transform;}
class VRCExpressionsMenu{public const int MAX_CONTROLS=8;public List<Control> controls=new List<Control>();public class Control{public string name;public enum ControlType{Toggle}public ControlType type;public Parameter parameter;public float value;public class Parameter{public string name;}}}
class Probe{BODIES
static Descriptor descriptor;static VRCExpressionsMenu menuAsset;static int writes;
static List<string> Run(JArray items){PREFLIGHT
var result=new List<string>();foreach(var item in items){var objectPath=FirstNonEmpty(item,"sampleObjectPath","objectPath");BINDING
writes++;result.Add(binding.path);EnsureMenuToggle(menuAsset,"Hat",FirstNonEmpty(item,"parameterName"));}return result;}
static JObject Item(string path,string parameter="Hat")=>new JObject{data=new Dictionary<string,string>{{"sampleObjectPath",path},{"parameterName",parameter}}};
static bool Reject(JArray items){writes=0;try{Run(items);return false;}catch(InvalidOperationException){if(writes!=0)throw new Exception("partial writes before invalid item");return true;}}
static void Check(bool ok,string m){if(!ok)throw new Exception(m);}
static void Main(){var root=new Transform{name="Avatar"};var hat=new Transform{name="Hat",parent=root};root.children.Add(hat);descriptor=new Descriptor{transform=root};menuAsset=new VRCExpressionsMenu();if(MENU_ONLY){for(int i=0;i<8;i++)menuAsset.controls.Add(new VRCExpressionsMenu.Control{parameter=new VRCExpressionsMenu.Control.Parameter{name="Existing"+i}});Check(Reject(new JArray{Item("Hat")}),"full menu silently accepted unavailable toggle");return;}Check(Run(new JArray{Item("Avatar/Hat")})[0]=="Hat","full path was not avatar-relative");Check(Run(new JArray{Item("Hat")})[0]=="Hat","relative path regressed");Check(Reject(new JArray{Item("Hat"),Item("OtherAvatar/Hat")}),"outside path accepted");root.children.Add(new Transform{name="Hat",parent=root});Check(Reject(new JArray{Item("Hat")}),"ambiguous sibling accepted");root.children.RemoveAt(1);menuAsset.controls.Clear();for(int i=0;i<8;i++)menuAsset.controls.Add(new VRCExpressionsMenu.Control{parameter=new VRCExpressionsMenu.Control.Parameter{name="P"+i}});Check(Reject(new JArray{Item("Hat")}),"full menu silently accepted unavailable toggle");Check(Run(new JArray{Item("Hat","P0")})[0]=="Hat","existing control rejected on full menu");}}
'''.replace('BODIES',bodies).replace('PREFLIGHT',preflight).replace('BINDING',binding).replace('MENU_ONLY','true' if scenario == 'menu' else 'false'))

def test_clothing_avatar_selector_requires_unique_root(tmp_path):
    path='Assets/VRCForge/Editor/ClothingFxAuthor.cs';ref=os.environ.get('VRCFORGE_CLOTHING_BINDING_GIT_REF')
    raw=subprocess.check_output(['git','show',f'{ref}:{path}'],cwd=ROOT,text=True,encoding='utf-8') if ref else (ROOT/path).read_text(encoding='utf-8-sig')
    bodies='\n'.join(method(raw,m) for m in ['private static VRCAvatarDescriptor ResolveAvatarDescriptor','private static string GetTransformPath','private static string NormalizePath'])
    run(tmp_path,r'''using System;using System.Linq;using System.Collections.Generic;
class Scene{public bool isLoaded=true;public bool IsValid()=>true;}class GameObject{public Scene scene=new Scene();}class Transform{public string name;public Transform parent;}
class VRCAvatarDescriptor{public string name;public GameObject gameObject=new GameObject();public Transform transform;}
static class Resources{public static List<VRCAvatarDescriptor> items=new List<VRCAvatarDescriptor>();public static T[] FindObjectsOfTypeAll<T>()=>items.Cast<T>().ToArray();}static class EditorUtility{public static bool IsPersistent(object x)=>false;}
class Probe{BODIES
static bool Reject(string p){try{ResolveAvatarDescriptor(p);return false;}catch(InvalidOperationException){return true;}}
static void Main(){var a=new VRCAvatarDescriptor{name="Avatar",transform=new Transform{name="Avatar"}};var b=new VRCAvatarDescriptor{name="Avatar",transform=new Transform{name="Avatar"}};Resources.items.Add(a);Resources.items.Add(b);if(!Reject("Avatar")||!Reject(""))throw new Exception("ambiguous avatar selected first");Resources.items.Remove(b);if(ResolveAvatarDescriptor("")!=a||ResolveAvatarDescriptor("avatar")!=a)throw new Exception("unique avatar semantics changed");}}
'''.replace('BODIES',bodies))

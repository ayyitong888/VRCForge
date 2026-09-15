"""Compile exact secondary identity resolvers; SDK/scene-discovery seams, no Unity writes."""
from pathlib import Path
import os,subprocess
from test_curve_fx_authoring_runtime_contract import method
from test_constraint_conversion_scope_runtime import run
ROOT=Path(__file__).resolve().parents[1]
def source(path):
    ref=os.environ.get('VRCFORGE_PLUGIN_SECONDARY_IDENTITY_GIT_REF')
    return subprocess.check_output(['git','show',f'{ref}:{path}'],cwd=ROOT,text=True,encoding='utf-8') if ref else (ROOT/path).read_text(encoding='utf-8-sig')

def test_ma_root_rejects_duplicate_descriptor_paths(tmp_path):
    raw=source('Assets/VRCForge/Editor/MAComponentWriter.cs')
    bodies='\n'.join(method(raw,m) for m in ['private static Transform ResolveAvatarRoot','private static GameObject ResolveSceneObject','private static string GetTransformPath','private static string NormalizePath','private static Type FindType'])
    run(tmp_path,r'''using System;using System.Linq;using System.Collections.Generic;
class GameObject{public Transform transform;}
class Component{public Transform transform;public GameObject gameObject;}
class Transform:Component{public string name;public Transform parent;public bool IsChildOf(Transform r){for(var t=parent;t!=null;t=t.parent)if(t==r)return true;return false;}public Transform Find(string p)=>null;}
static class Resources{public static List<Component> Items=new List<Component>();public static Component[] FindObjectsOfTypeAll(Type t)=>Items.Where(t.IsInstanceOfType).ToArray();public static T[] FindObjectsOfTypeAll<T>()=>Items.OfType<T>().ToArray();}
namespace VRC.SDK3.Avatars.Components{class VRCAvatarDescriptor:Component{}}
class Probe{BODIES
static bool IsSceneComponent(Component c)=>true;
static Transform Add(string n,Transform p=null){var t=new Transform{name=n,parent=p};t.transform=t;t.gameObject=new GameObject{transform=t};Resources.Items.Add(t);return t;}
static void Main(){var a=Add("Avatar");var b=Add("Avatar");Resources.Items.Add(new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor{transform=a,gameObject=a.gameObject});Resources.Items.Add(new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor{transform=b,gameObject=b.gameObject});var first=Add("Hat",a);var intended=Add("Hat",b);bool rejected=false;try{ResolveSceneObject("Avatar/Hat",ResolveAvatarRoot("Avatar"));}catch(InvalidOperationException){rejected=true;}if(!rejected)throw new Exception("ambiguous avatar root selected first");
Resources.Items.RemoveAll(c=>c.transform==b || c.transform==intended);if(ResolveAvatarRoot("Avatar")!=a)throw new Exception("unique root regressed");Resources.Items.Add(new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor{transform=a,gameObject=a.gameObject});if(ResolveAvatarRoot("Avatar")!=a)throw new Exception("same root duplicate descriptor not deduplicated");if(ResolveAvatarRoot("Missing")!=null)throw new Exception("missing changed");}
}'''.replace("BODIES",bodies))

def test_descriptor_viseme_rejects_duplicate_renderer_paths(tmp_path):
    raw=source("Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs")
    raw=raw[raw.index("public static class WriteAvatarDescriptorTool"):]
    body=method(raw,"private static T ResolveSceneComponent")
    run(tmp_path,r'''using System;using System.Linq;using System.Collections.Generic;
class Scene{public bool isLoaded=true;public bool IsValid()=>true;}
class GameObject{public Scene scene=new Scene();}
class Component{public string transform;public GameObject gameObject=new GameObject();}
class SkinnedMeshRenderer:Component{}
static class Resources{public static List<Component> items=new List<Component>();public static T[] FindObjectsOfTypeAll<T>()=>items.OfType<T>().ToArray();}
static class EditorUtility{public static bool IsPersistent(Component o)=>false;}
static class AvatarPrimitiveCrudCore{public static string NormalizePath(string p)=>p;public static string GetTransformPath(string p)=>p;}
class Probe{BODY
static void Main(){var a=new SkinnedMeshRenderer{transform="Avatar/Face"};var b=new SkinnedMeshRenderer{transform="Avatar/Face"};Resources.items.Add(a);Resources.items.Add(b);bool rejected=false;try{ResolveSceneComponent<SkinnedMeshRenderer>("Avatar/Face");}catch(InvalidOperationException){rejected=true;}if(!rejected)throw new Exception("duplicate viseme renderer selected first");Resources.items.Remove(b);Resources.items.Add(a);if(ResolveSceneComponent<SkinnedMeshRenderer>("Avatar/Face")!=a)throw new Exception("unique/deduplicated renderer changed");if(ResolveSceneComponent<SkinnedMeshRenderer>("")!=null)throw new Exception("explicit clear changed");}}
'''.replace("BODY",body))

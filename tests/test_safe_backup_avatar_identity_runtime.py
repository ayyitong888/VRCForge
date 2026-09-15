"""Compile the production backup Avatar selector with scene discovery seams."""
from pathlib import Path
import os
import subprocess
from test_curve_fx_authoring_runtime_contract import method
from test_constraint_conversion_scope_runtime import run
ROOT=Path(__file__).resolve().parents[1]

def test_backup_avatar_selector_requires_unique_match(tmp_path):
    path="Assets/VRCForge/Editor/ConsoleTools.cs"
    ref=os.environ.get("VRCFORGE_BACKUP_AVATAR_GIT_REF")
    raw=subprocess.check_output(["git","show",f"{ref}:{path}"],cwd=ROOT,text=True,encoding="utf-8") if ref else (ROOT/path).read_text(encoding="utf-8-sig")
    bodies="\n".join(method(raw,s) for s in ("private static Transform ResolveAvatarRoot", "private static bool IsSceneObject", "private static Type FindType", "private static string GetTransformPath", "private static string NormalizePath"))
    run(tmp_path,r'''using System;using System.Linq;using System.Collections.Generic;
using UnityEngine;using UnityEditor;
namespace UnityEngine {
public class Object{public bool persistent;}
public class Scene{public bool isLoaded=true;public bool IsValid()=>true;}
public class GameObject:Object{public Scene scene=new Scene();}
public class Component:Object{public string name;public Transform transform;public GameObject gameObject=new GameObject();}
public class Transform{public string name;public Transform parent;}
public static class Resources{public static List<Object> Items=new List<Object>();public static Object[] FindObjectsOfTypeAll(Type t)=>Items.Where(t.IsInstanceOfType).ToArray();}
}
namespace UnityEditor{public static class EditorUtility{public static bool IsPersistent(UnityEngine.Object o)=>o.persistent;}}
namespace VRC.SDK3.Avatars.Components{public class VRCAvatarDescriptor:Component{}}
class Probe{
BODIES
static Component Add(string root,string name){var c=new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor{name=name,transform=new Transform{name=name,parent=new Transform{name=root}}};Resources.Items.Add(c);return c;}
static bool Reject(string path){try{ResolveAvatarRoot(path);return false;}catch(InvalidOperationException){return true;}}
static void Check(bool value,string message){if(!value)throw new Exception(message);}
static void Main(){
var first=Add("A","Avatar");var second=Add("B","Avatar");Check(Reject("Avatar"),"ambiguous leaf selected first avatar");
Check(ResolveAvatarRoot("A/Avatar")==first.transform,"exact selector changed");
Check(ResolveAvatarRoot("a/avatar")==first.transform,"unique case-insensitive selector changed");
Resources.Items.Clear();first=Add("Container","Avatar");first.transform.parent.parent=new Transform{name="SceneRoot"};
Check(ResolveAvatarRoot("Container/Avatar")==first.transform,"unique suffix selector changed");
second=Add("Container","Avatar");second.transform.parent.parent=new Transform{name="OtherRoot"};Check(Reject("Container/Avatar"),"ambiguous suffix selected first avatar");
second.persistent=true;Check(ResolveAvatarRoot("Container/Avatar")==first.transform,"persistent prefab candidate not filtered");
second.persistent=false;second.gameObject.scene.isLoaded=false;Check(ResolveAvatarRoot("Container/Avatar")==first.transform,"unloaded candidate not filtered");
Check(Reject("Missing"),"missing avatar selected");
}}
'''.replace("BODIES",bodies))

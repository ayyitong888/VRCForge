"""Compile production path selectors with exact-name scene doubles, not a Unity acceptance substitute."""
from pathlib import Path
from unittest.mock import patch

import pytest

from test_constraint_conversion_scope_runtime import run
from test_wardrobe_avatar_identity_runtime import _extract

ROOT = Path(__file__).resolve().parents[1]


def _public(source, marker):
    return _extract(source, marker).replace("private static", "public static", 1)


def test_scene_selectors_and_animation_bindings_keep_path_characters(tmp_path):
    primitive = (ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8-sig")
    thry = (ROOT / "Assets/VRCForge/Editor/AvatarPerformanceTool.cs").read_text(encoding="utf-8-sig").split("public static class ThryAvatarPerformanceTool", 1)[1]
    inbound = (ROOT / "Assets/VRCForge/Editor/Generic/InboundReferenceClosureTool.cs").read_text(encoding="utf-8-sig")
    component = (ROOT / "Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(encoding="utf-8-sig")
    binding_line = next(line for line in primitive.splitlines() if "var bindingPath =" in line)
    source_start = primitive.index('var sourceBindingPathText =')
    source_end = primitive.index('var sourceComponentTypeText', source_start)
    serialized_branch = inbound.split("else if (iterator.propertyType == SerializedPropertyType.String)", 1)[1]
    serialized_line = next(line for line in serialized_branch.splitlines() if "var value =" in line)
    code = r'''
using System;using System.Linq;using System.Collections.Generic;
class Scene {public bool isLoaded=true;public bool IsValid()=>true;}
class GameObject {public string name;public Scene scene=new Scene();public Transform transform;public GameObject(string name,Transform parent=null){this.name=name;transform=new Transform{name=name,parent=parent,gameObject=this};if(parent!=null)parent.children[name]=transform;}}
class Component {public GameObject gameObject;public Transform transform=>gameObject.transform;public string name=>gameObject.name;}
class SkinnedMeshRenderer:Component{}
class Transform {public string name;public Transform parent;public GameObject gameObject;public Dictionary<string,Transform> children=new Dictionary<string,Transform>();public Transform Find(string path){Transform t=this;foreach(var segment in path.Split('/')){if(!t.children.TryGetValue(segment,out t))return null;}return t;}}
class Resources {public static List<Component> values=new List<Component>();public static T[] FindObjectsOfTypeAll<T>()=>values.OfType<T>().ToArray();public static object[] FindObjectsOfTypeAll(Type t)=>values.Where(t.IsInstanceOfType).Cast<object>().ToArray();}
class EditorUtility {public static bool IsPersistent(object o)=>false;}
class ComponentCrudCore {public class GameObjectNotFoundException:InvalidOperationException{} public static Dictionary<string,GameObject> objects=new Dictionary<string,GameObject>();public static GameObject ResolveGameObject(string path){if(objects.TryGetValue(path,out var obj))return obj;throw new GameObjectNotFoundException();}
''' + _extract(component, "internal static string NormalizePath") + r'''}
namespace VRC.SDK3.Avatars.Components {class VRCAvatarDescriptor:Component{}}
static class AvatarPrimitiveCrudCore {
''' + _extract(primitive, "internal static string NormalizePath") + _extract(primitive, "internal static string GetTransformPath") + "}\nstatic class Primitive {\n" + _public(primitive, "private static T ResolveSceneComponent<T>") + "}\nstatic class Thry {\n" + _public(thry, "private static GameObject ResolveAvatarObject") + _extract(thry, "private static string NormalizePath") + _extract(thry, "private static string GetTransformPath") + _extract(thry, "private static Type FindType") + "}\nstatic class Inbound {\n" + _public(inbound, "private static Transform ResolveBindingTransform") + _extract(inbound, "private static string NormalizePath") + r'''}
class Params {public string binding,source;public string this[string key]=>key=="bindingPath"?binding:key=="sourceBindingPath"?source:null;}
class Probe {
static int failures;
static void Check(bool value,string label){if(!value){Console.WriteLine("FAIL "+label);failures++;}}
static string[] Binding(string destination,string source){var @params=new Params{binding=destination,source=source};
''' + binding_line + primitive[source_start:source_end] + r'''
return new[]{bindingPath,sourceBindingPath};}
static string SerializedPath(string text){var iterator=new {stringValue=text};
''' + serialized_line + r'''
return value;}
static int Main(){
var avatar=new GameObject("Avatar");
foreach(var name in new[]{"Part "," Part","   "}){
var plain=new GameObject("Part",avatar.transform);var exact=new GameObject(name,avatar.transform);
Resources.values.Clear();Resources.values.Add(new SkinnedMeshRenderer{gameObject=plain});Resources.values.Add(new SkinnedMeshRenderer{gameObject=exact});
try {Check(ReferenceEquals(Primitive.ResolveSceneComponent<SkinnedMeshRenderer>("Avatar/"+name).gameObject,exact),"descriptor "+name);}catch(Exception e){Check(false,"descriptor "+name+": "+e.Message);}
Check(ReferenceEquals(Inbound.ResolveBindingTransform(avatar.transform,name),exact.transform),"inbound "+name);
Check(SerializedPath(name)==name,"serialized "+name);
Check(Binding(name,name).All(x=>x==name),"animation "+name);
}
var bare=new GameObject("Avatar");var spaced=new GameObject(" Avatar ");
ComponentCrudCore.objects["Avatar"]=bare;ComponentCrudCore.objects[" Avatar "]=spaced;
Check(ReferenceEquals(Thry.ResolveAvatarObject(" Avatar "),spaced),"Thry exact avatar");
Resources.values.Clear();Resources.values.Add(new SkinnedMeshRenderer{gameObject=new GameObject("Part",avatar.transform)});
bool rejected=false;try{Primitive.ResolveSceneComponent<SkinnedMeshRenderer>("Avatar/Part ");}catch(InvalidOperationException){rejected=true;}
Check(rejected,"missing exact object must not alias unspaced sibling");
Check(Inbound.ResolveBindingTransform(avatar.transform,"")==avatar.transform,"empty binding is root");
Check(Binding("Part","")[1]=="","explicit source root must remain root");
Check(Binding("Part",null)[1]=="Part","omitted source retains destination default");
Check(AvatarPrimitiveCrudCore.NormalizePath(" Menu ")=="Menu","menu helper behavior unchanged");
return failures==0?0:1;
}}
'''
    run(tmp_path, code)


@pytest.mark.parametrize("tool", ["scan_avatar_performance_sync", "scan_thry_avatar_performance_sync"])
def test_performance_gateway_preserves_exact_avatar_path(tmp_path, tool):
    import dashboard_server as app
    observed = []
    with (
        patch.object(app, "load_dashboard_settings"),
        patch.object(app, "build_dashboard_artifact_path", return_value=tmp_path / "scan.json"),
        patch.object(app, "write_dashboard_json_artifact"),
        patch.object(app, "invoke_unity_mcp", side_effect=lambda settings, name, args: observed.append(args) or {}),
        patch.object(app, "extract_tool_result_payload", return_value={"ok": True}),
    ):
        getattr(app, tool)({"avatarPath": " Avatar "})
    assert observed[0]["avatarPath"] == " Avatar "


def test_inbound_gateway_preserves_target_and_component_paths():
    import dashboard_server as app
    observed = []
    with (
        patch.object(app, "load_dashboard_settings"),
        patch.object(app, "invoke_unity_mcp", side_effect=lambda settings, name, args, **kwargs: observed.append(args) or app.McpResult(exit_code=0, stdout="", stderr="", payload={"structuredContent": {"ok": True}})),
        patch.object(app, "extract_tool_result_payload", return_value={"ok": True}),
    ):
        app.scan_inbound_reference_closure_sync({
            "avatarPath": " Avatar ", "targetPaths": [" Avatar /Part ", "   "],
            "targetComponentSelectors": [{"objectPath": " Avatar /Part ", "componentType": "UnityEngine.Transform"}],
        })
    assert observed[0]["avatarPath"] == " Avatar "
    assert observed[0]["targetPaths"] == [" Avatar /Part ", "   "]
    assert observed[0]["targetComponentSelectors"][0]["objectPath"] == " Avatar /Part "

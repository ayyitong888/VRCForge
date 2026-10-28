"""Replay the production binding preflight with explicit Unity native lookup seams."""
from pathlib import Path

from test_component_property_checkpoint_runtime import run
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs"


def test_binding_targets_report_missing_relative_object_component_and_property(tmp_path):
    source = SOURCE.read_text(encoding="utf-8-sig")
    helpers = "\n".join(method(source, signature) for signature in (
        "internal static void ValidateBindingTarget(", "internal static void ValidateClipBindings("))
    program = r'''
using System;using System.Linq;using System.Collections.Generic;
class GameObject {public string name;public Transform transform;public bool component=true;public GameObject(string n){name=n;transform=new Transform{gameObject=this};}}
class Transform {public GameObject gameObject;public Dictionary<string,Transform> children=new Dictionary<string,Transform>();public Transform Find(string p)=>children.TryGetValue(p,out var t)?t:null;}
class AnimationClip {public EditorCurveBinding[] bindings=Array.Empty<EditorCurveBinding>();}
struct EditorCurveBinding {public string path,propertyName;public Type type;}
static class ComponentCrudCore {public static string GetHierarchyPath(Transform t)=>t.gameObject.name;}
static class AnimationUtility {
 public static object GetAnimatedObject(GameObject r,EditorCurveBinding b){var t=b.path==""?r.transform:r.transform.Find(b.path);return t!=null&&t.gameObject.component?t.gameObject:null;}
 public static Type GetEditorCurveValueType(GameObject r,EditorCurveBinding b)=>b.propertyName=="m_IsActive"?typeof(bool):null;
 public static EditorCurveBinding[] GetCurveBindings(AnimationClip c)=>c.bindings;
 public static EditorCurveBinding[] GetObjectReferenceCurveBindings(AnimationClip c)=>Array.Empty<EditorCurveBinding>();
}
class Probe {
 HELPERS
 static void Check(bool b,string m){if(!b)throw new Exception(m);}
 static string Reject(GameObject root,EditorCurveBinding b){try{ValidateBindingTarget(root,b);throw new Exception("invalid binding accepted");}catch(InvalidOperationException e){return e.Message;}}
 static void Main(){
  var root=new GameObject("Milltina");var outfit=new GameObject("CityNoir");root.transform.children["CityNoir"]=outfit.transform;
  var b=new EditorCurveBinding{path="Milltina/CityNoir",type=typeof(GameObject),propertyName="m_IsActive"};
  var error=Reject(root,b);Check(error.Contains("object does not exist")&&error.Contains("'CityNoir'")&&error.Contains("relative")&&error.Contains("Milltina/CityNoir"),"wrong-root-prefix correction lost");
  b.path="CityNoir";ValidateBindingTarget(root,b);
  outfit.component=false;Check(Reject(root,b).Contains("component does not exist"),"missing component accepted");outfit.component=true;
  b.propertyName="m_Missing";Check(Reject(root,b).Contains("property does not exist"),"missing property accepted");
  b.propertyName="m_IsActive";b.path="";ValidateBindingTarget(root,b);
  var childRoot=new GameObject("NestedAnimator");childRoot.transform.children["CityNoir"]=outfit.transform;b.path="CityNoir";ValidateBindingTarget(childRoot,b);
  b.path="Milltina/CityNoir";var clip=new AnimationClip{bindings=new[]{b}};
  try{ValidateClipBindings(root,clip);throw new Exception("bad clip attached to FX");}catch(InvalidOperationException){}
  Console.WriteLine("wrong-prefix, component, property, root and nested Animator targets replayed");
 }
}
'''.replace("HELPERS", helpers)
    assert "wrong-prefix" in run(tmp_path, program)


def test_fx_attachment_checks_existing_motion_before_asset_mutation():
    source = SOURCE.read_text(encoding="utf-8-sig")
    handler = method(source[source.index("public static class ManageFxAnimatorTool"):], "public static object HandleCommand(")
    assert handler.index("WriteAnimationCurveTool.ValidateClipBindings(descriptor.gameObject") < handler.index("if (preview)")
    assert handler.index("WriteAnimationCurveTool.ValidateClipBindings(descriptor.gameObject") < handler.index("recovery.Capture(controllerPath)")


def test_missing_root_is_not_silently_asset_only(tmp_path):
    raw = SOURCE.read_text(encoding="utf-8-sig")
    helper = method(raw, "internal static GameObject ResolveBindingRoot(")
    program = r'''
using System;using Newtonsoft.Json.Linq;
class GameObject {public string path;}
class ComponentCrudCore {public static int reads;public static GameObject ResolveGameObject(string p){reads++;if(p=="Missing")throw new InvalidOperationException("GameObject not found");return new GameObject{path=p};}}
class Probe {
 HELPER
 static void Check(bool b,string m){if(!b)throw new Exception(m);}
 static string Reject(JObject a){try{ResolveBindingRoot(a);throw new Exception("missing mode accepted");}catch(InvalidOperationException e){return e.Message;}}
 static void Main(){
  Check(Reject(new JObject()).Contains("animatorRootPath is required"),"omitted root silently accepted");
  Check(Reject(JObject.Parse(@"{'assetOnly':false}")).Contains("animatorRootPath is required"),"false asset-only bypasses root");
  Check(ResolveBindingRoot(JObject.Parse(@"{'assetOnly':true}"))==null&&ComponentCrudCore.reads==0,"explicit asset-only resolves a random avatar");
  Check(ResolveBindingRoot(JObject.Parse(@"{'animatorRootPath':'Milltina/NestedAnimator'}")).path=="Milltina/NestedAnimator","root replaced with avatar");
  Check(Reject(JObject.Parse(@"{'animatorRootPath':'Milltina','assetOnly':true}")).Contains("cannot be combined"),"conflicting modes accepted");
  Check(Reject(JObject.Parse(@"{'animatorRootPath':'Missing'}")).Contains("not found"),"missing actual root accepted");
  Console.WriteLine("scene root required; explicit asset-only and nested roots preserved");
 }
}
'''.replace("HELPER", helper)
    assert "scene root required" in run(tmp_path, program)


def test_all_curve_forms_keep_root_contract_for_internal_and_external_lanes():
    import jsonschema
    import pytest
    from unity_shared_input_schemas import ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA as schema
    from unity_execution_plans_scene import build_scene_execution_plan
    import dashboard_server

    curve = {"bindingPath": "CityNoir", "propertyName": "m_IsActive", "constantFloat": 1}
    forms = [
        {"clipPath": "Assets/Outfit.anim", **curve},
        {"clipPath": "Assets/Outfit.anim", "curves": [curve]},
        {"clips": [{"clipPath": "Assets/Outfit.anim", "curves": [curve]}]},
        {"clips": [{"clipPath": "Assets/Outfit.anim", "curveSet": 0}], "curveSets": [[curve]]},
    ]
    for form in forms:
        value = {"projectPath": "D:/Project", **form}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, schema)
        for mode in ({"animatorRootPath": "Milltina"}, {"assetOnly": True}):
            request = {**value, **mode}
            jsonschema.validate(request, schema)
            tool, planned = build_scene_execution_plan("vrcforge_write_animation_curve", request)[0]
            from animation_curve_input import expand_animation_curve_sets
            direct = dashboard_server._avatar_primitive_request(expand_animation_curve_sets(request), preview=False)
            assert tool == "vrc_write_animation_curve" and planned == direct
            assert all(planned[key] == expected for key, expected in mode.items())
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**value, "animatorRootPath": "Milltina", "assetOnly": True}, schema)

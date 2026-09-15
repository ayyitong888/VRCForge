"""Actual Thry resolver; narrow scene/resolver seam, no plugin or Unity call."""
from pathlib import Path
import pytest
from test_constraint_conversion_scope_runtime import run
from test_curve_fx_authoring_runtime_contract import method

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('scenario',['exact_ambiguous','empty_ambiguous','name_ambiguous','unique_name','unique_empty','unique_path'])
def test_thry_requires_unique_avatar_identity(tmp_path,scenario):
    src=(ROOT/'Assets/VRCForge/Editor/AvatarPerformanceTool.cs').read_text(encoding='utf-8').split('public static class ThryAvatarPerformanceTool',1)[1]
    body=method(src,'private static GameObject ResolveAvatarObject')
    code='''using System;using System.Linq;using System.Collections.Generic;
class Scene{public bool IsValid()=>true;public bool isLoaded=true;}class Transform{public string path="Root/Avatar";}
class GameObject{public Scene scene=new Scene();public Transform transform=new Transform();}
class Component{public string name="Avatar";public GameObject gameObject=new GameObject();public Transform transform=>gameObject.transform;}
class Resources{public static Component[] values=new[]{new Component(),new Component()};public static object[] FindObjectsOfTypeAll(Type t)=>values;}
class EditorUtility{public static bool IsPersistent(object o)=>false;}
class ComponentCrudCore{internal sealed class GameObjectNotFoundException:InvalidOperationException{} public static string scenario;public static GameObject ResolveGameObject(string s){if(scenario=="exact_ambiguous")throw new InvalidOperationException("Ambiguous exact path");if(scenario=="unique_path")return Resources.values[0].gameObject;throw new GameObjectNotFoundException();}}
class Probe{static Type FindType(string s)=>typeof(Component);static string NormalizePath(string s)=>s;static string GetTransformPath(Transform t)=>t.path;
'''+body+'''static int Main(){string scenario="'''+scenario+'''";ComponentCrudCore.scenario=scenario;if(scenario.StartsWith("unique_"))Resources.values=new[]{new Component()};string path=scenario.Contains("empty")?"":scenario=="exact_ambiguous"||scenario=="unique_path"?"Root/Avatar":"avatar";bool rejected=false;GameObject selected=null;try{selected=ResolveAvatarObject(path);}catch(InvalidOperationException){rejected=true;}bool expectReject=scenario.Contains("ambiguous");if(rejected!=expectReject||(!expectReject&&!object.ReferenceEquals(selected,Resources.values[0].gameObject)))throw new Exception("Thry unique identity contract violated: "+scenario);return 0;}}'''
    run(tmp_path,code)

"""Execute production read methods with deterministic, read-only Unity seams."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_animation_binding_selection_runtime import sdk
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def scanner_probe(tmp_path_factory):
    folder = tmp_path_factory.mktemp("scan-read-facts")
    controls = (ROOT / "Assets/VRCForge/Editor/AvatarControlScanner.cs").read_text(encoding="utf-8")
    assets = (ROOT / "Assets/VRCForge/Editor/AssetTools.cs").read_text(encoding="utf-8")
    source = r'''
using System; using System.IO; using System.Linq; using System.Collections;
using System.Collections.Generic; using System.Globalization; using Newtonsoft.Json;
namespace UnityEngine { public class Object {public int GetInstanceID()=>1;} }
public class AnimationClip { public string path; }
public static class AssetDatabase {public static string GetAssetPath(AnimationClip c)=>c.path;}
public static class Application {public static string dataPath=>"/Project/Assets";}
public class Probe {
static object GetMemberValue(object o,string key)=>o?.GetType().GetField(key)?.GetValue(o);
class Component {public object expressionParameters;}
class ParameterAsset {public ParameterInfo[] parameters;}
static bool ToBool(object o)=>o!=null && Convert.ToBoolean(o);
static bool IsWardrobeCandidate(string name,string display,string path)=>name.Contains("Cloth") || name.Contains("cloth");
static float ToFloat(object o)=>o==null?0:Convert.ToSingle(o,CultureInfo.InvariantCulture);
static string ReadControlParameterName(object o)=>(string)GetMemberValue(o,"parameterName");
static string[] ReadControlSubParameterNames(object o)=>Array.Empty<string>();
static string NormalizePath(string s)=>s; static string NormalizeAssetPath(string s)=>s;
class Menu {public Control[] controls;}
class Control { public string name,type="Toggle",parameterName="Clothes"; public float value; public object subMenu; }
class ClipBindingItem {public string asset_path;public int binding_count=1,material_binding_count,object_toggle_binding_count,blendshape_binding_count;public List<WarningItem> warnings=new List<WarningItem>();}
class WarningItem {public string clip_path,path,property_name,severity,message;}
static List<AnimationClip> ResolveClips(string a,string b,List<string> paths,bool all)=>paths.Select(p=>new AnimationClip{path=p}).ToList();
static ClipBindingItem ScanClip(AnimationClip c,int keys,bool details)=>new ClipBindingItem{asset_path=c.path};
public static void Main(string[] args) {
 if(args[0]=="parameter-case") {
  var descriptor=new Component{expressionParameters=new ParameterAsset{parameters=new[]{
   new ParameterInfo{name="Clothes",valueType="Int",defaultValue=1,saved=true},
   new ParameterInfo{name="clothes",valueType="Bool",defaultValue=0,saved=false}}}};
  var map=ReadExpressionParameters(descriptor);
  var rows=new List<ControlItem>();
  TraverseMenu(new Menu{controls=new[]{new Control{name="Upper",parameterName="Clothes",value=1}}},"",map,rows,new HashSet<int>(),0);
  Console.WriteLine(JsonConvert.SerializeObject(new{parameterCount=map.Count,menu=rows,unlinked=ReadParameterOnlyItems(map,rows)}));
 } else if(args[0]=="controls") {
  var rows=new List<ControlItem>();
  var menu=new Menu {controls=new[]{new Control{name="A",value=1},new Control{name="B",value=10},new Control{name="B alternate",value=10}}};
  TraverseMenu(menu,"",new Dictionary<string,ParameterInfo>{{"Clothes",new ParameterInfo{name="Clothes",valueType="Int",defaultValue=10}}},rows,new HashSet<int>(),0);
  Console.WriteLine(JsonConvert.SerializeObject(rows));
 } else {
  var paths=new List<string>{"Assets/C.anim","Assets/A.anim","Assets/B.anim","Assets/A.anim"};
  Console.WriteLine(JsonConvert.SerializeObject(BuildAnimationBindingsPayload("Avatar","",paths,false,int.Parse(args[1]),2,false)));
 }
}
'''
    for declaration in ("private static void TraverseMenu", "private static Dictionary<string, ParameterInfo> ReadExpressionParameters", "private static List<ControlItem> ReadParameterOnlyItems", "private class ParameterInfo", "private class ControlItem"):
        source += method(controls, declaration)
    for declaration in ("private static AnimationBindingsPayload BuildAnimationBindingsPayload", "private class AnimationBindingsPayload", "private class AnimationBindingsSummary"):
        source += method(assets, declaration)
    source += "}"
    cs = folder / "Probe.cs"
    cs.write_text(source, encoding="utf-8")
    base, compiler, refs = sdk()
    dll = folder / "Probe.dll"
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    result = subprocess.run([str(base / "dotnet.exe"), str(compiler), "-nologo", "-target:exe", f"-out:{dll}", *[f"-r:{p}" for p in refs.glob("*.dll")], f"-r:{newtonsoft}", str(cs)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.copy2(newtonsoft, folder / newtonsoft.name)
    (folder / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"tfm": "net8.0", "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}))
    return base / "dotnet.exe", dll


def run(probe, *args):
    result = subprocess.run([*[str(p) for p in probe], *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_int_menu_controls_report_own_values_and_default_selection(scanner_probe):
    rows = run(scanner_probe, "controls")
    assert [row["active"] for row in rows] == [False, True, True]
    assert [row["value"] for row in rows] == [1, 10, 10]
    assert [row["menuPath"] for row in rows] == ["A", "B", "B alternate"]


def test_control_scan_preserves_case_distinct_parameter_metadata(scanner_probe):
    result = run(scanner_probe, "parameter-case")
    assert result["parameterCount"] == 2
    assert result["menu"][0]["valueType"] == "Int"
    assert result["menu"][0]["active"] is True
    assert result["menu"][0]["saved"] is True
    assert [item["parameterName"] for item in result["unlinked"]] == ["clothes"]


def test_legacy_clip_scan_marks_omitted_clips(scanner_probe):
    result = run(scanner_probe, "clips", "2")
    assert result["summary"]["clipCount"] == 2
    assert result["summary"]["totalClipCount"] == 3
    assert result["summary"]["clipsTruncated"] is True
    assert "bindingView" in result["readHints"]
    assert [clip["asset_path"] for clip in result["clips"]] == ["Assets/A.anim", "Assets/B.anim"]


def test_legacy_clip_scan_exact_limit_is_complete(scanner_probe):
    result = run(scanner_probe, "clips", "3")
    assert result["summary"]["clipCount"] == result["summary"]["totalClipCount"] == 3
    assert result["summary"]["clipsTruncated"] is False

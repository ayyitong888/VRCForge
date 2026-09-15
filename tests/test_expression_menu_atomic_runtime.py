"""Actual menu builder/planner regression; fake asset database only, no Unity writes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method
ROOT=Path(__file__).resolve().parents[1]


def test_menu_control_failure_preserves_existing_and_preview_rejects(tmp_path):
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1]; newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8").split("public static class ManageExpressionMenuTool",1)[1].split("public static class ManageFxAnimatorTool",1)[0]
    signatures=["private static VRCExpressionsMenu.Control BuildControl", "private static VRCExpressionsMenu.Control.ControlType ParseControlType", "private static T LoadAssetOrNull<T>", "private static void SetSubParameters", "private static List<string> PlanMenuAssetPaths", "private static int ResolveControlIndex"]
    if "private static void ValidateReorder" in source:
        signatures.append("private static void ValidateReorder")
    if "private static void ValidateControlAssets" in source: signatures.append("private static void ValidateControlAssets")
    selected=[method(source,sig) for sig in signatures]
    stubs=r'''
namespace UnityEngine {public class Object {} }
class Texture2D:UnityEngine.Object {}
class VRCExpressionsMenu:UnityEngine.Object {
 public List<Control> controls=new List<Control>();
 public class Control {
  public enum ControlType {Toggle,SubMenu}
  public class Parameter {public string name;}
  public string name; public ControlType type; public float value; public Parameter parameter;
  public Texture2D icon; public VRCExpressionsMenu subMenu; public Parameter[] subParameters;
  public object sdkExtra;
 }
}
static class AssetDatabase {public static T LoadAssetAtPath<T>(string p) where T:UnityEngine.Object {return null;} public static void CreateAsset(object a,string p){throw new Exception("Unexpected asset creation");}}
static class ScriptableObject {public static T CreateInstance<T>() where T:new(){return new T();}}
static class Undo {public static void RegisterCreatedObjectUndo(object a,string s){}}
static class AvatarPrimitiveCrudCore {public static string NormalizeAssetPath(string p)=>p;public static string Sanitize(string p,string fallback)=>p??fallback;public static void EnsureAssetFolder(string p){throw new Exception("Unexpected folder creation");}}
static class GeneratedAssetPaths {public static string UniqueAssetPath(string p)=>p;public static void ReserveAssetPath(string p,List<string> list,Func<string,string> f){list.Add(f(p));}}
'''
    runner=r'''
static int failures;
static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException){return true;}}
public static int Main(){
 var marker=new object();var parameter=new VRCExpressionsMenu.Control.Parameter{name="Keep"};
 var original=new VRCExpressionsMenu.Control{name="Bunny",value=1,parameter=parameter,sdkExtra=marker};
 var bad=JObject.Parse(@"{'newName':'Atomic2700_Temporary','iconAssetPath':'Assets/Missing.png'}");
 Check(Reject(()=>BuildControl(bad,"Assets",original,new Queue<string>())),"missing icon rejects");
 Check(original.name=="Bunny"&&original.value==1&&ReferenceEquals(original.parameter,parameter),"failed construction preserves original control");
 original.name="Bunny";
 var menu=new VRCExpressionsMenu();menu.controls.Add(original);
 Check(Reject(()=>PlanMenuAssetPaths("update",menu,"",bad,"Assets","Assets/Menu.asset")),"preview planner rejects missing icon");
 bad=JObject.Parse(@"{'newName':'Changed','subMenuAssetPath':'Assets/Missing.asset'}");
 Check(Reject(()=>BuildControl(bad,"Assets",original,new Queue<string>()))&&original.name=="Bunny","missing submenu preserves original");
 var good=JObject.Parse(@"{'newName':'Changed','parameterName':'New','subParameters':['A','B']}");
 var next=BuildControl(good,"Assets",original,new Queue<string>());
 Check(!ReferenceEquals(next,original)&&original.name=="Bunny"&&next.name=="Changed","successful construction returns detached replacement");
 Check(ReferenceEquals(next.sdkExtra,marker)&&next.value==1&&original.parameter.name=="Keep"&&next.parameter.name=="New","SDK fields preserved and nested original not modified");
 Check(next.subParameters.Length==2&&next.subParameters[1].name=="B"&&original.subParameters==null,"subparameter array replaced without sharing mutation");
 return failures==0?0:1;
}
'''
    program="using System;using System.Linq;using System.Reflection;using System.Collections.Generic;using Newtonsoft.Json.Linq;"+stubs+"public class Probe {"+"\n".join(selected)+runner+"}"
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==7

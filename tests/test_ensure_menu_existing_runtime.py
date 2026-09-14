"""Actual menu builder/planner regression; fake asset database only, no Unity writes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method
ROOT=Path(__file__).resolve().parents[1]


def test_ensure_existing_name_never_plans_overflow(tmp_path):
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1]; newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs").read_text(encoding="utf-8").split("public static class EnsureExpressionMenuControlTool",1)[1].split("public static class ",1)[0]
    signatures=["private static bool MenuContainsControl","private static VRCExpressionsMenu FindMenuByPath(","private static VRCExpressionsMenu FindMenuByPathParts","private static List<string> PlanMenuAssetPaths","private static void PlanMenuRoom"]
    if "private static VRCExpressionsMenu.Control FindControlByName" in source:signatures.append("private static VRCExpressionsMenu.Control FindControlByName")
    signatures.append("private static void UpdateControl")
    selected=[method(source,x) for x in signatures]
    stubs=r'''
class VRCExpressionsMenu {public const int MAX_CONTROLS=8;public int GetInstanceID()=>GetHashCode();public List<Control> controls=new List<Control>();public class Control {public enum ControlType{Toggle,SubMenu}public string name;public ControlType type;public float value;public Parameter parameter;public VRCExpressionsMenu subMenu;public class Parameter{public string name;}}}
static class Mathf{public static int RoundToInt(float f)=>(int)Math.Round(f);}
static class AvatarAuthoringCrudCore{public static string Sanitize(string s,string fallback)=>s??fallback;}
static class GeneratedAssetPaths{public static string UniqueAssetPath(string p)=>p;public static void ReserveAssetPath(string p,List<string> list,Func<string,string> f){list.Add(f(p));}}
'''
    runner=r'''
static int failures;static void Check(bool b,string n){Console.WriteLine((b?"PASS ":"FAIL ")+n);if(!b)failures++;}
static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException){return true;}}
public static int Main(){
 var menu=new VRCExpressionsMenu();var bunny=new VRCExpressionsMenu.Control{name="Bunny",value=1,parameter=new VRCExpressionsMenu.Control.Parameter{name="Wardrobe"}};menu.controls.Add(bunny);
 for(int i=1;i<8;i++)menu.controls.Add(new VRCExpressionsMenu.Control{name="Other"+i});var last=menu.controls[7];
 bool exists=MenuContainsControl(menu,"","Bunny","Wardrobe",2,new HashSet<int>(),0);
 Check(exists,"new requested value retains existing control identity");
 Check(PlanMenuAssetPaths(menu,"","Assets","Assets/Menu.asset","Bunny",VRCExpressionsMenu.Control.ControlType.Toggle,exists).Count==0,"full existing menu does not reserve overflow for update");
 Check(MenuContainsControl(menu,"","Bunny","DifferentParameter",2,new HashSet<int>(),0),"requested parameter change does not create duplicate name");
 Check(menu.controls.Count==8&&ReferenceEquals(menu.controls[7],last)&&bunny.value==1,"preview leaves layout and values unchanged");
 UpdateControl(bunny,VRCExpressionsMenu.Control.ControlType.Toggle,"Wardrobe",2);
 Check(menu.controls.Count==8&&ReferenceEquals(menu.controls[0],bunny)&&ReferenceEquals(menu.controls[7],last)&&bunny.value==2,"actual update changes existing control without pagination or reordering");
 Check(bunny.parameter.name=="Wardrobe"&&menu.controls.Count(c=>c.name=="Bunny")==1,"actual update retains one requested name and parameter");
 UpdateControl(bunny,VRCExpressionsMenu.Control.ControlType.Toggle,"Wardrobe",1);
 Check(bunny.value==1&&menu.controls.Count==8,"value restore updates same control rather than adding another");
 menu.controls.Add(new VRCExpressionsMenu.Control{name="Bunny"});
 Check(Reject(()=>MenuContainsControl(menu,"","Bunny","Wardrobe",1,new HashSet<int>(),0)),"duplicate exact names reject before mutation");
 return failures==0?0:1;
}
'''
    program="using System;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;"+stubs+"class Probe{"+"\n".join(selected)+runner+"}"
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==8

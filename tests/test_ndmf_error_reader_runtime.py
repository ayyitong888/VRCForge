"""Compile and replay the actual reflective reader against NDMF API/error fixtures."""
import os
from pathlib import Path
import subprocess

import pytest
from test_component_property_checkpoint_runtime import run

ROOT = Path(__file__).resolve().parents[1]
READER = "Assets/VRCForge/Editor/EditorDialogTools.cs"


def reader_source():
    ref = os.environ.get("VRCFORGE_NDMF_READER_GIT_REF")
    return subprocess.check_output(["git", "show", f"{ref}:{READER}"], cwd=ROOT, text=True, encoding="utf-8") if ref else (ROOT / READER).read_text(encoding="utf-8")


@pytest.mark.parametrize("scenario", ["not_installed", "no_report", "empty_report", "latest_report", "missing_api", "read_exception", "format_exception"])
def test_actual_reader_distinguishes_absence_empty_report_and_failed_read(tmp_path, scenario):
    fixture = r'''
namespace nadena.dev.ndmf {
 public interface IError {string ToMessage();}
 public class Error : IError {public string Severity=>"InternalError";public string TitleKey=>"fixture";public object[] References=>new object[]{new ObjectReference()};public virtual string ToMessage(){FORMAT} }
 public static class FixCounter {public static int Calls;}
 public class InlineErrorWithAutofix : Error {private readonly System.Action _autofix;public InlineErrorWithAutofix():base() {_autofix=()=>FixCounter.Calls++;}public new string ToMessage()=>"latest actual error";public string FormatTitle()=>"Fixture Auto Fix";}
 public class ObjectReference {public string Path=>"Avatar/Body/Texture";public object Object=>new UnityEngine.Object("MipTexture");public System.Type Type=>typeof(UnityEngine.Object);}
 public class Context {public IError TheError=new Error();public string PassName="Optimize";public object Plugin=new Error();}
 public class ErrorReport {internal static System.Collections.IList Reports=new System.Collections.ArrayList();public string AvatarName=>"Latest avatar";public System.Collections.IEnumerable Errors {get {READ}} }
}
'''
    setup = ""
    if scenario == "not_installed":
        fixture = ""
        assertions = 'Check(result.IsSuccessful && !(bool)p["readable"] && !(bool)p["reportAvailable"] && p["ndmfErrorCount"].Type==JTokenType.Null,"not installed is unavailable, not zero errors");Check((string)p["readStatus"]=="not_installed" && !string.IsNullOrEmpty((string)p["nextAction"]),"not-installed explanation");'
    else:
        read = "return new object[0];" if scenario == "empty_report" else "return new object[]{new Context()};"
        if scenario == "read_exception": read='throw new InvalidOperationException("report getter exploded");'
        format_body = 'throw new InvalidOperationException("format exploded");' if scenario == "format_exception" else 'return "latest actual error";'
        fixture=fixture.replace("FORMAT",format_body).replace("READ",read)
        if scenario == "latest_report": fixture=fixture.replace("IError TheError=new Error()", "IError TheError=new InlineErrorWithAutofix()")
        if scenario != "no_report": setup="nadena.dev.ndmf.ErrorReport.Reports.Add(new nadena.dev.ndmf.ErrorReport());"
        if scenario == "missing_api": fixture=fixture.replace("public System.Collections.IEnumerable Errors", "public System.Collections.IEnumerable RenamedErrors")
        if scenario == "no_report":
            assertions='Check(result.IsSuccessful && (bool)p["readable"] && !(bool)p["reportAvailable"] && p["ndmfErrorCount"].Type==JTokenType.Null,"no retained report is not zero errors");Check((string)p["readStatus"]=="no_report","no-report status");'
        elif scenario == "empty_report":
            assertions='Check(result.IsSuccessful && (bool)p["readable"] && (bool)p["reportAvailable"] && (int)p["ndmfErrorCount"]==0,"successfully read empty report");'
        elif scenario == "latest_report":
            setup=setup+setup
            assertions='Check(result.IsSuccessful && (bool)p["readable"] && (bool)p["reportAvailable"] && (int)p["ndmfErrorCount"]==1,"only latest report is read");Check((string)p["ndmfErrors"][0]["message"]=="latest actual error" && (string)p["ndmfErrors"][0]["pass"]=="Optimize","actual report context");var a=p["ndmfErrors"][0]["affectedObjects"][0];Check((string)a["path"]=="Avatar/Body/Texture"&&(string)a["name"]=="MipTexture"&&(string)a["assetPath"]=="Assets/Textures/MipTexture.png"&&(string)a["guid"]=="fixture-guid"&&(string)a["type"]=="Object","actual NDMF references retained");var fx=p["ndmfErrors"][0]["autoFix"];Check((bool)fx["available"]&&(string)fx["action"]["source"]=="ndmf"&&(int)fx["action"]["index"]==0&&(string)fx["action"]["label"]=="Fixture Auto Fix"&&(string)fx["action"]["impact"]=="native_callback_scope_unknown","native action described");VRCForge.Editor.NativeNdmfFixActions.Resolve((JObject)fx["action"]).Invoke();Check(nadena.dev.ndmf.FixCounter.Calls==1,"selected current native callback invoked");var wrong=(JObject)fx["action"].DeepClone();wrong["message"]="different row";try{VRCForge.Editor.NativeNdmfFixActions.Resolve(wrong).Invoke();throw new System.Exception("changed selection accepted");}catch(System.InvalidOperationException){}Check(nadena.dev.ndmf.FixCounter.Calls==1,"changed row does not invoke callback");'
        else:
            reason={"missing_api":"Errors","read_exception":"report getter exploded","format_exception":"format exploded"}[scenario]
            assertions='Check(!result.IsSuccessful && !(bool)p["readable"] && (bool)p["reportAvailable"] && p["ndmfErrorCount"].Type==JTokenType.Null,"read failure cannot report no errors");Check(result.Message.Contains("'+reason+'") && !string.IsNullOrEmpty((string)p["nextAction"]),"exact reason and next step");Check(!string.IsNullOrEmpty((string)p["diagnostics"]["exceptionType"]),"exception type retained");'
    attributes=r'''
namespace UnityEngine { public class Object {public string name;public Object(string value){name=value;}} }
namespace UnityEditor { public static class AssetDatabase {public static string GetAssetPath(UnityEngine.Object o)=>"Assets/Textures/MipTexture.png";public static string AssetPathToGUID(string p)=>"fixture-guid";} }
namespace VRCForge.Core.MCP {
 public enum VRCForgeCommandAccess { ReadOnly }
 public class VRCForgeCommandAttribute:System.Attribute {public VRCForgeCommandAttribute(string toolId){}public string Summary{get;set;}public VRCForgeCommandAccess Access{get;set;}}
}
'''
    result_source=(ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs").read_text(encoding="utf-8")
    program=attributes+fixture+'class Probe {static void Check(bool value,string m){if(!value)throw new System.Exception(m);}static void Main(){'+setup+'var result=(VRCForge.Core.MCP.VRCForgeToolResult)VRCForge.Editor.NdmfErrorReader.HandleCommand(new Newtonsoft.Json.Linq.JObject());var p=(Newtonsoft.Json.Linq.JObject)result.Payload;'+assertions+'System.Console.WriteLine("PASS NDMF read semantics");}}'
    adapter=(ROOT / "Assets/VRCForge/Editor/NativeNdmfFixActions.cs").read_text(encoding="utf-8")
    program=result_source+reader_source()+adapter+program
    lines=program.splitlines()
    usings=[line for line in lines if line.startswith("using ")]
    body=[line for line in lines if not line.startswith("using ")]
    program="\n".join(dict.fromkeys(usings))+"\n"+"\n".join(body)
    assert "PASS NDMF read semantics" in run(tmp_path,program)

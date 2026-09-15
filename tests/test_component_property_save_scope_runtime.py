"""Compile the exact production setter/save segment; narrow native persistence seams, not Unity."""
import json,os,subprocess,shutil
from pathlib import Path

def test_scene_component_write_does_not_save_unrelated_assets(tmp_path):
    root=Path(__file__).resolve().parents[1]
    ref=os.environ.get("VRCFORGE_COMPONENT_SAVE_GIT_REF")
    raw=subprocess.check_output(["git","show",f"{ref}:Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs"],cwd=root,text=True,encoding="utf-8") if ref else (root/"Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(encoding="utf-8")
    start=raw.index("ComponentCrudCore.SetMemberValue(component, member, newValue);",raw.index("public static class SetPropertyTool"))
    end=raw.index('failureStage = "persisted_readback";',start)
    slice=raw[start:end]
    out=tmp_path
    program=r'''using System;using Newtonsoft.Json.Linq;
    static class EditorUtility {public static void SetDirty(object value){}}
    static class AssetDatabase {public static bool UnrelatedDirty=true,UnrelatedSaved=false;public static void SaveAssets(){UnrelatedSaved=UnrelatedDirty;UnrelatedDirty=false;}}
    static class ComponentCrudCore {public static void SetMemberValue(object a,object b,object c){} public static bool SceneSaved; public static object SaveAndResolveScene(object s){SceneSaved=true;return s;}}
    class Probe{static void Main(){object component=new object(),member=new object(),newValue=true,go=new object(),beforeScene=new object();bool mutationApplied=false;string failureStage="";
    SLICE
    Console.WriteLine(new JObject{["sceneSaved"]=ComponentCrudCore.SceneSaved,["unrelatedAssetSaved"]=AssetDatabase.UnrelatedSaved,["unrelatedAssetDirty"]=AssetDatabase.UnrelatedDirty,["mutationApplied"]=mutationApplied}.ToString(Newtonsoft.Json.Formatting.None));}}
    '''.replace("SLICE",slice)
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet";csc=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))[-1];refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))[-1];dotnet=shutil.which("dotnet");n=csc.parents[2]/"Newtonsoft.Json.dll"
    cs=out/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=out/"Probe.dll"
    r=subprocess.run([dotnet,str(csc),"-nologo","-target:exe","-nostdlib+",f"-out:{dll}",f"-r:{n}"]+[f"-r:{x}" for x in refs.glob("*.dll")]+[str(cs)],capture_output=True,text=True);assert r.returncode==0,r.stdout+r.stderr
    shutil.copy2(n,out/"Newtonsoft.Json.dll");(out/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}))
    r=subprocess.run([dotnet,str(dll)],capture_output=True,text=True);assert r.returncode==0,r.stdout+r.stderr
    result=json.loads(r.stdout)
    assert result["sceneSaved"] is True
    assert result["unrelatedAssetSaved"] is False, result
    assert result["unrelatedAssetDirty"] is True, result

import json, os, shutil, subprocess
from pathlib import Path
from test_curve_fx_authoring_runtime_contract import method


def test_actual_clip_evidence_and_read_path(tmp_path):
    source = Path("Assets/VRCForge/Editor/RuntimeObservationTool.cs").read_text(encoding="utf-8")
    assert "ClipSnapshot(controller.GetCurrentAnimatorClipInfo(i))" in source
    assert "ClipSnapshot(controller.GetNextAnimatorClipInfo(i))" in source
    assert "controller.GetLayerName(i)" in source and "controller.GetLayerWeight(i)" in source
    actual = method(source, "internal static JObject ClipSnapshot")
    base=Path(os.environ["DOTNET_ROOT"]) if os.environ.get("DOTNET_ROOT") else Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet"
    compiler=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/net8.0"))[-1]
    newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    seam=r'''using System;using Newtonsoft.Json.Linq;
class AnimationClip {public string name,Path;public static implicit operator bool(AnimationClip c)=>c!=null;}
struct AnimatorClipInfo {public AnimationClip clip;public float weight;}
static class AssetDatabase {public static string GetAssetPath(AnimationClip c)=>c.Path??"";public static string AssetPathToGUID(string p)=>"guid:"+p;}
'''
    runner=r'''static int Main(){
var clips=new AnimatorClipInfo[18];for(int i=0;i<clips.Length;i++)clips[i]=new AnimatorClipInfo{clip=new AnimationClip{name="actual"+i,Path="Assets/"+i+".anim"},weight=.25f};
var value=ClipSnapshot(clips);var rows=(JArray)value["clips"];
if(rows.Count!=16||(int)value["totalCount"]!=18||!(bool)value["truncated"]||(string)rows[0]["assetGuid"]!="guid:Assets/0.anim"||(float)rows[0]["weight"]!=.25f)throw new Exception("actual clip or bounded evidence wrong");
var runtime=ClipSnapshot(new[]{new AnimatorClipInfo{clip=new AnimationClip{name="runtime"}},new AnimatorClipInfo()});
if((string)runtime["clips"][0]["assetIdentity"]!="runtime_no_asset"||(string)runtime["clips"][1]["assetIdentity"]!="missing_clip")throw new Exception("runtime/missing identity wrong");
if((int)ClipSnapshot(Array.Empty<AnimatorClipInfo>())["totalCount"]!=0)throw new Exception("empty evidence wrong");
Console.WriteLine("PASS actual clip array, asset/runtime/null, weight, bound and empty");return 0;}
'''
    cs=tmp_path/"Probe.cs";cs.write_text(seam+"class Probe {"+actual+runner+"}",encoding="utf-8")
    dll=tmp_path/"Probe.dll"
    command=[str(base/"dotnet.exe"),str(compiler),"-nologo","-target:exe","-nostdlib+",f"-out:{dll}"]+[f"-r:{p}" for p in refs.glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"net8.0","framework":{"name":"Microsoft.NETCore.App","version":"8.0.0"}}}))
    result=subprocess.run([str(base/"dotnet.exe"),str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr

"""Isolated C# regression for create-new writes and ownership-bound rollback."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_texture_patch_mask_runtime import ROOT, SOURCE, STUBS


PROBE = r'''
class OwnershipProbe {
 static string fault; static string target="Assets/output.png"; public static byte[] Sentinel={9,8,7,6}; public static bool InjectCollision; public static bool SameHashCollision;
 static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)Environment.ExitCode=1;}
 public static void CreateCollision(string path,byte[] output){if(SameHashCollision)Sentinel=(byte[])output.Clone();File.WriteAllBytes(path,Sentinel);}
 static void Save(string path){var t=new UnityEngine.Texture2D(1,1,UnityEngine.TextureFormat.RGBA32,false,false);t.SetPixels32(new[]{new UnityEngine.Color32(1,2,3,4)});File.WriteAllBytes(path,t.EncodeToPNG());}
 static JObject Run(JObject args)=>JObject.FromObject(VRCForge.Editor.TexturePatchTool.HandleCommand(args));
 public static Action<string> OnImport;
 public static void Main(string[] args){fault=args[0];Directory.CreateDirectory("Assets");Save("Assets/source.png");
  var input=JObject.Parse(@"{'sourceTexturePath':'Assets/source.png','targetTexturePath':'Assets/output.png','region':[0,0,1,1],'red':200,'green':100,'blue':50,'alpha':255,'opacity':1,'featherPixels':0,'preview':true}");
  var preview=Run(input);var apply=(JObject)input.DeepClone();apply["preview"]=false;foreach(var p in (JObject)preview["data"]["applyBinding"])apply[p.Key]=p.Value.DeepClone();
  InjectCollision=fault=="collision"||fault=="collision_same";SameHashCollision=fault=="collision_same";
  if(fault=="target"||fault=="meta")OnImport=_=>{if(fault=="target")File.WriteAllBytes(target,Sentinel);else File.WriteAllBytes(target+".meta",Sentinel);throw new InvalidOperationException("injected import failure");};
  var result=Run(apply);var preserved=fault=="meta"?File.Exists(target+".meta")&&File.ReadAllBytes(target+".meta").SequenceEqual(Sentinel):File.Exists(target)&&File.ReadAllBytes(target).SequenceEqual(Sentinel);
  var recovery=result["data"]?["checkpointRecoveryRequired"]?.Value<bool>()==true;
  Check(!result.Value<bool>("success"),"operation fails without committing");Check(preserved,"foreign target or metadata survives");
  Check(fault.StartsWith("collision")?!recovery:recovery,"collision stops before mutation; uncertain rollback requires recovery");
 }
}
'''


@pytest.mark.parametrize("fault", ["collision", "collision_same", "target", "meta"])
def test_texture_patch_only_rolls_back_owned_files(tmp_path, fault):
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK/reference pack required")
    fixture = tmp_path / "Probe.cs"
    stubs = "using System; using System.IO; using System.Linq; using Newtonsoft.Json.Linq;\n" + STUBS.split("class Probe {", 1)[0]
    stubs = stubs.replace(
        "public static void ImportAsset(string path,ImportAssetOptions options){}",
        'public static void ImportAsset(string path,ImportAssetOptions options){OwnershipProbe.OnImport?.Invoke(path);if(!File.Exists(path+".meta"))File.WriteAllText(path+".meta","guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");}',
    )
    fixture.write_text(stubs + PROBE, encoding="utf-8")
    source_text = SOURCE.read_text(encoding="utf-8")
    if os.environ.get("VRCFORGE_TEXTURE_PATCH_OWNERSHIP_BASELINE") == "1":
        source_text = subprocess.run(
            ["git", "show", "HEAD:Assets/VRCForge/Editor/TexturePatchTool.cs"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
    anchor = "var outputBytes = BuildPatchedPngBytes(sourceWidth, sourceHeight, patchedPixels);"
    assert anchor in source_text
    source_text = source_text.replace(anchor, anchor + "\n                    if (OwnershipProbe.InjectCollision) OwnershipProbe.CreateCollision(request.TargetFilePath, outputBytes);", 1)
    source_file = tmp_path / "TexturePatchTool.cs"
    source_file.write_text(source_text, encoding="utf-8")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    dll = tmp_path / "Probe.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}", f"-r:{newtonsoft}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")] + [str(source_file), str(fixture)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(
        '{"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}',
        encoding="utf-8",
    )
    run = subprocess.run([dotnet, str(dll), fault], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.count("PASS ") == 3, run.stdout

"""Whole production C# replay of image-byte snapshot consistency.

The existing codec substitute preserves RGBA; this does not test Unity's PNG
decoder. All file changes are confined to the fixture's temporary project.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_texture_patch_mask_runtime import SOURCE, STUBS


PROBE = r'''
class SnapshotProbe {
 static byte[] Encode(byte r,byte a){var t=new UnityEngine.Texture2D(2,1,UnityEngine.TextureFormat.RGBA32,false,false);t.SetPixels32(new[]{new UnityEngine.Color32(r,r,r,a),new UnityEngine.Color32(r,r,r,a)});return t.EncodeToPNG();}
 static string Hash(byte[] bytes){using(var sha=System.Security.Cryptography.SHA256.Create())return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
 static JObject Run(JObject p)=>JObject.FromObject(VRCForge.Editor.TexturePatchTool.HandleCommand(p));
 static void Check(bool yes,string name){Console.WriteLine((yes?"PASS ":"FAIL ")+name);if(!yes)Environment.ExitCode=1;}
 static void Hook(int index,Action action){int count=0;UnityEngine.Texture2D.AfterDecode=()=>{if(++count==index){UnityEngine.Texture2D.AfterDecode=null;action();}};}
 public static Action<string> OnImport;
 static void Main(string[] args){
  Directory.CreateDirectory("Assets");var approved=Encode(10,20);var other=Encode(100,200);File.WriteAllBytes("Assets/source.png",approved);
  var p=JObject.Parse(@"{'sourceTexturePath':'Assets/source.png','targetTexturePath':'Assets/output.png','region':[0,0,1,1],'red':50,'green':60,'blue':70,'alpha':255,'opacity':1,'featherPixels':0,'preview':true}");
  if(args[0]=="source_preview"){
   Hook(1,()=>File.WriteAllBytes("Assets/source.png",other));var result=Run(p);
   Check(result.Value<bool>("success"),"preview succeeds");
   Check((string)result["data"]["sourceFileHash"]==Hash(approved),"preview hash describes decoded source snapshot");return;
  }
  if(args[0]=="mask_preview"){
   p["maskTexturePath"]="Assets/mask.png";File.WriteAllBytes("Assets/mask.png",Encode(255,255));var captured=File.ReadAllBytes("Assets/mask.png");
   Hook(2,()=>File.WriteAllBytes("Assets/mask.png",Encode(0,255)));var result=Run(p);
   Check(result.Value<bool>("success"),"masked preview succeeds");
   Check((string)result["data"]["maskFileHash"]==Hash(captured),"mask hash describes decoded mask snapshot");return;
  }
  var preview=Run(p);p["preview"]=false;foreach(var pair in (JObject)preview["data"]["applyBinding"])p[pair.Key]=pair.Value.DeepClone();
  if(args[0]=="source_apply"){
   File.WriteAllBytes("Assets/source.png",other);Hook(1,()=>File.WriteAllBytes("Assets/source.png",approved));var result=Run(p);
   Check(!result.Value<bool>("success"),"nonapproved decoded source rejects");
   Check(!File.Exists("Assets/output.png")&&!File.Exists("Assets/output.png.meta"),"source drift leaves zero output artifacts");return;
  }
  if(args[0]=="target_readback"){
   OnImport=path=>File.WriteAllBytes(path,other);var result=Run(p);
   Check(!result.Value<bool>("success"),"changed persisted pixels reject");
   Check(File.ReadAllBytes("Assets/output.png").SequenceEqual(other),"failed readback preserves externally changed output");return;
  }
  if(args[0]=="redundant_hashes"){
   var result=Run(p);var data=(JObject)result["data"];
   Check(result.Value<bool>("success")&&data.Value<bool>("persistedReadback"),"normal apply keeps persisted verification");
   Check(data["targetFileHash"]==null&&data["changedPixelMaskHash"]==null&&data["readback"]["fileHash"]==null,"unused hash fields are absent");
   Check(data["readback"].Value<bool>("alphaUnchanged")&&data["readback"].Value<bool>("outsideAndProtectedPixelsUnchanged"),"alpha and protected pixel checks remain");
   Check(data.Value<int>("changedPixelCount")==1&&JToken.DeepEquals(data["changedBounds"],JArray.Parse("[0,0,1,1]")),"change count and bounds remain");return;
  }
  throw new InvalidOperationException("Unknown fixture case");
 }
}
'''


@pytest.fixture(scope="module")
def compiled_snapshot(tmp_path_factory):
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK/reference pack required")
    folder = tmp_path_factory.mktemp("texture-snapshot-compiler")
    stubs = STUBS.split("class Probe {", 1)[0]
    stubs = stubs.replace("public class Texture2D:Object {", "public class Texture2D:Object { public static Action AfterDecode;")
    stubs = stubs.replace("}return true;}", "}AfterDecode?.Invoke();return true;}")
    stubs = stubs.replace("public static void ImportAsset(string path,ImportAssetOptions options){}", "public static void ImportAsset(string path,ImportAssetOptions options){SnapshotProbe.OnImport?.Invoke(path);}")
    fixture = folder / "Probe.cs"
    fixture.write_text(stubs + PROBE, encoding="utf-8")
    dll = folder / "Probe.dll"
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}", f"-r:{newtonsoft}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")] + [str(SOURCE), str(fixture)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, folder / "Newtonsoft.Json.dll")
    (folder / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    return dotnet, dll


@pytest.mark.parametrize("case", ["source_preview", "source_apply", "target_readback", "mask_preview", "redundant_hashes"])
def test_pixels_and_hash_use_the_same_snapshot(compiled_snapshot, tmp_path, case):
    dotnet, dll = compiled_snapshot
    result = subprocess.run([dotnet, str(dll), case], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL " not in result.stdout

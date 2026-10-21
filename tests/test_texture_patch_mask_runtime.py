"""Compile the whole production tool and execute it with Unity API substitutes.

The codec substitute preserves RGBA exactly; this is not a Unity/PNG codec test.
Files and the child compiler/runtime belong to pytest's isolated temporary root.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Assets/VRCForge/Editor/TexturePatchTool.cs"

STUBS = r'''
using System; using System.IO; using System.Linq; using Newtonsoft.Json.Linq;
namespace UnityEngine {
 public class Object {public static void DestroyImmediate(Object value){}}
 public struct Color32 { public byte r,g,b,a; public Color32(byte r,byte g,byte b,byte a){this.r=r;this.g=g;this.b=b;this.a=a;} }
 public enum TextureFormat {RGBA32}
 public static class Application {public static string dataPath=Path.Combine(Directory.GetCurrentDirectory(),"Assets");}
 public static class Mathf {public static int Clamp(int v,int lo,int hi)=>Math.Max(lo,Math.Min(hi,v));}
 public class Texture2D:Object {
  public int width,height; Color32[] pixels;
  public Texture2D(int w,int h,TextureFormat f,bool m,bool l){width=w;height=h;}
  public bool LoadImage(byte[] bytes,bool n){using(var reader=new BinaryReader(new MemoryStream(bytes))){width=reader.ReadInt32();height=reader.ReadInt32();pixels=new Color32[width*height];for(int i=0;i<pixels.Length;i++)pixels[i]=new Color32(reader.ReadByte(),reader.ReadByte(),reader.ReadByte(),reader.ReadByte());}return true;}
  public Color32[] GetPixels32()=>(Color32[])pixels.Clone();
  public void SetPixels32(Color32[] p){pixels=(Color32[])p.Clone();}
  public void Apply(bool a,bool b){}
  public byte[] EncodeToPNG(){using(var m=new MemoryStream()){using(var w=new BinaryWriter(m)){w.Write(width);w.Write(height);foreach(var p in pixels){w.Write(p.r);w.Write(p.g);w.Write(p.b);w.Write(p.a);}w.Flush();return m.ToArray();}}}
 }
}
namespace UnityEditor {
 [Flags]public enum ImportAssetOptions {ForceSynchronousImport=1,ForceUpdate=2}
 public static class AssetDatabase {
  public static string AssetPathToGUID(string path)=>new string('a',32);
  public static T LoadAssetAtPath<T>(string path) where T:class=>null;
  public static void ImportAsset(string path,ImportAssetOptions options){}
  public static bool DeleteAsset(string path){File.Delete(path);File.Delete(path+".meta");return true;}
 }
}
namespace VRCForge.Core.MCP {
 [AttributeUsage(AttributeTargets.Class)]public class VRCForgeCommandAttribute:Attribute {public string Summary{get;set;}public VRCForgeCommandAttribute(string toolId){}}
 [AttributeUsage(AttributeTargets.Property)]public class VRCForgeInputAttribute:Attribute {public bool IsRequired{get;set;}public VRCForgeInputAttribute(string text){}}
 public static class VRCForgeToolResult {
  public static object Completed(string message,object data)=>new {success=true,message,data};
  public static object FailedWithCode(string code,string message,object data)=>new {success=false,code,message,data};
  public static object RejectedBeforeMutation(string code,string message,string layer,string phase,bool retry)=>new {success=false,code,message};
 }
}
namespace VRCForge.Editor { public static class SceneObjectCopyCore {public static bool MatchesCurrentProject(string path)=>Path.GetFullPath(path)==Directory.GetCurrentDirectory();} }
class Probe {
 static int failures;
 static void Check(bool yes,string name){Console.WriteLine((yes?"PASS ":"FAIL ")+name);if(!yes)failures++;}
 static UnityEngine.Color32[] Original=Enumerable.Range(0,12).Select(i=>new UnityEngine.Color32(10,20,30,(byte)(100+i))).ToArray();
 static void Save(string path,int width,int height,UnityEngine.Color32[] pixels){var texture=new UnityEngine.Texture2D(width,height,UnityEngine.TextureFormat.RGBA32,false,false);texture.SetPixels32(pixels);File.WriteAllBytes(path,texture.EncodeToPNG());}
 static JObject Run(JObject args)=>JObject.FromObject(VRCForge.Editor.TexturePatchTool.HandleCommand(args));
 static JObject Args(string name,bool mask=true){var p=JObject.Parse(@"{'sourceTexturePath':'Assets/source.png','region':[0,0,4,3],'protectedRegions':[[1,0,1,1]],'red':110,'green':120,'blue':130,'alpha':255,'opacity':1,'featherPixels':0,'preview':true}");p["targetTexturePath"]="Assets/"+name+".png";if(mask)p["maskTexturePath"]="Assets/mask.png";return p;}
 static JObject Apply(JObject args,JObject preview){var p=(JObject)args.DeepClone();p["preview"]=false;foreach(var pair in (JObject)preview["data"]["applyBinding"])p[pair.Key]=pair.Value.DeepClone();return Run(p);}
 public static int Main(){
  Directory.CreateDirectory("Assets");Save("Assets/source.png",4,3,Original);var sourceBytes=File.ReadAllBytes("Assets/source.png");
  var mask=new UnityEngine.Color32[12];for(int i=0;i<mask.Length;i++)mask[i]=new UnityEngine.Color32(0,0,0,255);
  mask[0]=mask[1]=mask[6]=new UnityEngine.Color32(255,255,255,255);mask[2]=new UnityEngine.Color32(128,128,128,255);mask[4]=new UnityEngine.Color32(255,255,255,128);
  Save("Assets/mask.png",4,3,mask);var maskBytes=File.ReadAllBytes("Assets/mask.png");
  var args=Args("masked");var preview=Run(args);Console.WriteLine("PREVIEW "+preview.ToString(Newtonsoft.Json.Formatting.None));
  Check(preview.Value<bool>("success") && !File.Exists("Assets/masked.png"),"preview performs no write");
  Check((int?)preview["data"]?["changedPixelCount"]==4,"mask coverage excludes black and protected pixels");
  Check(preview["data"]?["changedBounds"]?.ToString(Newtonsoft.Json.Formatting.None)=="[0,0,3,2]","exact changed bounds");
  var applied=Apply(args,preview);Console.WriteLine("APPLIED "+applied.ToString(Newtonsoft.Json.Formatting.None));
  Check(applied.Value<bool>("success"),"masked apply succeeds");
  var output=new UnityEngine.Texture2D(2,2,UnityEngine.TextureFormat.RGBA32,false,false);output.LoadImage(File.ReadAllBytes("Assets/masked.png"),false);var pixels=output.GetPixels32();
  Check(pixels[0].r==110 && pixels[2].r==60 && pixels[4].r==60 && pixels[6].r==110,"white grayscale and alpha arithmetic");
  Check(Enumerable.Range(0,12).All(i=>new[]{0,2,4,6}.Contains(i) || pixels[i].r==10),"unselected and protected pixels unchanged");
  Check(Enumerable.Range(0,12).All(i=>pixels[i].a==Original[i].a),"source alpha unchanged");
  Check((int?)applied["data"]?["changedPixelCount"]==4 && JToken.DeepEquals(applied["data"]?["changedBounds"],preview["data"]?["changedBounds"]),"persisted exact change evidence");
  Check(sourceBytes.SequenceEqual(File.ReadAllBytes("Assets/source.png")) && maskBytes.SequenceEqual(File.ReadAllBytes("Assets/mask.png")),"source and mask remain unchanged");
  var featherArgs=Args("feather");featherArgs["featherPixels"]=2;var feather=Apply(featherArgs,Run(featherArgs));output.LoadImage(File.ReadAllBytes("Assets/feather.png"),false);var featherPixels=output.GetPixels32();
  Check(feather.Value<bool>("success") && featherPixels[0].r==60 && featherPixels[2].r==35 && featherPixels[6].r==110,"mask multiplies existing rectangle feather");
  var zeroMask=Enumerable.Repeat(new UnityEngine.Color32(0,0,0,255),12).ToArray();Save("Assets/mask.png",4,3,zeroMask);Check(!Run(Args("black")).Value<bool>("success") && !File.Exists("Assets/black.png"),"all-black mask rejects no target before mutation");
  var transparent=Enumerable.Repeat(new UnityEngine.Color32(255,255,255,0),12).ToArray();Save("Assets/mask.png",4,3,transparent);Check(!Run(Args("transparent")).Value<bool>("success") && !File.Exists("Assets/transparent.png"),"transparent white mask protects every pixel");
  File.WriteAllBytes("Assets/mask.png",maskBytes);
  var driftArgs=Args("drift");var old=Run(driftArgs);mask[0]=new UnityEngine.Color32(0,0,0,255);Save("Assets/mask.png",4,3,mask);
  Check(!Apply(driftArgs,old).Value<bool>("success") && !File.Exists("Assets/drift.png"),"changed mask rejects before mutation");
  Save("Assets/mask.png",2,2,mask.Take(4).ToArray());Check(!Run(Args("size")).Value<bool>("success"),"mask dimensions reject");
  mask[0]=new UnityEngine.Color32(255,0,0,255);Save("Assets/mask.png",4,3,mask);Check(!Run(Args("color")).Value<bool>("success"),"colored mask rejects rather than guessing");
  var plainArgs=Args("plain",false);var plainPreview=Run(plainArgs);Check((int?)plainPreview["data"]?["changedPixelCount"]==11,"unmasked behavior preserved");
  var noChangeArgs=Args("nochange",false);noChangeArgs["opacity"]=0;var samePreview=Run(noChangeArgs);var same=Apply(noChangeArgs,samePreview);
  Check(same.Value<bool>("success") && (int?)same["data"]?["changedPixelCount"]==0 && same["data"]?["changedBounds"]?.Type==JTokenType.Null,"zero pixel changes still creates new asset with null bounds");
  return failures==0?0:1;
 }
}
'''


def test_production_texture_tool_mask_and_receipts(tmp_path):
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not refs:
        pytest.skip("Local .NET SDK/reference pack required")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    fixture = tmp_path / "Probe.cs"
    fixture.write_text(STUBS, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    command = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}", f"-r:{newtonsoft}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")] + [str(SOURCE), str(fixture)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {
        "tfm": "netcoreapp3.1", "framework": {"name": "Microsoft.NETCore.App", "version": "3.1.0"}}}), encoding="utf-8")
    run = subprocess.run([dotnet, str(dll)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.count("PASS ") == 17
    for line in run.stdout.splitlines():
        if line.startswith("APPLIED "):
            payload = json.loads(line.removeprefix("APPLIED "))["data"]
            assert payload["changedPixelCount"] == 4
            assert payload["changedBounds"] == [0, 0, 3, 2]
            assert "changedPixelMaskHash" not in payload
            assert "targetFileHash" not in payload
            assert "fileHash" not in payload["readback"]


def test_public_texture_schema_exposes_optional_mask():
    from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS
    schema = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_texture_patch"]
    assert "maskTexturePath" in schema["properties"]
    assert "maskTexturePath" not in schema["required"]
    assert "grayscale" in schema["properties"]["maskTexturePath"]["description"]

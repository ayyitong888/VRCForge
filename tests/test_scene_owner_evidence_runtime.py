"""Exercise actual scene evidence reading with and without content consumers."""
from pathlib import Path
import os
import subprocess

from test_component_property_checkpoint_runtime import run
from test_curve_fx_authoring_runtime_contract import method


def test_owner_evidence_skips_scene_bytes_but_keeps_guid_and_file_checks(tmp_path):
    path = "Assets/VRCForge/Editor/SceneObjectCopyCore.cs"
    ref = os.environ.get("VRCFORGE_OWNER_EVIDENCE_GIT_REF")
    raw = subprocess.check_output(["git", "show", f"{ref}:{path}"], text=True, encoding="utf-8") if ref else Path(path).read_text(encoding="utf-8")
    production = method(raw, "internal static StableAssetEvidence ReadStableAssetEvidence(")
    program = r'''
using System;using System.IO;using System.Text;using System.Security.Cryptography;
class SceneObjectCopyException:Exception{public SceneObjectCopyException(string m):base(m){}}
class StableFileEvidence{public string Digest,Identity;}
class StableAssetEvidence{public string Guid;public StableFileEvidence File,Meta;}
class FileHandleEvidence{public long Length;}
class Probe {
 static string Root;static long EndPosition;
 static string ToAbsoluteAssetPath(string p)=>Path.Combine(Root,p);
 static void RejectProjectPathReparsePoints(string p){}
 static FileHandleEvidence ReadFileHandleEvidence(FileStream f,string p,string label){if(!p.EndsWith(".meta"))EndPosition=f.Position;return new FileHandleEvidence{Length=f.Length};}
 static bool FileHandleEvidenceMatches(FileHandleEvidence a,FileHandleEvidence b)=>a.Length==b.Length;
 static string Hex(byte[] b)=>BitConverter.ToString(b).Replace("-","").ToLowerInvariant();
 static byte[] ReadBoundedStream(Stream s,int max,string label){using(var o=new MemoryStream()){s.CopyTo(o);if(o.Length>max)throw new SceneObjectCopyException("metadata too large");return o.ToArray();}}
 static string ParseMetaGuid(string s,string l)=>s.Trim();
 static string ReadAssetGuid(string p,string l)=>"scene-guid";
 static StableFileEvidence BuildStableFileEvidence(FileHandleEvidence h,string d)=>new StableFileEvidence{Digest=d,Identity=h.Length.ToString()};
 PRODUCTION
 static void Check(bool b,string m){if(!b)throw new Exception(m);}
 static void Main(string[] args){
  Root=Path.Combine(Path.GetTempPath(),"vrcforge-owner-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(Path.Combine(Root,"Assets"));
  try{
   var scene=ToAbsoluteAssetPath("Assets/Main.unity");File.WriteAllBytes(scene,new byte[8192]);File.WriteAllText(scene+".meta","scene-guid");
   var owner=ReadStableAssetEvidence("Assets/Main.unity","owner",includeContentDigests:false);
   Check(EndPosition==0,"identity-only owner read scene contents");Check(owner.File.Digest==""&&owner.Meta.Digest=="","unused hashes produced");Check(owner.Guid=="scene-guid","GUID lost");
   var full=ReadStableAssetEvidence("Assets/Main.unity","snapshot");Check(EndPosition==8192,"snapshot did not read scene");Check(full.File.Digest.Length==64&&full.Meta.Digest.Length==64,"snapshot hashes missing");
   File.WriteAllText(scene+".meta","wrong-guid");bool rejected=false;try{ReadStableAssetEvidence("Assets/Main.unity","owner",includeContentDigests:false);}catch(SceneObjectCopyException){rejected=true;}Check(rejected,"mismatched GUID accepted");
   File.Delete(scene);rejected=false;try{ReadStableAssetEvidence("Assets/Main.unity","owner",includeContentDigests:false);}catch(SceneObjectCopyException){rejected=true;}Check(rejected,"missing scene accepted");
   Console.WriteLine("owner0bytes snapshot8192bytes guid/missing rejected");
  }finally{Directory.Delete(Root,true);}
 }
}
'''.replace("PRODUCTION", production)
    assert "owner0bytes snapshot8192bytes guid/missing rejected" in run(tmp_path, program)

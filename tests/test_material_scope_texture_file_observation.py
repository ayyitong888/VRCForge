"""Material metadata reads must not consume a texture pixel-content budget."""
from pathlib import Path

from test_curve_fx_authoring_runtime_contract import method
from test_fx_authoritative_state_preview import compile_probe


def test_texture_metadata_scope_preserves_disk_checks_without_reading_pixels(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "Assets/VRCForge/Editor/MCP/ScanScopeJournal.cs").read_text(encoding="utf-8-sig")
    selected = method(source, "private static string FileHash(")
    program = r'''
using System; using System.IO; using System.Security.Cryptography;
class Probe {
 static bool AssetPath(string path)=>path.StartsWith("Assets/");
 FILE_HASH
 static int failures;
 static void Check(bool ok,string label){Console.WriteLine((ok?"PASS ":"FAIL ")+label);if(!ok)failures++;}
 public static int Main(){
  Directory.CreateDirectory("Assets");
  var path="Assets/Texture.png";
  using(var stream=File.Create(path))stream.SetLength(1024*1024);
  long budget=256;
  var before=FileHash(path,ref budget,true);
  Check(budget==256,"metadata observation does not consume the pixel budget");
  try{FileHash(path,ref budget);Check(false,"full-content read remains bounded");}
  catch(InvalidDataException){Check(true,"full-content read remains bounded");}
  File.SetLastWriteTimeUtc(path,DateTime.UtcNow.AddMinutes(2));
  Check(FileHash(path,ref budget,true)!=before,"source disk change remains observable");
  File.WriteAllText(path+".meta","guid: before");
  long metaBudget=1024;var metaBefore=FileHash(path+".meta",ref metaBudget);
  var stamp=File.GetLastWriteTimeUtc(path+".meta");
  File.WriteAllText(path+".meta","guid: after ");File.SetLastWriteTimeUtc(path+".meta",stamp);
  Check(FileHash(path+".meta",ref metaBudget)!=metaBefore,"importer metadata content remains fingerprinted");
  File.Delete(path);
  try{FileHash(path,ref budget,true);Check(false,"missing source rejected");}
  catch(InvalidDataException){Check(true,"missing source rejected");}
  try{FileHash("../outside",ref budget,true);Check(false,"invalid source rejected");}
  catch(InvalidDataException){Check(true,"invalid source rejected");}
  return failures==0?0:1;
 }
}
'''.replace("FILE_HASH", selected)
    compile_probe(tmp_path, program, 6)

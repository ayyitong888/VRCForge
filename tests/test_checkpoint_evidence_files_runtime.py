"""Run the production evidence writer with oversized snapshots, without Unity."""
import os
from pathlib import Path

from test_material_checkpoint_safety_lane import ROOT, _run_safety_probe


def test_external_evidence_is_complete_bounded_and_owned(tmp_path):
    source_path = Path(os.environ.get("VRCFORGE_CHECKPOINT_EVIDENCE_SOURCE", ROOT / "Assets/VRCForge/Editor/CheckpointRecoveryTool.cs"))
    source = source_path.read_text(encoding="utf-8")
    start = source.index("    internal sealed class CheckpointEvidenceFiles")
    end = source.index("\n    [VRCForgeCommand(", start)
    writer = source[start:end]
    result = (ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs").read_text(encoding="utf-8")
    program = '''using System; using System.IO; using System.Text; using System.Linq;
using System.Collections.Generic; using System.Security.Cryptography; using Newtonsoft.Json; using Newtonsoft.Json.Linq;
using VRCForge.Core.MCP;
''' + result.replace("using System;", "").replace("using Newtonsoft.Json.Linq;", "") + writer + '''
class Probe {
static void Check(bool value,string name){if(!value)throw new Exception(name);}
static JObject Read(VRCForgeToolResult result){return (JObject)result.ToStructuredContent()["data"];}
static void Verify(string root,JToken reference,object[] rows){
 var path=Path.Combine(root,(string)reference["reference"]);var bytes=File.ReadAllBytes(path);
 Check(bytes.Length==(long)reference["byteCount"],"byte count");
 using(var hash=SHA256.Create())Check(BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-","").ToLowerInvariant()==(string)reference["sha256"],"digest");
 Check(JToken.DeepEquals(JArray.Parse(Encoding.UTF8.GetString(bytes)),JArray.FromObject(rows)),"full evidence");
 Check((int)reference["objectCount"]==rows.Length,"object count");
 Check(Encoding.UTF8.GetByteCount(reference["summary"].ToString(Formatting.None))<=4096,"summary bound");
 Check(bytes.Length<3||bytes[0]!=0xef||bytes[1]!=0xbb||bytes[2]!=0xbf,"UTF8 BOM");
}
static int Main(){
var root=Path.Combine(Path.GetTempPath(),"vrc-evidence-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
try{
 var large=Enumerable.Range(0,40).Select(i=>(object)new {assetPath="Assets/"+new string('界',500)+i+".shader",type=new string('類',500),dirtyCount=i,serializedState=new string('x',300000)}).ToArray();
 Check(Encoding.UTF8.GetByteCount(JArray.FromObject(large).ToString(Formatting.None))>8388608,"oversized fixture");
 using(var files=new CheckpointEvidenceFiles(root,()=>large)){
  files.RecordBefore(large);
  var baseline=new JArray(new JObject{["assetPath"]="Assets/Menu.asset",["serializedState"]=new JObject{["value"]=3}});
  var data=Read(files.Complete(VRCForgeToolResult.Completed("ok",new {assetBaseline=baseline})));
  Verify(root,data["dirtyAssetEvidenceBefore"],large);Verify(root,data["dirtyAssetEvidenceAfter"],large);
  Check(JToken.DeepEquals(data["assetBaseline"],baseline),"assetBaseline changed");
  Check(Encoding.UTF8.GetByteCount(data.ToString(Formatting.None))<16384,"oversized response");
 }
 var dir=Path.Combine(root,"Library/VRCForge/CheckpointEvidence");
 Check(Directory.GetFiles(dir).Length==2,"published evidence removed");
 using(var files=new CheckpointEvidenceFiles(root,()=>large)){files.RecordBefore(large);}
 Check(Directory.GetFiles(dir).Length==2,"unpublished evidence leak");
 using(var files=new CheckpointEvidenceFiles(root,()=>throw new IOException("capture failed"))){
  files.RecordBefore(large);var failed=files.Complete(VRCForgeToolResult.FailedWithCode("old_failure","old",new {transaction=new {state="unknown"}}));
  var data=Read(failed);Check(!failed.IsSuccessful,"capture failure hidden");Check(data["dirtyAssetEvidenceBefore"]!=null,"failure lost reference");Check(data["transaction"]!=null,"failure lost transaction");
 }
 Check(Directory.GetFiles(dir).Length==3,"failure file ownership");
 var blocked=Path.Combine(root,"blocked");Directory.CreateDirectory(blocked);File.WriteAllText(Path.Combine(blocked,"Library"),"blocked");
 bool rejected=false;try{using(var files=new CheckpointEvidenceFiles(blocked,()=>large)){files.RecordBefore(large);}}catch(IOException){rejected=true;}
 Check(rejected,"writer failure hidden");Check(Directory.GetFiles(root,"*.tmp",SearchOption.AllDirectories).Length==0,"temporary file leak");
 Console.WriteLine("PASS external evidence");return 0;
}finally{Directory.Delete(root,true);}
}}
'''
    assert "PASS external evidence" in _run_safety_probe(tmp_path, program)


def test_both_restore_phases_use_owned_evidence_wrapper():
    source_path = Path(os.environ.get("VRCFORGE_CHECKPOINT_EVIDENCE_SOURCE", ROOT / "Assets/VRCForge/Editor/CheckpointRecoveryTool.cs"))
    source = source_path.read_text(encoding="utf-8")
    reload = source[source.index("public static class CheckpointReloadTool"):]
    assert "evidence.Complete(HandleCommandWithEvidence(@params, evidence))" in reload
    assert reload.count("evidence.RecordBefore(dirtyAssetEvidenceBefore);") == 2
    assert "dirtyAssetEvidenceAfter = CheckpointPrepareTool.DirtyAssetEvidence()" not in reload

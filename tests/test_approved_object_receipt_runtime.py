"""Execute the production receipt against narrow Unity object stubs."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_approved_object_receipt_identity_transition_and_guards(tmp_path):
    production = (ROOT / "Assets/VRCForge/Core/MCP/VRCForgeApprovedObjectReceipt.cs").read_text(encoding="utf-8-sig")
    harness = r'''
using System;
using UnityEngine;
using UnityEditor;
using VRCForge.Core.MCP;
namespace UnityEngine {
 public class Object { public static bool operator ==(Object left,Object right)=>ReferenceEquals(left,right); public static bool operator !=(Object left,Object right)=>!ReferenceEquals(left,right); public override bool Equals(object other)=>ReferenceEquals(this,other); public override int GetHashCode()=>base.GetHashCode(); }
 public class Scene { public string path="Assets/Sample.unity"; public bool IsValid()=>true; }
 public class Transform { public string name="Avatar/Outfit"; public Transform parent=null; }
 public class GameObject:Object { public Scene scene=new Scene(); public Transform transform=new Transform(); public string globalId="before-unpack"; }
}
namespace UnityEditor { using UnityEngine; public static class EditorUtility { public static bool IsPersistent(GameObject value)=>false; } public struct GlobalObjectId { public string value; public static GlobalObjectId GetGlobalObjectIdSlow(GameObject value)=>new GlobalObjectId{value=value.globalId}; public override string ToString()=>value; } }
class Program {
 static string Nonce(char c)=>new string(c,64);
 static void Check(bool condition,string label){if(!condition)throw new Exception(label);}
 static void Reject(Action action,string label){try{action();}catch(InvalidOperationException){return;}throw new Exception("expected rejection: "+label);}
 static void Main(){
  var target=new GameObject(); var n=Nonce('a');
  VRCForgeApprovedObjectReceipt.Reserve(n,new[]{"vrc_unpack_prefab","vrc_setup_outfit"});
  Check(VRCForgeApprovedObjectReceipt.Bind(n,target)=="before-unpack","bind identity");
  Check(VRCForgeApprovedObjectReceipt.Consume(n,"vrc_unpack_prefab",target)=="before-unpack","consume original identity");
  target.globalId="after-unpack";
  VRCForgeApprovedObjectReceipt.AdvanceAfterVerifiedUnpack(n,target,"before-unpack","after-unpack","Assets/Sample.unity","Avatar/Outfit");
  Check(VRCForgeApprovedObjectReceipt.Consume(n,"vrc_setup_outfit",target)=="after-unpack","continue setup");
  var solo=new GameObject(); var ns=Nonce('b');
  VRCForgeApprovedObjectReceipt.Reserve(ns,new[]{"vrc_unpack_prefab"}); VRCForgeApprovedObjectReceipt.Bind(ns,solo); VRCForgeApprovedObjectReceipt.Consume(ns,"vrc_unpack_prefab",solo); solo.globalId="persisted-unpack";
  VRCForgeApprovedObjectReceipt.AdvanceAfterVerifiedUnpack(ns,solo,"before-unpack","persisted-unpack","Assets/Sample.unity","Avatar/Outfit");
  Check(VRCForgeApprovedObjectReceipt.Reserve(ns,new[]{"vrc_setup_outfit"})==1,"single-step receipt closed"); VRCForgeApprovedObjectReceipt.CancelReservation(ns);
  var ordered=new GameObject(); var no=Nonce('c'); VRCForgeApprovedObjectReceipt.Reserve(no,new[]{"vrc_unpack_prefab","vrc_setup_outfit"}); VRCForgeApprovedObjectReceipt.Bind(no,ordered); Reject(()=>VRCForgeApprovedObjectReceipt.Consume(no,"vrc_setup_outfit",ordered),"order");
  var replaced=new GameObject(); var nr=Nonce('d'); VRCForgeApprovedObjectReceipt.Reserve(nr,new[]{"vrc_unpack_prefab","vrc_setup_outfit"}); VRCForgeApprovedObjectReceipt.Bind(nr,replaced); VRCForgeApprovedObjectReceipt.Consume(nr,"vrc_unpack_prefab",replaced); replaced.globalId="after-unpack"; var substitute=new GameObject{globalId="after-unpack"}; Reject(()=>VRCForgeApprovedObjectReceipt.AdvanceAfterVerifiedUnpack(nr,substitute,"before-unpack","after-unpack","Assets/Sample.unity","Avatar/Outfit"),"replacement");
  var moved=new GameObject(); var nm=Nonce('e'); VRCForgeApprovedObjectReceipt.Reserve(nm,new[]{"vrc_unpack_prefab","vrc_setup_outfit"}); VRCForgeApprovedObjectReceipt.Bind(nm,moved); VRCForgeApprovedObjectReceipt.Consume(nm,"vrc_unpack_prefab",moved); moved.globalId="after-unpack"; moved.transform.name="Avatar/Moved"; Reject(()=>VRCForgeApprovedObjectReceipt.AdvanceAfterVerifiedUnpack(nm,moved,"before-unpack","after-unpack","Assets/Sample.unity","Avatar/Moved"),"moved");
  var wrong=new GameObject(); var nw=Nonce('f'); VRCForgeApprovedObjectReceipt.Reserve(nw,new[]{"vrc_unpack_prefab","vrc_setup_outfit"}); VRCForgeApprovedObjectReceipt.Bind(nw,wrong); VRCForgeApprovedObjectReceipt.Consume(nw,"vrc_unpack_prefab",wrong); wrong.globalId="after-unpack"; Reject(()=>VRCForgeApprovedObjectReceipt.AdvanceAfterVerifiedUnpack(Nonce('9'),wrong,"before-unpack","after-unpack","Assets/Sample.unity","Avatar/Outfit"),"nonce");
  Console.WriteLine("PASS production receipt: verified transition, solo closeout, order/object/path/nonce guards");
 }
}
'''
    assert "AdvanceAfterVerifiedUnpack" in production
    cs = tmp_path / "ReceiptProbe.cs"
    production_body = "\n".join(line for line in production.splitlines() if not line.startswith("using "))
    harness_body = "\n".join(line for line in harness.splitlines() if not line.startswith("using "))
    preamble = "#define UNITY_EDITOR\nusing System;\nusing System.Collections.Generic;\nusing System.Linq;\nusing UnityEngine;\nusing UnityEditor;\nusing VRCForge.Core.MCP;\n"
    cs.write_text(preamble + harness_body + "\n" + production_body, encoding="utf-8")
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    packs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    if not dotnet or not compilers or not packs:
        pytest.skip("Local .NET SDK and netcoreapp3.1 reference pack required")
    dll = tmp_path / "ReceiptProbe.dll"
    built = subprocess.run([dotnet, str(compilers[-1]), "-nologo", "-target:exe", "-nostdlib+", f"-out:{dll}"] + [f"-r:{ref}" for ref in packs[-1].glob("*.dll")] + [str(cs)], capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    (tmp_path / "ReceiptProbe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}), encoding="utf-8")
    run = subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "PASS production receipt" in run.stdout

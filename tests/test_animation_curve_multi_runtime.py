"""Compile actual batch preflight and curve methods; Unity persistence is not simulated."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method, STUBS

ROOT = Path(__file__).resolve().parents[1]


def test_actual_multi_clip_validation_and_owned_restore(tmp_path):
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compilers = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet = shutil.which("dotnet")
    if not dotnet or not compilers or not refs:
        pytest.skip("Installed .NET SDK/reference pack required")
    compiler = compilers[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    source = (ROOT / "Assets/VRCForge/Editor/Generic/UnityAnimationCurveBatch.cs").read_text(encoding="utf-8")
    original = (ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8-sig")
    selected = [method(source, sig) for sig in ("internal static JArray ValidateClips", "internal static JArray ValidateEnvelope", "private static void RequireEvidence", "private static bool RestorePreparedClips")]
    runner = r'''
    static int failures;
    static void Check(bool ok,string name){Console.WriteLine((ok?"PASS ":"FAIL ")+name);if(!ok)failures++;}
    static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException){return true;}}
    static JObject Row(string path) => new JObject {["clipPath"]=path,["curves"]=new JArray(new JObject {["propertyName"]="p",["constantFloat"]=0})};
    static PreparedClip Saved(string path,string before,string after){File.WriteAllText(path,before);File.WriteAllText(path+".meta","guid-"+path);var b=SceneObjectCopyCore.ReadStableAssetEvidence(path,"");var bytes=File.ReadAllBytes(path);File.WriteAllText(path,after);return new PreparedClip{Path=path,Before=b,Owned=SceneObjectCopyCore.ReadStableAssetEvidence(path,""),Bytes=bytes,Started=true};}
    public static int Main(){
      var a=new JObject {["clips"]=new JArray(Row("Assets/A.anim"),Row("Assets/B.anim"))};
      Check(ValidateClips(a).Count==2,"two clips accepted");
      ((JArray)a["clips"])[1]=Row("Assets/a.anim");Check(Reject(()=>ValidateClips(a)),"case duplicate clip rejects");
      ((JArray)a["clips"])[1]=Row("Assets/../B.anim");Check(Reject(()=>ValidateClips(a)),"traversal rejects");
      ((JArray)a["clips"])[1]=Row("Assets/B.anim");a["clipPath"]="Assets/C.anim";Check(Reject(()=>ValidateClips(a)),"mixed single rejects");a.Remove("clipPath");
      ((JObject)a["clips"][1]["curves"][0])["keys"]=new JArray();Check(Reject(()=>ValidateClips(a)),"invalid final curve preflight rejects");
      Directory.CreateDirectory("Assets");
      var one=Saved("Assets/A.anim","before-A","owned-A");var two=Saved("Assets/B.anim","before-B","owned-B");
      Check(RestorePreparedClips(new[]{one,two})&&File.ReadAllText(one.Path)=="before-A"&&File.ReadAllText(two.Path)=="before-B","later failure restores every owned saved clip");
      one=Saved("Assets/A.anim","before-A","owned-A");two=Saved("Assets/B.anim","before-B","owned-B");File.WriteAllText(two.Path,"external-B");
      Check(!RestorePreparedClips(new[]{one,two})&&File.ReadAllText(one.Path)=="before-A"&&File.ReadAllText(two.Path)=="external-B","external file preserved while other owned clip restored");
      one=Saved("Assets/A.anim","before-A","owned-A");File.WriteAllText(one.Path+".meta","external-meta");
      Check(!RestorePreparedClips(new[]{one})&&File.ReadAllText(one.Path)=="owned-A"&&File.ReadAllText(one.Path+".meta")=="external-meta","changed meta blocks overwrite");
      one=Saved("Assets/A.anim","before-A","before-A");one.Owned=null;
      Check(RestorePreparedClips(new[]{one})&&File.ReadAllText(one.Path)=="before-A","unsaved mutation reloads unchanged prestate");
      one=Saved("Assets/A.anim","before-A","partial-unknown");one.Owned=null;
      Check(!RestorePreparedClips(new[]{one})&&File.ReadAllText(one.Path)=="partial-unknown","ambiguous save never assumed owned");
      one=Saved("Assets/A.anim","before-A","before-A");one.Started=false;File.WriteAllText(one.Path,"external-unstarted");
      Check(!RestorePreparedClips(new[]{one})&&File.ReadAllText(one.Path)=="external-unstarted","unstarted external changes never restored");
      one=Saved("Assets/A.anim","before-A","owned-A");EditorUtility.UnknownMemory=true;
      Check(!RestorePreparedClips(new[]{one})&&File.ReadAllText(one.Path)=="owned-A","unknown dirty memory prevents destructive import");
      return failures==0?0:1;
    }
    '''
    stubs = r'''
using System;using System.IO;using System.Linq;using System.Text;using System.Collections.Generic;using Newtonsoft.Json;using Newtonsoft.Json.Linq;
public class AnimationClip{}
[Flags]public enum ImportAssetOptions{ForceSynchronousImport=1,ForceUpdate=2}
public static class AssetDatabase{public static void ImportAsset(string path,ImportAssetOptions flags){} public static T LoadAssetAtPath<T>(string path)where T:new()=>new T();}
public static class EditorUtility{public static bool UnknownMemory;public static bool IsDirty(object o)=>UnknownMemory;}
public class Fingerprint{public string Digest;}
public class StableAssetEvidence{public string Guid;public Fingerprint File,Meta;}
public static class SceneObjectCopyCore{
 public static string ToAbsoluteAssetPath(string p)=>Path.GetFullPath(p);
 public static StableAssetEvidence ReadStableAssetEvidence(string p,string why)=>new StableAssetEvidence{Guid=System.IO.File.ReadAllText(p+".meta"),File=new Fingerprint{Digest=Convert.ToBase64String(System.IO.File.ReadAllBytes(p))},Meta=new Fingerprint{Digest=Convert.ToBase64String(System.IO.File.ReadAllBytes(p+".meta"))}};
 public static bool StableAssetEvidenceMatches(StableAssetEvidence a,StableAssetEvidence b,bool strict)=>a.Guid==b.Guid&&a.File.Digest==b.File.Digest&&a.Meta.Digest==b.Meta.Digest;
}
public class Probe{
 private static void VerifyPreparedClip(PreparedClip e,AnimationClip a){if(EditorUtility.UnknownMemory)throw new InvalidOperationException("external dirty memory");}
 private class PreparedClip{internal string Path;internal StableAssetEvidence Before,Owned;internal byte[] Bytes;internal bool Started;}
'''
    program=stubs+"\n".join(selected)+runner+"}"
    cs=tmp_path / "Probe.cs"; cs.write_text(program, encoding="utf-8")
    dll=tmp_path / "Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]
    command += [f"-r:{p}" for p in refs[-1].glob("*.dll")]
    command += [f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30,cwd=tmp_path)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==12

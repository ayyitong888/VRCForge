"""Production Core methods with in-memory I/O substitutes; not Unity acceptance."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
ROOT = Path(__file__).resolve().parents[1]

def test_material_baseline_production_methods(tmp_path):
    dotnet = shutil.which("dotnet")
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"
    compiler = sorted((base / "sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    refs = sorted((base / "packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))[-1]
    newtonsoft = compiler.parents[2] / "Newtonsoft.Json.dll"
    source = (ROOT / "Assets/VRCForge/Editor/CheckpointRecoveryTool.cs").read_text(encoding="utf-8")
    # Extract unchanged production bodies, including validation and restore.
    start = source.index("        internal static JArray CaptureReadOnlyMaterialBaseline")
    methods = source[start:source.index("        internal static string ProjectRoot()", start)]
    program = r"""
using System; using System.Linq; using System.IO; using System.Collections.Generic;
using Newtonsoft.Json.Linq; using UnityEngine; using UnityEditor;
namespace UnityEngine { public class Object { public string Json="{}"; public bool Dirty; }
public class Material:Object {} public class ScriptableObject:Object {} }
namespace UnityEditor {
public enum ImportAssetOptions {ForceUpdate=1,ForceSynchronousImport=2}
public static class EditorJsonUtility {public static string ToJson(UnityEngine.Object o)=>o.Json;}
public static class EditorUtility {public static bool IsDirty(UnityEngine.Object o)=>o.Dirty;}
public static class AssetDatabase {
public static Dictionary<string,UnityEngine.Object> Assets=new Dictionary<string,UnityEngine.Object>();
public static int Imports;public static bool Corrupt;public static string Guid=new string('a',32);
public static UnityEngine.Object LoadMainAssetAtPath(string p)=>Assets[p];
public static string AssetPathToGUID(string p)=>Guid;
public static void ImportAsset(string p,ImportAssetOptions options){Imports++;if(Corrupt)Assets[p].Json="{\"changed\":1}";}
public static void SaveAssets(){throw new Exception("UNRELATED SAVE");}
}}
class SceneObjectCopyCore {public class FileInfoEvidence { public string Digest=new string('b',64); }
public class Evidence { public string Guid=AssetDatabase.Guid; public FileInfoEvidence File=new FileInfoEvidence(); }
public static Evidence ReadStableAssetEvidence(string path,string label)=>new Evidence();}
class Probe {
static string Root;static string ProjectRoot()=>Root;
""" + methods + r"""
static int Checks;
static void Check(bool b){if(!b)throw new Exception("assertion "+Checks);Checks++;}
static void Reject(Action action){bool bad=false;try{action();}catch(InvalidOperationException){bad=true;}Check(bad);}
static int Main(string[] args){Root=args[0];Directory.CreateDirectory(Path.Combine(Root,"Assets"));
var paths=new JArray();for(int i=0;i<128;i++){string p="Assets/M"+i+".mat";paths.Add(p);System.IO.File.WriteAllText(Path.Combine(Root,p),"material");AssetDatabase.Assets[p]=new Material();}
var baseline=CaptureReadOnlyMaterialBaseline(paths);Check(baseline.Count==128);Check(baseline[0]["materialStateDigest"].Value<string>().Length==64);
Check(RestoreAssetBaseline(baseline).Count==128);
var first=new JArray(paths[0].DeepClone());AssetDatabase.Assets[first[0].Value<string>()].Dirty=true;
Reject(()=>CaptureReadOnlyMaterialBaseline(first));AssetDatabase.Assets[first[0].Value<string>()].Dirty=false;
AssetDatabase.Corrupt=true;Reject(()=>RestoreAssetBaseline(baseline));AssetDatabase.Corrupt=false;
AssetDatabase.Guid=new string('c',32);int imports=AssetDatabase.Imports;Reject(()=>RestoreAssetBaseline(baseline));Check(AssetDatabase.Imports==imports);AssetDatabase.Guid=new string('a',32);
Reject(()=>CaptureReadOnlyMaterialBaseline(new JArray("Assets/../Bad.mat")));
Reject(()=>CaptureAssetBaseline(new JArray(Enumerable.Range(0,33).Select(i=>(object)("Assets/Menu"+i+".asset")))));
Reject(()=>RestoreAssetBaseline(new JArray(Enumerable.Range(0,33).Select(i=>(object)new JObject {["assetPath"]="Assets/Menu"+i+".asset"}))));
var invalid=(JArray)baseline.DeepClone();invalid[127]["assetPath"]="Assets/../M127.mat";imports=AssetDatabase.Imports;Reject(()=>RestoreAssetBaseline(invalid));Check(AssetDatabase.Imports==imports);
Console.WriteLine("PASS "+Checks);return 0;}}
"""
    (tmp_path / "Probe.cs").write_text(program, encoding="utf-8")
    dll = tmp_path / "Probe.dll"
    cmd = [dotnet, str(compiler), "-nologo", "-target:exe", "-nostdlib+", "-langversion:8.0", f"-out:{dll}", f"-r:{newtonsoft}"]
    cmd += [f"-r:{p}" for p in refs.glob("*.dll")]+[str(tmp_path / "Probe.cs")]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    shutil.copy2(newtonsoft, tmp_path / "Newtonsoft.Json.dll")
    (tmp_path / "Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result = subprocess.run([dotnet, str(dll), str(tmp_path)],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    assert "PASS 12" in result.stdout
    # The public handler routes this mode before any ordinary saving preparation.
    handler = source[source.index("public static object HandleCommand"):source.index("internal static JArray CaptureReadOnlyMaterialBaseline")]
    assert handler.index('if (@params?["materialBaselineOnly"]') < handler.index("AssetDatabase.SaveAssets()")

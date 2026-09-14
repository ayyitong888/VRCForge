"""Actual handler with explicit deferred-Undo test model, not live Unity proof."""
import json,os,shutil,subprocess
from pathlib import Path
from test_curve_fx_authoring_runtime_contract import method
ROOT=Path(__file__).resolve().parents[1]

def test_blendshape_flushes_before_scoped_save_and_checks_result(tmp_path):
 s=(ROOT/"Assets/VRCForge/Editor/BlendshapeApplier.cs").read_text(encoding="utf-8")
 body=method(s,"public static object HandleCommand(JObject @params)")
 receipt=method(s,"private sealed class BlendshapeChangeReceipt")
 code=r"""using System;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;
namespace UnityEngine.SceneManagement {public class Scene {public string path="Assets/Test.unity";public bool isDirty;public bool isLoaded=true;public bool IsValid()=>true;}}
class Go {public UnityEngine.SceneManagement.Scene scene=new UnityEngine.SceneManagement.Scene();}
class Mesh {public int GetBlendShapeIndex(string s)=>0;}
class Renderer {public Go gameObject=new Go();public Mesh sharedMesh=new Mesh();public float weight=100;public float GetBlendShapeWeight(int i)=>weight;public void SetBlendShapeWeight(int i,float w){weight=w;}}
class Mathf {public static float Clamp(float x,float min,float max)=>Math.Max(min,Math.Min(max,x));}
class EditorUtility {public static void SetDirty(object o){}}
class Undo {public static bool Pending;public static void RecordObject(object r,string s){Pending=true;}public static void FlushUndoRecordObjects(){if(Pending)Probe.Target.gameObject.scene.isDirty=true;Pending=false;}}
class AssetDatabase {public static int Saves;public static void SaveAssets(){Saves++;}}
class EditorSceneManager {public static bool Fail;public static int GlobalSaves;public static void MarkSceneDirty(UnityEngine.SceneManagement.Scene s){s.isDirty=true;}public static bool SaveScene(UnityEngine.SceneManagement.Scene s){if(Fail)return false;s.isDirty=false;return true;}public static bool SaveOpenScenes(){GlobalSaves++;return SaveScene(Probe.Target.gameObject.scene);}}
class VRCForgeToolResult {public bool ok;public object payload;public static object Completed(string s,object p)=>new VRCForgeToolResult{ok=true,payload=p};public static object Failed(string s)=>new VRCForgeToolResult{ok=false};}
class Probe {public static Renderer Target=new Renderer();static Renderer ResolveRenderer(string a,string r)=>Target;
"""+body+receipt+r"""
static int Main(){var request=JObject.FromObject(new {adjustments=new[]{new {rendererPath="Avatar/Coat",blendshapeName="BigBreast",targetWeight=99}}});
var result=(VRCForgeToolResult)HandleCommand(request);Undo.FlushUndoRecordObjects();if(!result.ok||Target.gameObject.scene.isDirty)throw new Exception("successful save left deferred dirty scene");
if(AssetDatabase.Saves!=0||EditorSceneManager.GlobalSaves!=0)throw new Exception("saved unrelated assets/scenes");
EditorSceneManager.Fail=true;result=(VRCForgeToolResult)HandleCommand(request);if(result.ok)throw new Exception("false save accepted");
EditorSceneManager.Fail=false;request["saveAssets"]=false;result=(VRCForgeToolResult)HandleCommand(request);if(!result.ok||JObject.FromObject(result.payload).Value<bool>("saved"))throw new Exception("unsaved path claimed saved");
Console.WriteLine("PASS flush scoped failure unsaved");return 0;}}
"""
 base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet";compiler=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))[-1];refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))[-1];newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll";dotnet=shutil.which("dotnet")
 (tmp_path/"Probe.cs").write_text(code,encoding="utf-8");dll=tmp_path/"Probe.dll"
 cmd=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+",f"-out:{dll}",f"-r:{newtonsoft}"]+[f"-r:{p}" for p in refs.glob("*.dll")]+[str(tmp_path/"Probe.cs")]
 built=subprocess.run(cmd,capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stdout+built.stderr
 shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll");(tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
 ran=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30);assert ran.returncode==0,ran.stdout+ran.stderr

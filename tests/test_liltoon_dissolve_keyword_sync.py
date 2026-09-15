"""C# runtime isolation for lilToon Multi dissolve keyword synchronization."""
import os, subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "Assets/VRCForge/Editor/ShaderMaterialAdapters.cs"

STUBS = r'''
using System; using System.Collections.Generic; using System.Linq;
namespace VRCForge.Editor { public sealed class MaterialPropertyValue { public string type; public object value; public bool writable; } }
namespace UnityEngine.Rendering { public enum ShaderPropertyType { Color,Vector,Float,Range,Texture,Int } }
namespace UnityEngine {
 public struct Vector4 {public float x,y,z,w; public float this[int i]{get=>i==0?x:i==1?y:i==2?z:w;set{if(i==0)x=value;else if(i==1)y=value;else if(i==2)z=value;else w=value;}}}
 public class LocalKeyword {public string name;public bool isValid;}
 public class LocalKeywordSpace {public bool Declared=true;public LocalKeyword FindKeyword(string n)=>new LocalKeyword{name=n,isValid=Declared&&n=="GEOM_TYPE_BRANCH_DETAIL"};}
 public class Shader {public int FindPropertyIndex(string name)=>name=="_DissolveParams"?0:-1;public UnityEngine.Rendering.ShaderPropertyType GetPropertyType(int index)=>UnityEngine.Rendering.ShaderPropertyType.Vector;public string name;public bool DeclareBranch=true;public LocalKeywordSpace keywordSpace=>new LocalKeywordSpace{Declared=DeclareBranch};}
 public class Material {public Shader shader; public Vector4 Dissolve; public HashSet<string> Keywords=new HashSet<string>(); public Dictionary<string,bool> Props=new Dictionary<string,bool>();
  public bool HasProperty(string n)=>n=="_DissolveParams"; public Vector4 GetVector(string n)=>Dissolve; public void SetVector(string n,Vector4 v){Dissolve=v;}
  public float GetFloat(string n)=>0; public void SetFloat(string n,float v){} public Color GetColor(string n)=>new Color(); public void SetColor(string n,Color v){} public bool IsKeywordEnabled(string n)=>Keywords.Contains(n); public void EnableKeyword(string n){Keywords.Add(n);} public void DisableKeyword(string n){Keywords.Remove(n);}
 }
 public static class Mathf {public static float Clamp(float v,float a,float b)=>Math.Max(a,Math.Min(b,v));public static float Round(float v)=>MathF.Round(v);public static bool Approximately(float a,float b)=>Math.Abs(a-b)<.00001f;}
 public struct Color{} public static class ColorUtility {public static string ToHtmlStringRGBA(Color c)=>"00000000";public static bool TryParseHtmlString(string s,out Color c){c=new Color();return false;}}
}
'''

RUNNER = r'''
public static class Probe {static int f;static void C(bool v,string n){System.Console.WriteLine((v?"PASS ":"FAIL ")+n);if(!v)f++;}
 static bool Apply(Material m,string p,float v){object before,after;string w;return new LilToonShaderAdapter().TryApplyChange(m,p,v,out before,out after,out w);}
 public static int Main(){var a=new LilToonShaderAdapter();var open=new Material{shader=new Shader{name="_lil/lilToonMulti"},Dissolve=new Vector4()};C(Apply(open,"dissolve_mode",3)&&open.IsKeywordEnabled("GEOM_TYPE_BRANCH_DETAIL"),"Multi mode 3 enables keyword");var close=new Material{shader=new Shader{name="_lil/lilToonMulti"},Dissolve=new Vector4()};close.EnableKeyword("GEOM_TYPE_BRANCH_DETAIL");C(Apply(close,"dissolve_mode",0)&&!close.IsKeywordEnabled("GEOM_TYPE_BRANCH_DETAIL"),"Multi mode 0 disables keyword");var normal=new Material{shader=new Shader{name="Hidden/lilToonCutout"},Dissolve=new Vector4()};normal.EnableKeyword("GEOM_TYPE_BRANCH_DETAIL");C(Apply(normal,"dissolve_mode",3)&&normal.IsKeywordEnabled("GEOM_TYPE_BRANCH_DETAIL"),"non-Multi keyword unchanged");C(Apply(open,"dissolve_border",.5f)&&open.IsKeywordEnabled("GEOM_TYPE_BRANCH_DETAIL"),"other semantic leaves keyword unchanged");var unknown=new Material{shader=new Shader{name="_lil/lilToonMulti",DeclareBranch=false},Dissolve=new Vector4()};C(!Apply(unknown,"dissolve_mode",3),"undeclared keyword fails");return f;}}
'''

def compile_run(tmp_path, source, label):
    roots = [Path(os.environ.get("DOTNET_ROOT", "__missing__")),
             Path.home() / "AppData/Local/Microsoft/dotnet",
             Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet"]
    selected = next(((r, sorted((r / "sdk").glob("8.*/Roslyn/bincore/csc.dll")),
                      sorted((r / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0")))
                     for r in roots if list((r / "sdk").glob("8.*/Roslyn/bincore/csc.dll"))
                     and list((r / "packs/Microsoft.NETCore.App.Ref").glob("8.*/ref/net8.0"))), None)
    if selected is None:
        pytest.skip("Local SDK8 compiler and net8.0 reference pack required")
    root, compilers, refs = selected
    dotnet = str(root / ("dotnet.exe" if os.name == "nt" else "dotnet"))
    compiler = compilers[-1]
    source_body = source[source.index("namespace VRCForge.Editor"):]
    cs=tmp_path/(label+".cs"); cs.write_text("using System; using System.Linq; using System.Collections.Generic; using System.Globalization; using UnityEngine; using UnityEngine.Rendering; using VRCForge.Editor;"+STUBS+source_body+RUNNER,encoding="utf-8-sig"); dll=tmp_path/(label+".dll")
    # Test-owned finite children; closed stdin, captured pipes, no listener or authentication.
    cmd=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[str(cs)]
    built=subprocess.run(cmd,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding="utf-8",timeout=60); assert built.returncode==0,built.stdout+built.stderr
    (tmp_path/(label+".runtimeconfig.json")).write_text('{"runtimeOptions":{"tfm":"net8.0","framework":{"name":"Microsoft.NETCore.App","version":"8.0.0"}}}')
    return subprocess.run([dotnet,str(dll)],cwd=tmp_path,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding="utf-8",timeout=30)

def test_actual_adapter_runtime_and_old_source_regression(tmp_path):
    current=compile_run(tmp_path,SOURCE_PATH.read_text(encoding="utf-8-sig"),"current")
    assert current.returncode==0,current.stdout+current.stderr
    assert current.stdout.count("PASS ")==5,current.stdout
    old=SOURCE_PATH.read_text(encoding="utf-8-sig").replace("            ApplySemanticSideEffects(material, semanticProperty, appliedValue, out warning);\n            return string.IsNullOrEmpty(warning);","            return true;")
    previous=compile_run(tmp_path,old,"previous")
    assert previous.returncode != 0, "old source unexpectedly passed the keyword synchronization behavior test"

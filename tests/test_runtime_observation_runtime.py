"""Execute actual production scheduling/finish methods; Unity objects are explicit test seams."""
import json,os,shutil,subprocess
from pathlib import Path
import pytest
from test_curve_fx_authoring_runtime_contract import method
ROOT=Path(__file__).resolve().parents[1]

def test_actual_schedule_and_cleanup_faults(tmp_path):
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"));refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"));dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs:pytest.skip("SDK/reference pack required")
    compiler=compilers[-1];newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    source=(ROOT/"Assets/VRCForge/Editor/RuntimeObservationTool.cs").read_text(encoding="utf-8")
    top="\n".join(method(source,s) for s in ("internal static void ValidateLimits", "internal static int DueIndex", "internal static string Digest"))
    actual="\n".join(method(source,s) for s in ("internal void Tick", "internal void Cleanup", "internal void Finish"))
    seams=r'''using System;using System.IO;using System.Linq;using System.Collections.Generic;using System.Security.Cryptography;using Newtonsoft.Json.Linq;
namespace UnityEngine {class Object {public static int Destroyed; public static void DestroyImmediate(object o){Destroyed++;}}}
class Obj {public static implicit operator bool(Obj o)=>o!=null;}
class Texture2D:Obj {public bool Fail;public byte[] EncodeToPNG(){if(Fail)throw new IOException("encode fault");return new byte[]{1,2,3};}}
class Camera:Obj {public object targetTexture;}
class Target:Obj {public void Release(){}}
class Pump:Obj {public Action Tick,Lost;}
static class EditorApplication {public static double timeSinceStartup;}
static class Time {public static int frameCount;}
'''
    body=r'''internal const int MaxFrames=32;static Job active;
    class ParameterStep {internal double TimeSeconds;internal string Name,Type;internal float Value;internal object Parameter;}
    static class GestureManagerRuntimeBridge {internal static object Param=new object();internal static bool Mismatch;internal static bool TryReadParameter(object m,string n,out object p,out float v,out string t){p=Param;v=Mismatch?2:0;t="Float";return true;} internal static void SetParameter(object m,object p,float v){} }
    class Job {internal bool Done,Started=true,FailIdentity;internal string Error="",Output;internal double Deadline,StartTime,Duration=.5;internal int LastFrame=-1,Count=6,Next,Captures;internal object Manager;internal List<ParameterStep> Steps=new List<ParameterStep>();internal int NextStep;internal JArray StepReceipts=new JArray();
internal Pump Pump=new Pump();internal Camera Camera=new Camera();internal Target Target=new Target();internal Obj CameraObject=new Obj();
internal List<Texture2D> Pixels=new List<Texture2D>();internal JArray Frames=new JArray();
internal void AssertIdentity(){if(FailIdentity)throw new Exception("identity changed");}
internal void Capture(int i,double e){Captures++;Pixels.Add(new Texture2D());Frames.Add(new JObject{["sampleIndex"]=i});}
'''
    runner=r'''
static void Check(bool value,string name){if(!value)throw new Exception(name);Console.WriteLine("PASS "+name);}
static Job Make(){return new Job{Output=Path.Combine(Path.GetTempPath(),Guid.NewGuid().ToString("N"))};}
public static int Main(){
Check(DueIndex(.31,.5,6)==3,"late frame skips missed deadlines rather than bursts");
bool rejected=false;try{ValidateLimits(.5,32,512,512);}catch(ArgumentException){rejected=true;}Check(rejected,"baseline counts against raw pixel budget");
var j=Make();active=j;Time.frameCount=1;EditorApplication.timeSinceStartup=.1;j.Tick();j.Tick();Check(j.Captures==1,"duplicate frame does not sample twice");
Time.frameCount=2;EditorApplication.timeSinceStartup=.5;j.Tick();Check(j.Done&&j.Captures==2&&active==null,"finite job ends and releases owner");
Check(j.Pixels.Count==0&&j.Pump.Tick==null&&j.Pump.Lost==null,"buffer and callback cleanup");
j=Make();j.Capture(0,0);j.Pixels[0].Fail=true;active=j;j.Finish("");Check(j.Error.Contains("encode fault")&&j.Pixels.Count==0&&active==null,"encode fault still cleans resources");
j=Make();Directory.CreateDirectory(j.Output);var user=Path.Combine(j.Output,"user.txt");File.WriteAllText(user,"user");j.Capture(0,0);j.Finish("");Check(File.ReadAllText(user)=="user"&&j.Error.Contains("another operation"),"unknown directory contents preserved");
    j=Make();j.FailIdentity=true;active=j;Time.frameCount=3;j.Tick();Check(j.Done&&j.Captures==0&&j.Error=="identity changed","identity drift aborts before sample");
    j=Make();j.Steps.Add(new ParameterStep{TimeSeconds=.1,Name="A",Value=1,Type="Float",Parameter=GestureManagerRuntimeBridge.Param});j.FailIdentity=true;active=j;Time.frameCount=4;j.Tick();Check(j.StepReceipts.Count==1&&(string)j.StepReceipts[0]["status"]=="not_applied","identity failure records remaining steps");
    j=Make();j.Steps.Add(new ParameterStep{TimeSeconds=.1,Name="A",Value=1,Type="Float",Parameter=GestureManagerRuntimeBridge.Param});GestureManagerRuntimeBridge.Mismatch=true;active=j;Time.frameCount=5;EditorApplication.timeSinceStartup=.1;j.Tick();Check(j.StepReceipts.Count==1&&(string)j.StepReceipts[0]["status"]=="failed"&&(float)j.StepReceipts[0]["afterValue"]==2,"readback mismatch preserves observed after value");
    return 0;}
'''
    program=seams+"class Probe {"+top+body+actual+"}"+runner+"}"
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    cmd=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==10

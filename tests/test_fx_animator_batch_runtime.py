"""Execute actual FX JSON planner and comparison methods, without Unity emulation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from test_curve_fx_authoring_runtime_contract import method

ROOT=Path(__file__).resolve().parents[1]


def test_actual_ordered_fx_planner(tmp_path):
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compilers=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))
    dotnet=shutil.which("dotnet")
    if not dotnet or not compilers or not refs: pytest.skip("Local .NET SDK/reference pack required")
    compiler=compilers[-1]; newtonsoft=compiler.parents[2]/"Newtonsoft.Json.dll"
    source=(ROOT/"Assets/VRCForge/Editor/Generic/UnityFxAnimatorBatch.cs").read_text(encoding="utf-8")
    selected=[method(source,sig) for sig in ("internal static JArray ValidateEnvelope","internal static JObject BuildPlan","private static IEnumerable<JObject> States","private static string Required","private static float Number","private static bool Boolean","private static string InterruptionSource","internal static JToken Projection","internal static void Verify")]
    runner=r'''
    static int failures;
    static void Check(bool v,string name){Console.WriteLine((v?"PASS ":"FAIL ")+name);if(!v)failures++;}
    static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException){return true;}}
    static JObject Original()=>JObject.Parse(@"{'parameters':[{'name':'P','type':'Int'}],'layers':[{'name':'L','defaultWeight':0,'machine':{'name':'L','defaultState':'Idle','states':[{'name':'Idle','speed':1.0,'writeDefaultValues':true,'motionPath':'Assets/Idle.anim','transitions':[],'_id':'state1','_guard':{'tag':'keep'}}],'anyStateTransitions':[],'machines':[]}}]}");
    static JArray Edits()=>JArray.Parse(@"[{'action':'ensure_state','layerName':'L','stateName':'New','motionClipPath':'Assets/New.anim'},{'action':'ensure_transition','layerName':'L','sourceStateName':'Idle','destinationStateName':'New','interruptionSource':'SourceThenDestination','conditions':[{'parameterName':'P','mode':'Equals','threshold':1}]}]");
    static HashSet<string> Motions()=>new HashSet<string>{"Assets/New.anim"};
    static JArray Edges(JObject plan)=>(JArray)plan["layers"][0]["machine"]["states"][0]["transitions"];
    public static int Main(){
        var before=Original();var edits=Edits();var plan=BuildPlan(before,edits,Motions());
        Check(((JArray)plan["layers"][0]["machine"]["states"]).Count==2 && Edges(plan).Count==1,"later edge sees newly planned state");
        Check((string)Edges(plan)[0]["interruptionSource"]=="SourceThenDestination","interruption source is planned");
        Check((bool)Edges(plan)[0]["orderedInterruption"]==true,"omitted ordered interruption preserves Unity default");
        Check(((JArray)before["layers"][0]["machine"]["states"]).Count==1 && Edges(before).Count==0,"planning leaves input state untouched");
        Check((int)plan["layers"][0]["defaultWeight"]==0,"muted existing layer remains muted");
        edits=Edits();edits.Add(edits[1].DeepClone());plan=BuildPlan(before,edits,Motions());
        Check(Edges(plan).Count==2,"ensure transition appends rather than deduplicates");
        edits.Add(JObject.Parse(@"{'action':'delete_transition','layerName':'L','sourceStateName':'Idle','destinationStateName':'New','transitionIndex':0}"));
        edits.Add(edits.Last.DeepClone());plan=BuildPlan(before,edits,Motions());
        Check(Edges(plan).Count==0,"delete indexes are interpreted sequentially");
        edits=Edits();edits.Add(JObject.Parse(@"{'action':'delete_transition','layerName':'L','sourceStateName':'Idle','destinationStateName':'Idle','transitionIndex':0}"));
        Check(Reject(()=>BuildPlan(before,edits,Motions())),"wrong delete destination rejects");
        edits=Edits();edits[0]["action"]="update_state";
        Check(Reject(()=>BuildPlan(before,edits,Motions())),"update missing state rejects");
        Check(Reject(()=>BuildPlan(before,Edits(),new HashSet<string>())),"missing motion rejects");
        edits=Edits();edits[1]["orderedInterruption"]=false;plan=BuildPlan(before,edits,Motions());
        Check((bool)Edges(plan)[0]["orderedInterruption"]==false,"explicit false is planned");
        edits=Edits();edits[1]["orderedInterruption"]=JValue.CreateString("false");
        Check(Reject(()=>BuildPlan(before,edits,Motions())),"nonboolean ordered interruption rejects");
        var duplicate=Original();((JArray)duplicate["layers"][0]["machine"]["states"]).Add(duplicate["layers"][0]["machine"]["states"][0].DeepClone());
        Check(Reject(()=>BuildPlan(duplicate,Edits(),Motions())),"ambiguous state names reject");
        edits=Edits();edits[1]["conditions"][0]["parameterName"]="Missing";
        Check(Reject(()=>BuildPlan(before,edits,Motions())),"missing parameter rejects");
        edits=Edits();edits[1]["conditions"][0]["mode"]="If";
        Check(Reject(()=>BuildPlan(before,edits,Motions())),"wrong parameter mode rejects");
        edits=Edits();edits[1]["duration"]=-1;
        Check(Reject(()=>BuildPlan(before,edits,Motions())),"invalid final edit rejects before caller mutation");
        var expectedWithInterruption=BuildPlan(before,Edits(),Motions());
        var interruptionMismatch=(JObject)expectedWithInterruption.DeepClone();
        ((JArray)interruptionMismatch["layers"][0]["machine"]["states"][0]["transitions"])[0]["interruptionSource"]="None";
        Check(Reject(()=>Verify(expectedWithInterruption,interruptionMismatch)),"interruption source participates in verify");
        var orderedMismatch=(JObject)expectedWithInterruption.DeepClone();
        ((JArray)orderedMismatch["layers"][0]["machine"]["states"][0]["transitions"])[0]["orderedInterruption"]=false;
        Check(Reject(()=>Verify(expectedWithInterruption,orderedMismatch)),"ordered interruption participates in verify");
        var actual=Original();actual["layers"][0]["machine"]["states"][0]["_guard"]["tag"]="changed";
        Check(Reject(()=>Verify(before,actual)),"untouched existing guard changes reject");
        actual=Original();actual["layers"][0]["defaultWeight"]=1;
        Check(Reject(()=>Verify(before,actual)),"unrequested layer changes reject");
        Verify(before,Original());Check(true,"matching independent snapshot verifies");
        var envelope=new JObject{["controllerPath"]="Assets/FX.controller",["edits"]=new JArray()};
        for(int i=0;i<128;i++)((JArray)envelope["edits"]).Add(new JObject());
        Check(ValidateEnvelope(envelope).Count==128,"128 edits accepted");
        ((JArray)envelope["edits"]).Add(new JObject());Check(Reject(()=>ValidateEnvelope(envelope)),"129 edits reject");
        envelope["edits"]=Edits();envelope["action"]="single";Check(Reject(()=>ValidateEnvelope(envelope)),"single and batch fields reject");
        envelope.Remove("action");envelope["controllerPath"]=new string('x',512*1024);Check(Reject(()=>ValidateEnvelope(envelope)),"512 KiB limit enforced");
        return failures==0?0:1;
    }
    '''
    program="using System;using System.Linq;using System.Text;using System.Collections.Generic;using Newtonsoft.Json;using Newtonsoft.Json.Linq; public class Probe {"+"\n".join(selected)+runner+"}"
    cs=tmp_path/"Probe.cs";cs.write_text(program,encoding="utf-8");dll=tmp_path/"Probe.dll"
    command=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+","-langversion:8.0",f"-out:{dll}"]+[f"-r:{p}" for p in refs[-1].glob("*.dll")]+[f"-r:{newtonsoft}",str(cs)]
    compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    shutil.copy2(newtonsoft,tmp_path/"Newtonsoft.Json.dll")
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count("PASS ")==25

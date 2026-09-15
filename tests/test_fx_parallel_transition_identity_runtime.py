"""Compile actual transition selector/mutator with Unity doubles; no Unity/project writes."""
from pathlib import Path
import sys,json,subprocess,shutil,os
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_curve_fx_authoring_runtime_contract import method

def test_parallel_transition_identity(tmp_path):
    root=Path(__file__).resolve().parents[1]; ref=os.environ.get('VRCFORGE_ANIMATION_EDGE_GIT_REF'); src=subprocess.check_output(['git','show',ref+':Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs'],cwd=root,text=True) if ref else (root/'Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs').read_text(encoding='utf-8-sig'); fx=src[src.index('public static class ManageFxAnimatorTool'):]
    selected='\n'.join(method(fx,x) for x in ['private static void ValidateTransitionPreview','private static void DeleteTransition','private static string Required','private static AnimatorControllerLayer FindLayer'])
    program=r'''
    using System;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;
    class AnimatorState {public string name;public AnimatorStateTransition[] transitions=new AnimatorStateTransition[0];public void RemoveTransition(AnimatorStateTransition t){transitions=transitions.Where(x=>x!=t).ToArray();}}
    class AnimatorStateTransition {public AnimatorState destinationState;}
    class AnimatorStateMachine {public AnimatorState[] states;public AnimatorStateTransition[] anyStateTransitions;public void RemoveAnyStateTransition(AnimatorStateTransition t){anyStateTransitions=anyStateTransitions.Where(x=>x!=t).ToArray();}}
    class AnimatorControllerLayer {public string name;public AnimatorStateMachine stateMachine;}
    class AnimatorControllerParameter {public string name;}
    class AnimatorController {public AnimatorControllerLayer[] layers;public AnimatorControllerParameter[] parameters=new AnimatorControllerParameter[0];}
    static class AvatarPrimitiveCrudCore {public static AnimatorState FindState(AnimatorStateMachine m,string n)=>m.states.SingleOrDefault(s=>s.name==n);}
    class ConditionSpec {public string parameter;}
    class Probe {
    static List<ConditionSpec> ReadConditions(JObject p)=>new List<ConditionSpec>();
    static string NormalizeAction(string a)=>a;
    SELECTED
    static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException e){return e.Message.Contains("ambiguous");}}
    public static void Main(){foreach(var directed in new[]{false,true}){
     var target=new AnimatorState{name="Target"};var source=new AnimatorState{name="Source"};var first=new AnimatorStateTransition{destinationState=target};var second=new AnimatorStateTransition{destinationState=target};source.transitions=new[]{first,second};var machine=new AnimatorStateMachine{states=new[]{source,target},anyStateTransitions=new[]{first,second}};var controller=new AnimatorController{layers=new[]{new AnimatorControllerLayer{name="FX",stateMachine=machine}}};var args=JObject.Parse(@"{'action':'delete_transition','layerName':'FX','destinationStateName':'Target'}");if(directed)args["sourceStateName"]="Source";
     if(!Reject(()=>ValidateTransitionPreview(controller,args)))throw new Exception("preview accepted ambiguous edges");
     if(!Reject(()=>DeleteTransition(controller,args)))throw new Exception("apply accepted ambiguous edges");
     if((directed?source.transitions:machine.anyStateTransitions).Length!=2)throw new Exception("rejection mutated edges");
     args["transitionIndex"]=1;ValidateTransitionPreview(controller,args);DeleteTransition(controller,args);
     var remaining=directed?source.transitions:machine.anyStateTransitions;if(remaining.Length!=1||!ReferenceEquals(remaining[0],first))throw new Exception("explicit index changed wrong edge");
     args.Remove("transitionIndex");ValidateTransitionPreview(controller,args);DeleteTransition(controller,args);if((directed?source.transitions:machine.anyStateTransitions).Length!=0)throw new Exception("unique edge not removed");
     Console.WriteLine("PASS "+(directed?"directed":"anyState")+" preview/apply ambiguity refusal, no mutation, explicit index and unique target");}}
    
    }
    '''.replace('SELECTED',selected)
    out=tmp_path/'csharp';out.mkdir(exist_ok=True);cs=out/'Probe.cs';cs.write_text(program)
    base=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'dotnet';compiler=sorted((base/'sdk').glob('*/Roslyn/bincore/csc.dll'))[-1];refs=sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))[-1];newton=compiler.parents[2]/'Newtonsoft.Json.dll';dll=out/'Probe.dll';cmd=['dotnet',str(compiler),'-nologo','-target:exe','-nostdlib+','-langversion:8.0',f'-out:{dll}']+[f'-r:{p}' for p in refs.glob('*.dll')]+[f'-r:{newton}',str(cs)];p=subprocess.run(cmd,capture_output=True,text=True);print(p.stdout,p.stderr);assert p.returncode==0
    shutil.copy2(newton,out/'Newtonsoft.Json.dll');(out/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'netcoreapp3.1','framework':{'name':'Microsoft.NETCore.App','version':'3.1.0'}}}));p=subprocess.run(['dotnet',str(dll)],capture_output=True,text=True);print(p.stdout,p.stderr);assert p.returncode==0

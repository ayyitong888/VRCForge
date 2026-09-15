"""Production wardrobe-specific layer/state identity regression; no Unity writes."""
from pathlib import Path
import sys,os,json,subprocess
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_wardrobe_scanner_runtime import STUBS,_method

def test_wardrobe_layer_state_identity(tmp_path):
    root=Path(__file__).resolve().parents[1];ref=os.environ.get('VRCFORGE_WARDROBE_SELECTION_GIT_REF');s=subprocess.check_output(['git','show',ref+':Assets/VRCForge/Editor/WardrobeOutfitWriter.cs'],cwd=root,text=True) if ref else (root/'Assets/VRCForge/Editor/WardrobeOutfitWriter.cs').read_text(encoding='utf-8-sig')
    selected='\n'.join(_method(s,x) for x in ['private static int FindWardrobeLayerIndex','private static bool LayerHasEquals','private static void CollectStatesByName'])
    program=STUBS+'class AnimatorController {public AnimatorControllerLayer[] layers;} class AnimatorControllerLayer {public AnimatorStateMachine stateMachine;} class Probe {'+selected+r'''
    static bool Reject(Action a){try{a();return false;}catch(InvalidOperationException){return true;}}
    public static void Main(){var first=new AnimatorState{name="Same"};var second=new AnimatorState{name="Same"};var m1=new AnimatorStateMachine{states=new[]{new ChildAnimatorState{state=first}},anyStateTransitions=new[]{new AnimatorStateTransition{destinationState=first,conditions=new[]{new AnimatorCondition{mode=AnimatorConditionMode.Equals,parameter="Clothes",threshold=1}}}}};var m2=new AnimatorStateMachine{states=new[]{new ChildAnimatorState{state=second}},anyStateTransitions=m1.anyStateTransitions};var controller=new AnimatorController{layers=new[]{new AnimatorControllerLayer{stateMachine=m1},new AnimatorControllerLayer{stateMachine=m2}}};if(!Reject(()=>FindWardrobeLayerIndex(controller,"Clothes")))throw new Exception("ambiguous wardrobe layer accepted");controller.layers=new[]{controller.layers[1]};if(FindWardrobeLayerIndex(controller,"Clothes")!=0||FindWardrobeLayerIndex(controller,"Other")!=-1)throw new Exception("unique/missing layer regressed");m1.stateMachines=new[]{new ChildAnimatorStateMachine{stateMachine=m2}};var states=new Dictionary<string,AnimatorState>();if(!Reject(()=>CollectStatesByName(m1,states)))throw new Exception("duplicate state names accepted");second.name="Unique";states.Clear();CollectStatesByName(m1,states);if(states.Count!=2||states["Unique"]!=second)throw new Exception("unique nested state regressed");CollectStatesByName(m1,states);if(states.Count!=2)throw new Exception("same instance revisit regressed");Console.WriteLine("PASS ambiguous layer/state rejected; unique/missing layer, unique nested state, same instance revisit preserved");}}
    
    '''
    p=tmp_path/'selection';p.mkdir(exist_ok=True);cs=p/'Probe.cs';cs.write_text(program);base=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'dotnet';c=sorted((base/'sdk').glob('*/Roslyn/bincore/csc.dll'))[-1];refs=sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))[-1];dll=p/'Probe.dll';r=subprocess.run(['dotnet',str(c),'-nologo','-target:exe','-nostdlib+',f'-out:{dll}']+[f'-r:{x}' for x in refs.glob('*.dll')]+[str(cs)],capture_output=True,text=True);print(r.stdout,r.stderr);assert r.returncode==0
    (p/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'netcoreapp3.1','framework':{'name':'Microsoft.NETCore.App','version':'3.1.0'}}}));r=subprocess.run(['dotnet',str(dll)],capture_output=True,text=True);print(r.stdout,r.stderr);assert r.returncode==0


def test_scanner_uses_strict_avatar_identity(tmp_path, monkeypatch):
    import test_wardrobe_avatar_identity_runtime as h
    monkeypatch.setitem(h.WRITERS, 'part', 'Assets/VRCForge/Editor/WardrobeScanner.cs')
    result = h._run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr

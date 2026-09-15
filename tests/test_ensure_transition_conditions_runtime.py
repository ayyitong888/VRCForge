"""Execute the production ensure helper with in-memory Animator API doubles."""
from pathlib import Path
import subprocess

from test_parameter_writer_runtime import _extract_method


def test_ensure_transition_requires_the_complete_condition_set(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    source = (root / "Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs").read_text(encoding="utf-8-sig")
    helper = _extract_method(source, "private static bool EnsureAnyStateTransition")
    program = r'''
using System; using System.Linq;
enum AnimatorConditionMode { Equals, Greater }
class AnimatorState {}
class AnimatorCondition { public string parameter; public AnimatorConditionMode mode; public float threshold; }
class AnimatorStateTransition {
 public AnimatorState destinationState; public AnimatorCondition[] conditions;
 public bool hasExitTime,canTransitionToSelf; public float exitTime,duration;
 public void AddCondition(AnimatorConditionMode m,float t,string p){conditions=new[]{new AnimatorCondition{mode=m,threshold=t,parameter=p}};}
}
class AnimatorStateMachine {
 public AnimatorStateTransition[] anyStateTransitions=Array.Empty<AnimatorStateTransition>();
 public AnimatorStateTransition AddAnyStateTransition(AnimatorState state){var t=new AnimatorStateTransition{destinationState=state};anyStateTransitions=anyStateTransitions.Concat(new[]{t}).ToArray();return t;}
}
static class Mathf { public static bool Approximately(float a,float b)=>Math.Abs(a-b)<0.000001f; }
class Probe {
 HELPER
 static int failures;
 static void Check(bool value,string label){Console.WriteLine((value?"PASS ":"FAIL ")+label);if(!value)failures++;}
 static AnimatorCondition Condition(string p,float value)=>new AnimatorCondition{parameter=p,mode=AnimatorConditionMode.Equals,threshold=value};
 static bool Ensure(AnimatorStateMachine machine,AnimatorState state)=>EnsureAnyStateTransition(machine,state,"Wardrobe",AnimatorConditionMode.Equals,2);
 static int Main(){
  var state=new AnimatorState();var guarded=new AnimatorStateTransition{destinationState=state,conditions=new[]{Condition("Wardrobe",2),Condition("Guard",1)}};
  var machine=new AnimatorStateMachine{anyStateTransitions=new[]{guarded}};
  Check(Ensure(machine,state),"additional guard does not satisfy single-condition request");
  Check(machine.anyStateTransitions.Length==2&&guarded.conditions.Length==2,"existing guarded transition preserved");
  Check(!Ensure(machine,state)&&machine.anyStateTransitions.Length==2,"exact new transition reused on repetition");
  var exact=new AnimatorStateMachine{anyStateTransitions=new[]{new AnimatorStateTransition{destinationState=state,conditions=new[]{Condition("Wardrobe",2)}}}};
  Check(!Ensure(exact,state),"existing exact condition reused");
  var wrong=new AnimatorStateMachine{anyStateTransitions=new[]{new AnimatorStateTransition{destinationState=state,conditions=new[]{Condition("Wardrobe",3)}}}};
  Check(Ensure(wrong,state),"different threshold remains distinct");
  return failures==0?0:1;
 }
}
'''.replace("HELPER", helper)
    (tmp_path / "Program.cs").write_text(program, encoding="utf-8")
    project = tmp_path / "probe.csproj"
    project.write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup></Project>', encoding="utf-8")
    result = subprocess.run(["dotnet", "run", "--project", str(project), "--nologo"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

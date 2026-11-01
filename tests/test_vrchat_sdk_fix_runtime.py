from pathlib import Path

from test_component_property_checkpoint_runtime import run


ROOT = Path(__file__).resolve().parents[1]


def test_sdk_native_fix_adapter_resolves_once_and_replays_sdk_button_order(tmp_path):
    adapter = (ROOT / "Assets/VRCForge/Editor/NativeSdkFixActions.cs").read_text(encoding="utf-8-sig")
    adapter = "\n".join(line for line in adapter.splitlines() if not line.startswith("using ") and line not in ("#if !VRC_CLIENT", "#endif"))
    stubs = r'''
namespace UnityEngine { public class Object {} }
namespace UnityEditor { public static class EditorUtility { public static void SetDirty(UnityEngine.Object o){VRCForge.Editor.Probe.Events.Add("dirty");} } }
namespace UnityEditor.SceneManagement { public static class EditorSceneManager { public static void MarkSceneDirty(UnityEngine.SceneManagement.Scene s){VRCForge.Editor.Probe.Events.Add("scene");} } }
namespace UnityEngine.SceneManagement { public struct Scene {} public static class SceneManager { public static Scene GetActiveScene(){return new Scene();} } }
namespace VRCForge.Editor {

 internal sealed class FakePanel { public void RunValidations(){Probe.Events.Add("refresh");} }
 internal static class VrchatSdkBuilderAlertsTool {
 internal sealed class NativeFixBinding { internal Action FixAction; internal UnityEngine.Object Subject; internal FakePanel Panel; }
  internal static int Resolves; internal static bool RowCurrent=true; internal static FakePanel Panel=new FakePanel();
  internal static NativeFixBinding ResolveNativeFixBinding(JObject p){Resolves++;Probe.Events.Add("resolve"+Resolves);if((string)p["source"]!="sdk"||(int)p["index"]!=4||(string)p["message"]!="current issue"||!RowCurrent)throw new InvalidOperationException("row changed");return new NativeFixBinding{FixAction=()=>Probe.Events.Add("fix"),Subject=new UnityEngine.Object(),Panel=Panel};}
  internal static MethodInfo FindInstanceMethod(Type t,string n)=>t.GetMethod(n,BindingFlags.Instance|BindingFlags.Public|BindingFlags.NonPublic);
 }
 public static class Probe {
  public static readonly List<string> Events=new List<string>();
  static void Check(bool ok,string m){if(!ok)throw new Exception(m);}
  public static void Main(){
   var selector=new JObject{{"source","sdk"},{"index",4},{"message","current issue"}};
   var action=NativeSdkFixActions.Resolve(selector);
   Check(VrchatSdkBuilderAlertsTool.Resolves==1,"selector is checked before dispatch");
   action();
   Check(VrchatSdkBuilderAlertsTool.Resolves==1,"selected callback is reused at immediate invocation");
   Check(string.Join(",",Events)=="resolve1,fix,scene,dirty,refresh","SDK Auto Fix post-action order");
   Events.Clear(); VrchatSdkBuilderAlertsTool.RowCurrent=false;
   try{NativeSdkFixActions.Resolve(selector);throw new Exception("stale selector invoked");}catch(InvalidOperationException){}
   Check(!Events.Contains("fix")&&!Events.Contains("scene")&&!Events.Contains("dirty"),"stale row has no side effects");
   Console.WriteLine("PASS SDK native Auto Fix adapter");
  }
 }
}
'''
    usings = "using System;\nusing System.Collections.Generic;\nusing System.Reflection;\nusing Newtonsoft.Json.Linq;\nusing UnityEditor;\nusing UnityEditor.SceneManagement;\nusing UnityEngine;\nusing UnityEngine.SceneManagement;\n"
    assert "PASS SDK native Auto Fix adapter" in run(tmp_path, usings + stubs + adapter)

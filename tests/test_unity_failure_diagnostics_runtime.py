"""Replay production C# failure factories, preserving state and original causes."""
from pathlib import Path
from test_component_property_checkpoint_runtime import run
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def test_shared_failure_factories_keep_original_exception_and_rollback_facts(tmp_path):
    contract = (ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs").read_text(encoding="utf-8")
    contract = contract.replace("using System;", "").replace("using Newtonsoft.Json.Linq;", "")
    scene = method((ROOT / "Assets/VRCForge/Editor/SceneObjectCopyCore.cs").read_text(encoding="utf-8"), "internal static object Failure(Exception exception)")
    feature = method((ROOT / "Assets/VRCForge/Editor/ComponentFeatureWriteCore.cs").read_text(encoding="utf-8"), "internal static object Failure(Exception exception)")
    curve = method((ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs").read_text(encoding="utf-8"), "internal static VRCForgeToolResult EditFailure(")
    program = r'''
using System;using System.Reflection;using Newtonsoft.Json.Linq;using VRCForge.Core.MCP;
CONTRACT
class SceneObjectCopyException:Exception{public SceneObjectCopyException(string s):base(s){}}
class ComponentFeatureWriteException:Exception{public ComponentFeatureWriteException(string s):base(s){}}
class SceneProbe {SCENE}
class FeatureProbe {FEATURE}
class CurveProbe {public class AssetEditRecovery{public bool restored;public bool Restore()=>restored;} CURVE}
class Probe {
 static void Check(bool b,string s){if(!b)throw new Exception(s);}
 static void ThrowLeaf(){throw new InvalidOperationException("Serialized reference points to a missing material.");}
 static JObject Json(object v)=>((VRCForgeToolResult)v).ToStructuredContent();
 static void Diagnostics(JObject j){
  var d=j["data"]?["diagnostics"];
  Check(d!=null,"original exception diagnostics were discarded");
  Check(d.ToString().Contains("Serialized reference points to a missing material."),"original reason missing");
  Check(d.ToString().Contains("InvalidOperationException"),"original type missing");
  Check(d.ToString().Contains("TargetInvocationException"),"reflection wrapper missing");
  Check(d.ToString().Contains("ThrowLeaf"),"failing callsite missing");
 }
 static void Main(){
  Exception failure;try{ThrowLeaf();throw new Exception("unreachable");}catch(Exception e){failure=new TargetInvocationException("Reflection invocation failed.",e);}
  var scene=Json(SceneProbe.Failure(failure));Diagnostics(scene);Check(scene["success"].Value<bool>()==false,"scene failure status");
  Check(scene["data"]?["mutationStarted"]==null,"unknown scene mutation became false");
  var feature=Json(FeatureProbe.Failure(failure));Diagnostics(feature);
  var rejected=Json(CurveProbe.EditFailure("curve_validation",failure,false,"binding_validation",null));Diagnostics(rejected);
  Check((string)rejected["code"]=="curve_validation"&&(string)rejected["data"]["failurePhase"]=="binding_validation", "precise code and phase changed");
  Check(rejected["data"]["mutationStarted"].Value<bool>()==false&&(string)rejected["data"]["commitState"]=="not_started","read-only rejection facts changed");
  foreach(bool restored in new[]{false,true}){
   var curve=Json(CurveProbe.EditFailure("curve_apply",failure,true,"persist",new CurveProbe.AssetEditRecovery{restored=restored}));Diagnostics(curve);
   Check(curve["data"]["mutationStarted"].Value<bool>()==true,"write mutation lost");
   Check((string)curve["data"]["commitState"]==(restored?"rolled_back":"unknown"),"rollback facts changed");
   Check(curve["data"]["checkpointRecoveryRequired"].Value<bool>()==!restored,"recovery instruction changed");
  }
  var controlled=Json(SceneProbe.Failure(new SceneObjectCopyException("Choose one saved scene.")));
  Check(controlled["data"]==null&&(string)controlled["error"]=="Choose one saved scene.","clear parameter rejection was inflated");
  Console.WriteLine("original exceptions and existing mutation/rollback facts preserved");
 }
}
'''.replace("CONTRACT",contract).replace("SCENE",scene).replace("FEATURE",feature).replace("CURVE",curve)
    assert "original exceptions and existing mutation/rollback facts preserved" in run(tmp_path, program)


def test_post_mutation_and_scene_save_factories_keep_cause_and_recovery(tmp_path):
    def read(name):
        return (ROOT / "Assets/VRCForge/Editor" / name).read_text(encoding="utf-8")
    contract = (ROOT / "Assets/VRCForge/Core/MCP/VRCForgeToolResult.cs").read_text(encoding="utf-8")
    contract = contract.replace("using System;", "").replace("using Newtonsoft.Json.Linq;", "")
    scene = method(read("SceneObjectCopyCore.cs"), "internal static object BuildMutationFailure(")
    feature = method(read("ComponentFeatureWriteCore.cs"), "internal static object BuildMutationFailure(")
    constraint = method(read("ConstraintSourceTool.cs"), "private static object BuildMutationFailure(")
    save = read("SaveNewSceneTool.cs")
    first_save = method(save, "private static object MutationFailure(")
    current_save = method(save[save.index("public static class SaveCurrentSceneTool"):], "private static object MutationFailure(")
    bitpack = read("ParameterBitPackingTool.cs")
    before_preview = bitpack[:bitpack.index("private static object BuildPreviewPayload(")]
    bitpack_catch = method(before_preview[before_preview.rindex("catch (Exception exception)"):], "catch (Exception exception)")
    program = r'''
using System;using Newtonsoft.Json.Linq;using VRCForge.Core.MCP;
CONTRACT
class SceneProbe {const string ResultSchema="copy",DuplicateOperation="duplicate_scene_object",PrefabOperation="prefab";SCENE}
class FeatureProbe {const string ResultSchema="feature";FEATURE}
class ConstraintProbe {const string ResultSchema="constraint";CONSTRAINT public static object Run(bool restored,Exception e)=>BuildMutationFailure(restored,e);}
class SaveProbe {const string ResultSchema="save",Operation="new_scene";SAVE public static object Run(bool started,Exception e)=>MutationFailure("scene_save_failed","Save failed.",started,e);}
class CurrentProbe {const string ResultSchema="current",Operation="current_scene";CURRENT public static object Run(bool started,Exception e)=>MutationFailure("current_scene_save_failed","Save failed.",started,e);}
class Probe {
 static JObject Json(object value)=>((VRCForgeToolResult)value).ToStructuredContent();
 static void Check(bool value,string message){if(!value)throw new Exception(message);}
 static object Bitpack(Exception failure){var operationStage="clone_asset_staging";try{throw failure;}BITPACK}
 static void Main(){
  var failure=new InvalidOperationException("Destination folder disappeared while saving.");
  foreach(bool restored in new[]{false,true}){
   foreach(var result in new[]{SceneProbe.BuildMutationFailure("duplicate_scene_object",restored,failure),FeatureProbe.BuildMutationFailure(restored,failure),ConstraintProbe.Run(restored,failure)}){
    var json=Json(result);var data=json["data"];
    Check(data["diagnostics"].ToString().Contains(failure.Message),"post-mutation cause lost");
    Check(data["mutationStarted"].Value<bool>()&&data["restored"].Value<bool>()==restored,"original mutation facts changed");
    Check(data["checkpointRestoreRequired"].Value<bool>()==!restored,"checkpoint recovery fact changed");
    Check(((string)data["nextAction"]).Contains(restored?"Read back":"checkpoint"),"next action disagrees with recovery");
   }
  }
  foreach(bool started in new[]{false,true}){
   var saved=Json(SaveProbe.Run(started,failure));var current=Json(CurrentProbe.Run(started,failure));
   foreach(var json in new[]{saved,current}){
    Check(json["data"]["diagnostics"].ToString().Contains(failure.Message),"save failure reason lost");
    Check((string)json["error"]=="Save failed. "+failure.Message,"save returns only opaque error code");
    Check((string)json["data"]["commitState"]==(started?"unknown":"not_started"),"unknown save became safe retry");
   }
   Check(saved["data"]["checkpointRestoreRequired"].Value<bool>()==started,"new scene recovery changed");
   Check(current["data"]["manualRecoveryRequired"].Value<bool>()==started&&current["data"]["checkpointRestoreRequired"].Value<bool>()==false,"current save recovery changed");
  }
  var bit=Json(Bitpack(failure));Check(bit["data"]["diagnostics"].ToString().Contains(failure.Message),"bitpack cause lost");
  Check((string)bit["data"]["failureStage"]=="clone_asset_staging","bitpack stage lost");
  Console.WriteLine("post-mutation, save and packing failure causes retained without changing recovery");
 }
}
'''.replace("CONTRACT",contract).replace("SCENE",scene).replace("FEATURE",feature).replace("CONSTRAINT",constraint).replace("SAVE",first_save).replace("CURRENT",current_save).replace("BITPACK",bitpack_catch)
    assert "post-mutation, save and packing failure causes retained without changing recovery" in run(tmp_path, program)

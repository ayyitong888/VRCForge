"""Actual Apply body + actual object-rename action; deferred Undo model, no Unity."""
import sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_curve_fx_authoring_runtime_contract import method
from test_constraint_conversion_scope_runtime import run
s=(ROOT/'Assets/VRCForge/Editor/AtomicReferenceRenameTool.cs').read_text(encoding='utf-8')
body=method(s,'private static object Apply(RenameSnapshot snapshot, ref bool mutationStarted)')
start=s.index('Undo.RecordObject(target, "Rename VRCForge avatar object");')
action=s[start:s.index('});',start)].strip()
code='''using System;using System.Linq;using System.Collections.Generic;
class AtomicReferenceRenameException:Exception{public AtomicReferenceRenameException(string s):base(s){}}
class Target {public string name="Old";}
class Scene {public bool isDirty;}
class Saved {public Scene Scene=new Scene();public int Handle=1;public string FileIdentity="file",MetaIdentity="meta";}
class Request {public string ScenePath="Assets/Test.unity",NewName="New";public Request Reverse()=>this;}
class Ref {public Action Apply;}
class Asset {public string AssetPath="Assets/Test.unity";}
class RenameSnapshot {public Request Request=new Request();public string PlanDigest="digest";public Saved Scene=new Saved();public List<Asset> Assets=new List<Asset>{new Asset()};public List<Ref> References=new List<Ref>();}
class Undo {public static bool Pending;public static void RecordObject(object o,string s){Pending=true;}public static void IncrementCurrentGroup(){}public static int GetCurrentGroup()=>1;public static void SetCurrentGroupName(string s){}public static void CollapseUndoOperations(int i){}public static void FlushUndoRecordObjects(){if(Pending)Probe.snapshot.Scene.Scene.isDirty=true;Pending=false;}}
class EditorUtility {public static void SetDirty(object o){}}
class EditorSceneManager {public static object GetSceneManagerSetup()=>null;public static void MarkSceneDirty(Scene s){s.isDirty=true;}public static bool SaveScene(Scene s){s.isDirty=false;return true;}}
class SceneObjectCopyCore {public static Saved ResolveSavedScene(string p,string label)=>Probe.snapshot.Scene;}
class VRCForgeToolResult {public bool ok;public static object Completed(string s,object p)=>new VRCForgeToolResult{ok=true};}
class Probe {public static RenameSnapshot snapshot=new RenameSnapshot();static RenameSnapshot BuildPreview(Request r)=>snapshot;static object CaptureBackups(object a)=>null;static void RequireNoDirtyProjectAssets(){}static void SavePlannedAssets(RenameSnapshot s){}static void VerifyReverseReadback(RenameSnapshot a,RenameSnapshot b){}static void VerifySavedEvidence(RenameSnapshot a,RenameSnapshot b,Saved c){}static object BuildApplyPayload(RenameSnapshot a,RenameSnapshot b,Saved c)=>null;static bool RestoreFailedApply(RenameSnapshot s,object a,object b,int i)=>false;static object Failure(Exception e,bool m,bool r)=>new VRCForgeToolResult();
'''+body+'''
static int Main(){var target=new Target();var request=snapshot.Request;snapshot.References.Add(new Ref{Apply=()=>{'''+action+'''}});bool changed=false;
var result=(VRCForgeToolResult)Apply(snapshot,ref changed);Undo.FlushUndoRecordObjects();
if(!result.ok||!snapshot.Scene.Scene.isDirty)throw new Exception("deferred Undo save or recovery contract failed");return 0;}}
'''

def test_actual_object_rename_apply_flushes_before_save(tmp_path):
    current = code.replace('if(!result.ok||!snapshot.Scene.Scene.isDirty)', 'if(!result.ok||snapshot.Scene.Scene.isDirty)')
    run(tmp_path,current)


def test_actual_object_rename_failure_flushes_before_recovery(tmp_path):
    current = code.replace('static void SavePlannedAssets(RenameSnapshot s){}', 'static void SavePlannedAssets(RenameSnapshot s){throw new Exception("injected failure");}')
    current = current.replace('static bool RestoreFailedApply(RenameSnapshot s,object a,object b,int i)=>false;', 'static bool RestoreFailedApply(RenameSnapshot s,object a,object b,int i)=>!Undo.Pending;')
    current = current.replace('static object Failure(Exception e,bool m,bool r)=>new VRCForgeToolResult();', 'static object Failure(Exception e,bool m,bool r)=>new VRCForgeToolResult{ok=r};')
    current = current.replace('Undo.FlushUndoRecordObjects();\nif(!result.ok||!snapshot.Scene.Scene.isDirty)', 'if(!result.ok)')
    run(tmp_path,current)

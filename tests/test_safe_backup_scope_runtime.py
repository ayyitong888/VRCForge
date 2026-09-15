"""Run the production backup asset selector with controlled Unity discovery."""
from pathlib import Path
from test_curve_fx_authoring_runtime_contract import method
from test_constraint_conversion_scope_runtime import run


def test_invalid_restore_subset_never_becomes_restore_all(tmp_path):
    source = (Path(__file__).resolve().parents[1] /
              "Assets/VRCForge/Editor/PrefabTools.cs").read_text(encoding="utf-8-sig")
    bodies = "\n".join(method(source, signature) for signature in (
        "private static string NormalizeRequestedAssetPath",
        "private static bool TryNormalizeManifestRelativePath",
        "private static bool ShouldRestore"))
    run(tmp_path, r'''
using System; using System.IO; using System.Linq; using System.Collections.Generic;
class Probe {
''' + bodies + r'''
static int Main(){
foreach(var requested in new[]{"../Wrong.asset","Assets/../Wrong.asset","/Assets/Wrong.asset"}){
bool rejected=false;
try {
var subset=new HashSet<string>(new[]{requested}.Select(NormalizeRequestedAssetPath).Where(p=>!string.IsNullOrWhiteSpace(p)),StringComparer.OrdinalIgnoreCase);
if(ShouldRestore("Assets/Unrelated.asset",subset))throw new Exception("Invalid subset expanded to restore all: "+requested);
}catch(InvalidOperationException){rejected=true;}
if(!rejected)throw new Exception("Invalid subset was silently ignored");
}
var valid=new HashSet<string>{NormalizeRequestedAssetPath("Assets/Requested.asset")};
if(!ShouldRestore("Assets/Requested.asset.meta",valid)||ShouldRestore("Assets/Unrelated.asset",valid))throw new Exception("Valid subset changed");
if(!ShouldRestore("Assets/Unrelated.asset",new HashSet<string>()))throw new Exception("Explicit all default changed");
return 0;
}}
''')


def test_explicit_backup_assets_do_not_include_editor_selection(tmp_path):
    source = (Path(__file__).resolve().parents[1] /
              "Assets/VRCForge/Editor/ConsoleTools.cs").read_text(encoding="utf-8-sig")
    bodies = "\n".join(method(source, signature) for signature in (
        "private static List<string> ResolveRequestedAssetPaths",
        "private static void AddAssetPath(", "private static string NormalizeAssetPath"))
    run(tmp_path, r'''
using System; using System.Linq; using System.Collections.Generic;
namespace UnityEngine { public class Object { public string path; } }
class Selection { public static UnityEngine.Object[] objects = {new UnityEngine.Object {path="Assets/Unrelated.mat"}}; }
class AssetDatabase { public static string GetAssetPath(UnityEngine.Object o) => o.path; }
class Transform {public UnityEngine.Object gameObject;}
class PrefabUtility {public static UnityEngine.Object GetCorrespondingObjectFromSource(UnityEngine.Object o)=>o;}
class Scene {public string path; public bool isDirty; public bool IsValid()=>true;}
class SceneManager {public static int sceneCount=0; public static Scene GetSceneAt(int i)=>new Scene();}
class CreateSafeBackupParameters {public List<string> assetPaths=new List<string>(); public string avatarPath=""; public bool? includeOpenScenes=false;}
class Probe {
static Transform ResolveAvatarRoot(string s)=>throw new Exception("unused");
''' + bodies + r'''
static int Main(){
var explicitPaths=ResolveRequestedAssetPaths(new CreateSafeBackupParameters {assetPaths=new List<string>{"Assets/Requested.mat"}},new List<string>());
if(!explicitPaths.SequenceEqual(new[]{"Assets/Requested.mat"}))throw new Exception("Explicit backup included unrelated selection: "+string.Join(",",explicitPaths));
var fallback=ResolveRequestedAssetPaths(new CreateSafeBackupParameters(),new List<string>());
if(!fallback.SequenceEqual(new[]{"Assets/Unrelated.mat"}))throw new Exception("Selection fallback regressed");
return 0;
}}
''')

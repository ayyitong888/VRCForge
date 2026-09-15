"""Execute exact production scene-save/readback segments against Unity persistence ports.

The scene/asset storage ports are modeled; no real Editor or project assets are
modified. Set VRCFORGE_SCENE_SAVE_SCOPE_GIT_REF to replay a pre-fix revision.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _source(name):
    path = f"Assets/VRCForge/Editor/{name}.cs"
    revision = os.environ.get("VRCFORGE_SCENE_SAVE_SCOPE_GIT_REF")
    if revision:
        return subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True, encoding="utf-8")
    return (ROOT / path).read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("kind", ["new", "save", "save_as"])
def test_scene_save_retains_unrelated_dirty_asset_and_checks_readback(tmp_path, kind):
    if kind == "new":
        source = _source("SaveNewSceneTool")
        begin = source.index("if (!EditorSceneManager.SaveScene(")
        end = source.index('return VRCForgeToolResult.Completed(\n                    "Saved the active unsaved scene', begin)
        setup = 'var snapshot = new Snapshot();'
    else:
        source = _source("Stage1SceneTools")
        begin = source.index("mutationStarted = true;", source.index('if (action == "save" && !scene.isDirty)'))
        end = source.index('return VRCForgeToolResult.Completed("Scene save committed', begin)
        setup = 'var scene = new Scene { path="Assets/Existing.unity" }; string action="'+kind+'", scenePath=scene.path, destination="Assets/Copy.unity";'
    segment = source[begin:end]
    program = r'''
using System;
class Scene {public string path=""; public bool isDirty=true,isLoaded=true;public int handle=7;public bool IsValid(){return true;}}
class Snapshot {public Scene Scene=new Scene();public string ScenePath="Assets/New.unity",SceneHierarchyDigest="original";public int SceneHandle=7;}
class SceneObjectCopyException:Exception {public SceneObjectCopyException(string s):base(s){}}
[Flags] enum ImportAssetOptions {ForceSynchronousImport=1,ForceUpdate=2}
static class SceneObjectCopyCore {
 public static bool AssetOrMetaExists(string p){return false;}
 public static object ReadStableAssetEvidence(string p,string reason){Probe.evidenceReads++;if(p!=Probe.savedPath)throw new Exception("wrong persisted scene");return new object();}
}
static class EditorSceneManager {public static bool SaveScene(Scene s,string p,bool copy){s.path=p;s.isDirty=false;Probe.saved=s;Probe.savedPath=p;Probe.sceneWrites++;return true;}}
static class SceneManager {public static Scene GetSceneByPath(string p){Probe.sceneReads++;if(p!=Probe.savedPath)throw new Exception("wrong loaded scene");return Probe.saved;}}
static class AssetDatabase {
 public static void SaveAssets(){Probe.globalSaves++;if(Probe.unrelatedDirty){Probe.unrelatedDiskValue=Probe.unrelatedMemoryValue;Probe.unrelatedDirty=false;}}
 public static void ImportAsset(string p,ImportAssetOptions o){if(p!=Probe.savedPath)throw new Exception("wrong import");Probe.imports++;}
 public static void Refresh(ImportAssetOptions o){Probe.imports++;}
}
static class Stage1SceneToolCore {public static string HierarchyDigest(Scene s){return Probe.badReadback?"changed":"original";}}
class Probe {
 public static int globalSaves,sceneWrites,unrelatedDiskValue,unrelatedMemoryValue,imports,evidenceReads,sceneReads;
 public static bool unrelatedDirty,badReadback;public static Scene saved;public static string savedPath;
 static string ComputeSceneHierarchyDigest(Scene s){return Stage1SceneToolCore.HierarchyDigest(s);}
 static object MutationFailure(string code,string message,bool mutated){throw new SceneObjectCopyException(code);}
 static object Save(){bool mutationStarted=false;string hierarchy="original";
 //SETUP
 //SEGMENT
 return null;}
 static void Run(bool corrupt){globalSaves=sceneWrites=imports=evidenceReads=sceneReads=0;unrelatedDiskValue=1;unrelatedMemoryValue=2;unrelatedDirty=true;badReadback=corrupt;bool rejected=false;
 try{Save();}catch(SceneObjectCopyException){rejected=true;}
 if(globalSaves!=0||unrelatedDiskValue!=1||!unrelatedDirty)throw new Exception("unrelated dirty asset persisted");
 if(sceneWrites!=1||imports!=1||evidenceReads!=1||sceneReads!=1||saved.isDirty)throw new Exception("scene save/import/readback regressed");
 if(rejected!=corrupt)throw new Exception("scene hierarchy readback guard regressed");
 Console.WriteLine("PASS scoped scene save; unrelated asset retained; corruptReadback="+corrupt);}
 static void Main(){Run(false);Run(true);}
}
'''.replace('//SETUP', setup).replace('//SEGMENT', segment)
    dotnet = shutil.which('dotnet')
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'dotnet'
    compilers = sorted((base/'sdk').glob('*/Roslyn/bincore/csc.dll'))
    packs = sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))
    if not dotnet or not compilers or not packs:
        pytest.skip('Local .NET SDK and netcoreapp3.1 reference pack required')
    cs=tmp_path/'Probe.cs';cs.write_text(program, encoding='utf-8');dll=tmp_path/'Probe.dll'
    built=subprocess.run([dotnet,str(compilers[-1]),'-nologo','-target:exe','-nostdlib+',f'-out:{dll}']+[f'-r:{x}' for x in packs[-1].glob('*.dll')]+[str(cs)],capture_output=True,text=True,timeout=60)
    assert built.returncode==0,built.stdout+built.stderr
    (tmp_path/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'netcoreapp3.1','framework':{'name':'Microsoft.NETCore.App','version':'3.1.0'}}}))
    run=subprocess.run([dotnet,str(dll)],capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    assert run.stdout.count('PASS')==2

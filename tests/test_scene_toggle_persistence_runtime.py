"""Production toggle handler with explicit Unity persistence doubles, not live Unity."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


def test_public_toggle_preserves_verified_core_receipt(monkeypatch):
    import dashboard_server as server
    payload = {'objectPath': 'Avatar/Hat', 'active': True, 'saved': True,
               'verified': True, 'persistedReadback': True,
               'readback': {'persisted': True, 'data': {'active': True}}}
    monkeypatch.setattr(server, 'load_dashboard_settings', lambda *_: object())
    monkeypatch.setattr(server, 'toggle_scene_object_direct', lambda *_: payload)
    monkeypatch.setattr(server, 'emit_log', lambda *_: None)
    result = server.toggle_scene_object_sync({'objectPath': 'Avatar/Hat', 'active': True})
    assert result['verified'] is True
    assert result['readback'] == payload['readback']
    assert result['result'] == payload


def test_toggle_uses_scoped_verified_save_and_preserves_unsaved_mode(tmp_path):
    source = (ROOT / 'Assets/VRCForge/Editor/AvatarControlScanner.cs').read_text(encoding='utf-8')
    source = source[source.index('public static class SceneObjectToggler'):]
    body = method(source, 'public static object HandleCommand(JObject @params)')
    helpers = method(source, 'private static string GetTransformPath') + method(source, 'private static string NormalizePath')
    code = r'''using System;using System.Linq;using System.Collections.Generic;using Newtonsoft.Json.Linq;
class Scene {public bool isLoaded=true; public bool IsValid()=>true;}
class GameObject {public Scene scene=new Scene(); public bool activeSelf;public Transform transform;public void SetActive(bool a){activeSelf=a;}}
class Transform {public string name="Hat";public Transform parent;public GameObject gameObject=new GameObject();public Transform(){gameObject.transform=this;}}
class Resources {public static T[] FindObjectsOfTypeAll<T>()=>new[]{(T)(object)Probe.Target};}
class EditorUtility {public static bool IsPersistent(object o)=>false;public static void SetDirty(object o){}}
class Undo {public static void RecordObject(object o,string s){} public static void FlushUndoRecordObjects(){}}
class EditorSceneManager {public static void MarkSceneDirty(Scene s){} public static bool SaveOpenScenes(){Probe.GlobalSaves++;return true;}}
class AssetDatabase {public static void SaveAssets(){Probe.GlobalSaves++;}}
class ComponentCrudCore {public static GameObject ResolveGameObject(string s)=>Probe.Target.gameObject;}
class VRCForgeToolResult {public bool IsSuccessful;public object Payload;public static VRCForgeToolResult Completed(string s,object p=null)=>new VRCForgeToolResult{IsSuccessful=true,Payload=p};public static VRCForgeToolResult Failed(string s,object p=null)=>new VRCForgeToolResult{IsSuccessful=false,Payload=p};}
class SetGameObjectActiveTool {public static object HandleCommand(JObject p){Probe.ScopedSaves++;if(Probe.Fail)return VRCForgeToolResult.Failed("save failed");var old=Probe.Target.gameObject.activeSelf;Probe.Target.gameObject.SetActive(p.Value<bool>("active"));return VRCForgeToolResult.Completed("saved",new{gameObjectPath="Hat",oldActive=old,newActive=Probe.Target.gameObject.activeSelf,sceneSaved=true,persistedReadback=true,scenePath="Assets/Main.unity",sceneFileDigestAfter="digest"});}}
class Probe {public static Transform Target=new Transform();public static int GlobalSaves,ScopedSaves;public static bool Fail;
''' + body + helpers + r'''
static int Main(){var request=JObject.FromObject(new{objectPath="Hat",active=true});
var r=(VRCForgeToolResult)HandleCommand(request);var p=JObject.FromObject(r.Payload);
if(!r.IsSuccessful||GlobalSaves!=0||ScopedSaves!=1||p.Value<bool?>("persistedReadback")!=true||p.Value<bool?>("verified")!=true)throw new Exception("saved toggle lacks scoped persistence proof");
Fail=true;r=(VRCForgeToolResult)HandleCommand(request);if(r.IsSuccessful)throw new Exception("save failure reported success");
Fail=false;request["saveAssets"]=false;request["active"]=false;var prior=ScopedSaves;r=(VRCForgeToolResult)HandleCommand(request);p=JObject.FromObject(r.Payload);
if(!r.IsSuccessful||ScopedSaves!=prior||p.Value<bool>("saved")||!p.Value<bool>("pending")||Target.gameObject.activeSelf)throw new Exception("unsaved path changed contract");
request.Remove("active");r=(VRCForgeToolResult)HandleCommand(request);if(r.IsSuccessful)throw new Exception("missing active mutated target");
Console.WriteLine("PASS toggle scoped failure pending required");return 0;}}
'''
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'dotnet'
    compiler = sorted((base / 'sdk').glob('*/Roslyn/bincore/csc.dll'))[-1]
    refs = sorted((base / 'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))[-1]
    newtonsoft = compiler.parents[2] / 'Newtonsoft.Json.dll'
    dotnet = shutil.which('dotnet')
    (tmp_path / 'Probe.cs').write_text(code, encoding='utf-8')
    dll = tmp_path / 'Probe.dll'
    command = [dotnet, str(compiler), '-nologo', '-target:exe', '-nostdlib+', f'-out:{dll}', f'-r:{newtonsoft}'] + [f'-r:{p}' for p in refs.glob('*.dll')] + [str(tmp_path / 'Probe.cs')]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    shutil.copy2(newtonsoft, tmp_path / 'Newtonsoft.Json.dll')
    (tmp_path / 'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {'tfm': 'netcoreapp3.1', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '3.1.0'}}}), encoding='utf-8')
    ran = subprocess.run([dotnet, str(dll)], capture_output=True, text=True, timeout=30)
    assert ran.returncode == 0, ran.stdout + ran.stderr

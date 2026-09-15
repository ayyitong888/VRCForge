"""Production catch code with explicit Unity persistence/identity doubles."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scenario', ['saved', 'save_failed', 'readback_failed', 'parent_drift'])
def test_prepare_failure_keeps_recovery_until_saved_rollback(tmp_path, scenario):
    source = (ROOT / 'Assets/VRCForge/Editor/UserAdjustmentHandoffTool.cs').read_text(encoding='utf-8')
    prepare = method(source, 'private static object Prepare(Snapshot s)')
    catch = prepare[prepare.rindex('            catch'):]
    catch = catch[:catch.rfind('}')].strip()
    code = r'''using System;using System.IO;using System.Collections.Generic;
class Transform {public Transform parent;public int localPosition,localRotation,localScale;public void SetParent(Transform p,bool keep){parent=p;}}
class GameObject {public Transform transform=new Transform();public object scene=new object();}
class Snapshot {public GameObject Target;public string TargetId="target";}
namespace UnityEngine {class Object {public static void DestroyImmediate(GameObject g){Probe.ProxyInMemory=false;}}}
class EditorSceneManager {public static void MarkSceneDirty(object s){} public static bool SaveScene(object s){Probe.SaveCalls++;if(Probe.Scenario=="save_failed")return false;Probe.DiskContainsProxy=Probe.ProxyInMemory;return true;}}
class Probe {public static bool ProxyInMemory=true,DiskContainsProxy=true;public static int SaveCalls;public static string Scenario;static string Root;static GameObject Target;
static string StatePath(string id)=>Path.Combine(Root,id+".json");
static int ReadVector(int value,string key)=>value;static int ReadQuaternion(int value,string key)=>value;
static GameObject Resolve(string id,string label){if(Scenario=="readback_failed")throw new Exception("readback failed");return Target;}
static void Main(string[] args){Root=args[0];Scenario=args[1];var id="owned";var proxy=new GameObject();var originalParent=new Transform();var target=new Transform{parent=proxy.transform};Target=new GameObject{transform=target};var s=new Snapshot{Target=Target};var state=new Dictionary<string,int>{{"localPosition",1},{"localRotation",2},{"localScale",3}};
if(Scenario=="parent_drift")target.parent=new Transform();
File.WriteAllText(StatePath(id),"owned recovery record");
try{try{throw new Exception("late failure after successful prepare save");}
''' + catch + r'''
}catch(Exception){}
if(Scenario=="saved"){
if(DiskContainsProxy || ProxyInMemory || File.Exists(StatePath(id)) || SaveCalls!=1 || target.parent!=originalParent || target.localPosition!=1 || target.localRotation!=2 || target.localScale!=3)throw new Exception("rollback was not saved and verified before state deletion");
}else if(!File.Exists(StatePath(id)))throw new Exception("failed rollback deleted recovery state");
if(Scenario=="parent_drift" && (!ProxyInMemory || SaveCalls!=0))throw new Exception("drifted hierarchy was mutated");
}}
'''
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'dotnet'
    compiler = sorted((base / 'sdk').glob('*/Roslyn/bincore/csc.dll'))[-1]
    refs = sorted((base / 'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))[-1]
    dotnet = shutil.which('dotnet')
    (tmp_path / 'Probe.cs').write_text(code, encoding='utf-8')
    dll = tmp_path / 'Probe.dll'
    built = subprocess.run([dotnet, str(compiler), '-nologo', '-target:exe', '-nostdlib+', f'-out:{dll}'] + [f'-r:{p}' for p in refs.glob('*.dll')] + [str(tmp_path / 'Probe.cs')], capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    (tmp_path / 'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {'tfm': 'netcoreapp3.1', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '3.1.0'}}}), encoding='utf-8')
    ran = subprocess.run([dotnet, str(dll), str(tmp_path), scenario], capture_output=True, text=True, timeout=30)
    assert ran.returncode == 0, ran.stdout + ran.stderr

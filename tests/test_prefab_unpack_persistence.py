"""Unpack must finish its own scene save and verify the persisted target."""
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'Assets/VRCForge/Editor/Generic/UnityAssetPrefabCrud.cs'


def unpack_source():
    return Path(os.environ.get('VRCFORGE_UNPACK_TEST_SOURCE',str(SOURCE))).read_text(encoding='utf-8-sig').split('public static class UnpackPrefabTool',1)[1]


def test_unpack_saves_and_resolves_before_verified_receipt():
    source=unpack_source()
    steps=['ComponentCrudCore.ResolveSavedSceneFor(go)', 'PrefabUtility.UnpackPrefabInstance', 'ComponentCrudCore.SaveAndResolveScene(beforeScene)', 'SceneObjectCopyCore.ResolveUniqueGameObject', 'IsPersistedUnpackMatch(', 'verified = true']
    positions=[source.index(step) for step in steps]
    assert positions==sorted(positions)
    assert 'pending = true' not in source
    assert 'pending = false' in source
    assert 'AssetDatabase.SaveAssets' not in source
    assert 'AssetDatabase.Refresh' not in source
    assert source.index('mutationStarted = true') < source.index('PrefabUtility.UnpackPrefabInstance')


def test_production_persisted_unpack_identity_predicate(tmp_path):
    source=unpack_source();start=source.index('        private static bool IsPersistedUnpackMatch(')
    end=source.index('\n        }',start)+len('\n        }')
    program='using System; class Probe {\n'+source[start:end]+r'''
static void Check(bool value){if(!value)throw new Exception("Unpack persistence predicate mismatch");}
static int Main(){
Check(IsPersistedUnpackMatch("Avatar/Mask","id","Avatar/Mask","id","",false));
Check(!IsPersistedUnpackMatch("Avatar/Mask","id","Other","id","",false));
Check(!IsPersistedUnpackMatch("Avatar/Mask","id","Avatar/Mask","changed","",false));
Check(!IsPersistedUnpackMatch("Avatar/Mask","id","Avatar/Mask","id","Assets/Mask.prefab",false));
Check(!IsPersistedUnpackMatch("Avatar/Mask","id","Avatar/Mask","id","",true));
Console.WriteLine("PASS unpack identity");return 0;}}
'''
    base=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'dotnet'
    compiler=sorted((base/'sdk').glob('*/Roslyn/bincore/csc.dll'))[-1]
    refs=sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))[-1]
    cs=tmp_path/'Probe.cs';cs.write_text(program);dll=tmp_path/'Probe.dll'
    command=[shutil.which('dotnet'),str(compiler),'-nologo','-target:exe','-nostdlib+','-langversion:8.0',f'-out:{dll}']+[f'-r:{p}' for p in refs.glob('*.dll')]+[str(cs)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    (tmp_path/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'netcoreapp3.1','framework':{'name':'Microsoft.NETCore.App','version':'3.1.0'}}}))
    result=subprocess.run([shutil.which('dotnet'),str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS unpack identity' in result.stdout

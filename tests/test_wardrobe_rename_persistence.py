"""Production rename readback predicate and scoped persistence contract; not live Unity proof."""
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Assets/VRCForge/Editor/WardrobeManagerWriter.cs'


def source():
    return Path(os.environ.get('VRCFORGE_RENAME_TEST_SOURCE', str(SOURCE))).read_text(encoding='utf-8-sig')


def test_rename_has_scoped_save_and_persisted_identity_readback():
    text = source()
    start = text.index('        private static JObject RenameOutfitVerified(')
    method = text[start:text.index('        private static JObject RenameControlEvidence(', start)]
    assert 'AssetDatabase.SaveAssets()' not in method
    assert 'AssetDatabase.Refresh()' not in method
    ordered = ['RenameOutfit(context', 'Undo.FlushUndoRecordObjects()', 'AssetDatabase.SaveAssetIfDirty', 'AssetDatabase.ImportAsset', 'AssetDatabase.LoadAllAssetsAtPath', 'AssertRenameReadback(expected, after)']
    positions = [method.index(value) for value in ordered]
    assert positions == sorted(positions)
    assert 'matches.Count != 1' in method
    assert 'EditorUtility.IsDirty(matches[0])' in method
    assert 'TryGetGUIDAndLocalFileIdentifier' in method
    assert 'controlCount' in method
    handler = text[:text.index('        private static void ApplyAction(')]
    branch = handler.index('var evidence = RenameOutfitVerified(')
    assert branch < handler.index('AssetDatabase.SaveAssets()')
    assert handler.index('return VRCForgeToolResult.Completed', branch) < handler.index('AssetDatabase.SaveAssets()')


def test_production_readback_rejects_missing_or_changed_target(tmp_path):
    text = source()
    start = text.index('        private static void AssertRenameReadback(')
    end = text.index('\n        }', start) + len('\n        }')
    method = text[start:end]
    program = 'using System; using Newtonsoft.Json.Linq; class Probe {\n' + method + r'''
static void Reject(JArray expected,JArray actual) {
    try { AssertRenameReadback(expected,actual); }
    catch(InvalidOperationException) { return; }
    throw new Exception("Readback accepted a mismatch");
}
static int Main() {
    var expected=JArray.Parse("[{ 'path':'Assets/Menu.asset','guid':'menu','localId':1,'kind':'menu','index':0,'controlCount':1,'control':{'name':'New','type':'Toggle','parameter':'Wardrobe','value':1}}, {'path':'Assets/FX.controller','guid':'fx','localId':2,'kind':'state','name':'New'}]");
    AssertRenameReadback(expected,(JArray)expected.DeepClone());
    Reject(expected,new JArray()); Reject(new JArray(),new JArray());
    foreach(var key in new[]{"name","type","parameter","value"}) {
        var actual=(JArray)expected.DeepClone(); actual[0]["control"][key]="wrong"; Reject(expected,actual);
    }
    foreach(var key in new[]{"guid","localId","index","controlCount"}) {
        var actual=(JArray)expected.DeepClone(); actual[0][key]="wrong"; Reject(expected,actual);
    }
    var state=(JArray)expected.DeepClone();state[1]["name"]="Old";Reject(expected,state);
    var missing=(JArray)expected.DeepClone();missing.RemoveAt(1);Reject(expected,missing);
    Console.WriteLine("PASS persisted rename checks"); return 0;
}}
'''
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'dotnet'
    compiler = sorted((base/'sdk').glob('*/Roslyn/bincore/csc.dll'))[-1]
    refs = sorted((base/'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))[-1]
    newtonsoft = compiler.parents[2]/'Newtonsoft.Json.dll'
    cs=tmp_path/'Probe.cs';cs.write_text(program,encoding='utf-8');dll=tmp_path/'Probe.dll'
    cmd=[shutil.which('dotnet'),str(compiler),'-nologo','-target:exe','-nostdlib+','-langversion:8.0',f'-out:{dll}',f'-r:{newtonsoft}']
    cmd += [f'-r:{p}' for p in refs.glob('*.dll')]+[str(cs)]
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    shutil.copy2(newtonsoft,tmp_path/'Newtonsoft.Json.dll')
    (tmp_path/'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions':{'tfm':'netcoreapp3.1','framework':{'name':'Microsoft.NETCore.App','version':'3.1.0'}}}))
    result=subprocess.run([shutil.which('dotnet'),str(dll)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS persisted rename checks' in result.stdout

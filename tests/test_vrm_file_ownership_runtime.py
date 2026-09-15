"""Run production C# file helpers against real files; no Unity export claim."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scenario', ['commit_collision', 'recovery_collision', 'recovery_missing_output', 'partial_collision', 'replacement', 'failed_commit'])
def test_vrm_preserves_unowned_files(tmp_path, scenario):
    source = (ROOT / 'Assets/VRCForge/Editor/VrmExporter.cs').read_text(encoding='utf-8')
    helpers = '\n'.join(method(source, signature) for signature in (
        'private static void CommitValidatedOutput',
        'private static void RecoverInterruptedReplacement',
        'private static void DeleteTemporaryFile',
        'private static void WriteTemporaryFile',
    ))
    code = 'using System;using System.IO;class Probe{' + helpers + r'''
static void Main(string[] args){
var output=Path.Combine(args[0],"output.vrm");var temp=output+".partial";var backup=output+".replace-backup";
File.WriteAllText(output,"original");File.WriteAllText(temp,"candidate");
if(args[1]=="partial_collision"){
bool created=false;bool rejected=false;try{WriteTemporaryFile(temp,new byte[]{1},ref created);}catch(IOException){rejected=true;}
if(!rejected || created || File.ReadAllText(temp)!="candidate")throw new Exception("unowned partial was overwritten");
}else if(args[1]=="commit_collision" || args[1]=="recovery_collision" || args[1]=="recovery_missing_output"){
bool missing=args[1]=="recovery_missing_output";if(missing)File.Delete(output);
File.WriteAllText(backup,"unowned");bool rejected=false;
try{if(args[1]=="commit_collision")CommitValidatedOutput(temp,output,backup,true);else RecoverInterruptedReplacement(output,backup);}
catch(IOException){rejected=true;}catch(InvalidOperationException){rejected=true;}
if(!rejected || !File.Exists(backup) || File.ReadAllText(backup)!="unowned" || (missing ? File.Exists(output) : File.ReadAllText(output)!="original") || File.ReadAllText(temp)!="candidate")throw new Exception("unowned collision was not preserved");
}else if(args[1]=="replacement"){
CommitValidatedOutput(temp,output,backup,true);
if(File.ReadAllText(output)!="candidate" || File.Exists(temp) || File.Exists(backup))throw new Exception("replacement failed");
}else{
File.Delete(temp);bool rejected=false;try{CommitValidatedOutput(temp,output,backup,true);}catch(IOException){rejected=true;}
if(!rejected || File.ReadAllText(output)!="original" || File.Exists(backup))throw new Exception("failed commit did not restore output");
}
}}
'''
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'dotnet'
    compiler = sorted((base / 'sdk').glob('*/Roslyn/bincore/csc.dll'))[-1]
    refs = sorted((base / 'packs/Microsoft.NETCore.App.Ref').glob('*/ref/netcoreapp3.1'))[-1]
    dotnet = shutil.which('dotnet')
    (tmp_path / 'Probe.cs').write_text(code, encoding='utf-8')
    dll = tmp_path / 'Probe.dll'
    command = [dotnet, str(compiler), '-nologo', '-target:exe', '-nostdlib+', f'-out:{dll}'] + [f'-r:{p}' for p in refs.glob('*.dll')] + [str(tmp_path / 'Probe.cs')]
    built = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    (tmp_path / 'Probe.runtimeconfig.json').write_text(json.dumps({'runtimeOptions': {'tfm': 'netcoreapp3.1', 'framework': {'name': 'Microsoft.NETCore.App', 'version': '3.1.0'}}}), encoding='utf-8')
    ran = subprocess.run([dotnet, str(dll), str(tmp_path), scenario], capture_output=True, text=True, timeout=30)
    assert ran.returncode == 0, ran.stdout + ran.stderr

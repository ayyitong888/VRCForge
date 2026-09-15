"""Actual post-SDK and file-commit branches; no Unity build/upload is executed."""
from pathlib import Path
import pytest
from test_constraint_conversion_scope_runtime import run
from test_curve_fx_authoring_runtime_contract import method

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('success_event', [False, True])
def test_upload_requires_sdk_success_event_not_existing_pipeline_id(tmp_path, success_event):
    source=(ROOT/'Assets/VRCForge/Editor/VrchatBuildTestTool.cs').read_text(encoding='utf-8')
    start=source.index('job.TaskCompleted = true;')
    body=source[start:source.index('var remoteAfter = await VRCApi.GetAvatar',start)]
    flag=str(success_event).lower()
    code='using System;class Job{public bool TaskCompleted,UploadSucceeded='+flag+';public string PipelineIdAfter="avtr-existing",RemoteAvatarId='+('"avtr-existing"' if success_event else '""')+';}class Probe{static void Refresh(Job job){}static void Check(Job job){'+body+'}static int Main(){var job=new Job();bool rejected=false;try{Check(job);}catch(InvalidOperationException){rejected=true;}if(rejected=='+flag+')throw new Exception("expected rejection without SDK upload success, and acceptance with success event");return 0;}}'
    run(tmp_path,code)

@pytest.mark.parametrize('late_destination', [False, True])
def test_vrm_create_new_commit_never_replaces_late_file(tmp_path, late_destination):
    source=(ROOT/'Assets/VRCForge/Editor/VrmExporter.cs').read_text(encoding='utf-8')
    helpers='\n'.join(method(source,s) for s in ['private static void CommitValidatedOutput','private static void DeleteTemporaryFile'])
    start=source.index('CommitValidatedOutput(temporaryPath, outputPath, replacementBackupPath')
    call=source[start:source.index(';',start)+1]
    code='using System;using System.IO;class Params{public bool? overwrite=false;}class Probe{'+helpers+'static int Main(){var parameters=new Params();var outputPath=Path.Combine(Path.GetTempPath(),Guid.NewGuid()+".vrm");var temporaryPath=outputPath+".partial";var replacementBackupPath=outputPath+".replace-backup";try{'+('File.WriteAllText(outputPath,"late-user-file");' if late_destination else '')+'File.WriteAllText(temporaryPath,"export");bool rejected=false;try{'+call+'}catch(IOException){rejected=true;}if('+('!rejected||File.ReadAllText(outputPath)!="late-user-file"' if late_destination else 'rejected||File.ReadAllText(outputPath)!="export"')+')throw new Exception("create-new VRM commit contract violated");return 0;}finally{foreach(var p in new[]{outputPath,temporaryPath,replacementBackupPath})if(File.Exists(p))File.Delete(p);}}}'
    run(tmp_path,code)

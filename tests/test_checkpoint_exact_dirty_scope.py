from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from agent_gateway import AgentGateway
import dashboard_server as server

def test_exact_archive_scope_reaches_prepare_and_stays_exact(tmp_path):
    project=tmp_path/'project'
    for name in ('Assets','Packages','ProjectSettings'): (project/name).mkdir(parents=True)
    asset=project/'Assets/Recovery.controller';asset.write_bytes(b'original');Path(str(asset)+'.meta').write_bytes(b'meta')
    (project/'Assets/Other.shader').write_bytes(b'unrelated')
    gateway=AgentGateway(tmp_path/'config.json',tmp_path/'audit');seen=[]
    gateway.approval_transactions.checkpoint_prepare_handler=lambda root,*,checkpoint_files: seen.append(checkpoint_files) or {'ok':True}
    result=gateway.approval_transactions._create_pre_write_checkpoint({'id':'approval','targetTool':'vrcforge_manage_fx_animator'}, {'projectPath':str(project),'controllerPath':'Assets/Recovery.controller','edits':[{'action':'update_state','layerName':'Left Hand','stateName':'Fist','speed':0.75}]})
    assert result['ok'],result
    assert seen==[['Assets/Recovery.controller']]
    assert result['archiveFiles']==['Assets/Recovery.controller','Assets/Recovery.controller.meta']

def test_exact_restore_import_scope_comes_from_actual_disk_result(tmp_path):
    captured=[]
    with patch.object(server,'load_unity_mcp_core_connection',return_value=None), patch.object(server,'load_dashboard_settings',return_value=SimpleNamespace(unity_mcp_timeout_seconds=30,unity_mcp_retries=1)), patch.object(server,'build_agent_connection_request',return_value={}), patch.object(server,'invoke_unity_mcp',side_effect=lambda settings,tool,args,**kw: captured.append(args) or {'ok':True}), patch.object(server,'normalize_unity_checkpoint_result',side_effect=lambda result,root:result), patch.object(server,'_wait_for_checkpoint_reload_ready',return_value={'ok':True}):
        server.reload_unity_checkpoint_sync(tmp_path,{'scenes':[],'checkpointFiles':['Assets/Recovery.controller'],'restoredFiles':['Assets/Recovery.controller','Assets/Recovery.controller.meta'],'deletedFiles':[]})
    assert captured[0]['restoredAssetPaths']==['Assets/Recovery.controller','Assets/Recovery.controller.meta']

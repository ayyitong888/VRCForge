"""Registered interrupted-recovery callers reject contradictory exact selectors."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import dashboard_server as d
import agent_checkpoint_recovery as domain
from agent_gateway import AgentGatewayError

TOOLS=['vrcforge_resolve_interrupted_apply_recovery','vrcforge_preview_interrupted_apply_recovery','vrcforge_export_interrupted_apply_incident_bundle']

@pytest.fixture
def ports(tmp_path,monkeypatch):
 svc=d.AGENT_GATEWAY.checkpoint_recovery
 records=[{'id':'A','checkpointId':'CA','status':'applying'},{'id':'B','checkpointId':'CB','status':'applying'}]
 finish=Mock(side_effect=lambda r,**k:{**r,**k});write=Mock();preview=Mock(return_value={'ok':True})
 monkeypatch.setattr(domain.AgentCheckpointRecoveryService,'_coalesced_apply_recoveries',lambda self,**kw:records)
 monkeypatch.setattr(domain.AgentCheckpointRecoveryService,'preview_restore_checkpoint',lambda self,p:preview(p))
 monkeypatch.setattr(svc,'_ports',SimpleNamespace(approval=SimpleNamespace(apply_recovery_blocks_writes=lambda r:r['status']=='applying',finish_apply_recovery=finish),recent_audit_logs=lambda **k:[],audit_dir=lambda:tmp_path,append_audit=Mock()))
 monkeypatch.setattr(domain,'atomic_write_json',write)
 return records,finish,write,preview

def call(name,args):
 if name==TOOLS[0]:return d.AGENT_GATEWAY._write_handlers[name].handler({'confirmResolved':True,**args})
 return d.AGENT_GATEWAY._tools[name].handler(args)

@pytest.mark.parametrize('name',TOOLS)
@pytest.mark.parametrize('args',[{'recoveryId':'A','checkpointId':'CB'},{'recoveryId':'A','recovery_id':'B'},{'recoveryId':'A','id':'B'},{'checkpointId':'CA','checkpoint_id':'CB'}])
def test_conflicts_rejected_before_finish_preview_or_export(ports,name,args):
 records,finish,write,preview=ports
 with pytest.raises(AgentGatewayError):call(name,args)
 finish.assert_not_called();write.assert_not_called();preview.assert_not_called()

@pytest.mark.parametrize('name',TOOLS)
@pytest.mark.parametrize('args',[{}, {'recoveryId':'A','recovery_id':'A','checkpointId':'CA','checkpoint_id':'CA'},{'checkpointId':'CA'}])
def test_latest_and_consistent_exact_selectors_preserved(ports,name,args):
 records,finish,write,preview=ports
 result=call(name,args)
 assert result['ok'] is True
 if name==TOOLS[0]:assert finish.call_args.args[0]['id']=='A'
 elif name==TOOLS[1]:assert result['recovery']['id']=='A'
 else:assert result['bundle']['recovery']['id']=='A' and write.call_count==1


def test_checkpoint_first_match_semantics_not_expanded(ports):
 records,finish,write,preview=ports
 records[1]['checkpointId']='CA'
 assert call(TOOLS[0],{'checkpointId':'CA'})['recovery']['id']=='A'

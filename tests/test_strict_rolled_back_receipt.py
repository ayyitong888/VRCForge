from unittest.mock import patch
import pytest
import dashboard_server as ds
from agent_gateway import AgentGateway
from test_agent_gateway_apply_lifecycle import create_project, approved_write


def payload():
    return {"schema":"vrcforge.project_asset_copy.v2","ok":False,"batch":True,"verified":False,
        "mutationStarted":True,"committed":False,"saved":False,"restored":True,"commitState":"rolled_back",
        "commitStateKnown":True,"cleanupRequired":False,"checkpointRecoveryRequired":False,
        "failurePhase":"created_asset_readback","failureDetails":{"rowIndex":3,"sourceAssetPath":"Assets/Source.anim","destinationAssetPath":"Assets/VRCForgeGenerated/Test.anim","failedPredicates":["hash"],"expected":{"hash":"A"},"actual":{"hash":"B"}},
        "attemptedPaths":["Assets/VRCForgeGenerated/Test.anim"],"failureContext":{"rowIndex":3,"expected":"hashA","actual":"hashB"}}


def wrapped(raw):
    transport=ds.McpResult(exit_code=1,stdout="untrusted",stderr="",payload={"isError":True,
        "structuredContent":{"success":False,"message":"copy failed","data":raw}})
    with patch.object(ds,"load_dashboard_settings"),patch.object(ds,"invoke_unity_mcp",return_value=transport):
        return ds.unity_mcp_write_sync({"toolName":"vrc_duplicate_project_asset","arguments":{},"projectPath":"D:/Project",
                                     "_vrcforge_approved_execution":{"lane":"approved_write"}})


def test_strict_wrapper_preserves_proven_rollback_and_complete_core_evidence():
    raw=payload(); result=wrapped(raw)
    assert result["commitState"]=="rolled_back" and result["restored"] is True
    assert result["commitStateKnown"] is True and result["checkpointRecoveryRequired"] is False
    assert result["attemptedPaths"]==raw["attemptedPaths"] and result["coreFailureEvidence"]=={**raw,"message":"copy failed","success":False}
    assert result["failureDetails"]==raw["failureDetails"] and result["failurePhase"]==raw["failurePhase"]
    assert result["ok"] is False and "untrusted" not in str(result)


@pytest.mark.parametrize("change",[{"restored":False},{"commitStateKnown":False},{"committed":True},{"cleanupRequired":True},{"checkpointRecoveryRequired":True},{"restored":None}])
def test_incomplete_or_contradictory_rollback_stays_unknown(change):
    result=wrapped({**payload(),**change})
    assert result["commitState"]=="unknown" and result["checkpointRecoveryRequired"] is True
    assert result["commitStateKnown"] is False


def test_real_approval_lifecycle_finishes_only_proven_atomic_rollback(tmp_path):
    project=create_project(tmp_path)
    gateway=AgentGateway(tmp_path/"config/gateway.json",tmp_path/"audit")
    result=approved_write(gateway,project,handler=lambda _:wrapped(payload()))
    assert result["ok"] is False and result["writeFailure"]["commitState"]=="rolled_back"
    assert gateway.checkpoint_recovery.list_interrupted_apply_recoveries()["activeCount"]==0


@pytest.mark.parametrize("change",[{"restored":False},{"commitStateKnown":False},{"cleanupRequired":True}])
def test_real_approval_lifecycle_keeps_unproven_recovery(tmp_path,change):
    project=create_project(tmp_path)
    gateway=AgentGateway(tmp_path/"config/gateway.json",tmp_path/"audit")
    result=approved_write(gateway,project,handler=lambda _:wrapped({**payload(),**change}))
    assert result["ok"] is False
    assert gateway.checkpoint_recovery.list_interrupted_apply_recoveries()["activeCount"]==1


@pytest.mark.parametrize("proven",[True,False])
def test_external_call_cleanup_state_uses_proven_rollback(tmp_path,proven):
    from test_mcp_write_transaction_contract import _gateway
    gateway=_gateway(tmp_path/"gateway")
    raw=payload()
    if not proven:raw["restored"]=False
    name="vrcforge_test_strict_atomic_rollback"
    gateway.approval_transactions.register_write_handler(name,"Atomic rollback contract","high",lambda _:wrapped(raw),
        pre_write_checkpoint_required=False)
    gateway.register_external_mcp_unity_tool(name,"avatar")
    proposal=gateway.call_external_mcp_tool(name,{})
    result=gateway.call_external_mcp_tool(name,{"confirmation":{**proposal["confirmation"],"decision":"approve"}})
    assert result["ok"] is False
    assert result["cleanupState"]==("complete" if proven else "unknown")
    assert result["commitState"]==("rolled_back" if proven else "unknown")
    assert result["writeFailure"]["checkpointRecoveryRequired"] is (not proven)
    assert result["readbackState"]=="failed"

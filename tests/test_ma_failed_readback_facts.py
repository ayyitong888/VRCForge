from copy import deepcopy

import pytest

from agent_tool_result_contract import normalize_agent_tool_result
from wardrobe_outfit_workflow_service import finalize_add_modular_avatar_component_verification


def report_case():
    # The reported saved-write/readback-failure combination, without Unity writes.
    args = {"gameObjectPath": "Avatar/Outfit/Bindings", "componentType": "BlendshapeSync",
            "saveScene": True, "fields": {"Bindings": [{"ReferenceMesh": {"referencePath": "Body_Base"},
                "RemapCurve": {"keys": [{"time": 0, "value": 0}], "preWrapMode": 0}}]}}
    receipt = {"ok": True, "updatedComponent": True, "sceneSaved": True, "sceneDirty": False,
               "pending": False, "before": {"count": 1}, "after": {"count": 1}}
    inspection = {"ok": True, "present": True, "count": 1, "references": [], "sceneDirty": False}
    return args, receipt, inspection


@pytest.mark.parametrize("mode", ["incomplete", "mismatch", "exception", "inspector"])
def test_saved_write_keeps_mutation_facts_when_readback_fails(mode):
    args, receipt, inspection = report_case()
    observed = deepcopy(args["fields"]["Bindings"])
    observed[0]["RemapCurve"]["preWrapMode"] = 2
    def read(_):
        if mode == "exception":
            raise RuntimeError("property transport failed")
        return {"ok": True, "propertyValue": observed, "valueIncomplete": mode == "incomplete"}
    result = finalize_add_modular_avatar_component_verification(
        args, receipt, lambda _: {"ok": False, "error": "inspection failed"} if mode == "inspector" else inspection, read)
    assert result["ok"] is False
    assert result["mutationStarted"] is True and result["mutationApplied"] is True
    assert result["sceneSaved"] is True and result["persistenceState"] == "persisted"
    assert result.get("verified") is not True and result["readbackState"] == "failed"
    assert "Do not repeat" in result["nextAction"]
    outcome = normalize_agent_tool_result(result, fallback_summary="MA write", write=True)
    assert outcome["mutationStarted"] is True and outcome["status"] == "failed"
    if mode == "mismatch":
        assert result["delta"]["path"] == "/Bindings/0/RemapCurve/preWrapMode"
        assert result["expected"] == 0 and result["observed"] == 2
    elif mode == "incomplete":
        assert result["delta"]["reason"] == "readback_incomplete"
        assert result["delta"]["readResult"]["valueIncomplete"] is True
        assert result["expected"] == args["fields"]["Bindings"]
        assert result["observed"] == observed
    assert receipt == report_case()[1], "Do not rewrite the original Core receipt"


def test_failed_direct_reference_read_preserves_actual_failure():
    args, receipt, inspection = report_case()
    args.pop("fields")
    args["references"] = {"animator": "Assets/Expected.controller"}
    result = finalize_add_modular_avatar_component_verification(args, receipt, lambda _: inspection,
        lambda _: {"ok": False, "error": "property unavailable"})
    assert result["mutationStarted"] is True
    assert result["delta"]["path"] == "/animator"
    assert result["delta"]["readResult"]["error"] == "property unavailable"
    assert result["expected"] == "Assets/Expected.controller"


@pytest.mark.parametrize("external", [False, True])
def test_saved_ma_readback_failure_reaches_internal_and_external_agent(tmp_path, external):
    from test_mcp_write_transaction_contract import _gateway, _unity_project
    gateway = _gateway(tmp_path)
    project = _unity_project(tmp_path)
    args, receipt, inspection = report_case()
    args["projectPath"] = str(project)
    tool = "vrcforge_contract_saved_ma_readback"
    observed = deepcopy(args["fields"]["Bindings"])
    observed[0]["RemapCurve"]["preWrapMode"] = 2
    gateway.approval_transactions.register_write_handler(
        tool, "MA failed readback", "high", lambda _: deepcopy(receipt),
        pre_write_checkpoint_required=False,
        verification_prepare_handler=lambda _: {},
        verification_finalize_handler=lambda arguments, baseline, result:
            finalize_add_modular_avatar_component_verification(arguments, result, lambda _: inspection,
                lambda _: {"ok": True, "propertyValue": observed}),
    )
    if external:
        gateway.register_external_mcp_unity_tool(tool, "avatar")
        proposal = gateway.call_external_mcp_tool(tool, args)
        result = gateway.call_external_mcp_tool(tool, {
            **args, "confirmation": {**proposal["confirmation"], "decision": "approve"}})
    else:
        request = gateway.approval_transactions.create_apply_request({"target_tool": tool, "arguments": args})
        gateway.approval_transactions.approve(request["approval"]["id"])
        result = gateway.approval_transactions.apply_approved({"approval_id": request["approval"]["id"]})
    assert result["ok"] is False
    assert result["outcome"]["mutationStarted"] is True
    assert result["outcome"]["delta"]["path"] == "/Bindings/0/RemapCurve/preWrapMode"
    assert result["writeFailure"]["mutationStarted"] is True
    assert result["outcome"]["expected"] == 0
    assert result["outcome"]["observed"] == 2
    if external:
        assert result["errorDetails"]["expected"] == 0
        assert result["errorDetails"]["observed"] == 2
        assert result["mutationStarted"] is True and result["mutationApplied"] is True
        assert result["persistenceState"] == "persisted"
        assert result["errorDetails"]["rawResult"] == receipt

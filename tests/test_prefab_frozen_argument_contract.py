"""Approved plans and actual prefab callbacks must carry identical identity guards."""
import copy

import pytest

import dashboard_server as server
from approved_unity_execution import ApprovedUnityExecutionClaimError, create_approved_unity_execution_plan


UNPACK_GUARDS = {
    "expectedGlobalObjectId": "object-before", "expectedPrefabGuid": "a" * 32,
    "expectedAssetDependencyHash": "b" * 32, "expectedScenePath": "Assets/Main.unity",
    "approvedObjectReceiptNonce": "c" * 64,
}
INSTANTIATE_GUARDS = {
    "expectedPrefabGuid": "a" * 32, "expectedAssetDependencyHash": "b" * 32,
    "expectedScenePath": "Assets/Main.unity", "expectedParentGlobalObjectId": "parent-before",
    "expectedResultPath": "Avatar/Clothing", "approvedObjectReceiptNonce": "c" * 64,
    "approvedContinuationTools": ["vrc_unpack_prefab", "vrc_setup_outfit"],
}


def bound_call(tmp_path, monkeypatch, target, parameters, *, mutate=None):
    handler = server.AGENT_GATEWAY._write_handlers[target]
    approved = create_approved_unity_execution_plan({
        "lane": "approved_write", "approvalId": "approval", "checkpointId": "checkpoint",
        "targetTool": target, "projectRoot": str(tmp_path), "issuedAtUnixMs": 0, "expiresAtUnixMs": 100,
    }, handler.approved_execution_plan_builder(parameters))
    sent = []
    def invoke(settings, tool, request):
        claim = approved.claim(tool, request, tmp_path, now_unix_ms=1)
        sent.append((tool, copy.deepcopy(request)))
        claim.complete()
        return {"ok": True}
    monkeypatch.setattr(server, "load_dashboard_settings", lambda value: value)
    monkeypatch.setattr(server, "build_agent_connection_request", lambda value: value)
    monkeypatch.setattr(server, "invoke_unity_mcp", invoke)
    monkeypatch.setattr(server, "extract_tool_result_payload", lambda value: value)
    monkeypatch.setattr(server, "emit_log", lambda *args: None)
    actual = copy.deepcopy(parameters)
    if mutate:
        mutate(actual)
    result = handler.handler(actual)
    assert result["ok"] is True and approved.consumed is True
    return sent[0][1]


@pytest.mark.parametrize("guard", [*UNPACK_GUARDS, "all"])
def test_registered_unpack_freezes_every_guard_forwarded_by_its_callback(tmp_path, monkeypatch, guard):
    guards = UNPACK_GUARDS if guard == "all" else {guard: UNPACK_GUARDS[guard]}
    request = {"gameObjectPath": "Avatar/Clothing", **guards}
    sent = bound_call(tmp_path, monkeypatch, "vrcforge_unpack_prefab", request)
    assert all(sent[key] == value for key, value in guards.items())


@pytest.mark.parametrize("guard", [*INSTANTIATE_GUARDS, "all"])
def test_registered_instantiation_freezes_every_guard_forwarded_by_its_callback(tmp_path, monkeypatch, guard):
    guards = INSTANTIATE_GUARDS if guard == "all" else {guard: INSTANTIATE_GUARDS[guard]}
    request = {"assetPath": "Assets/Clothing.prefab", "parentPath": "Avatar", **guards}
    sent = bound_call(tmp_path, monkeypatch, "vrcforge_instantiate_prefab", request)
    assert all(sent[key] == value for key, value in guards.items())


@pytest.mark.parametrize("target,parameters", [
    ("vrcforge_unpack_prefab", {"gameObjectPath": "Avatar/Clothing", **UNPACK_GUARDS}),
    ("vrcforge_instantiate_prefab", {"assetPath": "Assets/Clothing.prefab", **INSTANTIATE_GUARDS}),
])
def test_identity_change_after_approval_still_fails_before_transport(tmp_path, monkeypatch, target, parameters):
    with pytest.raises(ApprovedUnityExecutionClaimError, match="arguments do not match"):
        bound_call(tmp_path, monkeypatch, target, parameters, mutate=lambda args: args.update(expectedPrefabGuid="d" * 32))

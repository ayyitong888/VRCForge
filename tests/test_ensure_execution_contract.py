"""Core-preview footprint, approved plan and actual ensure call share one contract."""
from copy import deepcopy

import pytest

import dashboard_server as server
import vrchat_blendshape_agent as agent
from approved_unity_execution import bind_approved_unity_execution, create_approved_unity_execution_plan
from test_approved_unity_execution import _context, _descriptor, _settings


PARAMETER_PATH = "Assets/Generated/AvatarParameters.asset"
FIXTURE_REQUEST = {
    "avatarPath": "Avatar", "parameterName": "VRCForge_Outfit",
    "valueType": "Int", "defaultValue": 0, "saved": True, "networkSynced": True,
}
CASES = [
    ("ensure_expression_parameter", FIXTURE_REQUEST, [PARAMETER_PATH]),
    ("ensure_expression_menu_control", {"avatarPath": "Avatar", "controlName": "Outfit", "parameterName": "Outfit"}, ["Assets/Menu.asset", "Assets/Submenu.asset"]),
    ("ensure_animator_state", {"avatarPath": "Avatar", "layerName": "Outfit", "stateName": "Default", "parameterName": "Outfit"}, ["Assets/FX.controller", "Assets/Default.anim"]),
]


@pytest.mark.parametrize("action,inputs,paths", CASES)
@pytest.mark.parametrize("drift", [False, True])
def test_registered_ensure_preview_to_frozen_plan_to_real_transport(tmp_path, monkeypatch, action, inputs, paths, drift):
    _descriptor(tmp_path)
    monkeypatch.setattr(server, "load_dashboard_settings", lambda *_: _settings(tmp_path))
    transported = []

    class Core:
        def __init__(self, *_args, **_kwargs):
            pass

        def call_tool(self, tool, arguments, *, execution_context=None):
            transported.append((tool, deepcopy(arguments), execution_context))
            assert tool == "vrc_" + action
            if arguments["preview"]:
                return {"isError": False, "structuredContent": {"ok": True, "preview": True,
                    "plan": {"action": action, "checkpointAssetPaths": paths}}}
            return {"isError": False, "structuredContent": {"ok": True}}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", Core)
    registered = server.AGENT_GATEWAY._write_handlers["vrcforge_" + action]
    prepared, _ = registered.request_preparer({**deepcopy(inputs), "projectPath": str(tmp_path)}, None)
    assert prepared["expectedCheckpointAssetPaths"] == paths
    calls = registered.approved_execution_plan_builder(prepared)
    plan = create_approved_unity_execution_plan(_context(tmp_path, issued=0, expires=9_999_999_999_999), calls)
    if drift:
        prepared["expectedCheckpointAssetPaths"] = ["Assets/Unapproved.asset"]
    with bind_approved_unity_execution(plan):
        if drift:
            with pytest.raises(agent.UnityMcpError, match="arguments do not match"):
                registered.handler(prepared)
            assert len(transported) == 1 and not plan.consumed
        else:
            result = registered.handler(prepared)
            assert result["ok"] and plan.consumed
            assert len(transported) == 2
            assert transported[-1][:2] == calls[0]
            assert transported[-1][1]["expectedCheckpointAssetPaths"] == paths


@pytest.mark.parametrize("extra", [
    {}, {"timeParameterActive": None, "timeParameter": None},
    {"time_parameter_active": None, "time_parameter": None},
    {"time_parameter_active": "false", "time_parameter": " v2/Eye "},
    {"timeParameterActive": True, "timeParameter": "v2/Eye", "asset_dir": " Assets/FT "},
])
def test_animator_optional_time_fields_match_execution_normalization(extra):
    from unity_execution_plans_scene import build_scene_execution_plan
    arguments = {**CASES[2][1], **extra, "expectedCheckpointAssetPaths": CASES[2][2]}
    calls = build_scene_execution_plan("vrcforge_ensure_animator_state", arguments)
    assert calls == [("vrc_ensure_animator_state", server.build_ensure_animator_state_request(arguments, False))]


def test_checkpoint_scope_changes_frozen_request_digest():
    from unity_mcp_core_client import canonical_arguments_sha256
    request = server.build_ensure_expression_parameter_request(FIXTURE_REQUEST, False)
    assert canonical_arguments_sha256(request) == "46ac6332c19a03a900ae90d426eb41a28593f09017125cfe714d6820c4c7d703"
    scoped = server.build_ensure_expression_parameter_request({**FIXTURE_REQUEST, "expectedCheckpointAssetPaths": [PARAMETER_PATH]}, False)
    assert scoped != request and set(scoped) - set(request) == {"expectedCheckpointAssetPaths"}


@pytest.mark.parametrize("prior_call", [False, True])
def test_preroute_rejection_recovery_uses_operation_facts(tmp_path, prior_call):
    from agent_gateway import AgentGateway
    from test_agent_gateway_apply_lifecycle import create_project, approved_write
    from unity_mcp_core_client import _record_unity_mcp_core_call_audit
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config/gateway.json", tmp_path / "audit")

    def handler(_arguments):
        if prior_call:
            _record_unity_mcp_core_call_audit({"toolName": "vrc_ensure_expression_parameter", "resultSummary": "complete"})
        raise agent._unity_mcp_pre_route_error(
            "Approved Unity execution arguments do not match the frozen plan.",
            cause_code="approved_execution_claim_rejected",
            core_tool="vrc_ensure_expression_parameter", failure_phase="approved_execution_claim",
        )

    result = approved_write(gateway, project, handler=handler)
    assert result["ok"] is False
    facts = result["writeFailure"]
    active = gateway.checkpoint_recovery._active_apply_recoveries()
    if prior_call:
        assert facts["commitState"] == "unknown"
        assert facts["mutationStarted"] is None and facts["committed"] is None
        assert facts["checkpointRecoveryRequired"] is True
        assert facts["recovery"]["required"] is True
        assert len(active) == 1
    else:
        assert facts["mutationStarted"] is False and facts["committed"] is False
        assert facts["commitState"] == "not_started"
        assert facts["checkpointRecoveryRequired"] is False
        assert facts["safeToRetry"] is False  # invalid plan is not a retry instruction
        assert active == []

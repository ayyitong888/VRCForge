from __future__ import annotations

from types import SimpleNamespace

import agent_gateway
import dashboard_server
import unity_mcp_tool_contract


def test_deformation_schema_is_strict_and_shared_in_avatar_read_block() -> None:
    schema = agent_gateway.UNITY_READ_TOOL_INPUT_SCHEMAS[
        "vrcforge_inspect_skinned_mesh_deformation"
    ]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["projectPath", "gameObjectPath", "componentIndex"]
    assert schema["properties"]["componentIndex"]["minimum"] == 0
    assert "vrcforge_inspect_skinned_mesh_deformation" in agent_gateway.EXTERNAL_MCP_READ_TOOL_BLOCKS["core"]
    assert "vrc_inspect_skinned_mesh_deformation" in unity_mcp_tool_contract.EXPECTED_TOOL_NAMES
    assert unity_mcp_tool_contract.EXPECTED_TOOL_COUNT == 81
    assert unity_mcp_tool_contract.TOOL_CONTRACT_VERSION == "83"


def test_deformation_read_forwards_exact_core_arguments_and_preserves_metrics(monkeypatch) -> None:
    metrics = {
        "rest": {"vertexCount": 10, "finiteVertexCount": 10},
        "play": {"vertexCount": 10, "finiteVertexCount": 10},
        "world": {"finiteVertexCount": 10},
        "usedBoneReconstructedSkinMatrix": {"maxAbsDeviation": 0.01},
    }
    observed = {}
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda _settings, tool, request, preserve_tool_error=False: (
            observed.update(tool=tool, request=request, preserve_tool_error=preserve_tool_error)
            or SimpleNamespace(exit_code=0, payload={"ok": True, **metrics})
        ),
    )
    result = dashboard_server.inspect_skinned_mesh_deformation_sync(
        {
            "projectPath": "D:/Unity/Avatar",
            "gameObjectPath": "FinalAvatar/Body",
            "componentIndex": 2,
        }
    )
    assert observed == {
        "tool": "vrc_inspect_skinned_mesh_deformation",
        "request": {"gameObjectPath": "FinalAvatar/Body", "componentIndex": 2},
        "preserve_tool_error": True,
    }
    assert result["rest"] is metrics["rest"]
    assert result["play"] is metrics["play"]
    assert result["usedBoneReconstructedSkinMatrix"] is metrics["usedBoneReconstructedSkinMatrix"]


def test_deformation_read_preserves_handler_diagnostics_and_failed_step(monkeypatch) -> None:
    handler_failure = {
        "success": False,
        "ok": False,
        "errorCode": "skinned_mesh_deformation_inspection_failed",
        "error": "BakeMesh failed",
        "failureLayer": "unity_tool_handler",
        "failurePhase": "tool_handler_exception",
        "failedStep": "bake_mesh",
        "diagnostics": {"schema": "vrcforge.unity_tool_handler_diagnostics.v1", "handlerException": {"type": "InvalidOperationException"}},
    }
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda *_args, **_kwargs: SimpleNamespace(exit_code=0, payload=handler_failure),
    )
    result = dashboard_server.inspect_skinned_mesh_deformation_sync(
        {"projectPath": "D:/Unity/Avatar", "gameObjectPath": "FinalAvatar/Body", "componentIndex": 0}
    )
    assert result["failedStep"] == "bake_mesh"
    assert result["diagnostics"] == handler_failure["diagnostics"]


def test_deformation_argument_failure_has_canonical_read_envelope() -> None:
    result = dashboard_server.inspect_skinned_mesh_deformation_sync({})
    assert result["ok"] is False
    assert result["errorCode"] == "skinned_mesh_deformation_arguments_missing"
    assert result["failureLayer"] == "external_tool_arguments"
    assert result["failurePhase"] == "argument_validation"
    assert result["causeChain"] == []
    assert result["mutationStarted"] is False
    assert result["committed"] is False
    assert result["commitState"] == "not_started"
    assert result["commitStateKnown"] is True
    assert result["toolRoutingStarted"] is False

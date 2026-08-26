from __future__ import annotations

from types import SimpleNamespace

import agent_gateway
import dashboard_server
import unity_mcp_tool_contract


def test_remap_schema_is_shared_and_exposed_in_avatar_block() -> None:
    schema = agent_gateway.EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_remap_skinned_mesh_bone"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "projectPath", "gameObjectPath", "componentIndex", "boneIndex",
        "expectedCurrentBonePath", "targetBonePath", "expectedMeshName", "preview",
    ]
    assert agent_gateway.canonical_unity_write_tool_input_schema("vrcforge_remap_skinned_mesh_bone") == schema
    assert "vrcforge_remap_skinned_mesh_bone" in agent_gateway.EXTERNAL_MCP_WRITE_TOOL_BLOCKS["avatar"]
    assert "vrcforge_remap_skinned_mesh_bone" in dashboard_server.AGENT_GATEWAY.approval_transactions.registered_write_target_names()
    assert unity_mcp_tool_contract.TOOL_CONTRACT_VERSION == "84"
    assert unity_mcp_tool_contract.EXPECTED_TOOL_COUNT == 82
    assert unity_mcp_tool_contract.PREVIOUS_CORE_TOOL_CONTRACT_VERSION == "83"
    assert len(unity_mcp_tool_contract.PREVIOUS_CORE_TOOL_NAMES) == 81
    assert "vrc_inspect_skinned_mesh_deformation" in unity_mcp_tool_contract.PREVIOUS_CORE_TOOL_NAMES
    assert "vrc_set_material_texture" not in unity_mcp_tool_contract.PREVIOUS_CORE_TOOL_NAMES


def test_remap_execution_plan_freezes_one_exact_core_call() -> None:
    calls = dashboard_server.build_scene_execution_plan(
        "vrcforge_remap_skinned_mesh_bone",
        {
            "gameObjectPath": "Avatar/Shinano_CatEars",
            "componentIndex": 0,
            "boneIndex": 2,
            "expectedCurrentBonePath": "Avatar/Head/OldBone",
            "targetBonePath": "Avatar/Head/NewBone",
            "expectedMeshName": "Shinano_CatEars",
            "preview": True,
        },
    )
    assert calls == [
        (
            "vrc_remap_skinned_mesh_bone",
            {
                "gameObjectPath": "Avatar/Shinano_CatEars",
                "componentIndex": 0,
                "boneIndex": 2,
                "expectedCurrentBonePath": "Avatar/Head/OldBone",
                "targetBonePath": "Avatar/Head/NewBone",
                "expectedMeshName": "Shinano_CatEars",
                "preview": False,
            },
        )
    ]


def test_remap_forwards_core_failure_without_swallowing_cause(monkeypatch) -> None:
    observed = {}
    failure = {
        "success": False,
        "errorCode": "bone_slot_current_path_mismatch",
        "error": "Current bone path did not match the expected receipt.",
        "failureLayer": "unity_core_dispatch",
        "failurePhase": "argument_validation",
        "causeChain": [{"code": "expected_current_bone_mismatch"}],
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
    }

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda _settings, tool, request, preserve_tool_error=False: (
            observed.update(tool=tool, request=request, preserve_tool_error=preserve_tool_error)
            or SimpleNamespace(exit_code=1, payload={"isError": True, "structuredContent": failure})
        ),
    )
    result = dashboard_server.remap_skinned_mesh_bone_sync(
        {
            "projectPath": "D:/Unity/Avatar",
            "gameObjectPath": "Avatar/Shinano_CatEars",
            "componentIndex": 0,
            "boneIndex": 2,
            "expectedCurrentBonePath": "Avatar/Head/OldBone",
            "targetBonePath": "Avatar/Head/NewBone",
            "expectedMeshName": "Shinano_CatEars",
            "preview": True,
        }
    )
    assert observed["tool"] == "vrc_remap_skinned_mesh_bone"
    assert observed["preserve_tool_error"] is True
    assert result["errorCode"] == failure["errorCode"]
    assert result["failureLayer"] == failure["failureLayer"]
    assert result["failurePhase"] == failure["failurePhase"]
    assert result["causeChain"] == failure["causeChain"]
    assert result["mutationStarted"] is False
    assert result["committed"] is False
    assert result["commitState"] == failure["commitState"]
    assert result["commitStateKnown"] is True


def test_remap_argument_failure_has_complete_canonical_envelope() -> None:
    result = dashboard_server.remap_skinned_mesh_bone_sync({"projectPath": "D:/Unity/Avatar"})
    assert result == {
        "ok": False,
        "errorCode": "remap_skinned_mesh_bone_arguments_missing",
        "error": "Missing required fields: gameObjectPath, componentIndex, boneIndex, expectedCurrentBonePath, targetBonePath, expectedMeshName, preview",
        "failureLayer": "external_tool_arguments",
        "failurePhase": "argument_validation",
        "causeChain": [],
        "mutationStarted": False,
        "committed": False,
        "commitState": "not_started",
        "commitStateKnown": True,
        "toolRoutingStarted": False,
    }


def test_remap_preserves_current_and_target_skinning_metrics_verbatim(monkeypatch) -> None:
    metrics = {
        "translationMagnitude": 0.125,
        "maxAbsDeviation": 0.5,
        "determinant": 1.0,
        "nearIdentity": False,
        "reconstructedSkinMatrix": [1.0, 2.0, 3.0, 4.0],
    }
    core_payload = {
        "success": True,
        "ok": True,
        "currentSkinningMetrics": metrics,
        "targetSkinningMetrics": {**metrics, "translationMagnitude": 0.25},
    }
    observed = {}
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda _settings, tool, request, preserve_tool_error=False: (
            observed.update(tool=tool, request=request, preserve_tool_error=preserve_tool_error)
            or SimpleNamespace(exit_code=0, payload=core_payload)
        ),
    )
    result = dashboard_server.remap_skinned_mesh_bone_sync({
        "projectPath": "D:/Unity/Avatar",
        "gameObjectPath": "Avatar/Body",
        "componentIndex": 0,
        "boneIndex": 3,
        "expectedCurrentBonePath": "Avatar/Armature/Neck",
        "targetBonePath": "Avatar/Armature/Head",
        "expectedMeshName": "Body",
        "preview": True,
    })
    assert observed["tool"] == "vrc_remap_skinned_mesh_bone"
    assert observed["preserve_tool_error"] is True
    assert result["currentSkinningMetrics"] is metrics
    assert result["targetSkinningMetrics"]["translationMagnitude"] == 0.25

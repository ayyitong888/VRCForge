from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

import dashboard_server
from agent_gateway import AgentGateway, AgentGatewayError
from material_shader_assignment import (
    APPROVAL_PREVIEW_SCHEMA,
    MaterialShaderAssignmentError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
    compute_shared_impact_commitment,
    compute_shared_impact_digest,
    compute_shared_impact_tail_digest,
)


def preview_payload() -> dict:
    payload = {
        "schema": "vrcforge.material_shader_assignment.v1",
        "ok": True,
        "preview": True,
        "verified": True,
        "changed": False,
        "wouldChange": True,
        "rendererPath": "Avatar/Body",
        "rendererScenePath": "Assets/Scenes/Avatar.unity",
        "rendererSceneGuid": "e" * 32,
        "rendererSceneHandle": 7,
        "rendererComponentId": "d" * 64,
        "rendererComponentType": "UnityEngine.SkinnedMeshRenderer",
        "rendererComponentIndex": 0,
        "slotIndex": 1,
        "materialAssetPath": "Assets/Avatar/Body.mat",
        "materialAssetGuid": "a" * 32,
        "materialFileDigestBefore": "b" * 64,
        "materialFileDigestAfter": "b" * 64,
        "beforeShader": "Standard",
        "beforeShaderAssetPath": "",
        "beforeShaderAssetGuid": "",
        "requestedShader": "Project/Toon",
        "afterShader": "Project/Toon",
        "shaderAssetPath": "Assets/Shaders/Toon.shader",
        "shaderAssetGuid": "c" * 32,
        "saved": False,
        "sharedImpact": {
            "scope": "loaded_scene_renderers_and_project_scene_prefab_dependencies",
            "dependencyCandidateCount": 2,
            "loadedRendererSlotCount": 1,
            "loadedRendererSlots": [
                {
                    "scenePath": "Assets/Scenes/Avatar.unity",
                    "sceneGuid": "e" * 32,
                    "sceneHandle": 7,
                    "rendererPath": "Avatar/Body",
                    "rendererComponentId": "d" * 64,
                    "rendererComponentType": "UnityEngine.SkinnedMeshRenderer",
                    "rendererComponentIndex": 0,
                    "slotIndex": 1,
                }
            ],
            "dependentAssetCount": 1,
            "dependentAssets": ["Assets/Avatar/Avatar.prefab"],
            "listsTruncated": False,
        },
    }
    refresh_impact_receipts(payload)
    return payload


def refresh_impact_receipts(
    payload: dict,
    *,
    tail_slots: list[dict] | None = None,
    tail_assets: list[str] | None = None,
) -> None:
    impact = payload["sharedImpact"]
    display_digest = compute_shared_impact_digest(impact)
    tail_digest = compute_shared_impact_tail_digest(
        impact,
        slots=tail_slots or [],
        assets=tail_assets or [],
    )
    payload["sharedImpactDigestSchema"] = "vrcforge.material_shader_impact.v2"
    payload["sharedImpactDisplayDigest"] = display_digest
    payload["sharedImpactTailDigest"] = tail_digest
    payload["sharedImpactDigest"] = compute_shared_impact_commitment(
        impact,
        display_digest=display_digest,
        tail_digest=tail_digest,
    )


def wrapper_arguments(project_path: str = "D:/UnityProject") -> dict:
    return {
        "projectPath": project_path,
        "toolName": "vrc_set_material_shader",
        "arguments": {
            "rendererPath": "Body",
            "slotIndex": 1,
            "shaderName": "Project/Toon",
            "shaderAssetPath": "Assets/Shaders/Toon.shader",
        },
    }


def test_preview_arguments_remove_caller_preconditions_and_force_read_only_mode() -> None:
    raw = {
        "shaderName": "Project/Toon",
        "expectedBeforeShader": "spoofed",
        "expectedMaterialAssetPath": "Assets/Spoof.mat",
        "expectedMaterialAssetGuid": "1" * 32,
        "expectedMaterialFileDigest": "2" * 64,
        "expectedSharedImpactDigest": "3" * 64,
        "preview": False,
        "saveAssets": False,
    }

    prepared = build_preview_arguments(raw)

    assert prepared["preview"] is True
    assert prepared["saveAssets"] is True
    assert not any(key.startswith("expected") for key in prepared)
    assert raw["expectedBeforeShader"] == "spoofed"


def test_flat_preview_inputs_are_normalized_into_the_write_wrapper_shape() -> None:
    wrapper = build_wrapper_arguments(
        {
            "projectPath": "D:/UnityProject",
            "rendererPath": "Avatar/Body",
            "slotIndex": 1,
            "shaderName": "Project/Toon",
        }
    )

    assert wrapper["projectPath"] == "D:/UnityProject"
    assert wrapper["toolName"] == "vrc_set_material_shader"
    assert wrapper["arguments"] == {
        "rendererPath": "Avatar/Body",
        "slotIndex": 1,
        "shaderName": "Project/Toon",
    }
    assert "rendererPath" not in {key for key in wrapper if key != "arguments"}


def test_authoritative_preview_binds_target_file_shader_and_impact_receipts() -> None:
    canonical, preview = bind_authoritative_preview(wrapper_arguments(), preview_payload())
    nested = canonical["arguments"]

    assert preview["schema"] == APPROVAL_PREVIEW_SCHEMA
    assert preview["sharedImpactDisplayDigest"] == "2933f57c5996e9c75e7af37b9531fb368c85e9516fdadbcc3f7f2e8a7ed0250b"
    assert preview["sharedImpactTailDigest"] == "01e8fc78f443fb1bca38378e7d3135af8842f62d875b9a6fccae722ffdd114db"
    assert preview["sharedImpactDigest"] == "71841b596f64c81b212ce283b0ac88c2dba3863a97575c626c97de6a6c8711ac"
    assert preview["target"]["materialAssetPath"] == "Assets/Avatar/Body.mat"
    assert preview["sharedImpactDisplayDigest"] == compute_shared_impact_digest(preview["sharedImpact"])
    assert nested["rendererPath"] == "Avatar/Body"
    assert nested["rendererComponentId"] == "d" * 64
    assert nested["expectedRendererScenePath"] == "Assets/Scenes/Avatar.unity"
    assert nested["expectedRendererSceneGuid"] == "e" * 32
    assert nested["expectedRendererSceneHandle"] == 7
    assert nested["expectedRendererComponentId"] == "d" * 64
    assert nested["expectedRendererComponentType"] == "UnityEngine.SkinnedMeshRenderer"
    assert nested["expectedRendererComponentIndex"] == 0
    assert nested["expectedBeforeShader"] == "Standard"
    assert nested["expectedBeforeShaderAssetPath"] == ""
    assert nested["expectedBeforeShaderAssetGuid"] == ""
    assert nested["expectedMaterialAssetPath"] == "Assets/Avatar/Body.mat"
    assert nested["expectedMaterialAssetGuid"] == "a" * 32
    assert nested["expectedMaterialFileDigest"] == "b" * 64
    assert nested["expectedSharedImpactDigest"] == preview["sharedImpactDigest"]
    assert nested["expectedShaderAssetPath"] == "Assets/Shaders/Toon.shader"
    assert nested["expectedShaderAssetGuid"] == "c" * 32
    assert nested["preview"] is False
    assert nested["saveAssets"] is True


@pytest.mark.parametrize(
    ("mutator"),
    [
        lambda payload: payload.update({"materialAssetPath": "../Other.mat"}),
        lambda payload: payload.update({"materialFileDigestBefore": "not-a-digest"}),
        lambda payload: payload.update({"sharedImpactDigest": "e" * 63}),
        lambda payload: payload.update({"sharedImpactDisplayDigest": "f" * 64}),
        lambda payload: payload.update({"sharedImpactTailDigest": "f" * 64}),
        lambda payload: payload.update({"changed": True}),
        lambda payload: payload.update({"saved": True}),
        lambda payload: payload.update({"materialFileDigestAfter": "f" * 64}),
        lambda payload: payload["sharedImpact"].update({"loadedRendererSlotCount": 0}),
        lambda payload: payload["sharedImpact"].update({"listsTruncated": True}),
        lambda payload: payload.update({"requestedShader": "Different/Shader"}),
    ],
)
def test_authoritative_preview_rejects_untrusted_or_inconsistent_receipts(mutator) -> None:
    payload = preview_payload()
    mutator(payload)

    with pytest.raises(MaterialShaderAssignmentError):
        bind_authoritative_preview(wrapper_arguments(), payload)


def test_direct_material_preview_removes_renderer_selector() -> None:
    wrapper = wrapper_arguments()
    wrapper["arguments"] = {
        "materialAssetPath": "Assets/Avatar/Body.mat",
        "shaderName": "Project/Toon",
    }
    payload = preview_payload()
    payload["rendererPath"] = ""
    payload["rendererScenePath"] = ""
    payload["rendererSceneGuid"] = ""
    payload["rendererSceneHandle"] = -1
    payload["rendererComponentId"] = ""
    payload["rendererComponentType"] = ""
    payload["rendererComponentIndex"] = -1
    payload["slotIndex"] = -1

    canonical, _preview = bind_authoritative_preview(wrapper, payload)

    assert canonical["arguments"]["materialAssetPath"] == "Assets/Avatar/Body.mat"
    assert "rendererPath" not in canonical["arguments"]
    assert "slotIndex" not in canonical["arguments"]


@pytest.mark.parametrize(
    "change",
    [
        {"rendererPath": "Other/Renderer"},
        {"slotIndex": 2},
        {"rendererComponentId": "1" * 64},
        {"shaderAssetPath": "Assets/Shaders/Other.shader"},
    ],
)
def test_authoritative_preview_rejects_selector_or_explicit_shader_path_substitution(change: dict) -> None:
    wrapper = wrapper_arguments()
    wrapper["arguments"].update(change)

    with pytest.raises(MaterialShaderAssignmentError):
        bind_authoritative_preview(wrapper, preview_payload())


@pytest.mark.parametrize(
    ("caller_key", "caller_value", "receipt_key", "receipt_value"),
    [
        ("rendererPath", "Avatar/Straße", "rendererPath", "Avatar/Strasse"),
        ("materialAssetPath", "Assets/Straße.mat", "materialAssetPath", "Assets/Strasse.mat"),
        (
            "shaderAssetPath",
            "Assets/Shaders/Straße.shader",
            "shaderAssetPath",
            "Assets/Shaders/Strasse.shader",
        ),
    ],
)
def test_selector_binding_does_not_use_broad_unicode_case_folding(
    caller_key: str,
    caller_value: str,
    receipt_key: str,
    receipt_value: str,
) -> None:
    wrapper = wrapper_arguments()
    payload = preview_payload()
    if caller_key == "materialAssetPath":
        wrapper["arguments"].pop("rendererPath")
        wrapper["arguments"].pop("slotIndex")
        wrapper["arguments"][caller_key] = caller_value
        payload["rendererPath"] = ""
        payload["rendererScenePath"] = ""
        payload["rendererSceneGuid"] = ""
        payload["rendererSceneHandle"] = -1
        payload["rendererComponentId"] = ""
        payload["rendererComponentType"] = ""
        payload["rendererComponentIndex"] = -1
        payload["slotIndex"] = -1
    else:
        wrapper["arguments"][caller_key] = caller_value
    payload[receipt_key] = receipt_value

    with pytest.raises(MaterialShaderAssignmentError, match="changed the requested"):
        bind_authoritative_preview(wrapper, payload)


def test_same_named_distinct_shader_assets_remain_a_real_change() -> None:
    payload = preview_payload()
    payload["beforeShader"] = "Project/Toon"
    payload["beforeShaderAssetPath"] = "Assets/Shaders/OldToon.shader"
    payload["beforeShaderAssetGuid"] = "9" * 32
    payload["wouldChange"] = True

    canonical, preview = bind_authoritative_preview(wrapper_arguments(), payload)

    assert preview["change"]["wouldChange"] is True
    assert canonical["arguments"]["expectedBeforeShaderAssetGuid"] == "9" * 32


def test_truncated_impact_requires_the_full_display_window() -> None:
    payload = preview_payload()
    impact = payload["sharedImpact"]
    impact["loadedRendererSlotCount"] = 129
    impact["listsTruncated"] = True
    refresh_impact_receipts(payload, tail_slots=[impact["loadedRendererSlots"][0]])

    with pytest.raises(MaterialShaderAssignmentError, match="length is inconsistent"):
        bind_authoritative_preview(wrapper_arguments(), payload)


def test_truncated_impact_binds_both_full_state_and_visible_display() -> None:
    payload = preview_payload()
    impact = payload["sharedImpact"]
    first = impact["loadedRendererSlots"][0]
    impact["loadedRendererSlots"] = [
        first,
        *[
            {
                "scenePath": "Assets/Scenes/Avatar.unity",
                "sceneGuid": "e" * 32,
                "sceneHandle": 7,
                "rendererPath": f"Avatar/Body{index:03d}",
                "rendererComponentId": f"{index + 1:064x}",
                "rendererComponentType": "UnityEngine.SkinnedMeshRenderer",
                "rendererComponentIndex": 0,
                "slotIndex": 1,
            }
            for index in range(127)
        ],
    ]
    impact["loadedRendererSlotCount"] = 129
    impact["listsTruncated"] = True
    tail_slot = {
        "scenePath": "Assets/Scenes/Avatar.unity",
        "sceneGuid": "e" * 32,
        "sceneHandle": 7,
        "rendererPath": "Avatar/Tail",
        "rendererComponentId": "f" * 64,
        "rendererComponentType": "UnityEngine.SkinnedMeshRenderer",
        "rendererComponentIndex": 0,
        "slotIndex": 1,
    }
    refresh_impact_receipts(payload, tail_slots=[tail_slot])

    canonical, preview = bind_authoritative_preview(wrapper_arguments(), payload)

    assert canonical["arguments"]["expectedSharedImpactDigest"] == payload["sharedImpactDigest"]
    assert preview["sharedImpact"]["listsTruncated"] is True
    assert len(preview["sharedImpact"]["loadedRendererSlots"]) == 128

    tampered = deepcopy(payload)
    tampered["sharedImpact"]["loadedRendererSlots"][0]["rendererPath"] = "Avatar/A"
    with pytest.raises(MaterialShaderAssignmentError, match="display digest"):
        bind_authoritative_preview(wrapper_arguments(), tampered)


def test_truncated_impact_rejects_recomputed_display_with_stale_commitment() -> None:
    payload = preview_payload()
    impact = payload["sharedImpact"]
    impact["loadedRendererSlotCount"] = 129
    impact["loadedRendererSlots"] = [
        {
            "scenePath": "Assets/Scenes/Avatar.unity",
            "sceneGuid": "e" * 32,
            "sceneHandle": 7,
            "rendererPath": f"Avatar/Body{index:03d}",
            "rendererComponentId": f"{index + 1:064x}",
            "rendererComponentType": "UnityEngine.SkinnedMeshRenderer",
            "rendererComponentIndex": 0,
            "slotIndex": 1,
        }
        for index in range(128)
    ]
    impact["listsTruncated"] = True
    refresh_impact_receipts(payload, tail_slots=[deepcopy(impact["loadedRendererSlots"][0])])
    stale_commitment = payload["sharedImpactDigest"]
    impact["loadedRendererSlots"][0]["rendererPath"] = "Avatar/Body000Changed"
    impact["loadedRendererSlots"].sort(key=lambda item: item["rendererPath"].encode("utf-16-be"))
    payload["sharedImpactDisplayDigest"] = compute_shared_impact_digest(impact)
    payload["sharedImpactDigest"] = stale_commitment

    with pytest.raises(MaterialShaderAssignmentError, match="commitment"):
        bind_authoritative_preview(wrapper_arguments(), payload)


def test_impact_slot_order_uses_utf16_ordinal_semantics() -> None:
    payload = preview_payload()
    impact = payload["sharedImpact"]
    impact["loadedRendererSlotCount"] = 2
    impact["loadedRendererSlots"] = [
        {**impact["loadedRendererSlots"][0], "rendererPath": "Avatar/A"},
        {**impact["loadedRendererSlots"][0], "rendererPath": "Avatar/["},
    ]
    refresh_impact_receipts(payload)

    bind_authoritative_preview(wrapper_arguments(), payload)

    reversed_payload = deepcopy(payload)
    reversed_payload["sharedImpact"]["loadedRendererSlots"].reverse()
    refresh_impact_receipts(reversed_payload)
    with pytest.raises(MaterialShaderAssignmentError, match="sorted and unique"):
        bind_authoritative_preview(wrapper_arguments(), reversed_payload)


def test_impact_distinguishes_same_path_renderer_components_by_stable_or_session_identity() -> None:
    payload = preview_payload()
    impact = payload["sharedImpact"]
    first = impact["loadedRendererSlots"][0]
    impact["loadedRendererSlotCount"] = 2
    impact["loadedRendererSlots"] = [
        {**first, "rendererComponentId": "1" * 64},
        {**first, "rendererComponentId": "2" * 64},
    ]
    refresh_impact_receipts(payload)

    bind_authoritative_preview(wrapper_arguments(), payload)

    duplicate = deepcopy(payload)
    duplicate["sharedImpact"]["loadedRendererSlots"][1] = deepcopy(
        duplicate["sharedImpact"]["loadedRendererSlots"][0]
    )
    refresh_impact_receipts(duplicate)
    with pytest.raises(MaterialShaderAssignmentError, match="sorted and unique"):
        bind_authoritative_preview(wrapper_arguments(), duplicate)


def test_gateway_replaces_caller_preview_with_live_verified_receipt(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.register_write_handler(
        "vrcforge_unity_mcp_write",
        "Unity write",
        "high",
        lambda params: {"ok": True, "params": params},
        request_preparer=dashboard_server.prepare_unity_mcp_write_request,
    )
    result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": preview_payload()},
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=result) as invoke,
    ):
        requested = gateway.create_apply_request(
            {
                "target_tool": "vrcforge_unity_mcp_write",
                "arguments": wrapper_arguments(str(project)),
                "preview": {"spoofed": True},
            }
        )

    approval = requested["approval"]
    stored_approval = gateway._approvals[approval["id"]]
    nested = stored_approval["arguments"]["arguments"]
    assert approval["preview"]["schema"] == APPROVAL_PREVIEW_SCHEMA
    assert "spoofed" not in approval["preview"]
    assert nested["expectedMaterialAssetGuid"] == "a" * 32
    assert nested["expectedSharedImpactDigest"] == stored_approval["preview"]["sharedImpactDigest"]
    assert nested["expectedProjectPath"] == str(project.resolve())
    assert stored_approval["arguments"]["projectPath"] == str(project.resolve())
    preview_call = invoke.call_args.args[2]
    assert preview_call["preview"] is True
    assert "expectedMaterialAssetGuid" not in preview_call


def test_gateway_preview_failure_creates_no_approval(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    gateway.register_write_handler(
        "vrcforge_unity_mcp_write",
        "Unity write",
        "high",
        lambda params: {"ok": True, "params": params},
        request_preparer=dashboard_server.prepare_unity_mcp_write_request,
    )
    result = dashboard_server.McpResult(exit_code=1, stdout="sensitive", stderr="sensitive", payload=None)

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=result),
        pytest.raises(AgentGatewayError, match="could not be verified"),
    ):
        gateway.create_apply_request(
            {
                "target_tool": "vrcforge_unity_mcp_write",
                "arguments": wrapper_arguments(str(project)),
                "preview": {"spoofed": True},
            }
        )

    assert gateway._approvals == {}


def test_gateway_requires_an_existing_absolute_unity_project(tmp_path: Path) -> None:
    wrapper = wrapper_arguments(str(tmp_path / "missing"))

    with pytest.raises(AgentGatewayError, match="accessible Unity project"):
        dashboard_server.prepare_unity_mcp_write_request(wrapper, None)


def test_bridge_failure_does_not_expose_raw_preview_details(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    wrapper = wrapper_arguments(str(project))

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch(
            "dashboard_server.invoke_unity_mcp",
            side_effect=RuntimeError("credential at C:/private/project"),
        ),
        pytest.raises(AgentGatewayError) as captured,
    ):
        dashboard_server.prepare_unity_mcp_write_request(wrapper, None)

    assert str(captured.value) == "Material shader preview could not be verified against the current project."
    assert "private" not in str(captured.value)


def test_non_material_unity_write_keeps_existing_request_semantics() -> None:
    arguments = {"toolName": "vrc_apply_blendshapes", "arguments": {"preview": False}}
    preview = {"summary": "existing preview"}

    prepared_arguments, prepared_preview = dashboard_server.prepare_unity_mcp_write_request(
        deepcopy(arguments),
        deepcopy(preview),
    )

    assert prepared_arguments == arguments
    assert prepared_preview == preview


def test_registered_plan_tool_returns_the_same_authoritative_preview() -> None:
    project = Path.cwd()
    result = dashboard_server.McpResult(
        exit_code=0,
        stdout="",
        stderr="",
        payload={"data": preview_payload()},
    )

    with (
        patch("dashboard_server.load_dashboard_settings"),
        patch("dashboard_server.invoke_unity_mcp", return_value=result) as invoke,
    ):
        preview = dashboard_server.preview_material_shader_assignment_sync(
            {
                "projectPath": str(project),
                "rendererPath": "Body",
                "slotIndex": 1,
                "shaderName": "Project/Toon",
            }
        )

    assert preview["ok"] is True
    assert preview["preview"]["schema"] == APPROVAL_PREVIEW_SCHEMA
    assert invoke.call_args.args[1] == "vrc_set_material_shader"
    assert invoke.call_args.args[2]["preview"] is True
    assert "vrc_set_material_shader" in dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS
    assert "vrcforge_preview_material_shader_assignment" in dashboard_server.AGENT_GATEWAY._tools

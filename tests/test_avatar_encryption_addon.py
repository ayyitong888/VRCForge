from __future__ import annotations

from fastapi.testclient import TestClient

import dashboard_server


def make_encryption_inventory() -> dict:
    return {
        "type": "material_inventory_snapshot",
        "version": "0.2",
        "materials": [
            {
                "material_id": "mat_liltoon_body",
                "avatar_path": "Scene/HeroAvatar",
                "renderer_path": "Scene/HeroAvatar/Body",
                "renderer_id": "renderer_body",
                "mesh_name": "BodyMesh",
                "slot_index": 0,
                "material_name": "Body_lilToon",
                "shader_name": "lilToon",
                "shader_family": "lilToon",
            },
            {
                "material_id": "mat_poiyomi_jacket",
                "avatar_path": "Scene/HeroAvatar",
                "renderer_path": "Scene/HeroAvatar/Jacket",
                "renderer_id": "renderer_jacket",
                "mesh_name": "JacketMesh",
                "slot_index": 0,
                "material_name": "Jacket_Poi",
                "shader_name": ".poiyomi/Poiyomi Toon",
                "shader_family": "Poiyomi",
            },
            {
                "material_id": "mat_generic_hat",
                "avatar_path": "Scene/HeroAvatar",
                "renderer_path": "Scene/HeroAvatar/Hat",
                "renderer_id": "renderer_hat",
                "mesh_name": "HatMesh",
                "slot_index": 0,
                "material_name": "Hat_Generic",
                "shader_name": "Generic",
                "shader_family": "Generic",
            },
            {
                "material_id": "mat_unknown",
                "avatar_path": "Scene/HeroAvatar",
                "renderer_path": "Scene/HeroAvatar/Accessory",
                "renderer_id": "renderer_accessory",
                "mesh_name": "AccessoryMesh",
                "slot_index": 0,
                "material_name": "Accessory_Arktoon",
                "shader_name": "Arktoon",
                "shader_family": "Arktoon",
            },
        ],
        "summary": {"materialCount": 4},
    }


def test_avatar_encryption_scan_prioritizes_liltoon_then_poiyomi_and_blocks_others() -> None:
    payload = dashboard_server.scan_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionScanRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
        )
    )

    assert payload["schema"] == "vrcforge.avatar_encryption.v1"
    assert payload["readOnly"] is True
    assert payload["summary"]["candidateCount"] == 2
    assert payload["summary"]["lilToonCandidateCount"] == 1
    assert payload["summary"]["poiyomiCandidateCount"] == 1
    assert payload["summary"]["compatibilityOnlyCount"] == 2

    targets = payload["targets"]
    assert [item["shaderFamilyId"] for item in targets[:2]] == ["liltoon", "poiyomi"]
    assert targets[0]["supportLevel"] == "first_class"
    assert targets[1]["supportLevel"] == "first_class"
    blocked = {item["materialId"]: item for item in targets if item["supportLevel"] == "compatibility_only"}
    assert blocked["mat_generic_hat"]["status"] == "blocked"
    assert "shader_family.restore_adapter_missing" in blocked["mat_generic_hat"]["blockers"]
    assert blocked["mat_unknown"]["status"] == "blocked"


def test_avatar_encryption_plan_is_preview_only_and_records_key_limitations() -> None:
    payload = dashboard_server.plan_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPlanRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            confirm_creator_owned_assets=True,
        )
    )
    plan = payload["plan"]

    assert plan["readOnly"] is True
    assert plan["writeStatus"] == "blocked"
    assert plan["selectedCandidateCount"] == 2
    assert plan["targetShaderFamilies"] == ["liltoon", "poiyomi"]
    assert plan["keyChannel"]["id"] == "avatar_parameter_32bit"
    assert "cannot carry a full AES-256 secret" in plan["keyChannel"]["warning"]
    assert plan["futureRequestTools"]["status"] == "not_registered_in_1.0.1"
    assert any("rollback" in item.lower() for item in plan["proofRequirements"])


def test_avatar_encryption_preview_does_not_write_and_includes_rollback_policy() -> None:
    payload = dashboard_server.preview_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPreviewRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            confirm_creator_owned_assets=True,
        )
    )

    assert payload["previewOnly"] is True
    assert payload["writeAllowed"] is False
    assert payload["wouldWrite"] is False
    assert payload["blockedApply"]["status"] == "blocked"
    assert payload["rollbackPolicyPreview"]["requiresCheckpoint"] is True
    assert payload["rollbackPolicyPreview"]["removeMustRestoreOriginalMeshesAndMaterials"] is True
    assert payload["writeTargetsPreview"]
    assert all(item["wouldModifyOriginalAsset"] is False for item in payload["writeTargetsPreview"])


def test_avatar_encryption_rest_endpoints_accept_inventory_without_unity_writes() -> None:
    with TestClient(dashboard_server.app) as client:
        response = client.post(
            "/api/avatar-encryption/plan",
            json={
                "avatarPath": "Scene/HeroAvatar",
                "inventory": make_encryption_inventory(),
                "confirmCreatorOwnedAssets": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["writeStatus"] == "blocked"
    assert body["plan"]["selectedCandidateCount"] == 2


def test_avatar_encryption_tools_are_projected_without_write_targets() -> None:
    manifest = dashboard_server.AGENT_GATEWAY.build_manifest()
    tool_names = {tool["name"] for tool in manifest["tools"]}
    write_targets = {target["name"] for target in manifest["writeTargets"]}

    expected = {
        "vrcforge_avatar_encryption_research_report",
        "vrcforge_avatar_encryption_scan",
        "vrcforge_avatar_encryption_plan",
        "vrcforge_avatar_encryption_preview",
    }
    assert expected <= tool_names
    assert not expected & write_targets
    assert not any("avatar_encryption" in name and name.endswith("_apply_request") for name in tool_names)

    skills = dashboard_server.AGENT_GATEWAY.build_skill_registry()["skills"]
    group = next(skill for skill in skills if skill["name"] == "avatar-encryption-addon-preview")
    assert group["permissionMode"] == "preview"
    assert group["riskLevel"] == "low"
    assert expected <= set(group["allowedTools"])
    assert "vrcforge_request_apply" not in group["allowedTools"]
    assert "vrcforge_avatar_encryption_liltoon_apply_request" in group["disallowedTools"]

    registry = dashboard_server.AGENT_GATEWAY.build_tool_registry()
    entries = [entry for entry in registry["tools"] if entry["name"].startswith("vrcforge_avatar_encryption")]
    assert {entry["category"] for entry in entries} == {"avatar-encryption"}
    assert {entry["risk"] for entry in entries} <= {"read_only", "plan"}
    assert all(entry["requiresApproval"] is False for entry in entries)

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import agent_gateway
import dashboard_server


@pytest.fixture(autouse=True)
def force_gateway_approval_mode():
    config = dashboard_server.AGENT_GATEWAY.ensure_config()
    original_mode = config.execution_mode
    config.execution_mode = "approval"
    dashboard_server.AGENT_GATEWAY.save_config(config)
    dashboard_server.AGENT_GATEWAY._approvals.clear()
    try:
        yield
    finally:
        config = dashboard_server.AGENT_GATEWAY.ensure_config()
        config.execution_mode = original_mode
        dashboard_server.AGENT_GATEWAY.save_config(config)
        dashboard_server.AGENT_GATEWAY._approvals.clear()


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


def test_avatar_encryption_plan_blocks_until_private_addon_is_configured() -> None:
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
    assert plan["profile"]["id"] == "standard"
    assert plan["recommendedProfile"] == "standard"
    assert [card["id"] for card in plan["profileCards"]] == ["lite", "standard", "paranoid"]
    assert plan["profileCards"][1]["icon"] == "shield"
    assert len(plan["benchmarkTable"]) == 9
    assert {row["triangles"] for row in plan["benchmarkTable"]} == {50_000, 100_000, 200_000}
    assert {row["avatarScale"] for row in plan["benchmarkTable"]} == {"50k triangles", "100k triangles", "200k triangles"}
    assert plan["benchmarkAssumptions"]["kind"] == "estimated_static_profile_budget"
    assert plan["externalAddon"]["configured"] is False
    assert "addon.private_module_not_configured" in plan["hardGate"]["blockingIds"]
    assert plan["layers"] == [{"id": "profile_managed", "label": "Standard", "status": "managed_by_private_addon"}]
    assert plan["dynamicTimePolicy"]["mode"] == "private_addon_managed"
    assert "omitted" in plan["dynamicTimePolicy"]["details"]
    assert plan["futureRequestTools"]["status"] == "registered_request_only_private_addon_connector"
    assert all(capability["registered"] is True for capability in plan["futureCapabilities"])
    assert any("rollback" in item.lower() for item in plan["proofRequirements"])


def test_avatar_encryption_plan_is_request_ready_when_private_addon_is_configured(monkeypatch) -> None:
    monkeypatch.setenv(dashboard_server.AVATAR_ENCRYPTION_ADDON_URL_ENV, "http://127.0.0.1:9876")
    payload = dashboard_server.plan_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPlanRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            profile="standard",
            platform="windows",
            confirm_creator_owned_assets=True,
        )
    )
    plan = payload["plan"]

    assert plan["status"] == "request_ready"
    assert plan["writeStatus"] == "approval_request_available"
    assert plan["externalAddon"]["configured"] is True
    assert plan["profile"]["id"] == "standard"
    assert plan["layers"][0]["id"] == "profile_managed"
    assert plan["dynamicTimePolicy"]["mode"] == "private_addon_managed"
    assert plan["platform"]["status"] == "supported"


@pytest.mark.parametrize("profile", ["lite", "standard", "paranoid"])
def test_avatar_encryption_all_profiles_block_quest(profile: str) -> None:
    payload = dashboard_server.plan_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPlanRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            profile=profile,
            platform="quest",
            confirm_creator_owned_assets=True,
        )
    )
    plan = payload["plan"]

    assert plan["status"] == "blocked"
    assert plan["platform"]["status"] == "blocked"
    assert "platform.windows_only" in plan["hardGate"]["blockingIds"]
    assert "Windows PC-only" in plan["platform"]["reason"]


@pytest.mark.parametrize("field", ["platform", "targetPlatform"])
def test_avatar_encryption_apply_request_blocks_mobile_platform_aliases(field: str) -> None:
    payload = dashboard_server.request_avatar_encryption_apply_sync(
        {
            "avatarPath": "Scene/HeroAvatar",
            "inventory": make_encryption_inventory(),
            field: "quest",
            "confirmCreatorOwnedAssets": True,
        },
        target_family="liltoon",
        agent_name="unit-test",
    )

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert "platform.windows_only" in payload["error"]


def test_avatar_encryption_paranoid_profile_reports_blocked_proof_gate() -> None:
    payload = dashboard_server.plan_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPlanRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            protectionProfile="paranoid",
            confirm_creator_owned_assets=True,
        )
    )
    plan = payload["plan"]

    assert plan["profile"]["id"] == "paranoid"
    assert plan["profile"]["applyStatus"] == "blocked_until_blendshape_proof"
    assert plan["layers"][0]["id"] == "profile_managed"
    assert plan["layers"][0]["status"] == "managed_by_private_addon"
    assert "profile.paranoid_blendshape_proof_required" in plan["hardGate"]["blockingIds"]


def test_avatar_encryption_quest_profile_alias_does_not_enable_mobile_path() -> None:
    payload = dashboard_server.plan_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPlanRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            profile="quest",
            confirm_creator_owned_assets=True,
        )
    )

    assert payload["plan"]["profile"]["id"] == "standard"
    assert payload["plan"]["platform"]["status"] == "supported"


def test_avatar_encryption_public_plan_ignores_implementation_layer_inputs() -> None:
    payload = dashboard_server.plan_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPlanRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            profile="standard",
            confirm_creator_owned_assets=True,
        )
    )

    assert payload["plan"]["status"] == "blocked"
    assert "profile.custom_layers_not_supported" not in payload["plan"]["hardGate"]["blockingIds"]
    assert payload["plan"]["layers"][0]["id"] == "profile_managed"


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
    assert payload["applyRequestReady"] is False
    assert payload["blockedApply"]["status"] == "blocked"
    assert payload["rollbackPolicyPreview"]["requiresCheckpoint"] is True
    assert payload["rollbackPolicyPreview"]["removeMustRestoreOriginalMeshesAndMaterials"] is True
    assert payload["writeTargetsPreview"]
    assert all(item["wouldModifyOriginalAsset"] is False for item in payload["writeTargetsPreview"])
    assert all(item["adapterId"] in {"liltoon", "poiyomi"} for item in payload["writeTargetsPreview"])


def test_avatar_encryption_rest_scan_uses_camelcase_avatar_path(monkeypatch) -> None:
    seen: list[str] = []

    def fake_scan(_settings, avatar_path: str | None) -> dict:
        seen.append(str(avatar_path or ""))
        return make_encryption_inventory()

    monkeypatch.setattr(dashboard_server, "scan_shader_materials_direct", fake_scan)
    with TestClient(dashboard_server.app) as client:
        response = client.post("/api/avatar-encryption/scan", json={"avatarPath": "Scene/RequestedAvatar"})

    assert response.status_code == 200
    assert seen == ["Scene/RequestedAvatar"]
    assert response.json()["avatarPath"] == "Scene/RequestedAvatar"


def test_avatar_encryption_preview_blocks_standalone_caller_supplied_plan() -> None:
    payload = dashboard_server.preview_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPreviewRequest(
            plan={
                "selectedCandidates": [
                    {
                        "materialId": "mat_generic",
                        "rendererPath": "Avatar/Hat",
                        "materialName": "Hat",
                        "shaderFamilyId": "generic",
                        "shaderFamily": "Generic",
                        "status": "blocked",
                    },
                    {
                        "materialId": "mat_liltoon_body",
                        "rendererPath": "Avatar/Body",
                        "materialName": "Body",
                        "shaderFamilyId": "liltoon",
                        "shaderFamily": "lilToon",
                        "status": "candidate",
                    },
                ]
            }
        )
    )

    assert payload["applyRequestReady"] is False
    assert payload["writeTargetsPreview"] == []
    assert payload["plan"]["status"] == "blocked"
    assert "plan.untrusted_external_plan" in payload["plan"]["hardGate"]["blockingIds"]


def test_avatar_encryption_preview_rebuilds_forged_quest_plan_from_inventory() -> None:
    payload = dashboard_server.preview_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPreviewRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            platform="quest",
            confirm_creator_owned_assets=True,
            plan={
                "status": "request_ready",
                "hardGate": {"status": "request_ready", "blockingIds": []},
                "selectedCandidates": [
                    {
                        "materialId": "fake",
                        "rendererPath": "Scene/HeroAvatar/Fake",
                        "materialName": "Fake",
                        "shaderFamilyId": "liltoon",
                        "shaderFamily": "lilToon",
                        "status": "candidate",
                    }
                ],
            },
        )
    )

    assert payload["applyRequestReady"] is False
    assert payload["writeTargetsPreview"]
    assert payload["plan"]["platform"]["status"] == "blocked"
    assert "platform.windows_only" in payload["plan"]["hardGate"]["blockingIds"]
    assert {item["materialId"] for item in payload["writeTargetsPreview"]} == {
        "mat_liltoon_body",
        "mat_poiyomi_jacket",
    }


def test_avatar_encryption_plan_blocks_requested_unsupported_family_without_fallback() -> None:
    payload = dashboard_server.plan_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPlanRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            targetShaderFamilies=["Arktoon"],
            confirmCreatorOwnedAssets=True,
        )
    )
    plan = payload["plan"]

    assert plan["status"] == "blocked"
    assert plan["selectedCandidateCount"] == 0
    assert plan["targetShaderFamilies"] == ["unsupported"]
    assert "shader_family.requested_restore_adapter_missing" in plan["hardGate"]["blockingIds"]


def test_avatar_encryption_plan_filters_external_target_selection(monkeypatch) -> None:
    monkeypatch.setenv(dashboard_server.AVATAR_ENCRYPTION_ADDON_URL_ENV, "http://127.0.0.1:9876")
    payload = dashboard_server.plan_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPlanRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            materialIds=["mat_poiyomi_jacket"],
            confirmCreatorOwnedAssets=True,
        )
    )
    plan = payload["plan"]

    assert plan["status"] == "request_ready"
    assert plan["selectedCandidateCount"] == 1
    assert plan["selectedCandidates"][0]["materialId"] == "mat_poiyomi_jacket"


def test_avatar_encryption_plan_blocks_missing_external_target_selection() -> None:
    payload = dashboard_server.plan_avatar_encryption_sync(
        dashboard_server.AvatarEncryptionPlanRequest(
            avatar_path="Scene/HeroAvatar",
            inventory=make_encryption_inventory(),
            rendererPaths=["Scene/HeroAvatar/MissingRenderer"],
            confirmCreatorOwnedAssets=True,
        )
    )
    plan = payload["plan"]

    assert plan["status"] == "blocked"
    assert "targets.requested_targets_not_found" in plan["hardGate"]["blockingIds"]


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
    assert "addon.private_module_not_configured" in body["plan"]["hardGate"]["blockingIds"]
    assert body["plan"]["selectedCandidateCount"] == 2


def test_avatar_encryption_tools_are_projected_as_request_only_writes() -> None:
    manifest = dashboard_server.AGENT_GATEWAY.build_manifest()
    tool_names = {tool["name"] for tool in manifest["tools"]}
    write_targets = {target["name"] for target in manifest["writeTargets"]}

    read_expected = {
        "vrcforge_avatar_encryption_research_report",
        "vrcforge_avatar_encryption_scan",
        "vrcforge_avatar_encryption_plan",
        "vrcforge_avatar_encryption_preview",
        "vrcforge_avatar_encryption_addon_status",
    }
    request_expected = {
        "vrcforge_avatar_encryption_liltoon_apply_request",
        "vrcforge_avatar_encryption_poiyomi_apply_request",
        "vrcforge_avatar_encryption_remove_request",
    }
    assert read_expected <= tool_names
    assert request_expected <= tool_names
    assert not read_expected & write_targets
    assert dashboard_server.AVATAR_ENCRYPTION_ADDON_APPLY_TOOL not in tool_names
    assert dashboard_server.AVATAR_ENCRYPTION_ADDON_REMOVE_TOOL not in tool_names
    assert dashboard_server.AVATAR_ENCRYPTION_ADDON_APPLY_TOOL not in write_targets
    assert dashboard_server.AVATAR_ENCRYPTION_ADDON_REMOVE_TOOL not in write_targets

    skills = dashboard_server.AGENT_GATEWAY.build_skill_registry()["skills"]
    groups = {skill["name"]: skill for skill in skills if skill["source"] == "builtin" and skill["skillType"] == "group"}
    expected_groups = {
        "avatar-encryption-research-scan",
        "avatar-encryption-plan-preview",
        "avatar-encryption-liltoon-apply-request",
        "avatar-encryption-poiyomi-apply-request",
        "avatar-encryption-remove-rollback",
    }
    assert expected_groups <= set(groups)
    assert groups["avatar-encryption-research-scan"]["permissionMode"] == "read_only"
    assert groups["avatar-encryption-plan-preview"]["permissionMode"] == "preview"
    assert groups["avatar-encryption-liltoon-apply-request"]["permissionMode"] == "approval_required"
    assert groups["avatar-encryption-poiyomi-apply-request"]["permissionMode"] == "approval_required"
    assert groups["avatar-encryption-remove-rollback"]["permissionMode"] == "approval_required"
    assert set(agent_gateway.AVATAR_ENCRYPTION_READ_TOOL_NAMES) <= set(groups["avatar-encryption-research-scan"]["allowedTools"])
    assert set(agent_gateway.AVATAR_ENCRYPTION_PLAN_TOOL_NAMES) <= set(groups["avatar-encryption-plan-preview"]["allowedTools"])
    assert set(agent_gateway.AVATAR_ENCRYPTION_STATUS_TOOL_NAMES) <= set(groups["avatar-encryption-research-scan"]["allowedTools"])
    assert "vrcforge_avatar_encryption_liltoon_apply_request" in groups["avatar-encryption-liltoon-apply-request"]["allowedTools"]
    assert "vrcforge_avatar_encryption_poiyomi_apply_request" in groups["avatar-encryption-poiyomi-apply-request"]["allowedTools"]
    assert "vrcforge_avatar_encryption_remove_request" in groups["avatar-encryption-remove-rollback"]["allowedTools"]
    assert dashboard_server.AVATAR_ENCRYPTION_ADDON_APPLY_TOOL in groups["avatar-encryption-liltoon-apply-request"]["disallowedTools"]
    assert dashboard_server.AVATAR_ENCRYPTION_ADDON_REMOVE_TOOL in groups["avatar-encryption-remove-rollback"]["disallowedTools"]
    skill_names = {skill["name"] for skill in skills}
    assert dashboard_server.AVATAR_ENCRYPTION_ADDON_APPLY_TOOL not in skill_names
    assert dashboard_server.AVATAR_ENCRYPTION_ADDON_REMOVE_TOOL not in skill_names

    registry = dashboard_server.AGENT_GATEWAY.build_tool_registry()
    entries = [entry for entry in registry["tools"] if entry["name"].startswith("vrcforge_avatar_encryption")]
    assert {entry["category"] for entry in entries} == {"avatar-encryption"}
    risk_by_name = {entry["name"]: entry["risk"] for entry in entries}
    assert {risk_by_name[name] for name in read_expected} <= {"read_only", "plan"}
    assert {risk_by_name[name] for name in request_expected} == {"write_request"}
    approval_by_name = {entry["name"]: entry["requiresApproval"] for entry in entries}
    assert all(approval_by_name[name] is False for name in read_expected)
    assert all(approval_by_name[name] is True for name in request_expected)
    assert set(agent_gateway.AVATAR_ENCRYPTION_TOOL_NAMES) == read_expected | request_expected


def test_avatar_encryption_apply_request_blocks_without_private_addon() -> None:
    dashboard_server.AGENT_GATEWAY._approvals.clear()
    payload = dashboard_server.request_avatar_encryption_apply_sync(
        {
            "avatarPath": "Scene/HeroAvatar",
            "projectPath": "E:/unity/Hero",
            "inventory": make_encryption_inventory(),
            "confirmCreatorOwnedAssets": True,
        },
        target_family="liltoon",
        agent_name="unit-test",
    )

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert "addon.private_module_not_configured" in payload["error"]


def test_avatar_encryption_liltoon_apply_request_creates_explicit_connector_approval(monkeypatch) -> None:
    monkeypatch.setenv(dashboard_server.AVATAR_ENCRYPTION_ADDON_URL_ENV, "http://127.0.0.1:9876")
    dashboard_server.AGENT_GATEWAY._approvals.clear()
    payload = dashboard_server.request_avatar_encryption_apply_sync(
        {
            "avatarPath": "Scene/HeroAvatar",
            "projectPath": "E:/unity/Hero",
            "inventory": make_encryption_inventory(),
            "confirmCreatorOwnedAssets": True,
        },
        target_family="liltoon",
        agent_name="unit-test",
    )

    assert payload["ok"] is True
    assert payload["status"] == "pending"
    approval = payload["approval"]
    assert approval["targetTool"] == dashboard_server.AVATAR_ENCRYPTION_ADDON_APPLY_TOOL
    assert approval["requiresExplicitApproval"] is True
    assert approval["arguments"]["targetShaderFamily"] == "liltoon"
    assert approval["arguments"]["targets"] == {"type": "list", "count": 1}
    stored_approval = dashboard_server.AGENT_GATEWAY._approvals[approval["id"]]
    assert stored_approval["arguments"]["targetShaderFamily"] == "liltoon"
    assert stored_approval["arguments"]["projectPath"] == "E:/unity/Hero"
    assert stored_approval["arguments"]["profile"] == "standard"
    assert stored_approval["arguments"]["platform"] == "pc"
    assert stored_approval["arguments"]["targetPlatform"] == "pc"
    assert stored_approval["arguments"]["targets"][0]["shaderFamilyId"] == "liltoon"
    assert stored_approval["arguments"]["connectorContract"] == "private-addon-rest-v1"
    assert "layers" not in stored_approval["arguments"]
    assert "keyChannel" not in stored_approval["arguments"]
    assert "checkpoint" in str(approval["preview"]["rollback"]).lower()


def test_avatar_encryption_apply_request_blocks_output_folder_escape(monkeypatch) -> None:
    monkeypatch.setenv(dashboard_server.AVATAR_ENCRYPTION_ADDON_URL_ENV, "http://127.0.0.1:9876")
    dashboard_server.AGENT_GATEWAY._approvals.clear()
    payload = dashboard_server.request_avatar_encryption_apply_sync(
        {
            "avatarPath": "Scene/HeroAvatar",
            "projectPath": "E:/unity/Hero",
            "inventory": make_encryption_inventory(),
            "outputFolder": "../Outside",
            "confirmCreatorOwnedAssets": True,
        },
        target_family="liltoon",
        agent_name="unit-test",
    )

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert "outputFolder" in payload["error"]


def test_avatar_encryption_apply_request_keeps_long_constraints_out_of_write_arguments(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(dashboard_server.AVATAR_ENCRYPTION_ADDON_URL_ENV, "http://127.0.0.1:9876")
    constraints_path = tmp_path / "AGENTS.md"
    constraints_body = "creator-owned assets only\n" * 500
    constraints_path.write_text(constraints_body, encoding="utf-8")
    monkeypatch.setattr(
        agent_gateway.AgentGateway,
        "user_constraints_path",
        property(lambda _self: constraints_path),
    )
    dashboard_server.AGENT_GATEWAY._approvals.clear()

    payload = dashboard_server.request_avatar_encryption_apply_sync(
        {
            "avatarPath": "Scene/HeroAvatar",
            "projectPath": "E:/unity/Hero",
            "inventory": make_encryption_inventory(),
            "confirmCreatorOwnedAssets": True,
        },
        target_family="liltoon",
        agent_name="unit-test",
    )

    approval = dashboard_server.AGENT_GATEWAY._approvals[payload["approval"]["id"]]
    arguments_text = json.dumps(approval["arguments"], ensure_ascii=False)
    constraints_meta = approval["arguments"]["_vrcforge_user_constraints"]
    assert approval["userConstraintsApplied"] is True
    assert constraints_meta["contentInline"] is False
    assert constraints_meta["contentRedacted"] is True
    assert constraints_meta["contentLength"] == len(constraints_body.strip())
    assert "content" not in constraints_meta
    assert "userConstraints" not in approval["arguments"]
    assert "user_constraints" not in approval["arguments"]
    assert constraints_body not in arguments_text


def test_avatar_encryption_remove_request_requires_manifest_or_avatar_target() -> None:
    payload = dashboard_server.request_avatar_encryption_remove_sync(
        {
            "confirmRemove": True,
        },
        agent_name="unit-test",
    )

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert "manifestPath or avatarPath" in payload["error"]


def test_avatar_encryption_remove_request_blocks_manifest_outside_project(monkeypatch) -> None:
    monkeypatch.setenv(dashboard_server.AVATAR_ENCRYPTION_ADDON_URL_ENV, "http://127.0.0.1:9876")
    dashboard_server.AGENT_GATEWAY._approvals.clear()
    payload = dashboard_server.request_avatar_encryption_remove_sync(
        {
            "confirmRemove": True,
            "projectPath": "E:/unity/Hero",
            "manifestPath": "E:/tmp/manifest.private.json",
        },
        agent_name="unit-test",
    )

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert "manifestPath" in payload["error"]


def test_avatar_encryption_remove_request_normalizes_manifest_and_output_folder(monkeypatch) -> None:
    monkeypatch.setenv(dashboard_server.AVATAR_ENCRYPTION_ADDON_URL_ENV, "http://127.0.0.1:9876")
    dashboard_server.AGENT_GATEWAY._approvals.clear()
    payload = dashboard_server.request_avatar_encryption_remove_sync(
        {
            "confirmRemove": True,
            "projectPath": "E:/unity/Hero",
            "manifestPath": "Assets/VRCForgeGenerated/AvatarEncryption/Hero/manifest.private.json",
            "outputFolder": "Assets/VRCForgeGenerated/AvatarEncryption/Hero",
        },
        agent_name="unit-test",
    )

    assert payload["ok"] is True
    approval = dashboard_server.AGENT_GATEWAY._approvals[payload["approval"]["id"]]
    assert approval["arguments"]["manifestPath"] == "Assets/VRCForgeGenerated/AvatarEncryption/Hero/manifest.private.json"
    assert approval["arguments"]["outputFolder"] == "Assets/VRCForgeGenerated/AvatarEncryption/Hero"


def test_avatar_encryption_direct_internal_write_target_requires_wrapper() -> None:
    with pytest.raises(agent_gateway.AgentGatewayError, match="dedicated VRCForge request tool"):
        dashboard_server.AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": dashboard_server.AVATAR_ENCRYPTION_ADDON_APPLY_TOOL,
                "arguments": {},
                "reason": "unit direct call should fail",
            }
        )


def test_write_handler_ok_false_marks_approval_failed(tmp_path, monkeypatch) -> None:
    gateway = agent_gateway.AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    gateway.register_write_handler(
        "vrcforge_test_avatar_encryption_failure",
        "Avatar encryption failure",
        "high",
        lambda _arguments: {"ok": False, "error": "BlendShape target is blocked."},
    )
    monkeypatch.setattr(gateway, "_create_pre_write_checkpoint", lambda _approval, _arguments: {"ok": True, "id": "ckpt_test"})
    request = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_test_avatar_encryption_failure",
            "arguments": {"projectPath": "E:/unity/Hero"},
        }
    )
    approval_id = request["approval"]["id"]
    gateway.approve(approval_id)

    applied = gateway.apply_approved({"approval_id": approval_id})

    assert applied["ok"] is False
    assert applied["status"] == "failed"
    assert applied["approval"]["status"] == "failed"
    assert applied["checkpoint"]["id"] == "ckpt_test"
    assert "BlendShape target is blocked" in applied["error"]


def test_avatar_encryption_public_repo_contains_no_unity_or_shader_implementation() -> None:
    assert not (dashboard_server.ROOT_DIR / "Assets" / "VRCForge" / "Editor" / "AvatarEncryptionTool.cs").exists()
    assert not (
        dashboard_server.ROOT_DIR
        / "Assets"
        / "VRCForge"
        / "Runtime"
        / "AvatarEncryption"
        / "VRCForgeAvatarEncryptionRestore.shader"
    ).exists()
    assert not (dashboard_server.ROOT_DIR / "scripts" / "smoke_avatar_encryption_live.py").exists()


def test_avatar_encryption_external_mcp_can_list_and_call_split_tools() -> None:
    config = dashboard_server.AGENT_GATEWAY.ensure_config()
    config.enabled = True
    dashboard_server.AGENT_GATEWAY.save_config(config)
    dashboard_server.AGENT_GATEWAY._approvals.clear()
    headers = {
        "Authorization": f"Bearer {config.token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    with TestClient(dashboard_server.app) as client:
        initialize = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "avatar-encryption-test", "version": "0"},
                },
            },
        )
        assert initialize.status_code == 200

        listed = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200
        tool_names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        assert {
            "vrcforge_avatar_encryption_research_report",
            "vrcforge_avatar_encryption_scan",
            "vrcforge_avatar_encryption_plan",
            "vrcforge_avatar_encryption_preview",
            "vrcforge_avatar_encryption_addon_status",
            "vrcforge_avatar_encryption_liltoon_apply_request",
            "vrcforge_avatar_encryption_poiyomi_apply_request",
            "vrcforge_avatar_encryption_remove_request",
        } <= tool_names
        assert dashboard_server.AVATAR_ENCRYPTION_ADDON_APPLY_TOOL not in tool_names
        assert dashboard_server.AVATAR_ENCRYPTION_ADDON_REMOVE_TOOL not in tool_names

        scan = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "vrcforge_avatar_encryption_scan",
                    "arguments": {
                        "params": {
                            "avatarPath": "Scene/HeroAvatar",
                            "inventory": make_encryption_inventory(),
                        }
                    },
                },
            },
        )
        assert scan.status_code == 200
        scan_payload = json.loads(scan.json()["result"]["content"][0]["text"])
        assert scan_payload["result"]["summary"]["candidateCount"] == 2

        apply_request = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "vrcforge_avatar_encryption_liltoon_apply_request",
                    "arguments": {
                        "params": {
                            "avatarPath": "Scene/HeroAvatar",
                            "projectPath": "E:/unity/Hero",
                            "inventory": make_encryption_inventory(),
                            "confirmCreatorOwnedAssets": True,
                        }
                    },
                },
            },
        )
        assert apply_request.status_code == 200
        request_payload = json.loads(apply_request.json()["result"]["content"][0]["text"])
        assert request_payload["result"]["status"] == "blocked"
        assert "addon.private_module_not_configured" in request_payload["result"]["error"]

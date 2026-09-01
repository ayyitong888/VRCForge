from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

import dashboard_server
from agent_gateway import RUNTIME_SKILL_SUPPORT_MAX_FILES, parse_skill_markdown
from skill_packages import SkillPackageService


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "skills"
CASES = (
    ("vrcforge-avatar-wardrobe", "com.vrcforge.skills.avatar_wardrobe", "VRChat 衣柜制作", False),
    ("vrcforge-avatar-hairstyle", "com.vrcforge.skills.avatar_hairstyle", "VRChat 发型切换", False),
    ("vrcforge-avatar-accessory-switch", "com.vrcforge.skills.avatar_accessory_switch", "VRChat 饰品安装与开关", False),
    ("vrcforge-avatar-expression-menu", "com.vrcforge.skills.avatar_expression_menu", "VRChat 菜单制作", False),
    ("vrcforge-avatar-animation", "com.vrcforge.skills.avatar_animation", "VRChat 动画与体型补偿", False),
    ("vrcforge-avatar-breast-physics-audit", "com.vrcforge.skills.avatar_breast_physics_audit", "VRChat 胸部动态骨骼检查", True),
    ("vrcforge-avatar-audit", "com.vrcforge.skills.avatar_audit", "VRChat Avatar 检查与验收", True),
)


def _json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize("slug,package_id,title,read_only", CASES, ids=[case[0] for case in CASES])
def test_avatar_domain_skills_are_agentic_bounded_and_use_real_tools(
    slug: str, package_id: str, title: str, read_only: bool
) -> None:
    root = ARTIFACTS / slug
    manifest = _json(root / "manifest.json")
    skill = parse_skill_markdown(root / "SKILL.md")
    workflow = _json(root / manifest["entrypoints"]["workflow"])
    external_execution = {
        tool["name"]
        for tool in dashboard_server.AGENT_GATEWAY.build_external_mcp_tools(
            "execution", tool_blocks=["*"]
        )
    }

    assert manifest["id"] == package_id
    assert manifest["skill_name"] == slug
    assert manifest["name"] == skill["title"] == title
    assert manifest["execution"] == "agentic"
    assert "executionPlan" not in manifest["entrypoints"]
    assert not (root / "workflows" / "execution-plan.json").exists()
    assert len(skill["supportFiles"]) == 2
    assert len(skill["supportFiles"]) <= RUNTIME_SKILL_SUPPORT_MAX_FILES
    assert set(skill["supportFiles"]) == {
        manifest["entrypoints"]["workflow"],
        manifest["entrypoints"]["guide"],
    }
    assert set(skill["allowedTools"]) <= external_execution
    assert workflow["schema"] == "vrcforge.skill-package.workflow.v1"
    assert workflow["selfContained"] is True
    assert workflow["requiresOtherSkills"] == []
    assert workflow["uncertaintyPolicy"]["consult"] == [
        "mature_community_guides",
        "asset_author_instructions",
        "official_documentation",
    ]
    assert workflow["uncertaintyPolicy"]["onInsufficientEvidence"] == {
        "capabilityGap": True,
        "ready": False,
        "action": "stop_and_report",
    }
    assert all(
        tool in skill["allowedTools"]
        for step in workflow["steps"]
        for tool in [step["tool"], *step.get("tools", [])]
    )
    write_steps = [step for step in workflow["steps"] if step["writes"]]
    if read_only:
        planning_tools = {
            tool["name"]
            for tool in dashboard_server.AGENT_GATEWAY.build_external_mcp_tools(
                "planning", tool_blocks=["*"]
            )
        }
        assert skill["permissionMode"] == "read_only"
        assert set(skill["allowedTools"]) <= planning_tools
        assert set(manifest["permissions"]) <= {
            "read_project", "unity_scan_scene", "unity_run_validation"
        }
        assert not write_steps
        assert workflow["approval"]["required"] is False
        assert workflow["checkpoint"]["required"] is False
    else:
        assert skill["permissionMode"] == "approval_required"
        assert "write_project_files" in manifest["permissions"]
        assert write_steps
        assert all(step["runtimePermissionGateRequired"] is True for step in write_steps)
        assert all(step["runtimeApprovalRequired"] is True for step in write_steps)
        assert workflow["approval"]["required"] is True
        assert workflow["checkpoint"]["required"] is True
        assert workflow["rollback"]["requiresSeparateApproval"] is True


def test_wardrobe_skill_contains_complete_menu_parameter_fx_animation_loop() -> None:
    root = ARTIFACTS / "vrcforge-avatar-wardrobe"
    manifest = _json(root / "manifest.json")
    workflow = _json(root / manifest["entrypoints"]["workflow"])
    skill = parse_skill_markdown(root / "SKILL.md")

    assert {
        "outfit_mount", "wardrobe", "expression_parameters", "fx_animator",
        "animation_curves", "expression_menu", "runtime_acceptance",
    } <= set(workflow["scope"])
    assert {
        "vrcforge_setup_outfit",
        "vrcforge_add_wardrobe_outfit",
        "vrcforge_ensure_expression_parameter",
        "vrcforge_ensure_animator_state",
        "vrcforge_write_animation_curve",
        "vrcforge_ensure_expression_menu_control",
    } <= set(skill["allowedTools"])
    contract = workflow["communityWardrobeContract"]
    assert contract["selector"]["type"] == "Int"
    assert contract["selector"] == {
        "name": "衣柜",
        "type": "Int",
        "saved": True,
        "synced": True,
        "default": 0,
        "fixedValueRequiredOnEveryOutfitWrite": True,
        "automaticMaxPlusOneForbidden": True,
        "preserveApprovedValuesAndAliases": True,
    }
    assert contract["fx"]["outfitSelectionTransitionSource"] == "AnyState"
    assert contract["fx"]["outfitSelectionCondition"] == "衣柜 == selected_value"
    assert contract["fx"]["hasExitTime"] is False
    assert contract["fx"]["durationSeconds"] == 0
    assert contract["fx"]["preserveVerifiedWorkingExistingTopology"] is True
    assert contract["fx"]["verifiedUnconditionalAnyStateBaselineMayBePreserved"] is True
    assert contract["fx"]["mustNotNormalizeWorkingTopologyToIdleOrBase"] is True
    assert contract["fx"]["newTopologyMustMatchUserConfirmedReference"] is True
    assert contract["fx"]["removeOnlyTransitionsStatesOrLayersWithProvenConflict"] is True
    assert contract["animation"]["keyframeTimeSeconds"] == 0
    assert contract["animation"]["fullMutualExclusionMatrixRequired"] is True
    assert contract["animation"]["rewriteEveryApprovedExistingValue"] is True
    assert contract["animation"]["writeDefaultsMustNotSubstituteForMatrix"] is True
    assert set(contract["animation"]["morphInventoryScope"]) == {
        "current_avatar_body_renderers",
        "current_outfit_renderers",
        "existing_animation_bindings",
        "candidate_animation_bindings",
    }
    assert contract["animation"]["inspectExistingAndCandidateOutfitAnimationMorphsFirst"] is True
    assert contract["animation"]["bodyBaselinePolicy"] == (
        "preserve_current_accepted_body_baseline_except_inside_an_outfit_that_cannot_fit_it"
    )
    assert contract["animation"]["pairedMorphCurvesRequired"] == [
        "apply_needed_body_or_clothing_morphs_in_target_outfit",
        "reset_or_restore_body_and_clothing_morphs_in_other_outfits",
    ]
    assert contract["animation"]["mustNotForceEveryOutfitToMaximumBodyMorph"] is True
    assert contract["animation"]["mustNotHardcodeMorphValue100"] is True
    assert contract["animation"]["projectExamplesOnly"] == {
        "manuka": [
            "Breast_big",
            "Breast_big_PLUS",
            "Foot_heel",
            "Foot_heel_high",
        ]
    }
    assert contract["animation"]["projectExamplesAreNotRequiredFields"] is True
    assert contract["animation"]["visualAcceptanceAfterMorphChange"] == [
        "body_no_penetration",
        "outfit_no_penetration",
        "heel_and_pose_alignment_correct",
    ]
    assert contract["protectedSystems"]["preserveAllUserConfiguredPhysics"] is True
    assert "Marshmallow PB 2.x" in contract["protectedSystems"]["targetProjectExamples"]
    assert contract["headObjects"]["refitOnlyWhen"] == [
        "head_or_neck_transplant_detected",
        "current_head_skeleton_differs_from_original_fit_target",
        "current_head_surface_differs_from_original_fit_target",
        "visual_offset_or_penetration_observed",
    ]
    assert contract["headObjects"]["preserveWhen"] == [
        "complete_model_original_fit_is_valid",
        "existing_outfit_fit_has_no_mismatch_or_visual_defect",
    ]
    assert contract["headObjects"]["projectExampleOnly"] == {
        "targetHead": "Sapphy Head"
    }
    assert contract["menu"]["targetProjectRootControls"] == ["面捕", "原模型菜单"]
    assert contract["menu"]["targetProjectWardrobeParent"] == "原模型菜单"
    assert contract["menu"]["doNotMigrate"] == ["FT2 hair content"]
    assert contract["menu"]["maximumControlsPerPageIncludingNextPage"] == 8
    assert contract["perOutfitGate"]["oneOutfitAtATime"] is True
    assert contract["replacementCleanup"] == {
        "fixedValueReuseRequiresExactOldBindingRemovalFirst": True,
        "legacyFxLayerDeletionByExactNameOnly": True,
        "legacySceneObjectRequiresCompleteInboundClosure": True,
        "disableBeforeDelete": True,
        "deleteRequiresSeparateApproval": True,
        "neverDeleteSourceAvatarOrSourceAsset": True,
    }
    assert contract["projectBinding"] == {
        "everyToolCallRequiresAbsoluteProjectPath": True,
        "projectPathMustRemainConstant": True,
    }
    assert {
        "vrcforge_scan_inbound_reference_closure",
        "vrcforge_manage_fx_animator",
        "vrcforge_write_animation_curve",
        "vrcforge_set_property",
        "vrcforge_set_gameobject_active",
        "vrcforge_delete_gameobject",
    } <= set(skill["allowedTools"])
    instructions = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "max+1" in instructions
    assert "完整互斥矩阵" in instructions
    assert "来源 FT2 中已验证可工作的无条件 AnyState 基线不是缺陷" in instructions
    assert "不得为了理论规范化改成 Idle/Base" in instructions
    assert "先检查每套衣服现有/候选动画是否已经带形态键" in instructions
    assert "成对写入必要的 body/clothing `reset/apply` 曲线" in instructions
    assert "不硬编码 100 或固定名称" in instructions
    assert "Sapphy Head 仅是当前目标示例" in instructions
    assert "正常完整模型和已正确适配的衣服保留原配置" in instructions
    assert workflow["requiresOtherSkills"] == []


@pytest.mark.parametrize("slug,package_id,title,read_only", CASES, ids=[case[0] for case in CASES])
def test_avatar_domain_skills_export_as_signed_agentic_packages(
    tmp_path: Path, slug: str, package_id: str, title: str, read_only: bool
) -> None:
    root = ARTIFACTS / slug
    service = SkillPackageService(tmp_path / "store", vrcforge_version="1.7.9")
    signer = service.generate_signing_keypair()
    package = service.export_release(root, tmp_path / f"{slug}.vsk", signer.private_key_pem)
    preview = service.inspect_package(package.package_path)

    assert preview.signature_status == "signed"
    assert preview.signer_fingerprint == signer.fingerprint
    assert preview.manifest["id"] == package_id
    assert preview.manifest["name"] == title
    assert preview.manifest["execution"] == "agentic"
    assert ("write_project_files" in preview.manifest["permissions"]) is not read_only


def test_official_preparation_replaces_old_avatar_bundle_with_nine_real_domains() -> None:
    helper = runpy.run_path(
        str(ROOT / "scripts" / "prepare_official_workflows.py")
    )
    packages = helper["PACKAGES"]
    expected_ids = {
        "com.vrcforge.workflows.avatar_head_transplant",
        "com.vrcforge.workflows.avatar_part_transplant",
        *(case[1] for case in CASES),
    }

    assert len(packages) == len(expected_ids) == 9
    assert {item[0] for item in packages} == expected_ids
    assert helper["LEGACY_PACKAGE_ID"] == "community.personal.avatar-authoring-workflow"
    assert helper["LEGACY_PACKAGE_ID"] not in expected_ids

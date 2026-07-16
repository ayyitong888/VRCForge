from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

import path_to_skill as path_to_skill_module
from path_to_skill import (
    PATH_TO_SKILL_SCHEMA,
    PATH_TO_SKILL_RECIPE_DEFINITIONS,
    PathToSkillSecurityError,
    PathToSkillValidationError,
    build_path_to_skill_source,
    write_path_to_skill_source,
)
from skill_packages import SkillPackageService


RECIPE_CAPTURE_CASES = [
    pytest.param(
        {
            "status": "passed",
            "workflow": "saved_group_selection",
            "recipeType": "ttt_material_group",
            "projectPath": "C:\\Users\\RecipeOwner\\AvatarProject",
            "avatarPath": "AvatarRoot",
            "steps": [
                {
                    "name": "plan-atlas",
                    "params": {
                        "projectRoot": "C:\\Users\\RecipeOwner\\AvatarProject",
                        "materialPath": "C:\\Users\\RecipeOwner\\AvatarProject\\Assets\\Avatar\\Materials\\Body.mat",
                        "rendererPath": "AvatarRoot/Body",
                        "slots": [0, 1],
                    },
                }
            ],
        },
        "ttt_material_group",
        "vrcforge_optimization_ttt_atlas_plan",
        "request_only",
        {"projectPath"},
        ("C:\\Users\\RecipeOwner",),
        id="ttt-material-group",
    ),
    pytest.param(
        {
            "status": "passed",
            "workflow": "package_intake_review",
            "recipeType": "booth_import_preflight",
            "projectPath": "C:\\Users\\RecipeOwner\\AvatarProject",
            "packagePath": "D:\\BoothDownloads\\CreatorOutfit.zip",
            "plan": {
                "targetPrefabPath": "C:\\Users\\RecipeOwner\\AvatarProject\\Assets\\Avatar\\Outfits\\Outfit.prefab",
                "selection": "Outfit.unitypackage",
            },
            "steps": ["inspect-structure", "plan-import-without-writing"],
        },
        "booth_import_preflight",
        "vrcforge_inspect_outfit_package",
        "read_only",
        {"projectPath", "packagePath"},
        ("C:\\Users\\RecipeOwner", "D:\\BoothDownloads"),
        id="booth-import-preflight",
    ),
    pytest.param(
        {
            "status": "passed",
            "workflow": "budget_reduction_review",
            "recipeType": "parameter_compression",
            "projectPath": "C:\\Users\\RecipeOwner\\AvatarProject",
            "avatarPath": "AvatarRoot",
            "evidence": {
                "artifactPath": "C:\\Users\\RecipeOwner\\AvatarProject\\Assets\\VRCForge\\parameter-proof.json",
                "candidateNames": ["OutfitSelection", "AccessoryToggle"],
                "blockedNames": ["FaceTrackingFloat"],
            },
            "steps": ["inventory", "menu-map", "behavior-regression", "blocked-preview"],
        },
        "parameter_compression",
        "vrcforge_optimization_parameter_path_to_skill",
        "blocked_preview",
        {"projectPath"},
        ("C:\\Users\\RecipeOwner",),
        id="parameter-compression",
    ),
    pytest.param(
        {
            "status": "passed",
            "workflow": "cross_platform_readiness",
            "recipeType": "pc_quest_upload_pass",
            "projectPath": "C:\\Users\\RecipeOwner\\AvatarProject",
            "avatarPath": "AvatarRoot",
            "platforms": ["pc", "quest"],
            "reports": {
                "sdkReportPath": "C:\\Users\\RecipeOwner\\AvatarProject\\Library\\VRCSDK\\upload-report.json",
                "unknownMetrics": ["questDownloadSize"],
            },
            "steps": ["pc-gate", "quest-gate", "report-unknowns"],
        },
        "pc_quest_upload_pass",
        "vrcforge_optimization_upload_gate_audit",
        "read_only",
        {"projectPath"},
        ("C:\\Users\\RecipeOwner",),
        id="pc-quest-upload-pass",
    ),
]


def test_shader_workflow_capture_redacts_paths_and_exports_vsk(tmp_path: Path) -> None:
    proof = {
        "schema": PATH_TO_SKILL_SCHEMA,
        "source": "shader-adapter-smoke",
        "status": "passed",
        "workflow": "shader_adapter_semantic_tuning",
        "variables": {
            "projectPath": "E:\\unity\\milltina",
            "avatarPath": "Milltina",
            "rendererPath": "Milltina/Milltina_cloth_apron",
            "slotIndex": 0,
        },
        "requirements": {
            "packageId": "com.poiyomi.toon",
            "repository": "https://vpm.poiyomi.com/vpm.json",
            "targetShader": "Poiyomi Toon",
            "targetShaderAssetPath": "E:\\unity\\milltina\\Packages\\com.poiyomi.toon\\Shaders\\Poiyomi Toon.shader",
        },
        "steps": [
            "scan_materials",
            "request_shader_switch",
            "verify_target_family",
            "request_semantic_tuning",
            "checkpoint_restore",
        ],
        "validation": {
            "requiresApproval": True,
            "requiresCheckpoint": True,
            "requiresRollback": True,
            "semanticProperty": "smoothness",
        },
    }

    captured = write_path_to_skill_source(
        proof,
        tmp_path / "source",
        package_id="community.path-to-skill.shader-preset",
        skill_name="shader-preset",
        title="Shader Preset",
    )

    serialized = json.dumps(captured.source_files, ensure_ascii=False)
    assert "E:\\unity" not in serialized
    assert "{{projectPath}}" in serialized
    assert captured.workflow["variables"]["projectPath"]["placeholder"] == "{{projectPath}}"
    assert {
        ("source.variables.projectPath", "projectPath"),
        ("source.requirements.targetShaderAssetPath", "projectPath"),
    } <= {
        (item["field"], item["variable"])
        for item in captured.workflow["remapping"]["fields"]
    }
    assert captured.manifest["entrypoints"] == {
        "skill": "SKILL.md",
        "workflow": "workflows/captured-path.json",
    }
    assert "vrcforge_plan_shader_tuning" in captured.skill_markdown
    assert "rollback verification" in captured.skill_markdown

    service = SkillPackageService(tmp_path / "store", vrcforge_version="1.3.0")
    package = service.export_dev(tmp_path / "source", tmp_path / "shader-preset.vsk")
    preview = service.inspect_package(package.package_path)

    assert preview.manifest["id"] == "community.path-to-skill.shader-preset"
    assert preview.manifest["entrypoints"]["workflow"] == "workflows/captured-path.json"
    assert preview.risk_level in {"medium", "high"}


def test_optimizer_capture_uses_variables_for_project_paths() -> None:
    proof = {
        "status": "passed",
        "workflow": "optimizer_conservative_profile",
        "projectPath": "C:\\Users\\xiao123\\AvatarProject",
        "avatarPath": "AvatarRoot",
        "steps": [
            {
                "name": "optimizer.apply",
                "tool": "optimization.meshia.simplify-apply-request",
                "params": {
                    "projectRoot": "C:\\Users\\xiao123\\AvatarProject",
                    "artifactPath": "C:\\Users\\xiao123\\AvatarProject\\Assets\\VRCForge\\proof.json",
                    "rendererPath": "AvatarRoot/Hat",
                },
            }
        ],
    }

    captured = build_path_to_skill_source(proof, package_id="community.path-to-skill.optimizer-profile")
    workflow = captured.workflow
    serialized = json.dumps(workflow, ensure_ascii=False)

    assert "C:\\Users" not in serialized
    assert "{{projectPath}}" in serialized
    assert "{{projectPath}}/Assets/VRCForge/proof.json" in serialized
    assert workflow["validation"]["requiresApproval"] is True
    assert workflow["validation"]["requiresCheckpoint"] is True
    assert workflow["validation"]["requiresRollback"] is True
    assert "unity_modify_components" in captured.manifest["permissions"]
    assert "vrcforge_optimization_plan" in captured.skill_markdown


def test_capture_defaults_to_vrcforge_1_3_0_compatibility() -> None:
    captured = build_path_to_skill_source(
        {"workflow": "compatibility_floor", "steps": ["inspect"]},
        package_id="community.path-to-skill.compatibility-floor",
    )

    assert captured.manifest["min_vrcforge_version"] == "1.3.0"


@pytest.mark.parametrize(
    (
        "summary",
        "recipe_type",
        "entrypoint_tool",
        "write_path",
        "required_variables",
        "private_path_markers",
    ),
    RECIPE_CAPTURE_CASES,
)
def test_explicit_recipe_definitions_variablize_and_redact_paths(
    tmp_path: Path,
    summary: dict[str, object],
    recipe_type: str,
    entrypoint_tool: str,
    write_path: str,
    required_variables: set[str],
    private_path_markers: tuple[str, ...],
) -> None:
    captured = build_path_to_skill_source(
        summary,
        package_id=f"community.path-to-skill.{recipe_type.replace('_', '-')}",
    )
    definition = PATH_TO_SKILL_RECIPE_DEFINITIONS[recipe_type]
    recipe = captured.workflow["recipe"]
    serialized = json.dumps(captured.source_files, ensure_ascii=False)

    assert recipe["type"] == recipe_type
    assert recipe["shape"] == definition.shape
    assert recipe["writePath"] == write_path
    assert recipe["argumentHint"] == definition.argument_hint
    assert recipe["permissions"] == list(definition.permissions)
    assert recipe["entrypointTool"] == entrypoint_tool
    assert recipe["allowedTools"] == list(definition.allowed_tools)
    assert recipe["detectorRules"] == list(definition.detector_rules)
    assert recipe["requiredEvidence"] == list(definition.required_evidence)
    assert recipe["validationDefaults"] == {
        "requiresApproval": definition.requires_approval,
        "requiresCheckpoint": definition.requires_checkpoint,
        "requiresRollback": definition.requires_rollback,
    }
    assert captured.workflow["validation"]["requiresApproval"] is definition.requires_approval
    assert captured.workflow["validation"]["requiresCheckpoint"] is definition.requires_checkpoint
    assert captured.workflow["validation"]["requiresRollback"] is definition.requires_rollback
    assert captured.manifest["agent"]["write_path"] == write_path
    assert captured.manifest["permissions"] == sorted(definition.permissions)
    assert entrypoint_tool in captured.skill_markdown
    assert f"permission-mode: {definition.permission_mode}" in captured.skill_markdown
    assert f"risk-level: {definition.risk_level}" in captured.skill_markdown
    assert "support-files:\n  - workflows/captured-path.json" in captured.skill_markdown

    assert captured.workflow["variables"]["projectPath"]["placeholder"] == "{{projectPath}}"
    assert required_variables <= set(captured.workflow["variables"])
    assert required_variables <= set(captured.workflow["remapping"]["required"])
    assert "{{projectPath}}" in serialized
    for marker in private_path_markers:
        assert marker not in serialized
    assert any(
        item["variable"] == "projectPath" and item["field"].startswith("source.")
        for item in captured.workflow["remapping"]["fields"]
    )

    source_dir = captured.write_to(tmp_path / "source")
    service = SkillPackageService(tmp_path / "store", vrcforge_version="1.3.0")
    package = service.export_dev(source_dir, tmp_path / f"{recipe_type}.vsk")
    preview = service.inspect_package(package.package_path)
    assert preview.manifest["agent"]["write_path"] == write_path
    assert preview.manifest["entrypoints"]["workflow"] == "workflows/captured-path.json"


@pytest.mark.parametrize(
    ("workflow", "recipe_type"),
    [
        ("ttt_atlas_material_group", "ttt_material_group"),
        ("booth_import_preflight", "booth_import_preflight"),
        ("parameter_compression", "parameter_compression"),
        ("pc_quest_upload_pass", "pc_quest_upload_pass"),
    ],
)
def test_exact_recipe_workflow_names_and_legacy_alias_are_resolved(workflow: str, recipe_type: str) -> None:
    captured = build_path_to_skill_source(
        {"workflow": workflow, "status": "passed"},
        package_id=f"community.path-to-skill.{recipe_type.replace('_', '-')}",
    )

    assert captured.workflow["recipe"]["type"] == recipe_type


@pytest.mark.parametrize(
    "workflow",
    [
        "my_ttt_material_group_capture",
        "booth_import_package_review",
        "parameter_compression_report",
        "pc_quest_upload_pass_extra",
    ],
)
def test_recipe_workflow_matching_does_not_use_partial_tokens(workflow: str) -> None:
    captured = build_path_to_skill_source(
        {"workflow": workflow, "status": "passed", "steps": ["captured.read"]},
        package_id="community.path-to-skill.generic-capture",
    )

    assert "recipe" not in captured.workflow


@pytest.mark.parametrize(
    "steps",
    [[], [""], [{}], [False], [0]],
)
def test_generic_capture_requires_a_meaningful_operation(steps: list[object]) -> None:
    with pytest.raises(PathToSkillValidationError, match="at least one non-empty"):
        build_path_to_skill_source(
            {"workflow": "captured_workflow", "steps": steps},
            package_id="community.path-to-skill.empty-capture",
        )


def test_normalized_exact_recipe_alias_remains_compatible() -> None:
    captured = build_path_to_skill_source(
        {"workflow": "TTT Atlas Material Group", "status": "passed"},
        package_id="community.path-to-skill.ttt-legacy-alias",
    )

    assert captured.workflow["recipe"]["type"] == "ttt_material_group"


@pytest.mark.parametrize(
    ("recipe_type", "secret_field"),
    [
        ("ttt_material_group", {"apiKey": "test-secret-value-123456789"}),
        ("booth_import_preflight", {"sessionToken": "test-session-value-123456789"}),
        ("parameter_compression", {"privateKey": "test-private-value-123456789"}),
        ("pc_quest_upload_pass", {"authorization": "Bearer test-secret-value-123456789"}),
    ],
)
def test_explicit_recipe_capture_rejects_secret_fields(
    recipe_type: str,
    secret_field: dict[str, str],
) -> None:
    summary: dict[str, object] = {
        "workflow": "explicit_recipe_fixture",
        "recipeType": recipe_type,
        "projectPath": "C:\\Users\\RecipeOwner\\AvatarProject",
    }
    summary.update(deepcopy(secret_field))

    with pytest.raises(PathToSkillSecurityError):
        build_path_to_skill_source(
            summary,
            package_id=f"community.path-to-skill.{recipe_type.replace('_', '-')}",
        )


def test_explicit_recipe_type_must_be_known() -> None:
    with pytest.raises(PathToSkillValidationError, match="Unknown Path-to-Skill recipeType"):
        build_path_to_skill_source(
            {"workflow": "capture", "recipeType": "invented_recipe"},
            package_id="community.path-to-skill.unknown-recipe",
        )


def test_explicit_write_recipe_cannot_weaken_required_safety_gates() -> None:
    recipe_type = "ttt_material_group"
    captured = build_path_to_skill_source(
        {
            "workflow": "captured_write_recipe",
            "recipeType": recipe_type,
            "validation": {
                "requiresApproval": False,
                "requiresCheckpoint": False,
                "requiresRollback": False,
            },
        },
        package_id=f"community.path-to-skill.{recipe_type.replace('_', '-')}",
    )

    assert captured.workflow["validation"]["requiresApproval"] is True
    assert captured.workflow["validation"]["requiresCheckpoint"] is True
    assert captured.workflow["validation"]["requiresRollback"] is True


def test_generic_request_only_capture_cannot_weaken_required_safety_gates() -> None:
    captured = build_path_to_skill_source(
        {
            "workflow": "generic_write_capture",
            "steps": ["request-supervised-write"],
            "validation": {
                "requiresApproval": False,
                "requiresCheckpoint": False,
                "requiresRollback": False,
            },
        },
        package_id="community.path-to-skill.generic-write-capture",
    )

    assert captured.manifest["agent"]["write_path"] == "request_only"
    assert captured.workflow["validation"]["requiresApproval"] is True
    assert captured.workflow["validation"]["requiresCheckpoint"] is True
    assert captured.workflow["validation"]["requiresRollback"] is True


def test_blocked_preview_has_no_current_write_gates_and_separate_future_apply_gate() -> None:
    captured = build_path_to_skill_source(
        {
            "workflow": "captured_parameter_preview",
            "recipeType": "parameter_compression",
            "validation": {
                "requiresApproval": True,
                "requiresCheckpoint": True,
                "requiresRollback": True,
            },
        },
        package_id="community.path-to-skill.parameter-preview",
    )

    assert captured.workflow["validation"] == {
        "requiresApproval": False,
        "requiresCheckpoint": False,
        "requiresRollback": False,
    }
    assert captured.workflow["recipe"]["futureApplyGate"] == {
        "status": "blocked",
        "applyToolExposed": False,
        "requiresApproval": True,
        "requiresCheckpoint": True,
        "requiresRollback": True,
    }
    assert "Do not request or perform a project write" in captured.skill_markdown


def test_ttt_recipe_declares_component_and_material_permissions() -> None:
    permissions = PATH_TO_SKILL_RECIPE_DEFINITIONS["ttt_material_group"].permissions

    assert "unity_modify_components" in permissions
    assert "unity_modify_materials" in permissions


@pytest.mark.parametrize(
    "relative_path",
    [
        "../PaidBooth/Creator.zip",
        "..\\..\\Users\\Alice\\Downloads\\PaidBooth\\Creator.zip",
        "Downloads/../PaidBooth/Creator.zip",
    ],
)
def test_capture_rejects_parent_traversal_in_relative_path_fields(relative_path: str) -> None:
    with pytest.raises(PathToSkillSecurityError, match="parent-directory traversal"):
        build_path_to_skill_source(
            {
                "workflow": "booth_import_preflight",
                "packagePath": relative_path,
                "steps": ["inspect"],
            },
            package_id="community.path-to-skill.traversal",
        )


def test_external_relative_package_path_is_variablized_without_private_name_leak() -> None:
    captured = build_path_to_skill_source(
        {
            "workflow": "booth_import_preflight",
            "packagePath": "Users/Alice/Downloads/PaidBooth/SecretCreatorOutfit.zip",
            "steps": ["inspect"],
        },
        package_id="community.path-to-skill.private-relative-package",
    )
    serialized = json.dumps(captured.source_files, ensure_ascii=False)

    assert captured.workflow["sourceSummary"]["packagePath"] == "{{packagePath}}"
    assert captured.workflow["variables"]["packagePath"]["placeholder"] == "{{packagePath}}"
    assert {
        "field": "source.packagePath",
        "variable": "packagePath",
        "reason": "external relative path redacted",
    } in captured.workflow["remapping"]["fields"]
    assert "Alice" not in serialized
    assert "SecretCreatorOutfit" not in serialized
    assert "PaidBooth" not in serialized


def test_existing_private_path_placeholder_is_registered_as_required_variable() -> None:
    captured = build_path_to_skill_source(
        {
            "workflow": "booth_import_preflight",
            "privatePackagePath": "{{privatePackagePath}}",
            "steps": ["inspect"],
        },
        package_id="community.path-to-skill.placeholder-registration",
    )

    assert captured.workflow["sourceSummary"]["privatePackagePath"] == "{{privatePackagePath}}"
    assert captured.workflow["variables"]["privatePackagePath"]["required"] is True
    assert "privatePackagePath" in captured.workflow["remapping"]["required"]
    assert {
        "field": "source.privatePackagePath",
        "variable": "privatePackagePath",
        "reason": "existing path variable required",
    } in captured.workflow["remapping"]["fields"]


@pytest.mark.parametrize(
    "private_location",
    [
        "file:///C:/Users/Alice/Downloads/PaidBooth/SecretOutfit.zip",
        "http://127.0.0.1:8080/private/SecretOutfit.zip",
        "https://asset-cache.internal/private/SecretOutfit.zip",
        "https://cdn.example.com/download.zip?signature=private-signed-value",
    ],
)
def test_file_and_private_urls_are_redacted_to_required_variables(private_location: str) -> None:
    captured = build_path_to_skill_source(
        {
            "workflow": "booth_import_preflight",
            "privatePackagePath": private_location,
            "steps": ["inspect"],
        },
        package_id="community.path-to-skill.private-url-redaction",
    )
    serialized = json.dumps(captured.source_files, ensure_ascii=False)

    assert captured.workflow["sourceSummary"]["privatePackagePath"] == "{{privatePackagePath}}"
    assert "privatePackagePath" in captured.workflow["remapping"]["required"]
    assert private_location not in serialized
    assert "SecretOutfit" not in serialized


def test_public_https_url_is_preserved() -> None:
    public_url = "https://vpm.poiyomi.com/vpm.json"
    captured = build_path_to_skill_source(
        {
            "workflow": "public_repository_capture",
            "repository": public_url,
            "steps": ["inspect"],
        },
        package_id="community.path-to-skill.public-repository",
    )

    assert captured.workflow["sourceSummary"]["repository"] == public_url


def test_safe_unity_relative_asset_and_hierarchy_paths_are_preserved() -> None:
    captured = build_path_to_skill_source(
        {
            "workflow": "safe_relative_paths",
            "targetPrefabPath": "Assets/Avatar/Outfits/Jacket.prefab",
            "materialPath": "Packages/com.example.avatar/Materials/Jacket.mat",
            "rendererPath": "AvatarRoot/Body/Jacket",
            "steps": ["inspect"],
        },
        package_id="community.path-to-skill.safe-relative-paths",
    )

    summary = captured.workflow["sourceSummary"]
    assert summary["targetPrefabPath"] == "Assets/Avatar/Outfits/Jacket.prefab"
    assert summary["materialPath"] == "Packages/com.example.avatar/Materials/Jacket.mat"
    assert summary["rendererPath"] == "AvatarRoot/Body/Jacket"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"package_id": "community.path-to-skill.bad---package"},
        {"skill_name": "bad---skill"},
        {"title": "Bad --- Title"},
        {"version": "1.0.0---dev"},
        {"author": "Bad --- Author"},
        {"min_vrcforge_version": "1.3.0---dev"},
    ],
)
def test_user_controlled_frontmatter_arguments_reject_yaml_delimiter(kwargs: dict[str, str]) -> None:
    with pytest.raises(PathToSkillSecurityError, match="frontmatter delimiter"):
        build_path_to_skill_source(
            {"workflow": "safe_capture", "steps": ["inspect"]},
            **kwargs,
        )


@pytest.mark.parametrize(
    "summary",
    [
        {"workflow": "bad---workflow", "steps": ["inspect"]},
        {"workflow": "safe_capture", "steps": ["inspect---mutate"]},
        {"workflow": "safe_capture", "steps": ["inspect"], "note---name": "bad"},
    ],
)
def test_summary_scalars_and_keys_reject_yaml_frontmatter_delimiter(summary: dict[str, object]) -> None:
    with pytest.raises(PathToSkillSecurityError, match="frontmatter delimiter"):
        build_path_to_skill_source(
            summary,
            package_id="community.path-to-skill.frontmatter-delimiter",
        )


def test_frontmatter_rejection_happens_before_any_output_is_created(tmp_path: Path) -> None:
    destination = tmp_path / "must-not-exist"

    with pytest.raises(PathToSkillSecurityError, match="frontmatter delimiter"):
        write_path_to_skill_source(
            {"workflow": "safe_capture", "steps": ["inspect"]},
            destination,
            package_id="community.path-to-skill.no-partial-frontmatter",
            title="Unsafe --- Title",
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"workflow": "bad", "gatewayToken": "test-token-123456789"},
        {"workflow": "bad", "notes": "api_key = sk-testonly-secret-value-123456"},
        {"workflow": "bad", "steps": [{"name": "x", "privateKey": "-----BEGIN PRIVATE KEY-----"}]},
    ],
)
def test_capture_rejects_secret_fields_and_values(payload: dict[str, object]) -> None:
    with pytest.raises(PathToSkillSecurityError):
        build_path_to_skill_source(payload, package_id="community.path-to-skill.bad-secret")


@pytest.mark.parametrize(
    "payload",
    [
        {"workflow": "bad", "assetPayload": "paid binary bytes would go here"},
        {"workflow": "bad", "binaryPayload": "AAAA"},
        {"workflow": "bad", "screenshotBytes": b"\x89PNG"},
        {"workflow": "bad", "boothZipContents": "zip bytes"},
    ],
)
def test_capture_rejects_paid_asset_and_binary_payload_fields(payload: dict[str, object]) -> None:
    with pytest.raises(PathToSkillSecurityError):
        build_path_to_skill_source(payload, package_id="community.path-to-skill.bad-payload")


def test_capture_does_not_hide_invalid_manifest_identity() -> None:
    with pytest.raises(ValueError, match="reverse-domain"):
        build_path_to_skill_source(
            {"workflow": "valid_workflow"},
            package_id="not-a-reverse-domain-id",
        )


def _captured_write_fixture():
    return build_path_to_skill_source(
        {"workflow": "atomic_write_capture", "steps": ["inspect"]},
        package_id="community.path-to-skill.atomic-write-capture",
    )


def test_write_refuses_existing_dirty_output_without_explicit_overwrite(tmp_path: Path) -> None:
    captured = _captured_write_fixture()
    destination = tmp_path / "captured-source"
    destination.mkdir()
    stale = destination / "unrelated-private-file.txt"
    stale.write_text("do not merge", encoding="utf-8")

    with pytest.raises(PathToSkillValidationError, match="already exists"):
        captured.write_to(destination)

    assert stale.read_text(encoding="utf-8") == "do not merge"
    assert not (destination / "manifest.json").exists()


def test_explicit_overwrite_atomically_replaces_dirty_output_without_extra_files(tmp_path: Path) -> None:
    captured = _captured_write_fixture()
    destination = tmp_path / "captured-source"
    destination.mkdir()
    (destination / "unrelated-private-file.txt").write_text("remove on replacement", encoding="utf-8")

    published = captured.write_to(destination, overwrite=True)
    written_files = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }

    assert published == destination.absolute()
    assert written_files == set(captured.source_files)
    assert not (destination / "unrelated-private-file.txt").exists()


def test_write_rejects_output_symlink_and_parent_symlink(tmp_path: Path) -> None:
    captured = _captured_write_fixture()
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "marker.txt"
    marker.write_text("untouched", encoding="utf-8")
    output_link = tmp_path / "output-link"
    parent_link = tmp_path / "parent-link"
    dirty_output = tmp_path / "dirty-output"
    dirty_output.mkdir()
    nested_link = dirty_output / "nested-link"
    try:
        output_link.symlink_to(external, target_is_directory=True)
        parent_link.symlink_to(external, target_is_directory=True)
        nested_link.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")

    with pytest.raises(PathToSkillSecurityError, match="symlink|junction|reparse"):
        captured.write_to(output_link, overwrite=True)
    with pytest.raises(PathToSkillSecurityError, match="symlink|junction|reparse"):
        captured.write_to(parent_link / "captured-source")
    with pytest.raises(PathToSkillSecurityError, match="link|reparse"):
        captured.write_to(dirty_output, overwrite=True)

    assert marker.read_text(encoding="utf-8") == "untouched"
    assert not (external / "captured-source").exists()
    assert nested_link.is_symlink()


def test_write_rejects_source_path_traversal_without_partial_output(tmp_path: Path) -> None:
    captured = _captured_write_fixture()
    malicious = replace(
        captured,
        source_files={**captured.source_files, "../escaped.txt": "must not escape\n"},
    )
    destination = tmp_path / "captured-source"

    with pytest.raises(PathToSkillSecurityError, match="Unsafe captured source file path"):
        malicious.write_to(destination)

    assert not destination.exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_staging_write_failure_leaves_no_partial_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured = _captured_write_fixture()
    destination = tmp_path / "partial-output"
    original_write_text = Path.write_text

    def fail_on_skill_markdown(path: Path, *args: object, **kwargs: object) -> int:
        if path.name == "SKILL.md":
            raise OSError("simulated staged write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_on_skill_markdown)

    with pytest.raises(PathToSkillValidationError, match="Could not stage"):
        captured.write_to(destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".partial-output.vrcforge-stage-*"))


def test_publish_failure_restores_previous_output_without_partial_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured = _captured_write_fixture()
    destination = tmp_path / "captured-source"
    destination.mkdir()
    stale = destination / "previous.txt"
    stale.write_text("previous tree", encoding="utf-8")
    original_replace = path_to_skill_module.os.replace
    failed = False

    def fail_new_tree_publish(source: str | Path, target: str | Path) -> None:
        nonlocal failed
        if not failed and Path(source).name == "tree" and Path(target) == destination:
            failed = True
            raise OSError("simulated atomic publish failure")
        original_replace(source, target)

    monkeypatch.setattr(path_to_skill_module.os, "replace", fail_new_tree_publish)

    with pytest.raises(PathToSkillValidationError, match="atomically publish"):
        captured.write_to(destination, overwrite=True)

    assert stale.read_text(encoding="utf-8") == "previous tree"
    assert not (destination / "manifest.json").exists()
    assert not list(tmp_path.glob(".captured-source.vrcforge-stage-*"))

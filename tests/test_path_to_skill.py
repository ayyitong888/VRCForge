from __future__ import annotations

import json
from pathlib import Path

import pytest

from path_to_skill import (
    PATH_TO_SKILL_SCHEMA,
    PathToSkillSecurityError,
    build_path_to_skill_source,
    write_path_to_skill_source,
)
from skill_packages import SkillPackageService


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

    service = SkillPackageService(tmp_path / "store", vrcforge_version="0.9.0-beta")
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

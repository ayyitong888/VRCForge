from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_gateway import (
    AgentGateway,
    AgentGatewayError,
    PROJECTED_SKILL_STATE_MAX_BYTES,
    PROJECTED_SKILL_STATE_NAME,
    PROJECTED_SKILL_STATE_SCHEMA,
    parse_skill_markdown,
)
from skill_package_projection import (
    SkillPackageProjectionPorts,
    SkillPackageProjectionService,
)
from skill_packages import SkillPackageService


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPOSITORY_ROOT / "examples" / "skill-packages"

EXAMPLES = (
    {
        "slug": "validation-report-extension",
        "package_id": "community.examples.validation-report-extension",
        "skill_name": "validation-report-extension",
        "workflow_tool": "vrcforge_run_validation_report",
        "entrypoint_tool": "vrcforge_run_validation_report",
        "category": "read/debug",
        "permissions": {"read_project", "unity_run_validation", "unity_scan_scene"},
        "write": False,
        "min_version": "1.3.0",
    },
    {
        "slug": "material-preset-pack",
        "package_id": "community.examples.material-preset-pack",
        "skill_name": "material-preset-pack",
        "workflow_tool": "vrcforge_request_apply",
        "target_tool": "vrcforge_apply_shader_tuning",
        "permissions": {"read_project", "unity_modify_materials"},
        "write": True,
        "min_version": "1.3.0",
    },
    {
        "slug": "outfit-naming-helper",
        "package_id": "community.examples.outfit-naming-helper",
        "skill_name": "outfit-naming-helper",
        "workflow_tool": "vrcforge_request_apply",
        "target_tool": "vrcforge_unity_mcp_write",
        "permissions": {"read_project", "unity_scan_scene", "write_project_files"},
        "write": True,
        "min_version": "1.4.0",
    },
    {
        "slug": "optimizer-report-helper",
        "package_id": "community.examples.optimizer-report-helper",
        "skill_name": "optimizer-report-helper",
        "workflow_tool": "vrcforge_optimization_plan",
        "entrypoint_tool": "vrcforge_optimization_plan",
        "category": "plan/preview",
        "permissions": {"read_project", "unity_run_validation", "unity_scan_scene"},
        "write": False,
        "min_version": "1.3.0",
    },
)


def _projection(gateway: AgentGateway) -> SkillPackageProjectionService:
    return SkillPackageProjectionService(
        SkillPackageProjectionPorts(
            user_skills_dir=lambda: gateway.user_skills_dir,
            user_skill_lock=gateway.user_skill_lock,
            find_user_skill=gateway._find_user_skill,  # noqa: SLF001 - isolated runtime projection fixture.
            parse_skill=parse_skill_markdown,
            parse_error_types=(AgentGatewayError,),
            state_name=PROJECTED_SKILL_STATE_NAME,
            state_schema=PROJECTED_SKILL_STATE_SCHEMA,
            state_max_bytes=PROJECTED_SKILL_STATE_MAX_BYTES,
        )
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_unity_project(root: Path) -> Path:
    project = root / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Assets" / "example.txt").write_text("example", encoding="utf-8")
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3",
        encoding="utf-8",
    )
    return project


@pytest.mark.parametrize("case", EXAMPLES, ids=lambda case: str(case["slug"]))
def test_example_skill_package_source_is_exportable_and_primitive(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    source = EXAMPLE_ROOT / str(case["slug"])
    manifest = _load_json(source / "manifest.json")
    workflow_path = source / manifest["entrypoints"]["workflow"]
    workflow = _load_json(workflow_path)

    assert manifest["id"] == case["package_id"]
    assert manifest["skill_name"] == case["skill_name"]
    assert manifest["min_vrcforge_version"] == case["min_version"]
    assert set(manifest["permissions"]) == case["permissions"]
    assert manifest["entrypoints"]["skill"] == "SKILL.md"
    assert workflow["schema"] == "vrcforge.skill-package.workflow.v1"
    assert len(workflow["steps"]) == 1
    assert workflow["steps"][0]["tool"] == case["workflow_tool"]
    assert workflow["steps"][0]["writes"] is case["write"]
    assert workflow["approval"]["required"] is case["write"]
    assert workflow["rollback"]["required"] is case["write"]
    skill_metadata = parse_skill_markdown(source / manifest["entrypoints"]["skill"])
    assert set(skill_metadata["supportFiles"]) == {
        value for name, value in manifest["entrypoints"].items() if name != "skill"
    }

    service = SkillPackageService(tmp_path / "store", vrcforge_version="1.4.0")
    package = service.export_dev(source, tmp_path / f"{case['slug']}.vsk").package_path
    preview = service.preflight_import(package).as_dict()

    assert preview["manifest"]["id"] == case["package_id"]
    assert preview["governance"]["importAllowed"] is True
    assert preview["dryRun"]["willWrite"] is False


def test_examples_keep_writes_on_existing_supervised_channels() -> None:
    material = _load_json(
        EXAMPLE_ROOT / "material-preset-pack" / "workflows" / "material-preset-pack.json"
    )
    material_request = material["steps"][0]["request"]
    assert material_request == {
        "targetTool": "vrcforge_apply_shader_tuning",
        "presetSource": "presets/material-presets.json",
        "onePresetPerExecution": True,
    }
    presets = _load_json(
        EXAMPLE_ROOT / "material-preset-pack" / "presets" / "material-presets.json"
    )
    assert presets["schema"] == "vrcforge.material-preset-pack.v1"
    assert {key for preset in presets["presets"] for key in preset["values"]} <= {
        "smoothness",
        "specular_strength",
        "emission_strength",
    }

    naming = _load_json(
        EXAMPLE_ROOT / "outfit-naming-helper" / "workflows" / "outfit-naming-helper.json"
    )
    naming_step = naming["steps"][0]
    naming_request = naming_step["request"]
    naming_manifest = _load_json(EXAMPLE_ROOT / "outfit-naming-helper" / "manifest.json")
    naming_skill = parse_skill_markdown(EXAMPLE_ROOT / "outfit-naming-helper" / "SKILL.md")
    assert naming_step["tool"] == "vrcforge_request_apply"
    assert naming_step["writes"] is True
    assert naming_request["targetTool"] == "vrcforge_unity_mcp_write"
    assert naming_request["scanTool"] == "vrcforge_scan_animation_bindings"
    assert naming_request["previewTool"] == "vrcforge_preview_atomic_reference_rename"
    assert naming_request["oneOperationPerExecution"] is True
    wrappers = naming_request["argumentsByOperation"]
    assert set(wrappers) == {"game_object", "parameter"}
    assert set(wrappers["game_object"]["arguments"]) == {
        "operationKind",
        "scenePath",
        "avatarPath",
        "targetObjectPath",
        "newName",
    }
    assert set(wrappers["parameter"]["arguments"]) == {
        "operationKind",
        "scenePath",
        "avatarPath",
        "oldParameterName",
        "newParameterName",
    }
    assert all(wrapper["toolName"] == "vrc_atomic_reference_rename" for wrapper in wrappers.values())
    assert all(set(wrapper) == {"projectPath", "toolName", "arguments"} for wrapper in wrappers.values())
    assert naming["approval"]["required"] is True
    assert naming["checkpoint"]["required"] is True
    assert naming["rollback"] == {
        "required": True,
        "tool": "vrcforge_restore_checkpoint",
        "requiresSeparateApproval": True,
        "reason": "Restore the pre-write checkpoint only through a separate supervised approval.",
    }
    assert set(naming_manifest["permissions"]) == {
        "read_project",
        "unity_scan_scene",
        "write_project_files",
    }
    assert naming_skill["permissionMode"] == "approval_required"
    assert naming_skill["riskLevel"] == "high"
    assert naming_skill["allowedTools"] == [
        "vrcforge_scan_animation_bindings",
        "vrcforge_preview_atomic_reference_rename",
        "vrcforge_request_apply",
    ]
    assert "vrcforge_unity_mcp_write" not in naming_skill["allowedTools"]
    assert "vrc_atomic_reference_rename" not in naming_skill["allowedTools"]
    assert "vrcforge_rename_gameobject" not in naming_skill["allowedTools"]


@pytest.mark.parametrize("case", EXAMPLES, ids=lambda case: str(case["slug"]))
def test_example_skill_package_trust_import_execute_and_audit(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    source = EXAMPLE_ROOT / str(case["slug"])
    service = SkillPackageService(tmp_path / "app" / "skill-packages", vrcforge_version="1.4.0")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(
        source,
        tmp_path / f"{case['slug']}.vsk",
        key_pair.private_key_pem,
    ).package_path

    untrusted = service.preflight_import(package).as_dict()
    assert untrusted["governance"]["signatureVerified"] is True
    assert untrusted["governance"]["signerTrustStatus"] == "untrusted"
    assert untrusted["governance"]["safeMode"]["defaultEnabled"] is False

    service.trust_signer(key_pair.fingerprint, reason="example package test signer")
    trusted = service.preflight_import(package).as_dict()
    assert trusted["governance"]["signerTrustStatus"] == "trusted"
    assert trusted["governance"]["safeMode"]["defaultEnabled"] is True

    installed = service.install(package, source="example-package-test")
    assert installed.registry_entry["enabled"] is True
    package_audit = [entry["event"] for entry in service.load_registry()["audit"]]
    assert "skill_package_signer_trusted" in package_audit
    assert "skill_package_imported" in package_audit

    gateway = AgentGateway(tmp_path / "app" / "config" / "agent_gateway.json", tmp_path / "audit")
    project = _make_unity_project(tmp_path)
    calls: list[dict[str, Any]] = []

    if case["write"]:
        gateway.register_tool(
            "vrcforge_request_apply",
            "Request approval for one write.",
            "supervised-write",
            gateway.create_apply_request,
            write=True,
        )
        target_names = {str(case["target_tool"])}
        for target_name in target_names:
            def apply_example(arguments: dict[str, Any], _target: str = target_name) -> dict[str, Any]:
                calls.append({"tool": _target, "arguments": dict(arguments)})
                (project / "Assets" / "example.txt").write_text(f"changed by {_target}", encoding="utf-8")
                return {"ok": True, "tool": _target}

            gateway.register_write_handler(
                target_name,
                f"Example handler for {target_name}.",
                "high",
                apply_example,
            )
        skill_metadata = parse_skill_markdown(source / "SKILL.md")
        for tool_name in skill_metadata["allowedTools"]:
            if tool_name in {"vrcforge_request_apply", *target_names}:
                continue
            category = "plan/preview" if "preview" in tool_name else "read/debug"
            gateway.register_tool(
                tool_name,
                f"Example handler for {tool_name}.",
                category,
                lambda arguments, _tool=tool_name: {
                    "ok": True,
                    "tool": _tool,
                    "arguments": dict(arguments),
                },
            )
    else:
        entrypoint = str(case["entrypoint_tool"])
        gateway.register_tool(
            entrypoint,
            f"Example handler for {entrypoint}.",
            str(case["category"]),
            lambda arguments: calls.append({"tool": entrypoint, "arguments": dict(arguments)})
            or {"ok": True, "tool": entrypoint},
        )

    projection = _projection(gateway).project_installed(
        installed.installed_path,
        installed.preview.manifest,
    )
    assert projection is not None
    assert projection["name"] == case["skill_name"]
    loaded = gateway.execute_runtime_skill(
        str(case["skill_name"]),
        {"projectPath": str(project), "arguments": "example target"},
        "example-package-test",
    )

    if case["write"]:
        assert loaded["status"] == "loaded"
        assert calls == []
        runtime_support = {
            item["path"]: item["content"]
            for item in loaded["result"]["supportFiles"]
        }
        workflow_path = _load_json(source / "manifest.json")["entrypoints"]["workflow"]
        workflow = json.loads(runtime_support[workflow_path])
        request_definition = workflow["steps"][0]["request"]
        target_tool = str(request_definition["targetTool"])
        assert target_tool == case["target_tool"]
        request_arguments: dict[str, Any] = {
            "projectRoot": str(project),
            "example": str(case["slug"]),
        }
        if case["slug"] == "outfit-naming-helper":
            request_arguments = {
                "projectPath": str(project),
                "toolName": "vrc_atomic_reference_rename",
                "arguments": {
                    "operationKind": "game_object",
                    "scenePath": "Assets/Avatar.unity",
                    "avatarPath": "/Avatar",
                    "targetObjectPath": "/Avatar/OldOutfit",
                    "newName": "Outfit_Casual",
                },
            }
        request = gateway.create_apply_request(
            {
                "target_tool": target_tool,
                "arguments": request_arguments,
                "reason": "Example package gated execution test.",
            }
        )
        approval_id = request["approval"]["id"]
        gateway.approve(approval_id)
        applied = gateway.apply_approved({"approval_id": approval_id})

        assert applied["ok"] is True
        assert applied["checkpoint"]["ok"] is True
        assert [call["tool"] for call in calls] == [target_tool]
        assert calls[0]["arguments"] == request_arguments
        assert (project / "Assets" / "example.txt").read_text(encoding="utf-8") == f"changed by {target_tool}"
        restored = gateway.restore_checkpoint(
            {"checkpointId": applied["checkpoint"]["id"], "confirmRestore": True}
        )
        assert restored["ok"] is True
        assert (project / "Assets" / "example.txt").read_text(encoding="utf-8") == "example"
    else:
        assert loaded["status"] == "executed"
        assert loaded["entrypointTool"] == case["entrypoint_tool"]
        assert [call["tool"] for call in calls] == [case["entrypoint_tool"]]

    runtime_audit = gateway.recent_audit_logs(limit=50)
    runtime_events = [entry["event"] for entry in runtime_audit]
    assert "runtime_skill_package_loaded" in runtime_events
    loaded_events = [entry for entry in runtime_audit if entry.get("event") == "runtime_skill_package_loaded"]
    assert loaded_events[-1]["packageId"] == case["package_id"]
    assert loaded_events[-1]["signerFingerprint"] == key_pair.fingerprint
    if case["write"]:
        assert "approval_requested" in runtime_events
        assert "approval_applied" in runtime_events
        assert "checkpoint_restored" in runtime_events
    else:
        assert "runtime_skill_entrypoint_executed" in runtime_events


def test_signed_material_package_install_projection_and_runtime_support_are_complete(
    tmp_path: Path,
) -> None:
    gateway = AgentGateway(tmp_path / "app" / "config" / "agent_gateway.json", tmp_path / "audit")
    service = SkillPackageService(tmp_path / "app" / "skill-packages", vrcforge_version="1.3.0")
    key_pair = service.generate_signing_keypair()
    package = service.export_release(
        EXAMPLE_ROOT / "material-preset-pack",
        tmp_path / "material-preset-pack.vsk",
        key_pair.private_key_pem,
    ).package_path
    service.trust_signer(key_pair.fingerprint, reason="projection integration fixture")
    installed = service.install(package, source="projection-integration-test")

    gateway.register_tool(
        "vrcforge_request_apply",
        "Request approval for one material write.",
        "supervised-write",
        gateway.create_apply_request,
        write=True,
    )
    gateway.register_write_handler(
        "vrcforge_apply_shader_tuning",
        "Apply shader tuning.",
        "high",
        lambda arguments: {"ok": True, "arguments": arguments},
    )
    projection = _projection(gateway).project_installed(
        installed.installed_path,
        installed.preview.manifest,
    )

    assert projection is not None
    assert set(projection["supportFiles"]) == {
        "workflows/material-preset-pack.json",
        "presets/material-presets.json",
    }
    for relative in projection["supportFiles"]:
        assert (gateway.user_skills_dir / "material-preset-pack" / relative).is_file()

    loaded = gateway.execute_runtime_skill("material-preset-pack", {}, "projection-integration-test")

    assert loaded["status"] == "loaded"
    support = {item["path"]: item["content"] for item in loaded["result"]["supportFiles"]}
    assert set(support) == {
        "workflows/material-preset-pack.json",
        "presets/material-presets.json",
    }
    assert json.loads(support["workflows/material-preset-pack.json"])["steps"][0]["tool"] == "vrcforge_request_apply"
    assert json.loads(support["presets/material-presets.json"])["schema"] == "vrcforge.material-preset-pack.v1"
    package_events = [
        event for event in gateway.recent_audit_logs(limit=20) if event.get("event") == "runtime_skill_package_loaded"
    ]
    assert package_events[-1]["packageId"] == "community.examples.material-preset-pack"
    assert package_events[-1]["signerFingerprint"] == key_pair.fingerprint

from __future__ import annotations

import copy
from pathlib import Path

import pytest

import dashboard_server
from agent_gateway import AgentGatewayConfig
from optimization_service import (
    OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES,
    OPTIMIZATION_TOOL_DEFINITIONS,
    STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES,
    build_dependency_doctor,
    build_optimization_report,
    build_optimization_tool_result,
)


def make_unity_project(root: Path) -> None:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "ProjectSettings").mkdir()
    (root / "Packages" / "manifest.json").write_text('{"dependencies":{}}', encoding="utf-8")
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.22f1", encoding="utf-8")


def install_package(root: Path, package_id: str, version: str = "1.0.0") -> None:
    package_dir = root / "Packages" / package_id
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "package.json").write_text(
        f'{{"name":"{package_id}","version":"{version}"}}',
        encoding="utf-8",
    )


def fake_validation() -> dict:
    return {
        "ok": True,
        "schema": "vrcforge.validation.v1",
        "summary": {"severityCounts": {"Error": 0, "Warning": 0, "Suggestion": 1, "Info": 1, "Ignored": 0}},
        "gate": {"status": "pass"},
        "sources": {
            "performance_pc": {
                "ok": True,
                "payload": {
                    "rank": "Good",
                    "triangleCount": 42000,
                    "downloadSizeBytes": 260 * 1024 * 1024,
                    "uncompressedSizeBytes": 420 * 1024 * 1024,
                    "textureMemoryBytes": 96 * 1024 * 1024,
                    "materialSlotCount": 12,
                    "skinnedMeshCount": 3,
                    "physBoneCount": 36,
                    "physBoneAffectedTransforms": 290,
                    "physBoneColliderCount": 12,
                    "physBoneCollisionCheckCount": 540,
                    "meshReadWriteWarning": "Mesh Read/Write Disabled",
                },
            },
            "performance_quest": {
                "ok": True,
                "payload": {
                    "rank": "Medium",
                    "triangleCount": 42000,
                    "downloadSizeBytes": 8 * 1024 * 1024,
                    "uncompressedSizeBytes": 55 * 1024 * 1024,
                    "textureMemoryBytes": 38 * 1024 * 1024,
                },
            },
            "materials": {
                "ok": True,
                "payload": {
                    "renderers": [
                        {
                            "rendererPath": "Avatar/Body",
                            "materials": [
                                {"name": "SkinLil", "shaderName": "lilToon"},
                                {"name": "HairPoiyomi", "shaderName": "Poiyomi Toon"},
                                {"name": "AccessoryStd", "shaderName": "Standard"},
                            ],
                            "textures": [{"textureName": "Assets/Avatar/body_albedo_4096.png", "width": 4096, "height": 4096}],
                        }
                    ]
                },
            },
            "parameters": {
                "ok": True,
                "payload": {
                    "totalParameters": 6,
                    "totalEstimatedCost": 280,
                    "parameterNames": [
                        {"name": "FaceTrackingFloat", "valueType": "Float", "networkSynced": True, "defaultValue": 0.0},
                        {"name": "OutfitToggleHat", "valueType": "Bool", "networkSynced": True, "defaultValue": 0.0},
                        {"name": "Wardrobe", "valueType": "Int", "networkSynced": True, "defaultValue": 0.0},
                        {"name": "LocalOnlyPreview", "valueType": "Bool", "networkSynced": False, "defaultValue": 0.0},
                        {"name": "UnusedInt", "valueType": "Int", "networkSynced": True, "defaultValue": 0.0},
                        {"name": "PuppetAxis", "valueType": "Float", "networkSynced": True, "defaultValue": 0.0},
                    ],
                },
            },
            "menu": {
                "ok": True,
                "payload": {
                    "items": [
                        {"displayName": "Hat", "menuPath": "Wardrobe/Hat", "parameterName": "OutfitToggleHat", "controlType": "Toggle", "valueType": "Bool", "networkSynced": True},
                        {"displayName": "Casual", "menuPath": "Wardrobe/Casual", "parameterName": "Wardrobe", "controlType": "Toggle", "valueType": "Int", "networkSynced": True},
                        {"displayName": "Dress", "menuPath": "Wardrobe/Dress", "parameterName": "Wardrobe", "controlType": "Toggle", "valueType": "Int", "networkSynced": True},
                        {"displayName": "Puppet", "menuPath": "Face/Puppet", "parameterName": "PuppetAxis", "controlType": "TwoAxisPuppet", "valueType": "Float", "networkSynced": True},
                    ]
                },
            },
            "avatar_items": {
                "ok": True,
                "payload": {
                    "items": [
                        {
                            "gameObjectPath": "Avatar/HatAccessory",
                            "triangleCount": 3200,
                            "componentTypes": ["SkinnedMeshRenderer", "VRCPhysBone", "VRCContactReceiver"],
                            "physBoneAffectedTransforms": 18,
                            "physBoneColliderCount": 1,
                            "physBoneCollisionCheckCount": 12,
                            "renderer_count": 1,
                            "skinned_renderer_count": 1,
                            "material_summary": {"material_slot_count": 1},
                        },
                        {"gameObjectPath": "Avatar/Body Skin", "triangleCount": 22000, "componentTypes": ["SkinnedMeshRenderer"]},
                        {"gameObjectPath": "Avatar/Face", "triangleCount": 18000, "blendShapeCount": 80},
                        {"gameObjectPath": "Avatar/Sparkle", "componentTypes": ["ParticleSystem"], "renderer_count": 1, "skinned_renderer_count": 0},
                    ]
                },
            },
            "fx": {
                "ok": True,
                "payload": {
                    "layers": [
                        {
                            "layerName": "MA Responsive: Object Toggle Hat",
                            "transitions": [{"conditions": [{"parameter": "OutfitToggleHat", "mode": "If"}]}],
                        },
                        {
                            "layerName": "Wardrobe",
                            "transitions": [{"conditions": [{"parameter": "Wardrobe", "mode": "Equals", "threshold": 1}]}],
                        },
                        {
                            "layerName": "ma_to_blendtree: Already Converted",
                            "transitions": [],
                        },
                    ],
                    "parameters": [
                        {"name": "OutfitToggleHat", "type": "Bool", "used_by_condition": True},
                        {"name": "Wardrobe", "type": "Int", "used_by_condition": True},
                        {"name": "UnusedInt", "type": "Int", "used_by_condition": False},
                    ],
                },
            },
            "animation_bindings": {
                "ok": True,
                "payload": {"summary": {"clipCount": 4, "bindingCount": 21}},
            },
            "generated_residue": {"ok": True, "payload": {"residueCount": 0}},
        },
    }


def test_dependency_doctor_missing_plugins_is_graceful(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)

    payload = build_dependency_doctor({"projectPath": str(project)})

    assert payload["schema"] == "vrcforge.optimization.v1"
    assert payload["projectReadable"] is True
    assert payload["summary"]["missing"] >= 8
    assert all(item["status"] in {"installed", "missing", "unknown"} for item in payload["dependencies"])
    assert all(item["installMethod"]["automatic"] is False for item in payload["dependencies"])


def test_optimization_report_schema_is_stable_and_plan_only(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)

    payload = build_optimization_report({"projectPath": str(project), "targetProfile": "pc_medium"}, fake_validation())

    assert payload["schema"] == "vrcforge.optimization.v1"
    assert payload["readOnly"] is True
    assert payload["planOnly"] is True
    assert payload["noProjectWrites"] is True
    assert payload["directApplyExposed"] is False
    assert payload["targetProfile"]["id"] == "pc_medium"
    assert payload["baseline"]["performanceHeadline"]["pc"]["rank"] == "Good"
    assert payload["recommendedOrder"]
    assert {card["id"] for card in payload["actionCards"]} >= {
        "optimize_texture_memory",
        "reduce_material_slots",
        "check_parameter_budget",
        "plan_mesh_simplification",
    }
    assert all(tool["directApplyExposed"] is False for tool in payload["tools"])
    assert all(item["externalName"].endswith("apply-request") for item in payload["futureWriteRequestTools"])


def test_plan_tools_do_not_write(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    params = {"projectPath": str(project), "targetProfile": "pc_conservative"}

    for definition in OPTIMIZATION_TOOL_DEFINITIONS:
        payload = build_optimization_tool_result(definition["externalName"], params, fake_validation())
        assert payload["schema"] == "vrcforge.optimization.v1"
        assert payload["readOnly"] is True
        assert payload["noProjectWrites"] is True
        assert payload["directApplyExposed"] is False
        assert "apply" not in payload["gatewayTool"].replace("profile_plan", "").replace("atlas_plan", "").replace("trace_plan", "").replace("simplify_plan", "")


def test_upload_gate_audit_separates_hard_blockers_from_rank_offenders(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)

    payload = build_optimization_tool_result(
        "optimization.upload-gate.audit",
        {"projectPath": str(project), "targetProfile": "pc_conservative"},
        fake_validation(),
    )

    result = payload["result"]
    assert payload["readOnly"] is True
    assert payload["planOnly"] is False
    assert result["summary"]["hardBlockerCount"] == 3
    assert {item["id"] for item in result["groups"]["hardUploadBlockers"]} == {
        "pc_download_size",
        "android_uncompressed_size",
        "synced_parameter_bits",
    }
    assert "mesh_read_write_disabled" in {item["id"] for item in result["groups"]["riskyFixes"]}
    assert result["metrics"]["pc"]["uncompressedSizeBytes"] == 420 * 1024 * 1024
    assert result["metrics"]["parameters"]["totalCustomParameters"] == 6


def test_parameter_hard_gate_surfaces_are_read_only_and_conservative(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    validation = fake_validation()

    inventory = build_optimization_tool_result("optimization.parameter.inventory", {"projectPath": str(project)}, validation)["result"]
    menu_map = build_optimization_tool_result("optimization.parameter.menu-map", {"projectPath": str(project)}, validation)["result"]
    usage = build_optimization_tool_result("optimization.parameter.animator-usage", {"projectPath": str(project)}, validation)["result"]
    compressibility = build_optimization_tool_result("optimization.parameter.compressibility-plan", {"projectPath": str(project)}, validation)
    vrcfury_plan = build_optimization_tool_result("optimization.parameter.vrcfury-compressor-plan", {"projectPath": str(project)}, validation)

    assert inventory["summary"]["syncedBits"] == 280
    assert inventory["summary"]["totalCustomParameters"] == 6
    assert menu_map["summary"]["mappedParameterCount"] == 3
    assert usage["summary"]["conditionParameterCount"] == 2
    assert compressibility["planOnly"] is True
    categories = compressibility["result"]["categories"]
    assert "FaceTrackingFloat" in {item["name"] for item in categories["danger_osc_or_face_tracking"]}
    assert "PuppetAxis" in {item["name"] for item in categories["danger_puppet"]}
    assert "OutfitToggleHat" in {item["name"] for item in categories["safe_to_pack"]}
    assert "Wardrobe" in {item["name"] for item in categories["safe_to_int_exclusive"]}
    assert vrcfury_plan["result"]["experimentalOnly"] is True
    assert vrcfury_plan["result"]["applyBlocked"] is True


def test_advanced_optimization_0_9_surfaces_are_plan_only(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    validation = fake_validation()
    params = {"projectPath": str(project), "targetProfile": "event_light"}

    report = build_optimization_report(params, validation)
    physbone = build_optimization_tool_result("optimization.physbone.audit", params, validation)
    physbone_plan = build_optimization_tool_result("optimization.physbone.reduce-plan", params, validation)
    hidden_body = build_optimization_tool_result("optimization.aao.hidden-body-cut-plan", params, validation)
    shader_registry = build_optimization_tool_result("optimization.shader.adapter-registry", params, validation)
    regression = build_optimization_tool_result("optimization.parameter.behavior-regression", params, validation)
    path_to_skill = build_optimization_tool_result("optimization.parameter.path-to-skill", params, validation)
    ma2bt_skipped = build_optimization_tool_result("optimization.ma2bt.skipped-reasons", params, validation)

    assert report["audits"]["physBones"]["summary"]["reportedComponentCount"] == 36
    assert report["audits"]["shaderAdapterRegistry"]["summary"]["detectedAdapters"] == [
        "generic-semantic",
        "liltoon",
        "poiyomi",
    ]
    assert report["audits"]["ma2btSkippedReasons"]["summary"]["skippedLayerCount"] == 1
    assert report["plans"]["physBoneReduce"]["planOnly"] is True
    assert report["plans"]["physBoneReduce"]["applyRequestTool"] == "optimization.aao.physbone-cleanup-apply-request"
    assert report["plans"]["physBoneReduce"]["hardGate"]["status"] == "blocked"
    assert "rollback.proof" in report["plans"]["physBoneReduce"]["hardGate"]["blockingIds"]
    assert report["plans"]["hiddenBodyCut"]["applyBlocked"] is True
    assert report["plans"]["hiddenBodyCut"]["manualConfirmationRequired"] is True
    assert report["plans"]["hiddenBodyCut"]["hardGate"]["status"] == "blocked"
    assert report["plans"]["ma2btConvertibility"]["summary"]["skippedLayerCount"] == 1
    assert report["plans"]["ma2btConvertibility"]["diagnostics"][0]["recommendedAction"]
    assert report["plans"]["parameterBehaviorRegression"]["proofReady"] is False
    assert report["plans"]["parameterPathToSkill"]["applyBlocked"] is True
    assert physbone["readOnly"] is True
    assert physbone["planOnly"] is False
    assert {row["id"] for row in physbone["result"]["metrics"] if row["status"] == "offender"} >= {
        "physbone_components",
        "physbone_affected_transforms",
        "physbone_collision_checks",
    }
    assert physbone_plan["result"]["requiredProof"]
    assert hidden_body["planOnly"] is True
    assert hidden_body["result"]["candidateCount"] >= 1
    assert hidden_body["result"]["applyRequestTool"] == "optimization.aao.hidden-body-cut-apply-request"
    assert "visual_review.proof" in hidden_body["result"]["hardGate"]["blockingIds"]
    assert shader_registry["result"]["rawPropertyMutationBlocked"] is True
    assert {item["adapter"] for item in shader_registry["result"]["materialCoverage"]} >= {
        "generic-semantic",
        "liltoon",
        "poiyomi",
    }
    assert ma2bt_skipped["result"]["diagnostics"][0]["recommendedAction"]
    assert regression["result"]["summary"]["testCaseCount"] >= 3
    assert regression["result"]["summary"]["dangerParameterCount"] >= 2
    assert "optimization.parameter.behavior-regression" in {
        step["tool"] for step in path_to_skill["result"]["skillPath"]
    }
    assert path_to_skill["result"]["hardGates"]["blockedParameterCount"] >= 2


def test_rollback_verify_requires_git_like_ma_vrcf_ndmf_coverage(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "nadena.dev.modular-avatar", "1.17.1")
    install_package(project, "com.vrcfury.vrcfury", "1.1334.0")
    install_package(project, "nadena.dev.ndmf", "1.13.1")
    validation = copy.deepcopy(fake_validation())
    validation["sources"]["avatar_items"]["payload"]["items"].append(
        {
            "gameObjectPath": "Avatar/VRCFury Toggle",
            "componentTypes": ["VF.Model.VRCFury", "VRCFuryToggle"],
        }
    )
    validation["sources"]["generated_residue"] = {"ok": True, "payload": {"residueCount": 0}}

    rollback = build_optimization_tool_result("optimization.rollback.verify", {"projectPath": str(project)}, validation)["result"]

    assert rollback["gitLikeRollbackRequired"] is True
    assert rollback["hardGate"]["status"] == "pass"
    coverage = {item["id"]: item for item in rollback["coverage"]}
    assert coverage["checkpoint.assets"]["status"] == "pass"
    assert coverage["checkpoint.packages"]["evidence"]["unityCacheCleanup"] == [
        "Library/Bee",
        "Library/ScriptAssemblies",
        "Library/PackageCache",
    ]
    assert coverage["checkpoint.project_settings"]["status"] == "pass"
    assert coverage["rollback.post_restore_validation"]["status"] == "pass"
    assert coverage["ecosystem.modular_avatar"]["status"] == "pass"
    assert coverage["ecosystem.vrcfury"]["status"] == "pass"
    assert coverage["ecosystem.ndmf"]["status"] == "pass"
    ecosystem = {item["id"]: item for item in rollback["ecosystemCoverage"]["components"]}
    assert ecosystem["modular_avatar"]["detected"] is True
    assert ecosystem["vrcfury"]["detected"] is True
    assert ecosystem["ndmf"]["detected"] is True
    assert rollback["ecosystemCoverage"]["requiresMaVrcfNdmfProof"] is True


def test_rollback_verify_blocks_when_ma_vrcf_validation_proof_is_missing(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "com.vrcfury.vrcfury", "1.1334.0")

    rollback = build_optimization_tool_result("optimization.rollback.verify", {"projectPath": str(project)}, {"sources": {}})["result"]

    assert rollback["hardGate"]["status"] == "blocked"
    assert "rollback.post_restore_validation" in rollback["hardGate"]["blockingIds"]
    assert "ecosystem.vrcfury" in rollback["hardGate"]["blockingIds"]
    vrcfury = next(item for item in rollback["ecosystemCoverage"]["components"] if item["id"] == "vrcfury")
    assert vrcfury["detected"] is True
    assert vrcfury["coverageStatus"] == "blocked"


def test_mcp_projection_exposes_read_plan_without_direct_apply() -> None:
    manifest = dashboard_server.AGENT_GATEWAY.build_manifest()
    tool_names = {tool["name"] for tool in manifest["tools"]}
    write_targets = {target["name"] for target in manifest["writeTargets"]}

    assert "vrcforge_optimization_plan" in tool_names
    for definition in OPTIMIZATION_TOOL_DEFINITIONS:
        assert definition["gatewayName"] in tool_names
        assert definition["gatewayName"] not in write_targets
    assert not any(name.startswith("vrcforge_optimization_") for name in write_targets)
    assert "vrcforge_optimization_lac_apply" not in tool_names
    assert "vrcforge_optimization_aao_apply" not in tool_names
    assert set(STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES) <= tool_names
    assert all(name.endswith("_apply_request") for name in STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES)
    assert "vrcforge_optimization_ttt_atlas_apply_request" in tool_names
    assert "vrcforge_optimization_meshia_simplify_apply_request" in tool_names
    assert "vrcforge_optimization_vrcfury_parameter_compressor_apply_request" in tool_names
    assert "vrcforge_optimization_vrcfury_direct_tree_apply_request" in tool_names
    assert "vrcforge_optimization_validation_delta" in tool_names
    assert "vrcforge_optimization_validation_delta" not in write_targets
    assert "vrcforge_scan_thry_avatar_performance" in tool_names
    assert "vrcforge_configure_optimizer_component" not in tool_names
    assert "vrcforge_configure_optimizer_component" not in write_targets
    assert "vrcforge_install_vpm_package" not in write_targets

    registry = dashboard_server.AGENT_GATEWAY.build_tool_registry()
    optimization_entries = [entry for entry in registry["tools"] if entry["name"].startswith("vrcforge_optimization")]
    assert optimization_entries
    assert all(entry["category"] == "optimization" for entry in optimization_entries)
    apply_request_entries = [entry for entry in optimization_entries if entry["name"] in STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES]
    assert apply_request_entries
    assert {entry["risk"] for entry in apply_request_entries} == {"write_request"}
    read_plan_entries = [entry for entry in optimization_entries if entry["name"] not in STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES]
    assert {entry["risk"] for entry in read_plan_entries} <= {"read_only", "plan"}
    direct_apply_names = {
        name
        for name in tool_names
        if name.startswith("vrcforge_optimization_") and name.endswith("_apply") and not name.endswith("_apply_request")
    }
    assert direct_apply_names == set()


def test_optimizer_apply_requests_are_stable_request_tools_without_direct_apply() -> None:
    manifest = dashboard_server.AGENT_GATEWAY.build_manifest()
    tool_names = {tool["name"] for tool in manifest["tools"]}
    unstable = set(OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES) - set(STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES)

    assert unstable == set()
    assert set(OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES) <= tool_names
    assert not any(
        name.startswith("vrcforge_optimization_") and name.endswith("_apply") and not name.endswith("_apply_request")
        for name in tool_names
    )


def test_avatar_optimization_skill_group_contains_stable_request_tools_only() -> None:
    skills = dashboard_server.AGENT_GATEWAY.build_skill_registry()["skills"]
    group = next(skill for skill in skills if skill["name"] == "avatar-optimization-skills")
    allowed = set(group["allowedTools"])

    assert set(STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES) <= allowed
    assert "vrcforge_optimization_validation_delta" in allowed
    assert "vrcforge_optimization_ttt_atlas_apply_request" in allowed
    assert "vrcforge_optimization_meshia_simplify_apply_request" in allowed
    assert "vrcforge_scan_thry_avatar_performance" in allowed
    assert "vrcforge_package_install_plan" in allowed
    assert "vrcforge_package_install_request" in allowed
    assert "vrcforge_configure_optimizer_component" not in allowed
    assert "vrcforge_install_vpm_package" not in allowed


def test_optimization_validation_delta_reports_improvement_and_rollback_match() -> None:
    before = {
        "schema": "vrcforge.validation.v1",
        "ok": True,
        "summary": {"severityCounts": {"Error": 0, "Warning": 1, "Suggestion": 1, "Info": 0, "Ignored": 0}, "findingCount": 2, "gateStatus": "pass"},
        "gate": {"status": "pass"},
        "sections": [
            {"id": "materials", "name": "Materials", "status": "warning", "counts": {"Error": 0, "Warning": 1, "Suggestion": 1, "Info": 0, "Ignored": 0}},
        ],
        "sources": {
            "performance_pc": {"ok": True, "payload": {"rank": "Poor", "triangleCount": 72000, "textureMemoryBytes": 180000000}},
            "performance_quest": {"ok": True, "payload": {"rank": "VeryPoor", "triangleCount": 42000}},
            "parameters": {"ok": True, "payload": {"totalEstimatedCost": 240, "totalParameters": 22}},
        },
        "findings": [
            {"section": "Materials", "severity": "Warning", "title": "Large texture", "source": "materials"},
            {"section": "Materials", "severity": "Suggestion", "title": "Atlas candidate", "source": "materials"},
        ],
    }
    after = {
        "schema": "vrcforge.validation.v1",
        "ok": True,
        "summary": {"severityCounts": {"Error": 0, "Warning": 0, "Suggestion": 1, "Info": 0, "Ignored": 0}, "findingCount": 1, "gateStatus": "pass"},
        "gate": {"status": "pass"},
        "sections": [
            {"id": "materials", "name": "Materials", "status": "review", "counts": {"Error": 0, "Warning": 0, "Suggestion": 1, "Info": 0, "Ignored": 0}},
        ],
        "sources": {
            "performance_pc": {"ok": True, "payload": {"rank": "Medium", "triangleCount": 68000, "textureMemoryBytes": 128000000}},
            "performance_quest": {"ok": True, "payload": {"rank": "Poor", "triangleCount": 39000}},
            "parameters": {"ok": True, "payload": {"totalEstimatedCost": 212, "totalParameters": 20}},
        },
        "findings": [
            {"section": "Materials", "severity": "Suggestion", "title": "Atlas candidate", "source": "materials"},
        ],
    }

    delta = dashboard_server.build_optimization_validation_delta_sync(
        {
            "optimizerTool": "optimization.lac.apply-request",
            "checkpointId": "ckpt_test",
            "beforeValidation": before,
            "afterValidation": after,
            "rollbackValidation": before,
        }
    )

    assert delta["schema"] == "vrcforge.optimization.validation_delta.v1"
    assert delta["readOnly"] is True
    assert delta["noProjectWrites"] is True
    assert delta["ok"] is True
    assert delta["status"] == "improved"
    assert delta["severityDelta"]["Warning"] == -1
    assert delta["findingDelta"]["removedCount"] == 1
    assert delta["profileDiff"]["pc"]["rankBefore"] == "Poor"
    assert delta["profileDiff"]["pc"]["rankAfter"] == "Medium"
    assert delta["profileDiff"]["pc"]["metricsDelta"]["triangles"] == -4000
    assert delta["profileDiff"]["quest"]["rankChanged"] is True
    assert delta["parameterBudgetDelta"]["syncedBitsDelta"] == -28
    assert delta["parameterBudgetDelta"]["totalCustomParametersDelta"] == -2
    assert delta["parameterBudgetDelta"]["rollbackMatchesBefore"] is True
    assert delta["rollbackProof"]["matchesBeforeSeverityAndGate"] is True


def test_optimization_validation_delta_flags_regression_and_rollback_drift() -> None:
    before = {
        "schema": "vrcforge.validation.v1",
        "ok": True,
        "summary": {"severityCounts": {"Error": 0, "Warning": 0, "Suggestion": 0, "Info": 0, "Ignored": 0}, "findingCount": 0, "gateStatus": "pass"},
        "gate": {"status": "pass"},
        "findings": [],
    }
    after = {
        "schema": "vrcforge.validation.v1",
        "ok": False,
        "summary": {"severityCounts": {"Error": 1, "Warning": 0, "Suggestion": 0, "Info": 0, "Ignored": 0}, "findingCount": 1, "gateStatus": "blocked"},
        "gate": {"status": "blocked"},
        "findings": [{"section": "Unity compile", "severity": "Error", "title": "Compile error", "source": "compile"}],
    }
    rollback = {
        "schema": "vrcforge.validation.v1",
        "ok": True,
        "summary": {"severityCounts": {"Error": 0, "Warning": 1, "Suggestion": 0, "Info": 0, "Ignored": 0}, "findingCount": 1, "gateStatus": "pass"},
        "gate": {"status": "pass"},
        "findings": [{"section": "Materials", "severity": "Warning", "title": "New rollback warning", "source": "materials"}],
    }

    delta = dashboard_server.build_optimization_validation_delta_sync(
        {
            "optimizerTool": "optimization.meshia.simplify-apply-request",
            "beforeValidation": before,
            "afterValidation": after,
            "rollbackValidation": rollback,
        }
    )

    assert delta["ok"] is False
    assert delta["status"] == "regressed"
    assert delta["severityDelta"]["Error"] == 1
    assert delta["rollbackProof"]["matchesBeforeSeverityAndGate"] is False


def test_wrapper_only_optimizer_targets_reject_generic_apply_request(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_server.AGENT_GATEWAY,
        "ensure_config",
        lambda: AgentGatewayConfig(enabled=True, allow_write_requests=True),
    )
    with pytest.raises(dashboard_server.AgentGatewayError, match="dedicated VRCForge request tool"):
        dashboard_server.AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": "vrcforge_configure_optimizer_component",
                "arguments": {},
                "reason": "direct optimizer write should not be requestable",
                "preview": {},
            }
        )


def test_optimizer_apply_request_requires_explicit_approval_even_in_auto_mode(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "dev.limitex.avatar-compressor", "0.8.0")
    original_approvals = dict(dashboard_server.AGENT_GATEWAY._approvals)
    dashboard_server.AGENT_GATEWAY._approvals.clear()
    monkeypatch.setattr(
        dashboard_server.AGENT_GATEWAY,
        "ensure_config",
        lambda: AgentGatewayConfig(enabled=True, allow_write_requests=True, execution_mode="auto"),
    )
    try:
        payload = dashboard_server.request_optimization_apply_sync(
            {
                "tool": "optimization.lac.apply-request",
                "projectPath": str(project),
                "avatarPath": "Avatar",
                "targetProfile": "pc_conservative",
            },
            agent_name="test-agent",
        )

        assert payload["ok"] is True
        assert payload["status"] == "pending"
        assert payload.get("autoApproved") is not True
        approval = payload["approval"]
        assert approval["status"] == "pending"
        assert approval["targetTool"] == "vrcforge_configure_optimizer_component"
        assert approval["requiresExplicitApproval"] is True
        assert approval["autoApprovalBlocked"] is True
        assert "Optimizer apply requests" in approval["explicitApprovalReason"]
    finally:
        dashboard_server.AGENT_GATEWAY._approvals.clear()
        dashboard_server.AGENT_GATEWAY._approvals.update(original_approvals)


def test_non_optimizer_apply_request_can_still_auto_approve(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    original_approvals = dict(dashboard_server.AGENT_GATEWAY._approvals)
    original_handlers = dict(dashboard_server.AGENT_GATEWAY._write_handlers)
    original_prepare = dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler
    dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = lambda _root: {"ok": True}
    dashboard_server.AGENT_GATEWAY._approvals.clear()
    calls: list[dict] = []

    def write_handler(args: dict) -> dict:
        calls.append(args)
        return {"ok": True, "value": args.get("value")}

    monkeypatch.setattr(
        dashboard_server.AGENT_GATEWAY,
        "ensure_config",
        lambda: AgentGatewayConfig(enabled=True, allow_write_requests=True, execution_mode="auto"),
    )
    try:
        dashboard_server.AGENT_GATEWAY.register_write_handler(
            "vrcforge_test_auto_write",
            "Test auto write.",
            "high",
            write_handler,
        )
        payload = dashboard_server.AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": "vrcforge_test_auto_write",
                "arguments": {"projectRoot": str(project), "value": "kept"},
                "preview": {"ok": True},
            }
        )

        assert payload["ok"] is True
        assert payload["status"] == "executed"
        assert payload["autoApproved"] is True
        assert payload["approval"]["status"] == "applied"
        assert len(calls) == 1
        assert calls[0]["value"] == "kept"
        assert calls[0]["projectRoot"] == str(project)
    finally:
        dashboard_server.AGENT_GATEWAY._approvals.clear()
        dashboard_server.AGENT_GATEWAY._approvals.update(original_approvals)
        dashboard_server.AGENT_GATEWAY._write_handlers = original_handlers
        dashboard_server.AGENT_GATEWAY.checkpoint_prepare_handler = original_prepare


def test_stable_apply_request_preview_is_lightweight_and_ready_for_installed_dependency(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "dev.limitex.avatar-compressor", "0.8.0")

    def fail_full_plan(_params):
        raise AssertionError("apply-request preview must not run the full optimization plan")

    monkeypatch.setattr(dashboard_server, "build_optimization_plan_sync", fail_full_plan)
    monkeypatch.setattr(
        dashboard_server,
        "package_install_plan_sync",
        lambda _params: (_ for _ in ()).throw(AssertionError("installed dependency must not build an install plan")),
    )

    payload = dashboard_server.build_optimization_apply_request_preview_sync(
        {
            "tool": "optimization.lac.apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetProfile": "pc_conservative",
        }
    )

    assert payload["readyToRequest"] is True
    assert payload["stableCallable"] is True
    assert payload["writeSupported"] is True
    assert payload["dependencyInstallPlan"] is None
    assert payload["applyArguments"]["componentType"] == "dev.limitex.avatar.compressor.TextureCompressor"


def test_ttt_apply_request_is_stable_but_requires_confirmed_material_paths(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "net.rs64.tex-trans-tool", "1.1.0-beta.8")

    blocked = dashboard_server.build_optimization_apply_request_preview_sync(
        {
            "tool": "optimization.ttt.atlas-apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetProfile": "pc_conservative",
        }
    )

    assert blocked["stableCallable"] is True
    assert blocked["writeSupported"] is True
    assert blocked["readyToRequest"] is False
    assert any("material asset paths" in reason for reason in blocked["blockedReasons"])

    ready = dashboard_server.build_optimization_apply_request_preview_sync(
        {
            "tool": "optimization.ttt.atlas-apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetProfile": "pc_conservative",
            "options": {"atlasTargetMaterials": ["Assets/Avatar/Materials/Body.mat"]},
        }
    )

    assert ready["readyToRequest"] is True
    assert ready["applyArguments"]["componentType"] == "net.rs64.TexTransTool.TextureAtlas.AtlasTexture"
    assert ready["applyArguments"]["options"]["atlasTargetMaterials"] == ["Assets/Avatar/Materials/Body.mat"]


def test_meshia_apply_request_targets_renderer_and_blocks_aggressive_ratios(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "com.ramtype0.meshia.mesh-simplification", "3.2.0")

    missing_renderer = dashboard_server.build_optimization_apply_request_preview_sync(
        {
            "tool": "optimization.meshia.simplify-apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetProfile": "pc_conservative",
        }
    )
    assert missing_renderer["stableCallable"] is True
    assert missing_renderer["readyToRequest"] is False
    assert any("rendererPath" in reason for reason in missing_renderer["blockedReasons"])

    aggressive = dashboard_server.build_optimization_apply_request_preview_sync(
        {
            "tool": "optimization.meshia.simplify-apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetProfile": "pc_conservative",
            "options": {"rendererPath": "Avatar/HatAccessory", "relativeVertexCount": 0.4},
        }
    )
    assert aggressive["readyToRequest"] is False
    assert any("experimental" in reason for reason in aggressive["blockedReasons"])

    ready = dashboard_server.build_optimization_apply_request_preview_sync(
        {
            "tool": "optimization.meshia.simplify-apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetProfile": "pc_conservative",
            "options": {"rendererPath": "Avatar/HatAccessory", "relativeVertexCount": 0.9},
        }
    )
    assert ready["readyToRequest"] is True
    assert ready["applyArguments"]["targetPath"] == "Avatar/HatAccessory"
    assert ready["applyArguments"]["componentType"] == "Meshia.MeshSimplification.Ndmf.MeshiaMeshSimplifier"


def test_configure_optimizer_component_saves_dirty_scene_assets(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    added_requests: list[dict] = []
    property_requests: list[dict] = []
    save_requests: list[Path] = []

    monkeypatch.setattr(dashboard_server, "_component_already_present", lambda *_args: (False, {}))
    monkeypatch.setattr(
        dashboard_server,
        "add_component_sync",
        lambda request: added_requests.append(request) or {"ok": True, "componentIndex": 0},
    )
    monkeypatch.setattr(
        dashboard_server,
        "set_component_property_sync",
        lambda request: property_requests.append(request) or {"ok": True, "propertyPath": request["propertyPath"]},
    )
    monkeypatch.setattr(
        dashboard_server,
        "prepare_unity_checkpoint_sync",
        lambda path: save_requests.append(path) or {"ok": True, "projectPath": str(path), "stdout": "saved", "stderr": ""},
    )

    result = dashboard_server.configure_optimizer_component_sync(
        {
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetPath": "Avatar/HatAccessory",
            "optimizerId": "meshia",
            "mode": "meshia_simplify",
            "componentType": "Meshia.MeshSimplification.Ndmf.MeshiaMeshSimplifier",
            "profile": "pc_conservative",
            "options": {"rendererPath": "Avatar/HatAccessory", "relativeVertexCount": 0.9},
        }
    )

    assert result["ok"] is True
    assert added_requests[0]["gameObjectPath"] == "Avatar/HatAccessory"
    assert property_requests[0]["propertyPath"] == "target"
    assert save_requests == [project]
    assert result["save"]["ok"] is True
    assert any(step["id"] == "save_dirty_scene_assets" and step["status"] == "done" for step in result["steps"])


def test_configure_optimizer_component_fails_when_dirty_scene_save_fails(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)

    monkeypatch.setattr(dashboard_server, "_component_already_present", lambda *_args: (False, {}))
    monkeypatch.setattr(dashboard_server, "add_component_sync", lambda _request: {"ok": True, "componentIndex": 0})
    monkeypatch.setattr(dashboard_server, "set_component_property_sync", lambda request: {"ok": True, "propertyPath": request["propertyPath"]})
    monkeypatch.setattr(
        dashboard_server,
        "prepare_unity_checkpoint_sync",
        lambda _path: {"ok": False, "stderr": "could not save scene"},
    )

    result = dashboard_server.configure_optimizer_component_sync(
        {
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetPath": "Avatar/HatAccessory",
            "optimizerId": "meshia",
            "mode": "meshia_simplify",
            "componentType": "Meshia.MeshSimplification.Ndmf.MeshiaMeshSimplifier",
            "profile": "pc_conservative",
            "options": {"rendererPath": "Avatar/HatAccessory", "relativeVertexCount": 0.9},
        }
    )

    assert result["ok"] is False
    assert "could not save scene" in result["error"]
    assert any(step["id"] == "save_dirty_scene_assets" and step["status"] == "failed" for step in result["steps"])


def test_vrcfury_apply_requests_are_stable_blocked_surfaces(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "com.vrcfury.vrcfury", "1.1334.0")

    payload = dashboard_server.build_optimization_apply_request_preview_sync(
        {
            "tool": "optimization.vrcfury.parameter-compressor-apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetProfile": "pc_conservative",
        }
    )

    assert payload["stableCallable"] is True
    assert payload["writeSupported"] is False
    assert payload["readyToRequest"] is False
    assert any("public validated writer path" in reason for reason in payload["blockedReasons"])
    assert payload["hardGate"]["status"] == "blocked"
    assert "experimental.writer_proof" in payload["hardGate"]["blockingIds"]
    assert payload["rollbackRequirements"]["postRestoreValidationRequired"] is True


def test_hidden_body_and_physbone_apply_surfaces_are_blocked_with_hard_gates(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    install_package(project, "com.anatawa12.avatar-optimizer", "1.8.0")

    hidden_body = dashboard_server.build_optimization_apply_request_preview_sync(
        {
            "tool": "optimization.aao.hidden-body-cut-apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetProfile": "pc_conservative",
        }
    )
    physbone = dashboard_server.build_optimization_apply_request_preview_sync(
        {
            "tool": "optimization.aao.physbone-cleanup-apply-request",
            "projectPath": str(project),
            "avatarPath": "Avatar",
            "targetProfile": "pc_conservative",
        }
    )

    assert hidden_body["stableCallable"] is True
    assert hidden_body["writeSupported"] is False
    assert hidden_body["readyToRequest"] is False
    assert hidden_body["versionStage"] == "0.9.x-rc"
    assert any("manual occlusion evidence" in reason for reason in hidden_body["blockedReasons"])
    assert hidden_body["hardGate"]["status"] == "blocked"
    assert hidden_body["rollbackRequirements"]["checkpointScope"] == ["Assets", "Packages", "ProjectSettings"]

    assert physbone["stableCallable"] is True
    assert physbone["writeSupported"] is False
    assert physbone["readyToRequest"] is False
    assert physbone["versionStage"] == "0.9.x-rc"
    assert any("motion behavior proof" in reason for reason in physbone["blockedReasons"])
    assert physbone["hardGate"]["status"] == "blocked"
    assert physbone["rollbackRequirements"]["generatedResidueCheckRequired"] is True


def test_optimizer_profile_diff_requires_before_after_and_rollback_snapshots(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    before = fake_validation()
    after = copy.deepcopy(before)
    after["sources"]["performance_pc"]["payload"]["rank"] = "Medium"
    after["sources"]["performance_pc"]["payload"]["triangleCount"] = 36000
    after["sources"]["performance_quest"]["payload"]["rank"] = "Good"
    after["sources"]["parameters"]["payload"]["totalEstimatedCost"] = 240
    after["sources"]["parameters"]["payload"]["totalParameters"] = 5

    payload = build_optimization_tool_result(
        "optimization.profile.diff",
        {
            "projectPath": str(project),
            "beforeValidation": before,
            "afterValidation": after,
            "rollbackValidation": before,
        },
        {},
    )
    missing = build_optimization_tool_result(
        "optimization.profile.diff",
        {"projectPath": str(project), "beforeValidation": before},
        {},
    )

    diff = payload["result"]
    assert payload["readOnly"] is True
    assert diff["hardGate"]["status"] == "pass"
    assert diff["pc"]["rankBefore"] == "Good"
    assert diff["pc"]["rankAfter"] == "Medium"
    assert diff["pc"]["rankRollback"] == "Good"
    assert diff["pc"]["metricsDelta"]["triangles"] == -6000
    assert diff["pc"]["rollbackRankMatchesBefore"] is True
    assert diff["quest"]["rankChanged"] is True
    assert diff["parameters"]["syncedBitsDelta"] == -40
    assert diff["parameters"]["totalCustomParametersDelta"] == -1
    assert diff["parameters"]["rollbackMatchesBefore"] is True
    assert missing["result"]["hardGate"]["status"] == "blocked"
    assert "profile.after" in missing["result"]["hardGate"]["blockingIds"]
    assert "profile.rollback" in missing["result"]["hardGate"]["blockingIds"]


def test_package_install_plan_prefers_vcc_alcom_handoff_before_agent_download(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    monkeypatch.setattr(
        dashboard_server,
        "locate_vpm_package_managers",
        lambda: [
            {
                "name": "vcc",
                "path": "C:/Program Files/VRChat Creator Companion/CreatorCompanion.exe",
                "kind": "app",
                "label": "VRChat Creator Companion",
                "supportsCommandInstall": False,
                "supportsUiHandoff": True,
            }
        ],
    )

    plan = dashboard_server.package_install_plan_sync(
        {
            "projectPath": str(project),
            "packageId": "com.anatawa12.avatar-optimizer",
            "allowAgentManagedDownload": True,
        }
    )

    assert plan["ok"] is True
    assert plan["strategy"] == "ui_handoff"
    assert plan["preferredManager"]["name"] == "vcc"
    assert plan["agentManagedDownload"]["available"] is False
    assert plan["canExecuteCommandInstall"] is False
    assert plan["canCreateInstallRequest"] is True


def test_package_install_request_creates_checkpointed_approval_with_cli(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    monkeypatch.setattr(
        dashboard_server,
        "locate_vpm_package_managers",
        lambda: [
            {
                "name": "vpm",
                "path": "C:/tools/vpm.exe",
                "kind": "cli",
                "label": "VCC vpm CLI",
                "supportsCommandInstall": True,
                "supportsUiHandoff": False,
            }
        ],
    )
    captured: dict[str, object] = {}

    def fake_create_apply_request(params, *, internal_wrapper=False):
        captured["params"] = params
        captured["internalWrapper"] = internal_wrapper
        return {
            "ok": True,
            "approval": {
                "status": "pending",
                "targetTool": params["target_tool"],
                "arguments": params["arguments"],
                "preview": params["preview"],
            },
        }

    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "create_apply_request", fake_create_apply_request)

    payload = dashboard_server.request_package_install_sync(
        {
            "projectPath": str(project),
            "packageId": "com.anatawa12.avatar-optimizer",
        },
        agent_name="test-agent",
    )

    assert payload["ok"] is True
    approval = payload["approval"]
    assert approval["status"] in {"pending", "auto_approved"}
    assert approval["targetTool"] == "vrcforge_install_vpm_package"
    assert approval["arguments"]["packageId"] == "com.anatawa12.avatar-optimizer"
    assert approval["preview"]["requiresCheckpoint"] is True
    assert captured["internalWrapper"] is True
    assert captured["params"]["requires_explicit_approval"] is True
    assert "Optimizer package install requests" in captured["params"]["explicit_approval_reason"]


def test_vrc_get_install_command_uses_prerelease_before_package(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        dashboard_server,
        "locate_vpm_package_managers",
        lambda: [
            {
                "name": "vrc-get",
                "path": "C:/tools/vrc-get.exe",
                "kind": "managed-cli",
                "supportsCommandInstall": True,
                "supportsUiHandoff": False,
            }
        ],
    )

    class Proc:
        returncode = 0
        stdout = "installed"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Proc()

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)
    refresh_calls: list[dict] = []
    monkeypatch.setattr(
        dashboard_server,
        "refresh_asset_database_sync",
        lambda params: refresh_calls.append(params) or {"ok": True},
    )

    result = dashboard_server.install_vpm_package_sync(
        {
            "projectPath": str(project),
            "packageId": "com.anatawa12.avatar-optimizer",
            "includePrerelease": True,
        }
    )

    assert result["ok"] is True
    assert calls
    command = calls[0]
    assert command[:4] == ["C:/tools/vrc-get.exe", "install", "-p", str(project)]
    assert "--prerelease" in command
    assert command[-1] == "com.anatawa12.avatar-optimizer"
    assert refresh_calls[-1]["resolvePackages"] is True


def test_vrc_get_install_adds_requested_repository_before_install(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        dashboard_server,
        "locate_vpm_package_managers",
        lambda: [
            {
                "name": "vrc-get",
                "path": "C:/tools/vrc-get.exe",
                "kind": "managed-cli",
                "supportsCommandInstall": True,
                "supportsUiHandoff": False,
            }
        ],
    )

    class Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Proc()

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)
    refresh_calls: list[dict] = []
    monkeypatch.setattr(
        dashboard_server,
        "refresh_asset_database_sync",
        lambda params: refresh_calls.append(params) or {"ok": True},
    )

    result = dashboard_server.install_vpm_package_sync(
        {
            "projectPath": str(project),
            "packageId": "com.poiyomi.toon",
            "repository": "https://poiyomi.github.io/vpm/index.json",
        }
    )

    assert result["ok"] is True
    assert calls[0] == ["C:/tools/vrc-get.exe", "repo", "add", "https://poiyomi.github.io/vpm/index.json"]
    assert calls[1] == ["C:/tools/vrc-get.exe", "update"]
    assert calls[2][-1] == "com.poiyomi.toon"
    assert refresh_calls[-1]["resolvePackages"] is True
    assert refresh_calls[-1]["packageResolveTimeoutSeconds"] == 180
    assert result["repository"] == "https://poiyomi.github.io/vpm/index.json"


def test_vrc_get_install_tolerates_existing_repository_before_install(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    make_unity_project(project)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        dashboard_server,
        "locate_vpm_package_managers",
        lambda: [
            {
                "name": "vrc-get",
                "path": "C:/tools/vrc-get.exe",
                "kind": "managed-cli",
                "supportsCommandInstall": True,
                "supportsUiHandoff": False,
            }
        ],
    )

    class Proc:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["C:/tools/vrc-get.exe", "repo", "add"]:
            return Proc(1, stderr="repository already exists")
        return Proc(0, stdout="ok")

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)
    refresh_calls: list[dict] = []
    monkeypatch.setattr(
        dashboard_server,
        "refresh_asset_database_sync",
        lambda params: refresh_calls.append(params) or {"ok": True},
    )

    result = dashboard_server.install_vpm_package_sync(
        {
            "projectPath": str(project),
            "packageId": "com.poiyomi.toon",
            "repository": "https://poiyomi.github.io/vpm/index.json",
        }
    )

    assert result["ok"] is True
    assert calls[0] == ["C:/tools/vrc-get.exe", "repo", "add", "https://poiyomi.github.io/vpm/index.json"]
    assert calls[1] == ["C:/tools/vrc-get.exe", "update"]
    assert calls[2][-1] == "com.poiyomi.toon"
    assert result["preflightResults"][0]["ignoredNonZero"] is True
    assert len(result["preflightResults"]) == 2
    assert refresh_calls[-1]["resolvePackages"] is True


def test_public_optimization_docs_include_roadmap_sequence() -> None:
    text = Path("docs/OPTIMIZATION_STRATEGY.md").read_text(encoding="utf-8")
    for marker in ["0.7.2-beta", "0.8.0-beta", "0.9.0-beta", "0.9.x", "0.9.5-rc", "1.0 Public Stable"]:
        assert marker in text
    assert "Calling third-party tools vs first-class VRCForge capabilities" in text
    assert "No direct apply" in text


def test_optimizer_apply_rollback_smoke_script_uses_validation_delta_endpoint() -> None:
    text = Path("scripts/smoke_optimizer_apply_rollback.py").read_text(encoding="utf-8")
    assert "vrcforge.optimizer_apply_rollback_smoke.v1" in text
    assert "/api/app/optimization/apply-request" in text
    assert "/api/app/optimization/validation-delta" in text
    assert "/api/app/checkpoints/{self.checkpoint_id}/restore" in text

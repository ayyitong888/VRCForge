from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OPTIMIZATION_SCHEMA = "vrcforge.optimization.v1"
OPTIMIZATION_VERSION_STAGE = "0.7.2-beta"


OPTIMIZATION_TOOL_DEFINITIONS: list[dict[str, str]] = [
    {
        "externalName": "optimization.baseline.scan",
        "gatewayName": "vrcforge_optimization_baseline_scan",
        "category": "read/debug",
        "description": "Run the read-only model optimization baseline scan and summarize current avatar performance state.",
    },
    {
        "externalName": "optimization.target.profile",
        "gatewayName": "vrcforge_optimization_target_profile",
        "category": "plan/preview",
        "description": "Resolve a model optimization target profile into priority weights without writing project files.",
    },
    {
        "externalName": "optimization.dependency.doctor",
        "gatewayName": "vrcforge_optimization_dependency_doctor",
        "category": "read/debug",
        "description": "Detect supported optimizer and avatar tooling packages without installing or repairing them.",
    },
    {
        "externalName": "optimization.texture-vram-audit",
        "gatewayName": "vrcforge_optimization_texture_vram_audit",
        "category": "read/debug",
        "description": "Audit texture and VRAM signals from scanner output without changing import settings.",
    },
    {
        "externalName": "optimization.lac.profile-plan",
        "gatewayName": "vrcforge_optimization_lac_profile_plan",
        "category": "plan/preview",
        "description": "Plan conservative Avatar Compressor profiles without adding LAC components.",
    },
    {
        "externalName": "optimization.material-slot-audit",
        "gatewayName": "vrcforge_optimization_material_slot_audit",
        "category": "read/debug",
        "description": "Audit material slots and possible atlas groups without baking atlases.",
    },
    {
        "externalName": "optimization.ttt.atlas-plan",
        "gatewayName": "vrcforge_optimization_ttt_atlas_plan",
        "category": "plan/preview",
        "description": "Plan TexTransTool atlas grouping without creating atlas assets.",
    },
    {
        "externalName": "optimization.aao.trace-plan",
        "gatewayName": "vrcforge_optimization_aao_trace_plan",
        "category": "plan/preview",
        "description": "Plan conservative AAO Trace And Optimize usage without adding or configuring AAO components.",
    },
    {
        "externalName": "optimization.mesh.triangle-audit",
        "gatewayName": "vrcforge_optimization_mesh_triangle_audit",
        "category": "read/debug",
        "description": "Audit renderer triangle counts and simplification risk classes without simplifying meshes.",
    },
    {
        "externalName": "optimization.meshia.simplify-plan",
        "gatewayName": "vrcforge_optimization_meshia_simplify_plan",
        "category": "plan/preview",
        "description": "Plan Meshia simplification candidates while excluding high-risk meshes by default.",
    },
    {
        "externalName": "optimization.parameter-budget-audit",
        "gatewayName": "vrcforge_optimization_parameter_budget_audit",
        "category": "read/debug",
        "description": "Audit synced expression parameter bit usage without compressing parameters.",
    },
    {
        "externalName": "optimization.vrcfury.compatibility-report",
        "gatewayName": "vrcforge_optimization_vrcfury_compatibility_report",
        "category": "read/debug",
        "description": "Report VRCFury optimizer compatibility risks without editing VRCFury components.",
    },
    {
        "externalName": "optimization.ma-responsive-layer-audit",
        "gatewayName": "vrcforge_optimization_ma_responsive_layer_audit",
        "category": "read/debug",
        "description": "Detect Modular Avatar responsive layer candidates without converting animator layers.",
    },
    {
        "externalName": "optimization.ma2bt.convertibility-plan",
        "gatewayName": "vrcforge_optimization_ma2bt_convertibility_plan",
        "category": "plan/preview",
        "description": "Plan MA2BT-Pro conversion candidates and skip reasons without applying MA2BT-Pro.",
    },
    {
        "externalName": "optimization.visual-regression.plan",
        "gatewayName": "vrcforge_optimization_visual_regression_plan",
        "category": "plan/preview",
        "description": "Plan before/after screenshot checkpoints for future optimization applies.",
    },
    {
        "externalName": "optimization.rollback.verify",
        "gatewayName": "vrcforge_optimization_rollback_verify",
        "category": "read/debug",
        "description": "Report whether future optimization applies can produce rollback proof.",
    },
]


OPTIMIZATION_TOOL_BY_EXTERNAL = {item["externalName"]: item for item in OPTIMIZATION_TOOL_DEFINITIONS}
OPTIMIZATION_TOOL_BY_GATEWAY = {item["gatewayName"]: item for item in OPTIMIZATION_TOOL_DEFINITIONS}
OPTIMIZATION_GATEWAY_TOOL_NAMES = [item["gatewayName"] for item in OPTIMIZATION_TOOL_DEFINITIONS]


TARGET_PROFILES: dict[str, dict[str, Any]] = {
    "pc_conservative": {
        "id": "pc_conservative",
        "label": "PC Conservative",
        "platform": "PC",
        "riskTolerance": "low",
        "weights": {"visualFidelity": 0.45, "vram": 0.22, "materials": 0.16, "animator": 0.1, "triangles": 0.07},
        "rules": ["Prefer reversible NDMF/build-time changes.", "Avoid face/body simplification.", "Recommend one step at a time."],
    },
    "pc_medium": {
        "id": "pc_medium",
        "label": "PC Medium",
        "platform": "PC",
        "riskTolerance": "medium",
        "weights": {"visualFidelity": 0.32, "vram": 0.25, "materials": 0.18, "animator": 0.13, "triangles": 0.12},
        "rules": ["Allow medium-risk planning only after baseline and visual checkpoints.", "Keep direct writes disabled in 0.7.2."],
    },
    "quest_medium": {
        "id": "quest_medium",
        "label": "Quest Medium",
        "platform": "Quest",
        "riskTolerance": "medium",
        "weights": {"visualFidelity": 0.2, "vram": 0.3, "materials": 0.2, "animator": 0.1, "triangles": 0.2},
        "rules": ["Plan conservative reductions first.", "Treat shader/material and mesh changes as high risk until delegated apply ships."],
    },
    "event_light": {
        "id": "event_light",
        "label": "Event Light",
        "platform": "PC/Quest",
        "riskTolerance": "medium",
        "weights": {"visualFidelity": 0.18, "vram": 0.24, "materials": 0.22, "animator": 0.14, "triangles": 0.22},
        "rules": ["Prioritize predictable performance deltas.", "Require rollback proof before every future apply."],
    },
    "custom": {
        "id": "custom",
        "label": "Custom",
        "platform": "Custom",
        "riskTolerance": "custom",
        "weights": {"visualFidelity": 0.25, "vram": 0.25, "materials": 0.2, "animator": 0.15, "triangles": 0.15},
        "rules": ["Custom weights never enable direct optimizer apply in 0.7.2."],
    },
}


OPTIMIZER_DEPENDENCIES: list[dict[str, Any]] = [
    {
        "id": "aao",
        "label": "AAO / Avatar Optimizer",
        "packageIds": ["com.anatawa12.avatar-optimizer"],
        "displayName": "AAO: Avatar Optimizer",
        "recommendedRole": "Conservative Trace And Optimize, unused BlendShape/object cleanup, and future safe merge planning.",
        "riskLevel": "medium",
        "docsLink": "https://vpm.anatawa12.com/avatar-optimizer/",
        "vpmRepository": "https://vpm.anatawa12.com/vpm.json",
        "componentSignals": [
            "TraceAndOptimize",
            "MergeSkinnedMesh",
            "MergePhysBone",
            "RemoveMeshByBlendShape",
            "RemoveMeshByMask",
        ],
    },
    {
        "id": "lac",
        "label": "Avatar Compressor / LAC",
        "packageIds": ["dev.limitex.avatar-compressor"],
        "displayName": "Avatar Compressor",
        "recommendedRole": "NDMF texture compression profile delegated through a supervised component configuration.",
        "riskLevel": "medium",
        "docsLink": "https://github.com/limitex/avatar-compressor",
        "vpmRepository": "https://vpm.limitex.dev/index.json",
        "componentSignals": ["dev.limitex.avatar.compressor.TextureCompressor", "LAC Texture Compressor"],
    },
    {
        "id": "textrans_tool",
        "label": "TexTransTool",
        "packageIds": ["net.rs64.tex-trans-tool", "rs64.tex-trans-tool"],
        "displayName": "TexTransTool",
        "recommendedRole": "Texture atlas and texture transfer planning; material slot reduction usually needs AAO or mesh merge coordination.",
        "riskLevel": "medium",
        "docsLink": "https://github.com/ReinaS-64892/TexTransTool",
        "vpmRepository": "https://vpm.rs64.net/vpm.json",
        "componentSignals": ["AtlasTexture", "TextureConfigurator", "MaterialModifier", "TTT"],
    },
    {
        "id": "meshia",
        "label": "Meshia Mesh Simplification",
        "packageIds": ["com.ramtype0.meshia.mesh-simplification"],
        "displayName": "Meshia Mesh Simplification",
        "recommendedRole": "Mesh simplification planning for low-risk accessories or selected clothing only.",
        "riskLevel": "high",
        "docsLink": "https://github.com/RamType0/Meshia.MeshSimplification",
        "vpmRepository": "https://ramtype0.github.io/VpmRepository/",
        "componentSignals": ["MeshiaMeshSimplifier", "MeshiaCascadingAvatarMeshSimplifier"],
    },
    {
        "id": "ma2bt_pro",
        "label": "MA2BT-Pro",
        "packageIds": ["com.zhuozhi.ma2bt-pro"],
        "displayName": "MA2BT Pro",
        "recommendedRole": "Convert eligible Modular Avatar responsive FX layers to BlendTree form after MA processing.",
        "riskLevel": "medium",
        "docsLink": "https://github.com/zhuozhi233/MA2BT-Pro",
        "vpmRepository": "https://zhuozhi233.github.io/vpm-listing/index.json",
        "componentSignals": ["zhuozhi.MA2BTPro.MAToBlendTreePro", "MA_To_BlendTree_Layer", "MA Responsive:"],
    },
    {
        "id": "vrcfury",
        "label": "VRCFury",
        "packageIds": ["com.vrcfury.vrcfury"],
        "displayName": "VRCFury",
        "recommendedRole": "Compatibility reporting for VRCFury build hooks, parameter compression, Direct Tree, and controller transforms.",
        "riskLevel": "high",
        "docsLink": "https://vrcfury.com/",
        "vpmRepository": "https://vcc.vrcfury.com/",
        "componentSignals": ["VF.Model.VRCFury", "VRCFury", "FuryToggle", "FuryFullController"],
    },
    {
        "id": "vrc_avatar_performance_tools",
        "label": "VRC Avatar Performance Tools",
        "packageIds": ["de.thryrallo.vrc.avatar-performance-tools"],
        "displayName": "Thry's Avatar Performance Tools",
        "recommendedRole": "Optional editor-side performance/VRAM reference; VRCForge only consumes read-only signals in 0.7.2.",
        "riskLevel": "low",
        "docsLink": "https://github.com/Thryrallo/VRC-Avatar-Performance-Tools",
        "vpmRepository": "https://thryrallo.github.io/VRC-Avatar-Performance-Tools",
        "componentSignals": ["AvatarEvaluator", "TextureVRAM"],
    },
    {
        "id": "ndmf",
        "label": "NDMF",
        "packageIds": ["nadena.dev.ndmf"],
        "displayName": "Non-Destructive Modular Framework",
        "recommendedRole": "Build-time framework used by AAO, LAC, TexTransTool, Meshia, MA2BT-Pro, and Modular Avatar.",
        "riskLevel": "low",
        "docsLink": "https://ndmf.nadena.dev/",
        "vpmRepository": "https://vpm.nadena.dev/vpm.json",
        "componentSignals": ["nadena.dev.ndmf"],
    },
    {
        "id": "modular_avatar",
        "label": "Modular Avatar",
        "packageIds": ["nadena.dev.modular-avatar"],
        "displayName": "Modular Avatar",
        "recommendedRole": "Avatar authoring framework whose responsive layers and generated objects are inputs to optimizer planning.",
        "riskLevel": "low",
        "docsLink": "https://modular-avatar.nadena.dev/",
        "vpmRepository": "https://vpm.nadena.dev/vpm.json",
        "componentSignals": ["ModularAvatar", "MA Merge Animator", "MA Parameters", "MA Responsive:"],
    },
    {
        "id": "vrchat_sdk",
        "label": "VRChat SDK",
        "packageIds": ["com.vrchat.avatars", "com.vrchat.base"],
        "displayName": "VRChat SDK",
        "recommendedRole": "Source of avatar performance stats, expression parameter limits, and build validation context.",
        "riskLevel": "low",
        "docsLink": "https://vcc.docs.vrchat.com/vpm/packages",
        "vpmRepository": "https://vrchat.github.io/packages/index.json",
        "componentSignals": ["VRCAvatarDescriptor", "VRC.SDK3A", "VRC.SDKBase"],
    },
]


FUTURE_WRITE_REQUEST_TOOLS = [
    {"externalName": "optimization.lac.apply-request", "versionStage": "0.8.0-beta", "directApplyExposed": False},
    {"externalName": "optimization.aao.trace-apply-request", "versionStage": "0.8.0-beta", "directApplyExposed": False},
    {"externalName": "optimization.ttt.atlas-apply-request", "versionStage": "0.8.0-beta", "directApplyExposed": False},
    {"externalName": "optimization.ma2bt.convert-apply-request", "versionStage": "0.8.0-beta", "directApplyExposed": False},
    {"externalName": "optimization.meshia.simplify-apply-request", "versionStage": "0.8.1-beta", "directApplyExposed": False},
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_tool_name(name: str) -> str:
    value = str(name or "").strip()
    if value in OPTIMIZATION_TOOL_BY_EXTERNAL:
        return value
    definition = OPTIMIZATION_TOOL_BY_GATEWAY.get(value)
    if definition:
        return definition["externalName"]
    if value.startswith("vrcforge_"):
        dotted = value[len("vrcforge_") :].replace("_", ".")
        candidate = f"optimization.{dotted.split('optimization.', 1)[-1]}" if "optimization." in dotted else dotted
        if candidate in OPTIMIZATION_TOOL_BY_EXTERNAL:
            return candidate
    raise ValueError(f"Unknown optimization tool: {name}")


def build_target_profile(params: dict[str, Any]) -> dict[str, Any]:
    requested = str(params.get("target_profile") or params.get("targetProfile") or params.get("target") or "pc_conservative").strip()
    key = re.sub(r"[^a-z0-9]+", "_", requested.lower()).strip("_")
    aliases = {
        "pc_conservative": "pc_conservative",
        "conservative_pc": "pc_conservative",
        "pc_medium": "pc_medium",
        "medium_pc": "pc_medium",
        "quest_medium": "quest_medium",
        "event_light": "event_light",
        "custom": "custom",
    }
    profile_id = aliases.get(key, "custom" if key else "pc_conservative")
    profile = dict(TARGET_PROFILES[profile_id])
    profile["weights"] = dict(profile.get("weights") or {})
    profile["rules"] = list(profile.get("rules") or [])
    if profile_id == "custom":
        custom = params.get("custom_profile") or params.get("customProfile") or {}
        if isinstance(custom, dict):
            weights = custom.get("weights")
            if isinstance(weights, dict):
                for weight_key in profile["weights"]:
                    try:
                        profile["weights"][weight_key] = max(0.0, min(1.0, float(weights.get(weight_key, profile["weights"][weight_key]))))
                    except (TypeError, ValueError):
                        pass
            label = str(custom.get("label") or "").strip()
            if label:
                profile["label"] = label[:80]
    profile["requested"] = requested or profile["label"]
    profile["writePolicy"] = "No optimizer apply is enabled by target profiles in 0.7.2-beta."
    return profile


def build_dependency_doctor(params: dict[str, Any]) -> dict[str, Any]:
    project_path = _coerce_project_path(params)
    facts = _read_project_package_facts(project_path)
    dependencies = [_detect_dependency(definition, facts) for definition in OPTIMIZER_DEPENDENCIES]
    counts = {
        "installed": sum(1 for item in dependencies if item["status"] == "installed"),
        "missing": sum(1 for item in dependencies if item["status"] == "missing"),
        "unknown": sum(1 for item in dependencies if item["status"] == "unknown"),
    }
    return {
        "schema": OPTIMIZATION_SCHEMA,
        "projectConfigured": project_path is not None,
        "projectReadable": bool(facts.get("projectReadable")),
        "sourceSummary": facts.get("sourceSummary"),
        "dependencies": dependencies,
        "summary": counts,
        "installPolicy": {
            "automaticInstall": False,
            "supervisedInstallRequestAvailable": False,
            "message": "0.7.2 reports dependency cards and VPM repository hints only; package installation is not automatic.",
        },
    }


def build_optimization_report(params: dict[str, Any], validation_report: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    validation = validation_report if isinstance(validation_report, dict) else {}
    profile = build_target_profile(params)
    dependency_doctor = build_dependency_doctor(params)
    baseline = build_baseline_scan(params, validation)
    texture_audit = build_texture_vram_audit(validation)
    material_audit = build_material_slot_audit(validation)
    mesh_audit = build_mesh_triangle_audit(validation)
    parameter_audit = build_parameter_budget_audit(validation)
    aao_plan = build_aao_trace_plan(dependency_doctor, validation)
    lac_plan = build_lac_profile_plan(profile, dependency_doctor, texture_audit)
    ttt_plan = build_ttt_atlas_plan(dependency_doctor, material_audit, texture_audit)
    meshia_plan = build_meshia_simplify_plan(dependency_doctor, mesh_audit)
    vrcfury_report = build_vrcfury_compatibility_report(dependency_doctor, validation)
    ma_audit = build_ma_responsive_layer_audit(validation)
    ma2bt_plan = build_ma2bt_convertibility_plan(dependency_doctor, ma_audit)
    visual_plan = build_visual_regression_plan(params)
    rollback = build_rollback_verify(params, validation)
    action_cards = build_action_cards(
        profile,
        dependency_doctor,
        baseline,
        texture_audit,
        material_audit,
        mesh_audit,
        parameter_audit,
        aao_plan,
        lac_plan,
        ttt_plan,
        meshia_plan,
        vrcfury_report,
        ma_audit,
        ma2bt_plan,
        visual_plan,
        rollback,
    )
    recommended = [card for card in action_cards if card.get("enabled")]
    return {
        "ok": True,
        "schema": OPTIMIZATION_SCHEMA,
        "versionStage": OPTIMIZATION_VERSION_STAGE,
        "generatedAt": now_iso(),
        "readOnly": True,
        "planOnly": True,
        "noProjectWrites": True,
        "directApplyExposed": False,
        "targetProfile": profile,
        "baseline": baseline,
        "dependencyDoctor": dependency_doctor,
        "audits": {
            "textureVram": texture_audit,
            "materialSlots": material_audit,
            "meshTriangles": mesh_audit,
            "parameterBudget": parameter_audit,
            "maResponsiveLayers": ma_audit,
        },
        "plans": {
            "lacProfile": lac_plan,
            "tttAtlas": ttt_plan,
            "aaoTrace": aao_plan,
            "meshiaSimplify": meshia_plan,
            "vrcfuryCompatibility": vrcfury_report,
            "ma2btConvertibility": ma2bt_plan,
            "visualRegression": visual_plan,
            "rollbackVerify": rollback,
        },
        "topOffenders": build_top_offenders(baseline, texture_audit, material_audit, mesh_audit, parameter_audit),
        "actionCards": action_cards,
        "recommendedOrder": [card["id"] for card in recommended[:6]],
        "nextSafeAction": recommended[0] if recommended else None,
        "tools": [
            {
                "externalName": item["externalName"],
                "gatewayName": item["gatewayName"],
                "level": "plan-only" if item["category"] == "plan/preview" else "read-only",
                "directApplyExposed": False,
            }
            for item in OPTIMIZATION_TOOL_DEFINITIONS
        ],
        "futureWriteRequestTools": FUTURE_WRITE_REQUEST_TOOLS,
        "rules": optimization_rules(),
    }


def build_optimization_tool_result(
    tool_name: str,
    params: dict[str, Any],
    validation_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    external_name = normalize_tool_name(tool_name)
    definition = OPTIMIZATION_TOOL_BY_EXTERNAL[external_name]
    validation = validation_report if isinstance(validation_report, dict) else {}
    dependency_doctor = build_dependency_doctor(params)
    profile = build_target_profile(params)
    result: Any
    if external_name == "optimization.baseline.scan":
        result = build_baseline_scan(params, validation)
    elif external_name == "optimization.target.profile":
        result = profile
    elif external_name == "optimization.dependency.doctor":
        result = dependency_doctor
    elif external_name == "optimization.texture-vram-audit":
        result = build_texture_vram_audit(validation)
    elif external_name == "optimization.lac.profile-plan":
        result = build_lac_profile_plan(profile, dependency_doctor, build_texture_vram_audit(validation))
    elif external_name == "optimization.material-slot-audit":
        result = build_material_slot_audit(validation)
    elif external_name == "optimization.ttt.atlas-plan":
        result = build_ttt_atlas_plan(dependency_doctor, build_material_slot_audit(validation), build_texture_vram_audit(validation))
    elif external_name == "optimization.aao.trace-plan":
        result = build_aao_trace_plan(dependency_doctor, validation)
    elif external_name == "optimization.mesh.triangle-audit":
        result = build_mesh_triangle_audit(validation)
    elif external_name == "optimization.meshia.simplify-plan":
        result = build_meshia_simplify_plan(dependency_doctor, build_mesh_triangle_audit(validation))
    elif external_name == "optimization.parameter-budget-audit":
        result = build_parameter_budget_audit(validation)
    elif external_name == "optimization.vrcfury.compatibility-report":
        result = build_vrcfury_compatibility_report(dependency_doctor, validation)
    elif external_name == "optimization.ma-responsive-layer-audit":
        result = build_ma_responsive_layer_audit(validation)
    elif external_name == "optimization.ma2bt.convertibility-plan":
        result = build_ma2bt_convertibility_plan(dependency_doctor, build_ma_responsive_layer_audit(validation))
    elif external_name == "optimization.visual-regression.plan":
        result = build_visual_regression_plan(params)
    elif external_name == "optimization.rollback.verify":
        result = build_rollback_verify(params, validation)
    else:
        raise ValueError(f"Unknown optimization tool: {tool_name}")
    return {
        "ok": True,
        "schema": OPTIMIZATION_SCHEMA,
        "versionStage": OPTIMIZATION_VERSION_STAGE,
        "generatedAt": now_iso(),
        "tool": external_name,
        "gatewayTool": definition["gatewayName"],
        "level": "plan-only" if definition["category"] == "plan/preview" else "read-only",
        "readOnly": True,
        "planOnly": definition["category"] == "plan/preview",
        "noProjectWrites": True,
        "directApplyExposed": False,
        "result": result,
        "rules": optimization_rules(),
    }


def build_baseline_scan(params: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    sources = _validation_sources(validation)
    pc = _source_payload(sources, "performance_pc")
    quest = _source_payload(sources, "performance_quest")
    parameters = _source_payload(sources, "parameters")
    materials = _source_payload(sources, "materials")
    avatar_items = _source_payload(sources, "avatar_items")
    residue = _source_payload(sources, "generated_residue")
    return {
        "readOnly": True,
        "projectConfigured": bool(params.get("project_path") or params.get("projectPath")),
        "avatarSelected": bool(params.get("avatar_path") or params.get("avatarPath")),
        "performanceHeadline": {
            "pc": _performance_headline(pc),
            "quest": _performance_headline(quest),
        },
        "metrics": {
            "textureMemoryBytes": _first_numeric(materials, ("textureMemoryBytes", "vramBytes", "totalTextureBytes", "totalVRAMBytes")),
            "materialSlots": _first_numeric(materials, ("materialSlotCount", "slotCount", "materialSlots", "materialCount")),
            "skinnedMeshCount": _first_numeric(avatar_items, ("skinnedMeshCount", "skinnedMeshRendererCount", "skinnedMeshes")),
            "triangleCount": _first_numeric(pc, ("triangleCount", "triangles", "polygonCount", "polygons")),
            "physBones": _first_numeric(avatar_items, ("physBoneCount", "physBones")),
            "contacts": _first_numeric(avatar_items, ("contactCount", "contacts")),
            "colliders": _first_numeric(avatar_items, ("colliderCount", "colliders")),
            "constraints": _first_numeric(avatar_items, ("constraintCount", "constraints")),
            "expressionParameterBits": _first_numeric(parameters, ("syncedBits", "bitsUsed", "totalCost", "parameterCost")),
            "generatedResidueCount": _first_numeric(residue, ("residueCount", "changedFileCount", "generatedAssetCount")),
        },
        "validationGate": validation.get("gate") if isinstance(validation.get("gate"), dict) else None,
        "validationSummary": validation.get("summary") if isinstance(validation.get("summary"), dict) else None,
        "scannerStatus": _scanner_statuses(validation),
    }


def build_texture_vram_audit(validation: dict[str, Any]) -> dict[str, Any]:
    materials = _source_payload(_validation_sources(validation), "materials")
    textures = []
    seen: set[str] = set()
    for entry in _walk_dicts(materials):
        name = _first_text(entry, ("textureName", "texture", "name", "assetPath", "path"))
        if not name or "texture" not in " ".join(str(key).lower() for key in entry.keys()) and not _looks_like_texture_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        width = _first_numeric(entry, ("width", "textureWidth"))
        height = _first_numeric(entry, ("height", "textureHeight"))
        bytes_value = _first_numeric(entry, ("bytes", "sizeBytes", "vramBytes", "memoryBytes"))
        textures.append(
            {
                "name": _safe_asset_label(name),
                "role": classify_texture_role(name),
                "width": width,
                "height": height,
                "bytes": bytes_value,
                "large": _is_large_texture(width, height, bytes_value),
                "duplicateKey": _texture_duplicate_key(name),
            }
        )
    duplicate_groups = _duplicate_groups(textures)
    return {
        "readOnly": True,
        "summary": {
            "knownTextureCount": len(textures),
            "largeTextureCount": sum(1 for item in textures if item.get("large")),
            "duplicateGroupCount": len(duplicate_groups),
            "scannerCoverage": "metadata" if textures else "unknown",
        },
        "textures": textures[:200],
        "duplicateGroups": duplicate_groups[:50],
        "notes": ["No texture import settings are changed by this audit."],
    }


def build_material_slot_audit(validation: dict[str, Any]) -> dict[str, Any]:
    materials = _source_payload(_validation_sources(validation), "materials")
    renderers = []
    for entry in _walk_dicts(materials):
        renderer = _first_text(entry, ("rendererPath", "gameObjectPath", "objectPath", "path"))
        material_list = _coerce_list(entry.get("materials") or entry.get("materialNames") or entry.get("slots"))
        if not renderer or not material_list:
            continue
        labels = [_safe_asset_label(str(item.get("name") if isinstance(item, dict) else item)) for item in material_list]
        renderers.append(
            {
                "renderer": _safe_asset_label(renderer),
                "slotCount": len(labels),
                "materials": labels[:32],
                "flags": material_flags(" ".join(labels)),
            }
        )
    total_slots = sum(int(item.get("slotCount") or 0) for item in renderers)
    return {
        "readOnly": True,
        "summary": {
            "rendererCount": len(renderers),
            "knownMaterialSlotCount": total_slots or _first_numeric(materials, ("materialSlotCount", "slotCount", "materialCount")),
            "specialMaterialRendererCount": sum(1 for item in renderers if item.get("flags")),
            "atlasGroupCandidateCount": len([item for item in renderers if int(item.get("slotCount") or 0) > 1]),
        },
        "renderers": renderers[:200],
        "atlasGroupHints": [
            {"renderer": item["renderer"], "slotCount": item["slotCount"], "flags": item["flags"]}
            for item in renderers
            if int(item.get("slotCount") or 0) > 1
        ][:80],
        "notes": ["Atlas planning is read-only; material slot reduction usually needs coordination with AAO or mesh merge."],
    }


def build_mesh_triangle_audit(validation: dict[str, Any]) -> dict[str, Any]:
    sources = _validation_sources(validation)
    avatar_items = _source_payload(sources, "avatar_items")
    pc = _source_payload(sources, "performance_pc")
    candidates = []
    for payload in (avatar_items, pc):
        for entry in _walk_dicts(payload):
            triangles = _first_numeric(entry, ("triangleCount", "triangles", "polygonCount", "polygons"))
            if triangles is None:
                continue
            renderer = _first_text(entry, ("rendererPath", "gameObjectPath", "objectPath", "name", "path")) or "renderer"
            label = _safe_asset_label(renderer)
            candidates.append(
                {
                    "renderer": label,
                    "triangleCount": int(triangles),
                    "riskClass": classify_mesh_risk(label, entry),
                    "hasBlendshapes": bool(_first_numeric(entry, ("blendShapeCount", "blendshapes"))),
                }
            )
    unique = _unique_by(candidates, "renderer")
    return {
        "readOnly": True,
        "summary": {
            "knownRendererCount": len(unique),
            "knownTriangleCount": sum(int(item.get("triangleCount") or 0) for item in unique) or _first_numeric(pc, ("triangleCount", "triangles", "polygonCount", "polygons")),
            "lowRiskCandidateCount": sum(1 for item in unique if item.get("riskClass") == "low-risk accessory"),
            "highRiskCandidateCount": sum(1 for item in unique if str(item.get("riskClass") or "").startswith(("body", "face", "high"))),
        },
        "renderers": sorted(unique, key=lambda item: int(item.get("triangleCount") or 0), reverse=True)[:200],
        "defaultExclusions": ["face mesh", "body mesh", "high-expression mesh", "eyes", "mouth", "hands"],
    }


def build_parameter_budget_audit(validation: dict[str, Any]) -> dict[str, Any]:
    parameters = _source_payload(_validation_sources(validation), "parameters")
    entries = []
    for entry in _walk_dicts(parameters):
        name = _first_text(entry, ("parameterName", "name", "param"))
        value_type = _first_text(entry, ("type", "valueType", "parameterType"))
        bits = _first_numeric(entry, ("bits", "cost", "syncedBits", "bitCost"))
        if not name or (value_type is None and bits is None):
            continue
        flags = []
        if str(value_type or "").lower() in {"float", "int", "integer"}:
            flags.append("high-cost type")
        lower_name = name.lower()
        if "osc" in lower_name or "tracking" in lower_name or "face" in lower_name:
            flags.append("OSC/face tracking risk")
        entries.append({"name": name[:120], "type": value_type or "unknown", "bits": bits, "flags": flags})
    total_bits = _first_numeric(parameters, ("syncedBits", "bitsUsed", "totalCost", "parameterCost"))
    return {
        "readOnly": True,
        "summary": {
            "knownParameterCount": len(entries),
            "syncedBits": total_bits,
            "flaggedParameterCount": sum(1 for item in entries if item.get("flags")),
            "scannerCoverage": "metadata" if entries or total_bits is not None else "unknown",
        },
        "parameters": entries[:200],
        "notes": ["This audit does not compress, delete, or rewrite expression parameters."],
    }


def build_lac_profile_plan(profile: dict[str, Any], dependency_doctor: dict[str, Any], texture_audit: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "lac")
    profiles = [
        ("conservative_pc", "Conservative PC", "low", "Small VRAM reduction with lowest visual risk."),
        ("balanced_pc", "Balanced PC", "medium", "Moderate texture memory reduction with review required."),
        ("aggressive_pc", "Aggressive PC", "high", "Higher compression risk; keep experimental until sample matrix passes."),
        ("quest_medium", "Quest Medium", "medium", "Quest-oriented memory reduction, still plan-only in 0.7.2."),
        ("quest_fallback", "Quest Fallback", "high", "Fallback-avatar oriented profile; future delegated apply only."),
    ]
    return {
        "planOnly": True,
        "dependency": dep,
        "blocked": dep.get("status") != "installed",
        "blockedReason": None if dep.get("status") == "installed" else "Avatar Compressor / LAC is not detected.",
        "profiles": [
            {
                "id": profile_id,
                "label": label,
                "visualRisk": risk,
                "estimatedBenefit": _estimated_texture_benefit(texture_audit, risk),
                "recommendedForTarget": profile_id.startswith(str(profile.get("id") or "").split("_")[0]),
                "notes": note,
            }
            for profile_id, label, risk, note in profiles
        ],
        "nextStage": "0.8.0-beta delegated apply through approval/checkpoint/validation/rollback.",
    }


def build_ttt_atlas_plan(dependency_doctor: dict[str, Any], material_audit: dict[str, Any], texture_audit: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "textrans_tool")
    groups = []
    for hint in material_audit.get("atlasGroupHints") or []:
        groups.append(
            {
                "renderer": hint.get("renderer"),
                "slotCount": hint.get("slotCount"),
                "risk": "high" if hint.get("flags") else "medium",
                "reason": "Special shader/material flags need manual confirmation." if hint.get("flags") else "Multiple material slots may benefit from atlas planning.",
            }
        )
    return {
        "planOnly": True,
        "dependency": dep,
        "blocked": dep.get("status") != "installed",
        "blockedReason": None if dep.get("status") == "installed" else "TexTransTool is not detected.",
        "candidateGroups": groups[:80],
        "estimatedBenefit": "medium" if groups or texture_audit.get("summary", {}).get("largeTextureCount") else "unknown",
        "coordinationNote": "TTT atlas alone may reduce VRAM and texture organization; material slot reduction often requires AAO or mesh merge coordination.",
    }


def build_aao_trace_plan(dependency_doctor: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "aao")
    items = _source_payload(_validation_sources(validation), "avatar_items")
    blendshape_candidates = []
    physbone_candidates = []
    merge_candidates = []
    hidden_body_candidates = []
    for entry in _walk_dicts(items):
        name = _first_text(entry, ("gameObjectPath", "objectPath", "rendererPath", "name", "path"))
        if not name:
            continue
        label = _safe_asset_label(name)
        text = f"{label} {' '.join(str(v) for v in entry.values() if isinstance(v, (str, int, float)))}".lower()
        if "blend" in text:
            blendshape_candidates.append(label)
        if "physbone" in text or "phys bone" in text:
            physbone_candidates.append(label)
        if "skinned" in text or "renderer" in text:
            merge_candidates.append(label)
        if "body" in text or "skin" in text:
            hidden_body_candidates.append(label)
    return {
        "planOnly": True,
        "dependency": dep,
        "blocked": dep.get("status") != "installed",
        "blockedReason": None if dep.get("status") == "installed" else "AAO / Avatar Optimizer is not detected.",
        "unusedBlendShapes": _dedupe_labels(blendshape_candidates)[:80],
        "unusedObjects": [],
        "physBoneCleanupCandidates": _dedupe_labels(physbone_candidates)[:80],
        "skinnedMeshMergeCandidates": _dedupe_labels(merge_candidates)[:80],
        "hiddenBodyCutCandidates": [{"name": item, "risk": "high", "status": "plan-only"} for item in _dedupe_labels(hidden_body_candidates)[:40]],
        "riskNotes": ["Hidden body cut is high-risk and remains plan-only in 0.7.2."],
    }


def build_meshia_simplify_plan(dependency_doctor: dict[str, Any], mesh_audit: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "meshia")
    candidates = []
    skipped = []
    for renderer in mesh_audit.get("renderers") or []:
        risk = str(renderer.get("riskClass") or "unknown")
        item = {"renderer": renderer.get("renderer"), "triangleCount": renderer.get("triangleCount"), "riskClass": risk}
        if risk in {"low-risk accessory", "clothing"}:
            candidates.append({**item, "status": "candidate"})
        else:
            skipped.append({**item, "skipReason": "Default exclusion for 0.7.2 planner."})
    return {
        "planOnly": True,
        "dependency": dep,
        "blocked": dep.get("status") != "installed",
        "blockedReason": None if dep.get("status") == "installed" else "Meshia Mesh Simplification is not detected.",
        "candidates": candidates[:80],
        "skipped": skipped[:120],
        "defaultExclusions": mesh_audit.get("defaultExclusions") or [],
        "nextStage": "0.8.1-beta preview/apply for low-risk accessories or clothing only.",
    }


def build_vrcfury_compatibility_report(dependency_doctor: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "vrcfury")
    warnings = [
        "VRCFury may run its own parameter compression and Direct Tree/controller transformations during build.",
        "Coordinate VRCFury with Modular Avatar, AAO, MA2BT-Pro, Write Defaults checks, and parameter-budget planning.",
    ]
    sources = _validation_sources(validation)
    fx = _source_payload(sources, "fx")
    direct_tree_hints = []
    for entry in _walk_dicts(fx):
        name = _first_text(entry, ("name", "layerName", "stateName"))
        if name and "tree" in name.lower():
            direct_tree_hints.append(name[:120])
    return {
        "readOnly": True,
        "dependency": dep,
        "present": dep.get("status") == "installed",
        "componentsDetected": [],
        "possibleConflicts": warnings,
        "directTreeHints": _dedupe_labels(direct_tree_hints)[:40],
        "applyPolicy": "VRCFury Parameter Compressor and Direct Tree apply remain experimental and are not enabled in 0.7.2.",
    }


def build_ma_responsive_layer_audit(validation: dict[str, Any]) -> dict[str, Any]:
    fx = _source_payload(_validation_sources(validation), "fx")
    candidates = []
    skipped = []
    for entry in _walk_dicts(fx):
        name = _first_text(entry, ("layerName", "name", "path"))
        if not name:
            continue
        lower = name.lower()
        item = {"layer": name[:160], "type": _first_text(entry, ("type", "kind")) or "unknown"}
        if lower.startswith("ma responsive:") or lower.startswith("rc ma responsive:") or "ma responsive" in lower:
            candidates.append({**item, "candidateType": _classify_ma_layer(name)})
        elif "ma_to_blendtree" in lower:
            skipped.append({**item, "skipReason": "Already appears converted by MA2BT-Pro."})
    return {
        "readOnly": True,
        "summary": {"candidateCount": len(candidates), "skippedCount": len(skipped), "scannerCoverage": "metadata" if candidates or skipped else "unknown"},
        "candidates": candidates[:120],
        "skipped": skipped[:120],
    }


def build_ma2bt_convertibility_plan(dependency_doctor: dict[str, Any], ma_audit: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "ma2bt_pro")
    candidates = []
    skipped = []
    for item in ma_audit.get("candidates") or []:
        layer = str(item.get("layer") or "")
        if "direct blend tree" in layer.lower() or "unsafe" in layer.lower():
            skipped.append({**item, "skipReason": "Layer requires manual review before conversion."})
        else:
            candidates.append({**item, "status": "convertible-plan"})
    return {
        "planOnly": True,
        "dependency": dep,
        "blocked": dep.get("status") != "installed",
        "blockedReason": None if dep.get("status") == "installed" else "MA2BT-Pro is not detected.",
        "convertibleLayers": candidates[:120],
        "skippedLayers": (ma_audit.get("skipped") or []) + skipped[:120],
        "notes": ["scanAllLayers is higher risk; VRCForge defaults to MA Responsive layers only."],
    }


def build_visual_regression_plan(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "planOnly": True,
        "requiresVisionProvider": False,
        "avatarSelected": bool(params.get("avatar_path") or params.get("avatarPath")),
        "shots": [
            {"id": "front", "label": "Front", "requiresPlayMode": False},
            {"id": "left", "label": "Left side", "requiresPlayMode": False},
            {"id": "right", "label": "Right side", "requiresPlayMode": False},
            {"id": "back", "label": "Back", "requiresPlayMode": False},
            {"id": "gesture_set", "label": "Gesture set", "requiresPlayMode": True},
        ],
        "futureUse": "0.8.0+ can compare before/after screenshots after each delegated optimization step.",
    }


def build_rollback_verify(params: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    project_path = _coerce_project_path(params)
    residue = _source_payload(_validation_sources(validation), "generated_residue")
    return {
        "readOnly": True,
        "checkpointInfrastructureRequired": True,
        "projectConfigured": project_path is not None,
        "projectReadable": bool(project_path and project_path.exists()),
        "canGenerateFutureProof": bool(project_path and project_path.exists()),
        "generatedResidueCount": _first_numeric(residue, ("residueCount", "generatedAssetCount")),
        "futureApplyRule": "Each optimizer apply must run preview -> approval -> checkpoint -> apply -> validation -> rollback proof.",
    }


def build_action_cards(
    profile: dict[str, Any],
    dependency_doctor: dict[str, Any],
    baseline: dict[str, Any],
    texture_audit: dict[str, Any],
    material_audit: dict[str, Any],
    mesh_audit: dict[str, Any],
    parameter_audit: dict[str, Any],
    aao_plan: dict[str, Any],
    lac_plan: dict[str, Any],
    ttt_plan: dict[str, Any],
    meshia_plan: dict[str, Any],
    vrcfury_report: dict[str, Any],
    ma_audit: dict[str, Any],
    ma2bt_plan: dict[str, Any],
    visual_plan: dict[str, Any],
    rollback: dict[str, Any],
) -> list[dict[str, Any]]:
    del baseline, visual_plan, rollback
    cards = [
        _action_card(
            "optimize_texture_memory",
            "Optimize texture memory",
            "Review large or duplicated textures and plan LAC or TTT steps.",
            "medium",
            "Avatar Compressor / LAC",
            "0.7.2-beta",
            "plan-only",
            lac_plan.get("blockedReason"),
            _estimated_texture_benefit(texture_audit, "medium"),
            "Texture memory is often the first conservative optimization target.",
            "Run optimization.texture-vram-audit, then optimization.lac.profile-plan.",
            texture_audit.get("textures") or [],
        ),
        _action_card(
            "reduce_material_slots",
            "Reduce material slots",
            "Group material slots for atlas planning without baking atlas assets.",
            "medium",
            "TexTransTool + AAO",
            "0.7.2-beta",
            "plan-only",
            ttt_plan.get("blockedReason"),
            "medium" if material_audit.get("summary", {}).get("atlasGroupCandidateCount") else "unknown",
            "Material slot reduction needs atlas planning and later mesh/material coordination.",
            "Run optimization.material-slot-audit, then optimization.ttt.atlas-plan.",
            material_audit.get("atlasGroupHints") or [],
        ),
        _action_card(
            "clean_unused_blendshapes",
            "Clean unused BlendShapes",
            "Plan AAO Trace And Optimize cleanup candidates.",
            "medium",
            "AAO / Avatar Optimizer",
            "0.7.2-beta",
            "plan-only",
            aao_plan.get("blockedReason"),
            "medium" if aao_plan.get("unusedBlendShapes") else "unknown",
            "Unused BlendShapes can affect memory and build complexity.",
            "Run optimization.aao.trace-plan and review BlendShape candidates.",
            aao_plan.get("unusedBlendShapes") or [],
        ),
        _action_card(
            "reduce_physbone_overhead",
            "Reduce PhysBone overhead",
            "Plan PhysBone cleanup candidates without removing components.",
            "medium",
            "AAO / Avatar Optimizer",
            "0.7.2-beta",
            "plan-only",
            aao_plan.get("blockedReason"),
            "unknown",
            "Physics overhead should be reviewed separately from visual mesh changes.",
            "Review PhysBone candidates in optimization.aao.trace-plan.",
            aao_plan.get("physBoneCleanupCandidates") or [],
        ),
        _action_card(
            "merge_safe_skinned_meshes",
            "Merge safe Skinned Meshes",
            "Plan merge candidates while avoiding face, body, and expression-heavy meshes.",
            "high",
            "AAO / Avatar Optimizer",
            "0.8.0-beta",
            "plan-only",
            aao_plan.get("blockedReason"),
            "medium" if aao_plan.get("skinnedMeshMergeCandidates") else "unknown",
            "Skinned mesh merging can help draw calls but needs visual and rig validation.",
            "Use the trace plan only; apply is deferred to 0.8.0.",
            aao_plan.get("skinnedMeshMergeCandidates") or [],
        ),
        _action_card(
            "optimize_ma_animation_layers",
            "Optimize MA animation layers",
            "Plan MA2BT-Pro conversion for eligible MA Responsive layers.",
            "medium",
            "MA2BT-Pro",
            "0.7.2-beta",
            "plan-only",
            ma2bt_plan.get("blockedReason"),
            "medium" if ma_audit.get("summary", {}).get("candidateCount") else "unknown",
            "MA-heavy avatars often accumulate FX layers that can be converted safely only when eligible.",
            "Run optimization.ma-responsive-layer-audit, then optimization.ma2bt.convertibility-plan.",
            ma2bt_plan.get("convertibleLayers") or [],
        ),
        _action_card(
            "check_parameter_budget",
            "Check parameter budget",
            "Audit synced parameter bits and high-cost parameter types.",
            "low",
            "VRChat SDK",
            "0.7.2-beta",
            "read-only",
            None,
            "medium" if parameter_audit.get("summary", {}).get("flaggedParameterCount") else "unknown",
            "Parameter budget changes can affect OSC, face tracking, and menu behavior.",
            "Run optimization.parameter-budget-audit before any compression plan.",
            parameter_audit.get("parameters") or [],
        ),
        _action_card(
            "plan_mesh_simplification",
            "Plan mesh simplification",
            "List Meshia candidates while excluding body, face, eyes, mouth, hands, and high-expression meshes.",
            "high",
            "Meshia Mesh Simplification",
            "0.8.1-beta",
            "plan-only",
            meshia_plan.get("blockedReason"),
            "medium" if mesh_audit.get("summary", {}).get("lowRiskCandidateCount") else "unknown",
            "Triangle reduction has high visual and weighting risk, so default planning is conservative.",
            "Run optimization.mesh.triangle-audit, then optimization.meshia.simplify-plan.",
            meshia_plan.get("candidates") or [],
        ),
        _action_card(
            "check_vrcfury_compatibility",
            "Check VRCFury compatibility",
            "Report VRCFury interactions with MA, AAO, MA2BT-Pro, WD behavior, and parameter compression.",
            "high",
            "VRCFury",
            "0.7.2-beta",
            "read-only",
            None,
            "risk-reduction",
            "VRCFury can perform its own build-time controller and parameter transformations.",
            "Run optimization.vrcfury.compatibility-report before optimizer apply planning.",
            vrcfury_report.get("possibleConflicts") or [],
        ),
        _action_card(
            "prepare_quest_event_light",
            "Prepare Quest/Event-light version",
            "Use target profile weights to plan one conservative step at a time.",
            "medium",
            "VRCForge planner",
            "0.9.0-beta",
            "plan-only",
            None,
            "profile-driven",
            f"Current target is {profile.get('label')}; VRCForge should recommend one step at a time.",
            "Resolve target profile, then run baseline and dependency doctor.",
            [profile],
        ),
    ]
    return cards


def build_top_offenders(
    baseline: dict[str, Any],
    texture_audit: dict[str, Any],
    material_audit: dict[str, Any],
    mesh_audit: dict[str, Any],
    parameter_audit: dict[str, Any],
) -> list[dict[str, Any]]:
    offenders = []
    texture_summary = texture_audit.get("summary") or {}
    if texture_summary.get("largeTextureCount"):
        offenders.append({"id": "texture_vram", "label": "Large textures", "severity": "warning", "count": texture_summary.get("largeTextureCount")})
    material_summary = material_audit.get("summary") or {}
    if material_summary.get("atlasGroupCandidateCount"):
        offenders.append({"id": "material_slots", "label": "Material slot candidates", "severity": "suggestion", "count": material_summary.get("atlasGroupCandidateCount")})
    mesh_summary = mesh_audit.get("summary") or {}
    if mesh_summary.get("knownTriangleCount"):
        offenders.append({"id": "triangles", "label": "Triangle budget", "severity": "info", "count": mesh_summary.get("knownTriangleCount")})
    parameter_summary = parameter_audit.get("summary") or {}
    if parameter_summary.get("flaggedParameterCount"):
        offenders.append({"id": "parameter_budget", "label": "Parameter budget flags", "severity": "warning", "count": parameter_summary.get("flaggedParameterCount")})
    metrics = baseline.get("metrics") or {}
    if not offenders and any(value is not None for value in metrics.values()):
        offenders.append({"id": "baseline", "label": "Baseline captured", "severity": "info", "count": 1})
    if not offenders:
        offenders.append({"id": "scanner_coverage", "label": "Scanner coverage incomplete", "severity": "info", "count": 0})
    return offenders[:8]


def optimization_rules() -> dict[str, Any]:
    return {
        "releaseScope": "0.7.2-beta planner",
        "readOnly": True,
        "planOnly": True,
        "noProjectWrites": True,
        "noDirectOptimizerApply": True,
        "noOneClickAllOptimizers": True,
        "noAutomaticPackageInstall": True,
        "futureApplyRequires": ["preview", "approval", "checkpoint", "apply", "validation", "rollback"],
        "externalAgentsMay": ["scan", "audit", "plan", "request future supervised writes"],
        "externalAgentsMustNot": ["direct apply", "raw Unity writes", "raw Roslyn full-auto", "read private asset binaries into model context"],
    }


def _coerce_project_path(params: dict[str, Any]) -> Path | None:
    raw = str(params.get("project_path") or params.get("projectPath") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except (OSError, ValueError):
        return None


def _read_project_package_facts(project_path: Path | None) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "projectReadable": False,
        "manifest": {},
        "manifestDependencies": {},
        "lockDependencies": {},
        "vpmDependencies": {},
        "packageJson": {},
        "legacySignals": set(),
        "sourceSummary": {"manifest": False, "packagesLock": False, "vpmManifest": False, "packageFolders": 0},
    }
    if not project_path:
        return facts
    packages_dir = project_path / "Packages"
    assets_dir = project_path / "Assets"
    facts["projectReadable"] = packages_dir.is_dir()
    manifest = _read_json(packages_dir / "manifest.json")
    if isinstance(manifest, dict):
        facts["manifest"] = manifest
        facts["manifestDependencies"] = manifest.get("dependencies") if isinstance(manifest.get("dependencies"), dict) else {}
        facts["sourceSummary"]["manifest"] = True
    lock = _read_json(packages_dir / "packages-lock.json")
    if isinstance(lock, dict):
        facts["lockDependencies"] = lock.get("dependencies") if isinstance(lock.get("dependencies"), dict) else {}
        facts["sourceSummary"]["packagesLock"] = True
    vpm = _read_json(packages_dir / "vpm-manifest.json")
    if isinstance(vpm, dict):
        facts["vpmDependencies"] = vpm.get("dependencies") if isinstance(vpm.get("dependencies"), dict) else {}
        facts["sourceSummary"]["vpmManifest"] = True
    package_json: dict[str, dict[str, Any]] = {}
    if packages_dir.is_dir():
        for definition in OPTIMIZER_DEPENDENCIES:
            for package_id in definition.get("packageIds") or []:
                data = _read_json(packages_dir / package_id / "package.json")
                if isinstance(data, dict):
                    package_json[package_id] = data
    facts["packageJson"] = package_json
    facts["sourceSummary"]["packageFolders"] = len(package_json)
    legacy_signals: set[str] = set()
    for signal, relative in {
        "vrcfury": Path("VRCFury"),
        "textrans_tool": Path("TexTransTool"),
        "vrc_avatar_performance_tools": Path("Thry") / "Avatar",
    }.items():
        if assets_dir.joinpath(relative).exists():
            legacy_signals.add(signal)
    facts["legacySignals"] = legacy_signals
    return facts


def _detect_dependency(definition: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    package_ids = list(definition.get("packageIds") or [])
    manifest_deps = facts.get("manifestDependencies") if isinstance(facts.get("manifestDependencies"), dict) else {}
    lock_deps = facts.get("lockDependencies") if isinstance(facts.get("lockDependencies"), dict) else {}
    vpm_deps = facts.get("vpmDependencies") if isinstance(facts.get("vpmDependencies"), dict) else {}
    package_json = facts.get("packageJson") if isinstance(facts.get("packageJson"), dict) else {}
    matched_id = ""
    source = ""
    version = ""
    wanted_version = ""
    for package_id in package_ids:
        folder_data = package_json.get(package_id)
        if isinstance(folder_data, dict):
            matched_id = package_id
            source = "package-folder"
            version = str(folder_data.get("version") or "")
            break
        lock_data = lock_deps.get(package_id)
        if isinstance(lock_data, dict):
            matched_id = package_id
            source = "packages-lock"
            version = str(lock_data.get("version") or "")
            break
        if package_id in manifest_deps:
            matched_id = package_id
            source = "manifest"
            raw = manifest_deps.get(package_id)
            version = str(raw if isinstance(raw, str) else "")
            break
    if not matched_id:
        for package_id in package_ids:
            raw = vpm_deps.get(package_id)
            if raw is not None:
                matched_id = package_id
                source = "vpm-manifest"
                wanted_version = _extract_version(raw)
                break
    legacy = definition.get("id") in facts.get("legacySignals", set())
    if source in {"package-folder", "packages-lock", "manifest"}:
        status = "installed"
    elif source == "vpm-manifest" or legacy:
        status = "unknown"
    else:
        status = "missing"
    return {
        "id": definition.get("id"),
        "label": definition.get("label"),
        "status": status,
        "installed": status == "installed",
        "packageIds": package_ids,
        "matchedPackageId": matched_id,
        "version": version or wanted_version or None,
        "wantedVersion": wanted_version or None,
        "source": source or ("legacy-assets" if legacy else "not-detected"),
        "recommendedRole": definition.get("recommendedRole"),
        "riskLevel": definition.get("riskLevel"),
        "docsLink": definition.get("docsLink"),
        "installMethod": {
            "kind": "vpm",
            "repository": definition.get("vpmRepository"),
            "automatic": False,
            "supervisedRequestSupported": False,
        },
        "componentSignals": definition.get("componentSignals") or [],
    }


def _read_json(path: Path) -> Any:
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def _extract_version(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("version", "lockedVersion", "requestedVersion"):
            value = raw.get(key)
            if value:
                return str(value)
    return ""


def _validation_sources(validation: dict[str, Any]) -> dict[str, Any]:
    sources = validation.get("sources")
    return sources if isinstance(sources, dict) else {}


def _source_payload(sources: dict[str, Any], name: str) -> dict[str, Any]:
    source = sources.get(name)
    if isinstance(source, dict):
        payload = source.get("payload")
        if isinstance(payload, dict):
            return payload
        summary = source.get("summary")
        if isinstance(summary, dict):
            return summary
    return {}


def _scanner_statuses(validation: dict[str, Any]) -> list[dict[str, Any]]:
    statuses = []
    for name, source in _validation_sources(validation).items():
        if isinstance(source, dict):
            statuses.append({"id": name, "ok": bool(source.get("ok")), "error": source.get("error")})
    return statuses


def _performance_headline(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": _first_text(payload, ("rank", "performanceRank", "overallRank", "rating")) or "unknown",
        "triangleCount": _first_numeric(payload, ("triangleCount", "triangles", "polygonCount", "polygons")),
        "materialSlots": _first_numeric(payload, ("materialSlotCount", "slotCount", "materialCount")),
        "textureMemoryBytes": _first_numeric(payload, ("textureMemoryBytes", "vramBytes", "totalTextureBytes")),
    }


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _first_numeric(value: Any, names: tuple[str, ...]) -> int | float | None:
    wanted = {_normalize_key(name) for name in names}
    for entry in _walk_dicts(value):
        for key, raw in entry.items():
            if _normalize_key(str(key)) not in wanted:
                continue
            if isinstance(raw, bool):
                continue
            if isinstance(raw, (int, float)):
                return raw
            if isinstance(raw, list):
                return len(raw)
            if isinstance(raw, str):
                match = re.search(r"-?\d+(?:\.\d+)?", raw.replace(",", ""))
                if match:
                    number = float(match.group(0))
                    return int(number) if number.is_integer() else number
    return None


def _first_text(value: Any, names: tuple[str, ...]) -> str | None:
    wanted = {_normalize_key(name) for name in names}
    for entry in _walk_dicts(value):
        for key, raw in entry.items():
            if _normalize_key(str(key)) in wanted and raw is not None:
                if isinstance(raw, (str, int, float)):
                    text = str(raw).strip()
                    if text:
                        return text
    return None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _safe_asset_label(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return ""
    if "/Assets/" in text:
        text = "Assets/" + text.split("/Assets/", 1)[1]
    parts = [part for part in text.split("/") if part]
    return "/".join(parts[-4:])[:180] if parts else text[:180]


def _looks_like_texture_name(value: str) -> bool:
    return bool(re.search(r"\.(png|jpg|jpeg|tga|psd|exr|hdr|asset)$", value.lower())) or any(
        token in value.lower() for token in ("texture", "tex_", "_tex", "normal", "mask", "matcap")
    )


def classify_texture_role(name: str) -> str:
    text = name.lower()
    for role, tokens in {
        "normal": ("normal", "_nrm", "_n."),
        "mask": ("mask", "rough", "metal", "smooth", "ao"),
        "emission": ("emission", "emit", "_emi"),
        "matcap": ("matcap", "cap"),
        "hair": ("hair", "bang", "twin"),
        "skin": ("skin", "body", "face"),
        "clothing": ("cloth", "dress", "shirt", "pants", "skirt", "outfit", "swimsuit"),
        "accessory": ("acc", "accessory", "hat", "glasses", "ribbon", "shoe"),
        "base color": ("base", "albedo", "diffuse", "color", "maintex"),
    }.items():
        if any(token in text for token in tokens):
            return role
    return "unknown"


def _is_large_texture(width: int | float | None, height: int | float | None, bytes_value: int | float | None) -> bool:
    if bytes_value is not None and bytes_value >= 16 * 1024 * 1024:
        return True
    if width is not None and height is not None and max(width, height) >= 2048:
        return True
    return False


def _texture_duplicate_key(name: str) -> str:
    return re.sub(r"([_-]?\d{3,4}x\d{3,4}|copy|duplicate|\s+)", "", Path(name.replace("\\", "/")).stem.lower())


def _duplicate_groups(textures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for texture in textures:
        key = str(texture.get("duplicateKey") or "")
        if key:
            groups.setdefault(key, []).append(texture)
    return [{"key": key, "count": len(items), "textures": [item.get("name") for item in items[:8]]} for key, items in groups.items() if len(items) > 1]


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def material_flags(text: str) -> list[str]:
    lower = text.lower()
    flags = []
    for label, tokens in {
        "transparent/glass": ("transparent", "alpha", "glass", "cutout"),
        "emission": ("emission", "emit", "glow"),
        "skin": ("skin", "body", "face"),
        "hair": ("hair", "bang"),
        "special shader": ("poiyomi", "liltoon", "arktoon", "toon"),
    }.items():
        if any(token in lower for token in tokens):
            flags.append(label)
    return flags


def classify_mesh_risk(label: str, entry: dict[str, Any]) -> str:
    text = f"{label} {' '.join(str(v) for v in entry.values() if isinstance(v, (str, int, float)))}".lower()
    if any(token in text for token in ("face", "head", "eye", "mouth", "hand")):
        return "face"
    if any(token in text for token in ("body", "skin")):
        return "body"
    if any(token in text for token in ("blendshape", "blend shape", "expression")):
        return "high-blendshape mesh"
    if "hair" in text:
        return "hair"
    if any(token in text for token in ("cloth", "dress", "shirt", "pants", "skirt", "outfit")):
        return "clothing"
    if any(token in text for token in ("accessory", "hat", "ribbon", "glasses", "shoe", "acc")):
        return "low-risk accessory"
    return "unknown"


def _unique_by(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for item in items:
        value = str(item.get(key) or "")
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(item)
    return out


def _dependency_by_id(dependency_doctor: dict[str, Any], dependency_id: str) -> dict[str, Any]:
    for item in dependency_doctor.get("dependencies") or []:
        if item.get("id") == dependency_id:
            return item
    return {"id": dependency_id, "status": "missing", "installed": False}


def _estimated_texture_benefit(texture_audit: dict[str, Any], risk: str) -> str:
    large_count = int(texture_audit.get("summary", {}).get("largeTextureCount") or 0)
    if risk == "low":
        return "low" if large_count else "unknown"
    if large_count >= 3:
        return "high"
    if large_count:
        return "medium"
    return "unknown"


def _dedupe_labels(labels: list[str]) -> list[str]:
    out = []
    seen = set()
    for label in labels:
        text = str(label or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _classify_ma_layer(name: str) -> str:
    lower = name.lower()
    if "toggle" in lower:
        return "Object Toggle"
    if "material" in lower:
        return "Material Setter"
    if "shape" in lower or "blend" in lower:
        return "Shape Changer"
    return "MA Responsive layer"


def _action_card(
    card_id: str,
    title: str,
    description: str,
    risk: str,
    dependency: str,
    version_stage: str,
    level: str,
    blocked_reason: str | None,
    expected_benefit: str,
    why: str,
    next_action: str,
    affected: list[Any],
) -> dict[str, Any]:
    return {
        "id": card_id,
        "title": title,
        "description": description,
        "riskLevel": risk,
        "dependency": dependency,
        "recommendedVersionStage": version_stage,
        "level": level,
        "enabled": not blocked_reason,
        "blockedReason": blocked_reason,
        "expectedBenefit": expected_benefit,
        "whyRecommended": why,
        "nextSafeAction": next_action,
        "affectedAssetsOrRenderers": affected[:12],
        "directApplyExposed": False,
    }

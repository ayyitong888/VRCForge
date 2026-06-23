from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OPTIMIZATION_SCHEMA = "vrcforge.optimization.v1"
OPTIMIZATION_VERSION_STAGE = "0.7.2-beta"
MEGABYTE = 1024 * 1024

UPLOAD_GATE_LIMITS: dict[str, Any] = {
    "docsCheckedAt": "2026-06-23",
    "sources": [
        "https://creators.vrchat.com/avatars/avatar-size-limits/",
        "https://creators.vrchat.com/avatars/animator-parameters/",
        "https://creators.vrchat.com/avatars/avatar-performance-ranking-system/",
    ],
    "pc": {
        "downloadSizeBytes": 200 * MEGABYTE,
        "downloadSizeMb": 200,
        "uncompressedSizeBytes": 500 * MEGABYTE,
        "uncompressedSizeMb": 500,
    },
    "android": {
        "downloadSizeBytes": 10 * MEGABYTE,
        "downloadSizeMb": 10,
        "uncompressedSizeBytes": 40 * MEGABYTE,
        "uncompressedSizeMb": 40,
        "mobileComponentLimits": {
            "physBoneComponents": 8,
            "physBoneAffectedTransforms": 64,
            "physBoneColliders": 16,
            "physBoneCollisionCheckCount": 64,
            "contacts": 16,
            "constraints": 150,
            "constraintDepth": 50,
        },
    },
    "parameters": {
        "syncedBits": 256,
        "totalCustomParameters": 8192,
        "typeCosts": {"Bool": 1, "Int": 8, "Float": 8},
    },
}

PERFORMANCE_REVIEW_LIMITS: dict[str, Any] = {
    "pc": {
        "triangles": 70000,
        "textureMemoryBytes": 150 * MEGABYTE,
        "skinnedMeshes": 16,
        "basicMeshes": 24,
        "materialSlots": 32,
        "physBoneComponents": 32,
        "physBoneAffectedTransforms": 256,
        "physBoneColliders": 32,
        "physBoneCollisionCheckCount": 512,
        "contacts": 32,
        "constraints": 350,
        "animators": 32,
        "particles": 16,
    },
    "android": {
        "triangles": 20000,
        "textureMemoryBytes": 40 * MEGABYTE,
        "skinnedMeshes": 2,
        "basicMeshes": 2,
        "materialSlots": 4,
        "physBoneComponents": 8,
        "physBoneAffectedTransforms": 64,
        "physBoneColliders": 16,
        "physBoneCollisionCheckCount": 64,
        "contacts": 16,
        "constraints": 150,
        "animators": 2,
        "particles": 2,
    },
}


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
        "externalName": "optimization.physbone.audit",
        "gatewayName": "vrcforge_optimization_physbone_audit",
        "category": "read/debug",
        "description": "Audit PhysBone counts, affected transforms, colliders, and collision checks without changing physics components.",
    },
    {
        "externalName": "optimization.physbone.reduce-plan",
        "gatewayName": "vrcforge_optimization_physbone_reduce_plan",
        "category": "plan/preview",
        "description": "Plan conservative PhysBone overhead reduction gates without merging, removing, or disabling components.",
    },
    {
        "externalName": "optimization.aao.hidden-body-cut-plan",
        "gatewayName": "vrcforge_optimization_aao_hidden_body_cut_plan",
        "category": "plan/preview",
        "description": "Plan high-risk hidden body cut review gates without adding AAO Remove Mesh components.",
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
        "externalName": "optimization.upload-gate.audit",
        "gatewayName": "vrcforge_optimization_upload_gate_audit",
        "category": "read/debug",
        "description": "Separate SDK upload blockers from performance-rank offenders without changing project files.",
    },
    {
        "externalName": "optimization.upload-gate.fix-plan",
        "gatewayName": "vrcforge_optimization_upload_gate_fix_plan",
        "category": "plan/preview",
        "description": "Plan read-only upload-gate fixes grouped by blocker, performance offender, and risky fix.",
    },
    {
        "externalName": "optimization.parameter.inventory",
        "gatewayName": "vrcforge_optimization_parameter_inventory",
        "category": "read/debug",
        "description": "Inventory custom Expression Parameters and synced bit usage without changing parameters.",
    },
    {
        "externalName": "optimization.parameter.menu-map",
        "gatewayName": "vrcforge_optimization_parameter_menu_map",
        "category": "read/debug",
        "description": "Map expression menu controls to parameters without editing menus.",
    },
    {
        "externalName": "optimization.parameter.animator-usage",
        "gatewayName": "vrcforge_optimization_parameter_animator_usage",
        "category": "read/debug",
        "description": "Map FX animator conditions and controller declarations to custom parameters without editing controllers.",
    },
    {
        "externalName": "optimization.parameter.compressibility-plan",
        "gatewayName": "vrcforge_optimization_parameter_compressibility_plan",
        "category": "plan/preview",
        "description": "Classify parameter compression candidates and exclusions without applying compression.",
    },
    {
        "externalName": "optimization.parameter.vrcfury-compressor-plan",
        "gatewayName": "vrcforge_optimization_parameter_vrcfury_compressor_plan",
        "category": "plan/preview",
        "description": "Plan VRCFury Parameter Compressor usage as an experimental request-only path with behavior-regression gates.",
    },
    {
        "externalName": "optimization.parameter.behavior-regression",
        "gatewayName": "vrcforge_optimization_parameter_behavior_regression",
        "category": "plan/preview",
        "description": "Plan menu, FX, puppet, OSC, and face-tracking behavior regression checks before any parameter compression.",
    },
    {
        "externalName": "optimization.parameter.path-to-skill",
        "gatewayName": "vrcforge_optimization_parameter_path_to_skill",
        "category": "plan/preview",
        "description": "Map parameter compression candidates into the future request-only skill path and hard gates.",
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
    {
        "externalName": "optimization.performance-tools.report",
        "gatewayName": "vrcforge_optimization_performance_tools_report",
        "category": "read/debug",
        "description": "Report how VRCForge can call VRC Avatar Performance Tools and expose the read-only Thry VRAM/mesh calculator surface.",
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
    {"externalName": "optimization.vrcfury.parameter-compressor-apply-request", "versionStage": "0.8.1-beta", "directApplyExposed": False},
    {"externalName": "optimization.vrcfury.direct-tree-apply-request", "versionStage": "0.8.1-beta", "directApplyExposed": False},
]


OPTIMIZATION_APPLY_REQUEST_DEFINITIONS: list[dict[str, Any]] = [
    {
        "externalName": "optimization.lac.apply-request",
        "gatewayName": "vrcforge_optimization_lac_apply_request",
        "optimizerId": "lac",
        "planTool": "optimization.lac.profile-plan",
        "targetTool": "vrcforge_configure_optimizer_component",
        "mode": "lac_profile",
        "componentType": "dev.limitex.avatar.compressor.TextureCompressor",
        "riskLevel": "medium",
        "versionStage": "0.8.0-beta",
        "writeSupported": True,
        "stableCallable": True,
        "supportedProfiles": ["pc_conservative", "conservative_pc", "high_quality", "pc_medium", "balanced_pc", "balanced"],
        "description": "Request supervised LAC / Avatar Compressor component setup. Creates an approval request only; no direct apply is exposed.",
    },
    {
        "externalName": "optimization.aao.trace-apply-request",
        "gatewayName": "vrcforge_optimization_aao_trace_apply_request",
        "optimizerId": "aao",
        "planTool": "optimization.aao.trace-plan",
        "targetTool": "vrcforge_configure_optimizer_component",
        "mode": "aao_trace",
        "componentType": "Anatawa12.AvatarOptimizer.TraceAndOptimize",
        "riskLevel": "medium",
        "versionStage": "0.8.0-beta",
        "writeSupported": True,
        "stableCallable": True,
        "supportedProfiles": ["pc_conservative", "conservative_pc", "pc_medium", "balanced_pc", "custom"],
        "description": "Request supervised AAO Trace And Optimize setup. Creates an approval request only; no direct apply is exposed.",
    },
    {
        "externalName": "optimization.ttt.atlas-apply-request",
        "gatewayName": "vrcforge_optimization_ttt_atlas_apply_request",
        "optimizerId": "textrans_tool",
        "planTool": "optimization.ttt.atlas-plan",
        "targetTool": "vrcforge_configure_optimizer_component",
        "mode": "ttt_atlas",
        "componentType": "net.rs64.TexTransTool.TextureAtlas.AtlasTexture",
        "riskLevel": "medium",
        "versionStage": "0.8.0-beta",
        "writeSupported": True,
        "stableCallable": True,
        "supportedProfiles": ["pc_conservative", "conservative_pc", "pc_medium", "balanced_pc", "custom"],
        "requiresUserConfirmedMaterials": True,
        "description": "Request supervised TexTransTool AtlasTexture setup with user-confirmed material asset paths. Creates an approval request only; no direct apply is exposed.",
    },
    {
        "externalName": "optimization.ma2bt.convert-apply-request",
        "gatewayName": "vrcforge_optimization_ma2bt_convert_apply_request",
        "optimizerId": "ma2bt_pro",
        "planTool": "optimization.ma2bt.convertibility-plan",
        "targetTool": "vrcforge_configure_optimizer_component",
        "mode": "ma2bt_convert",
        "componentType": "zhuozhi.MA2BTPro.MAToBlendTreePro",
        "riskLevel": "medium",
        "versionStage": "0.8.0-beta",
        "writeSupported": True,
        "stableCallable": True,
        "supportedProfiles": ["pc_conservative", "conservative_pc", "pc_medium", "balanced_pc", "custom"],
        "description": "Request supervised MA2BT-Pro component setup for MA-heavy avatars. Creates an approval request only; no direct apply is exposed.",
    },
    {
        "externalName": "optimization.meshia.simplify-apply-request",
        "gatewayName": "vrcforge_optimization_meshia_simplify_apply_request",
        "optimizerId": "meshia",
        "planTool": "optimization.meshia.simplify-plan",
        "targetTool": "vrcforge_configure_optimizer_component",
        "mode": "meshia_simplify",
        "componentType": "Meshia.MeshSimplification.Ndmf.MeshiaMeshSimplifier",
        "riskLevel": "high",
        "versionStage": "0.8.1-beta",
        "writeSupported": True,
        "stableCallable": True,
        "supportedProfiles": ["pc_conservative", "conservative_pc", "pc_medium", "balanced_pc", "custom"],
        "requiresRendererPath": True,
        "description": "Request supervised Meshia simplifier setup on one user-selected low-risk Renderer. Creates an approval request only; no direct apply is exposed.",
    },
    {
        "externalName": "optimization.vrcfury.parameter-compressor-apply-request",
        "gatewayName": "vrcforge_optimization_vrcfury_parameter_compressor_apply_request",
        "optimizerId": "vrcfury",
        "planTool": "optimization.vrcfury.compatibility-report",
        "targetTool": "vrcforge_configure_optimizer_component",
        "mode": "vrcfury_parameter_compressor",
        "componentType": "",
        "riskLevel": "high",
        "versionStage": "0.8.1-beta",
        "writeSupported": False,
        "stableCallable": True,
        "supportedProfiles": [],
        "description": "Stable request surface for VRCFury Parameter Compressor. It returns a blocked preview until a public, validated VRCFury writer path exists.",
    },
    {
        "externalName": "optimization.vrcfury.direct-tree-apply-request",
        "gatewayName": "vrcforge_optimization_vrcfury_direct_tree_apply_request",
        "optimizerId": "vrcfury",
        "planTool": "optimization.vrcfury.compatibility-report",
        "targetTool": "vrcforge_configure_optimizer_component",
        "mode": "vrcfury_direct_tree",
        "componentType": "",
        "riskLevel": "high",
        "versionStage": "0.8.1-beta",
        "writeSupported": False,
        "stableCallable": True,
        "supportedProfiles": [],
        "description": "Stable request surface for VRCFury Direct Tree. It returns a blocked preview by default because Direct Tree remains experimental.",
    },
]


STABLE_OPTIMIZATION_APPLY_REQUEST_DEFINITIONS = [
    item for item in OPTIMIZATION_APPLY_REQUEST_DEFINITIONS if item.get("stableCallable")
]
OPTIMIZATION_APPLY_REQUEST_BY_EXTERNAL = {
    item["externalName"]: item for item in OPTIMIZATION_APPLY_REQUEST_DEFINITIONS
}
OPTIMIZATION_APPLY_REQUEST_BY_GATEWAY = {
    item["gatewayName"]: item for item in OPTIMIZATION_APPLY_REQUEST_DEFINITIONS
}
OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES = [
    item["gatewayName"] for item in OPTIMIZATION_APPLY_REQUEST_DEFINITIONS
]
STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES = [
    item["gatewayName"] for item in STABLE_OPTIMIZATION_APPLY_REQUEST_DEFINITIONS
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
            "supervisedInstallRequestAvailable": True,
            "preferredManagers": ["ALCOM/VCC UI handoff", "VCC vpm CLI", "vrc-get CLI", "agent-managed download plan"],
            "message": "Dependency installs are request-only: VRCForge plans package-manager use first, then any project write still requires approval and checkpoint.",
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
    physbone_audit = build_physbone_audit(validation)
    parameter_audit = build_parameter_budget_audit(validation)
    upload_gate_audit = build_upload_gate_audit(validation)
    parameter_inventory = build_parameter_inventory(validation)
    parameter_menu_map = build_parameter_menu_map(validation)
    parameter_animator_usage = build_parameter_animator_usage(validation)
    parameter_compressibility = build_parameter_compressibility_plan(validation)
    parameter_behavior_regression = build_parameter_behavior_regression_plan(validation)
    parameter_path_to_skill = build_parameter_path_to_skill_plan(dependency_doctor, validation)
    aao_plan = build_aao_trace_plan(dependency_doctor, validation)
    physbone_reduce_plan = build_physbone_reduce_plan(dependency_doctor, physbone_audit)
    hidden_body_cut_plan = build_aao_hidden_body_cut_plan(dependency_doctor, validation)
    lac_plan = build_lac_profile_plan(profile, dependency_doctor, texture_audit)
    ttt_plan = build_ttt_atlas_plan(dependency_doctor, material_audit, texture_audit)
    meshia_plan = build_meshia_simplify_plan(dependency_doctor, mesh_audit)
    vrcfury_report = build_vrcfury_compatibility_report(dependency_doctor, validation)
    ma_audit = build_ma_responsive_layer_audit(validation)
    ma2bt_plan = build_ma2bt_convertibility_plan(dependency_doctor, ma_audit)
    visual_plan = build_visual_regression_plan(params)
    rollback = build_rollback_verify(params, validation)
    performance_tools_report = build_performance_tools_report(dependency_doctor, validation)
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
        performance_tools_report,
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
            "physBones": physbone_audit,
            "parameterBudget": parameter_audit,
            "uploadGate": upload_gate_audit,
            "parameterInventory": parameter_inventory,
            "parameterMenuMap": parameter_menu_map,
            "parameterAnimatorUsage": parameter_animator_usage,
            "parameterCompressibility": parameter_compressibility,
            "maResponsiveLayers": ma_audit,
        },
        "plans": {
            "lacProfile": lac_plan,
            "tttAtlas": ttt_plan,
            "aaoTrace": aao_plan,
            "physBoneReduce": physbone_reduce_plan,
            "hiddenBodyCut": hidden_body_cut_plan,
            "meshiaSimplify": meshia_plan,
            "vrcfuryCompatibility": vrcfury_report,
            "ma2btConvertibility": ma2bt_plan,
            "parameterBehaviorRegression": parameter_behavior_regression,
            "parameterPathToSkill": parameter_path_to_skill,
            "visualRegression": visual_plan,
            "rollbackVerify": rollback,
            "performanceTools": performance_tools_report,
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
    elif external_name == "optimization.physbone.audit":
        result = build_physbone_audit(validation)
    elif external_name == "optimization.physbone.reduce-plan":
        result = build_physbone_reduce_plan(dependency_doctor, build_physbone_audit(validation))
    elif external_name == "optimization.aao.hidden-body-cut-plan":
        result = build_aao_hidden_body_cut_plan(dependency_doctor, validation)
    elif external_name == "optimization.mesh.triangle-audit":
        result = build_mesh_triangle_audit(validation)
    elif external_name == "optimization.meshia.simplify-plan":
        result = build_meshia_simplify_plan(dependency_doctor, build_mesh_triangle_audit(validation))
    elif external_name == "optimization.parameter-budget-audit":
        result = build_parameter_budget_audit(validation)
    elif external_name == "optimization.upload-gate.audit":
        result = build_upload_gate_audit(validation)
    elif external_name == "optimization.upload-gate.fix-plan":
        result = build_upload_gate_fix_plan(build_upload_gate_audit(validation))
    elif external_name == "optimization.parameter.inventory":
        result = build_parameter_inventory(validation)
    elif external_name == "optimization.parameter.menu-map":
        result = build_parameter_menu_map(validation)
    elif external_name == "optimization.parameter.animator-usage":
        result = build_parameter_animator_usage(validation)
    elif external_name == "optimization.parameter.compressibility-plan":
        result = build_parameter_compressibility_plan(validation)
    elif external_name == "optimization.parameter.vrcfury-compressor-plan":
        result = build_vrcfury_parameter_compressor_plan(dependency_doctor, validation)
    elif external_name == "optimization.parameter.behavior-regression":
        result = build_parameter_behavior_regression_plan(validation)
    elif external_name == "optimization.parameter.path-to-skill":
        result = build_parameter_path_to_skill_plan(dependency_doctor, validation)
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
    elif external_name == "optimization.performance-tools.report":
        result = build_performance_tools_report(dependency_doctor, validation)
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


def build_upload_gate_audit(validation: dict[str, Any]) -> dict[str, Any]:
    sources = _validation_sources(validation)
    pc = _source_payload(sources, "performance_pc")
    android = _source_payload(sources, "performance_quest")
    materials = _source_payload(sources, "materials")
    avatar_items = _source_payload(sources, "avatar_items")
    parameter_inventory = build_parameter_inventory(validation)
    material_audit = build_material_slot_audit(validation)

    metrics = {
        "pc": {
            "downloadSizeBytes": _first_size_bytes(pc, ("downloadSizeBytes", "downloadSize", "compressedSizeBytes", "compressedSize", "buildSizeBytes", "fileSizeBytes", "pcDownloadSizeBytes", "downloadSizeMb", "downloadSizeMB")),
            "uncompressedSizeBytes": _first_size_bytes(pc, ("uncompressedSizeBytes", "uncompressedSize", "uncompressedBytes", "bundleUncompressedSizeBytes", "pcUncompressedSizeBytes", "uncompressedSizeMb", "uncompressedSizeMB")),
            "rank": _performance_headline(pc).get("rank"),
        },
        "android": {
            "downloadSizeBytes": _first_size_bytes(android, ("downloadSizeBytes", "downloadSize", "compressedSizeBytes", "compressedSize", "buildSizeBytes", "fileSizeBytes", "androidDownloadSizeBytes", "questDownloadSizeBytes", "downloadSizeMb", "downloadSizeMB")),
            "uncompressedSizeBytes": _first_size_bytes(android, ("uncompressedSizeBytes", "uncompressedSize", "uncompressedBytes", "bundleUncompressedSizeBytes", "androidUncompressedSizeBytes", "questUncompressedSizeBytes", "uncompressedSizeMb", "uncompressedSizeMB")),
            "rank": _performance_headline(android).get("rank"),
        },
        "parameters": {
            "syncedBits": parameter_inventory.get("summary", {}).get("syncedBits"),
            "totalCustomParameters": parameter_inventory.get("summary", {}).get("totalCustomParameters"),
        },
        "performance": {
            "textureMemoryBytes": _first_size_bytes(materials, ("textureMemoryBytes", "textureBytes", "vramBytes", "totalTextureBytes", "totalVRAMBytes")) or _first_size_bytes(pc, ("textureMemoryBytes", "textureBytes", "vramBytes", "totalTextureBytes")),
            "triangles": _first_numeric(pc, ("triangleCount", "triangles", "polygonCount", "polygons")),
            "materialSlots": material_audit.get("summary", {}).get("knownMaterialSlotCount") or _first_numeric(pc, ("materialSlotCount", "slotCount", "materialCount")),
            "skinnedMeshes": _first_numeric(pc, ("skinnedMeshCount", "skinnedMeshes", "skinnedMeshRendererCount")) or _sum_direct_numeric(avatar_items, ("skinned_renderer_count", "skinnedRendererCount", "skinnedMeshCount")),
            "basicMeshes": _first_numeric(pc, ("meshCount", "basicMeshCount", "meshRendererCount", "basicMeshes")) or _estimate_basic_mesh_count(avatar_items),
            "physBoneComponents": _first_numeric(pc, ("physBoneCount", "physBones", "physBoneComponents")) or _component_type_count(avatar_items, ("physbone",)),
            "physBoneAffectedTransforms": _first_numeric(pc, ("physBoneAffectedTransforms", "affectedTransforms")),
            "physBoneColliders": _first_numeric(pc, ("physBoneColliderCount", "physBoneColliders")),
            "physBoneCollisionCheckCount": _first_numeric(pc, ("physBoneCollisionCheckCount", "collisionCheckCount")),
            "contacts": _first_numeric(pc, ("contactCount", "contacts")) or _component_type_count(avatar_items, ("contact",)),
            "constraints": _first_numeric(pc, ("constraintCount", "constraints")) or _component_type_count(avatar_items, ("constraint",)),
            "animators": _first_numeric(pc, ("animatorCount", "animators")) or _component_type_count(avatar_items, ("animator",)),
            "particles": _first_numeric(pc, ("particleSystemCount", "particleSystems", "particles")) or _component_type_count(avatar_items, ("particlesystem", "particle system")),
            "meshReadWriteDisabled": _detect_mesh_read_write_disabled(pc, android),
        },
    }

    checks = [
        _limit_check("pc_download_size", "PC download size", metrics["pc"]["downloadSizeBytes"], UPLOAD_GATE_LIMITS["pc"]["downloadSizeBytes"], "bytes", "hard_upload_blocker", "performance_pc"),
        _limit_check("pc_uncompressed_size", "PC uncompressed size", metrics["pc"]["uncompressedSizeBytes"], UPLOAD_GATE_LIMITS["pc"]["uncompressedSizeBytes"], "bytes", "hard_upload_blocker", "performance_pc"),
        _limit_check("android_download_size", "Android download size", metrics["android"]["downloadSizeBytes"], UPLOAD_GATE_LIMITS["android"]["downloadSizeBytes"], "bytes", "hard_upload_blocker", "performance_quest"),
        _limit_check("android_uncompressed_size", "Android uncompressed size", metrics["android"]["uncompressedSizeBytes"], UPLOAD_GATE_LIMITS["android"]["uncompressedSizeBytes"], "bytes", "hard_upload_blocker", "performance_quest"),
        _limit_check("synced_parameter_bits", "Synced Expression Parameter memory", metrics["parameters"]["syncedBits"], UPLOAD_GATE_LIMITS["parameters"]["syncedBits"], "bits", "hard_upload_blocker", "parameters"),
        _limit_check("total_custom_expression_parameters", "Total custom Expression Parameters", metrics["parameters"]["totalCustomParameters"], UPLOAD_GATE_LIMITS["parameters"]["totalCustomParameters"], "count", "hard_upload_blocker", "parameters"),
    ]

    performance_checks = [
        _limit_check("texture_memory", "Texture Memory", metrics["performance"]["textureMemoryBytes"], PERFORMANCE_REVIEW_LIMITS["pc"]["textureMemoryBytes"], "bytes", "performance_rank_offender", "materials"),
        _limit_check("triangles", "Triangles", metrics["performance"]["triangles"], PERFORMANCE_REVIEW_LIMITS["pc"]["triangles"], "count", "performance_rank_offender", "performance_pc"),
        _limit_check("material_slots", "Material slots", metrics["performance"]["materialSlots"], PERFORMANCE_REVIEW_LIMITS["pc"]["materialSlots"], "count", "performance_rank_offender", "materials"),
        _limit_check("skinned_meshes", "Skinned meshes", metrics["performance"]["skinnedMeshes"], PERFORMANCE_REVIEW_LIMITS["pc"]["skinnedMeshes"], "count", "performance_rank_offender", "avatar_items"),
        _limit_check("basic_meshes", "Basic meshes", metrics["performance"]["basicMeshes"], PERFORMANCE_REVIEW_LIMITS["pc"]["basicMeshes"], "count", "performance_rank_offender", "avatar_items"),
        _limit_check("physbone_components", "PhysBone components", metrics["performance"]["physBoneComponents"], PERFORMANCE_REVIEW_LIMITS["pc"]["physBoneComponents"], "count", "performance_rank_offender", "avatar_items"),
        _limit_check("physbone_affected_transforms", "PhysBone affected transforms", metrics["performance"]["physBoneAffectedTransforms"], PERFORMANCE_REVIEW_LIMITS["pc"]["physBoneAffectedTransforms"], "count", "performance_rank_offender", "performance_pc"),
        _limit_check("physbone_colliders", "PhysBone colliders", metrics["performance"]["physBoneColliders"], PERFORMANCE_REVIEW_LIMITS["pc"]["physBoneColliders"], "count", "performance_rank_offender", "performance_pc"),
        _limit_check("physbone_collision_checks", "PhysBone collision check count", metrics["performance"]["physBoneCollisionCheckCount"], PERFORMANCE_REVIEW_LIMITS["pc"]["physBoneCollisionCheckCount"], "count", "performance_rank_offender", "performance_pc"),
        _limit_check("contacts", "Contacts", metrics["performance"]["contacts"], PERFORMANCE_REVIEW_LIMITS["pc"]["contacts"], "count", "performance_rank_offender", "avatar_items"),
        _limit_check("constraints", "Constraints", metrics["performance"]["constraints"], PERFORMANCE_REVIEW_LIMITS["pc"]["constraints"], "count", "performance_rank_offender", "avatar_items"),
        _limit_check("animators", "Animators", metrics["performance"]["animators"], PERFORMANCE_REVIEW_LIMITS["pc"]["animators"], "count", "performance_rank_offender", "avatar_items"),
        _limit_check("particles", "Particles", metrics["performance"]["particles"], PERFORMANCE_REVIEW_LIMITS["pc"]["particles"], "count", "performance_rank_offender", "avatar_items"),
    ]
    checks.extend(performance_checks)

    mesh_read_write = metrics["performance"]["meshReadWriteDisabled"]
    checks.append(
        {
            "id": "mesh_read_write_disabled",
            "label": "Mesh Read/Write Disabled guard",
            "value": mesh_read_write,
            "limit": False,
            "unit": "bool",
            "status": "risk" if mesh_read_write is True else ("unknown" if mesh_read_write is None else "pass"),
            "category": "risky_fix",
            "source": "performance_pc",
            "message": "Disabling Mesh Read/Write is not an optimization; when detected by the SDK it can force Very Poor rank or upload warning.",
        }
    )

    hard_blockers = [item for item in checks if item.get("category") == "hard_upload_blocker" and item.get("status") == "blocker"]
    performance_offenders = [item for item in checks if item.get("category") == "performance_rank_offender" and item.get("status") == "offender"]
    risky_fixes = [item for item in checks if item.get("category") == "risky_fix" and item.get("status") in {"risk", "unknown"}]
    return {
        "readOnly": True,
        "limits": UPLOAD_GATE_LIMITS,
        "metrics": metrics,
        "checks": checks,
        "groups": {
            "hardUploadBlockers": hard_blockers,
            "performanceRankOffenders": performance_offenders,
            "riskyFixes": risky_fixes,
        },
        "summary": {
            "hardBlockerCount": len(hard_blockers),
            "performanceRankOffenderCount": len(performance_offenders),
            "riskyFixCount": len(risky_fixes),
            "unknownMetricCount": sum(1 for item in checks if item.get("status") == "unknown"),
            "sourceStatus": {name: _validation_source_status(source) for name, source in sources.items()},
        },
        "notes": [
            "Upload hard blockers are separated from performance-rank offenders.",
            "PC uncompressed size is bundle size, not VRAM.",
            "Unknown SDK fields stay unknown; VRCForge does not infer upload pass from missing data.",
        ],
    }


def build_upload_gate_fix_plan(upload_gate: dict[str, Any]) -> dict[str, Any]:
    groups = upload_gate.get("groups") if isinstance(upload_gate.get("groups"), dict) else {}
    steps = []
    for item in groups.get("hardUploadBlockers") or []:
        steps.append(
            {
                "id": f"fix_{item.get('id')}",
                "category": "hard_upload_blocker",
                "title": f"Bring {item.get('label')} under the upload limit",
                "risk": "medium",
                "writePath": "request-only",
                "why": item.get("message"),
            }
        )
    for item in groups.get("performanceRankOffenders") or []:
        steps.append(
            {
                "id": f"review_{item.get('id')}",
                "category": "performance_rank_offender",
                "title": f"Review {item.get('label')} before optimizing",
                "risk": "review",
                "writePath": "none",
                "why": item.get("message"),
            }
        )
    for item in groups.get("riskyFixes") or []:
        steps.append(
            {
                "id": f"guard_{item.get('id')}",
                "category": "risky_fix",
                "title": f"Do not blindly apply {item.get('label')}",
                "risk": "high",
                "writePath": "blocked_until_dedicated_proof",
                "why": item.get("message"),
            }
        )
    return {
        "planOnly": True,
        "uploadGateSummary": upload_gate.get("summary") or {},
        "steps": steps,
        "nextStage": "0.8.0-beta read-only foundation; future apply requests require approval, checkpoint, validation, and rollback proof.",
        "notes": ["This plan does not change textures, meshes, parameters, or import settings."],
    }


def build_parameter_inventory(validation: dict[str, Any]) -> dict[str, Any]:
    parameters = _source_payload(_validation_sources(validation), "parameters")
    entries = _parameter_entries(parameters)
    computed_synced_bits = sum(int(item.get("syncedBits") or 0) for item in entries)
    reported_synced_bits = _first_numeric(parameters, ("syncedBits", "bitsUsed", "totalEstimatedCost", "totalCost", "parameterCost"))
    reported_total = _first_numeric(parameters, ("totalParameters", "totalCustomParameters", "parameterCount", "customParameterCount"))
    synced_bits = int(reported_synced_bits if reported_synced_bits is not None else computed_synced_bits)
    total_parameters = int(reported_total if reported_total is not None else len(entries))
    return {
        "readOnly": True,
        "limits": UPLOAD_GATE_LIMITS["parameters"],
        "summary": {
            "totalCustomParameters": total_parameters,
            "totalCustomParameterLimit": UPLOAD_GATE_LIMITS["parameters"]["totalCustomParameters"],
            "syncedBits": synced_bits,
            "syncedBitLimit": UPLOAD_GATE_LIMITS["parameters"]["syncedBits"],
            "syncedParameterCount": sum(1 for item in entries if item.get("networkSynced")),
            "unsyncedParameterCount": sum(1 for item in entries if item.get("networkSynced") is False),
            "boolCount": sum(1 for item in entries if item.get("type") == "Bool"),
            "intCount": sum(1 for item in entries if item.get("type") == "Int"),
            "floatCount": sum(1 for item in entries if item.get("type") == "Float"),
            "scannerCoverage": "metadata" if entries or reported_synced_bits is not None or reported_total is not None else "unknown",
        },
        "parameters": entries[:500],
        "notes": ["Synced bits and total custom parameter count are different limits."],
    }


def build_parameter_menu_map(validation: dict[str, Any]) -> dict[str, Any]:
    menu = _source_payload(_validation_sources(validation), "menu")
    controls = []
    for item in _dict_list(menu, ("items", "controls", "menuItems")):
        parameter_name = _direct_text(item, ("parameterName", "parameter", "param"))
        display_name = _direct_text(item, ("displayName", "name", "controlName")) or parameter_name
        menu_path = _direct_text(item, ("menuPath", "path", "controlPath")) or ""
        if not display_name and not parameter_name:
            continue
        controls.append(
            {
                "displayName": str(display_name or "")[:120],
                "menuPath": _safe_asset_label(str(menu_path)),
                "parameterName": str(parameter_name or "")[:120],
                "controlType": _direct_text(item, ("controlType", "type")) or "unknown",
                "valueType": _normalize_parameter_type(_direct_text(item, ("valueType", "parameterType", "type"))),
                "networkSynced": _direct_bool(item, ("networkSynced", "synced")),
                "source": _direct_text(item, ("source",)) or "menu",
            }
        )
    mapped = {}
    for control in controls:
        name = str(control.get("parameterName") or "")
        if not name:
            continue
        bucket = mapped.setdefault(name, {"parameterName": name, "controlCount": 0, "controlTypes": set(), "menuPaths": []})
        bucket["controlCount"] += 1
        bucket["controlTypes"].add(control.get("controlType") or "unknown")
        if control.get("menuPath"):
            bucket["menuPaths"].append(control["menuPath"])
    parameter_map = [
        {
            "parameterName": value["parameterName"],
            "controlCount": value["controlCount"],
            "controlTypes": sorted(value["controlTypes"]),
            "menuPaths": _dedupe_labels(value["menuPaths"])[:12],
        }
        for value in mapped.values()
    ]
    return {
        "readOnly": True,
        "summary": {
            "knownControlCount": len(controls),
            "mappedParameterCount": len(parameter_map),
            "scannerCoverage": "metadata" if controls else "unknown",
        },
        "controls": controls[:500],
        "parameterMap": sorted(parameter_map, key=lambda item: item["parameterName"].lower())[:300],
    }


def build_parameter_animator_usage(validation: dict[str, Any]) -> dict[str, Any]:
    sources = _validation_sources(validation)
    fx = _source_payload(sources, "fx")
    bindings = _source_payload(sources, "animation_bindings")
    inventory = build_parameter_inventory(validation)
    menu_map = build_parameter_menu_map(validation)
    usage: dict[str, dict[str, Any]] = {}

    for parameter in inventory.get("parameters") or []:
        name = str(parameter.get("name") or "").strip()
        if name:
            usage.setdefault(name, _parameter_usage_bucket(name))["expressionDeclared"] = True
    for control in menu_map.get("controls") or []:
        name = str(control.get("parameterName") or "").strip()
        if name:
            bucket = usage.setdefault(name, _parameter_usage_bucket(name))
            bucket["menuControlCount"] += 1
            bucket["menuControlTypes"].add(str(control.get("controlType") or "unknown"))
    for parameter in _dict_list(fx, ("parameters", "controllerParameters")):
        name = _direct_text(parameter, ("name", "parameterName", "param"))
        if not name:
            continue
        bucket = usage.setdefault(name, _parameter_usage_bucket(name))
        bucket["animatorDeclared"] = True
        bucket["animatorType"] = _normalize_parameter_type(_direct_text(parameter, ("type", "valueType", "parameterType")))
        if _direct_bool(parameter, ("used_by_condition", "usedByCondition", "used")):
            bucket["usedByCondition"] = True
    for entry in _walk_dicts(fx):
        for condition in _coerce_list(entry.get("conditions")):
            if not isinstance(condition, dict):
                continue
            name = _direct_text(condition, ("parameter", "parameterName", "param"))
            if not name:
                continue
            bucket = usage.setdefault(name, _parameter_usage_bucket(name))
            bucket["conditionCount"] += 1
            bucket["usedByCondition"] = True
            mode = _direct_text(condition, ("mode", "conditionMode"))
            if mode:
                bucket["conditionModes"].add(mode)
    rows = []
    for bucket in usage.values():
        row = dict(bucket)
        row["menuControlTypes"] = sorted(row["menuControlTypes"])
        row["conditionModes"] = sorted(row["conditionModes"])
        row["usageClass"] = _parameter_usage_class(row)
        rows.append(row)
    binding_summary = bindings.get("summary") if isinstance(bindings.get("summary"), dict) else {}
    return {
        "readOnly": True,
        "summary": {
            "knownParameterUsageCount": len(rows),
            "conditionParameterCount": sum(1 for item in rows if item.get("conditionCount")),
            "menuControlledParameterCount": sum(1 for item in rows if item.get("menuControlCount")),
            "clipCount": binding_summary.get("clipCount"),
            "bindingCount": binding_summary.get("bindingCount"),
            "scannerCoverage": "metadata" if rows else "unknown",
        },
        "parameters": sorted(rows, key=lambda item: item["parameterName"].lower())[:500],
        "notes": ["Animator usage is read-only evidence; it is not a behavior-regression proof by itself."],
    }


def build_parameter_compressibility_plan(validation: dict[str, Any]) -> dict[str, Any]:
    inventory = build_parameter_inventory(validation)
    menu_map = build_parameter_menu_map(validation)
    animator_usage = build_parameter_animator_usage(validation)
    menu_counts = {item["parameterName"]: int(item.get("controlCount") or 0) for item in menu_map.get("parameterMap") or []}
    usage_map = {item["parameterName"]: item for item in animator_usage.get("parameters") or []}
    duplicate_names = _duplicate_parameter_keys(inventory.get("parameters") or [])
    categories: dict[str, list[dict[str, Any]]] = {
        "safe_to_unsync": [],
        "safe_to_pack": [],
        "safe_to_int_exclusive": [],
        "already_optimal_bool": [],
        "danger_continuous_float": [],
        "danger_puppet": [],
        "danger_osc_or_face_tracking": [],
        "unused_candidate": [],
        "duplicate_candidate": [],
        "unknown_do_not_touch": [],
    }
    for parameter in inventory.get("parameters") or []:
        category, reason = _classify_parameter_compressibility(parameter, menu_counts, usage_map, duplicate_names)
        categories.setdefault(category, []).append(
            {
                "name": parameter.get("name"),
                "type": parameter.get("type"),
                "syncedBits": parameter.get("syncedBits"),
                "networkSynced": parameter.get("networkSynced"),
                "menuControlCount": menu_counts.get(str(parameter.get("name") or ""), 0),
                "usageClass": usage_map.get(str(parameter.get("name") or ""), {}).get("usageClass"),
                "reason": reason,
            }
        )
    return {
        "planOnly": True,
        "limits": UPLOAD_GATE_LIMITS["parameters"],
        "summary": {
            "syncedBits": inventory.get("summary", {}).get("syncedBits"),
            "syncedBitLimit": UPLOAD_GATE_LIMITS["parameters"]["syncedBits"],
            "totalCustomParameters": inventory.get("summary", {}).get("totalCustomParameters"),
            "totalCustomParameterLimit": UPLOAD_GATE_LIMITS["parameters"]["totalCustomParameters"],
            "safeToPackCount": len(categories["safe_to_pack"]),
            "safeToIntExclusiveCount": len(categories["safe_to_int_exclusive"]),
            "dangerPuppetCount": len(categories["danger_puppet"]),
            "dangerOscOrFaceTrackingCount": len(categories["danger_osc_or_face_tracking"]),
            "continuousFloatDangerCount": len(categories["danger_continuous_float"]),
            "unknownDoNotTouchCount": len(categories["unknown_do_not_touch"]),
        },
        "categories": {key: value[:120] for key, value in categories.items()},
        "rules": [
            "Do not compress OSC, face tracking, puppet, or continuous real-time parameters automatically.",
            "Any compression apply path needs menu behavior regression and rollback proof.",
        ],
    }


def build_vrcfury_parameter_compressor_plan(dependency_doctor: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "vrcfury")
    compressibility = build_parameter_compressibility_plan(validation)
    categories = compressibility.get("categories") if isinstance(compressibility.get("categories"), dict) else {}
    candidates = []
    for category in ("safe_to_unsync", "safe_to_pack", "safe_to_int_exclusive", "unused_candidate", "duplicate_candidate"):
        for item in categories.get(category) or []:
            candidate = dict(item)
            candidate["category"] = category
            candidates.append(candidate)
    return {
        "planOnly": True,
        "dependency": dep,
        "experimentalOnly": True,
        "applyRequestTool": "optimization.vrcfury.parameter-compressor-apply-request",
        "applyBlocked": True,
        "blockedReason": "VRCFury Parameter Compressor writes stay experimental until behavior-regression and rollback proof exist.",
        "candidateCount": len(candidates),
        "candidates": candidates[:120],
        "dangerCounts": {
            "puppet": compressibility.get("summary", {}).get("dangerPuppetCount"),
            "oscOrFaceTracking": compressibility.get("summary", {}).get("dangerOscOrFaceTrackingCount"),
            "continuousFloat": compressibility.get("summary", {}).get("continuousFloatDangerCount"),
        },
        "requiredProof": [
            "menu toggle behavior regression",
            "outfit/int-exclusive state regression",
            "puppet exclusion proof",
            "OSC/face-tracking exclusion proof",
            "PC/Android parameter-order compatibility check",
            "approval -> checkpoint -> apply -> validation -> rollback proof",
        ],
    }


def build_parameter_behavior_regression_plan(validation: dict[str, Any]) -> dict[str, Any]:
    menu_map = build_parameter_menu_map(validation)
    animator_usage = build_parameter_animator_usage(validation)
    compressibility = build_parameter_compressibility_plan(validation)
    usage_by_name = {str(item.get("parameterName") or ""): item for item in animator_usage.get("parameters") or []}
    cases = []
    for control in menu_map.get("controls") or []:
        parameter_name = str(control.get("parameterName") or "").strip()
        if not parameter_name:
            continue
        control_type = str(control.get("controlType") or "unknown")
        usage = usage_by_name.get(parameter_name) or {}
        risk_flags = _parameter_regression_risk_flags(parameter_name, control_type, usage)
        cases.append(
            {
                "id": f"menu_{_normalize_key(parameter_name)[:60]}_{len(cases) + 1}",
                "parameterName": parameter_name,
                "source": "expression_menu",
                "controlType": control_type,
                "menuPath": control.get("menuPath"),
                "expectedProbe": _parameter_expected_probe(control_type),
                "riskFlags": risk_flags,
                "status": "required",
            }
        )
    menu_case_parameters = {case["parameterName"] for case in cases}
    for usage in animator_usage.get("parameters") or []:
        parameter_name = str(usage.get("parameterName") or "").strip()
        if not parameter_name or parameter_name in menu_case_parameters or not usage.get("conditionCount"):
            continue
        cases.append(
            {
                "id": f"fx_{_normalize_key(parameter_name)[:60]}_{len(cases) + 1}",
                "parameterName": parameter_name,
                "source": "fx_animator",
                "controlType": "condition",
                "menuPath": None,
                "expectedProbe": "Toggle or set the parameter in a controlled scene and verify the same FX state transition before/after.",
                "riskFlags": _parameter_regression_risk_flags(parameter_name, "condition", usage),
                "status": "required",
            }
        )
    danger_categories = []
    categories = compressibility.get("categories") if isinstance(compressibility.get("categories"), dict) else {}
    for category in ("danger_osc_or_face_tracking", "danger_puppet", "danger_continuous_float", "unknown_do_not_touch"):
        for item in categories.get(category) or []:
            danger_categories.append({"category": category, "name": item.get("name"), "reason": item.get("reason")})
    return {
        "planOnly": True,
        "proofReady": False,
        "summary": {
            "testCaseCount": len(cases),
            "menuControlCount": menu_map.get("summary", {}).get("knownControlCount"),
            "conditionParameterCount": animator_usage.get("summary", {}).get("conditionParameterCount"),
            "dangerParameterCount": len(danger_categories),
            "scannerCoverage": "metadata" if cases or danger_categories else "unknown",
        },
        "testCases": cases[:240],
        "blockedParameters": danger_categories[:160],
        "requiredArtifacts": [
            "before menu/FX behavior evidence",
            "after behavior evidence for every touched parameter",
            "validation delta",
            "rollback behavior spot-check",
        ],
        "notes": ["This regression plan is a gate; it does not prove behavior by itself."],
    }


def build_parameter_path_to_skill_plan(dependency_doctor: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "vrcfury")
    compressibility = build_parameter_compressibility_plan(validation)
    regression = build_parameter_behavior_regression_plan(validation)
    categories = compressibility.get("categories") if isinstance(compressibility.get("categories"), dict) else {}
    candidate_categories = ("safe_to_pack", "safe_to_int_exclusive", "safe_to_unsync", "unused_candidate", "duplicate_candidate")
    candidates = []
    for category in candidate_categories:
        for item in categories.get(category) or []:
            candidates.append({**item, "category": category})
    hard_gate_failures = []
    for key in ("danger_puppet", "danger_osc_or_face_tracking", "danger_continuous_float", "unknown_do_not_touch"):
        count = len(categories.get(key) or [])
        if count:
            hard_gate_failures.append({"id": key, "count": count, "status": "manual_review_required"})
    return {
        "planOnly": True,
        "dependency": dep,
        "candidateCount": len(candidates),
        "candidates": candidates[:120],
        "skillPath": [
            {"step": "inventory", "tool": "optimization.parameter.inventory", "status": "available"},
            {"step": "menu-map", "tool": "optimization.parameter.menu-map", "status": "available"},
            {"step": "animator-usage", "tool": "optimization.parameter.animator-usage", "status": "available"},
            {"step": "compressibility", "tool": "optimization.parameter.compressibility-plan", "status": "available"},
            {"step": "behavior-regression", "tool": "optimization.parameter.behavior-regression", "status": "required_before_write"},
            {"step": "future-request", "tool": "optimization.vrcfury.parameter-compressor-apply-request", "status": "blocked_preview"},
        ],
        "hardGates": {
            "behaviorRegressionCaseCount": regression.get("summary", {}).get("testCaseCount"),
            "blockedParameterCount": regression.get("summary", {}).get("dangerParameterCount"),
            "failures": hard_gate_failures,
        },
        "applyBlocked": True,
        "blockedReason": "Parameter compression remains request-only/blocked until behavior-regression proof and rollback proof are public.",
        "notes": ["This path converts planner evidence into future skill gates; it does not call VRCFury or rewrite parameters."],
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


def build_physbone_audit(validation: dict[str, Any]) -> dict[str, Any]:
    sources = _validation_sources(validation)
    pc = _source_payload(sources, "performance_pc")
    android = _source_payload(sources, "performance_quest")
    avatar_items = _source_payload(sources, "avatar_items")
    components = []
    for entry in _walk_dicts(avatar_items):
        raw_components = _coerce_list(entry.get("component_types") or entry.get("componentTypes") or entry.get("components"))
        joined_components = " ".join(str(item).replace(" ", "").lower() for item in raw_components)
        entry_text = json.dumps(entry, ensure_ascii=False, default=str).lower()
        if "physbone" not in joined_components and "phys bone" not in entry_text and "physbone" not in entry_text:
            continue
        object_path = _direct_text(entry, ("gameObjectPath", "objectPath", "path", "name")) or "unknown"
        affected = _direct_numeric(entry, ("physBoneAffectedTransforms", "affectedTransforms", "affectedTransformCount"))
        colliders = _direct_numeric(entry, ("physBoneColliders", "physBoneColliderCount", "colliderCount", "colliders"))
        collision_checks = _direct_numeric(entry, ("physBoneCollisionCheckCount", "collisionCheckCount", "collisionChecks"))
        flags = []
        if affected is not None and affected > PERFORMANCE_REVIEW_LIMITS["pc"]["physBoneAffectedTransforms"]:
            flags.append("pc_affected_transform_over_limit")
        if colliders is not None and colliders > PERFORMANCE_REVIEW_LIMITS["pc"]["physBoneColliders"]:
            flags.append("pc_collider_over_limit")
        if collision_checks is not None and collision_checks > PERFORMANCE_REVIEW_LIMITS["pc"]["physBoneCollisionCheckCount"]:
            flags.append("pc_collision_check_over_limit")
        if any(token in str(object_path).lower() for token in ("hair", "skirt", "tail", "sleeve", "cloth")):
            flags.append("visual_motion_review")
        components.append(
            {
                "objectPath": _safe_asset_label(object_path),
                "componentTypes": [str(item)[:120] for item in raw_components],
                "affectedTransforms": affected,
                "colliders": colliders,
                "collisionCheckCount": collision_checks,
                "flags": flags,
            }
        )
    unique_components = _unique_by(components, "objectPath")
    metric_rows = [
        _physbone_metric_row("physbone_components", "PhysBone components", _first_numeric(pc, ("physBoneCount", "physBones", "physBoneComponents")) or len(unique_components) or None, "physBoneComponents"),
        _physbone_metric_row("physbone_affected_transforms", "PhysBone affected transforms", _first_numeric(pc, ("physBoneAffectedTransforms", "affectedTransforms")), "physBoneAffectedTransforms"),
        _physbone_metric_row("physbone_colliders", "PhysBone colliders", _first_numeric(pc, ("physBoneColliderCount", "physBoneColliders")), "physBoneColliders"),
        _physbone_metric_row("physbone_collision_checks", "PhysBone collision checks", _first_numeric(pc, ("physBoneCollisionCheckCount", "collisionCheckCount")), "physBoneCollisionCheckCount"),
    ]
    android_metric_rows = [
        _physbone_metric_row("android_physbone_components", "Android PhysBone components", _first_numeric(android, ("physBoneCount", "physBones", "physBoneComponents")), "physBoneComponents", platform="android"),
        _physbone_metric_row("android_physbone_affected_transforms", "Android PhysBone affected transforms", _first_numeric(android, ("physBoneAffectedTransforms", "affectedTransforms")), "physBoneAffectedTransforms", platform="android"),
        _physbone_metric_row("android_physbone_colliders", "Android PhysBone colliders", _first_numeric(android, ("physBoneColliderCount", "physBoneColliders")), "physBoneColliders", platform="android"),
        _physbone_metric_row("android_physbone_collision_checks", "Android PhysBone collision checks", _first_numeric(android, ("physBoneCollisionCheckCount", "collisionCheckCount")), "physBoneCollisionCheckCount", platform="android"),
    ]
    review_rows = [row for row in metric_rows + android_metric_rows if row["status"] in {"review", "offender", "unknown"}]
    return {
        "readOnly": True,
        "limits": {
            "pc": {
                key: PERFORMANCE_REVIEW_LIMITS["pc"][key]
                for key in ("physBoneComponents", "physBoneAffectedTransforms", "physBoneColliders", "physBoneCollisionCheckCount")
            },
            "android": UPLOAD_GATE_LIMITS["android"]["mobileComponentLimits"],
        },
        "summary": {
            "knownComponentCount": len(unique_components),
            "reportedComponentCount": metric_rows[0]["value"],
            "reviewMetricCount": len([row for row in review_rows if row["status"] != "unknown"]),
            "unknownMetricCount": len([row for row in review_rows if row["status"] == "unknown"]),
            "scannerCoverage": "metadata" if unique_components or any(row["value"] is not None for row in metric_rows + android_metric_rows) else "unknown",
        },
        "metrics": metric_rows + android_metric_rows,
        "components": unique_components[:160],
        "notes": ["This audit does not merge, delete, disable, or retarget PhysBone components."],
    }


def build_physbone_reduce_plan(dependency_doctor: dict[str, Any], physbone_audit: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "aao")
    review_metrics = [row for row in physbone_audit.get("metrics") or [] if row.get("status") in {"review", "offender"}]
    candidates = []
    for component in physbone_audit.get("components") or []:
        flags = list(component.get("flags") or [])
        risk = "medium" if flags else "review"
        if "visual_motion_review" in flags:
            risk = "high"
        candidates.append(
            {
                "objectPath": component.get("objectPath"),
                "risk": risk,
                "flags": flags,
                "suggestedAction": "review merge/simplify settings only after visual motion proof",
            }
        )
    return {
        "planOnly": True,
        "dependency": dep,
        "blocked": dep.get("status") != "installed",
        "blockedReason": None if dep.get("status") == "installed" else "AAO / Avatar Optimizer is not detected.",
        "summary": {
            "candidateCount": len(candidates),
            "reviewMetricCount": len(review_metrics),
            "scannerCoverage": physbone_audit.get("summary", {}).get("scannerCoverage"),
        },
        "reviewMetrics": review_metrics,
        "candidates": candidates[:120],
        "writePolicy": "No PhysBone merge/remove writer is exposed in this plan.",
        "requiredProof": [
            "baseline PhysBone metrics",
            "Play Mode motion screenshot/video review",
            "before/after validation delta",
            "checkpoint restore proof",
            "Quest/mobile component limit review when targeting Android",
        ],
    }


def build_aao_hidden_body_cut_plan(dependency_doctor: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "aao")
    avatar_items = _source_payload(_validation_sources(validation), "avatar_items")
    aao_plan = build_aao_trace_plan(dependency_doctor, validation)
    candidates = []
    for item in aao_plan.get("hiddenBodyCutCandidates") or []:
        name = str(item.get("name") or "")
        if name:
            candidates.append(_hidden_body_candidate(name, "aao_trace_hint"))
    for entry in _walk_dicts(avatar_items):
        name = _direct_text(entry, ("gameObjectPath", "objectPath", "rendererPath", "path", "name"))
        if not name:
            continue
        lower = name.lower()
        raw_components = _coerce_list(entry.get("component_types") or entry.get("componentTypes") or entry.get("components"))
        component_text = " ".join(str(item).lower() for item in raw_components)
        if not any(token in lower for token in ("body", "skin", "torso", "chest", "leg", "arm")):
            continue
        if raw_components and "renderer" not in component_text and "mesh" not in component_text:
            continue
        candidates.append(_hidden_body_candidate(name, "avatar_item"))
    unique = _unique_by(candidates, "objectPath")
    return {
        "planOnly": True,
        "dependency": dep,
        "blocked": True,
        "blockedReason": "Hidden body cut remains blocked until occlusion evidence, visual proof, and rollback proof are captured.",
        "applyBlocked": True,
        "applyRequestTool": None,
        "candidateCount": len(unique),
        "candidates": unique[:120],
        "requiredEvidence": [
            "clothing coverage or mask evidence",
            "front/side/back before screenshots",
            "front/side/back after screenshots",
            "gesture and crouch/sit clipping review",
            "validation delta with no new errors",
            "checkpoint rollback proof",
        ],
        "notes": ["This plan does not add AAO Remove Mesh By Mask or Remove Mesh By BlendShape components."],
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
        "applyPolicy": "VRCFury Parameter Compressor and Direct Tree have stable request surfaces, but VRCForge blocks writes until a public, validated VRCFury writer path exists.",
    }


def build_performance_tools_report(dependency_doctor: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    dep = _dependency_by_id(dependency_doctor, "vrc_avatar_performance_tools")
    sources = _validation_sources(validation)
    pc = _source_payload(sources, "performance_pc")
    materials = _source_payload(sources, "materials")
    return {
        "readOnly": True,
        "dependency": dep,
        "present": dep.get("status") == "installed",
        "stableGatewayTool": "vrcforge_scan_thry_avatar_performance",
        "unityMcpTool": "vrc_scan_thry_avatar_performance",
        "callPolicy": "Call read-only Thry VRAM/mesh calculator helpers when the package is installed; never alter texture import settings from this report.",
        "availableFallbacks": ["vrcforge_scan_avatar_performance", "optimization.texture-vram-audit", "optimization.mesh.triangle-audit"],
        "baselineSignals": {
            "pcRank": _performance_headline(pc).get("rank"),
            "textureMemoryBytes": _first_numeric(materials, ("textureMemoryBytes", "vramBytes", "totalTextureBytes", "totalVRAMBytes")),
        },
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
    performance_tools_report: dict[str, Any],
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
            "optimization.lac.apply-request",
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
            "optimization.ttt.atlas-apply-request",
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
            "optimization.aao.trace-apply-request",
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
            "optimization.aao.trace-apply-request",
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
            "optimization.aao.trace-apply-request",
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
            "optimization.ma2bt.convert-apply-request",
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
            "optimization.meshia.simplify-apply-request",
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
            "optimization.vrcfury.parameter-compressor-apply-request",
        ),
        _action_card(
            "run_performance_tools_report",
            "Run performance tools report",
            "Call the read-only VRC Avatar Performance Tools VRAM/mesh calculator when installed.",
            "low",
            "VRC Avatar Performance Tools",
            "0.8.1-beta",
            "read-only",
            None if performance_tools_report.get("present") else "VRC Avatar Performance Tools is not detected.",
            "risk-reduction",
            "Thry's tool provides an independent editor-side VRAM/mesh reference for optimization planning.",
            "Run vrcforge_scan_thry_avatar_performance or optimization.performance-tools.report.",
            [performance_tools_report],
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
        "releaseScope": "0.8.1-beta stable optimization skill surfaces",
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
            "supervisedRequestSupported": True,
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
            statuses.append({"id": name, "ok": bool(source.get("ok")), "status": _validation_source_status(source), "error": source.get("error")})
    return statuses


def _validation_source_status(source: Any) -> str:
    if not isinstance(source, dict):
        return "unavailable"
    if source.get("ok"):
        return "ok"
    error = str(source.get("error") or "").lower()
    if "timeout" in error or "timed out" in error:
        return "timeout"
    if "avatarperformancestats" in error or "performance stats" in error or "sdk type" in error:
        return "missing_sdk_type"
    if "compile" in error or "compilation" in error:
        return "package_compile_blocked"
    if "unsupported" in error or "version" in error:
        return "unsupported_sdk_version"
    return "unavailable"


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


def _first_size_bytes(value: Any, names: tuple[str, ...]) -> int | None:
    wanted = {_normalize_key(name) for name in names}
    for entry in _walk_dicts(value):
        for key, raw in entry.items():
            if _normalize_key(str(key)) not in wanted:
                continue
            parsed = _coerce_size_bytes(raw, str(key))
            if parsed is not None:
                return parsed
    return None


def _coerce_size_bytes(raw: Any, key: str = "") -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    key_lower = key.lower()
    if isinstance(raw, (int, float)):
        value = float(raw)
        if "gb" in key_lower:
            value *= 1024 * MEGABYTE
        elif "mb" in key_lower or "mib" in key_lower:
            value *= MEGABYTE
        elif "kb" in key_lower or "kib" in key_lower:
            value *= 1024
        return int(value)
    if isinstance(raw, str):
        text = raw.strip().lower().replace(",", "")
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*(gib|gb|mib|mb|kib|kb|bytes|byte|b)?", text)
        if not match:
            return None
        value = float(match.group(1))
        unit = match.group(2) or ("mb" if "mb" in key_lower else "b")
        if unit in {"gib", "gb"}:
            value *= 1024 * MEGABYTE
        elif unit in {"mib", "mb"}:
            value *= MEGABYTE
        elif unit in {"kib", "kb"}:
            value *= 1024
        return int(value)
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


def _direct_text(entry: dict[str, Any], names: tuple[str, ...]) -> str | None:
    wanted = {_normalize_key(name) for name in names}
    for key, raw in entry.items():
        if _normalize_key(str(key)) in wanted and raw is not None:
            text = str(raw).strip()
            if text:
                return text
    return None


def _direct_numeric(entry: dict[str, Any], names: tuple[str, ...]) -> int | float | None:
    wanted = {_normalize_key(name) for name in names}
    for key, raw in entry.items():
        if _normalize_key(str(key)) not in wanted or isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            return raw
        if isinstance(raw, str):
            match = re.search(r"-?\d+(?:\.\d+)?", raw.replace(",", ""))
            if match:
                number = float(match.group(0))
                return int(number) if number.is_integer() else number
    return None


def _direct_bool(entry: dict[str, Any], names: tuple[str, ...]) -> bool | None:
    wanted = {_normalize_key(name) for name in names}
    for key, raw in entry.items():
        if _normalize_key(str(key)) not in wanted:
            continue
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        if isinstance(raw, str):
            text = raw.strip().lower()
            if text in {"true", "yes", "1", "on"}:
                return True
            if text in {"false", "no", "0", "off"}:
                return False
    return None


def _dict_list(payload: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    wanted = {_normalize_key(key) for key in keys}
    for entry in _walk_dicts(payload):
        for key, raw in entry.items():
            if _normalize_key(str(key)) not in wanted:
                continue
            for item in _coerce_list(raw):
                if isinstance(item, dict):
                    result.append(item)
    return result


def _sum_direct_numeric(payload: dict[str, Any], names: tuple[str, ...]) -> int | float | None:
    total: int | float = 0
    found = False
    for entry in _walk_dicts(payload):
        value = _direct_numeric(entry, names)
        if value is None:
            continue
        total += value
        found = True
    return total if found else None


def _component_type_count(payload: dict[str, Any], tokens: tuple[str, ...]) -> int | None:
    count = 0
    found = False
    normalized_tokens = tuple(token.replace(" ", "").lower() for token in tokens)
    for entry in _walk_dicts(payload):
        raw = entry.get("component_types")
        if raw is None:
            raw = entry.get("componentTypes")
        components = _coerce_list(raw)
        if not components:
            continue
        found = True
        joined = " ".join(str(item).replace(" ", "").lower() for item in components)
        if any(token in joined for token in normalized_tokens):
            count += 1
    return count if found else None


def _estimate_basic_mesh_count(payload: dict[str, Any]) -> int | None:
    renderer_total = _sum_direct_numeric(payload, ("renderer_count", "rendererCount"))
    skinned = _sum_direct_numeric(payload, ("skinned_renderer_count", "skinnedRendererCount"))
    if renderer_total is None:
        return _component_type_count(payload, ("meshrenderer", "mesh renderer"))
    return max(0, int(renderer_total) - int(skinned or 0))


def _detect_mesh_read_write_disabled(*payloads: dict[str, Any]) -> bool | None:
    saw_payload = False
    for payload in payloads:
        if not payload:
            continue
        saw_payload = True
        text = json.dumps(payload, ensure_ascii=False, default=str).lower()
        if "mesh read/write disabled" in text or "mesh readwrite disabled" in text or "read/write disabled" in text:
            return True
    return None if saw_payload else None


def _physbone_metric_row(
    metric_id: str,
    label: str,
    value: int | float | None,
    limit_key: str,
    *,
    platform: str = "pc",
) -> dict[str, Any]:
    limits = UPLOAD_GATE_LIMITS["android"]["mobileComponentLimits"] if platform == "android" else PERFORMANCE_REVIEW_LIMITS["pc"]
    limit = limits.get(limit_key)
    if value is None or limit is None:
        status = "unknown"
    elif value > limit:
        status = "offender" if platform == "pc" else "review"
    else:
        status = "pass"
    return {
        "id": metric_id,
        "label": label,
        "platform": platform,
        "value": value,
        "limit": limit,
        "status": status,
        "message": _limit_message(label, value, limit or 0, "count", status, "performance_rank_offender"),
    }


def _hidden_body_candidate(name: str, source: str) -> dict[str, Any]:
    label = _safe_asset_label(name)
    lower = label.lower()
    risk = "high"
    if any(token in lower for token in ("inner", "under", "basebody", "base_body", "covered")):
        risk = "medium"
    return {
        "objectPath": label,
        "source": source,
        "risk": risk,
        "status": "blocked_until_visual_evidence",
        "reason": "Body/skin geometry removal can cause clipping, expression, or outfit-state regressions.",
    }


def _parameter_regression_risk_flags(parameter_name: str, control_type: str, usage: dict[str, Any]) -> list[str]:
    text = f"{parameter_name} {control_type}".lower()
    flags = []
    if any(token in text for token in ("puppet", "axis", "joystick", "radial")):
        flags.append("puppet_or_continuous_control")
    if any(token in text for token in ("osc", "face", "tracking", "vrcft", "eye")):
        flags.append("osc_or_face_tracking")
    if usage.get("conditionCount"):
        flags.append("fx_condition")
    if usage.get("menuControlCount"):
        flags.append("menu_control")
    if not flags:
        flags.append("manual_review")
    return flags


def _parameter_expected_probe(control_type: str) -> str:
    lowered = str(control_type or "").lower()
    if "puppet" in lowered or "axis" in lowered or "radial" in lowered:
        return "Exercise the full continuous control range and verify matching animator/material response before and after."
    if "toggle" in lowered or "button" in lowered:
        return "Toggle the control on/off and verify the same object/material/animator state before and after."
    if "sub" in lowered:
        return "Open the submenu and verify child controls still resolve to the same parameters before and after."
    return "Drive the control value and compare visible behavior plus FX state before and after."


def _limit_check(
    check_id: str,
    label: str,
    value: int | float | None,
    limit: int | float,
    unit: str,
    category: str,
    source: str,
) -> dict[str, Any]:
    if value is None:
        status = "unknown"
    elif category == "hard_upload_blocker":
        status = "blocker" if value > limit else "pass"
    else:
        status = "offender" if value > limit else "pass"
    return {
        "id": check_id,
        "label": label,
        "value": value,
        "limit": limit,
        "unit": unit,
        "status": status,
        "category": category,
        "source": source,
        "message": _limit_message(label, value, limit, unit, status, category),
    }


def _limit_message(label: str, value: int | float | None, limit: int | float, unit: str, status: str, category: str) -> str:
    if status == "unknown":
        return f"{label} was not reported by the available scanner source."
    if status == "pass":
        return f"{label} is within the current VRCForge reference limit."
    if category == "hard_upload_blocker":
        return f"{label} exceeds the upload hard gate limit."
    return f"{label} exceeds the current performance-rank review threshold."


def _parameter_entries(parameters: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = _dict_list(parameters, ("parameterNames", "parameters", "items"))
    if not raw_items:
        raw_items = [
            entry
            for entry in _walk_dicts(parameters)
            if isinstance(entry, dict) and _direct_text(entry, ("parameterName", "name", "param"))
        ]
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items:
        name = _direct_text(item, ("parameterName", "name", "param"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        value_type = _normalize_parameter_type(_direct_text(item, ("valueType", "type", "parameterType")))
        explicit_bits = _direct_numeric(item, ("bits", "cost", "syncedBits", "bitCost"))
        network_synced = _direct_bool(item, ("networkSynced", "synced"))
        if network_synced is None:
            network_synced = explicit_bits is not None
        bits = _parameter_bit_cost(value_type, network_synced, explicit_bits)
        entries.append(
            {
                "name": name[:120],
                "type": value_type,
                "networkSynced": network_synced,
                "saved": _direct_bool(item, ("saved",)),
                "defaultValue": _direct_numeric(item, ("defaultValue", "default", "value")),
                "syncedBits": bits,
                "flags": _parameter_flags(name, value_type),
            }
        )
    return entries


def _normalize_parameter_type(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if "bool" in text:
        return "Bool"
    if "float" in text:
        return "Float"
    if "int" in text or "integer" in text:
        return "Int"
    return "unknown"


def _parameter_bit_cost(value_type: str, network_synced: bool | None, explicit_bits: int | float | None) -> int:
    if network_synced is False:
        return 0
    if explicit_bits is not None:
        return int(explicit_bits)
    if value_type == "Bool":
        return 1
    if value_type in {"Int", "Float"}:
        return 8
    return 0


def _parameter_flags(name: str, value_type: str) -> list[str]:
    lower = name.lower()
    flags = []
    if any(token in lower for token in ("osc", "face", "tracking", "vrcft", "eye")):
        flags.append("OSC/face tracking risk")
    if any(token in lower for token in ("puppet", "axis", "joystick", "radial")):
        flags.append("puppet risk")
    if value_type == "Float":
        flags.append("continuous float review")
    return flags


def _parameter_usage_bucket(name: str) -> dict[str, Any]:
    return {
        "parameterName": name,
        "expressionDeclared": False,
        "animatorDeclared": False,
        "animatorType": "unknown",
        "usedByCondition": False,
        "conditionCount": 0,
        "conditionModes": set(),
        "menuControlCount": 0,
        "menuControlTypes": set(),
    }


def _parameter_usage_class(row: dict[str, Any]) -> str:
    if row.get("usedByCondition") and row.get("menuControlCount"):
        return "menu_and_animator"
    if row.get("usedByCondition"):
        return "animator_only"
    if row.get("menuControlCount"):
        return "menu_only"
    if row.get("expressionDeclared"):
        return "declared_only"
    return "unknown"


def _duplicate_parameter_keys(parameters: list[dict[str, Any]]) -> set[str]:
    seen: dict[str, int] = {}
    for parameter in parameters:
        key = re.sub(r"[^a-z0-9]+", "", str(parameter.get("name") or "").lower())
        if not key:
            continue
        seen[key] = seen.get(key, 0) + 1
    return {key for key, count in seen.items() if count > 1}


def _classify_parameter_compressibility(
    parameter: dict[str, Any],
    menu_counts: dict[str, int],
    usage_map: dict[str, dict[str, Any]],
    duplicate_names: set[str],
) -> tuple[str, str]:
    name = str(parameter.get("name") or "")
    lower = name.lower()
    value_type = str(parameter.get("type") or "unknown")
    usage = usage_map.get(name) or {}
    key = re.sub(r"[^a-z0-9]+", "", lower)
    if any(token in lower for token in ("osc", "face", "tracking", "vrcft", "eye")):
        return "danger_osc_or_face_tracking", "OSC, face tracking, and eye tracking parameters are excluded from automatic compression."
    if any(token in lower for token in ("puppet", "axis", "joystick", "radial")) or "puppet" in " ".join(usage.get("menuControlTypes") or []).lower():
        return "danger_puppet", "Puppet and axis-style controls can be continuous behavior and are excluded."
    if value_type == "Float":
        return "danger_continuous_float", "Float parameters may drive continuous real-time behavior."
    if key in duplicate_names:
        return "duplicate_candidate", "Name-normalized duplicate candidate; needs manual behavior review."
    if not usage.get("usedByCondition") and not usage.get("menuControlCount"):
        return "unused_candidate", "No menu control or FX condition evidence was found."
    if value_type == "Bool" and any(token in lower for token in ("toggle", "show", "hide", "enable", "cloth", "outfit", "wardrobe")):
        return "safe_to_pack", "Bool toggle group candidate; only safe after behavior regression."
    if value_type == "Bool":
        return "already_optimal_bool", "Bool already uses one synced bit."
    if value_type == "Int" and menu_counts.get(name, 0) > 1:
        return "safe_to_int_exclusive", "Int parameter with multiple menu controls may already represent an exclusive group."
    return "unknown_do_not_touch", "Insufficient evidence for safe compression."


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
    request_tool: str = "",
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
        "requestTool": request_tool,
        "requestOnly": bool(request_tool),
        "affectedAssetsOrRenderers": affected[:12],
        "directApplyExposed": False,
    }

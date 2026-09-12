from __future__ import annotations

import re
from typing import Any


SHADER_ADAPTER_IDS = ("liltoon", "poiyomi", "generic-semantic")
PRIMARY_AVATAR_ENCRYPTION_ADAPTER_IDS = ("liltoon", "poiyomi")

_SHADER_ADAPTER_DEFINITIONS: dict[str, dict[str, Any]] = {
    "liltoon": {
        "label": "lilToon",
        "knownPackageIds": ["jp.lilxyzw.liltoon"],
        "safeSemanticProperties": [
            "main_color", "main_saturation", "shade_color", "shadow_border", "smoothness",
            "emission_strength", "rendering_mode", "dissolve_mode", "dissolve_shape",
            "dissolve_border", "dissolve_blur", "dissolve_direction_x",
            "dissolve_direction_y", "dissolve_direction_z"
        ],
        "safeSemanticPropertyMetadata": {
            "dissolve_mode": {"type": "enum", "range": [0, 3], "default": 0, "description": "0 disabled, 1 texture mask, 2 UV distance, 3 object-space position"},
            "dissolve_shape": {"type": "enum", "range": [0, 1], "default": 0, "description": "0 radial/distance, 1 directional axis"},
            "dissolve_border": {"type": "float", "range": ["float_min", "float_max"], "default": 0.5, "description": "Dissolve threshold; directional dot distance can be negative and UV/object-space distance can exceed 1"},
            "dissolve_blur": {"type": "float", "range": [0.0001, "float_max"], "default": 0.1, "description": "Positive dissolve edge width; zero is rejected to avoid shader division by zero"},
            "dissolve_direction_x": {"type": "float", "range": [-1.0, 1.0], "default": 0.0, "description": "Object-space dissolve position/direction X component"},
            "dissolve_direction_y": {"type": "float", "range": [-1.0, 1.0], "default": 0.0, "description": "Object-space dissolve position/direction Y component"},
            "dissolve_direction_z": {"type": "float", "range": [-1.0, 1.0], "default": 0.0, "description": "Object-space dissolve position/direction Z component"},
        },
        "dissolveRequirements": {
            "requiredShaderFeature": "LIL_FEATURE_DISSOLVE",
            "supportedRenderingModes": ["Cutout", "Transparent"],
            "opaqueBehavior": "dissolve_is_bypassed",
            "directionNote": "direction components are meaningful only with mode=3 and shape=1; position-space use is shader-defined",
        },
        "blockedProperties": ["raw_property_name", "unknown_texture_slot", "render_queue_without_adapter", "dissolve_raw_property", "dissolve_unsupported_shader"],
        "semanticTuning": True,
        "restoreEncryption": True,
        "proofStatus": "first_class_preview",
    },
    "poiyomi": {
        "label": "Poiyomi",
        "knownPackageIds": ["com.poiyomi.toon"],
        "safeSemanticProperties": ["main_color", "emission_strength", "smoothness", "metallic", "render_queue"],
        "blockedProperties": ["raw_property_name", "shader_feature_toggle_without_adapter", "unknown_keyword"],
        "semanticTuning": True,
        "restoreEncryption": True,
        "proofStatus": "first_class_preview",
    },
    "generic-semantic": {
        "label": "Generic semantic",
        "knownPackageIds": [],
        "safeSemanticProperties": ["main_color", "smoothness", "metallic", "emission_color"],
        "blockedProperties": ["raw_property_name", "shader_specific_keyword", "unsupported_blend_mode"],
        "semanticTuning": True,
        "restoreEncryption": False,
        "proofStatus": "compatibility_only",
    },
    "standard": {
        "label": "Standard/Mobile",
        "knownPackageIds": [],
        "safeSemanticProperties": [],
        "blockedProperties": ["all_writes"],
        "semanticTuning": False,
        "restoreEncryption": False,
        "proofStatus": "blocked",
    },
    "unsupported": {
        "label": "Unsupported",
        "knownPackageIds": [],
        "safeSemanticProperties": [],
        "blockedProperties": ["all_writes"],
        "semanticTuning": False,
        "restoreEncryption": False,
        "proofStatus": "blocked",
    },
}


def normalize_shader_family_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    compact = re.sub(r"[^a-z0-9]+", "", text)
    if "liltoon" in compact or ("lil" in compact and "toon" in compact):
        return "liltoon"
    if "poiyomi" in compact or "poiyomitoon" in compact or "poitoon" in compact:
        return "poiyomi"
    if "generic" in compact:
        return "generic-semantic"
    if "standard" in compact or "vrchatmobile" in compact or "mobile" in compact:
        return "standard"
    return "unsupported"


def classify_shader_adapter(material_name: str, shader_name: str, *, generic_fallback: bool = True) -> dict[str, Any]:
    family = normalize_shader_family_id(f"{material_name} {shader_name}")
    confidence = "high" if family in {"liltoon", "poiyomi"} else "medium"
    if family == "unsupported" and generic_fallback and (material_name or shader_name):
        family = "generic-semantic"
        confidence = "medium" if shader_name else "low"
    elif family in {"standard", "unsupported"}:
        confidence = "low"
    definition = shader_adapter_definition(family, {family})
    return {
        "adapter": family,
        "confidence": confidence,
        "safeSemanticProperties": definition.get("safeSemanticProperties") or [],
        "blockedProperties": definition.get("blockedProperties") or [],
        "semanticTuning": bool(definition.get("semanticTuning")),
        "restoreEncryption": bool(definition.get("restoreEncryption")),
        "proofStatus": definition.get("proofStatus") or "blocked",
    }


def shader_adapter_definition(adapter_id: str, detected_adapters: set[str] | None = None) -> dict[str, Any]:
    normalized = normalize_shader_family_id(adapter_id)
    if adapter_id == "generic-semantic":
        normalized = "generic-semantic"
    detected = detected_adapters or set()
    base = _SHADER_ADAPTER_DEFINITIONS.get(normalized, _SHADER_ADAPTER_DEFINITIONS["unsupported"])
    return {
        "id": normalized,
        **base,
        "detectedInCurrentScan": normalized in detected,
        "applyPolicy": "semantic-allowlist-only" if base.get("semanticTuning") else "blocked",
    }


def shader_family_label(family_id: str) -> str:
    return str(shader_adapter_definition(family_id).get("label") or family_id)

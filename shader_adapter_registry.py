from __future__ import annotations

import re
from typing import Any


SHADER_ADAPTER_IDS = ("liltoon", "poiyomi", "generic-semantic")
PRIMARY_AVATAR_ENCRYPTION_ADAPTER_IDS = ("liltoon", "poiyomi")

_SHADER_ADAPTER_DEFINITIONS: dict[str, dict[str, Any]] = {
    "liltoon": {
        "label": "lilToon",
        "knownPackageIds": ["jp.lilxyzw.liltoon"],
        "safeSemanticProperties": ["main_color", "shade_color", "smoothness", "emission_strength", "rendering_mode"],
        "blockedProperties": ["raw_property_name", "unknown_texture_slot", "render_queue_without_adapter"],
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

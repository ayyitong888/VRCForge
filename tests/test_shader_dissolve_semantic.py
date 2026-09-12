from __future__ import annotations

from pathlib import Path

import dashboard_server
from shader_adapter_registry import shader_adapter_definition


def test_liltoon_dissolve_semantics_are_allowlisted_and_other_families_stay_blocked() -> None:
    definition = shader_adapter_definition("liltoon")
    liltoon = definition["safeSemanticProperties"]
    assert {
        "dissolve_mode",
        "dissolve_shape",
        "dissolve_border",
        "dissolve_blur",
        "dissolve_direction_x",
        "dissolve_direction_y",
        "dissolve_direction_z",
    }.issubset(liltoon)
    assert not any(name.startswith("dissolve_") for name in shader_adapter_definition("poiyomi")["safeSemanticProperties"])
    assert not any(name.startswith("dissolve_") for name in shader_adapter_definition("generic-semantic")["safeSemanticProperties"])
    assert definition["dissolveRequirements"]["requiredShaderFeature"] == "LIL_FEATURE_DISSOLVE"
    assert definition["dissolveRequirements"]["supportedRenderingModes"] == ["Cutout", "Transparent"]
    assert definition["dissolveRequirements"]["opaqueBehavior"] == "dissolve_is_bypassed"


def test_liltoon_dissolve_mapping_is_bounded_to_supported_vector_components() -> None:
    source = Path("Assets/VRCForge/Editor/ShaderMaterialAdapters.cs").read_text(encoding="utf-8-sig")
    liltoon = source.split("public sealed class LilToonShaderAdapter", 1)[1].split(
        "public sealed class PoiyomiShaderAdapter", 1
    )[0]
    assert 'SemanticPropertyMapping.EnumVectorComponent(0f, 3f, 0, "_DissolveParams")' in liltoon
    assert 'SemanticPropertyMapping.EnumVectorComponent(0f, 1f, 1, "_DissolveParams")' in liltoon
    assert 'SemanticPropertyMapping.VectorComponent(float.MinValue, float.MaxValue, 2, "_DissolveParams")' in liltoon
    assert 'SemanticPropertyMapping.VectorComponent(0.0001f, float.MaxValue, 3, "_DissolveParams")' in liltoon
    assert 'SemanticPropertyMapping.VectorComponent(-1f, 1f, 0, "_DissolvePos")' in liltoon
    assert 'SemanticPropertyMapping.VectorComponent(-1f, 1f, 1, "_DissolvePos")' in liltoon
    assert 'SemanticPropertyMapping.VectorComponent(-1f, 1f, 2, "_DissolvePos")' in liltoon
    assert "dissolve_raw_property" in shader_adapter_definition("liltoon")["blockedProperties"]
    assert "dissolve_unsupported_shader" in shader_adapter_definition("liltoon")["blockedProperties"]


def _validation_inventory(shader_family: str = "lilToon") -> dict:
    return {
        "materials": [{
            "material_id": "mat_cloth",
            "material_name": "Cloth",
            "shader_family": shader_family,
            "supported_properties": {
                "dissolve_mode": {"type": "float", "value": 0, "writable": True},
                "dissolve_border": {"type": "float", "value": 0.5, "writable": True},
            },
        }]
    }


def test_dashboard_validation_keeps_a_supported_liltoon_dissolve_change() -> None:
    result = dashboard_server.validate_shader_material_tuning_plan(
        {"changes": [{"material_id": "mat_cloth", "semantic_property": "dissolve_border", "after": -0.25}]},
        _validation_inventory(),
    )
    assert result["skippedChanges"] == []
    assert result["validatedChanges"][0]["after"] == -0.25


def test_dashboard_validation_rejects_fractional_dissolve_enum() -> None:
    result = dashboard_server.validate_shader_material_tuning_plan(
        {"changes": [{"material_id": "mat_cloth", "semantic_property": "dissolve_mode", "after": 1.5}]},
        _validation_inventory(),
    )
    assert len(result["skippedChanges"]) == 1


def test_dashboard_validation_does_not_trust_dissolve_properties_on_non_liltoon() -> None:
    result = dashboard_server.validate_shader_material_tuning_plan(
        {"changes": [{"material_id": "mat_cloth", "semantic_property": "dissolve_border", "after": 0.5}]},
        _validation_inventory("Poiyomi"),
    )
    assert len(result["skippedChanges"]) == 1


def test_material_scanner_projects_visual_readiness_separately_from_has_property() -> None:
    source = Path("Assets/VRCForge/Editor/ShaderMaterialScanner.cs").read_text(encoding="utf-8-sig")
    assert "dissolve_properties_present" in source
    assert '"needs_preparation"' in source
    assert '"needs_feature_verification"' in source
    assert '"Opaque"' in source
    assert '"GemOrFur"' in source
    assert "LIL_FEATURE_DISSOLVE" in Path("shader_adapter_registry.py").read_text(encoding="utf-8")

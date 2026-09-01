from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server
from shader_adapter_registry import shader_adapter_definition


def _inventory() -> dict:
    return {
        "materials": [
            {
                "material_id": "mat_body",
                "material_name": "Manuka Body",
                "shader_family": "lilToon",
                "category": "skin",
                "supported_properties": {
                    "main_saturation": {"type": "float", "value": 1.0, "writable": True},
                },
            }
        ]
    }


def test_main_saturation_is_allowlisted_only_for_the_liltoon_adapter() -> None:
    assert "main_saturation" in dashboard_server.MATERIAL_SEMANTIC_PROPERTIES
    assert "main_saturation" in shader_adapter_definition("liltoon")["safeSemanticProperties"]
    assert "main_saturation" not in shader_adapter_definition("poiyomi")["safeSemanticProperties"]
    assert "main_saturation" not in shader_adapter_definition("generic-semantic")["safeSemanticProperties"]


@pytest.mark.parametrize(("requested", "expected"), [(-0.3, 0.0), (0.25, 0.25), (1.5, 1.0)])
def test_main_saturation_plan_is_validated_and_clamped(requested: float, expected: float) -> None:
    result = dashboard_server.validate_shader_material_tuning_plan(
        plan={
            "changes": [
                {
                    "material_id": "mat_body",
                    "semantic_property": "main_saturation",
                    "after": requested,
                }
            ]
        },
        inventory=_inventory(),
    )

    assert result["skippedChanges"] == []
    assert result["validatedChanges"][0]["before"] == 1.0
    assert result["validatedChanges"][0]["after"] == expected


def test_main_saturation_preview_preserves_the_validated_core_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(
        dashboard_server,
        "scan_shader_materials_direct",
        lambda _settings, _avatar, **_filters: _inventory(),
    )
    monkeypatch.setattr(dashboard_server, "load_shader_tuning_locks", lambda _avatar: {})
    monkeypatch.setattr(dashboard_server, "apply_shader_category_overrides", lambda inventory, _overrides: inventory)

    result = dashboard_server.preview_agent_shader_apply(
        {
            "avatar_path": "Scene/FinalAvatar",
            "changes": [
                {"material_id": "mat_body", "semantic_property": "main_saturation", "after": 0.25}
            ],
        }
    )

    assert result["ok"] is True
    assert result["changeCount"] == 1
    assert result["applyPayload"]["tool"] == "vrc_apply_material_tuning"
    assert result["applyPayload"]["params"]["changes"][0]["semantic_property"] == "main_saturation"
    assert result["applyPayload"]["params"]["changes"][0]["after"] == 0.25


def test_unity_liltoon_adapter_reads_and_writes_only_main_hsvg_y() -> None:
    source = Path("Assets/VRCForge/Editor/ShaderMaterialAdapters.cs").read_text(encoding="utf-8-sig")
    liltoon = source.split("public sealed class LilToonShaderAdapter", 1)[1].split(
        "public sealed class PoiyomiShaderAdapter", 1
    )[0]

    assert '"main_saturation",' in source
    assert (
        '["main_saturation"] = SemanticPropertyMapping.VectorComponent(0f, 1f, 1, "_MainTexHSVG")'
        in liltoon
    )
    assert source.count('["main_saturation"] = SemanticPropertyMapping.VectorComponent') == 1
    assert "material.GetVector(propertyName)[mapping.vectorComponent]" in source
    assert "vector[mapping.vectorComponent] = number;" in source
    assert "material.SetVector(propertyName, vector);" in source

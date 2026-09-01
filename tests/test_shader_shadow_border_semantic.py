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
                "material_id": "mat_face",
                "material_name": "Face",
                "shader_family": "lilToon",
                "category": "skin",
                "supported_properties": {
                    "shadow_border": {"type": "float", "value": 0.5, "writable": True},
                },
            }
        ]
    }


def test_shadow_border_is_allowlisted_only_for_the_liltoon_adapter() -> None:
    assert "shadow_border" in dashboard_server.MATERIAL_SEMANTIC_PROPERTIES
    assert "shadow_border" in shader_adapter_definition("liltoon")["safeSemanticProperties"]
    assert "shadow_border" not in shader_adapter_definition("poiyomi")["safeSemanticProperties"]
    assert "shadow_border" not in shader_adapter_definition("generic-semantic")["safeSemanticProperties"]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(-0.2, 0.0), (0.117, 0.117), (1.2, 1.0)],
)
def test_shadow_border_plan_is_validated_and_clamped(requested: float, expected: float) -> None:
    result = dashboard_server.validate_shader_material_tuning_plan(
        plan={
            "changes": [
                {
                    "material_id": "mat_face",
                    "semantic_property": "shadow_border",
                    "after": requested,
                }
            ]
        },
        inventory=_inventory(),
    )

    assert result["skippedChanges"] == []
    assert result["validatedChanges"][0]["before"] == 0.5
    assert result["validatedChanges"][0]["after"] == expected


def test_shadow_border_preview_preserves_the_validated_core_change(monkeypatch: pytest.MonkeyPatch) -> None:
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
                {"material_id": "mat_face", "semantic_property": "shadow_border", "after": 0.117}
            ],
        }
    )

    assert result["ok"] is True
    assert result["changeCount"] == 1
    assert result["applyPayload"]["tool"] == "vrc_apply_material_tuning"
    assert result["applyPayload"]["params"]["changes"][0]["semantic_property"] == "shadow_border"
    assert result["applyPayload"]["params"]["changes"][0]["after"] == 0.117


def test_unity_liltoon_adapter_maps_shadow_border_without_replacing_softness() -> None:
    source = Path("Assets/VRCForge/Editor/ShaderMaterialAdapters.cs").read_text(encoding="utf-8-sig")
    liltoon = source.split("public sealed class LilToonShaderAdapter", 1)[1].split(
        "public sealed class PoiyomiShaderAdapter", 1
    )[0]

    assert '"shadow_border",' in source
    assert '["shadow_border"] = SemanticPropertyMapping.Float(0f, 1f, "_ShadowBorder")' in liltoon
    assert '["shadow_softness"] = SemanticPropertyMapping.Float(0f, 1f, "_ShadowBlur", "_ShadowBorder")' in liltoon
    assert source.count('["shadow_border"] = SemanticPropertyMapping.Float') == 1

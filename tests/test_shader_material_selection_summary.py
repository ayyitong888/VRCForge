import json
from pathlib import Path

import dashboard_server


def test_selected_material_scan_summary_is_scoped_and_keeps_global_source_summary(monkeypatch):
    fixture = json.loads(
        Path("tests/fixtures/scan_materials_scope_880.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _: type("Settings", (), {"unity_mcp_timeout_seconds": 30})(),
    )
    monkeypatch.setattr(
        dashboard_server,
        "scan_shader_materials_direct",
        lambda *_args, **_kwargs: fixture,
    )
    request = dashboard_server.ShaderMaterialScanRequest(
        avatarPath="CyberCritter", materialIds=["mat-shirt", "mat-skirt"]
    )

    result = dashboard_server.scan_shader_materials_sync(request)

    assert result["summary"] == {
        "avatarCount": 1,
        "rendererCount": 2,
        "materialCount": 3,
        "lilToonCount": 3,
        "poiyomiCount": 0,
        "genericCount": 0,
        "unsupportedCount": 0,
    }
    assert result["sourceSummary"] == fixture["summary"]
    assert result["sourceSummary"]["rendererCount"] == 207
    assert result["sourceSummary"]["materialCount"] == 51  # raw mixed-scope report, not complete-avatar material count
    assert result["sourceSummaryScope"] == "unity_reported_mixed"
    assert result["summaryScope"] == "material_selection"
    assert result["inventory"]["summary"] == result["summary"]
    assert result["inventory"]["sourceSummary"] == fixture["summary"]
    assert result["inventory"]["sourceSummaryScope"] == "unity_reported_mixed"


def test_selected_material_scan_counts_core_shader_family_names(monkeypatch):
    fixture = json.loads(
        Path("tests/fixtures/scan_materials_scope_880.json").read_text(encoding="utf-8")
    )
    fixture = {
        **fixture,
        "materials": [
            *fixture["materials"],
            {"material_id": "mat-poiyomi", "renderer_path": "CyberCritter/Body/Shirt", "shader_family": "Poiyomi"},
            {"material_id": "mat-generic", "renderer_path": "CyberCritter/Body/Skirt", "shader_family": "Generic"},
            {"material_id": "mat-unsupported", "renderer_path": "CyberCritter/Body/Skirt", "shader_family": "Unsupported"},
        ],
    }
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _: type("Settings", (), {"unity_mcp_timeout_seconds": 30})())
    monkeypatch.setattr(dashboard_server, "scan_shader_materials_direct", lambda *_args, **_kwargs: fixture)
    result = dashboard_server.scan_shader_materials_sync(
        dashboard_server.ShaderMaterialScanRequest(avatarPath="CyberCritter", materialIds=["selected"])
    )
    assert result["summary"]["rendererCount"] == 2
    assert result["summary"]["materialCount"] == 6
    assert result["summary"]["poiyomiCount"] == 1
    assert result["summary"]["genericCount"] == 1
    assert result["summary"]["unsupportedCount"] == 1

from types import SimpleNamespace

import dashboard_server


def test_shader_material_scan_request_preserves_public_scan_parameters(monkeypatch):
    request = dashboard_server.ShaderMaterialScanRequest(
        avatarPath="Avatar",
        materialIds=["mat-a"],
        includeTextures=False,
        categoryOverrides={"mat-a": "hair"},
    )
    seen = {}

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _: SimpleNamespace(unity_mcp_timeout_seconds=30))

    def scan(_settings, _avatar, *, material_ids=None, include_textures=True):
        seen.update(material_ids=material_ids, include_textures=include_textures)
        return {"materials": [{"material_id": "mat-a", "category": "unknown"}], "summary": {}}

    monkeypatch.setattr(dashboard_server, "scan_shader_materials_direct", scan)
    monkeypatch.setattr(dashboard_server, "find_ambiguous_shader_material_ids", lambda _: set())
    result = dashboard_server.scan_shader_materials_sync(request)

    assert seen == {"material_ids": ["mat-a"], "include_textures": False}
    assert result["materials"][0]["category"] == "hair"


def test_shader_material_scan_request_accepts_snake_case_aliases():
    request = dashboard_server.ShaderMaterialScanRequest(
        avatar_path="Avatar",
        material_ids=["mat-a"],
        include_textures=False,
        category_overrides={"mat-a": "hair"},
    )
    assert request.material_ids == ["mat-a"]
    assert request.include_textures is False

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import dashboard_server


def test_direct_material_scan_can_bound_response_to_exact_ids(monkeypatch, tmp_path) -> None:
    seen: dict[str, object] = {}
    settings = SimpleNamespace(unity_mcp_timeout_seconds=30)

    monkeypatch.setattr(
        dashboard_server,
        "build_dashboard_artifact_path",
        lambda *_args: tmp_path / "inventory.json",
    )

    def invoke(_settings, tool_name, arguments):
        seen.update({"tool": tool_name, "arguments": arguments})
        return {"ok": True, "payload": {"materials": []}}

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    monkeypatch.setattr(
        dashboard_server,
        "extract_tool_result_payload",
        lambda result: result["payload"],
    )
    monkeypatch.setattr(dashboard_server, "write_dashboard_json_artifact", lambda *_args: None)

    result = dashboard_server.scan_shader_materials_direct(
        settings,
        "Scene/Avatar",
        material_ids=["mat_skin"],
        include_textures=False,
    )

    assert result == {"materials": []}
    assert seen == {
        "tool": "vrc_scan_avatar_materials",
        "arguments": {
            "avatarPath": "Scene/Avatar",
            "outputPath": "",
            "refreshAssets": False,
            "materialIds": ["mat_skin"],
            "includeTextures": False,
        },
    }
    assert settings.unity_mcp_timeout_seconds == 30


def test_unity_scanner_filters_before_serializing_heavy_dependencies() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "ShaderMaterialScanner.cs"
    ).read_text(encoding="utf-8")

    filter_index = source.index("selectedMaterialIds.Contains(materialId)")
    texture_index = source.index("ReadTextureDependencies(material)", filter_index)
    assert filter_index < texture_index
    assert "parameters.includeTextures ?? true" in source


def test_material_scan_and_apply_share_exact_subtree_scope_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    scanner = (root / "Assets" / "VRCForge" / "Editor" / "ShaderMaterialScanner.cs").read_text(
        encoding="utf-8"
    )
    applier = (root / "Assets" / "VRCForge" / "Editor" / "MaterialTuningApplier.cs").read_text(
        encoding="utf-8"
    )

    assert "var scopeIsAvatarRoot = ReferenceEquals(FindAvatarRoot(avatarRoot), avatarRoot);" in scanner
    assert "!scopeIsAvatarRoot || ReferenceEquals(FindAvatarRoot(renderer.transform), avatarRoot)" in scanner
    assert "NormalizePath(GetTransformPath(transform))" in scanner
    assert "var exactScopes = ResolveExactScopes(normalizedAvatarPath);" in applier
    assert "renderer.transform.IsChildOf(scope)" in applier

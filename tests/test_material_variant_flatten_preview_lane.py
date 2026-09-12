from pathlib import Path


def test_registered_flatten_has_an_explicit_core_preview_lane():
    root = Path(__file__).resolve().parents[1]
    server = (root / "Assets/VRCForge/Editor/MCP/VRCForgeMcpCoreServer.cs").read_text("utf-8")
    preview_set = server.split("HashSet<string> PreviewTools", 1)[1].split("};", 1)[0]
    assert '"vrc_flatten_material_variant"' in preview_set
    assert "PreviewTools.Contains(toolName) && HasExplicitPreviewRequest(arguments)" in server

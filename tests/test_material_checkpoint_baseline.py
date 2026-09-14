from copy import deepcopy
from types import SimpleNamespace
import pytest
import dashboard_server as server

@pytest.mark.parametrize("valid", [True, False])
def test_single_material_checkpoint_captures_readonly_baseline(tmp_path, monkeypatch, valid):
    path = "Assets/Clothes.mat"
    arguments = {"projectPath": str(tmp_path), "toolName": server.MATERIAL_TEXTURE_ASSIGNMENT_TOOL,
                 "arguments": {"materialAssetPath": path, "propertyName": "_MainTex"}}
    monkeypatch.setattr(server, "prepare_unity_mcp_write_request", lambda args, preview: (deepcopy(args), {}))
    monkeypatch.setattr(server, "load_dashboard_settings", lambda *_: SimpleNamespace())
    monkeypatch.setattr(server, "build_agent_connection_request", lambda *_: {})
    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", lambda *_: pytest.fail("must not save unrelated dirty assets"))
    calls = []
    monkeypatch.setattr(server, "invoke_unity_mcp", lambda settings, tool, params, **kw: calls.append((tool, params, kw)) or object())
    baseline = [{"assetPath": path if valid else "Assets/Other.mat", "assetGuid": "a" * 32, "serializedState": {}}]
    monkeypatch.setattr(server, "normalize_unity_checkpoint_result", lambda *_: {"ok": True, "assetBaseline": baseline})
    result = server.prepare_authoritative_unity_checkpoint_sync(tmp_path, arguments)
    assert len(calls) == 1
    assert calls[0][0] == "vrc_prepare_checkpoint"
    assert calls[0][1] == {"projectPath": str(tmp_path), "checkpointAssetPaths": [path], "materialBaselineOnly": True}
    assert result["ok"] is valid
    assert result.get("assetBaselineRequired") is (True if valid else None)

@pytest.mark.parametrize("tool", [server.MATERIAL_TEXTURE_ASSIGNMENT_TOOL, server.MATERIAL_SHADER_ASSIGNMENT_TOOL])
@pytest.mark.parametrize("mode", ["batch128", "duplicates", "renderer", "missing", "traversal"])
def test_material_checkpoint_exact_footprint(tmp_path, monkeypatch, tool, mode):
    expected = [f"Assets/M{i:03}.mat" for i in range(128)] if mode == "batch128" else ["Assets/One.mat"]
    nested = {"assignments": [{"materialAssetPath": p} for p in expected]}
    if mode == "duplicates": nested["assignments"] *= 3
    if mode == "renderer": nested = {"rendererPath": "Avatar/Clothes", "expectedMaterialAssetPath": expected[0]}
    if mode == "missing": nested = {"rendererPath": "Avatar/Clothes"}
    if mode == "traversal": nested = {"materialAssetPath": "Assets/../One.mat"}
    args = {"projectPath": str(tmp_path), "toolName": tool, "arguments": nested}
    monkeypatch.setattr(server, "prepare_unity_mcp_write_request", lambda args, preview: (deepcopy(args), {}))
    monkeypatch.setattr(server, "load_dashboard_settings", lambda *_: object())
    monkeypatch.setattr(server, "build_agent_connection_request", lambda *_: {})
    calls = []
    monkeypatch.setattr(server, "invoke_unity_mcp", lambda settings, tool, params, **kw: calls.append(params) or object())
    monkeypatch.setattr(server, "normalize_unity_checkpoint_result", lambda *_: {"ok": True, "assetBaseline": [{"assetPath": p} for p in expected]})
    result = server.prepare_authoritative_unity_checkpoint_sync(tmp_path, args)
    if mode in {"missing", "traversal"}:
        assert result["ok"] is False
        assert calls == []
    else:
        assert result["assetBaselineRequired"] is True
        assert calls[0]["checkpointAssetPaths"] == expected

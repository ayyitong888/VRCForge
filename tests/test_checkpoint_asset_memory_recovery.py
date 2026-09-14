"""Production checkpoint service and registered pre-write preparation, with Core I/O replaced."""
import pytest
from test_agent_checkpoint_recovery_service import _gateway


@pytest.mark.parametrize("mode", ["verified", "missing", "mismatch", "legacy"])
def test_zero_disk_diff_needs_exact_asset_reload_receipt(tmp_path, monkeypatch, mode):
    gateway = _gateway(tmp_path)
    service = gateway.checkpoint_recovery
    baseline = [{"assetPath": "Assets/Menu.asset", "assetGuid": "a" * 32, "serializedState": {"name": "Menu"}}]
    checkpoint = {"id": "memory", "strategy": "archive", "projectRoot": str(tmp_path),
                  "targetTool": "vrcforge_manage_expression_menu", "unityPrepare": {
                      "assetBaselineRequired": True, "assetBaseline": baseline}}
    if mode == "legacy": checkpoint["unityPrepare"] = {}
    cls = type(service)
    monkeypatch.setattr(cls, "_load_checkpoint", lambda *_: checkpoint)
    monkeypatch.setattr(cls, "_checkpoint_available", lambda *_: {"ok": True})
    monkeypatch.setattr(cls, "_restore_archive_checkpoint", lambda *_: {"ok": True, "restoredFiles": [], "deletedFiles": []})
    monkeypatch.setattr(cls, "_cleanup_checkpoint_restore_unity_caches", lambda *_: {})
    monkeypatch.setattr(cls, "_build_checkpoint_rollback_coverage_audit", lambda *a, **kw: {"ok": True})
    service.checkpoint_restore_prepare_handler = lambda _: {"ok": True, "scenes": []}
    seen = []
    def reload(_, context):
        seen.append(context)
        if mode == "missing": return {"ok": True}
        return {"ok": True, "assetBaselineVerified": True, "assetReadback": [
            {"assetPath": "Assets/Other.asset" if mode == "mismatch" else "Assets/Menu.asset",
             "assetGuid": "a" * 32, "fileDigest": "b" * 64, "verified": True}]}
    service.checkpoint_restore_handler = reload
    resolved = []
    monkeypatch.setattr(type(gateway.approval_transactions), "_resolve_apply_recoveries_for_checkpoint",
                        lambda *a, **kw: resolved.append(True) or [])
    result = service.restore_checkpoint({"checkpointId": "memory", "confirmRestore": True})
    assert result["ok"] is (mode == "verified")
    assert bool(resolved) is (mode == "verified")
    if mode != "legacy": assert seen[0]["assetBaseline"] == baseline
    if mode != "verified": assert result["checkpointRecoveryRequired"] is True


def test_registered_menu_checkpoint_uses_preview_exact_paths(tmp_path, monkeypatch):
    import dashboard_server as server
    handler = server.AGENT_GATEWAY._write_handlers["vrcforge_manage_expression_menu"]
    assert handler.checkpoint_prepare_handler is server.prepare_authoritative_unity_checkpoint_sync
    seen = []
    monkeypatch.setattr(server, "manage_expression_menu_sync", lambda arguments, preview: {
        "ok": True, "plan": {"checkpointAssetPaths": ["Assets/ExactSubmenu.asset"]}})
    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", lambda root, paths: seen.append(paths) or {"ok": True, "assetBaseline": [{"assetPath": "Assets/ExactSubmenu.asset"}]})
    result = handler.checkpoint_prepare_handler(tmp_path, {"_checkpointTargetTool": "vrcforge_manage_expression_menu", "menuPath": "logical/name"})
    assert seen == [["Assets/ExactSubmenu.asset"]]
    assert result["assetBaselineRequired"] is True


def test_menu_checkpoint_refuses_missing_preview_footprint(tmp_path, monkeypatch):
    import dashboard_server as server
    monkeypatch.setattr(server, "manage_expression_menu_sync", lambda *a, **kw: {"ok": True, "plan": {"rootMenuPath": "Assets/Root.asset"}})
    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", lambda *a: pytest.fail("must not guess the target from root or logical path"))
    assert server.prepare_authoritative_unity_checkpoint_sync(tmp_path, {"_checkpointTargetTool": "vrcforge_manage_expression_menu"})["ok"] is False

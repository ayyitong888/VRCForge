from agent_scan_scope_budget import ScopeCaptureBudget


def test_failed_full_inventory_does_not_suppress_a_different_exact_material_scope(tmp_path):
    budget = ScopeCaptureBudget(tmp_path / "budget.sqlite3")
    info = {"instanceId": "core", "captureRootIdentity": ["avatar", "scene", "root"]}
    full = {"scanTool": "vrcforge_scan_avatar_materials", "arguments": {"avatarPath": "Avatar"}}
    selected = {"scanTool": "vrcforge_scan_avatar_materials", "arguments": {
        "avatarPath": "Avatar", "materialIds": ["mat_body"], "includeTextures": True}}
    calls = []

    def fail():
        calls.append("full")
        return {"verified": False, "reason": "unity_core_timeout"}

    def succeed():
        calls.append("selected")
        return {"verified": True}

    budget.capture(tmp_path, info, full, fail)
    assert budget.capture(tmp_path, info, selected, succeed)["verified"] is True
    assert budget.capture(tmp_path, info, full, fail)["reason"] == "capture_suppressed:unity_core_timeout"
    assert calls == ["full", "selected"]

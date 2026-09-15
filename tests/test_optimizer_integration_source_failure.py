"""Integration planners must preserve failed scans at registered entry points."""
from dataclasses import replace

import pytest

import dashboard_server as dashboard


@pytest.mark.parametrize("suffix,source", [
    ("ttt_atlas_plan", "materials"),
    ("aao_trace_plan", "avatar_items"),
    ("aao_hidden_body_cut_plan", "avatar_items"),
    ("vrcfury_compatibility_report", "fx"),
    ("ma_responsive_layer_audit", "fx"),
    ("ma2bt_convertibility_plan", "fx"),
    ("ma2bt_skipped_reasons", "fx"),
    ("performance_tools_report", "performance_pc"),
    ("performance_tools_report", "materials"),
])
def test_integration_plan_propagates_failed_dependency(monkeypatch, suffix, source):
    validation = {"sources": {source: {"ok": False, "error": "Avatar descriptor not found"}}}
    monkeypatch.setattr(
        dashboard.OPTIMIZATION, "_ports",
        replace(dashboard.OPTIMIZATION._ports, build_validation_report=lambda _: validation),
    )
    result = dashboard.AGENT_GATEWAY._tools["vrcforge_optimization_" + suffix].handler({})
    assert result["ok"] is False
    assert result["error"]["source"] == source
    assert result["error"]["message"] == "Avatar descriptor not found"
    assert "result" not in result

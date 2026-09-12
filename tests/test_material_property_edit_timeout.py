"""Scalar writes retain their checks within a bounded, single transport attempt."""
from types import SimpleNamespace

import pytest

import dashboard_server as dashboard


@pytest.mark.parametrize("tool_name,arguments,configured,expected", [
    ("vrc_set_material_shader", {"propertyChanges": [{"propertyName": "_Cutoff", "value": 0.1}]}, 30, 120),
    ("vrc_set_material_shader", {"propertyChanges": [{"propertyName": "_Cutoff", "value": 0.1}]}, 180, 180),
    ("vrc_set_material_shader", {"shaderName": "Standard"}, 30, 30),
    ("vrc_set_material_shader", {"assignments": [{"shaderName": "Standard"}]}, 30, 30),
    ("vrc_set_material_shader", {"keywordChanges": [{"keyword": "X", "enabled": True}]}, 30, 30),
    ("vrc_set_material_texture", {"propertyChanges": []}, 30, 30),
])
def test_scalar_write_budget_preserves_other_modes_and_never_replays_timeout(monkeypatch, tool_name, arguments, configured, expected):
    settings = SimpleNamespace(unity_mcp_timeout_seconds=configured)
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _: settings)
    monkeypatch.setattr(dashboard, "build_agent_connection_request", lambda value: value)
    monkeypatch.setattr(dashboard, "authoritative_unity_write_has_strict_result", lambda _: True)
    calls = []
    failure = TimeoutError("Core response unknown after single dispatch")

    def invoke(actual_settings, actual_tool, actual_args, **kwargs):
        calls.append((actual_settings.unity_mcp_timeout_seconds, actual_tool, actual_args, kwargs))
        raise failure

    monkeypatch.setattr(dashboard, "invoke_unity_mcp", invoke)
    with pytest.raises(TimeoutError) as raised:
        dashboard.unity_mcp_write_sync({"toolName": tool_name, "arguments": arguments})
    assert raised.value is failure
    assert calls == [(expected, tool_name, arguments, {"preserve_tool_error": True})]
    assert settings.unity_mcp_timeout_seconds == configured

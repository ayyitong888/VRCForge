import pytest

from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS


@pytest.mark.parametrize("explicit", [None, False, True])
def test_binding_detail_schema_matches_gateway_default_and_explicit_values(monkeypatch, explicit):
    import dashboard_server as dashboard

    captured = []
    monkeypatch.setattr(dashboard, "run_unity_artifact_scan_sync",
                        lambda *args, **kwargs: captured.append(args[3]) or {"ok": True})
    arguments = {"clipPaths": ["Assets/Fixture.anim"]}
    if explicit is not None:
        arguments["includeBindingDetails"] = explicit
    dashboard.scan_animation_bindings_sync(arguments)
    declared = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_scan_animation_bindings"]["properties"]["includeBindingDetails"]
    assert captured[0]["includeBindingDetails"] is (declared["default"] if explicit is None else explicit)

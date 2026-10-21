"""Mixed Core reads must not consume an approved write's exact capability."""
from __future__ import annotations

import pytest

import vrchat_blendshape_agent as agent
from approved_unity_execution import bind_approved_unity_execution, create_approved_unity_execution_plan
from test_approved_unity_execution import _context, _descriptor, _settings


READ_CASES = [
    ("vrc_export_blendshapes", {"outputPath": "", "refreshAssets": False, "returnPayloadOnly": True}),
    *[(name, {"avatarPath": "Avatar", "outputPath": ""}) for name in (
        "vrc_scan_avatar_controls", "vrc_scan_avatar_parameters", "vrc_scan_wardrobe", "vrc_scan_thry_avatar_performance")],
    ("vrc_scan_avatar_materials", {"avatarPath": "Avatar", "outputPath": "", "refreshAssets": False}),
    ("vrc_scan_avatar_materials", {"avatarPath": "Avatar", "outputPath": "", "refreshAssets": False, "materialIds": ["mat"], "includeTextures": True}),
    ("vrc_scan_avatar_items", {"avatarPath": "Avatar", "outputPath": "", "refreshAssets": False, "maxItems": 20, "offset": 0}),
    ("vrc_scan_fx_animator", {"avatarPath": "Avatar", "outputPath": "", "refreshAssets": False, "controllerPath": ""}),
    ("vrc_scan_animation_bindings", {"avatarPath": "Avatar", "outputPath": "", "controllerPath": "", "clipPaths": [], "includeAllProjectClips": False, "includeBindingDetails": True, "maxClips": 30, "refreshAssets": False}),
    ("vrc_scan_avatar_performance", {"avatarPath": "Avatar", "outputPath": "", "isMobile": False}),
    ("vrc_capture_scene_view", {"statusOnly": True, "requirePlayMode": False}),
]


@pytest.mark.parametrize("name,arguments", READ_CASES)
def test_payload_read_preserves_exact_pending_write(tmp_path, monkeypatch, name, arguments):
    _descriptor(tmp_path)
    observed = []

    class Core:
        def __init__(self, *_args, **_kwargs):
            pass

        def call_tool(self, tool, params, *, execution_context=None):
            observed.append((tool, params, execution_context))
            return {"isError": False, "content": [], "structuredContent": {"avatars": []}}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", Core)
    settings = _settings(tmp_path)
    settings.export_tool_name = "vrc_export_blendshapes"
    write = {"avatarPath": "Avatar", "saveAssets": True, "adjustments": []}
    plan = create_approved_unity_execution_plan(
        _context(tmp_path, issued=0, expires=9_999_999_999_999), [("vrc_apply_blendshapes", write)])
    with bind_approved_unity_execution(plan):
        # Use the actual live-context export entrypoint for the reported failure.
        if name == "vrc_export_blendshapes":
            assert agent.export_blendshapes(settings) == {"avatars": []}
        else:
            agent.invoke_unity_mcp(settings, name, arguments)
        assert not plan.consumed
        agent.invoke_unity_mcp(settings, "vrc_apply_blendshapes", write)
        assert plan.consumed
        agent.invoke_unity_mcp(settings, name, arguments)  # post-write readback
    assert observed[0] == (name, arguments, None)
    assert observed[1][2]["unityToolName"] == "vrc_apply_blendshapes"
    assert observed[2] == (name, arguments, None)


@pytest.mark.parametrize("name,arguments", READ_CASES)
def test_output_or_unknown_fields_still_require_exact_write_claim(tmp_path, monkeypatch, name, arguments):
    _descriptor(tmp_path)
    monkeypatch.setattr(agent, "UnityMcpCoreClient", lambda *_a, **_k: pytest.fail("unexpected Core transport"))
    plan = create_approved_unity_execution_plan(
        _context(tmp_path, issued=0, expires=9_999_999_999_999), [("vrc_apply_blendshapes", {})])
    with bind_approved_unity_execution(plan):
        for changed in ({**arguments, "outputPath": "Assets/output.json"}, {**arguments, "extra": False}):
            with pytest.raises(agent.UnityMcpError, match="does not match the frozen plan"):
                agent.invoke_unity_mcp(_settings(tmp_path), name, changed)
    assert not plan.consumed


def test_explicitly_frozen_read_keeps_order_and_exact_arguments(tmp_path, monkeypatch):
    _descriptor(tmp_path)
    observed = []

    class Core:
        def __init__(self, *_a, **_k):
            pass

        def call_tool(self, name, arguments, *, execution_context=None):
            observed.append((name, arguments, execution_context))
            return {"isError": False, "content": []}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", Core)
    scan = {"avatarPath": "Avatar", "outputPath": "", "refreshAssets": False}
    write = {"avatarPath": "Avatar", "saveAssets": True, "changes": []}
    plan = create_approved_unity_execution_plan(
        _context(tmp_path, issued=0, expires=9_999_999_999_999),
        [("vrc_scan_avatar_materials", scan), ("vrc_apply_material_tuning", write)])
    with bind_approved_unity_execution(plan):
        with pytest.raises(agent.UnityMcpError, match="arguments do not match"):
            agent.invoke_unity_mcp(_settings(tmp_path), "vrc_scan_avatar_materials", {**scan, "avatarPath": "Other"})
        assert observed == []
        agent.invoke_unity_mcp(_settings(tmp_path), "vrc_scan_avatar_materials", scan)
        agent.invoke_unity_mcp(_settings(tmp_path), "vrc_apply_material_tuning", write)
    assert plan.consumed
    assert [call[2]["unityToolName"] for call in observed] == ["vrc_scan_avatar_materials", "vrc_apply_material_tuning"]


@pytest.mark.parametrize("drift", [False, True])
def test_face_handler_live_export_then_exact_write_and_readback(tmp_path, monkeypatch, drift):
    from dataclasses import replace
    from prepared_unity_execution import build_prepared_execution_plan
    from test_avatar_tuning_state_service import _prepared_service

    _descriptor(tmp_path)
    service, _stores, _undo, live = _prepared_service(tmp_path)
    settings = _settings(tmp_path)
    settings.export_tool_name = "vrc_export_blendshapes"
    original = service._ports
    observed = []

    class Core:
        def __init__(self, *_a, **_k):
            pass

        def call_tool(self, name, arguments, *, execution_context=None):
            observed.append((name, execution_context))
            return {"isError": False, "content": [], "structuredContent": {"avatars": []}}

    monkeypatch.setattr(agent, "UnityMcpCoreClient", Core)

    def context(arguments, hint):
        agent.export_blendshapes(settings)
        return replace(original.resolve_live_context(arguments, hint), settings=settings)

    def verify(ctx, changes):
        agent.export_blendshapes(settings)
        return original.verify_live_changes(ctx, changes)

    service._ports = replace(original, resolve_live_context=context, invoke_unity=agent.invoke_unity_mcp,
                             serialize_result=lambda result: {"exitCode": result.exit_code}, verify_live_changes=verify)
    prepared, _ = service.prepare_face_tuning({"avatar": "Avatar/Path", "instruction": "test", "mock_execute": False}, None)
    plan = create_approved_unity_execution_plan(_context(tmp_path, issued=0, expires=9_999_999_999_999), build_prepared_execution_plan(prepared))
    if drift:
        live["weight"] = 12.0
    with bind_approved_unity_execution(plan):
        if drift:
            with pytest.raises(RuntimeError, match="target values"):
                service.execute_face_tuning(prepared)
            assert [name for name, _ in observed] == ["vrc_export_blendshapes"]
            assert not plan.consumed
        else:
            result = service.execute_face_tuning(prepared)
            assert result["ok"] is True and result["readbackVerified"] is True
            assert plan.consumed
            assert [name for name, _ in observed] == ["vrc_export_blendshapes", "vrc_apply_blendshapes", "vrc_export_blendshapes"]
            assert observed[0][1] is None and observed[2][1] is None

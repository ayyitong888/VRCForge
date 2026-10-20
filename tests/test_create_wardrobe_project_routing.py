from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from wardrobe_outfit_workflow_service import (
    CreateWardrobePreviewPorts,
    CreateWardrobePreviewService,
    build_create_wardrobe_core_calls,
    build_create_wardrobe_request,
)


@pytest.mark.parametrize("project_key", ["projectPath", "project_path", "projectRoot", "project_root"])
@pytest.mark.parametrize("preview", [True, False])
def test_create_preview_routes_every_primitive_to_explicit_project(monkeypatch, tmp_path, project_key, preview):
    import dashboard_server as dashboard

    selected = str(tmp_path / "selected-other")
    requested = str(tmp_path / "requested")
    arguments = {project_key: requested, "avatarPath": "TestAvatar", "parameterName": "Clothes"}
    _assert_preview_route(monkeypatch, dashboard, selected, requested, arguments, preview=preview)


def _assert_preview_route(monkeypatch, dashboard, selected, requested, arguments, *, preview=True):
    monkeypatch.setattr(dashboard.DASHBOARD_STATE, "selected_project_path", selected)
    settings = SimpleNamespace(unity_mcp_host="127.0.0.1", unity_mcp_port=0,
                               unity_mcp_instance="", unity_project_path=selected)
    monkeypatch.setattr(dashboard, "load_runtime_settings_safely", lambda *a, **kw: settings)
    monkeypatch.setattr(dashboard.PROVIDER_CONFIGURATION, "serialize_api_config", lambda **kw: {})
    routed = []

    def invoke(actual_settings, tool, core_arguments, **kwargs):
        routed.append((actual_settings.unity_project_path, tool, dict(core_arguments)))
        return {"ok": actual_settings.unity_project_path == requested,
                "verified": True, "persistedReadback": True, "committed": True,
                "commitState": "committed", "readback": {"tool": tool}}

    monkeypatch.setattr(dashboard, "invoke_unity_mcp", invoke)
    monkeypatch.setattr(dashboard, "extract_tool_result_payload", lambda value: value)
    result = (dashboard.CREATE_WARDROBE_PREVIEW.preview(arguments) if preview else
              dashboard.CREATE_WARDROBE_APPROVED_WRITE.execute(arguments))
    assert result["ok"] is True
    assert len(routed) == 3
    assert all(project == requested for project, _, _ in routed)
    # Connection selectors must not change the exact approved Core plan/digest.
    assert [(tool, args) for _, tool, args in routed] == build_create_wardrobe_core_calls(arguments, preview)


def test_actual_wire_create_preview_routes_explicit_project(monkeypatch):
    fixture = os.environ.get("VRCFORGE_WARDROBE_WIRE_FIXTURE")
    if not fixture:
        pytest.skip("optional private real-wire replay fixture")
    import dashboard_server as dashboard

    arguments = json.loads(Path(fixture).read_text(encoding="utf-8"))[0]["arguments"]
    requested = arguments["projectPath"]
    _assert_preview_route(monkeypatch, dashboard, requested + "-other", requested, arguments)


def test_create_preview_preserves_execution_target_at_primitive_boundary():
    target = {"schema": "vrcforge.execution_target.v1", "project": {"root": "target"}}
    received = []

    def invoke(arguments):
        received.append(arguments)
        return {"ok": True}

    service = CreateWardrobePreviewService(CreateWardrobePreviewPorts(
        build_request=build_create_wardrobe_request, build_calls=build_create_wardrobe_core_calls,
        ensure_parameter=invoke, ensure_animator=invoke, ensure_menu=invoke,
    ))
    service.preview({"avatarPath": "TestAvatar", "executionTarget": target})
    assert all(arguments.get("executionTarget") == target for arguments in received)

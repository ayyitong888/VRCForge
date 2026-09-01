from __future__ import annotations

import json
from pathlib import Path

import pytest

import unity_editor_window_probe as reload_tool


def _project(tmp_path: Path, *, process_id: int = 4242) -> Path:
    descriptor = tmp_path / "Library" / "VRCForge" / "mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(json.dumps({"processId": process_id}), encoding="utf-8")
    return tmp_path


def _dialog(*, window: int = 101, button: int = 202) -> list[dict[str, object]]:
    return [
        {
            "windowHandle": window,
            "ownerWindow": 303,
            "title": "Unity",
            "className": "#32770",
            "visible": True,
            "enabled": True,
            "controls": [
                {"windowHandle": button, "text": "&Reload", "className": "Button"},
                {"windowHandle": 404, "text": "Ignore", "className": "Button"},
            ],
        }
    ]


def test_reload_confirmation_preparation_binds_exact_project_process_and_button(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(reload_tool, "_enumerate_process_windows", lambda _pid: _dialog())

    prepared, preview = reload_tool.prepare_unity_reload_confirmation(
        {"projectPath": str(project), "confirmReload": True},
        None,
    )

    assert prepared == {
        "projectPath": str(project.resolve()),
        "confirmReload": True,
        "expectedUnityProcessId": 4242,
        "expectedWindowHandle": 101,
        "expectedReloadButtonHandle": 202,
    }
    assert preview["schema"] == "vrcforge.unity_editor_reload_confirmation.v1"
    assert preview["unityProcessId"] == 4242
    assert preview["windowHandle"] == 101
    assert preview["reloadButtonHandle"] == 202
    assert preview["mayDiscardUnsavedEditorChanges"] is True
    assert preview["checkpointAvailable"] is False


@pytest.mark.parametrize("confirmation", (None, False, "true", 1))
def test_reload_confirmation_requires_explicit_boolean_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    confirmation: object,
) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(reload_tool, "_enumerate_process_windows", lambda _pid: _dialog())

    with pytest.raises(reload_tool.UnityReloadDialogError, match="confirmReload=true"):
        reload_tool.prepare_unity_reload_confirmation(
            {"projectPath": str(project), "confirmReload": confirmation},
            None,
        )


def test_reload_confirmation_rejects_dialog_without_exact_reload_button(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    windows = _dialog()
    windows[0]["controls"] = [
        {"windowHandle": 404, "text": "Ignore", "className": "Button"},
        {"windowHandle": 505, "text": "Reload", "className": "Static"},
    ]
    monkeypatch.setattr(reload_tool, "_enumerate_process_windows", lambda _pid: windows)

    with pytest.raises(reload_tool.UnityReloadDialogError, match="Reload button"):
        reload_tool.prepare_unity_reload_confirmation(
            {"projectPath": str(project), "confirmReload": True},
            None,
        )


def test_reload_confirmation_clicks_only_exact_preview_bound_reload_button(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    snapshots = iter((_dialog(), _dialog(), []))
    monkeypatch.setattr(reload_tool, "_enumerate_process_windows", lambda _pid: next(snapshots))
    calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        reload_tool,
        "_post_reload_button_click",
        lambda process_id, window_handle, button_handle: calls.append(
            (process_id, window_handle, button_handle)
        ),
    )
    prepared, _ = reload_tool.prepare_unity_reload_confirmation(
        {"projectPath": str(project), "confirmReload": True},
        None,
    )

    result = reload_tool.confirm_unity_reload_dialog(prepared)

    assert calls == [(4242, 101, 202)]
    assert result["ok"] is True
    assert result["reloadClicked"] is True
    assert result["dialogClosed"] is True
    assert result["unityProcessId"] == 4242
    assert result["windowHandle"] == 101
    assert result["reloadButtonHandle"] == 202


@pytest.mark.parametrize(
    ("changed_windows", "expected_message"),
    (
        (_dialog(window=111), "window changed"),
        (_dialog(button=222), "button changed"),
        ([], "no longer present"),
    ),
)
def test_reload_confirmation_rejects_preview_drift_before_any_click(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_windows: list[dict[str, object]],
    expected_message: str,
) -> None:
    project = _project(tmp_path)
    snapshots = iter((_dialog(), changed_windows))
    monkeypatch.setattr(reload_tool, "_enumerate_process_windows", lambda _pid: next(snapshots))
    monkeypatch.setattr(
        reload_tool,
        "_post_reload_button_click",
        lambda *_args: pytest.fail("A drifted Reload dialog must not be clicked"),
    )
    prepared, _ = reload_tool.prepare_unity_reload_confirmation(
        {"projectPath": str(project), "confirmReload": True},
        None,
    )

    with pytest.raises(reload_tool.UnityReloadDialogError, match=expected_message):
        reload_tool.confirm_unity_reload_dialog(prepared)


def test_reload_confirmation_rejects_descriptor_process_drift_before_any_click(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(reload_tool, "_enumerate_process_windows", lambda _pid: _dialog())
    monkeypatch.setattr(
        reload_tool,
        "_post_reload_button_click",
        lambda *_args: pytest.fail("A changed Unity process must not be clicked"),
    )
    prepared, _ = reload_tool.prepare_unity_reload_confirmation(
        {"projectPath": str(project), "confirmReload": True},
        None,
    )
    descriptor = project / "Library" / "VRCForge" / "mcp-core.json"
    descriptor.write_text(json.dumps({"processId": 9999}), encoding="utf-8")

    with pytest.raises(reload_tool.UnityReloadDialogError, match="process changed"):
        reload_tool.confirm_unity_reload_dialog(prepared)


def test_reload_confirmation_is_project_scoped_approval_bound_execution_tool() -> None:
    import dashboard_server
    from agent_gateway import canonical_unity_write_tool_input_schema

    tool_name = "vrcforge_confirm_unity_reload_dialog"
    gateway = dashboard_server.AGENT_GATEWAY
    handler = gateway._write_handlers[tool_name]

    assert handler.risk_level == "high"
    assert handler.pre_write_checkpoint_required is False
    assert handler.request_preparer is reload_tool.prepare_unity_reload_confirmation
    assert handler.handler is reload_tool.confirm_unity_reload_dialog
    assert "when to use:" in handler.description.casefold()
    assert "when not to use:" in handler.description.casefold()
    assert gateway.external_mcp_tool_block_for_name(tool_name, write=True) == "project"

    schema = canonical_unity_write_tool_input_schema(tool_name)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["projectPath", "confirmReload"]
    assert schema["properties"]["confirmReload"] == {"type": "boolean", "const": True}

    planning = dashboard_server._RuntimePlannerCatalog().read("planning")
    execution = dashboard_server._RuntimePlannerCatalog().read("execution")
    assert tool_name not in {tool.runtime_name for tool in planning.visible_tools}
    exposed = next(tool for tool in execution.visible_tools if tool.runtime_name == tool_name)
    assert exposed.block == "unity/project"
    assert exposed.write is True

    external = {
        item["name"]: item
        for item in gateway.build_external_mcp_tools("execution", tool_blocks=["project"])
    }
    assert external[tool_name]["inputSchema"] == schema

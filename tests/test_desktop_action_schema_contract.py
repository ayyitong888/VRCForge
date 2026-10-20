from __future__ import annotations

from pathlib import Path

from jsonschema import Draft202012Validator

from unity_tool_schema_projection import canonical_unity_read_tool_input_schema


class FakeDesktopService:
    def request_desktop_action(self, payload: dict) -> dict:
        assert payload["action"] == "computer_use"
        assert payload["params"]["operation"] == "list_apps"
        return {"status": "ok", "operation": payload["params"]["operation"], "apps": []}


def test_native_visible_desktop_schema_declares_two_level_call_shape() -> None:
    schema = canonical_unity_read_tool_input_schema("vrcforge_agent_desktop_action")
    assert schema["required"] == ["action"]
    assert schema["properties"]["action"]["enum"] == [
        "computer_use", "screenshot", "annotation", "browser", "desktop_rescue",
    ]
    operation = schema["properties"]["params"]["properties"]["operation"]
    assert "list_apps" in operation["enum"]
    Draft202012Validator(schema).validate(
        {"action": "computer_use", "params": {"operation": "list_apps"}}
    )


def test_read_only_list_apps_shape_reaches_desktop_service() -> None:
    payload = {"action": "computer_use", "params": {"operation": "list_apps"}}
    assert FakeDesktopService().request_desktop_action(payload) == {
        "status": "ok", "operation": "list_apps", "apps": [],
    }


def test_desktop_description_documents_outer_action_and_read_only_example() -> None:
    source = Path("dashboard_server.py").read_text(encoding="utf-8")
    assert "{action: 'computer_use', params: {operation: 'list_apps'}}" in source
    assert "Start with the read-only list_apps example" in source

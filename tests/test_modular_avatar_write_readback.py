from __future__ import annotations

from typing import Any

import dashboard_server
from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS
from wardrobe_outfit_workflow_service import (
    finalize_add_modular_avatar_component_verification,
)


def _write_result() -> dict[str, Any]:
    return {
        "ok": True,
        "addedComponent": True,
        "saveScene": True,
        "sceneSaved": True,
        "sceneDirty": False,
    }


def _inspection() -> dict[str, Any]:
    return {
        "ok": True,
        "present": True,
        "count": 1,
        "type": "nadena.dev.modular_avatar.core.ModularAvatarMergeArmature",
        "gameObjectPath": "FixtureAvatar/Part/Armature",
        "sceneDirty": False,
        "references": [{"componentIndex": 0, "member": "mergeTarget", "resolved": True, "resolvedPath": "FixtureAvatar/Armature"}],
    }


def test_approved_write_gets_independent_verified_readback() -> None:
    calls: list[dict[str, Any]] = []

    def inspect(arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(arguments)
        return _inspection()

    result = finalize_add_modular_avatar_component_verification(
        {
            "gameObjectPath": "FixtureAvatar/Part/Armature",
            "componentType": "MergeArmature",
            "saveScene": True,
            "references": {"mergeTarget": "FixtureAvatar/Armature"},
        },
        _write_result(),
        inspect,
    )
    assert result["verified"] is True
    assert result["commitState"] == "committed"
    assert result["readback"]["count"] == 1
    assert calls == [{"gameObjectPath": "FixtureAvatar/Part/Armature", "componentType": "MergeArmature"}]


def test_readback_preserves_routing_fields_and_verifies_requested_reference() -> None:
    calls: list[dict[str, Any]] = []
    arguments = {
        "projectPath": "D:/Unity/Project",
        "executionTarget": {"scope": "component", "namespace": "exact"},
        "gameObjectPath": "FixtureAvatar/Part/Armature",
        "componentType": "MergeArmature",
        "saveScene": True,
        "references": {"mergeTarget": "FixtureAvatar/Armature"},
    }
    inspected = _inspection()
    inspected["references"][0]["resolvedPath"] = "FixtureAvatar/Armature"
    result = finalize_add_modular_avatar_component_verification(
        arguments,
        {**_write_result(), "before": {"count": 0}, "after": {"count": 1}},
        lambda request: calls.append(request) or inspected,
    )
    assert result["verified"] is True
    assert calls == [{
        "projectPath": arguments["projectPath"],
        "executionTarget": arguments["executionTarget"],
        "gameObjectPath": arguments["gameObjectPath"],
        "componentType": arguments["componentType"],
    }]


def test_readback_rejects_missing_or_mismatched_reference_and_malformed_error() -> None:
    arguments = {"gameObjectPath": "FixtureAvatar/Armature", "componentType": "MergeArmature", "references": {"mergeTarget": "FixtureAvatar/Target"}}
    missing = finalize_add_modular_avatar_component_verification(arguments, _write_result(), lambda _request: {"ok": True, "present": True, "count": 1, "references": []})
    assert missing["ok"] is False
    malformed = finalize_add_modular_avatar_component_verification(arguments, _write_result(), lambda _request: "bad")
    assert malformed["ok"] is False
    assert "readback failed" in malformed["error"]
    wrong_case = _inspection()
    wrong_case["references"][0]["resolvedPath"] = "fixtureavatar/Armature"
    wrong = finalize_add_modular_avatar_component_verification(
        arguments,
        _write_result(),
        lambda _request: wrong_case,
    )
    assert wrong["ok"] is False
    assert "did not confirm reference" in wrong["error"]


def test_readback_rejects_dirty_scene_and_count_mismatch() -> None:
    dirty = _inspection()
    dirty["sceneDirty"] = True
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar/Armature", "componentType": "MergeArmature", "saveScene": True},
        _write_result(),
        lambda _request: dirty,
    )
    assert result["ok"] is False
    assert "still dirty" in result["error"]

    mismatched = _inspection()
    mismatched["count"] = 2
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar/Armature", "componentType": "MergeArmature", "saveScene": True},
        {**_write_result(), "before": {"count": 0}, "after": {"count": 1}},
        lambda _request: mismatched,
    )
    assert result["ok"] is False
    assert "differs from the write receipt" in result["error"]


def test_scalar_fields_are_not_marked_verified_without_independent_readback() -> None:
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar/Part/Armature", "componentType": "MergeArmature", "fields": {"prefix": "X"}},
        _write_result(),
        lambda _arguments: _inspection(),
    )
    assert result["ok"] is False
    assert "scalar fields" in result["error"]
    assert "verified" not in result


def test_scalar_fields_can_be_verified_through_independent_get_property_readback() -> None:
    calls: list[dict[str, Any]] = []
    result = finalize_add_modular_avatar_component_verification(
        {"projectPath": "D:/Unity/Project", "gameObjectPath": "FixtureAvatar/Armature", "componentType": "MergeArmature", "fields": {"prefix": "X"}},
        _write_result(),
        lambda _arguments: _inspection(),
        lambda arguments: calls.append(arguments) or {"ok": True, "value": "X"},
    )
    assert result["verified"] is True
    assert result["readback"]["fields"] == {"prefix": "X"}
    assert calls[0]["propertyPath"] == "prefix"
    assert calls[0]["componentType"] == _inspection()["type"]


def test_public_schema_exposes_component_references_and_write_options() -> None:
    schema = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_add_modular_avatar_component"]
    assert schema["required"] == ["projectPath", "gameObjectPath", "componentType"]
    assert schema["properties"]["references"] == {"type": "object", "additionalProperties": True}
    assert schema["properties"]["fields"] == {"type": "object", "additionalProperties": True}
    assert schema["properties"]["saveScene"]["type"] == "boolean"
    assert schema["properties"]["allowDuplicate"]["type"] == "boolean"


def test_registered_write_handler_uses_readback_finalizer() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_add_modular_avatar_component"]
    assert handler.verification_finalize_handler is not None

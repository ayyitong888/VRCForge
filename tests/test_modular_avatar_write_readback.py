from __future__ import annotations

from typing import Any

import pytest

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


def test_optional_unset_reference_does_not_block_requested_avatar_reference() -> None:
    inspected = _inspection()
    inspected["references"].insert(0, {
        "componentIndex": 0,
        "member": "relativePathRoot",
        "resolved": False,
        "referencePath": None,
        "resolvedPath": None,
    })
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar/Armature", "componentType": "MergeArmature",
         "references": {"mergeTarget": "FixtureAvatar/Armature"}},
        _write_result(),
        lambda _request: inspected,
    )
    assert result["verified"] is True
    assert result["readback"]["references"][0]["member"] == "relativePathRoot"


def test_requested_direct_unity_object_string_is_read_independently_with_component_index() -> None:
    inspected = _inspection()
    inspected["type"] = "nadena.dev.modular_avatar.core.ModularAvatarMergeAnimator"
    inspected["references"] = [{
        "componentIndex": 0,
        "member": "relativePathRoot",
        "resolved": False,
        "referencePath": None,
        "resolvedPath": None,
    }]
    calls: list[dict[str, Any]] = []
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar/Armature", "componentType": "MergeAnimator",
         "references": {"animator": "Assets/Controllers/FullFT.controller"}},
        _write_result(),
        lambda _request: inspected,
        lambda request: calls.append(request) or {
            "ok": True,
            "propertyValue": {
                "name": "FullFT",
                "type": "UnityEditor.Animations.AnimatorController",
                "assetPath": "Assets/Controllers/FullFT.controller",
                "hierarchyPath": "",
            },
            "valueTruncated": False,
            "valueIncomplete": False,
        },
    )
    assert result["verified"] is True
    assert result["readback"]["referenceReadback"]["animator"]["assetPath"] == "Assets/Controllers/FullFT.controller"
    assert calls == [{
        "gameObjectPath": "FixtureAvatar/Armature",
        "componentType": inspected["type"],
        "propertyPath": "animator",
        "componentIndex": 0,
        "maxItems": 50,
    }]


def test_requested_direct_unity_object_rejects_null_or_mismatched_property_readback() -> None:
    arguments = {"gameObjectPath": "FixtureAvatar/Armature", "componentType": "MergeAnimator",
                 "references": {"animator": "Assets/Controllers/FullFT.controller"}}
    for property_value in (None, {"assetPath": "Assets/Controllers/Other.controller", "hierarchyPath": ""}):
        result = finalize_add_modular_avatar_component_verification(
            arguments,
            _write_result(),
            lambda _request: {**_inspection(), "references": [{
                "componentIndex": 0, "member": "relativePathRoot", "resolved": False,
                "referencePath": None, "resolvedPath": None,
            }]},
            lambda _request, value=property_value: {"ok": True, "propertyValue": value},
        )
        assert result["ok"] is False
        assert "direct Unity Object" in result["error"]


def test_readback_treats_post_save_editor_dirty_as_pending_and_rejects_count_mismatch() -> None:
    dirty = _inspection()
    dirty["sceneDirty"] = True
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar/Armature", "componentType": "MergeArmature", "saveScene": True},
        _write_result(),
        lambda _request: dirty,
    )
    # ExecuteAlways components can legitimately dirty a scene after SaveScene;
    # the component exists, but a fresh dirty scene must not be called committed.
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["pending"] is True
    assert result["commitState"] == "pending"

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
    assert "cannot verify fields" in result["error"]
    assert "verified" not in result


def test_scalar_fields_can_be_verified_through_independent_get_property_readback() -> None:
    calls: list[dict[str, Any]] = []
    result = finalize_add_modular_avatar_component_verification(
        {"projectPath": "D:/Unity/Project", "gameObjectPath": "FixtureAvatar/Armature", "componentType": "MergeArmature", "fields": {"prefix": "X"}},
        _write_result(),
        lambda _arguments: _inspection(),
        lambda arguments: calls.append(arguments) or {"ok": True, "propertyValue": "X"},
    )
    assert result["verified"] is True
    assert result["readback"]["fields"] == {"prefix": "X"}
    assert calls[0]["propertyPath"] == "prefix"
    assert calls[0]["componentType"] == _inspection()["type"]


def test_structured_field_uses_property_value_and_matches_requested_projection() -> None:
    expected = [
        {"nameOrPrefix": "Toggle/EyeTracking", "syncType": "Bool", "saved": True},
        {"nameOrPrefix": "v2/EyeLidLeft", "syncType": "Float", "defaultValue": 0.75},
    ]
    observed = [
        {**expected[0], "internalParameter": False, "hasExplicitDefaultValue": True},
        {**expected[1], "internalParameter": False, "localOnly": False},
    ]
    calls: list[dict[str, Any]] = []
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar", "componentType": "Parameters", "fields": {"parameters": expected}},
        _write_result(),
        lambda _arguments: _inspection(),
        lambda arguments: calls.append(arguments) or {
            "ok": True,
            "propertyValue": observed,
            "valueTruncated": False,
        },
    )
    assert result["verified"] is True
    assert result["readback"]["fields"]["parameters"] == observed
    assert calls[0]["propertyPath"] == "parameters"
    assert calls[0]["maxItems"] == 50


def test_structured_field_requires_exact_array_and_rejects_truncation() -> None:
    expected = [{"nameOrPrefix": f"p{i}", "syncType": "Float"} for i in range(91)]
    observed = [dict(item, internalParameter=False) for item in expected]
    arguments = {"gameObjectPath": "FixtureAvatar", "componentType": "Parameters", "fields": {"parameters": expected}}
    calls: list[dict[str, Any]] = []

    truncated = finalize_add_modular_avatar_component_verification(
        arguments,
        _write_result(),
        lambda _arguments: _inspection(),
        lambda actual: calls.append(actual) or {"ok": True, "propertyValue": observed[:50], "valueTruncated": True},
    )
    assert truncated["ok"] is False
    assert "failed" in truncated["error"]
    assert calls[0]["maxItems"] == 91

    short = finalize_add_modular_avatar_component_verification(
        arguments,
        _write_result(),
        lambda _arguments: _inspection(),
        lambda _arguments: {"ok": True, "propertyValue": observed[:90], "valueTruncated": False},
    )
    assert short["ok"] is False
    assert "mismatched" in short["error"]


def test_structured_field_accepts_complete_91_item_property_value() -> None:
    expected = [{"nameOrPrefix": f"p{i}", "syncType": "Float"} for i in range(91)]
    observed = [dict(item, internalParameter=False, defaultValue=0.0) for item in expected]
    calls: list[dict[str, Any]] = []
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar", "componentType": "Parameters", "fields": {"parameters": expected}},
        _write_result(),
        lambda _arguments: _inspection(),
        lambda actual: calls.append(actual) or {
            "ok": True, "propertyValue": observed, "valueTruncated": False,
        },
    )
    assert result["verified"] is True
    assert calls[0]["maxItems"] == 91


def test_structured_field_rejects_missing_key_and_reordered_items() -> None:
    expected = [
        {"nameOrPrefix": "p0", "syncType": "Float"},
        {"nameOrPrefix": "p1", "syncType": "Bool"},
    ]
    arguments = {"gameObjectPath": "FixtureAvatar", "componentType": "Parameters", "fields": {"parameters": expected}}

    missing = finalize_add_modular_avatar_component_verification(
        arguments, _write_result(), lambda _arguments: _inspection(),
        lambda _arguments: {"ok": True, "propertyValue": [{"nameOrPrefix": "p0"}, expected[1]], "valueTruncated": False},
    )
    assert missing["ok"] is False
    assert "mismatched" in missing["error"]

    reordered = finalize_add_modular_avatar_component_verification(
        arguments, _write_result(), lambda _arguments: _inspection(),
        lambda _arguments: {"ok": True, "propertyValue": [expected[1], expected[0]], "valueTruncated": False},
    )
    assert reordered["ok"] is False
    assert "mismatched" in reordered["error"]


def test_structured_field_rejects_bool_type_coercion() -> None:
    expected = [{"nameOrPrefix": "toggle", "saved": True}]
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar", "componentType": "Parameters", "fields": {"parameters": expected}},
        _write_result(),
        lambda _arguments: _inspection(),
        lambda _arguments: {"ok": True, "propertyValue": [{"nameOrPrefix": "toggle", "saved": 1}], "valueTruncated": False},
    )
    assert result["ok"] is False
    assert "mismatched" in result["error"]


def test_structured_field_rejects_requested_member_mismatch() -> None:
    expected = [{"nameOrPrefix": "Toggle/EyeTracking", "syncType": "Bool"}]
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar", "componentType": "Parameters", "fields": {"parameters": expected}},
        _write_result(),
        lambda _arguments: _inspection(),
        lambda _arguments: {
            "ok": True,
            "propertyValue": [{"nameOrPrefix": "Toggle/EyeTracking", "syncType": "Float", "saved": True}],
            "valueTruncated": False,
        },
    )
    assert result["ok"] is False
    assert "mismatched" in result["error"]


def test_public_schema_exposes_component_references_and_write_options() -> None:
    schema = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_add_modular_avatar_component"]
    assert schema["required"] == ["projectPath", "gameObjectPath", "componentType"]
    assert schema["properties"]["references"] == {"type": "object", "additionalProperties": True}
    assert schema["properties"]["fields"] == {"type": "object", "additionalProperties": True}
    assert schema["properties"]["saveScene"]["type"] == "boolean"
    assert schema["properties"]["allowDuplicate"]["type"] == "boolean"


def test_structured_field_rejects_explicit_incomplete_readback() -> None:
    expected = [{"nameOrPrefix": "p0", "syncType": "Float"}]
    result = finalize_add_modular_avatar_component_verification(
        {"gameObjectPath": "FixtureAvatar", "componentType": "Parameters", "fields": {"parameters": expected}},
        _write_result(), lambda _arguments: _inspection(),
        lambda _arguments: {"ok": True, "propertyValue": expected, "valueIncomplete": True},
    )
    assert result["ok"] is False
    assert "verified" not in result


def test_structured_field_rejects_nested_incomplete_marker() -> None:
    expected = [{"nameOrPrefix": "p0", "syncType": "Float"}]
    for marker in ("__incomplete", "__truncated", "__cycle"):
        result = finalize_add_modular_avatar_component_verification(
            {"gameObjectPath": "FixtureAvatar", "componentType": "Parameters", "fields": {"parameters": expected}},
            _write_result(), lambda _arguments: _inspection(),
            lambda _arguments: {"ok": True, "propertyValue": [{**expected[0], marker: True}]},
        )
        assert result["ok"] is False, marker
        assert "verified" not in result


def test_registered_write_handler_uses_readback_finalizer() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_add_modular_avatar_component"]
    assert handler.verification_finalize_handler is not None


def _sync_readback_request() -> dict[str, Any]:
    return {
        "gameObjectPath": "FixtureAvatar/Shirt", "avatarPath": "FixtureAvatar",
        "componentType": "nadena.dev.modular_avatar.core.ModularAvatarBlendshapeSync",
        "saveScene": True,
        "fields": {"Bindings": [
            {"ReferenceMesh": {"referencePath": "Body"}, "Blendshape": name, "LocalBlendshape": name}
            for name in ("(Wear bra)Breasts_Big", "(Wear bra)Breasts_Cow")
        ]},
    }


def _sync_inspection() -> dict[str, Any]:
    request = _sync_readback_request()
    return {"ok": True, "present": True, "count": 1, "type": request["componentType"],
            "gameObjectPath": request["gameObjectPath"], "sceneDirty": True, "references": []}


def test_sync_delayed_editor_update_verifies_bindings_but_preserves_pending_save() -> None:
    from agent_tool_result_contract import normalize_agent_tool_result

    request = _sync_readback_request()
    reads = []
    result = finalize_add_modular_avatar_component_verification(
        request, {**_write_result(), "before": {"count": 0}, "after": {"count": 1}},
        lambda _: _sync_inspection(),
        lambda args: reads.append(args) or {"ok": True, "propertyValue": request["fields"]["Bindings"],
                                          "valueTruncated": False, "valueIncomplete": False},
    )
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["pending"] is True
    assert result["commitState"] == "pending"
    assert result.get("committed") is not True
    assert result["readback"]["fields"] == request["fields"]
    assert reads[0]["propertyPath"] == "Bindings"
    outcome = normalize_agent_tool_result(result, fallback_summary="MA Sync configuration", write=True)
    assert outcome["status"] != "failed", outcome
    assert outcome.get("commitState") != "committed"


def test_sync_dirty_scene_does_not_bypass_mismatched_binding_readback() -> None:
    request = _sync_readback_request()
    reads = []
    result = finalize_add_modular_avatar_component_verification(
        request, _write_result(), lambda _: _sync_inspection(),
        lambda args: reads.append(args) or {"ok": True, "propertyValue": [], "valueTruncated": False},
    )
    assert reads, "Dirty scene must not skip the requested field verification."
    assert result["ok"] is False
    assert "mismatched field 'Bindings'" in result["error"]
    assert result.get("verified") is not True


@pytest.mark.parametrize("dirty", [None, "false", 0])
def test_save_requested_requires_boolean_fresh_dirty_evidence(dirty) -> None:
    inspected = _sync_inspection()
    inspected["sceneDirty"] = dirty
    request = _sync_readback_request()
    result = finalize_add_modular_avatar_component_verification(
        request, _write_result(), lambda _: inspected,
        lambda _: {"ok": True, "propertyValue": request["fields"]["Bindings"]},
    )
    assert result["ok"] is False
    assert result.get("verified") is not True


def test_verified_sync_pending_save_does_not_fail_approval_or_create_recovery_lock(tmp_path) -> None:
    from test_mcp_write_transaction_contract import _gateway

    gateway = _gateway(tmp_path)
    request = _sync_readback_request()
    project = tmp_path / "project"
    for folder in ("Assets", "Packages", "ProjectSettings"):
        (project / folder).mkdir(parents=True)
    (project / "Assets/Main.unity").write_text("scene")
    (project / "Assets/Main.unity.meta").write_text("guid: scene")
    request["projectPath"] = str(project)
    tool = "vrcforge_contract_sync_editor_update"
    gateway.approval_transactions.register_write_handler(
        tool, "MA Sync pending save contract", "medium", lambda _: _write_result(),
        checkpoint_prepare_handler=lambda _root, _arguments:
            {"ok": True, "archiveAssetPaths": ["Assets/Main.unity"]},
        verification_profile="fresh_readback",
        verification_prepare_handler=lambda _: {},
        verification_finalize_handler=lambda arguments, _baseline, result:
            finalize_add_modular_avatar_component_verification(
                arguments, result, lambda _: _sync_inspection(),
                lambda _: {"ok": True, "propertyValue": request["fields"]["Bindings"]}),
    )
    created = gateway.approval_transactions.create_apply_request({"target_tool": tool, "arguments": request})
    approval_id = created["approval"]["id"]
    gateway.approval_transactions.approve(approval_id)
    applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
    assert applied["ok"] is True, applied
    assert applied["approval"]["status"] == "applied"
    assert applied["result"]["verified"] is True
    assert applied["result"]["pending"] is True
    assert applied["result"]["commitState"] == "pending"
    assert applied["outcome"]["status"] != "failed"
    assert applied["outcome"].get("commitState") != "committed"
    assert "writeFailure" not in applied
    assert applied["checkpoint"]["ok"] is True
    assert not gateway.approval_transactions._ports.checkpoint.active_apply_recoveries()
    assert not any(entry.get("event") == "approval_apply_failed"
                   for entry in gateway.approval_transactions.recent_audit_logs())


@pytest.mark.parametrize("expected", [1, "Set"])
def test_nested_enum_field_uses_independent_clr_identity(expected):
    result = finalize_add_modular_avatar_component_verification(
        {"componentType": "ShapeChanger", "fields": {"Shapes": [{"ChangeType": expected, "Value": 0}]}},
        _write_result(), lambda _: _inspection(),
        lambda _: {"ok": True, "propertyValue": [{"ChangeType": "Set", "Value": 0.0}],
                   "enumValues": {"/0/ChangeType": {"type": "Fixture.ChangeKind", "name": "Set", "value": 1}}},
    )
    assert result.get("verified") is True, result


@pytest.mark.parametrize("metadata", [None, {}, {"/0/ChangeType": {"type": "Fixture.Kind", "name": "Other", "value": 1}}, {"/0/ChangeType": {"type": "Fixture.Kind", "name": "Set", "value": 2}}])
def test_numeric_enum_does_not_guess_missing_or_mismatched_type_evidence(metadata):
    result = finalize_add_modular_avatar_component_verification(
        {"fields": {"Shapes": [{"ChangeType": 1}]}}, _write_result(), lambda _: _inspection(),
        lambda _: {"ok": True, "propertyValue": [{"ChangeType": "Set"}], "enumValues": metadata},
    )
    assert result.get("verified") is not True


def test_scalar_enum_and_escaped_member_pointer_share_comparison():
    from wardrobe_outfit_workflow_service import _requested_value_matches
    evidence = {"": {"type": "Fixture.Kind", "name": "FlagsA, FlagsB", "value": 3}}
    assert _requested_value_matches("FlagsA, FlagsB", 3, evidence)
    assert not _requested_value_matches("FlagsA, FlagsB", True, evidence)
    evidence = {"/a~1b~0c": {"type": "Fixture.Kind", "name": "Set", "value": 1}}
    assert _requested_value_matches({"a/b~c": "Set"}, {"a/b~c": 1}, evidence)

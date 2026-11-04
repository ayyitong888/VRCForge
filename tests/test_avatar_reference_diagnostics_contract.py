from __future__ import annotations

import re
from pathlib import Path

from unity_mcp_tool_contract import (
    EXPECTED_TOOL_COUNT,
    EXPECTED_TOOL_NAMES,
    PLANNING_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
    is_direct_read_call,
)
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "Assets/VRCForge/Editor/Generic/UnityAvatarReferenceDiagnostics.cs"
CONTRACT = ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpToolContract.cs"


def test_avatar_reference_diagnostics_are_an_exact_read_only_planning_tool() -> None:
    name = "vrc_scan_avatar_reference_diagnostics"
    source = TOOL.read_text(encoding="utf-8")
    contract = CONTRACT.read_text(encoding="utf-8")
    schema = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_scan_avatar_reference_diagnostics"]

    assert name in EXPECTED_TOOL_NAMES
    assert name in READ_ONLY_TOOL_NAMES
    assert name in PLANNING_TOOL_NAMES
    assert EXPECTED_TOOL_COUNT == 105
    assert re.search(rf'\{{ "{name}", "VRCForge\.Editor\.UnityAvatarReferenceDiagnostics" \}}', contract)
    assert is_direct_read_call(name, {"avatarPath": "Avatar"})
    assert schema["required"] == ["avatarPath"]
    assert schema["additionalProperties"] is False


def test_diagnostic_reports_only_unity_marked_missing_references_and_covers_inactive_children() -> None:
    source = TOOL.read_text(encoding="utf-8")
    assert "GetComponentsInChildren<Transform>(true)" in source
    assert "GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(gameObject)" in source
    assert "property.objectReferenceValue != null" in source
    assert "property.objectReferenceInstanceIDValue == 0" in source
    assert 'kind = "serialized_missing_object_reference"' in source
    assert 'kind = "missing_script_component"' in source
    assert 'proofStatus = unresolvedScans.Count == 0 ? "complete" : "indeterminate"' in source
    assert "mutat" in source.lower()
    assert "RemoveMonoBehavioursWithMissingScript" not in source
    assert "AssetDatabase.DeleteAsset" not in source


def test_menu_parameter_comparison_is_evidence_not_a_sdk_builtin_verdict() -> None:
    source = TOOL.read_text(encoding="utf-8")
    assert "AvatarControlScanner.HandleCommand" in source
    assert 'source"], "menu_control"' in source
    assert 'payload["expressionParameterNames"] as JArray' in source
    assert "explicitlyDeclaredInExpressionParameters" in source
    assert "may be an SDK built-in" in source
    assert "SDK built-in" in source

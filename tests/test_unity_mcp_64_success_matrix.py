from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from unity_mcp_tool_contract import EXPECTED_TOOL_NAMES


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_unity_mcp_64_success_matrix.py"
AVATAR_PRIMITIVE_CRUD = (
    ROOT / "Assets" / "VRCForge" / "Editor" / "Generic" / "UnityAvatarPrimitiveCrud.cs"
).read_text(encoding="utf-8-sig")


def load_validator():
    spec = importlib.util.spec_from_file_location("unity_mcp_64_matrix", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_covers_exact_80_tools_once_with_real_success_contracts() -> None:
    validator = load_validator()
    cases = validator.load_catalog()
    assert len(cases) == 80
    assert {case["tool"] for case in cases} == EXPECTED_TOOL_NAMES
    assert all(case["arguments"] is not None for case in cases)
    assert all(case["requiredFixtures"] for case in cases)
    assert all(case["successFields"] for case in cases)
    assert all(case["cleanup"] for case in cases)
    assert {case["mode"] for case in cases} == {"read", "preview", "approved_write", "safety"}


def test_catalog_placeholders_resolve_recursively_in_keys_and_values() -> None:
    validator = load_validator()
    sample = {
        "${DYNAMIC_KEY}": "${INTEGER_VALUE}",
        "${PREVIEW_RECEIPT}": None,
        "path": "${ROOT}/Assets/${NAME}",
        "nested": [{"value": "${BOOL_VALUE}"}],
    }
    resolved = validator.resolve_placeholders(
        sample,
        {
            "DYNAMIC_KEY": "operationSpecific",
            "INTEGER_VALUE": 42,
            "ROOT": "D:/fixture",
            "NAME": "Probe.asset",
            "BOOL_VALUE": True,
            "PREVIEW_RECEIPT": {"expectedDigest": "a" * 64, "expectedCount": 3},
        },
    )
    assert resolved == {
        "operationSpecific": 42,
        "path": "D:/fixture/Assets/Probe.asset",
        "nested": [{"value": True}],
        "expectedDigest": "a" * 64,
        "expectedCount": 3,
    }


def test_catalog_placeholder_resolution_fails_closed_when_context_is_missing() -> None:
    validator = load_validator()
    with pytest.raises(ValueError, match="missing placeholder"):
        validator.resolve_placeholders({"path": "${MISSING}"}, {})


def test_live_schema_gate_rejects_empty_or_missing_catalog_parameters() -> None:
    validator = load_validator()
    cases = validator.load_catalog()
    tools = []
    for case in cases:
        keys = [key for key in case["arguments"] if not key.startswith("${")]
        properties = {key: {"type": "object"} for key in keys}
        for key in case.get("runtimeInjectedArguments", []):
            properties[key] = {"type": "object"}
        if not properties:
            properties = {"fixture": {"type": "string"}}
        tools.append(
            {
                "name": case["tool"],
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "additionalProperties": False,
                },
            }
        )
    assert validator.validate_tool_schemas(cases, tools)["toolCount"] == 80

    broken = [dict(item) for item in tools]
    broken[0] = {
        **broken[0],
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }
    with pytest.raises(ValueError, match="exposes no parameters"):
        validator.validate_tool_schemas(cases, broken)


def test_avatar_primitive_crud_commands_expose_nonempty_schemas_covering_catalog_inputs() -> None:
    """The six primitive CRUD commands must not silently publish empty schemas."""
    validator = load_validator()
    class_by_tool = {
        "vrc_read_avatar_descriptor": "ReadAvatarDescriptorTool",
        "vrc_write_avatar_descriptor": "WriteAvatarDescriptorTool",
        "vrc_write_animation_curve": "WriteAnimationCurveTool",
        "vrc_manage_expression_parameters": "ManageExpressionParametersTool",
        "vrc_manage_expression_menu": "ManageExpressionMenuTool",
        "vrc_manage_fx_animator": "ManageFxAnimatorTool",
    }

    def class_body(class_name: str) -> str:
        match = re.search(rf"public static class {class_name}\s*\{{", AVATAR_PRIMITIVE_CRUD)
        assert match, class_name
        start = AVATAR_PRIMITIVE_CRUD.index("{", match.start())
        depth = 0
        for index in range(start, len(AVATAR_PRIMITIVE_CRUD)):
            if AVATAR_PRIMITIVE_CRUD[index] == "{":
                depth += 1
            elif AVATAR_PRIMITIVE_CRUD[index] == "}":
                depth -= 1
                if depth == 0:
                    return AVATAR_PRIMITIVE_CRUD[start : index + 1]
        raise AssertionError(f"unterminated {class_name}")

    schemas = {}
    for tool, class_name in class_by_tool.items():
        body = class_body(class_name)
        parameter_match = re.search(r"public class Parameters\s*\{", body)
        assert parameter_match, f"{tool} has no Parameters schema"
        parameter_body = body[parameter_match.start() :]
        fields = set(
            re.findall(
                r"\[VRCForgeInput(?:\([^\]]*\))?\]\s*public\s+[\w<>\[\]?]+\s+(\w+)\s*\{",
                parameter_body,
            )
        )
        assert fields, f"{tool} exposes an empty Parameters schema"
        schemas[tool] = (fields, parameter_body)

    for case in validator.load_catalog():
        if case["tool"] not in schemas:
            continue
        fields, _ = schemas[case["tool"]]
        concrete = {key for key in case["arguments"] if not key.startswith("${")}
        assert concrete <= fields, f"{case['tool']} catalog keys missing from schema: {sorted(concrete - fields)}"

    for tool in ("vrc_manage_expression_parameters", "vrc_manage_expression_menu", "vrc_manage_fx_animator"):
        assert re.search(r"VRCForgeInput\([^\n]+IsRequired = true\)\] public string action", schemas[tool][1])
    assert re.search(r"IsRequired = true\)\] public string clipPath", schemas["vrc_write_animation_curve"][1])
    assert re.search(r"IsRequired = true\)\] public string propertyName", schemas["vrc_write_animation_curve"][1])


def test_animation_curve_atom_supports_lossless_guarded_binding_retarget() -> None:
    source = AVATAR_PRIMITIVE_CRUD
    assert 'action == "retarget_curve"' in source
    assert "AnimationUtility.GetEditorCurve(existingClip" in source
    assert "new AnimationCurve(sourceCurve.keys)" in source
    assert "preWrapMode = sourceCurve.preWrapMode" in source
    assert "postWrapMode = sourceCurve.postWrapMode" in source
    assert "destinationCurve != null && !overwriteExisting" in source
    assert "AnimationUtility.SetEditorCurve(clip, source, null)" in source
    assert "BindingsEqual(source, binding)" in source


def test_fx_animator_atom_deletes_only_unreferenced_parameters() -> None:
    source = AVATAR_PRIMITIVE_CRUD
    assert 'action == "delete_parameter"' in source
    assert "HandleDeleteParameter(controller, @params, preview, plan)" in source
    assert "ValidateUnusedParameterDeletion" in source
    assert "CollectParameterReferences" in source
    assert "CollectTransitionReferences" in source
    assert "CollectMotionReferences" in source
    assert "CollectBehaviourReferences" in source
    assert "controller.RemoveParameter(parameterIndex)" in source
    assert "is still referenced by:" in source
    assert "FX animator parameter deletion persisted readback was not exact" in source
    assert "RestoreAnimatorControllerPreState" in source
    assert 'commitState = restored ? "rolled_back" : "unknown"' in source
    assert "fx_animator_delete_parameter_rejected" in source

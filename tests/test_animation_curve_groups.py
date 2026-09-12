import copy
import json
import os
from pathlib import Path
from unittest.mock import Mock

import jsonschema
import pytest

import dashboard_server as server
from animation_curve_input import expand_animation_curve_sets
from approved_unity_execution import freeze_approved_unity_execution_plan
from unity_execution_plans_scene import build_scene_execution_plan
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS


TOOL = "vrcforge_write_animation_curve"
ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = Path(os.environ["VRCFORGE_REGRESSION_ARCHIVE_ROOT"]) if os.environ.get("VRCFORGE_REGRESSION_ARCHIVE_ROOT") else ROOT / ".tmp" / "regression-archives"
GROUP = {
    "bindingPaths": ["Body", "Dress"],
    "componentType": "SkinnedMeshRenderer",
    "overwriteExisting": True,
    "properties": [
        {"propertyName": "blendShape.Fit", "constantFloat": 42},
        {"propertyName": "material._Amount", "keys": [{"time": 0, "value": 0}, {"time": 1, "value": 1}]},
    ],
}


def request(mode="clips"):
    value = {"projectPath": "D:/Project"}
    if mode == "single":
        return {**value, "clipPath": "Assets/A.anim", "curves": [copy.deepcopy(GROUP)]}
    if mode == "sets":
        return {**value, "curveSets": [[copy.deepcopy(GROUP)]], "clips": [
            {"clipPath": "Assets/A.anim", "curveSet": 0},
            {"clipPath": "Assets/B.anim", "curveSet": 0},
        ]}
    return {**value, "clips": [{"clipPath": "Assets/A.anim", "curves": [copy.deepcopy(GROUP)]}]}


def flat_rows(group):
    return [{"bindingPath": path, "componentType": group["componentType"],
             **({"overwriteExisting": group["overwriteExisting"]} if "overwriteExisting" in group else {}),
             **copy.deepcopy(prop)} for path in group["bindingPaths"] for prop in group["properties"]]


def flatten(value):
    result = copy.deepcopy(value)
    sets = result.pop("curveSets", [])
    clips = result.get("clips", [result])
    for clip in clips:
        rows = sets[clip.pop("curveSet")] if "curveSet" in clip else clip["curves"]
        clip["curves"] = [row for item in rows for row in (flat_rows(item) if "bindingPaths" in item else [item])]
    return result


@pytest.mark.parametrize("mode", ["single", "clips", "sets"])
@pytest.mark.parametrize("preview", [True, False])
def test_public_preview_write_and_approved_plan_share_exact_expansion(monkeypatch, mode, preview):
    value = request(mode)
    before = copy.deepcopy(value)
    for schema in (UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_write_animation_curve"],
                   EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS[TOOL]):
        jsonschema.validate(value, schema)
    invocation = Mock(return_value={"ok": True})
    monkeypatch.setattr(server, "load_dashboard_settings", lambda _: object())
    monkeypatch.setattr(server, "invoke_unity_mcp", invocation)
    monkeypatch.setattr(server, "extract_tool_result_payload", lambda result: result)
    assert server.write_animation_curve_sync(value, preview=preview) == {"ok": True}
    expected = server._avatar_primitive_request(flatten(value), preview=preview)
    assert invocation.call_args.args[1:] == ("vrc_write_animation_curve", expected)
    group_plan = build_scene_execution_plan(TOOL, value)
    flat_plan = build_scene_execution_plan(TOOL, flatten(value))
    assert group_plan == flat_plan
    assert freeze_approved_unity_execution_plan(group_plan) == freeze_approved_unity_execution_plan(flat_plan)
    assert value == before


def test_changed_group_changes_approval_and_expansion_has_no_aliases():
    value = request("sets")
    changed = copy.deepcopy(value)
    changed["curveSets"][0][0]["properties"][0]["constantFloat"] = 43
    assert freeze_approved_unity_execution_plan(build_scene_execution_plan(TOOL, value)) != freeze_approved_unity_execution_plan(build_scene_execution_plan(TOOL, changed))
    expanded = expand_animation_curve_sets(value)
    expanded["clips"][0]["curves"][1]["keys"][0]["value"] = 99
    assert expanded["clips"][0]["curves"][3]["keys"][0]["value"] == 0
    assert expanded["clips"][1]["curves"][1]["keys"][0]["value"] == 0
    assert value == request("sets")


def invalid_groups():
    for extra in ({"bindingPath": "Other"}, {"propertyName": "x"}, {"unknown": 1},
                  {"bindingPaths": []}, {"bindingPaths": ["Body", "Body"]}, {"properties": []},
                  {"properties": [GROUP]}, {"properties": [{"propertyName": "x", "constantFloat": 1, "keys": []}]},
                  {"properties": [{"propertyName": "x", "constantFloat": 1, "bindingPath": "Other"}]}):
        yield {**copy.deepcopy(GROUP), **extra}


@pytest.mark.parametrize("group", list(invalid_groups()))
def test_malformed_groups_reject_before_transport_and_approval(monkeypatch, group):
    value = request("single")
    value["curves"] = [group]
    invocation = Mock()
    monkeypatch.setattr(server, "invoke_unity_mcp", invocation)
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        server.write_animation_curve_sync(value, preview=True)
    invocation.assert_not_called()
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        build_scene_execution_plan(TOOL, value)


@pytest.mark.parametrize("limit", ["256", "4096", "512 KiB"])
def test_expanded_budgets_apply_before_transport(monkeypatch, limit):
    value = request("single")
    group = value["curves"][0]
    if limit == "256":
        group["bindingPaths"] = [f"Renderer{i}" for i in range(129)]
    elif limit == "4096":
        group["properties"] = [{"propertyName": "x", "keys": [{"time": i, "value": 1} for i in range(2050)]}]
    else:
        group["bindingPaths"] = ["x" * 140000, "y" * 140000]
    invocation = Mock()
    monkeypatch.setattr(server, "invoke_unity_mcp", invocation)
    with pytest.raises(ValueError, match=limit):
        server.write_animation_curve_sync(value, preview=True)
    invocation.assert_not_called()
    with pytest.raises(ValueError, match=limit):
        build_scene_execution_plan(TOOL, value)


def test_group_can_mix_with_flat_rows_and_preserves_root_path_and_defaults():
    value = request("single")
    group = value["curves"][0]
    group["bindingPaths"] = [""]
    del group["overwriteExisting"]
    value["curves"].append({"bindingPath": "Hat", "propertyName": "m_IsActive", "constantFloat": 1})
    assert expand_animation_curve_sets(value) == flatten(value)


@pytest.mark.parametrize("field", ["curveSets", "clips"])
def test_invalid_shared_envelope_still_uses_schema_rejection(field):
    value = request("sets")
    value[field] = 1
    with pytest.raises(jsonschema.ValidationError):
        expand_animation_curve_sets(value)


def test_real941_groups_expand_row_for_row_and_reduce_public_input():
    folder = ARCHIVE_ROOT
    path = folder / "941-cyber-default-cloud-write-request.json"
    if not path.exists():
        pytest.skip("Local public acceptance archive unavailable")
    totals = [0, 0, 0]
    for call in json.loads(path.read_text("utf-8-sig")):
        if call["params"]["name"] != TOOL:
            continue
        original = call["params"]["arguments"]
        grouped = copy.deepcopy(original)
        for clip in grouped["clips"]:
            paths = list(dict.fromkeys(row["bindingPath"] for row in clip["curves"]))
            first = [row for row in clip["curves"] if row["bindingPath"] == paths[0]]
            group = {"bindingPaths": paths, "componentType": first[0]["componentType"],
                     "overwriteExisting": first[0]["overwriteExisting"],
                     "properties": [{k: v for k, v in row.items() if k not in {"bindingPath", "componentType", "overwriteExisting"}} for row in first]}
            assert flat_rows(group) == clip["curves"]
            totals[2] += len(clip["curves"])
            clip["curves"] = [group]
        assert expand_animation_curve_sets(grouped) == original
        assert build_scene_execution_plan(TOOL, grouped) == build_scene_execution_plan(TOOL, original)
        for value, index in ((original, 0), (grouped, 1)):
            totals[index] += len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert totals == [195766, 12358, 1152]

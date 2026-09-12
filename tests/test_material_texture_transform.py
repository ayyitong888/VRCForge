from copy import deepcopy

import jsonschema
import pytest

import material_texture_assignment as m
from authoritative_unity_writes import prepare_authoritative_unity_write, validate_authoritative_unity_write_result
from unity_shared_input_schemas import MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA as SCHEMA
from test_material_texture_batch import compiled_texture, run_batch, ROWS


def transformed():
    return {**ROWS[0], "textureScale": {"x": 0.5, "y": 0.75}, "textureOffset": {"x": 0.125, "y": -0.25}}


def test_public_single_transform_uses_the_same_supervised_batch_path():
    row = transformed()
    for value in ({"projectPath": "Project", **row}, {"projectPath": "Project", "assignments": [row]}):
        jsonschema.validate(value, SCHEMA)
        wrapper = m.build_wrapper_arguments(value)
        assert wrapper["arguments"] == {"assignments": [row]}
        assert m.build_preview_arguments(wrapper["arguments"]) == {"assignments": [row], "preview": True}
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        m.build_wrapper_arguments({"assignments": ROWS, "textureScale": {"x": 1, "y": 1}})


@pytest.mark.parametrize("bad", [None, {}, [1, 1], {"x": True, "y": 1}, {"x": float("nan"), "y": 1},
                                   {"x": float("inf"), "y": 1}, {"x": 1e100, "y": 1}, {"x": 10**1000, "y": 1}, {"x": 1, "y": 1, "z": 0}])
def test_transform_rejects_nonfinite_or_non_vector_before_preview(bad):
    with pytest.raises(ValueError):
        m.build_wrapper_arguments({**ROWS[0], "textureScale": bad})


@pytest.mark.parametrize("case", ["transform", "transform_single", "transform_nochange", "transform_save", "transform_setter", "transform_invalid", "transform_stale", "transform_scale", "transform_offset"])
def test_real_core_transform_changes_no_texture_identity_and_restores_on_failure(compiled_texture, tmp_path, case):
    result = run_batch(compiled_texture, tmp_path, case)
    payload = result["payload"]
    assert result["globals"] == 0 and result["unrelatedDirty"] is True
    if case in {"transform", "transform_single", "transform_nochange", "transform_scale", "transform_offset"}:
        assert result["ok"] is True, result
        assert payload["changed"] is (case != "transform_nochange")
        assert result["a"]["mask"] == "Assets/Old.png"
        assert result["a"]["scale"] == ([1, 1] if case in {"transform_nochange", "transform_offset"} else [0.5, 0.75])
        assert result["a"]["offset"] == ([0, 0] if case in {"transform_nochange", "transform_scale"} else [0.125, -0.25])
    elif case in {"transform_save", "transform_setter"}:
        assert result["ok"] is False and payload["commitState"] == "rolled_back", result
        for key in ("a", "b", "memoryA", "memoryB"):
            assert result[key]["scale"] == [1, 1] and result[key]["offset"] == [0, 0]
    else:
        assert result["ok"] is False and payload["mutationStarted"] is False and result["saves"] == 0, result


@pytest.mark.parametrize("scale_x,case", [(0.5, "transform"), (0.1, "transform_decimal")])
def test_actual_transform_receipts_bind_request_and_reject_changed_readback(compiled_texture, tmp_path, scale_x, case):
    result = run_batch(compiled_texture, tmp_path, case)
    rows = [{**row, "textureAssetPath": "Assets/Old.png", "textureScale": {"x": scale_x, "y": 0.75},
             "textureOffset": {"x": 0.125, "y": -0.25}} for row in ROWS]
    wrapper = m.build_wrapper_arguments({"projectPath": str(tmp_path), "assignments": rows})
    canonical, _ = prepare_authoritative_unity_write(wrapper, None, lambda _name, _args: result["previewPayload"])
    assert validate_authoritative_unity_write_result(canonical, result["payload"])["changed"] is True
    for key in ("afterTextureScale", "afterTextureOffset"):
        bad = deepcopy(result["payload"])
        bad["readback"][0][key]["x"] = 0.25
        with pytest.raises(ValueError):
            validate_authoritative_unity_write_result(canonical, bad)
    bad = deepcopy(result["previewPayload"])
    bad["assignments"][0]["textureScale"]["x"] = 0.25
    with pytest.raises(ValueError):
        prepare_authoritative_unity_write(wrapper, None, lambda _name, _args: bad)

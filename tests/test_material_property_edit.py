"""Public scalar-edit preparation and sealed readback contracts; no Unity."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

import material_shader_assignment as assignment
from authoritative_unity_writes import prepare_authoritative_unity_write, validate_authoritative_unity_write_result
from unity_shared_input_schemas import MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA
from test_material_scalar_property_edit_runtime import compiled_scalar, run_scalar

REQUEST = {"projectPath": "D:/Project", "materialAssetPath": "Assets/Target.mat",
           "propertyChanges": [{"propertyName": "_DstBlend", "value": 10}, {"propertyName": "_Cutoff", "value": 0.1}], "renderQueue": -1}


def receipt():
    before = {"materialAssetPath": "Assets/Target.mat", "guid": "a" * 32, "fileDigest": "b" * 64, "metaDigest": "c" * 64,
              "state": {"shader": {"name": "Generic", "path": "Assets/S.shader", "guid": "d" * 32, "localId": 1, "dependencyHash": "e" * 32, "renderQueue": 3000},
                        "isVariant": False, "parent": "", "keywords": ["ACTIVE"], "renderQueue": 2000, "rawRenderQueue": 2000,
                        "properties": [{"name": "_DstBlend", "type": "Float", "value": 1}, {"name": "_Cutoff", "type": "Range", "value": 0.5, "range": [0, 1]},
                                       {"name": "_Tint", "type": "Color", "value": [1, 1, 1, 1]}]}}
    impact = {"scope": "loaded_scene_renderers_and_project_scene_prefab_dependencies", "dependencyCandidateCount": 0,
              "loadedRendererSlotCount": 0, "loadedRendererSlots": [], "dependentAssetCount": 0, "dependentAssets": [], "listsTruncated": False}
    display = assignment.compute_shared_impact_digest(impact)
    tail = assignment.compute_shared_impact_tail_digest(impact, slots=[], assets=[])
    before.update(sharedImpact=impact, sharedImpactDigestSchema=assignment.IMPACT_DIGEST_SCHEMA,
                  sharedImpactDisplayDigest=display, sharedImpactTailDigest=tail,
                  sharedImpactDigest=assignment.compute_shared_impact_commitment(impact, display_digest=display, tail_digest=tail))
    after = deepcopy(before["state"])
    after["properties"][0]["value"] = 10
    after["properties"][1]["value"] = 0.1
    after.update(rawRenderQueue=-1, renderQueue=3000)
    return {"schema": "vrcforge.material_property_edit.v1", "ok": True, "preview": True, "verified": True, "mutationStarted": False, "committed": False,
            "propertyChanges": deepcopy(REQUEST["propertyChanges"]), "renderQueue": -1, "before": before, "after": after, "wouldChange": True}


def test_property_mode_public_schema_and_wrapper_forwarding():
    Draft202012Validator(MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA).validate(REQUEST)
    wrapper = assignment.build_wrapper_arguments(REQUEST)
    assert wrapper["arguments"] == {k: REQUEST[k] for k in ("materialAssetPath", "propertyChanges", "renderQueue")}
    assert assignment.build_preview_arguments(wrapper["arguments"])["propertyChanges"] == REQUEST["propertyChanges"]


def test_property_mode_uses_existing_authoritative_preparer_and_readback_validator(tmp_path):
    p = receipt()
    (tmp_path / "Assets").mkdir()
    raw = {**REQUEST, "projectPath": str(tmp_path)}
    canonical, approval = prepare_authoritative_unity_write(assignment.build_wrapper_arguments(raw), None, lambda *_: p)
    args = canonical["arguments"]
    assert args["expectedPropertyEvidence"] == p["before"]
    assert args["expectedProjectPath"] == str(tmp_path)
    assert approval["rollbackRequired"] is True
    final = deepcopy(p)
    final.update(preview=False, mutationStarted=True, committed=True, persistedReadback=True, commitState="committed",
                 readback={**deepcopy(p["before"]), "fileDigest": "f" * 64, "state": deepcopy(p["after"])})
    assert validate_authoritative_unity_write_result(canonical, final) == final
    for field in ("guid", "metaDigest"):
        bad = deepcopy(final); bad["readback"][field] = "0" * len(bad["readback"][field])
        with pytest.raises(ValueError):
            validate_authoritative_unity_write_result(canonical, bad)
    bad = deepcopy(final); bad["readback"]["state"]["keywords"] = []
    with pytest.raises(ValueError):
        validate_authoritative_unity_write_result(canonical, bad)


@pytest.mark.parametrize("extra", [{"shaderName": "Other"}, {"shaderAssetPath": "Assets/S.shader"}, {"assignments": []},
                                   {"keywordChanges": []}, {"rendererPath": "Avatar/Body"}, {"slotIndex": 0}])
def test_property_mode_rejects_mixed_requests_in_public_and_preparer(extra):
    raw = {**REQUEST, **extra}
    assert list(Draft202012Validator(MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA).iter_errors(raw))
    with pytest.raises(ValueError):
        assignment.build_wrapper_arguments(raw)


@pytest.mark.parametrize("rows", [[], [{"propertyName": "_A", "value": True}], [{"propertyName": "_A", "value": float("nan")}],
                                  [{"propertyName": "_A", "value": float("inf")}], [{"propertyName": "_A", "value": 1e100}], [{"propertyName": "_A", "value": 10**400}],
                                  [{"propertyName": "_A", "value": 1}] * 2, [{"propertyName": "_A", "value": 1, "extra": 1}]])
def test_property_mode_rejects_invalid_scalar_requests(rows):
    with pytest.raises(ValueError):
        assignment.build_wrapper_arguments({**REQUEST, "propertyChanges": rows})


@pytest.mark.parametrize("queue", [True, -2, 5001, 1.5])
def test_property_mode_rejects_invalid_queue(queue):
    with pytest.raises(ValueError):
        assignment.build_wrapper_arguments({**REQUEST, "renderQueue": queue})


@pytest.mark.parametrize("tamper", ["unrequested", "shader", "wrongvalue", "range", "type", "queue", "stale"])
def test_property_preview_binds_exact_edit_and_untouched_state(tamper):
    p = receipt(); wrapper = assignment.build_wrapper_arguments(REQUEST)
    if tamper == "unrequested": p["after"]["properties"][2]["value"][0] = 0
    elif tamper == "shader": p["after"]["shader"]["guid"] = "0" * 32
    elif tamper == "wrongvalue": p["after"]["properties"][0]["value"] = 1
    elif tamper == "range": p["before"]["state"]["properties"][1]["range"] = [0.2, 1]
    elif tamper == "type": p["before"]["state"]["properties"][0]["type"] = "Texture"
    elif tamper == "queue": p["after"]["rawRenderQueue"] = 3000
    else: wrapper["arguments"]["expectedPropertyEvidence"] = {"stale": True}
    with pytest.raises(ValueError):
        assignment.bind_authoritative_preview(wrapper, p)


def test_queue_without_property_mode_is_rejected_instead_of_silently_ignored():
    with pytest.raises(ValueError):
        assignment.build_wrapper_arguments({"materialAssetPath": "Assets/A.mat", "shaderName": "S", "renderQueue": 2000})


@pytest.mark.parametrize("value", [1.5, 2**31, -(2**31)-1])
def test_shader_int_rejects_fractional_or_out_of_int32_preview(value):
    raw = {**REQUEST, "propertyChanges": [{"propertyName": "_DstBlend", "value": value}]}
    p = receipt(); p["before"]["state"]["properties"][0]["type"] = "Int"
    p["propertyChanges"] = raw["propertyChanges"]
    with pytest.raises(ValueError):
        assignment.bind_authoritative_preview(assignment.build_wrapper_arguments(raw), p)


def test_shader_int_and_omitted_queue_bind_without_changing_other_fields():
    raw = {"materialAssetPath": REQUEST["materialAssetPath"], "propertyChanges": [{"propertyName": "_DstBlend", "value": 10}]}
    p = receipt(); p.pop("renderQueue"); p["propertyChanges"] = raw["propertyChanges"]
    p["before"]["state"]["properties"][0]["type"] = "Int"
    p["after"] = deepcopy(p["before"]["state"]); p["after"]["properties"][0]["value"] = 10
    args, _ = assignment.bind_authoritative_preview(assignment.build_wrapper_arguments(raw), p)
    assert "renderQueue" not in args["arguments"]


def test_preparer_rejects_nested_mixed_mode_instead_of_dropping_outer_scalar_request():
    with pytest.raises(ValueError):
        assignment.build_wrapper_arguments({**REQUEST, "arguments": {"materialAssetPath": "Assets/Target.mat", "shaderName": "S"}})


def test_float32_roundtrip_and_no_change_preview_preserve_full_seal():
    p = receipt()
    p["after"]["properties"][1]["value"] = 0.10000000149011612
    p["before"]["state"] = deepcopy(p["after"])
    p["wouldChange"] = False
    canonical, _ = assignment.bind_authoritative_preview(assignment.build_wrapper_arguments(REQUEST), p)
    assert canonical["arguments"]["expectedPropertyEvidence"] == p["before"]


def test_property_binding_rejects_tampered_shared_impact_commitment():
    p = receipt(); p["before"]["sharedImpactDigest"] = "0" * 64
    with pytest.raises(ValueError):
        assignment.bind_authoritative_preview(assignment.build_wrapper_arguments(REQUEST), p)


@pytest.mark.parametrize("case", ["success", "nochange"])
def test_real_scalar_core_receipt_through_public_approval_boundary(compiled_scalar, tmp_path, case):
    result = run_scalar(compiled_scalar, tmp_path, case)
    assert result["ok"] is True, result
    preview = result["previewPayload"]
    raw = {"projectPath": str(tmp_path), "materialAssetPath": "Assets/Target.mat",
           "propertyChanges": preview["propertyChanges"], "renderQueue": preview["renderQueue"]}
    canonical, _ = prepare_authoritative_unity_write(assignment.build_wrapper_arguments(raw), None, lambda *_: preview)
    assert canonical["arguments"]["expectedPropertyEvidence"] == preview["before"]
    assert validate_authoritative_unity_write_result(canonical, result["payload"]) == result["payload"]

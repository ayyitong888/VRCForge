import json
import os
from pathlib import Path
import pytest
from optimization_service import build_shader_adapter_registry, build_optimization_tool_result

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = Path(os.environ["VRCFORGE_REGRESSION_ARCHIVE_ROOT"]) if os.environ.get("VRCFORGE_REGRESSION_ARCHIVE_ROOT") else ROOT / ".tmp" / "regression-archives"


def validation(rows):return {"sources":{"materials":{"ok":True,"payload":{"materials":rows}}}}


def test_actual_232_semantic_inventory_matches_registry_coverage():
    path=ARCHIVE_ROOT / "232.json"
    if not path.exists():pytest.skip("private live232 evidence unavailable")
    scan=json.loads(path.read_text(encoding="utf-8-sig"))["results"][2]["structuredContent"]["result"]
    result=build_optimization_tool_result("optimization.shader.adapter-registry",{}, {"sources":{"materials":{"ok":True,"payload":scan}}})["result"]
    coverage=result["materialCoverage"]
    assert len(coverage)==len(scan["materials"])==5
    for actual in coverage:
        source=next(row for row in scan["materials"] if row["material_id"]==actual["materialId"])
        assert actual["slotIndex"]==source["slot_index"]
        assert actual["rendererComponentIndex"]==source["renderer_component_index"]
        assert actual["materialAssetGuid"]==source["material_asset_guid"]
        assert actual["sceneGuid"]==source["renderer_scene_guid"]
        assert actual["dissolveReadiness"]==source["dissolve_readiness"]
        assert actual["dissolveFeatureStatus"]==source["dissolve_feature_status"]
        assert actual["proofStatus"]!="first_class_preview"
        assert not any(name.startswith("dissolve_") for name in actual["safeSemanticProperties"])
        assert "dissolve_border" in actual["familySafeSemanticProperties"]


def test_zero_unknown_slots_and_nonmaterials():
    base={"material_id":"one","material_name":"Valid","shader_name":"lilToon","renderer_component_index":4,
          "material_asset_path":"Assets/Valid.mat","textures":[{"name":"Tex","assetPath":"Assets/Tex.png"}]}
    rows=[{**base,"slot_index":0},{**base,"material_id":"two"},None,{},
          {"name":"Texture","assetPath":"Assets/Texture.png"},{"material_name":"","shader_name":""}]
    result=build_shader_adapter_registry({},validation(rows))
    assert len(result["materialCoverage"])==2
    assert [row["slotIndex"] for row in result["materialCoverage"]]==[0,None]
    assert all(row["rendererComponentIndex"]==4 for row in result["materialCoverage"])
    assert all(row["pathsAreDisplayOnly"] is True for row in result["materialCoverage"])


@pytest.mark.parametrize("mode,readiness,feature,allowed",[("GemOrFur","needs_preparation","unverified",False),("Opaque","needs_preparation","unverified",False),("Cutout","needs_feature_verification","unverified",False),("Cutout","ready","verified",True),("Cutout",None,None,False)])
def test_material_dissolve_proof_is_not_family_support(mode,readiness,feature,allowed):
    result=build_shader_adapter_registry({},validation([{"material_id":"known","material_name":"Material","shader_name":"Hidden/lilToonCutout",
       "dissolve_rendering_mode":mode,"dissolve_readiness":readiness,"dissolve_feature_status":feature}]))
    row=result["materialCoverage"][0]
    assert ("dissolve_border" in row["safeSemanticProperties"]) is allowed
    assert row["familyAdapterProofStatus"]=="first_class_preview"


def test_identified_embedded_material_is_not_mistaken_for_texture():
    rows=[{"material_id":"embedded","material_name":"ImportedMaterial","material_asset_path":"Assets/Model.fbx","shader_name":"Standard","slot_index":0}]
    result=build_shader_adapter_registry({},validation(rows))
    assert result["materialCoverage"][0]["materialId"]=="embedded"

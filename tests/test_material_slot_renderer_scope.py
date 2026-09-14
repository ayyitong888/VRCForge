from optimization_service import build_material_slot_audit, build_ttt_atlas_plan


def test_global_inventory_never_becomes_renderer_slot_array():
    rows = [{"renderer_path": "Avatar/AngelRing/Meshes/AngelRing", "material_name": "AngelRing_Emission", "slot_index": 0}]
    rows += [{"rendererPath": f"Avatar/Other{i}", "materialName": "Material", "slotIndex": 0} for i in range(315)]
    payload = {"gameObjectPath": "Avatar/AngelRing", "inventory": {"materials": rows}, "materials": rows}
    audit = build_material_slot_audit({"sources": {"materials": {"ok": True, "payload": payload}}})
    assert audit["summary"]["knownMaterialSlotCount"] == 316
    assert audit["summary"]["rendererCount"] == 316
    assert audit["atlasGroupHints"] == []
    assert build_ttt_atlas_plan({}, audit, {})["candidateGroups"] == []


def test_exact_renderer_paths_are_grouped_before_display_redaction():
    rows = [{"rendererPath": p, "materialName": "Same", "slotIndex": 0} for p in ("Avatar/A/Ring", "Avatar/B/Ring")]
    audit = build_material_slot_audit({"sources": {"materials": {"payload": {"materials": rows}}}})
    assert audit["summary"]["rendererCount"] == 2
    assert audit["summary"]["knownMaterialSlotCount"] == 2
    assert audit["atlasGroupHints"] == []


def test_renderer_local_slot_array_is_not_double_counted():
    audit = build_material_slot_audit({"sources": {"materials": {"payload": {"renderers": [
        {"rendererPath": "Avatar/Body", "materials": ["A", "B"]},
        {"gameObjectPath": "Avatar/Root", "materials": ["RootIsNotRenderer"]},
    ]}}}})
    assert audit["summary"]["rendererCount"] == 1
    assert audit["summary"]["knownMaterialSlotCount"] == 2
    assert len(audit["atlasGroupHints"]) == 1
    assert audit["atlasGroupHints"][0]["slotCount"] == 2


def test_atlas_candidates_preserve_callable_renderer_identity():
    paths = ["Workspace/FinalAvatar/A/Meshes/Accessories/Ring", "Workspace/FinalAvatar/B/Meshes/Accessories/Ring"]
    rows = [{"rendererPath": path, "rendererComponentId": f"component-{i}", "sceneGuid": "scene-guid", "materialName": f"Mat{slot}", "slotIndex": slot} for i, path in enumerate(paths) for slot in range(2)]
    audit = build_material_slot_audit({"sources": {"materials": {"payload": {"materials": rows}}}})
    groups = build_ttt_atlas_plan({}, audit, {})["candidateGroups"]
    assert [g["rendererPath"] for g in groups] == paths
    assert [g["rendererComponentId"] for g in groups] == ["component-0", "component-1"]
    assert all(g["sceneGuid"] == "scene-guid" and g["slotCount"] == 2 for g in groups)


def test_machine_absolute_paths_are_not_emitted_as_renderer_identity():
    audit = build_material_slot_audit({"sources": {"materials": {"payload": {"renderers": [
        {"rendererPath": "C:/Users/private/Mesh", "materials": ["A", "B"]}
    ]}}}})
    assert audit["atlasGroupHints"] == []

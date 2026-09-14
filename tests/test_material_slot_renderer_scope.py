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


def test_registered_atlas_keeps_renderer_identity_through_validation_projection(monkeypatch, tmp_path):
    import dashboard_server as dashboard
    exact = "Workspace/FinalAvatar/Accessories/Meshes/Body"
    payload = {"projectPath": "C:/Users/private/project", "materials": [
        {"renderer_path": exact, "material_name": f"Mat{i}", "slot_index": i,
         "rendererComponentId": "component-id", "material_path": "C:/Users/private/project/Assets/Mat.mat"}
        for i in range(2)
    ]}
    monkeypatch.setattr(dashboard, "_run_validation_source", lambda name, runner: {"ok": True, "payload": payload if name == "materials" else {}})
    tool = dashboard.AGENT_GATEWAY._tools["vrcforge_optimization_ttt_atlas_plan"]
    result = tool.handler({"projectPath": str(tmp_path), "avatarPath": "Workspace/FinalAvatar"})["result"]
    group = result["candidateGroups"][0]
    assert group["rendererPath"] == exact
    assert group["sceneGuid"] is None
    assert group["rendererComponentId"] == "component-id"
    assert "bind" in group["identityNote"]
    report = dashboard.build_validation_report_sync({"includeSources": True, "includeReadiness": False, "includeQuest": False})
    projected = report["sources"]["materials"]["payload"]
    assert "private" not in str(projected)
    assert dashboard._redact_doctor_detail({"rendererPath": exact})["rendererPath"] != exact


def test_material_projection_only_preserves_legal_renderer_hierarchy_paths():
    import dashboard_server as dashboard
    from validation_report_summary import material_source_payload
    raw = {"materials": [{"rendererPath": value} for value in
           ["C:/Users/private/Mesh", "//host/share/Mesh", "/Users/private/Mesh", ".../Mesh", "../Mesh", "Assets/Avatar/Mesh"]],
           "projectPath": "C:/Users/private/project"}
    safe = material_source_payload(raw, redact_detail=dashboard._redact_doctor_detail)
    assert [r["rendererPath"] for r in safe["materials"]] == [None] * 5 + ["Assets/Avatar/Mesh"]
    assert "private" not in str(safe)
    assert raw["materials"][0]["rendererPath"].startswith("C:/")

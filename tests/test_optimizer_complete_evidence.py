from dataclasses import replace

import pytest


@pytest.mark.parametrize("padding", [0, 500])
def test_registered_compression_keeps_puppet_evidence_after_display_limit(monkeypatch, tmp_path, padding):
    import dashboard_server as dashboard

    controls = [{"parameterName": "Filler", "controlType": "Toggle"} for _ in range(padding)]
    controls.append({"parameterName": "ClothesToggle", "controlType": "TwoAxisPuppet",
                     "subParameters": [{"name": "AxisX"}, {"name": "AxisY"}]})
    validation = {"sources": {
        "parameters": {"payload": {"parameters": [
            {"name": "ClothesToggle", "type": "Bool", "networkSynced": True},
            {"name": "Filler", "type": "Bool", "networkSynced": False},
            {"name": "AxisX", "type": "Float", "networkSynced": False},
            {"name": "AxisY", "type": "Float", "networkSynced": False},
        ]}},
        "menu": {"payload": {"items": controls}},
        "fx": {"payload": {"conditions": [{"parameter": "ClothesToggle", "mode": "If"}]}},
    }}
    monkeypatch.setattr(dashboard.OPTIMIZATION, "_ports", replace(
        dashboard.OPTIMIZATION._ports, build_validation_report=lambda _: validation))
    tool = dashboard.AGENT_GATEWAY._tools["vrcforge_optimization_parameter_compressibility_plan"]
    result = tool.handler({"projectPath": str(tmp_path), "avatarPath": "Fixture"})["result"]
    assert {item["name"] for item in result["categories"]["danger_puppet"]} == {
        "ClothesToggle", "AxisX", "AxisY",
    }
    assert all(item["name"] != "ClothesToggle" for item in result["categories"]["safe_to_pack"])


def test_public_parameter_reports_keep_bounded_output():
    import optimization_service as service

    validation = {"sources": {
        "parameters": {"payload": {"parameters": [{"name": f"P{i}", "type": "Bool"} for i in range(501)]}},
        "menu": {"payload": {"items": [{"parameterName": f"P{i}", "type": "Toggle"} for i in range(501)]}},
    }}
    assert len(service.build_parameter_inventory(validation)["parameters"]) == 500
    assert len(service.build_parameter_menu_map(validation)["controls"]) == 500
    assert len(service.build_parameter_menu_map(validation)["parameterMap"]) == 300
    assert len(service.build_parameter_animator_usage(validation)["parameters"]) == 500


def test_parameter_menu_map_separates_scanner_scene_candidates_and_unknown_configuration():
    import optimization_service as service

    # These rows are copied from the production unity_scan_avatar_controls receipt
    # (chat-before-manual-compact-037, itemCount 58: 39 menu_control + 19 scene_object).
    validation = {"sources": {"menu": {"payload": {
        "itemCount": 58,
        "menuTraversalComplete": True,
        "items": [
            {"displayName": "apron off", "menuPath": "Clothes/apron off", "parameterName": "apron",
             "controlType": "Toggle", "source": "menu_control"},
            {"displayName": "Jacket", "objectPath": "Milltina_Oneesan/CityNoir_Black/Jacket",
             "controlType": "SceneObject", "canToggleSceneObject": True, "source": "scene_object"},
            {"displayName": "OutfitToggleHat", "parameterName": "OutfitToggleHat", "source": "parameter"},
            {"displayName": "Future scanner item", "source": "future_source"},
        ],
    }}}}

    result = service.build_parameter_menu_map(validation)

    assert result["summary"]["knownControlCount"] == 1
    assert [item["displayName"] for item in result["controls"]] == ["apron off"]
    assert result["summary"]["descriptorConfiguration"] == "unknown"
    assert result["summary"]["sceneCandidateCount"] == 1
    assert result["sceneCandidates"][0]["objectPath"].endswith("/Jacket")
    assert result["summary"]["parameterCandidateCount"] == 1
    assert result["parameterCandidates"][0]["parameterName"] == "OutfitToggleHat"
    assert result["summary"]["otherCandidateCount"] == 1
    assert result["otherCandidates"][0]["source"] == "future_source"


def test_parameter_menu_map_reports_explicit_unassigned_descriptor_assets():
    import optimization_service as service

    result = service.build_parameter_menu_map({"sources": {"menu": {"payload": {
        "descriptorConfiguration": {
            "descriptorPresent": True,
            "expressionsMenuAssigned": False,
            "expressionParametersAssigned": False,
        },
        "items": [],
    }}}})

    assert result["summary"]["descriptorConfiguration"] == "menu_unassigned"
    assert result["summary"]["descriptorPresent"] is True
    assert result["summary"]["expressionsMenuAssigned"] is False

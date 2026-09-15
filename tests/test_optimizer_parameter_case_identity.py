"""Registered parameter reports must preserve Unity's case-sensitive identities."""
from dataclasses import replace

import pytest

import dashboard_server as dashboard


@pytest.mark.parametrize("suffix", ["inventory", "compressibility_plan"])
def test_registered_report_retains_case_distinct_float(monkeypatch, suffix):
    parameters = [
        {"name": "ClothesToggle", "valueType": "Bool", "networkSynced": True},
        {"name": "clothestoggle", "valueType": "Float", "networkSynced": True},
    ]
    validation = {"sources": {
        "parameters": {"ok": True, "payload": {"sourceDescriptorUsage": {
            "totalParameters": 2, "totalBitUsage": 9, "parameterNames": parameters,
        }}},
        "menu": {"ok": True, "payload": {"items": []}},
        "fx": {"ok": True, "payload": {"conditions": [{"parameter": "ClothesToggle", "mode": "If"}]}},
    }}
    monkeypatch.setattr(dashboard.OPTIMIZATION, "_ports", replace(
        dashboard.OPTIMIZATION._ports, build_validation_report=lambda _: validation))
    result = dashboard.AGENT_GATEWAY._tools["vrcforge_optimization_parameter_" + suffix].handler({})["result"]
    if suffix == "inventory":
        assert {p["name"] for p in result["parameters"]} == {"ClothesToggle", "clothestoggle"}
        assert result["summary"]["floatCount"] == 1
        assert result["summary"]["syncedParameterCount"] == 2
    else:
        assert result["summary"]["continuousFloatDangerCount"] == 1
        assert [p["name"] for p in result["categories"]["danger_continuous_float"]] == ["clothestoggle"]


def test_registered_inventory_still_deduplicates_same_exact_identity(monkeypatch):
    parameter = {"name": "ClothesToggle", "valueType": "Bool", "networkSynced": True}
    validation = {"sources": {"parameters": {"ok": True, "payload": {
        "parameters": [parameter], "sourceDescriptorUsage": {"parameterNames": [dict(parameter)]},
    }}}}
    monkeypatch.setattr(dashboard.OPTIMIZATION, "_ports", replace(
        dashboard.OPTIMIZATION._ports, build_validation_report=lambda _: validation))
    result = dashboard.AGENT_GATEWAY._tools["vrcforge_optimization_parameter_inventory"].handler({})["result"]
    assert [p["name"] for p in result["parameters"]] == ["ClothesToggle"]

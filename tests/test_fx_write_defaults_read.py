from copy import deepcopy
from unittest.mock import Mock

import dashboard_server as server
from agent_tool_result_reader import scan_result_summary


def test_fx_scan_exposes_write_defaults_before_large_result_paging(monkeypatch):
    states = [{"name": f"State {index}", "state_path": f"Nested/State {index}",
               "write_default_values": index != 0} for index in range(70)]
    payload = {"ok": True, "controller_path": "Assets/FX.controller",
               "summary": {"stateCount": 70}, "layers": [{"name": "FX", "states": states}]}
    original = deepcopy(payload)
    scan = Mock(side_effect=lambda *args, payload_transform=None: payload_transform(payload) if payload_transform else payload)
    monkeypatch.setattr(server, "run_unity_artifact_scan_sync", scan)

    result = server.scan_fx_animator_sync({"avatarPath": "Avatar"})
    assert result["writeDefaultsStatus"] == "mixed"
    assert result["writeDefaultsOnCount"] == 69
    assert result["writeDefaultsOffCount"] == 1
    assert result["writeDefaultsOffExample"] == "FX / Nested/State 0"
    assert result["layers"] == original["layers"]
    assert scan.call_count == 1
    step = {"tool": "vrcforge_scan_fx_animator", "result": result, "scanSnapshot": {}}
    summary = scan_result_summary(step, lambda value, limit: str(value)[:limit])
    assert summary["fields"]["writeDefaultsStatus"] == "mixed"
    assert summary["fields"]["writeDefaultsOffExample"] == "FX / Nested/State 0"



def test_fx_summary_reports_incomplete_coverage_without_inventing_a_convention():
    payload = {
        "summary": {"stateCount": 2},
        "layers": [{"name": "FX", "states": [{"name": "Idle", "write_default_values": True}]}],
    }
    result = server._summarize_fx_write_defaults(payload)
    assert result["writeDefaultsStatus"] == "incomplete"
    assert result["writeDefaultsOnCount"] == 1
    assert result["writeDefaultsOffCount"] == 0


def test_write_defaults_only_keeps_controller_and_evidence_without_bulk_rows(monkeypatch):
    payload = {
        "ok": True, "avatar_path": "Avatar", "controller_path": "Assets/FX.controller",
        "summary": {"stateCount": 1},
        "layers": [{"name": "FX", "states": [{"name": "Idle", "write_default_values": False}]}],
    }
    monkeypatch.setattr(server, "run_unity_artifact_scan_sync",
                        lambda *args, payload_transform: payload_transform(payload))
    result = server.scan_fx_animator_sync({"writeDefaultsOnly": True})
    assert result == {
        "ok": True, "avatar_path": "Avatar", "controller_path": "Assets/FX.controller",
        "writeDefaultsStatus": "all_off", "writeDefaultsOnCount": 0,
        "writeDefaultsOffCount": 1, "writeDefaultsOffExample": "FX / Idle",
    }

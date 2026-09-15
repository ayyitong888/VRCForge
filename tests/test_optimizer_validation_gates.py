"""Failed validation evidence cannot satisfy optimizer acceptance gates."""
from dataclasses import replace

import pytest

import dashboard_server as dashboard
from optimization_service import build_rollback_ecosystem_coverage


def invoke(monkeypatch, suffix, params, report):
    monkeypatch.setattr(dashboard.OPTIMIZATION, "_ports", replace(
        dashboard.OPTIMIZATION._ports, build_validation_report=lambda _: report))
    return dashboard.AGENT_GATEWAY._tools["vrcforge_optimization_" + suffix].handler(params)["result"]


def healthy():
    return {"sources": {name: {"ok": True, "payload": {}} for name in (
        "performance_pc", "performance_quest", "parameters")}}


@pytest.mark.parametrize("phase", ["before", "after", "rollback"])
@pytest.mark.parametrize("source", [{"ok": False, "error": "scan failed"},
                                     {"ok": True, "payload": {"ok": False, "error": "scan failed"}}])
def test_profile_blocks_failed_snapshot(monkeypatch, phase, source):
    params = {name + "Validation": healthy() for name in ("before", "after", "rollback")}
    params[phase + "Validation"]["sources"]["parameters"] = source
    result = invoke(monkeypatch, "profile_diff", params, {})
    assert result["hardGate"]["status"] == "blocked"
    assert "profile." + phase in result["hardGate"]["blockingIds"]


def test_profile_blocks_empty_snapshot(monkeypatch):
    result = invoke(monkeypatch, "profile_diff", {name + "Validation": {} for name in (
        "before", "after", "rollback")}, {})
    assert result["hardGate"]["status"] == "blocked"


@pytest.mark.parametrize("source", [{"ok": False, "error": "scan failed"},
                                     {"ok": True, "payload": {"ok": False, "error": "scan failed"}}])
def test_rollback_blocks_failed_validation(monkeypatch, tmp_path, source):
    report = {"sources": {"fx": source, "generated_residue": {"ok": True, "payload": {"residueCount": 0}}}}
    result = invoke(monkeypatch, "rollback_verify", {"projectPath": str(tmp_path)}, report)
    assert "rollback.post_restore_validation" in result["hardGate"]["blockingIds"]


def test_detected_ecosystem_cannot_claim_failed_validation_is_covered():
    doctor = {"dependencies": [{"id": "modular_avatar", "status": "installed"}]}
    report = {"sources": {"fx": {"ok": False, "error": "scan failed"}}}
    result = build_rollback_ecosystem_coverage(doctor, report)
    component = next(item for item in result["components"] if item["id"] == "modular_avatar")
    assert component["rollbackCovered"] is False
    assert component["coverageStatus"] == "blocked"

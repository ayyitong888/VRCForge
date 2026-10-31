from unittest.mock import Mock

import pytest

import dashboard_server as dashboard


@pytest.mark.parametrize("available", [True, False])
def test_report_reads_sdk_once_and_keeps_panel_coverage_distinct(monkeypatch, available):
    sdk = Mock(return_value={
        "ok": True, "available": available, "exact": available,
        "authoritativeForCurrentCachedPanelAlerts": available,
        "sdkVersion": "3.10.5", "reasonCode": None if available else "sdk_panel_not_open",
        "alerts": [{"scope": "avatar", "category": "error", "severity": "blocking",
                    "blocker": True, "message": "Mesh Body must have Read/Write enabled.",
                    "target": {"hierarchyPath": "Avatar/Body"}}] if available else [],
    })
    monkeypatch.setattr(dashboard, "read_vrchat_sdk_builder_alerts_sync", sdk)

    def source(name, callback):
        payload = callback() if name == "sdk_panel_alerts" else {"ok": True}
        return {"ok": True, "payload": payload}

    monkeypatch.setattr(dashboard, "_run_validation_source", source)
    result = dashboard.build_validation_report_sync({
        "avatarPath": "Avatar", "projectPath": "D:/Project",
        "includeReadiness": False, "includeQuest": False,
    })
    sdk.assert_called_once()
    assert result["sdkControlPanelAlertCoverage"]["status"] == ("read" if available else "unknown")
    assert result["authoritativeForSdkPanelAlerts"] is available
    assert result["gate"]["status"] == ("blocked" if available else "incomplete")
    assert result["summary"]["gateStatus"] == result["gate"]["status"]
    if available:
        finding = next(row for row in result["findings"] if "Read/Write" in row["message"])
        assert finding["severity"] == "Error"
        assert finding["detail"]["target"] == {"hierarchyPath": "Avatar/Body"}
    else:
        assert result["gate"]["checksStatus"] == "pass"
        assert "coverage is unknown" in result["gate"]["message"]

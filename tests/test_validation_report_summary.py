from __future__ import annotations

import ast
from pathlib import Path

import dashboard_server
import validation_report_summary as summary
from sdk_panel_alerts import apply_sdk_panel_coverage, sdk_panel_alert_report


def test_summary_has_no_runtime_owner_dependency() -> None:
    tree = ast.parse(Path(summary.__file__).read_text(encoding="utf-8"))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {"__future__", "typing"}
    assert {item.name for node in ast.walk(tree) if isinstance(node, ast.Import) for item in node.names} == {"re"}
    assert dashboard_server.VALIDATION_SECTION_ORDER is summary.VALIDATION_SECTION_ORDER
    assert dashboard_server._validation_gate is summary._validation_gate


def test_findings_redaction_keeps_root_callback_late_bound(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(dashboard_server, "redact_sensitive", lambda detail: seen.append(detail) or {"redacted": True})
    findings = []
    dashboard_server._validation_add_finding(findings, "Custom section", "unknown", "Title", "Message", "source", {"private": "value"})
    dashboard_server._validation_add_finding(findings, "Unity compile", "Error", "Failure", "Compile failed", "compile")
    assert seen == [{"private": "value"}]
    assert findings[0]["severity"] == "Info"
    assert findings[0]["id"] == "source.1"
    assert findings[0]["detail"] == {"redacted": True}
    assert "preview, approval, checkpoint, apply, validation, and restore" in findings[0]["fixPolicy"]
    assert "detail" not in findings[1]
    sections = dashboard_server._validation_section_summaries(findings, include_all=False)
    assert [section["name"] for section in sections] == ["Unity compile", "Custom section"]
    assert sections[0]["status"] == "error"
    assert sections[1]["id"] == "custom_section"
    assert sections[1]["counts"]["Info"] == 1
    assert summary._validation_gate(findings, enabled=True)["blockingFindingIds"] == ["compile.2"]
    assert summary._validation_gate(findings, enabled=False)["status"] == "pass"


def test_source_summary_is_bounded_before_redaction(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(dashboard_server, "redact_sensitive", lambda value: seen.append(value) or value)
    value = dashboard_server._validation_source_summary({
        "ok": False, "error": "failed", "discard": "hidden", "summary": {str(i): i for i in range(20)},
    })
    assert set(value) == {"ok", "error", "summary"}
    assert len(value["summary"]) == 12
    assert seen == [value]
    assert dashboard_server._validation_source_summary([]) == {"type": "list"}
    assert len(seen) == 1


def test_recursive_counts_and_empty_sections_keep_existing_semantics() -> None:
    payload = {"Count": 2, "nested": [{"COUNT": 3, "Items": [1, 2]}, {"items": [3]}]}
    assert summary._validation_max_number(payload, "count") == 3
    assert summary._validation_list_count(payload, "items") == 3
    assert summary._validation_max_number(payload, "missing") == 0
    sections = summary._validation_section_summaries([])
    assert [section["name"] for section in sections] == list(summary.VALIDATION_SECTION_ORDER)
    assert all(section["status"] == "not_run" for section in sections)


def test_sdk_panel_alert_report_distinguishes_unknown_from_available_zero() -> None:
    unavailable, unknown_findings = sdk_panel_alert_report({
        "available": False,
        "exact": False,
        "authoritativeForCurrentCachedPanelAlerts": False,
        "reasonCode": "sdk_selected_avatar_mismatch",
        "alerts": [],
    }, sdk_version="3.10.4")
    assert unavailable["status"] == "unknown"
    assert unavailable["authoritativeForSdkPanelAlerts"] is False
    assert unavailable["returnedAlertCount"] is None
    assert unknown_findings[0]["severity"] == "Warning"
    assert "unavailable" in unknown_findings[0]["message"]

    clear, clear_findings = sdk_panel_alert_report({
        "available": True,
        "exact": True,
        "authoritativeForCurrentCachedPanelAlerts": True,
        "sdkVersion": "3.10.4",
        "alerts": [],
        "countsByCategory": {"error": 0, "warning": 0},
    })
    assert clear["status"] == "read"
    assert clear["authoritativeForSdkPanelAlerts"] is True
    assert clear["returnedAlertCount"] == 0
    assert len(clear_findings) == 1
    assert clear_findings[0]["severity"] == "Info"
    assert "current SDK panel cache" in clear_findings[0]["message"]


def test_sdk_panel_alert_rows_retain_target_and_only_offer_supervised_guidance() -> None:
    coverage, findings = sdk_panel_alert_report({
        "available": True,
        "exact": True,
        "authoritativeForCurrentCachedPanelAlerts": True,
        "alerts": [{
            "index": 2,
            "scope": "avatar",
            "category": "error",
            "severity": "error",
            "message": "Missing component",
            "target": {"kind": "avatar", "name": "Test"},
            "blocker": True,
            "selectable": True,
            "fixable": True,
            "autoFixAvailable": True,
            "actionsExecuted": False,
        }],
    })
    assert coverage["status"] == "read"
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "Error"
    assert finding["message"] == "avatar: Missing component"
    assert finding["detail"]["target"] == {"kind": "avatar", "name": "Test"}
    assert finding["detail"]["fixable"] is True
    assert finding["detail"]["actionsExecuted"] is False


def test_sdk_panel_coverage_changes_only_report_status_and_preserves_check_status() -> None:
    blocked = apply_sdk_panel_coverage({"enabled": False, "status": "blocked", "message": "blocked"}, {"status": "unknown"})
    assert blocked["status"] == "blocked"
    assert blocked["checksStatus"] == "blocked"
    assert "current cached panel" in blocked["message"]

    unknown = apply_sdk_panel_coverage({"enabled": False, "status": "pass"}, {"status": "unknown"})
    assert unknown["status"] == "incomplete"
    assert unknown["checksStatus"] == "pass"
    assert "Build & Test success" in unknown["message"]

    read = apply_sdk_panel_coverage({"enabled": True, "status": "pass"}, {"status": "read"})
    assert read["status"] == "pass"
    assert read["checksStatus"] == "pass"
    assert "current panel cache" in read["message"]


def test_sdk_panel_report_keeps_performance_alert_as_warning_and_unknown_malformed_shape() -> None:
    coverage, findings = sdk_panel_alert_report({
        "available": True,
        "exact": False,
        "authoritativeForCurrentCachedPanelAlerts": False,
        "alerts": [{"category": "performance", "severity": "performance", "message": "Review draw calls"}],
    })
    assert coverage["status"] == "read"
    assert coverage["exact"] is False
    assert findings[0]["severity"] == "Warning"

    unknown, unknown_findings = sdk_panel_alert_report({"available": True, "alerts": None})
    assert unknown["status"] == "unknown"
    assert unknown_findings[0]["severity"] == "Warning"

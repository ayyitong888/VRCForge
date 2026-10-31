"""Project already-read SDK Control Panel alerts into validation output."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def sdk_panel_alert_report(value: Any, *, sdk_version: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = value if isinstance(value, Mapping) else {}
    alerts_value = raw.get("alerts")
    available = raw.get("available") is True and isinstance(alerts_value, list)
    alerts = alerts_value if isinstance(alerts_value, list) else []
    coverage = {
        "status": "read" if available else "unknown",
        "available": raw.get("available") if isinstance(raw.get("available"), bool) else None,
        "exact": raw.get("exact"),
        "sdkVersion": str(raw.get("sdkVersion") or sdk_version or ""),
        "reasonCode": raw.get("reasonCode") if not available else None,
        "authoritativeForSdkPanelAlerts": raw.get("authoritativeForCurrentCachedPanelAlerts"),
        "readFrom": "current_sdk_control_panel_cache" if available else None,
        "returnedAlertCount": len(alerts) if available else None,
        "countsByCategory": dict(raw.get("countsByCategory")) if isinstance(raw.get("countsByCategory"), Mapping) else None,
    }
    findings: list[dict[str, Any]] = []
    for index, alert in enumerate(alerts):
        if not isinstance(alert, Mapping):
            continue
        category = str(alert.get("category") or "alert").strip().lower()
        source_severity = str(alert.get("severity") or "").strip().lower()
        severity = (
            "Error" if alert.get("blocker") is True or category == "error" or source_severity == "error"
            else "Warning" if category in {"warning", "performance"} or source_severity in {"warning", "performance"}
            else "Info"
        )
        message = str(alert.get("message") or "SDK panel alert text unavailable")
        scope = str(alert.get("scope") or "SDK panel")
        findings.append({
            "severity": severity,
            "title": f"SDK panel {category}",
            "message": f"{scope}: {message}",
            "detail": {
                "index": alert.get("index", index),
                "scope": alert.get("scope"),
                "category": alert.get("category"),
                "target": alert.get("target"),
                "blocker": alert.get("blocker"),
                "selectable": alert.get("selectable"),
                "fixable": alert.get("fixable"),
                "autoFixAvailable": alert.get("autoFixAvailable"),
                "actionsExecuted": alert.get("actionsExecuted"),
            },
        })
    if available and not alerts:
        findings.append({
            "severity": "Info",
            "title": "SDK panel cache has no alerts",
            "message": "No alerts were returned by the current SDK panel cache.",
            "detail": {"alertCount": 0, "sdkVersion": coverage["sdkVersion"]},
        })
    elif not available:
        findings.append({
            "severity": "Warning",
            "title": "SDK panel alert coverage unknown",
            "message": "The current SDK panel alert result is unavailable or incomplete.",
            "detail": {key: coverage[key] for key in ("status", "available", "exact", "sdkVersion", "reasonCode", "authoritativeForSdkPanelAlerts")},
        })
    elif alerts and not findings:
        findings.append({
            "severity": "Warning",
            "title": "SDK panel alert data could not be summarized",
            "message": "The SDK panel returned alert data that could not be summarized.",
            "detail": {"returnedAlertCount": len(alerts), "sdkVersion": coverage["sdkVersion"]},
        })
    return coverage, findings


def apply_sdk_panel_coverage(gate: Mapping[str, Any], coverage: Mapping[str, Any]) -> dict[str, Any]:
    """Adjust report presentation only; never change whether checks ran or execution gates."""
    result = dict(gate)
    checks_status = str(gate.get("status") or "unknown")
    result["checksStatus"] = checks_status
    if checks_status == "blocked":
        result["message"] = (
            str(gate.get("message") or "Validation checks are blocked.")
            + " SDK panel findings reflect the current cached panel; Build & Test was not run."
        )
        return result
    if checks_status == "pass" and coverage.get("status") != "read":
        result["status"] = "incomplete"
        result["message"] = (
            "Validation checks passed, but SDK panel alert coverage is unknown; "
            "this report does not establish Build & Test success."
        )
    elif checks_status == "pass":
        result["message"] = (
            "No blocking validation errors. SDK alerts reflect the current panel cache; "
            "Build & Test was not run."
        )
    return result

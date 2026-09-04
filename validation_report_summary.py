"""Validation report finding and summary presentation, with caller-owned redaction.

This module owns no scanner, transport, execution gate, persistent state or writes.
"""
from __future__ import annotations

import re
from typing import Any, Callable


VALIDATION_SEVERITIES = ("Error", "Warning", "Suggestion", "Info", "Ignored")

VALIDATION_BLOCKING_SEVERITIES = ("Error",)

VALIDATION_SECTION_ORDER = (
    "Unity compile",
    "VRChat SDK",
    "Selected avatar",
    "Hierarchy paths",
    "Animation bindings",
    "Expression parameters",
    "Expression menu",
    "FX animator",
    "Materials / shaders",
    "PhysBones",
    "Contacts",
    "Particles",
    "Performance PC",
    "Performance Quest",
    "Modular Avatar conflicts",
    "VRCFury conflicts",
    "VRCForge Unity plugin",
    "MCP bridge",
    "Package manager",
    "Generated asset residue",
)

VALIDATION_SECTION_IDS = {
    name: re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    for name in VALIDATION_SECTION_ORDER
}

def _validation_severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {severity: sum(1 for finding in findings if finding.get("severity") == severity) for severity in VALIDATION_SEVERITIES}

def _validation_add_finding(
    findings: list[dict[str, Any]],
    section: str,
    severity: str,
    title: str,
    message: str,
    source: str,
    detail: Any = None,
    *,
    redact_detail: Callable[[Any], Any],
) -> None:
    if severity not in VALIDATION_SEVERITIES:
        severity = "Info"
    finding = {
        "id": f"{source}.{len(findings) + 1}",
        "section": section,
        "severity": severity,
        "title": title,
        "message": message,
        "source": source,
        "fixPolicy": "Fixes are separate plans and require preview, approval, checkpoint, apply, validation, and restore.",
    }
    if detail is not None:
        finding["detail"] = redact_detail(detail)
    findings.append(finding)

def _validation_section_status(counts: dict[str, int]) -> str:
    if counts.get("Error"):
        return "error"
    if counts.get("Warning"):
        return "warning"
    if counts.get("Suggestion"):
        return "suggestion"
    if counts.get("Info"):
        return "info"
    if counts.get("Ignored"):
        return "ignored"
    return "not_run"

def _validation_section_summaries(findings: list[dict[str, Any]], include_all: bool = True) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        grouped.setdefault(str(finding.get("section") or "Validation"), []).append(finding)
    names = [
        name
        for name in VALIDATION_SECTION_ORDER
        if include_all or name in grouped
    ] + sorted(name for name in grouped if name not in VALIDATION_SECTION_ORDER)
    return [
        {
            "name": name,
            "id": VALIDATION_SECTION_IDS.get(name) or re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"),
            "status": _validation_section_status(_validation_severity_counts(grouped.get(name, []))),
            "counts": _validation_severity_counts(grouped.get(name, [])),
            "findingIds": [str(item.get("id") or "") for item in grouped.get(name, [])],
        }
        for name in names
    ]

def _validation_gate(findings: list[dict[str, Any]], enabled: bool) -> dict[str, Any]:
    blocking = [
        finding
        for finding in findings
        if str(finding.get("severity") or "") in VALIDATION_BLOCKING_SEVERITIES
    ]
    status = "blocked" if enabled and blocking else "pass"
    return {
        "enabled": bool(enabled),
        "status": status,
        "blockingSeverities": list(VALIDATION_BLOCKING_SEVERITIES),
        "blockingFindingIds": [str(finding.get("id") or "") for finding in blocking],
        "message": (
            f"{len(blocking)} blocking validation error(s) must be resolved before Build & Test."
            if status == "blocked"
            else "No blocking validation errors."
        ),
    }

def _validation_find_numbers(value: Any, names: set[str]) -> list[float]:
    numbers: list[float] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in names and isinstance(item, (int, float)):
                numbers.append(float(item))
            numbers.extend(_validation_find_numbers(item, names))
    elif isinstance(value, list):
        for item in value:
            numbers.extend(_validation_find_numbers(item, names))
    return numbers

def _validation_find_lists(value: Any, names: set[str]) -> list[list[Any]]:
    lists: list[list[Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in names and isinstance(item, list):
                lists.append(item)
            lists.extend(_validation_find_lists(item, names))
    elif isinstance(value, list):
        for item in value:
            lists.extend(_validation_find_lists(item, names))
    return lists

def _validation_max_number(value: Any, *names: str) -> float:
    found = _validation_find_numbers(value, {name.lower() for name in names})
    return max(found) if found else 0.0

def _validation_list_count(value: Any, *names: str) -> int:
    return sum(len(items) for items in _validation_find_lists(value, {name.lower() for name in names}))

def _validation_source_summary(payload: Any, *, redact_detail: Callable[[Any], Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    summary: dict[str, Any] = {}
    for key in (
        "ok",
        "avatarPath",
        "error",
        "errorCount",
        "warningCount",
        "suggestionCount",
        "parameterCount",
        "controlCount",
        "materialCount",
        "wardrobeCount",
        "wardrobeCandidateCount",
        "looseControlCount",
        "rank",
        "performanceRank",
        "overallRank",
        "jsonPath",
    ):
        if key in payload:
            summary[key] = payload.get(key)
    nested_summary = payload.get("summary")
    if isinstance(nested_summary, dict):
        summary["summary"] = {key: nested_summary.get(key) for key in list(nested_summary.keys())[:12]}
    return redact_detail(summary)

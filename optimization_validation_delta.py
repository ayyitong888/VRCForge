from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

VALIDATION_DELTA_SEVERITIES = ("Error", "Warning", "Suggestion", "Info", "Ignored")


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _validation_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validation_delta_counts(report: dict[str, Any]) -> dict[str, int]:
    summary = _ensure_dict(report.get("summary"))
    counts = _ensure_dict(summary.get("severityCounts"))
    return {severity: int(counts.get(severity) or 0) for severity in VALIDATION_DELTA_SEVERITIES}


def _validation_delta_sections(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for item in report.get("sections") or []:
        if not isinstance(item, dict):
            continue
        section_id = str(item.get("id") or item.get("name") or "").strip()
        if not section_id:
            continue
        sections[section_id] = {
            "id": section_id,
            "name": item.get("name") or section_id,
            "status": item.get("status") or "unknown",
            "counts": {severity: int(_ensure_dict(item.get("counts")).get(severity) or 0) for severity in VALIDATION_DELTA_SEVERITIES},
        }
    return sections


def _validation_delta_finding_key(finding: dict[str, Any]) -> str:
    parts = [
        str(finding.get("id") or ""),
        str(finding.get("section") or finding.get("sectionId") or ""),
        str(finding.get("severity") or ""),
        str(finding.get("title") or finding.get("message") or ""),
        str(finding.get("source") or ""),
    ]
    normalized = "|".join(re.sub(r"\s+", " ", part.strip().lower()) for part in parts)
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _validation_delta_findings(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in report.get("findings") or []:
        if not isinstance(item, dict):
            continue
        key = _validation_delta_finding_key(item)
        result[key] = {
            "key": key,
            "id": item.get("id"),
            "section": item.get("section") or item.get("sectionId"),
            "severity": item.get("severity"),
            "title": item.get("title") or item.get("message"),
            "source": item.get("source"),
        }
    return result


def _validation_delta_summary(report: dict[str, Any]) -> dict[str, Any]:
    summary = _ensure_dict(report.get("summary"))
    return {
        "schema": report.get("schema"),
        "ok": bool(report.get("ok", True)),
        "gateStatus": _ensure_dict(report.get("gate")).get("status") or summary.get("gateStatus"),
        "severityCounts": _validation_delta_counts(report),
        "findingCount": int(summary.get("findingCount") or len(report.get("findings") or [])),
        "failedSourceCount": int(summary.get("failedSourceCount") or 0),
        "generatedAt": report.get("generatedAt"),
    }


def _validation_delta_count_change(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {severity: int(after.get(severity, 0)) - int(before.get(severity, 0)) for severity in VALIDATION_DELTA_SEVERITIES}


def _validation_delta_status(before: dict[str, Any], after: dict[str, Any], rollback: dict[str, Any]) -> str:
    before_counts = _ensure_dict(before.get("severityCounts"))
    after_counts = _ensure_dict(after.get("severityCounts"))
    before_gate = str(before.get("gateStatus") or "")
    after_gate = str(after.get("gateStatus") or "")
    rollback_counts = _ensure_dict(rollback.get("severityCounts"))
    error_delta = int(after_counts.get("Error") or 0) - int(before_counts.get("Error") or 0)
    warning_delta = int(after_counts.get("Warning") or 0) - int(before_counts.get("Warning") or 0)
    suggestion_delta = int(after_counts.get("Suggestion") or 0) - int(before_counts.get("Suggestion") or 0)
    if after_gate == "blocked" and before_gate != "blocked":
        return "regressed"
    if error_delta > 0 or warning_delta > 0:
        return "regressed"
    if error_delta < 0 or warning_delta < 0 or suggestion_delta < 0:
        return "improved"
    if rollback_counts and rollback_counts != before_counts:
        return "rollback-drift"
    return "unchanged"


def _validation_delta_source_payload(report: dict[str, Any], source_name: str) -> dict[str, Any]:
    source = _ensure_dict(_ensure_dict(report.get("sources")).get(source_name))
    payload = _ensure_dict(source.get("payload"))
    return payload or _ensure_dict(source.get("summary"))


def _validation_delta_walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _validation_delta_walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _validation_delta_walk_dicts(child)


def _validation_delta_normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _validation_delta_first_numeric(value: Any, names: tuple[str, ...]) -> int | float | None:
    wanted = {_validation_delta_normalize_key(name) for name in names}
    for entry in _validation_delta_walk_dicts(value):
        for key, raw in entry.items():
            if _validation_delta_normalize_key(str(key)) not in wanted or isinstance(raw, bool):
                continue
            if isinstance(raw, (int, float)):
                return raw
            if isinstance(raw, list):
                return len(raw)
            if isinstance(raw, str):
                match = re.search(r"-?\d+(?:\.\d+)?", raw.replace(",", ""))
                if match:
                    number = float(match.group(0))
                    return int(number) if number.is_integer() else number
    return None


def _validation_delta_first_text(value: Any, names: tuple[str, ...]) -> str | None:
    wanted = {_validation_delta_normalize_key(name) for name in names}
    for entry in _validation_delta_walk_dicts(value):
        for key, raw in entry.items():
            if _validation_delta_normalize_key(str(key)) in wanted and raw is not None:
                text = str(raw).strip()
                if text:
                    return text
    return None


def _validation_delta_platform_profile(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": _validation_delta_first_text(payload, ("rank", "performanceRank", "overallRank", "rating")) or "unknown",
        "triangles": _validation_delta_first_numeric(payload, ("triangleCount", "triangles", "polygonCount", "polygons")),
        "materialSlots": _validation_delta_first_numeric(payload, ("materialSlotCount", "slotCount", "materialCount")),
        "skinnedMeshes": _validation_delta_first_numeric(payload, ("skinnedMeshCount", "skinnedMeshes", "skinnedMeshRendererCount")),
        "textureMemoryBytes": _validation_delta_first_numeric(payload, ("textureMemoryBytes", "textureBytes", "vramBytes", "totalTextureBytes", "totalVRAMBytes")),
        "downloadSizeBytes": _validation_delta_first_numeric(payload, ("downloadSizeBytes", "downloadSize", "compressedSizeBytes", "buildSizeBytes", "fileSizeBytes")),
        "uncompressedSizeBytes": _validation_delta_first_numeric(payload, ("uncompressedSizeBytes", "uncompressedSize", "uncompressedBytes", "bundleUncompressedSizeBytes")),
        "physBoneComponents": _validation_delta_first_numeric(payload, ("physBoneCount", "physBones", "physBoneComponents")),
        "physBoneAffectedTransforms": _validation_delta_first_numeric(payload, ("physBoneAffectedTransforms", "affectedTransforms")),
    }


def _validation_delta_parameter_profile(report: dict[str, Any]) -> dict[str, Any]:
    payload = _validation_delta_source_payload(report, "parameters")
    parameter_items = _validation_delta_first_numeric(payload, ("totalParameters", "totalCustomParameters", "parameterCount", "customParameterCount"))
    return {
        "syncedBits": _validation_delta_first_numeric(payload, ("syncedBits", "bitsUsed", "totalEstimatedCost", "totalCost", "parameterCost")),
        "totalCustomParameters": parameter_items,
    }


def _validation_delta_profile_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "pc": _validation_delta_platform_profile(_validation_delta_source_payload(report, "performance_pc")),
        "quest": _validation_delta_platform_profile(_validation_delta_source_payload(report, "performance_quest")),
        "parameters": _validation_delta_parameter_profile(report),
    }


def _validation_delta_numeric_delta(before: Any, after: Any) -> int | float | None:
    if before is None or after is None:
        return None
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        delta = after - before
        return int(delta) if isinstance(delta, float) and delta.is_integer() else delta
    return None


def _validation_delta_platform_delta(before: dict[str, Any], after: dict[str, Any], rollback: dict[str, Any]) -> dict[str, Any]:
    numeric_keys = [
        "triangles",
        "materialSlots",
        "skinnedMeshes",
        "textureMemoryBytes",
        "downloadSizeBytes",
        "uncompressedSizeBytes",
        "physBoneComponents",
        "physBoneAffectedTransforms",
    ]
    return {
        "rankBefore": before.get("rank") or "unknown",
        "rankAfter": after.get("rank") or "unknown",
        "rankRollback": rollback.get("rank") if rollback else None,
        "rankChanged": (before.get("rank") or "unknown") != (after.get("rank") or "unknown"),
        "rollbackRankMatchesBefore": bool(rollback) and (rollback.get("rank") or "unknown") == (before.get("rank") or "unknown"),
        "metricsDelta": {key: _validation_delta_numeric_delta(before.get(key), after.get(key)) for key in numeric_keys},
        "rollbackMetricsMatchBefore": bool(rollback)
        and all(rollback.get(key) == before.get(key) for key in numeric_keys if before.get(key) is not None or rollback.get(key) is not None),
    }


def _validation_delta_profile_diff(before_report: dict[str, Any], after_report: dict[str, Any], rollback_report: dict[str, Any]) -> dict[str, Any]:
    before = _validation_delta_profile_snapshot(before_report)
    after = _validation_delta_profile_snapshot(after_report)
    rollback = _validation_delta_profile_snapshot(rollback_report) if rollback_report else {}
    before_params = _ensure_dict(before.get("parameters"))
    after_params = _ensure_dict(after.get("parameters"))
    rollback_params = _ensure_dict(rollback.get("parameters"))
    parameter_delta = {
        "syncedBitsDelta": _validation_delta_numeric_delta(before_params.get("syncedBits"), after_params.get("syncedBits")),
        "totalCustomParametersDelta": _validation_delta_numeric_delta(before_params.get("totalCustomParameters"), after_params.get("totalCustomParameters")),
        "rollbackMatchesBefore": bool(rollback_report)
        and rollback_params.get("syncedBits") == before_params.get("syncedBits")
        and rollback_params.get("totalCustomParameters") == before_params.get("totalCustomParameters"),
    }
    return {
        "readOnly": True,
        "before": before,
        "after": after,
        "rollback": rollback,
        "pc": _validation_delta_platform_delta(_ensure_dict(before.get("pc")), _ensure_dict(after.get("pc")), _ensure_dict(rollback.get("pc"))),
        "quest": _validation_delta_platform_delta(_ensure_dict(before.get("quest")), _ensure_dict(after.get("quest")), _ensure_dict(rollback.get("quest"))),
        "parameters": parameter_delta,
    }


def build_optimization_validation_delta(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    before_report = _ensure_dict(params.get("beforeValidation") or params.get("before_validation") or params.get("before") or {})
    after_report = _ensure_dict(params.get("afterValidation") or params.get("after_validation") or params.get("after") or {})
    rollback_report = _ensure_dict(params.get("rollbackValidation") or params.get("rollback_validation") or params.get("rollback") or {})
    before = _validation_delta_summary(before_report)
    after = _validation_delta_summary(after_report)
    rollback = _validation_delta_summary(rollback_report) if rollback_report else {}
    before_findings = _validation_delta_findings(before_report)
    after_findings = _validation_delta_findings(after_report)
    rollback_findings = _validation_delta_findings(rollback_report) if rollback_report else {}
    before_sections = _validation_delta_sections(before_report)
    after_sections = _validation_delta_sections(after_report)
    section_deltas = []
    for section_id in sorted(set(before_sections) | set(after_sections)):
        before_section = before_sections.get(section_id) or {"id": section_id, "counts": {}}
        after_section = after_sections.get(section_id) or {"id": section_id, "counts": {}}
        section_deltas.append(
            {
                "id": section_id,
                "name": after_section.get("name") or before_section.get("name") or section_id,
                "beforeStatus": before_section.get("status"),
                "afterStatus": after_section.get("status"),
                "severityDelta": _validation_delta_count_change(
                    _ensure_dict(before_section.get("counts")),
                    _ensure_dict(after_section.get("counts")),
                ),
            }
        )
    added_keys = sorted(set(after_findings) - set(before_findings))
    removed_keys = sorted(set(before_findings) - set(after_findings))
    persistent_keys = sorted(set(before_findings) & set(after_findings))
    rollback_matches_before = bool(rollback_report) and rollback.get("severityCounts") == before.get("severityCounts") and rollback.get("gateStatus") == before.get("gateStatus")
    status = _validation_delta_status(before, after, rollback)
    profile_diff = _validation_delta_profile_diff(before_report, after_report, rollback_report)
    return {
        "ok": status not in {"regressed", "rollback-drift"},
        "schema": "vrcforge.optimization.validation_delta.v1",
        "readOnly": True,
        "noProjectWrites": True,
        "generatedAt": _validation_now(),
        "optimizerTool": str(params.get("optimizerTool") or params.get("optimizer_tool") or ""),
        "approvalId": str(params.get("approvalId") or params.get("approval_id") or ""),
        "checkpointId": str(params.get("checkpointId") or params.get("checkpoint_id") or ""),
        "status": status,
        "before": before,
        "after": after,
        "rollback": rollback,
        "severityDelta": _validation_delta_count_change(
            _ensure_dict(before.get("severityCounts")),
            _ensure_dict(after.get("severityCounts")),
        ),
        "findingDelta": {
            "addedCount": len(added_keys),
            "removedCount": len(removed_keys),
            "persistentCount": len(persistent_keys),
            "added": [after_findings[key] for key in added_keys[:50]],
            "removed": [before_findings[key] for key in removed_keys[:50]],
        },
        "profileDiff": profile_diff,
        "parameterBudgetDelta": profile_diff.get("parameters"),
        "sectionDeltas": section_deltas,
        "rollbackProof": {
            "provided": bool(rollback_report),
            "matchesBeforeSeverityAndGate": rollback_matches_before,
            "remainingFindingCount": len(rollback_findings) if rollback_report else None,
        },
        "policy": {
            "deltaIsReadOnly": True,
            "optimizerApplyStillRequiresApprovalCheckpointValidationRollback": True,
            "externalAgentsMayGenerateReports": True,
            "externalAgentsMustNotApplyDirectly": True,
        },
    }

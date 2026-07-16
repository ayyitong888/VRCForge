from __future__ import annotations

from typing import Any

from diagnostic_logging import normalize_log_level


SAFETY_POSTURE_SCHEMA = "vrcforge.safety-posture.v1"
TRACE_REQUIRES_DEVELOPER_OPTIONS = True
STANDARD_LOG_LEVELS = ("error", "warn", "info", "debug")
DEVELOPER_LOG_LEVELS = (*STANDARD_LOG_LEVELS, "trace")
_EXECUTION_MODES = {"approval", "auto", "roslyn_full_auto"}


def available_log_levels(developer_options_enabled: bool) -> list[str]:
    levels = DEVELOPER_LOG_LEVELS if developer_options_enabled else STANDARD_LOG_LEVELS
    return list(levels)


def permission_security_state(permission: dict[str, Any] | None) -> dict[str, Any]:
    permission = permission if isinstance(permission, dict) else {}
    mode = str(permission.get("executionMode") or "approval").strip().lower()
    if mode not in _EXECUTION_MODES:
        mode = "approval"
    return {
        "executionMode": mode,
        "perActionApproval": bool(permission.get("perActionApproval", mode == "approval")),
        "autoApprove": bool(permission.get("autoApprove", mode in {"auto", "roslyn_full_auto"})),
        "autoApproveDangerousRequiresApproval": bool(
            permission.get("autoApproveDangerousRequiresApproval", mode == "auto")
        ),
        "fullPermission": mode == "roslyn_full_auto",
        "roslynRiskAcknowledged": bool(permission.get("roslynRiskAcknowledged")),
        "allowWriteRequests": bool(permission.get("allowWriteRequests")),
    }


def advanced_security_state(advanced: dict[str, Any] | None) -> dict[str, bool]:
    advanced = advanced if isinstance(advanced, dict) else {}
    developer_enabled = bool(advanced.get("developerOptionsEnabled"))
    return {
        "developerOptionsEnabled": developer_enabled,
        "developerOptionsEverEnabled": bool(advanced.get("developerOptionsEverEnabled")),
        "computerUseEnabled": bool(advanced.get("computerUseEnabled")) and developer_enabled,
        "computerUseEverEnabled": bool(advanced.get("computerUseEverEnabled")),
    }


def changed_safety_flags(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))


def build_safety_posture(
    permission: dict[str, Any] | None,
    advanced: dict[str, Any] | None,
    log_level: Any,
) -> dict[str, Any]:
    permission = permission if isinstance(permission, dict) else {}
    advanced = advanced if isinstance(advanced, dict) else {}
    mode = str(permission.get("executionMode") or "approval").strip().lower()
    if mode not in _EXECUTION_MODES:
        mode = "approval"
    developer_enabled = bool(advanced.get("developerOptionsEnabled"))
    current_level = normalize_log_level(log_level)
    full_permission = mode == "roslyn_full_auto"
    return {
        "schema": SAFETY_POSTURE_SCHEMA,
        "execution": {
            "mode": mode,
            "allowWriteRequests": bool(permission.get("allowWriteRequests")),
            "fullPermission": full_permission,
        },
        "approval": {
            "perAction": bool(permission.get("perActionApproval", mode == "approval")),
            "autoApprove": bool(permission.get("autoApprove", mode in {"auto", "roslyn_full_auto"})),
            "dangerousAutoApprovalRequiresApproval": bool(
                permission.get("autoApproveDangerousRequiresApproval", mode == "auto")
            ),
        },
        "checkpoint": {
            "requiredBeforeOrdinaryWrite": True,
            "restoreAndRecoveryExempt": True,
        },
        "rollback": {
            "availableForCheckpointedWrite": True,
            "approvalRecordRequired": True,
            "manualApprovalRequired": not full_permission,
            "restoreMayAutoExecute": full_permission,
        },
        "externalAgent": {
            "requestOnly": True,
            "directApplyExposed": False,
            "requestMayAutoExecute": mode in {"auto", "roslyn_full_auto"},
            "selectedMode": mode,
        },
        "developerOptions": {
            "enabled": developer_enabled,
            "everEnabled": bool(advanced.get("developerOptionsEverEnabled")),
            "strongConfirmationRequired": True,
        },
        "computerUse": {
            "enabled": bool(advanced.get("computerUseEnabled")) and developer_enabled,
            "everEnabled": bool(advanced.get("computerUseEverEnabled")),
            "developerOptionsRequired": True,
            "explicitTurnGrantRequired": True,
        },
        "fullPermission": {
            "enabled": full_permission,
            "everEnabled": bool(permission.get("roslynFullAutoEverEnabled")),
            "riskAcknowledged": bool(permission.get("roslynRiskAcknowledged")),
            "canOverrideExplicitApproval": full_permission,
        },
        "diagnostics": {
            "currentLevel": current_level,
            "traceActive": current_level == "trace",
            "traceAllowed": developer_enabled,
            "traceRequiresDeveloperOptions": TRACE_REQUIRES_DEVELOPER_OPTIONS,
        },
    }


__all__ = [
    "DEVELOPER_LOG_LEVELS",
    "SAFETY_POSTURE_SCHEMA",
    "STANDARD_LOG_LEVELS",
    "TRACE_REQUIRES_DEVELOPER_OPTIONS",
    "advanced_security_state",
    "available_log_levels",
    "build_safety_posture",
    "changed_safety_flags",
    "permission_security_state",
]

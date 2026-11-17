"""Risk and mandatory confirmation rules for both write entry points."""

from __future__ import annotations

from typing import Any, Mapping

from agent_gateway import AgentGatewayError, AgentWriteHandler, normalize_risk_level


CHECKPOINT_RESTORE_MANUAL_APPROVAL_REASON = (
    "Checkpoint restore always requires manual user approval because it can remove "
    "or replace project files."
)
_CHECKPOINT_RESTORE_AUTO_LAUNCH_BLOCKED_REASON = (
    "Checkpoint restore automatic launch is blocked after three proven zero-effect failures; "
    "a new invocation requires explicit manual user approval."
)
_AVATAR_UPLOAD_MANUAL_APPROVAL_REASON = (
    "VRChat avatar upload always requires one exact user confirmation because remote metadata, "
    "visibility, thumbnail, or bundle changes cannot be undone by a local checkpoint."
)
_ROLLBACK_MANUAL_APPROVAL_TOOLS = frozenset({
    "vrcforge_rollback_parameters",
    "vrcforge_rollback_project_lifecycle",
    "vrcforge_rollback_project_catalog_registration",
})
_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def resolve_write_risk(
    handler: AgentWriteHandler, arguments: Mapping[str, Any],
) -> tuple[str, str]:
    """Return declared and effective risk. A classifier may only increase it."""
    base = normalize_risk_level(handler.risk_level)
    if handler.risk_level_resolver is None:
        return base, base
    try:
        resolved = str(handler.risk_level_resolver(dict(arguments)) or "").strip().lower()
    except Exception as exc:
        raise AgentGatewayError(
            f"{type(exc).__name__}: {exc} "
            f"(Could not determine write risk for {handler.name}.)",
            status_code=500,
        ) from exc
    if resolved not in _RISK_RANK:
        raise AgentGatewayError(
            f"Write risk resolver returned an invalid level for {handler.name}.",
            status_code=500,
        )
    return base, resolved if _RISK_RANK[resolved] > _RISK_RANK[base] else base


def mandatory_write_confirmation(
    target: str,
    full_permission: bool,
    handler_reason: str,
    matched_restore_recovery: Mapping[str, Any] | None = None,
) -> tuple[str, bool]:
    """Return the mandatory reason and whether a matching recovery permits auto restore."""
    if target == "vrcforge_install_user_unity_tools":
        return handler_reason, False
    if target == "vrcforge_restore_checkpoint":
        if not full_permission or matched_restore_recovery is None:
            return CHECKPOINT_RESTORE_MANUAL_APPROVAL_REASON, False
        if matched_restore_recovery.get("zeroEffectAutoLaunchBlocked"):
            return _CHECKPOINT_RESTORE_AUTO_LAUNCH_BLOCKED_REASON, False
        return "", True
    if target in _ROLLBACK_MANUAL_APPROVAL_TOOLS:
        return "Rollback operations always require explicit manual user confirmation.", False
    if target == "vrcforge_build_and_upload_avatar" and not full_permission:
        return _AVATAR_UPLOAD_MANUAL_APPROVAL_REASON, False
    return "", False

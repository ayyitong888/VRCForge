from __future__ import annotations

import hmac
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from optimization_service import (
    OPTIMIZATION_GATEWAY_TOOL_NAMES,
    OPTIMIZATION_TOOL_DEFINITIONS,
    STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES,
)


ToolHandler = Callable[[dict[str, Any]], Any]


class AgentGatewayError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class AgentGatewayConfig:
    enabled: bool = False
    require_token: bool = True
    token: str = ""
    approval_token: str = ""
    allow_write_requests: bool = True
    allow_roslyn_advanced: bool = False
    approval_timeout_seconds: int = 600
    execution_mode: str = "approval"
    roslyn_risk_acknowledged: bool = False


@dataclass
class AgentTool:
    name: str
    description: str
    category: str
    handler: ToolHandler
    write: bool = False
    advanced: bool = False


@dataclass
class AgentWriteHandler:
    name: str
    description: str
    risk_level: str
    handler: ToolHandler
    advanced: bool = False


@dataclass
class UserConstraintsSnapshot:
    path: Path
    content: str = ""
    status: str = "ok"
    message: str = "No user constraints configured."
    error: str = ""


RUNTIME_DIRECT_SKILL_CATEGORIES = {"read/debug", "plan/preview"}
RUNTIME_BLOCKED_SKILLS = {
    "vrcforge_agent_message",
    "vrcforge_execute_shell",
    "vrcforge_execute_approved_shell",
    "vrcforge_request_apply",
    "vrcforge_apply_approved",
    "vrcforge_restore_last_backup",
    "vrcforge_request_roslyn_advanced",
}
EXTERNAL_AGENT_INTERNAL_TOOLS = {
    "vrcforge_apply_approved",
    "vrcforge_execute_approved_shell",
}
WRAPPER_ONLY_WRITE_TARGETS = {
    "vrcforge_configure_optimizer_component",
    "vrcforge_install_vpm_package",
}

SKILL_PERMISSION_MODES = {"read_only", "preview", "approval_required", "advanced_power_mode", "instruction_only"}
SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,80}$")
SKILL_INVOCATION_RE = re.compile(r"^\s*[/$]([a-zA-Z][a-zA-Z0-9_.-]{1,80})(?:\s+(.*))?\s*$")

BUILTIN_SKILL_OVERRIDES: dict[str, dict[str, Any]] = {
    "vrcforge_skill_manifest": {
        "title": "Skill Registry",
        "inputs": [],
        "outputs": ["Registered skill metadata and availability."],
        "sideEffects": "none",
    },
    "vrcforge_skill_check": {
        "title": "Skill Registry Check",
        "inputs": [],
        "outputs": ["Per-skill validation status and dependency reasons."],
        "sideEffects": "none",
    },
    "vrcforge_scan_project_index": {
        "title": "Project Index Scan",
        "inputs": ["Unity project path and optional file limit."],
        "outputs": ["Local file metadata deltas, package fingerprints, GUID map count, and affected scanner families."],
        "sideEffects": "updates VRCForge local project index only",
        "tags": ["project", "incremental-scan", "local-index"],
    },
    "vrcforge_inspect_outfit_package": {
        "title": "Outfit Package Inspection",
        "inputs": ["Local .unitypackage, Booth ZIP/folder, or loose prefab/texture folder path."],
        "outputs": ["Structural package summary, candidate UnityPackages, prefabs, textures, materials, models, and warnings."],
        "sideEffects": "reads archive directory and UnityPackage pathname metadata only",
        "tags": ["outfit", "booth", "unitypackage", "inspection"],
    },
    "vrcforge_plan_outfit_import": {
        "title": "Outfit Import Plan",
        "inputs": ["Local .unitypackage, Booth folder, or loose prefab/texture folder path plus optional Unity project path."],
        "outputs": ["Supervised import plan, selected package/prefab, expected assets, write target, and rollback requirements."],
        "sideEffects": "none",
        "tags": ["outfit", "booth", "unitypackage", "preview"],
    },
    "vrcforge_unity_status": {
        "title": "Unity MCP Status",
        "inputs": ["Optional Unity MCP host, port, and instance."],
        "outputs": ["MCP reachability, active Unity instance, and selected project status."],
        "sideEffects": "none",
    },
    "vrcforge_unity_tools": {
        "title": "Unity Tool Diagnostics",
        "outputs": ["Tool counts, VRCForge tool counts, and missing required Unity tools."],
        "sideEffects": "none",
    },
    "vrcforge_list_avatars": {
        "title": "Avatar Discovery",
        "outputs": ["Avatar names and scene paths from the active Unity instance."],
        "sideEffects": "none",
    },
    "vrcforge_capture_screenshot": {
        "title": "Gesture/Game View Capture",
        "inputs": ["Capture angle, dimensions, and optional avatar path."],
        "outputs": ["Screenshot path and capture diagnostics."],
        "sideEffects": "writes artifact image only",
    },
    "vrcforge_scan_modular_avatar": {
        "title": "Modular Avatar Scan",
        "inputs": ["Optional project path, avatar path, and skip_unity flag."],
        "outputs": ["Package install state, component carriers, and integration hints."],
        "sideEffects": "none",
        "tags": ["modular-avatar", "addon"],
    },
    "vrcforge_scan_vrcfury": {
        "title": "VRCFury Scan",
        "inputs": ["Optional project path, avatar path, and skip_unity flag."],
        "outputs": ["Package install state, component carriers, and integration hints."],
        "sideEffects": "none",
        "tags": ["vrcfury", "addon"],
    },
    "vrcforge_scan_avatar_items": {
        "title": "Avatar Item Scan",
        "inputs": ["Optional avatar path and max item count."],
        "outputs": ["Hierarchy items with component types and wardrobe hints."],
        "sideEffects": "writes artifact JSON only",
        "tags": ["avatar", "scan", "wardrobe"],
    },
    "vrcforge_scan_fx_animator": {
        "title": "FX Animator Scan",
        "inputs": ["Optional avatar path or animator controller path."],
        "outputs": ["FX layers, states, transitions, and parameters."],
        "sideEffects": "writes artifact JSON only",
        "tags": ["animator", "scan"],
    },
    "vrcforge_scan_animation_bindings": {
        "title": "Animation Binding Scan",
        "inputs": ["Optional avatar path, controller path, clip paths, and max clip count."],
        "outputs": ["Animation clip property bindings and target paths."],
        "sideEffects": "writes artifact JSON only",
        "tags": ["animation", "scan"],
    },
    "vrcforge_scan_avatar_controls": {
        "title": "Expression Menu Scan",
        "inputs": ["Optional avatar path."],
        "outputs": ["Expression menu controls and linked parameters."],
        "sideEffects": "writes artifact JSON only",
        "tags": ["menu", "scan"],
    },
    "vrcforge_scan_wardrobe": {
        "title": "Wardrobe Scan",
        "inputs": ["Optional avatar path."],
        "outputs": ["Int-exclusive wardrobe(s): outfit values, menu toggles, FX states, per-clip object on/off toggles, and Write Defaults flags."],
        "sideEffects": "writes artifact JSON only",
        "tags": ["wardrobe", "menu", "animator", "scan"],
    },
    "vrcforge_scan_parameters": {
        "title": "Expression Parameter Scan",
        "inputs": ["Optional avatar path."],
        "outputs": ["Expression parameter usage and animator parameter links."],
        "sideEffects": "writes artifact JSON only",
        "tags": ["parameter", "scan"],
    },
    "vrcforge_create_safe_backup": {
        "title": "Safe Backup Snapshot",
        "inputs": ["Optional avatar path, asset paths, backup root, and open-scene flag."],
        "outputs": ["Backup id, backup path, and included asset list."],
        "sideEffects": "writes backup snapshot files only",
        "tags": ["backup", "safety"],
    },
    "vrcforge_scan_avatar_performance": {
        "title": "Avatar Performance Scan",
        "inputs": ["Optional avatar path and mobile-limit flag."],
        "outputs": ["VRChat SDK performance rank per category plus raw statistics."],
        "sideEffects": "writes artifact JSON only",
        "tags": ["performance", "scan"],
    },
    "vrcforge_package_manager_status": {
        "title": "VPM Package Manager Status",
        "inputs": ["Optional Unity project path."],
        "outputs": ["Detected vrc-get/ALCOM/vpm CLIs and addon package install state."],
        "sideEffects": "none",
        "tags": ["package", "vpm", "addon"],
    },
    "vrcforge_diagnose_package_install_errors": {
        "title": "Package Install Error Diagnostics",
        "inputs": ["Optional Unity project path plus package-manager stdout/stderr or log text."],
        "outputs": ["Read-only symptoms, compile-error context, and supervised repair suggestions."],
        "sideEffects": "none",
        "tags": ["package", "diagnostics", "compile-errors"],
    },
    "vrcforge_build_test_readiness": {
        "title": "Build & Test Readiness",
        "inputs": ["Optional avatar path, Unity project path, Quest toggle, and compile-error limit."],
        "outputs": ["Read-only readiness gate, validation sections, package diagnostics, and supervised fix-plan suggestions."],
        "sideEffects": "none",
        "tags": ["validation", "build-test", "readiness"],
    },
    "vrcforge_optimization_validation_delta": {
        "title": "Optimization Validation Delta",
        "inputs": ["Before, after, and optional rollback vrcforge.validation.v1 reports."],
        "outputs": ["Severity, finding, section, gate, and rollback drift delta for one optimizer step."],
        "sideEffects": "none",
        "tags": ["optimization", "validation", "rollback-proof"],
    },
    "vrcforge_preview_setup_outfit": {
        "title": "Setup Outfit Preview",
        "inputs": ["Avatar path and outfit object path."],
        "outputs": ["Modular Avatar readiness checks and warnings, no writes."],
        "sideEffects": "none",
        "tags": ["modular-avatar", "wardrobe", "preview"],
    },
    "vrcforge_setup_outfit": {
        "title": "Setup Outfit",
        "inputs": ["Avatar path, outfit object path, and save-scene flag."],
        "outputs": ["Executed menu path and added Modular Avatar components."],
        "sideEffects": "runs Modular Avatar Setup Outfit on the scene after approval",
        "tags": ["modular-avatar", "wardrobe", "write"],
    },
    "vrcforge_preview_add_wardrobe_outfit": {
        "title": "Add Wardrobe Outfit Preview",
        "inputs": ["Avatar path, existing int parameter name, outfit name, and object paths."],
        "outputs": ["Planned int value, FX state, on/off objects, menu placement, and warnings; no writes."],
        "sideEffects": "none",
        "tags": ["wardrobe", "menu", "animator", "preview"],
    },
    "vrcforge_preview_add_outfit_part": {
        "title": "Add Outfit Part Preview",
        "inputs": ["Avatar path, wardrobe int parameter/value, part name, object paths, and optional Bool parameter/menu options."],
        "outputs": ["Planned Bool parameter, FX layer, clips, object bindings, and menu placement; no writes."],
        "sideEffects": "none",
        "tags": ["wardrobe", "menu", "animator", "preview"],
    },
    "vrcforge_preview_add_modular_avatar_component": {
        "title": "Add Modular Avatar Component Preview",
        "inputs": ["Scene object path, Modular Avatar component type, references, and scalar fields."],
        "outputs": ["Validated component type, resolved references, converted fields, and warnings; no writes."],
        "sideEffects": "none",
        "tags": ["modular-avatar", "component", "preview"],
    },
    "vrcforge_preview_manage_wardrobe": {
        "title": "Manage Wardrobe Preview",
        "inputs": ["Avatar path, wardrobe int parameter name, action, and target value/name/order/default options."],
        "outputs": ["Planned menu, parameter, FX, and optional object changes; no writes."],
        "sideEffects": "none",
        "tags": ["wardrobe", "menu", "animator", "preview"],
    },
    "vrcforge_preview_create_wardrobe": {
        "title": "Create Wardrobe Preview",
        "inputs": ["Avatar path, wardrobe int parameter name, menu name, and optional generated asset folder."],
        "outputs": ["Planned expression parameter, FX layer/default state, and menu/submenu changes; no writes."],
        "sideEffects": "none",
        "tags": ["wardrobe", "menu", "animator", "preview"],
    },
    "vrcforge_preview_ensure_expression_parameter": {
        "title": "Ensure Expression Parameter Preview",
        "inputs": ["Avatar path, parameter name, value type, default value, saved flag, and sync flag."],
        "outputs": ["Planned VRCExpressionParameters asset/entry changes; no writes."],
        "sideEffects": "none",
        "tags": ["parameter", "avatar-authoring", "preview"],
    },
    "vrcforge_ensure_expression_parameter": {
        "title": "Ensure Expression Parameter",
        "inputs": ["Avatar path, parameter name, value type, default value, saved flag, and sync flag."],
        "outputs": ["Created or updated VRCExpressionParameters asset entry."],
        "sideEffects": "creates or updates avatar expression parameters after approval",
        "tags": ["parameter", "avatar-authoring", "write"],
    },
    "vrcforge_preview_ensure_expression_menu_control": {
        "title": "Ensure Expression Menu Control Preview",
        "inputs": ["Avatar path, menu path, control name/type, optional parameter name, and control value."],
        "outputs": ["Planned expression menu root/submenu/control changes; no writes."],
        "sideEffects": "none",
        "tags": ["menu", "avatar-authoring", "preview"],
    },
    "vrcforge_ensure_expression_menu_control": {
        "title": "Ensure Expression Menu Control",
        "inputs": ["Avatar path, menu path, control name/type, optional parameter name, and control value."],
        "outputs": ["Created or reused expression menu assets and controls."],
        "sideEffects": "creates or updates avatar expression menus after approval",
        "tags": ["menu", "avatar-authoring", "write"],
    },
    "vrcforge_preview_ensure_animator_state": {
        "title": "Ensure Animator State Preview",
        "inputs": ["Avatar path, FX layer name, state name, parameter name/type, condition mode, threshold, and Write Defaults flag."],
        "outputs": ["Planned FX controller/layer/state/transition changes; no writes."],
        "sideEffects": "none",
        "tags": ["animator", "avatar-authoring", "preview"],
    },
    "vrcforge_ensure_animator_state": {
        "title": "Ensure Animator State",
        "inputs": ["Avatar path, FX layer name, state name, parameter name/type, condition mode, threshold, and Write Defaults flag."],
        "outputs": ["Created or updated FX controller parameter, layer, state, clip, and Any State condition."],
        "sideEffects": "creates or updates FX animator assets after approval",
        "tags": ["animator", "avatar-authoring", "write"],
    },
    "vrcforge_create_wardrobe": {
        "title": "Create Wardrobe",
        "inputs": ["Avatar path, wardrobe int parameter name, menu name, and optional generated asset folder."],
        "outputs": ["Created or reused expression parameters/menu/FX assets, default state, and default menu toggle."],
        "sideEffects": "creates or updates expression parameters, expression menu, FX controller/layer, and generated default clip after approval",
        "tags": ["wardrobe", "menu", "animator", "write"],
    },
    "vrcforge_add_wardrobe_outfit": {
        "title": "Add Wardrobe Outfit",
        "inputs": ["Avatar path, existing int parameter name, outfit name, object paths, optional off-objects/value/flags."],
        "outputs": ["Assigned int value, authored clip path, added FX state, and menu toggle path."],
        "sideEffects": "adds an FX state, animation clip, and menu toggle to an existing int-exclusive wardrobe after approval",
        "tags": ["wardrobe", "menu", "animator", "write"],
    },
    "vrcforge_add_outfit_part": {
        "title": "Add Outfit Part",
        "inputs": ["Avatar path, wardrobe int parameter/value, part name, object paths, and optional Bool parameter/menu options."],
        "outputs": ["Created Bool parameter, FX layer, clips, object bindings, and menu toggle."],
        "sideEffects": "adds an int-gated part toggle to an existing wardrobe after approval",
        "tags": ["wardrobe", "menu", "animator", "write"],
    },
    "vrcforge_add_modular_avatar_component": {
        "title": "Add Modular Avatar Component",
        "inputs": ["Scene object path, Modular Avatar component type, references, and scalar fields."],
        "outputs": ["Added and configured Modular Avatar component with resolved references."],
        "sideEffects": "adds and configures a Modular Avatar component after approval",
        "tags": ["modular-avatar", "component", "write"],
    },
    "vrcforge_manage_wardrobe": {
        "title": "Manage Wardrobe",
        "inputs": ["Avatar path, wardrobe int parameter name, action, target value/name, optional order/default/delete flags."],
        "outputs": ["Removed/renamed/reordered outfit controls, set default value, or deleted wardrobe bindings."],
        "sideEffects": "can remove or rename expression menu controls, FX states/transitions, expression parameters, and optionally scene outfit objects after approval",
        "tags": ["wardrobe", "menu", "animator", "write"],
    },
    "vrcforge_preview_add_outfit": {
        "title": "Add Outfit Workflow Preview",
        "inputs": ["Avatar path, prefab asset path/guid or asset query, optional wardrobe int parameter and manageWardrobe flag."],
        "outputs": ["Resolved prefab and ordered workflow steps; no writes."],
        "sideEffects": "none",
        "tags": ["wardrobe", "modular-avatar", "preview"],
    },
    "vrcforge_add_outfit": {
        "title": "Add Outfit Workflow",
        "inputs": ["Avatar path, prefab asset path/guid or asset query, optional wardrobe int parameter and manageWardrobe flag."],
        "outputs": ["Instantiated outfit path plus setup, wardrobe scan/create, and wardrobe write results."],
        "sideEffects": "instantiates a prefab, runs Modular Avatar Setup Outfit, scans/creates an int-exclusive wardrobe when needed, and adds the object to it after approval",
        "tags": ["wardrobe", "modular-avatar", "write"],
    },
    "vrcforge_import_outfit_package": {
        "title": "Import Outfit Package",
        "inputs": ["Direct .unitypackage or loose prefab folder path, Unity project path, and target Assets folder."],
        "outputs": ["Unity import result or copied asset paths plus imported prefab candidates."],
        "sideEffects": "imports UnityPackage or copies loose outfit assets into Assets after approval and checkpoint",
        "tags": ["outfit", "unitypackage", "write"],
    },
    "vrcforge_install_vpm_package": {
        "title": "VPM Package Install",
        "inputs": ["VPM package id, Unity project path, optional preferred package manager."],
        "outputs": ["Selected package-manager strategy, command result, and post-install package state."],
        "sideEffects": "modifies project VPM manifest and Packages through VCC vpm/vrc-get after approval and checkpoint",
        "tags": ["package", "vpm", "write"],
    },
    "vrcforge_package_install_plan": {
        "title": "VPM Package Install Plan",
        "permissionMode": "preview",
        "inputs": ["VPM package id, Unity project path, optional preferred package manager."],
        "outputs": ["ALCOM/VCC UI handoff, VCC vpm/vrc-get command installer, or agent-managed fallback plan."],
        "sideEffects": "none",
        "tags": ["package", "vpm", "preview"],
    },
    "vrcforge_package_install_request": {
        "title": "VPM Package Install Request",
        "permissionMode": "approval_required",
        "inputs": ["VPM package id, Unity project path, optional preferred package manager."],
        "outputs": ["Approval request for supervised package installation."],
        "sideEffects": "creates an approval request only; approved execution uses checkpoint-gated package manager install",
        "backupRestore": "requires approval, checkpoint, package resolve validation, and rollback proof where available",
        "tags": ["package", "vpm", "write-request"],
    },
    "vrcforge_configure_optimizer_component": {
        "title": "Configure Optimizer Component",
        "permissionMode": "approval_required",
        "inputs": ["Optimizer id, mode, avatar path, component type, target profile, and options."],
        "outputs": ["Added delegated optimizer component result and validation/rollback requirements."],
        "sideEffects": "adds one optimizer component to the avatar after approval and checkpoint",
        "backupRestore": "requires approval, checkpoint, validation delta, and rollback proof",
        "tags": ["optimization", "component", "write"],
    },
    "vrcforge_preview_restore_backup": {
        "title": "Backup Restore Preview",
        "inputs": ["Backup path or backup id, optional asset subset."],
        "outputs": ["Planned overwrites, changed files, and mismatch warnings."],
        "sideEffects": "none",
        "tags": ["backup", "restore", "preview"],
    },
    "vrcforge_list_checkpoints": {
        "title": "Checkpoint Timeline",
        "inputs": ["Optional project root and limit."],
        "outputs": ["Recent pre-write checkpoints with git refs and target tools."],
        "sideEffects": "none",
        "tags": ["checkpoint", "restore", "timeline"],
    },
    "vrcforge_preview_restore_checkpoint": {
        "title": "Checkpoint Restore Preview",
        "inputs": ["Checkpoint id."],
        "outputs": ["Files that differ from the checkpoint and current working tree status."],
        "sideEffects": "none",
        "tags": ["checkpoint", "restore", "preview"],
    },
    "vrcforge_restore_checkpoint": {
        "title": "Checkpoint Restore",
        "inputs": ["Checkpoint id and confirmRestore=true."],
        "outputs": ["Restore result, cleaned files, and checkpoint metadata."],
        "sideEffects": "restores Assets/Packages/ProjectSettings from a pre-write git checkpoint after approval",
        "tags": ["checkpoint", "restore", "write"],
    },
    "vrcforge_unity_mcp_write": {
        "title": "Supervised Unity MCP Write",
        "inputs": ["Unity MCP tool name and argument object."],
        "outputs": ["Unity MCP execution result plus the automatic pre-write checkpoint."],
        "sideEffects": "runs an arbitrary Unity MCP write only after approval and rollback checkpoint creation",
        "tags": ["unity", "mcp", "checkpoint", "write"],
    },
    "vrcforge_restore_safe_backup": {
        "title": "Safe Backup Restore",
        "inputs": ["Backup path or backup id, optional asset subset, overwrite flags."],
        "outputs": ["Restored file list and refresh status."],
        "sideEffects": "overwrites project files from a backup snapshot after approval",
        "tags": ["backup", "restore", "write"],
    },
    "vrcforge_toggle_scene_object": {
        "title": "Scene Object Toggle",
        "inputs": ["Scene object path and target active state."],
        "outputs": ["Toggle result and saved scene state."],
        "sideEffects": "writes scene object active state after approval",
        "tags": ["wardrobe", "write"],
    },
    "vrcforge_roslyn_status": {
        "title": "Roslyn Status",
        "outputs": ["Roslyn DLL, project flag, compiler, and Unity execution readiness."],
        "sideEffects": "none",
        "tags": ["roslyn", "advanced"],
    },
    "vrcforge_request_roslyn_advanced": {
        "title": "Roslyn Advanced Request",
        "permissionMode": "advanced_power_mode",
        "inputs": ["C# code, timeout, confirmAdvancedPowerMode=true."],
        "outputs": ["Approval record for Unity Roslyn execution."],
        "sideEffects": "requests critical code execution approval",
        "backupRestore": "caller must preview and back up affected Unity assets before writes",
        "tags": ["roslyn", "critical", "advanced"],
    },
    "vrcforge_roslyn_advanced": {
        "title": "Roslyn Advanced Execute",
        "permissionMode": "advanced_power_mode",
        "inputs": ["Approved Roslyn code payload."],
        "outputs": ["Compiled snippet result and Unity execution diagnostics."],
        "sideEffects": "can execute arbitrary approved C# inside Unity",
        "backupRestore": "requires explicit user acknowledgement and audit logging",
        "tags": ["roslyn", "critical", "advanced"],
    },
}

BUILTIN_SKILL_OVERRIDES["vrcforge_optimization_plan"] = {
    "title": "Model Optimization Planner",
    "permissionMode": "preview",
    "inputs": ["Optional Unity project path, avatar path, target profile, and Quest toggle."],
    "outputs": ["vrcforge.optimization.v1 baseline, dependency doctor, audits, plans, action cards, and recommended order."],
    "sideEffects": "none",
    "backupRestore": "not required for planning; future applies require preview, approval, checkpoint, validation, and rollback",
    "tags": ["optimization", "planner", "read-only", "plan-only"],
}
for _optimization_definition in OPTIMIZATION_TOOL_DEFINITIONS:
    _level = "plan-only" if _optimization_definition["category"] == "plan/preview" else "read-only"
    BUILTIN_SKILL_OVERRIDES[_optimization_definition["gatewayName"]] = {
        "title": _optimization_definition["externalName"],
        "permissionMode": "preview" if _optimization_definition["category"] == "plan/preview" else "read_only",
        "inputs": ["Optional Unity project path, avatar path, target profile, and scanner limits."],
        "outputs": [f"{_optimization_definition['externalName']} {_level} result under vrcforge.optimization.v1."],
        "sideEffects": "none",
        "backupRestore": "not required; this 0.7.2 tool never writes project assets",
        "tags": ["optimization", _level, "no-direct-apply"],
    }
for _optimization_apply_tool in STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES:
    BUILTIN_SKILL_OVERRIDES[_optimization_apply_tool] = {
        "title": _optimization_apply_tool.replace("vrcforge_optimization_", "optimization.").replace("_", "."),
        "permissionMode": "approval_required",
        "inputs": ["Unity project path, avatar path, target profile, and optional installMissingDependencies flag."],
        "outputs": ["Approval request for one optimizer step, or a dependency/package-install request when the optimizer is missing."],
        "sideEffects": "creates an approval request only; execution still requires VRCForge approval, checkpoint, validation, and rollback",
        "backupRestore": "required before any approved optimizer component configuration or dependency install",
        "tags": ["optimization", "write-request", "no-direct-apply"],
    }

BUILTIN_SKILL_GROUPS: list[dict[str, Any]] = [
    {
        "name": "runtime-diagnostics",
        "title": "Runtime Diagnostics",
        "description": "Inspect backend, agent runtime, logs, and gateway skill state.",
        "category": "diagnostics",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": "health status, logs, runtime diagnosis, backend diagnosis, agent status",
        "inputs": ["Optional session or log limit."],
        "outputs": ["Health, logs, tool, and skill registry snapshots."],
        "sideEffects": "none",
        "backupRestore": "not required",
        "allowedTools": [
            "vrcforge_health",
            "vrcforge_skill_manifest",
            "vrcforge_skill_check",
            "vrcforge_agent_observe",
            "vrcforge_read_recent_logs",
        ],
        "entrypointTool": "vrcforge_health",
        "tags": ["builtin", "group", "diagnostics"],
    },
    {
        "name": "unity-bridge-diagnostics",
        "title": "Unity Bridge Diagnostics",
        "description": "Inspect Unity MCP reachability, active instances, and registered Unity tools.",
        "category": "unity-bridge",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": "Unity MCP status, active Unity instance, missing VRCForge tools",
        "inputs": ["Optional MCP host, port, or session id."],
        "outputs": ["Unity bridge state, active project, registered tools, and missing tools."],
        "sideEffects": "none",
        "backupRestore": "not required",
        "allowedTools": ["vrcforge_unity_status", "vrcforge_unity_tools"],
        "entrypointTool": "vrcforge_unity_status",
        "tags": ["builtin", "group", "unity", "mcp"],
    },
    {
        "name": "project-golden-path-preflight",
        "title": "Project Golden Path Preflight",
        "description": "Build the local project index and inspect outfit package inputs before avatar workflows.",
        "category": "project",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": "incremental project scan, changed files, UnityPackage, Booth ZIP, outfit folder, loose prefab textures",
        "inputs": ["Unity project path or local outfit package/folder path."],
        "outputs": ["Project file deltas, affected scanner families, outfit package structural summaries, and supervised import plans."],
        "sideEffects": "updates local VRCForge index only; reads package directory/pathname metadata only",
        "backupRestore": "not required",
        "allowedTools": ["vrcforge_scan_project_index", "vrcforge_inspect_outfit_package", "vrcforge_plan_outfit_import"],
        "entrypointTool": "vrcforge_scan_project_index",
        "tags": ["builtin", "group", "project", "outfit", "unitypackage"],
    },
    {
        "name": "avatar-inventory-scan",
        "title": "Avatar Inventory Scan",
        "description": "Scan avatars, blendshapes, materials, animator state, and animation bindings.",
        "category": "avatar-scan",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": "avatar list, avatar scan, blendshape scan, material scan, animator scan",
        "inputs": ["Unity project context and optional avatar path."],
        "outputs": ["Avatar, blendshape, material, animator, and binding inventory."],
        "sideEffects": "none",
        "backupRestore": "not required",
        "allowedTools": [
            "vrcforge_list_avatars",
            "vrcforge_scan_blendshapes",
            "vrcforge_scan_materials",
            "vrcforge_scan_avatar_items",
            "vrcforge_scan_fx_animator",
            "vrcforge_scan_animation_bindings",
            "vrcforge_scan_avatar_controls",
            "vrcforge_scan_wardrobe",
            "vrcforge_scan_parameters",
            "vrcforge_scan_avatar_performance",
            "vrcforge_scan_thry_avatar_performance",
        ],
        "entrypointTool": "vrcforge_list_avatars",
        "tags": ["builtin", "group", "avatar", "scan"],
    },
    {
        "name": "gesture-vision-review",
        "title": "Gesture Vision Review",
        "description": "Capture Play Mode or Game View screenshots and run advisory vision checks.",
        "category": "vision",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": "Gesture Manager screenshot, Game View capture, visual review, screenshot audit",
        "inputs": ["Capture angle, dimensions, target image, and optional prompt."],
        "outputs": ["Capture status, image artifact path, and advisory review result."],
        "sideEffects": "writes artifact image only",
        "backupRestore": "not required",
        "allowedTools": [
            "vrcforge_capture_status",
            "vrcforge_capture_screenshot",
            "vrcforge_vision_audit",
        ],
        "entrypointTool": "vrcforge_capture_status",
        "tags": ["builtin", "group", "vision", "capture"],
    },
    {
        "name": "validation-readiness",
        "title": "Validation & Build Test Readiness",
        "description": "Run the stable validation report and preflight Build & Test without building or publishing.",
        "category": "validation",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": "validation report, build test readiness, compile gate, SDK check, rollback proof context",
        "inputs": ["Optional avatar path, Unity project path, Quest toggle, and compile-error limit."],
        "outputs": ["Stable validation sections, severity gate, readiness checks, and supervised fix-plan suggestions."],
        "sideEffects": "none",
        "backupRestore": "not required",
        "allowedTools": ["vrcforge_run_validation_report", "vrcforge_build_test_readiness", "vrcforge_diagnose_package_install_errors"],
        "entrypointTool": "vrcforge_build_test_readiness",
        "tags": ["builtin", "group", "validation", "build-test", "readiness"],
    },
    {
        "name": "model-optimization-planner",
        "title": "Model Optimization Planner",
        "description": "Scan, audit, and plan model optimization steps without modifying the avatar or Unity project.",
        "category": "optimization",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": "model optimization baseline, dependency doctor, VRAM/material/mesh/parameter audits, AAO/LAC/TTT/Meshia/MA2BT/VRCFury planning",
        "inputs": ["Optional avatar path, Unity project path, target profile, and Quest toggle."],
        "outputs": ["Stable vrcforge.optimization.v1 baseline, dependency status cards, action cards, and one-step-at-a-time optimization plan."],
        "sideEffects": "none",
        "backupRestore": "not required for 0.7.2 planning; future optimizer applies must use approval, checkpoint, validation, and rollback",
        "allowedTools": ["vrcforge_optimization_plan", "vrcforge_optimization_validation_delta", *OPTIMIZATION_GATEWAY_TOOL_NAMES],
        "entrypointTool": "vrcforge_optimization_plan",
        "tags": ["builtin", "group", "optimization", "read-only", "plan-only"],
    },
    {
        "name": "avatar-optimization-skills",
        "title": "Avatar Optimization Skills",
        "description": "Scan, plan, install dependencies, and request one stable delegated avatar optimizer step at a time.",
        "category": "optimization",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "request LAC apply, request AAO trace, request MA2BT conversion, install optimizer dependency, optimizer apply-request",
        "inputs": ["Unity project path, avatar path, target profile, optimizer request tool, and optional dependency install flag."],
        "outputs": ["Approval request for one optimizer configuration or dependency install step."],
        "sideEffects": "creates approval requests; approved execution can add a delegated optimizer component or install a VPM package through checkpointed package manager flow",
        "backupRestore": "approval, checkpoint, validation, and rollback proof are required before any approved write",
        "allowedTools": [
            "vrcforge_optimization_plan",
            "vrcforge_optimization_validation_delta",
            *OPTIMIZATION_GATEWAY_TOOL_NAMES,
            *STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES,
            "vrcforge_scan_thry_avatar_performance",
            "vrcforge_package_manager_status",
            "vrcforge_package_install_plan",
            "vrcforge_package_install_request",
            "vrcforge_request_apply",
        ],
        "entrypointTool": "vrcforge_optimization_plan",
        "tags": ["builtin", "group", "optimization", "write-request", "no-direct-apply"],
    },
    {
        "name": "face-tuning-workflow",
        "title": "Face Tuning Workflow",
        "description": "Plan, preview, approve, apply, and restore face Blendshape tuning.",
        "category": "face",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "face tuning, expression tuning, blendshape edit, face restore",
        "inputs": ["Avatar path, tuning request, blendshape targets, and approval id."],
        "outputs": ["Plan, dry-run preview, approval request, apply result, and restore result."],
        "sideEffects": "can write Unity avatar Blendshape values after approval",
        "backupRestore": "requires preview, backup, apply, validate, restore path",
        "allowedTools": [
            "vrcforge_scan_blendshapes",
            "vrcforge_plan_face_tuning",
            "vrcforge_preview_blendshape_apply",
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_apply_blendshapes",
            "vrcforge_run_face_tuning",
            "vrcforge_undo_blendshapes",
            "vrcforge_restore_last_backup",
        ],
        "entrypointTool": "vrcforge_plan_face_tuning",
        "tags": ["builtin", "group", "face", "blendshape", "write"],
    },
    {
        "name": "shader-material-workflow",
        "title": "Shader Material Workflow",
        "description": "Plan, preview, approve, apply, and restore shader/material tuning.",
        "category": "shader",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "shader tuning, material tuning, lilToon, Poiyomi, material restore",
        "inputs": ["Avatar path, material targets, tuning request, and approval id."],
        "outputs": ["Material inventory, tuning plan, dry-run preview, apply result, and restore result."],
        "sideEffects": "can write Unity material settings after approval",
        "backupRestore": "requires preview, backup, apply, validate, restore path",
        "allowedTools": [
            "vrcforge_scan_materials",
            "vrcforge_plan_shader_tuning",
            "vrcforge_preview_shader_apply",
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_apply_shader_tuning",
            "vrcforge_restore_shader_tuning",
            "vrcforge_restore_last_backup",
        ],
        "entrypointTool": "vrcforge_plan_shader_tuning",
        "tags": ["builtin", "group", "shader", "material", "write"],
    },
    {
        "name": "approval-restore-control",
        "title": "Approval Restore Control",
        "description": "Manage supervised write requests, approved apply calls, and restore paths.",
        "category": "approval",
        "permissionMode": "approval_required",
        "riskLevel": "medium",
        "whenToUse": "approval queue, apply approved, restore last backup, rollback",
        "inputs": ["Approval id, target tool, payload summary, and restore request."],
        "outputs": ["Approval record, apply result, restore result, and audit trail."],
        "sideEffects": "can apply or restore Unity project changes after approval",
        "backupRestore": "uses stored approval and restore metadata",
        "allowedTools": [
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_restore_last_backup",
            "vrcforge_restore_shader_tuning",
            "vrcforge_undo_blendshapes",
            "vrcforge_rollback_parameters",
            "vrcforge_create_safe_backup",
            "vrcforge_preview_restore_backup",
            "vrcforge_restore_safe_backup",
            "vrcforge_list_checkpoints",
            "vrcforge_preview_restore_checkpoint",
            "vrcforge_restore_checkpoint",
        ],
        "entrypointTool": "vrcforge_request_apply",
        "tags": ["builtin", "group", "approval", "restore"],
    },
    {
        "name": "parameter-fx-workflow",
        "title": "Parameter FX Workflow",
        "description": "Apply clothing FX assets and avatar parameter optimization through approval.",
        "category": "avatar-write",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "clothing FX, parameter optimization, menu parameter rollback",
        "inputs": ["Avatar path, generated FX payload, parameter plan, and approval id."],
        "outputs": ["FX apply result, parameter apply result, and rollback result."],
        "sideEffects": "can write animator, expression, and generated asset files after approval",
        "backupRestore": "requires backup and rollback metadata",
        "allowedTools": [
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_apply_clothing_fx",
            "vrcforge_apply_parameter_optimization",
            "vrcforge_rollback_parameters",
        ],
        "entrypointTool": "vrcforge_request_apply",
        "tags": ["builtin", "group", "fx", "parameters", "write"],
    },
    {
        "name": "shell-debug-loop",
        "title": "Shell Debug Loop",
        "description": "Classify shell commands, run low-risk commands, and queue high-risk commands for approval.",
        "category": "debug",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "shell command, terminal debug, file inspection, approved command execution",
        "inputs": ["Command, workspace root, cwd, and approval id."],
        "outputs": ["Risk classification, shell output, or pending approval."],
        "sideEffects": "low-risk reads may run directly; high-risk commands require approval",
        "backupRestore": "caller must back up before write commands",
        "allowedTools": [
            "vrcforge_classify_shell",
            "vrcforge_execute_shell",
            "vrcforge_execute_approved_shell",
            "vrcforge_shell_execute",
        ],
        "entrypointTool": "vrcforge_classify_shell",
        "tags": ["builtin", "group", "shell", "debug"],
    },
    {
        "name": "roslyn-advanced-power",
        "title": "Roslyn Advanced Power",
        "description": "Inspect, request, and execute explicitly approved Roslyn Advanced Power Mode flows.",
        "category": "advanced",
        "permissionMode": "advanced_power_mode",
        "riskLevel": "critical",
        "whenToUse": "Roslyn status, dynamic C# repair, advanced Unity execution",
        "inputs": ["C# code, timeout, approval id, and confirmAdvancedPowerMode=true."],
        "outputs": ["Roslyn status, approval record, and execution result."],
        "sideEffects": "can execute approved C# inside Unity",
        "backupRestore": "requires risk acknowledgement, audit log, and backup before asset writes",
        "allowedTools": [
            "vrcforge_roslyn_status",
            "vrcforge_request_roslyn_advanced",
            "vrcforge_roslyn_advanced",
        ],
        "entrypointTool": "vrcforge_roslyn_status",
        "tags": ["builtin", "group", "roslyn", "advanced", "critical"],
    },
    {
        "name": "modular-avatar-toolkit",
        "title": "Modular Avatar Toolkit",
        "description": "Detect the Modular Avatar package and inspect Modular Avatar components before edits.",
        "category": "addon-scan",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": "modular avatar, MA component, armature merge, menu installer, outfit install, non-destructive merge",
        "inputs": ["Optional Unity project path and avatar path."],
        "outputs": ["Package install state, component carriers, and integration hints."],
        "sideEffects": "none",
        "backupRestore": "not required",
        "allowedTools": [
            "vrcforge_scan_modular_avatar",
            "vrcforge_unity_status",
            "vrcforge_list_avatars",
        ],
        "entrypointTool": "vrcforge_scan_modular_avatar",
        "tags": ["builtin", "group", "modular-avatar", "addon"],
    },
    {
        "name": "vrcfury-toolkit",
        "title": "VRCFury Toolkit",
        "description": "Detect the VRCFury package and inspect VRCFury components before edits.",
        "category": "addon-scan",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": "vrcfury, fury component, toggle install, prefab feature, full controller, non-destructive feature",
        "inputs": ["Optional Unity project path and avatar path."],
        "outputs": ["Package install state, component carriers, and integration hints."],
        "sideEffects": "none",
        "backupRestore": "not required",
        "allowedTools": [
            "vrcforge_scan_vrcfury",
            "vrcforge_unity_status",
            "vrcforge_list_avatars",
        ],
        "entrypointTool": "vrcforge_scan_vrcfury",
        "tags": ["builtin", "group", "vrcfury", "addon"],
    },
    {
        "name": "wardrobe-control",
        "title": "Wardrobe Control",
        "description": "Scan wardrobe-related objects and toggle clothing items through approval.",
        "category": "wardrobe",
        "permissionMode": "approval_required",
        "riskLevel": "medium",
        "whenToUse": "clothes toggle, outfit switch, wardrobe scan, accessory on off",
        "inputs": ["Avatar path, scene object path, and target active state."],
        "outputs": ["Wardrobe item inventory and toggle results."],
        "sideEffects": "can toggle scene object active state after approval",
        "backupRestore": "uses safe backup snapshot before writes",
        "allowedTools": [
            "vrcforge_scan_avatar_items",
            "vrcforge_scan_avatar_controls",
            "vrcforge_scan_wardrobe",
            "vrcforge_create_safe_backup",
            "vrcforge_preview_ensure_expression_parameter",
            "vrcforge_preview_ensure_expression_menu_control",
            "vrcforge_preview_ensure_animator_state",
            "vrcforge_preview_add_wardrobe_outfit",
            "vrcforge_preview_add_outfit_part",
            "vrcforge_preview_add_modular_avatar_component",
            "vrcforge_preview_manage_wardrobe",
            "vrcforge_preview_create_wardrobe",
            "vrcforge_preview_add_outfit",
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_toggle_scene_object",
            "vrcforge_ensure_expression_parameter",
            "vrcforge_ensure_expression_menu_control",
            "vrcforge_ensure_animator_state",
            "vrcforge_create_wardrobe",
            "vrcforge_add_wardrobe_outfit",
            "vrcforge_add_outfit_part",
            "vrcforge_add_modular_avatar_component",
            "vrcforge_manage_wardrobe",
            "vrcforge_add_outfit",
        ],
        "entrypointTool": "vrcforge_scan_avatar_items",
        "tags": ["builtin", "group", "wardrobe", "write"],
    },
    {
        "name": "avatar-authoring-primitives",
        "title": "Avatar Authoring Primitives",
        "description": "Reusable expression parameter, expression menu, and FX animator authoring tools.",
        "category": "avatar",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "create parameter, add menu control, create FX layer, add animator state, any state condition",
        "inputs": ["Avatar path plus parameter/menu/animator authoring arguments."],
        "outputs": ["Preview or approved writes for reusable avatar authoring assets."],
        "sideEffects": "can create or update expression parameters, expression menus, FX controllers, generated clips, and animator transitions after approval",
        "backupRestore": "uses gateway checkpoint before approved writes",
        "allowedTools": [
            "vrcforge_scan_avatar_controls",
            "vrcforge_scan_fx_animator",
            "vrcforge_scan_parameters",
            "vrcforge_preview_ensure_expression_parameter",
            "vrcforge_preview_ensure_expression_menu_control",
            "vrcforge_preview_ensure_animator_state",
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_ensure_expression_parameter",
            "vrcforge_ensure_expression_menu_control",
            "vrcforge_ensure_animator_state",
        ],
        "entrypointTool": "vrcforge_preview_ensure_expression_parameter",
        "tags": ["builtin", "group", "avatar-authoring", "parameters", "menu", "animator", "write"],
    },
    {
        "name": "outfit-install-workflow",
        "title": "Outfit Install Workflow",
        "description": "Validate and run Modular Avatar Setup Outfit on an outfit under an avatar, with backup and approval.",
        "category": "wardrobe",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "install outfit, setup outfit, merge armature, add clothes, modular avatar install",
        "inputs": ["Avatar path and outfit object path under the avatar root."],
        "outputs": ["Readiness preview, executed setup result, and added components."],
        "sideEffects": "can run Modular Avatar Setup Outfit on the scene after approval",
        "backupRestore": "uses safe backup snapshot before setup; restore via safe backup restore",
        "allowedTools": [
            "vrcforge_scan_modular_avatar",
            "vrcforge_scan_avatar_items",
            "vrcforge_create_safe_backup",
            "vrcforge_preview_setup_outfit",
            "vrcforge_preview_ensure_expression_parameter",
            "vrcforge_preview_ensure_expression_menu_control",
            "vrcforge_preview_ensure_animator_state",
            "vrcforge_preview_create_wardrobe",
            "vrcforge_preview_add_outfit",
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_setup_outfit",
            "vrcforge_ensure_expression_parameter",
            "vrcforge_ensure_expression_menu_control",
            "vrcforge_ensure_animator_state",
            "vrcforge_create_wardrobe",
            "vrcforge_add_outfit",
            "vrcforge_restore_safe_backup",
        ],
        "entrypointTool": "vrcforge_preview_setup_outfit",
        "tags": ["builtin", "group", "modular-avatar", "wardrobe", "write"],
    },
    {
        "name": "package-maintenance",
        "title": "Package Maintenance",
        "description": "Detect ALCOM/VCC/vpm/vrc-get, explain package/plugin install failures, plan dependency installs, and request supervised package installs.",
        "category": "package",
        "permissionMode": "approval_required",
        "riskLevel": "medium",
        "whenToUse": "install package, vpm, vrc-get, alcom, vcc, add optimizer dependency, add modular avatar, add vrcfury",
        "inputs": ["Unity project path and VPM package id."],
        "outputs": ["Package manager status, install strategy, approval request, diagnostics, and post-install result."],
        "sideEffects": "can modify the project VPM manifest and Packages after approval/checkpoint through a supported package manager",
        "backupRestore": "requires VRCForge checkpoint before approved package-manager writes",
        "allowedTools": [
            "vrcforge_package_manager_status",
            "vrcforge_package_install_plan",
            "vrcforge_package_install_request",
            "vrcforge_diagnose_package_install_errors",
            "vrcforge_scan_modular_avatar",
            "vrcforge_scan_vrcfury",
            "vrcforge_request_apply",
        ],
        "entrypointTool": "vrcforge_package_manager_status",
        "tags": ["builtin", "group", "package", "vpm", "write"],
    },
]


class AgentGateway:
    def __init__(
        self,
        config_path: Path,
        audit_dir: Path,
        public_base_url: str = "http://127.0.0.1:8757",
    ) -> None:
        self.config_path = config_path
        self.audit_dir = audit_dir
        self.public_base_url = public_base_url.rstrip("/")
        self._tools: dict[str, AgentTool] = {}
        self._write_handlers: dict[str, AgentWriteHandler] = {}
        self._approvals: dict[str, dict[str, Any]] = {}
        self._runtime_sessions: dict[str, dict[str, Any]] = {}
        self.checkpoint_project_root_resolver: Callable[[], str] | None = None
        self.checkpoint_prepare_handler: Callable[[Path], dict[str, Any]] | None = None
        self.checkpoint_restore_handler: Callable[[Path], dict[str, Any]] | None = None
        self._lock = threading.RLock()
        # Optional LLM planner hook injected by the host server. Receives a prompt
        # string and returns the raw model response text. Any exception falls back
        # to the deterministic local planner.
        self.llm_plan_fn: Callable[[str], str] | None = None
        # 由宿主在配置/调用 LLM 时更新，例如 "DeepSeek · deepseek-chat"。
        # 写入 plan.plannerLabel 供前端徽章显示真实 provider+model。
        self.llm_planner_label: str = ""
        self.llm_reasoning_trace: dict[str, Any] = {}

    def configure_paths(self, config_path: Path, audit_dir: Path) -> None:
        with self._lock:
            self.config_path = config_path
            self.audit_dir = audit_dir
            self._approvals.clear()
            self._runtime_sessions.clear()

    def register_tool(
        self,
        name: str,
        description: str,
        category: str,
        handler: ToolHandler,
        write: bool = False,
        advanced: bool = False,
    ) -> None:
        self._tools[name] = AgentTool(
            name=name,
            description=description,
            category=category,
            handler=handler,
            write=write,
            advanced=advanced,
        )

    def register_write_handler(
        self,
        name: str,
        description: str,
        risk_level: str,
        handler: ToolHandler,
        advanced: bool = False,
    ) -> None:
        self._write_handlers[name] = AgentWriteHandler(
            name=name,
            description=description,
            risk_level=risk_level,
            handler=handler,
            advanced=advanced,
        )

    def ensure_config(self) -> AgentGatewayConfig:
        with self._lock:
            raw = self._read_config_payload()
            changed = False

            if not raw.get("token"):
                raw["token"] = secrets.token_urlsafe(32)
                changed = True
            if not raw.get("approval_token"):
                raw["approval_token"] = secrets.token_urlsafe(32)
                changed = True

            defaults = {
                "enabled": False,
                "require_token": True,
                "allow_write_requests": True,
                "allow_roslyn_advanced": False,
                "approval_timeout_seconds": 600,
                "execution_mode": "approval",
                "roslyn_risk_acknowledged": False,
            }
            for key, value in defaults.items():
                if key not in raw:
                    raw[key] = value
                    changed = True

            config = AgentGatewayConfig(
                enabled=bool(raw.get("enabled")),
                require_token=bool(raw.get("require_token", True)),
                token=str(raw.get("token") or ""),
                approval_token=str(raw.get("approval_token") or ""),
                allow_write_requests=bool(raw.get("allow_write_requests", True)),
                allow_roslyn_advanced=bool(raw.get("allow_roslyn_advanced", False)),
                approval_timeout_seconds=int(raw.get("approval_timeout_seconds", 600)),
                execution_mode=normalize_execution_mode(raw.get("execution_mode")),
                roslyn_risk_acknowledged=bool(raw.get("roslyn_risk_acknowledged", False)),
            )
            if changed:
                self.save_config(config)
            return config

    def save_config(self, config: AgentGatewayConfig) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": bool(config.enabled),
            "require_token": bool(config.require_token),
            "token": config.token or secrets.token_urlsafe(32),
            "approval_token": config.approval_token or secrets.token_urlsafe(32),
            "allow_write_requests": bool(config.allow_write_requests),
            "allow_roslyn_advanced": bool(config.allow_roslyn_advanced),
            "approval_timeout_seconds": int(config.approval_timeout_seconds),
            "execution_mode": normalize_execution_mode(config.execution_mode),
            "roslyn_risk_acknowledged": bool(config.roslyn_risk_acknowledged),
        }
        atomic_write_json(self.config_path, payload)

    def authenticate(
        self,
        headers: dict[str, str],
        query_params: dict[str, str],
        client_host: str | None,
        allow_disabled: bool = False,
    ) -> AgentGatewayConfig:
        config = self.ensure_config()
        if client_host and client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise AgentGatewayError("Agent Gateway only accepts loopback clients.", status_code=403)

        if config.require_token:
            supplied = self._extract_token(headers, query_params)
            if not supplied or not hmac.compare_digest(supplied, config.token):
                raise AgentGatewayError("Agent Gateway token is missing or invalid.", status_code=401)

        if not config.enabled and not allow_disabled:
            raise AgentGatewayError("Agent Gateway is disabled in config/agent_gateway.json.", status_code=403)

        return config

    def authenticate_approval(
        self,
        headers: dict[str, str],
        query_params: dict[str, str],
        client_host: str | None,
    ) -> AgentGatewayConfig:
        config = self.authenticate(headers, query_params, client_host, allow_disabled=False)
        supplied = (
            headers.get("x-vrcforge-approval-token")
            or headers.get("X-VRCForge-Approval-Token")
            or query_params.get("approval_token")
            or ""
        )
        if not supplied or not hmac.compare_digest(supplied, config.approval_token):
            raise AgentGatewayError("Approval token is missing or invalid.", status_code=401)
        return config

    def build_manifest(self) -> dict[str, Any]:
        config = self.ensure_config()
        user_constraints = self.read_user_constraints()
        tools = [
            self._serialize_tool(tool, config)
            for tool in self._tools.values()
            if self._tool_visible(tool, config)
        ]
        return {
            "ok": True,
            "name": "VRCForge Agent Gateway",
            "version": "0.1",
            "enabled": config.enabled,
            "mcpUrl": f"{self.public_base_url}/mcp",
            "restUrl": f"{self.public_base_url}/api/agent",
            "requiresToken": config.require_token,
            "allowWriteRequests": config.allow_write_requests,
            "allowRoslynAdvanced": self.roslyn_available(config),
            "executionMode": normalize_execution_mode(config.execution_mode),
            "roslynFullAuto": normalize_execution_mode(config.execution_mode) == "roslyn_full_auto",
            "roslynRiskAcknowledged": config.roslyn_risk_acknowledged,
            "approvalTimeoutSeconds": config.approval_timeout_seconds,
            "tools": tools,
            "toolCount": len(tools),
            "writeTargets": self.visible_write_targets(config),
            "skills": self.build_skill_registry(config)["skills"],
            "userConstraints": self._serialize_user_constraints(user_constraints),
        }

    def build_tool_registry(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self.ensure_config()
        tools: list[dict[str, Any]] = []
        for tool in self._tools.values():
            if tool.name in EXTERNAL_AGENT_INTERNAL_TOOLS:
                continue
            tools.append(self._serialize_tool_registry_entry(tool, config))
        for handler in self._write_handlers.values():
            if handler.name in WRAPPER_ONLY_WRITE_TARGETS:
                continue
            tools.append(self._serialize_write_registry_entry(handler, config))
        tools.sort(key=lambda item: (str(item.get("category") or ""), str(item.get("id") or "")))
        categories = sorted({str(item.get("category") or "misc") for item in tools})
        return {
            "ok": True,
            "schema": "vrcforge.tool_registry.v1",
            "generatedAt": utc_now_iso(),
            "count": len(tools),
            "categories": categories,
            "tools": tools,
        }

    def build_skill_registry(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self.ensure_config()
        builtin_skills = self._builtin_skill_definitions(config)
        user_skills = self._load_user_skills()
        skills = [*builtin_skills, *user_skills]
        skills = [self._decorate_skill_validation(skill, config) for skill in skills]
        available_count = sum(1 for skill in skills if skill.get("available") and skill.get("enabled", True))
        warning_count = sum(1 for skill in skills if ensure_dict(skill.get("validation")).get("status") == "warning")
        error_count = sum(1 for skill in skills if ensure_dict(skill.get("validation")).get("status") == "error")
        return {
            "ok": True,
            "schema": "vrcforge.skills.v1",
            "skills": skills,
            "count": len(skills),
            "availableCount": available_count,
            "builtinCount": len(builtin_skills),
            "userCount": len(user_skills),
            "warningCount": warning_count,
            "errorCount": error_count,
            "storage": {
                "scope": "user-data",
                "writable": True,
                "path": str(self.user_skills_dir),
            },
        }

    def check_skill_registry(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self.ensure_config()
        registry = self.build_skill_registry(config)
        checks = []
        for skill in registry["skills"]:
            validation = ensure_dict(skill.get("validation"))
            checks.append(
                {
                    "name": skill.get("name"),
                    "title": skill.get("title"),
                    "source": skill.get("source"),
                    "skillType": skill.get("skillType"),
                    "status": validation.get("status") or ("ok" if skill.get("available") else "warning"),
                    "reasons": ensure_string_list(validation.get("reasons")),
                    "available": bool(skill.get("available")),
                }
            )
        errors = [item for item in checks if item["status"] == "error"]
        warnings = [item for item in checks if item["status"] == "warning"]
        return {
            "ok": not errors,
            "schema": "vrcforge.skills.check.v1",
            "count": len(checks),
            "errorCount": len(errors),
            "warningCount": len(warnings),
            "checks": checks,
        }

    def create_user_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        skills = self._load_user_skills()
        skill = self._normalize_user_skill(payload)
        skill_id = str(skill["name"])
        self._ensure_user_skill_can_use_id(skill_id, skills)
        skills.append(skill)
        self._save_user_skills(skills)
        self.append_audit({"event": "user_skill_created", "skill": skill_id})
        return {"ok": True, "skill": skill, **self.build_skill_registry()}

    def update_user_skill(self, skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        skill_id = normalize_skill_id(skill_id)
        skills = self._load_user_skills()
        for index, existing in enumerate(skills):
            if existing.get("name") == skill_id:
                next_payload = {**existing, **payload, "name": skill_id}
                skills[index] = self._normalize_user_skill(next_payload, existing_id=skill_id)
                self._save_user_skills(skills)
                self.append_audit({"event": "user_skill_updated", "skill": skill_id})
                return {"ok": True, "skill": skills[index], **self.build_skill_registry()}
        raise AgentGatewayError(f"User skill was not found: {skill_id}", status_code=404)

    def delete_user_skill(self, skill_id: str) -> dict[str, Any]:
        skill_id = normalize_skill_id(skill_id)
        skills = self._load_user_skills()
        kept = [skill for skill in skills if skill.get("name") != skill_id]
        if len(kept) == len(skills):
            raise AgentGatewayError(f"User skill was not found: {skill_id}", status_code=404)
        self._save_user_skills(kept)
        self.append_audit({"event": "user_skill_deleted", "skill": skill_id})
        return {"ok": True, "deleted": skill_id, **self.build_skill_registry()}

    def build_health(self) -> dict[str, Any]:
        config = self.ensure_config()
        user_constraints = self.read_user_constraints()
        pending = [item for item in self.list_approvals(include_expired=False) if item.get("status") == "pending"]
        skills = self.build_skill_registry(config)
        return {
            "ok": True,
            "runtimeAlive": True,
            "enabled": config.enabled,
            "requiresToken": config.require_token,
            "configPath": str(self.config_path),
            "auditLogPath": str(self.audit_log_path),
            "mcpUrl": f"{self.public_base_url}/mcp",
            "restUrl": f"{self.public_base_url}/api/agent",
            "pendingApprovalCount": len(pending),
            "allowWriteRequests": config.allow_write_requests,
            "allowRoslynAdvanced": self.roslyn_available(config),
            "permission": self.permission_state(config),
            "userConstraints": self._serialize_user_constraints(user_constraints, include_error=True),
            "shellExecutor": {
                "status": "ok",
                "shell": "powershell",
                "timeoutSeconds": 120,
            },
            "deterministicPlanner": {
                "status": "ok",
                "available": True,
            },
            "skills": {
                "schema": skills["schema"],
                "count": skills["count"],
                "availableCount": skills["availableCount"],
                "builtinCount": skills["builtinCount"],
                "userCount": skills["userCount"],
                "roslynPresent": any(
                    "roslyn" in {str(tag).lower() for tag in ensure_list(skill.get("tags"))}
                    for skill in skills["skills"]
                ),
            },
            "runtimeSessions": len(self._runtime_sessions),
        }

    def auto_approval_enabled(self, config: AgentGatewayConfig | None = None) -> bool:
        config = config or self.ensure_config()
        return normalize_execution_mode(config.execution_mode) in {"auto", "roslyn_full_auto"}

    def permission_state(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self.ensure_config()
        mode = normalize_execution_mode(config.execution_mode)
        return {
            "executionMode": mode,
            "perActionApproval": mode == "approval",
            "autoApprove": mode in {"auto", "roslyn_full_auto"},
            "roslynFullAuto": mode == "roslyn_full_auto",
            "roslynRiskAcknowledged": bool(config.roslyn_risk_acknowledged),
            "allowWriteRequests": bool(config.allow_write_requests),
            "allowRoslynAdvanced": self.roslyn_available(config),
            "legacyRoslynEnvEnabled": os.environ.get("VRCFORGE_ENABLE_ROSLYN", "").strip().lower()
            in {"1", "true", "yes", "on"},
        }

    def update_permission_state(
        self,
        execution_mode: str,
        acknowledge_roslyn_risk: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            config = self.ensure_config()
            mode = normalize_execution_mode(execution_mode)
            entering_roslyn = mode == "roslyn_full_auto"
            if entering_roslyn and not config.roslyn_risk_acknowledged and not acknowledge_roslyn_risk:
                raise AgentGatewayError(
                    "Roslyn full-auto requires one-time risk acknowledgement.",
                    status_code=409,
                )

            previous = self.permission_state(config)
            config.execution_mode = mode
            if acknowledge_roslyn_risk and entering_roslyn:
                config.roslyn_risk_acknowledged = True
            config.allow_roslyn_advanced = entering_roslyn
            self.save_config(config)
            updated = self.permission_state(config)
            self.append_audit(
                {
                    "event": "permission_mode_updated",
                    "previous": previous,
                    "updated": updated,
                }
            )
            return {"ok": True, "permission": updated}

    def call_tool(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        agent_name: str = "external-agent",
    ) -> dict[str, Any]:
        config = self.ensure_config()
        if not config.enabled:
            raise AgentGatewayError("Agent Gateway is disabled in config/agent_gateway.json.", status_code=403)

        tool = self._tools.get(name)
        if not tool or not self._tool_visible(tool, config):
            raise AgentGatewayError(f"Unknown or unavailable agent tool: {name}", status_code=404)

        params = params or {}
        user_constraints = self.read_user_constraints()
        tool_params = self._inject_user_constraints(params, tool, user_constraints)
        try:
            result = tool.handler(tool_params)
            self.append_audit(
                {
                    "event": "tool_call",
                    "tool": name,
                    "agent": agent_name,
                    "paramsSummary": summarize_params(params),
                    "status": "ok",
                }
            )
            return {
                "ok": True,
                "tool": name,
                "agent": agent_name,
                "result": result,
            }
        except Exception as exc:  # noqa: BLE001 - tool errors must be returned to external agents.
            self.append_audit(
                {
                    "event": "tool_call",
                    "tool": name,
                    "agent": agent_name,
                    "paramsSummary": summarize_params(params),
                    "status": "error",
                    "error": str(exc),
                }
            )
            return {
                "ok": False,
                "tool": name,
                "agent": agent_name,
                "error": str(exc),
            }

    def runtime_message(
        self,
        params: dict[str, Any] | None = None,
        agent_name: str = "desktop-agent",
    ) -> dict[str, Any]:
        params = params or {}
        message = str(params.get("message") or "").strip()
        if not message:
            raise AgentGatewayError("message is required.")

        now = utc_now_iso()
        session_id = str(params.get("session_id") or params.get("sessionId") or "").strip()
        if not session_id:
            session_id = f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        turn_id = f"turn_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        history = [entry for entry in ensure_list(params.get("history")) if isinstance(entry, dict)]
        if history:
            self._restore_runtime_session(session_id, history, now)
        observe = self.runtime_observe(session_id=session_id)
        self.llm_reasoning_trace = {}
        plan = self._plan_agent_turn(message, params, observe, history)
        reasoning_trace = ensure_dict(self.llm_reasoning_trace)

        shell_payload: dict[str, Any] | None = None
        skill_payload: dict[str, Any] | None = None
        command = str(params.get("shell_command") or params.get("shellCommand") or plan.get("shellCommand") or "").strip()
        if command:
            shell_payload = self.execute_shell(
                {
                    "command": command,
                    "cwd": params.get("cwd"),
                    "workspace_root": params.get("workspace_root") or params.get("workspaceRoot"),
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "reason": plan.get("summary") or "Agent shell step",
                },
                agent_name=agent_name,
            )
        elif plan.get("skillNeeded") and plan.get("skillTool"):
            skill_payload = self._execute_runtime_skill(
                str(plan.get("skillTool") or ""),
                ensure_dict(plan.get("skillParams")),
                agent_name=agent_name,
            )

        turn = {
            "id": turn_id,
            "createdAt": now,
            "message": message,
            "observe": summarize_params(observe),
            "plan": plan,
        }
        if int(reasoning_trace.get("itemCount") or 0) > 0:
            turn["reasoning"] = reasoning_trace
        if shell_payload is not None:
            turn["shell"] = shell_payload
        if skill_payload is not None:
            turn["skill"] = skill_payload

        with self._lock:
            session = self._runtime_sessions.setdefault(
                session_id,
                {
                    "id": session_id,
                    "createdAt": now,
                    "updatedAt": now,
                    "turns": [],
                },
            )
            session["updatedAt"] = utc_now_iso()
            session["turns"].append(turn)

        self.append_audit(
            {
                "event": "agent_runtime_turn",
                "agent": agent_name,
                "sessionId": session_id,
                "turnId": turn_id,
                "messageSummary": summarize_text(message),
                "plan": plan,
                "shellStatus": shell_payload.get("status") if shell_payload else "none",
                "skillStatus": skill_payload.get("status") if skill_payload else "none",
                "skillTool": skill_payload.get("tool") if skill_payload else "",
            }
        )

        payload = {
            "ok": True,
            "session_id": session_id,
            "sessionId": session_id,
            "turn_id": turn_id,
            "turnId": turn_id,
            "observe": observe,
            "plan": plan,
        }
        if int(reasoning_trace.get("itemCount") or 0) > 0:
            payload["reasoning"] = reasoning_trace
        if shell_payload is not None:
            payload["shell"] = shell_payload
            if shell_payload.get("approval_id"):
                payload["approval_id"] = shell_payload["approval_id"]
                payload["approvalId"] = shell_payload["approval_id"]
            if shell_payload.get("result"):
                payload["result"] = shell_payload["result"]
        if skill_payload is not None:
            payload["skill"] = skill_payload
        return payload

    def _restore_runtime_session(self, session_id: str, history: list[dict[str, Any]], now: str) -> int:
        """Rebuild an in-memory session from a client-supplied transcript (history replay).

        The frontend resends the full prior conversation on every continued chat, so a
        restarted backend can recover lost session context. No-op when the session
        already holds live turns.
        """
        if not session_id:
            return 0
        with self._lock:
            session = self._runtime_sessions.get(session_id)
            if session and session.get("turns"):
                return 0
            turns: list[dict[str, Any]] = []
            for index, entry in enumerate(history):
                text = str(entry.get("text") or entry.get("message") or "").strip()
                if not text:
                    continue
                role = str(entry.get("role") or "user").strip().lower()
                if role not in ("user", "agent"):
                    role = "user"
                turns.append(
                    {
                        "id": f"restored_{index:04d}",
                        "createdAt": str(entry.get("createdAt") or now),
                        "restored": True,
                        "role": role,
                        "message": text,
                    }
                )
            if not turns:
                return 0
            self._runtime_sessions[session_id] = {
                "id": session_id,
                "createdAt": now,
                "updatedAt": now,
                "restoredFromTranscript": True,
                "turns": turns,
            }
            return len(turns)

    def runtime_observe(self, session_id: str | None = None) -> dict[str, Any]:
        config = self.ensure_config()
        user_constraints = self.read_user_constraints()
        pending = [item for item in self.list_approvals(include_expired=False) if item.get("status") == "pending"]
        session = self._runtime_sessions.get(session_id or "")
        return {
            "ok": True,
            "runtime": {
                "alive": True,
                "executionMode": normalize_execution_mode(config.execution_mode),
                "gatewayEnabled": config.enabled,
            },
            "workspaceRoot": str(self.default_workspace_root),
            "userConstraints": self._serialize_user_constraints(user_constraints, include_error=True),
            "approvalQueue": {
                "pendingCount": len(pending),
            },
            "shellExecutor": {
                "available": True,
                "shell": "powershell",
                "timeoutSeconds": 120,
            },
            "deterministicPlanner": {
                "available": True,
            },
            "tools": {
                "count": len(self.build_manifest().get("tools", [])),
            },
            "skills": summarize_skill_registry(self.build_skill_registry()),
            "session": {
                "id": session_id or "",
                "turnCount": len(session.get("turns", [])) if isinstance(session, dict) else 0,
                "restoredFromTranscript": bool(session.get("restoredFromTranscript")) if isinstance(session, dict) else False,
            },
        }

    def get_runtime_session(self, session_id: str) -> dict[str, Any]:
        session = self._runtime_sessions.get(session_id)
        if not session:
            raise AgentGatewayError(f"Runtime session was not found: {session_id}", status_code=404)
        return {"ok": True, "session": session}

    def classify_shell(self, params: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(params, str):
            params = {"command": params}
        command = str(params.get("command") or "").strip()
        workspace_root = self._resolve_workspace_root(params)
        cwd = self._resolve_cwd(params, workspace_root)
        reasons: list[str] = []

        if not command:
            return self._shell_classification(command, cwd, workspace_root, "reject", ["Command is empty."])
        if len(command) > 4000:
            return self._shell_classification(command, cwd, workspace_root, "reject", ["Command is too long."])

        if not is_path_within(cwd, workspace_root):
            reasons.append("cwd is outside the workspace root.")

        lowered = command.lower()
        if "\n" in command or "\r" in command:
            reasons.append("Command contains multiple lines.")
        if re.search(r"&&|\|\||[;|]|(?:^|\s)(?:\d?>|\*>|>>)", command):
            reasons.append("Command contains chaining, pipeline, or redirection syntax.")
        if "$(" in command or "{" in command or "}" in command or '@"' in command or "@'" in command:
            reasons.append("Command contains advanced PowerShell syntax.")
        if re.search(r"(^|\s|['\"])(?:\\\\|[a-zA-Z]:\\)", command):
            outside_paths = [
                token
                for token in tokenize_command(command)
                if looks_like_absolute_path(strip_quotes(token)) and not is_path_within(Path(strip_quotes(token)), workspace_root)
            ]
            if outside_paths:
                reasons.append("Command references an absolute path outside the workspace root.")
        if ".." in [part for token in tokenize_command(command) for part in re.split(r"[\\/]+", strip_quotes(token))]:
            reasons.append("Command contains parent path traversal.")
        if re.search(r"\.(ps1|bat|cmd|exe)(?:\s|$)", lowered):
            reasons.append("Command executes a script or executable directly.")

        tokens = tokenize_command(command)
        if not tokens:
            return self._shell_classification(command, cwd, workspace_root, "reject", ["Command could not be parsed."])

        if reasons:
            return self._shell_classification(command, cwd, workspace_root, "high", reasons)

        command_name = strip_quotes(tokens[0]).lower()
        args = [strip_quotes(token) for token in tokens[1:]]
        low_reasons = self._low_risk_reasons(command_name, args, workspace_root)
        if low_reasons:
            return self._shell_classification(command, cwd, workspace_root, "low", low_reasons)

        return self._shell_classification(command, cwd, workspace_root, "high", ["Command is not in the low-risk allowlist."])

    def execute_shell(
        self,
        params: dict[str, Any],
        agent_name: str = "desktop-agent",
    ) -> dict[str, Any]:
        classification = self.classify_shell(params)
        command = classification["command"]
        if classification["risk"] == "reject":
            self.append_audit({"event": "shell_rejected", "classification": classification, "agent": agent_name})
            return {"ok": False, "status": "rejected", "classification": classification, "error": "; ".join(classification["reasons"])}

        if classification["risk"] == "high":
            approval = self._create_shell_approval(params, classification, agent_name)
            if self.auto_approval_enabled():
                auto_payload = self._auto_execute_approval(approval)
                if auto_payload is not None:
                    auto_payload["classification"] = classification
                    return auto_payload
            return {
                "ok": True,
                "status": "pending_approval",
                "classification": classification,
                "approval": approval,
                "approval_id": approval["id"],
                "approvalId": approval["id"],
            }

        result = self._run_shell_command(command, Path(classification["cwd"]), timeout_seconds=int(params.get("timeout_seconds") or 120))
        self.append_audit(
            {
                "event": "shell_executed",
                "agent": agent_name,
                "classification": classification,
                "result": summarize_shell_result(result),
            }
        )
        return {"ok": result["ok"], "status": "executed", "classification": classification, "result": result}

    def execute_approved_shell(self, params: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(params.get("approval_id") or params.get("approvalId") or "").strip()
        if not approval_id:
            raise AgentGatewayError("approval_id is required.")
        approval = self._approvals.get(approval_id) or self._load_approval_from_audit(approval_id)
        if not approval:
            raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)
        if approval.get("targetTool") != "vrcforge_shell_execute":
            raise AgentGatewayError("Approval is not a shell execution approval.", status_code=400)
        return self.apply_approved({"approval_id": approval_id})

    def execute_shell_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        command = str(params.get("command") or "").strip()
        expected_hash = str(params.get("command_hash") or params.get("commandHash") or "")
        if expected_hash and expected_hash != command_hash(command):
            raise AgentGatewayError("Stored shell approval command hash does not match.")
        workspace_root = self._resolve_workspace_root(params)
        cwd = self._resolve_cwd(params, workspace_root)
        timeout_seconds = int(params.get("timeout_seconds") or params.get("timeoutSeconds") or 120)
        expected_cwd_hash = str(params.get("cwd_hash") or params.get("cwdHash") or "")
        expected_workspace_hash = str(params.get("workspace_root_hash") or params.get("workspaceRootHash") or "")
        expected_timeout_hash = str(params.get("timeout_hash") or params.get("timeoutHash") or "")
        if expected_cwd_hash and expected_cwd_hash != stable_hash(str(cwd)):
            raise AgentGatewayError("Stored shell approval cwd hash does not match.")
        if expected_workspace_hash and expected_workspace_hash != stable_hash(str(workspace_root)):
            raise AgentGatewayError("Stored shell approval workspace root hash does not match.")
        if expected_timeout_hash and expected_timeout_hash != stable_hash(str(timeout_seconds)):
            raise AgentGatewayError("Stored shell approval timeout hash does not match.")

        classification = self.classify_shell(
            {
                "command": command,
                "cwd": str(cwd),
                "workspace_root": str(workspace_root),
            }
        )
        if classification.get("risk") == "reject":
            raise AgentGatewayError("Approved shell command is no longer executable: " + "; ".join(classification.get("reasons") or []))
        if classification.get("commandHash") != expected_hash:
            raise AgentGatewayError("Reclassified shell command hash does not match approval.")

        result = self._run_shell_command(command, cwd, timeout_seconds=timeout_seconds)
        self.append_audit(
            {
                "event": "shell_approved_executed",
                "sessionId": params.get("session_id") or params.get("sessionId") or "",
                "turnId": params.get("turn_id") or params.get("turnId") or "",
                "commandHash": command_hash(command),
                "cwdHash": stable_hash(str(cwd)),
                "workspaceRootHash": stable_hash(str(workspace_root)),
                "timeoutHash": stable_hash(str(timeout_seconds)),
                "cwd": str(cwd),
                "workspaceRoot": str(workspace_root),
                "result": summarize_shell_result(result),
            }
        )
        return result

    def create_apply_request(self, params: dict[str, Any], *, internal_wrapper: bool = False) -> dict[str, Any]:
        config = self.ensure_config()
        if not config.allow_write_requests:
            raise AgentGatewayError("Agent Gateway write requests are disabled.", status_code=403)

        target_tool = str(params.get("target_tool") or params.get("targetTool") or "").strip()
        if not target_tool:
            raise AgentGatewayError("target_tool is required.")
        if target_tool in WRAPPER_ONLY_WRITE_TARGETS and not internal_wrapper:
            raise AgentGatewayError(
                f"{target_tool} can only be requested through its dedicated VRCForge request tool.",
                status_code=403,
            )

        write_handler = self._write_handlers.get(target_tool)
        if not write_handler or not self._write_handler_visible(write_handler, config):
            raise AgentGatewayError(f"Unknown or unavailable write target: {target_tool}", status_code=404)

        arguments = ensure_dict(params.get("arguments") or params.get("params") or {})
        user_constraints = self.read_user_constraints()
        arguments = self._inject_user_constraints_for_apply(arguments, user_constraints)
        preview = params.get("preview")
        if user_constraints.content and isinstance(preview, dict):
            preview = {
                **preview,
                "userConstraintsApplied": True,
                "userConstraintsPath": str(user_constraints.path),
            }
        approval = self._new_approval(
            agent_name=str(params.get("agent_name") or params.get("agentName") or "external-agent"),
            target_tool=target_tool,
            arguments=arguments,
            reason=str(params.get("reason") or ""),
            preview=preview,
            risk_level=write_handler.risk_level,
            user_constraints=user_constraints,
        )
        if self.auto_approval_enabled(config):
            auto_payload = self._auto_execute_approval(approval)
            if auto_payload is not None:
                return auto_payload
        return {
            "ok": True,
            "status": "pending",
            "approval": approval,
            "message": "Apply request is waiting for user approval.",
        }

    def _auto_execute_approval(self, approval: dict[str, Any]) -> dict[str, Any] | None:
        """Auto-approve and apply an approval under the auto / full-permission tiers.

        Returns the execution payload, or None when auto-approval could not
        proceed (caller then falls back to the normal pending flow). The
        approval record and audit trail are still produced, so every auto
        decision stays reviewable.
        """
        approval_id = str(approval.get("id") or "").strip()
        if not approval_id:
            return None
        approved = self.approve(approval_id)
        if not approved.get("ok"):
            return None
        self.append_audit(
            {
                "event": "approval_auto_approved",
                "approvalId": approval_id,
                "mode": normalize_execution_mode(self.ensure_config().execution_mode),
            }
        )
        applied = self.apply_approved({"approval_id": approval_id})
        payload: dict[str, Any] = {
            "ok": bool(applied.get("ok")),
            "status": "executed" if applied.get("ok") else "failed",
            "autoApproved": True,
            "approval": applied.get("approval") or approved.get("approval") or approval,
            "approval_id": approval_id,
            "approvalId": approval_id,
            "message": "Approval was auto-approved by the current permission mode.",
        }
        if applied.get("result") is not None:
            payload["result"] = applied.get("result")
        if not applied.get("ok"):
            payload["error"] = str(applied.get("error") or "Auto-approved execution failed.")
        return payload

    def apply_approved(self, params: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(params.get("approval_id") or params.get("approvalId") or "").strip()
        if not approval_id:
            raise AgentGatewayError("approval_id is required.")

        with self._lock:
            approval = self._approvals.get(approval_id)
            if not approval:
                approval = self._load_approval_from_audit(approval_id)
            if not approval:
                raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)

            approval = self._refresh_approval_expiry(approval)
            if approval.get("status") != "approved":
                return {
                    "ok": False,
                    "status": approval.get("status"),
                    "approval": approval,
                    "message": "Approval is not approved yet.",
                }

            target_tool = str(approval.get("targetTool") or "")
            write_handler = self._write_handlers.get(target_tool)
            if not write_handler:
                raise AgentGatewayError(f"Write target is no longer available: {target_tool}", status_code=404)

            approval["status"] = "applying"
            self._approvals[approval_id] = approval
            self.append_audit({"event": "approval_applying", "approval": approval})

        checkpoint: dict[str, Any] | None = None
        try:
            user_constraints = self.read_user_constraints()
            arguments = self._inject_user_constraints_for_apply(
                ensure_dict(approval.get("arguments") or {}),
                user_constraints,
            )
            checkpoint = self._create_pre_write_checkpoint(approval, arguments)
            if checkpoint:
                approval["checkpoint"] = checkpoint
                if checkpoint.get("blocking"):
                    raise AgentGatewayError(str(checkpoint.get("error") or "Pre-write checkpoint failed."))
            result = write_handler.handler(arguments)
            with self._lock:
                approval["status"] = "applied"
                approval["appliedAt"] = utc_now_iso()
                approval["resultSummary"] = summarize_params(result if isinstance(result, dict) else {"result": result})
                self._approvals[approval_id] = approval
                self.append_audit({"event": "approval_applied", "approval": approval})
            payload = {"ok": True, "status": "applied", "approval": approval, "result": result}
            if checkpoint:
                payload["checkpoint"] = checkpoint
            return payload
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                approval["status"] = "failed"
                approval["failedAt"] = utc_now_iso()
                approval["error"] = str(exc)
                self._approvals[approval_id] = approval
                self.append_audit({"event": "approval_failed", "approval": approval})
            payload = {"ok": False, "status": "failed", "approval": approval, "error": str(exc)}
            if checkpoint:
                payload["checkpoint"] = checkpoint
            return payload

    def list_approvals(self, include_expired: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            approvals = [self._refresh_approval_expiry(dict(item)) for item in self._approvals.values()]
            if include_expired:
                return [
                    redact_sensitive(item)
                    for item in sorted(approvals, key=lambda item: str(item.get("createdAt") or ""), reverse=True)
                ]
            filtered = [
                item
                for item in sorted(approvals, key=lambda approval: str(approval.get("createdAt") or ""), reverse=True)
                if item.get("status") != "expired"
            ]
            return [redact_sensitive(item) for item in filtered]

    def approve(self, approval_id: str) -> dict[str, Any]:
        return self._set_approval_status(approval_id, "approved")

    def reject(self, approval_id: str) -> dict[str, Any]:
        return self._set_approval_status(approval_id, "rejected")

    def recent_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.audit_log_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.audit_log_path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def list_checkpoints(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        limit = max(1, min(int(params.get("limit") or 50), 500))
        project_filter = str(params.get("project_root") or params.get("projectRoot") or "").strip()
        entries = self._read_checkpoint_entries(limit=500)
        if project_filter:
            normalized = normalize_filesystem_path(project_filter)
            entries = [entry for entry in entries if normalize_filesystem_path(str(entry.get("projectRoot") or "")) == normalized]
        entries = entries[:limit]
        return {"ok": True, "checkpoints": entries, "count": len(entries)}

    def preview_restore_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        checkpoint = self._load_checkpoint(str(params.get("checkpoint_id") or params.get("checkpointId") or "").strip())
        if not checkpoint:
            return {"ok": False, "error": "checkpoint_id was not found."}
        available = self._checkpoint_available(checkpoint)
        if not available.get("ok"):
            return available
        if checkpoint.get("strategy") == "archive":
            return self._preview_archive_checkpoint(checkpoint)
        git_root = Path(str(checkpoint["gitRoot"]))
        ref = str(checkpoint["checkpointRef"])
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        diff = self._run_git(git_root, ["diff", "--name-status", ref, "--", *pathspecs])
        status = self._run_git(git_root, ["status", "--porcelain", "--", *pathspecs])
        return {
            "ok": diff["ok"] and status["ok"],
            "checkpoint": checkpoint,
            "changedFiles": [line for line in diff["stdout"].splitlines() if line.strip()],
            "workingTreeStatus": [line for line in status["stdout"].splitlines() if line.strip()],
            "error": diff.get("error") or status.get("error") or "",
        }

    def restore_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        checkpoint = self._load_checkpoint(str(params.get("checkpoint_id") or params.get("checkpointId") or "").strip())
        if not checkpoint:
            return {"ok": False, "error": "checkpoint_id was not found."}
        if params.get("confirm_restore") is not True and params.get("confirmRestore") is not True:
            return {"ok": False, "error": "confirmRestore=true is required to restore a checkpoint."}
        available = self._checkpoint_available(checkpoint)
        if not available.get("ok"):
            return available
        if checkpoint.get("strategy") == "archive":
            payload = self._restore_archive_checkpoint(checkpoint)
        else:
            git_root = Path(str(checkpoint["gitRoot"]))
            ref = str(checkpoint["checkpointRef"])
            pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
            restore = self._run_git(git_root, ["restore", "--source", ref, "--staged", "--worktree", "--", *pathspecs], timeout_seconds=120)
            if not restore["ok"]:
                return {"ok": False, "checkpoint": checkpoint, "error": restore["error"], "stdout": restore["stdout"], "stderr": restore["stderr"]}
            clean = self._run_git(git_root, ["clean", "-fd", "--", *pathspecs], timeout_seconds=120)
            payload = {
                "ok": clean["ok"],
                "checkpoint": checkpoint,
                "restoredRef": ref,
                "cleaned": [line for line in clean["stdout"].splitlines() if line.strip()],
                "error": clean.get("error") or "",
            }
        if payload.get("ok") and self.checkpoint_restore_handler is not None:
            try:
                reload_result = ensure_dict(self.checkpoint_restore_handler(Path(str(checkpoint["projectRoot"]))))
            except Exception as exc:  # noqa: BLE001
                reload_result = {"ok": False, "error": str(exc)}
            payload["unityReload"] = reload_result
            if not reload_result.get("ok"):
                payload["unityReloadWarning"] = str(
                    reload_result.get("error") or "Unity did not reload after checkpoint restore."
                )
                payload["status"] = "restored_with_unity_reload_warning"
            else:
                payload["status"] = "restored"
        self.append_audit({"event": "checkpoint_restored", **payload})
        return payload

    def _create_pre_write_checkpoint(self, approval: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any] | None:
        target_tool = str(approval.get("targetTool") or "")
        if not target_tool or target_tool == "vrcforge_restore_checkpoint":
            return None
        project_root = self._resolve_checkpoint_project_root(arguments)
        checkpoint_id = f"ckpt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        base_record = {
            "id": checkpoint_id,
            "createdAt": utc_now_iso(),
            "approvalId": str(approval.get("id") or ""),
            "targetTool": target_tool,
            "status": "unavailable",
        }
        if project_root is None:
            record = {**base_record, "ok": False, "error": "No Unity project root was available for checkpointing."}
            self._append_checkpoint(record)
            return record
        project_root = project_root.resolve()
        record = {**base_record, "projectRoot": str(project_root)}
        if not self._is_unity_project_root(project_root):
            record.update({"ok": False, "error": "Resolved checkpoint root is not a Unity project."})
            self._append_checkpoint(record)
            return record

        if self.checkpoint_prepare_handler is not None:
            try:
                prepare_result = ensure_dict(self.checkpoint_prepare_handler(project_root))
            except Exception as exc:  # noqa: BLE001
                prepare_result = {"ok": False, "error": str(exc)}
            record["unityPrepare"] = prepare_result
            if not prepare_result.get("ok"):
                record.update(
                    {
                        "ok": False,
                        "blocking": True,
                        "status": "failed",
                        "error": str(prepare_result.get("error") or "Unity could not prepare a rollback checkpoint."),
                    }
                )
                self._append_checkpoint(record)
                return record

        git_root_result = self._run_git(project_root, ["rev-parse", "--show-toplevel"])
        if not git_root_result["ok"]:
            return self._create_archive_checkpoint(project_root, record)
        git_root = Path(git_root_result["stdout"].strip()).resolve()
        pathspecs = self._checkpoint_pathspecs(git_root, project_root)
        base_commit_result = self._run_git(git_root, ["rev-parse", "HEAD"])
        base_commit = base_commit_result["stdout"].strip() if base_commit_result["ok"] else ""

        status_before = self._run_git(git_root, ["status", "--porcelain", "--", *pathspecs])
        add_result = self._run_git(git_root, ["add", "-A", "--", *pathspecs], timeout_seconds=120)
        if not add_result["ok"]:
            record.update(
                {
                    "ok": False,
                    "blocking": True,
                    "status": "failed",
                    "gitRoot": str(git_root),
                    "pathspecs": pathspecs,
                    "baseCommit": base_commit,
                    "error": add_result["error"] or "git add failed while creating checkpoint.",
                }
            )
            self._append_checkpoint(record)
            return record

        staged_diff = self._run_git(git_root, ["diff", "--cached", "--quiet", "--", *pathspecs])
        created_commit = False
        checkpoint_ref = base_commit
        if staged_diff["returncode"] == 1:
            message = f"chore(vrcforge): checkpoint before {target_tool} {checkpoint_id}"
            commit_result = self._run_git(
                git_root,
                [
                    "-c",
                    "user.name=VRCForge",
                    "-c",
                    "user.email=vrcforge@example.invalid",
                    "commit",
                    "--no-verify",
                    "-m",
                    message,
                ],
                timeout_seconds=120,
            )
            if not commit_result["ok"]:
                record.update(
                    {
                        "ok": False,
                        "blocking": True,
                        "status": "failed",
                        "gitRoot": str(git_root),
                        "pathspecs": pathspecs,
                        "baseCommit": base_commit,
                        "error": commit_result["error"] or "git commit failed while creating checkpoint.",
                        "stdout": commit_result["stdout"],
                        "stderr": commit_result["stderr"],
                    }
                )
                self._append_checkpoint(record)
                return record
            created_commit = True
            head_result = self._run_git(git_root, ["rev-parse", "HEAD"])
            checkpoint_ref = head_result["stdout"].strip() if head_result["ok"] else base_commit
        elif staged_diff["returncode"] not in {0, 1}:
            record.update(
                {
                    "ok": False,
                    "blocking": True,
                    "status": "failed",
                    "gitRoot": str(git_root),
                    "pathspecs": pathspecs,
                    "baseCommit": base_commit,
                    "error": staged_diff["error"] or "git diff failed while creating checkpoint.",
                }
            )
            self._append_checkpoint(record)
            return record

        record.update(
            {
                "ok": True,
                "status": "ready",
                "strategy": "git",
                "gitRoot": str(git_root),
                "pathspecs": pathspecs,
                "baseCommit": base_commit,
                "checkpointRef": checkpoint_ref,
                "createdCommit": created_commit,
                "statusBefore": [line for line in status_before["stdout"].splitlines() if line.strip()] if status_before["ok"] else [],
            }
        )
        self._append_checkpoint(record)
        self.append_audit({"event": "checkpoint_created", "checkpoint": record})
        return record

    def _create_archive_checkpoint(self, project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
        checkpoint_id = str(record["id"])
        project_key = hashlib.sha256(normalize_filesystem_path(str(project_root)).encode("utf-8")).hexdigest()[:16]
        archive_dir = self.checkpoint_store_dir / project_key
        archive_path = archive_dir / f"{checkpoint_id}.zip"
        temp_path = archive_path.with_suffix(".zip.tmp")
        pathspecs = [name for name in ("Assets", "Packages", "ProjectSettings") if (project_root / name).is_dir()]
        file_count = 0
        total_bytes = 0
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            if temp_path.exists():
                temp_path.unlink()
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                for name in pathspecs:
                    root = project_root / name
                    for source in sorted(root.rglob("*")):
                        if not source.is_file():
                            continue
                        relative = source.relative_to(project_root).as_posix()
                        archive.write(source, relative)
                        file_count += 1
                        total_bytes += source.stat().st_size
            os.replace(temp_path, archive_path)
        except Exception as exc:  # noqa: BLE001
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
            record.update(
                {
                    "ok": False,
                    "blocking": True,
                    "status": "failed",
                    "strategy": "archive",
                    "archivePath": str(archive_path),
                    "pathspecs": pathspecs,
                    "error": f"Archive checkpoint failed: {exc}",
                }
            )
            self._append_checkpoint(record)
            return record

        record.update(
            {
                "ok": True,
                "status": "ready",
                "strategy": "archive",
                "archivePath": str(archive_path),
                "pathspecs": pathspecs,
                "fileCount": file_count,
                "uncompressedBytes": total_bytes,
            }
        )
        self._append_checkpoint(record)
        self.append_audit({"event": "checkpoint_created", "checkpoint": record})
        return record

    def _preview_archive_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        project_root = Path(str(checkpoint["projectRoot"])).resolve()
        archive_path = Path(str(checkpoint["archivePath"])).resolve()
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                archived = {
                    info.filename: (info.file_size, info.CRC)
                    for info in archive.infolist()
                    if not info.is_dir()
                }
            current: dict[str, tuple[int, int]] = {}
            for name in pathspecs:
                root = project_root / name
                if not root.is_dir():
                    continue
                for source in root.rglob("*"):
                    if not source.is_file():
                        continue
                    relative = source.relative_to(project_root).as_posix()
                    crc = 0
                    with source.open("rb") as handle:
                        while chunk := handle.read(1024 * 1024):
                            crc = zlib.crc32(chunk, crc)
                    current[relative] = (source.stat().st_size, crc & 0xFFFFFFFF)
            changed = [f"M\t{name}" for name in sorted(archived.keys() & current.keys()) if archived[name] != current[name]]
            changed.extend(f"D\t{name}" for name in sorted(archived.keys() - current.keys()))
            changed.extend(f"A\t{name}" for name in sorted(current.keys() - archived.keys()))
            return {"ok": True, "checkpoint": checkpoint, "changedFiles": changed, "workingTreeStatus": changed, "error": ""}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checkpoint": checkpoint, "error": str(exc)}

    def _restore_archive_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        project_root = Path(str(checkpoint["projectRoot"])).resolve()
        archive_path = Path(str(checkpoint["archivePath"])).resolve()
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        allowed = {"Assets", "Packages", "ProjectSettings"}
        if not pathspecs or any(name not in allowed for name in pathspecs):
            return {"ok": False, "checkpoint": checkpoint, "error": "Archive checkpoint pathspecs are unsafe."}
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                members = [info for info in archive.infolist() if not info.is_dir()]
                archived = {info.filename: info for info in members}
                for info in members:
                    parts = Path(info.filename).parts
                    if not parts or parts[0] not in allowed or ".." in parts or Path(info.filename).is_absolute():
                        raise ValueError(f"Unsafe archive member: {info.filename}")
                current: dict[str, Path] = {}
                for name in pathspecs:
                    target = (project_root / name).resolve()
                    if target.parent != project_root or target.name not in allowed:
                        raise ValueError(f"Unsafe restore target: {target}")
                    target.mkdir(parents=True, exist_ok=True)
                    for source in target.rglob("*"):
                        if source.is_file():
                            current[source.relative_to(project_root).as_posix()] = source

                deleted: list[str] = []
                for relative in sorted(current.keys() - archived.keys()):
                    current[relative].unlink()
                    deleted.append(relative)

                restored: list[str] = []
                for relative, info in archived.items():
                    target = project_root / Path(relative)
                    needs_restore = not target.is_file() or target.stat().st_size != info.file_size
                    if not needs_restore:
                        crc = 0
                        with target.open("rb") as handle:
                            while chunk := handle.read(1024 * 1024):
                                crc = zlib.crc32(chunk, crc)
                        needs_restore = (crc & 0xFFFFFFFF) != info.CRC
                    if not needs_restore:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp_target = target.with_name(target.name + ".vrcforge-restore-tmp")
                    with archive.open(info, "r") as source, temp_target.open("wb") as destination:
                        shutil.copyfileobj(source, destination, length=1024 * 1024)
                    os.replace(temp_target, target)
                    restored.append(relative)

                for name in pathspecs:
                    root = project_root / name
                    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
                        try:
                            directory.rmdir()
                        except OSError:
                            pass
            return {
                "ok": True,
                "checkpoint": checkpoint,
                "restoredArchive": str(archive_path),
                "restoredFileCount": len(restored),
                "restoredFiles": restored,
                "deletedFileCount": len(deleted),
                "deletedFiles": deleted,
                "cleaned": deleted,
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checkpoint": checkpoint, "error": f"Archive restore failed: {exc}"}

    def _resolve_checkpoint_project_root(self, arguments: dict[str, Any]) -> Path | None:
        for key in (
            "project_root",
            "projectRoot",
            "project_path",
            "projectPath",
            "unity_project",
            "unityProject",
            "workspace_root",
            "workspaceRoot",
            "cwd",
        ):
            value = str(arguments.get(key) or "").strip()
            if value:
                return Path(value)
        checkpoint_id = str(arguments.get("checkpoint_id") or arguments.get("checkpointId") or "").strip()
        if checkpoint_id:
            checkpoint = self._load_checkpoint(checkpoint_id)
            if checkpoint and checkpoint.get("projectRoot"):
                return Path(str(checkpoint["projectRoot"]))
        if self.checkpoint_project_root_resolver is not None:
            value = str(self.checkpoint_project_root_resolver() or "").strip()
            if value:
                return Path(value)
        return None

    def _checkpoint_available(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        if not checkpoint.get("ok"):
            return {"ok": False, "checkpoint": checkpoint, "error": str(checkpoint.get("error") or "Checkpoint is unavailable.")}
        if checkpoint.get("strategy") == "archive":
            archive_path = Path(str(checkpoint.get("archivePath") or ""))
            pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
            if not archive_path.is_file() or not pathspecs:
                return {"ok": False, "checkpoint": checkpoint, "error": "Archive checkpoint metadata is incomplete."}
            try:
                with zipfile.ZipFile(archive_path, "r") as archive:
                    if archive.testzip() is not None:
                        raise ValueError("archive CRC validation failed")
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "checkpoint": checkpoint, "error": f"Archive checkpoint is unreadable: {exc}"}
            return {"ok": True}
        git_root = Path(str(checkpoint.get("gitRoot") or ""))
        ref = str(checkpoint.get("checkpointRef") or "")
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        if not git_root.exists() or not ref or not pathspecs:
            return {"ok": False, "checkpoint": checkpoint, "error": "Checkpoint metadata is incomplete."}
        verify = self._run_git(git_root, ["cat-file", "-e", f"{ref}^{{commit}}"])
        if not verify["ok"]:
            return {"ok": False, "checkpoint": checkpoint, "error": "Checkpoint git ref is no longer available."}
        return {"ok": True}

    def _append_checkpoint(self, record: dict[str, Any]) -> None:
        self.checkpoint_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.checkpoint_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_checkpoint_entries(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.checkpoint_log_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.checkpoint_log_path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                entries.append(payload)
        return list(reversed(entries[-max(1, min(limit, 1000)) :]))

    def _load_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        if not checkpoint_id:
            return None
        for entry in self._read_checkpoint_entries(limit=1000):
            if entry.get("id") == checkpoint_id:
                return entry
        return None

    def _checkpoint_pathspecs(self, git_root: Path, project_root: Path) -> list[str]:
        try:
            relative_project = project_root.resolve().relative_to(git_root.resolve())
            prefix = "" if str(relative_project) == "." else relative_project.as_posix().rstrip("/") + "/"
        except ValueError:
            prefix = ""
        names = ["Assets", "Packages", "ProjectSettings"]
        return [prefix + name for name in names if (project_root / name).exists()] or [prefix + name for name in names]

    def _is_unity_project_root(self, path: Path) -> bool:
        return (path / "Assets").is_dir() and (path / "Packages").is_dir() and (path / "ProjectSettings").is_dir()

    def _run_git(self, cwd: Path, args: list[str], timeout_seconds: int = 30) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
                "error": "" if proc.returncode == 0 else (proc.stderr or proc.stdout or f"git exited {proc.returncode}").strip(),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "returncode": -1, "stdout": "", "stderr": "", "error": str(exc)}

    def read_user_constraints(self) -> UserConstraintsSnapshot:
        path = self.user_constraints_path
        try:
            if not path.exists():
                return UserConstraintsSnapshot(
                    path=path,
                    content="",
                    status="ok",
                    message="User AGENTS.md is not configured.",
                )
            content = path.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError) as exc:
            return UserConstraintsSnapshot(
                path=path,
                content="",
                status="warning",
                message="User AGENTS.md could not be read.",
                error=str(exc),
            )
        return UserConstraintsSnapshot(
            path=path,
            content=content,
            status="ok",
            message="User constraints are active." if content else "User AGENTS.md is empty.",
        )

    def _builtin_skill_definitions(self, config: AgentGatewayConfig) -> list[dict[str, Any]]:
        skills: list[dict[str, Any]] = []
        for group in BUILTIN_SKILL_GROUPS:
            skills.append(self._skill_from_builtin_group(group, config))
        for tool in self._tools.values():
            skills.append(self._skill_from_tool(tool, config))
        for handler in self._write_handlers.values():
            skills.append(self._skill_from_write_handler(handler, config))
        return sorted(skills, key=lambda item: (str(item.get("category") or ""), str(item.get("name") or "")))

    def _skill_from_builtin_group(self, group: dict[str, Any], config: AgentGatewayConfig) -> dict[str, Any]:
        allowed_tools = ensure_string_list(group.get("allowedTools") or group.get("tools"))
        permission_mode = normalize_skill_permission(group.get("permissionMode"))
        available = bool(group.get("enabled", True)) and all(
            self._skill_dependency_visible(tool_name, config) for tool_name in allowed_tools
        )
        return {
            "schema": "vrcforge.skill.v1",
            "name": str(group.get("name") or ""),
            "title": str(group.get("title") or title_from_name(str(group.get("name") or ""))),
            "description": str(group.get("description") or ""),
            "category": str(group.get("category") or "builtin"),
            "source": "builtin",
            "skillType": "group",
            "enabled": bool(group.get("enabled", True)),
            "available": available,
            "permissionMode": permission_mode,
            "riskLevel": normalize_risk_level(group.get("riskLevel")),
            "whenToUse": str(group.get("whenToUse") or ""),
            "inputs": ensure_string_list(group.get("inputs")),
            "outputs": ensure_string_list(group.get("outputs")),
            "sideEffects": str(group.get("sideEffects") or "none"),
            "backupRestore": str(group.get("backupRestore") or "not required"),
            "tools": allowed_tools,
            "allowedTools": allowed_tools,
            "disallowedTools": ensure_string_list(group.get("disallowedTools")),
            "entrypointTool": str(group.get("entrypointTool") or ""),
            "userInvocable": normalize_bool(group.get("userInvocable"), True),
            "disableModelInvocation": normalize_bool(group.get("disableModelInvocation"), False),
            "argumentHint": str(group.get("argumentHint") or ""),
            "requiresEnv": ensure_string_list(group.get("requiresEnv")),
            "requiresBinaries": ensure_string_list(group.get("requiresBinaries")),
            "supportedOs": ensure_string_list(group.get("supportedOs")),
            "supportFiles": ensure_string_list(group.get("supportFiles")),
            "testCommand": str(group.get("testCommand") or ""),
            "instructions": str(group.get("instructions") or ""),
            "advanced": permission_mode == "advanced_power_mode",
            "write": permission_mode in {"approval_required", "advanced_power_mode"},
            "tags": sorted({"builtin", "group", *ensure_string_list(group.get("tags"))}),
        }

    def _skill_from_tool(self, tool: AgentTool, config: AgentGatewayConfig) -> dict[str, Any]:
        override = BUILTIN_SKILL_OVERRIDES.get(tool.name, {})
        advanced = bool(tool.advanced)
        permission_mode = str(override.get("permissionMode") or self._permission_mode_for_tool(tool))
        tags = sorted({*ensure_string_list(override.get("tags")), tool.category, "builtin", *("advanced" if advanced else "",)})
        return {
            "schema": "vrcforge.skill.v1",
            "name": tool.name,
            "title": override.get("title") or title_from_name(tool.name),
            "description": tool.description,
            "category": tool.category,
            "source": "builtin",
            "skillType": "tool",
            "enabled": True,
            "available": self._tool_visible(tool, config),
            "permissionMode": permission_mode,
            "riskLevel": "critical" if advanced else "medium" if tool.write else "low",
            "whenToUse": override.get("whenToUse") or tool.description,
            "inputs": ensure_string_list(override.get("inputs")) or ["Tool-specific JSON arguments."],
            "outputs": ensure_string_list(override.get("outputs")) or ["Tool result JSON."],
            "sideEffects": override.get("sideEffects") or ("may request or perform approved writes" if tool.write else "none"),
            "backupRestore": override.get("backupRestore") or ("required before writes" if tool.write else "not required"),
            "tools": [tool.name],
            "allowedTools": [tool.name],
            "disallowedTools": [],
            "entrypointTool": tool.name,
            "userInvocable": True,
            "disableModelInvocation": False,
            "argumentHint": "",
            "requiresEnv": [],
            "requiresBinaries": [],
            "supportedOs": [],
            "supportFiles": [],
            "testCommand": override.get("testCommand") or "",
            "instructions": "",
            "advanced": advanced,
            "write": tool.write,
            "tags": [tag for tag in tags if tag],
        }

    def _skill_from_write_handler(self, handler: AgentWriteHandler, config: AgentGatewayConfig) -> dict[str, Any]:
        override = BUILTIN_SKILL_OVERRIDES.get(handler.name, {})
        advanced = bool(handler.advanced)
        tags = sorted({*ensure_string_list(override.get("tags")), "supervised-write", "builtin", *("advanced" if advanced else "",)})
        return {
            "schema": "vrcforge.skill.v1",
            "name": handler.name,
            "title": override.get("title") or title_from_name(handler.name),
            "description": handler.description,
            "category": "supervised-write",
            "source": "builtin",
            "skillType": "tool",
            "enabled": True,
            "available": self._write_handler_visible(handler, config),
            "permissionMode": str(override.get("permissionMode") or ("advanced_power_mode" if advanced else "approval_required")),
            "riskLevel": handler.risk_level,
            "whenToUse": override.get("whenToUse") or handler.description,
            "inputs": ensure_string_list(override.get("inputs")) or ["Approved write payload."],
            "outputs": ensure_string_list(override.get("outputs")) or ["Write result JSON and audit record."],
            "sideEffects": override.get("sideEffects") or "writes Unity project or generated artifacts after approval",
            "backupRestore": override.get("backupRestore") or "requires preview, backup, apply, validate, restore path",
            "tools": ["vrcforge_request_apply", "vrcforge_apply_approved", handler.name],
            "allowedTools": ["vrcforge_request_apply", "vrcforge_apply_approved", handler.name],
            "disallowedTools": [],
            "entrypointTool": handler.name,
            "userInvocable": True,
            "disableModelInvocation": False,
            "argumentHint": "",
            "requiresEnv": [],
            "requiresBinaries": [],
            "supportedOs": [],
            "supportFiles": [],
            "testCommand": override.get("testCommand") or "",
            "instructions": "",
            "advanced": advanced,
            "write": True,
            "tags": [tag for tag in tags if tag],
        }

    def _permission_mode_for_tool(self, tool: AgentTool) -> str:
        if tool.advanced:
            return "advanced_power_mode"
        if tool.write:
            return "approval_required"
        if tool.category == "plan/preview":
            return "preview"
        return "read_only"

    def _skill_dependency_visible(self, tool_name: str, config: AgentGatewayConfig) -> bool:
        tool_name = str(tool_name or "").strip()
        tool = self._tools.get(tool_name)
        if tool:
            return self._tool_visible(tool, config)
        handler = self._write_handlers.get(tool_name)
        if handler:
            return self._write_handler_visible(handler, config)
        return False

    @property
    def audit_log_path(self) -> Path:
        return self.audit_dir / "approvals.jsonl"

    @property
    def checkpoint_log_path(self) -> Path:
        return self.audit_dir / "checkpoints.jsonl"

    @property
    def checkpoint_store_dir(self) -> Path:
        return self.audit_dir / "checkpoint-archives"

    @property
    def user_constraints_path(self) -> Path:
        if self.config_path.parent.name.lower() == "config":
            return self.config_path.parent.parent / "AGENTS.md"
        user_data_dir = os.environ.get("VRCFORGE_USER_DATA_DIR", "").strip()
        if user_data_dir:
            return Path(user_data_dir) / "AGENTS.md"
        return self.config_path.parent / "AGENTS.md"

    @property
    def user_skills_dir(self) -> Path:
        if self.config_path.parent.name.lower() == "config":
            return self.config_path.parent.parent / "skills"
        user_data_dir = os.environ.get("VRCFORGE_USER_DATA_DIR", "").strip()
        if user_data_dir:
            return Path(user_data_dir) / "skills"
        return self.config_path.parent / "skills"

    def _load_user_skills(self) -> list[dict[str, Any]]:
        skills_dir = self.user_skills_dir
        if not skills_dir.exists():
            return []
        skills: list[dict[str, Any]] = []
        for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
            try:
                parsed = parse_skill_markdown(skill_file)
                normalized = self._normalize_user_skill(parsed, existing_id=str(parsed.get("name") or skill_file.parent.name))
                normalized["storagePath"] = str(skill_file)
                skills.append(normalized)
            except Exception as exc:  # noqa: BLE001 - one broken user skill must not break startup.
                fallback_name = normalize_skill_id(skill_file.parent.name)
                skills.append(
                    {
                        "schema": "vrcforge.skill.v1",
                        "name": fallback_name,
                        "title": fallback_name,
                        "description": "User skill could not be loaded.",
                        "category": "user",
                        "source": "user",
                        "skillType": "package",
                        "enabled": False,
                        "available": False,
                        "permissionMode": "instruction_only",
                        "riskLevel": "low",
                        "whenToUse": "",
                        "inputs": [],
                        "outputs": [],
                        "sideEffects": "none",
                        "backupRestore": "not required",
                        "tools": [],
                        "allowedTools": [],
                        "disallowedTools": [],
                        "entrypointTool": "",
                        "userInvocable": False,
                        "disableModelInvocation": True,
                        "argumentHint": "",
                        "requiresEnv": [],
                        "requiresBinaries": [],
                        "supportedOs": [],
                        "supportFiles": [],
                        "testCommand": "",
                        "instructions": "",
                        "advanced": False,
                        "write": False,
                        "tags": ["user", "invalid"],
                        "storagePath": str(skill_file),
                        "loadError": str(exc),
                    }
                )
        return skills

    def _find_user_skill(self, skill_id: str) -> dict[str, Any] | None:
        skill_id = normalize_skill_id(skill_id)
        for skill in self._load_user_skills():
            if skill.get("name") == skill_id:
                return skill
        return None

    def _save_user_skills(self, skills: list[dict[str, Any]]) -> None:
        skills_dir = self.user_skills_dir
        skills_dir.mkdir(parents=True, exist_ok=True)
        existing_dirs = {path.name: path for path in skills_dir.iterdir() if path.is_dir()}
        wanted = {str(skill.get("name") or "") for skill in skills}
        for name, path in existing_dirs.items():
            if name not in wanted and SKILL_ID_RE.fullmatch(name):
                remove_tree(path)
        for skill in skills:
            skill_id = normalize_skill_id(str(skill.get("name") or ""))
            skill_dir = skills_dir / skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(render_skill_markdown(skill), encoding="utf-8")

    def _normalize_user_skill(self, payload: dict[str, Any], existing_id: str | None = None) -> dict[str, Any]:
        skill_id = normalize_skill_id(str(first_payload_value(payload, "name") or existing_id or ""))
        if not SKILL_ID_RE.fullmatch(skill_id):
            raise AgentGatewayError("Skill name must match [a-z][a-z0-9_.-]{1,80}.", status_code=400)
        permission_mode = normalize_skill_permission(
            first_payload_value(payload, "permissionMode", "permission_mode", "permission-mode")
        )
        tools = ensure_string_list(first_payload_value(payload, "tools", "allowedTools", "allowed_tools", "allowed-tools"))
        allowed_tools = ensure_string_list(
            first_payload_value(payload, "allowedTools", "allowed_tools", "allowed-tools", default=tools)
        )
        disallowed_tools = ensure_string_list(first_payload_value(payload, "disallowedTools", "disallowed_tools", "disallowed-tools"))
        title = str(first_payload_value(payload, "title", default=title_from_name(skill_id))).strip()
        instructions = str(first_payload_value(payload, "instructions", "body", default="")).strip()
        return {
            "schema": "vrcforge.skill.v1",
            "name": skill_id,
            "title": title[:120],
            "description": str(first_payload_value(payload, "description", default="")).strip()[:500],
            "category": str(first_payload_value(payload, "category", default="user")).strip()[:80],
            "source": "user",
            "skillType": "package",
            "enabled": normalize_bool(first_payload_value(payload, "enabled", default=True), True),
            "available": normalize_bool(first_payload_value(payload, "enabled", default=True), True),
            "permissionMode": permission_mode,
            "riskLevel": normalize_risk_level(first_payload_value(payload, "riskLevel", "risk_level", "risk-level")),
            "whenToUse": str(first_payload_value(payload, "whenToUse", "when_to_use", "when-to-use", default="")).strip()[:1000],
            "inputs": ensure_string_list(first_payload_value(payload, "inputs")),
            "outputs": ensure_string_list(first_payload_value(payload, "outputs")),
            "sideEffects": str(
                first_payload_value(payload, "sideEffects", "side_effects", "side-effects", default="none")
            ).strip()[:500],
            "backupRestore": str(
                first_payload_value(payload, "backupRestore", "backup_restore", "backup-restore", default="not required")
            ).strip()[:500],
            "tools": tools,
            "allowedTools": allowed_tools,
            "disallowedTools": disallowed_tools,
            "entrypointTool": str(
                first_payload_value(payload, "entrypointTool", "entrypoint_tool", "entrypoint-tool", default="")
            ).strip(),
            "userInvocable": normalize_bool(
                first_payload_value(payload, "userInvocable", "user_invocable", "user-invocable", default=True),
                True,
            ),
            "disableModelInvocation": normalize_bool(
                first_payload_value(
                    payload,
                    "disableModelInvocation",
                    "disable_model_invocation",
                    "disable-model-invocation",
                    default=False,
                ),
                False,
            ),
            "argumentHint": str(
                first_payload_value(payload, "argumentHint", "argument_hint", "argument-hint", default="")
            ).strip()[:240],
            "requiresEnv": ensure_string_list(first_payload_value(payload, "requiresEnv", "requires_env", "requires-env")),
            "requiresBinaries": ensure_string_list(
                first_payload_value(payload, "requiresBinaries", "requires_binaries", "requires-binaries")
            ),
            "supportedOs": ensure_string_list(first_payload_value(payload, "supportedOs", "supported_os", "supported-os")),
            "supportFiles": ensure_string_list(first_payload_value(payload, "supportFiles", "support_files", "support-files")),
            "testCommand": str(first_payload_value(payload, "testCommand", "test_command", "test-command", default="")).strip()[:500],
            "instructions": instructions,
            "advanced": permission_mode == "advanced_power_mode",
            "write": permission_mode in {"approval_required", "advanced_power_mode"},
            "tags": sorted({"user", *ensure_string_list(first_payload_value(payload, "tags"))}),
        }

    def _ensure_user_skill_can_use_id(self, skill_id: str, skills: list[dict[str, Any]]) -> None:
        if skill_id in self._tools or skill_id in self._write_handlers:
            raise AgentGatewayError(f"Skill name conflicts with a builtin tool: {skill_id}", status_code=409)
        if any(skill.get("name") == skill_id for skill in skills):
            raise AgentGatewayError(f"User skill already exists: {skill_id}", status_code=409)

    def _decorate_skill_validation(self, skill: dict[str, Any], config: AgentGatewayConfig) -> dict[str, Any]:
        next_skill = dict(skill)
        validation = self._validate_skill(next_skill, config)
        next_skill["validation"] = validation
        next_skill["availabilityReasons"] = ensure_string_list(validation.get("reasons"))
        if validation.get("status") == "error":
            next_skill["available"] = False
        return next_skill

    def _validate_skill(self, skill: dict[str, Any], config: AgentGatewayConfig) -> dict[str, Any]:
        status = "ok"
        reasons: list[str] = []
        if skill.get("loadError"):
            return {"status": "error", "reasons": [str(skill.get("loadError"))]}
        if not skill.get("enabled", True):
            status = "warning"
            reasons.append("skill disabled")

        known_tools = set(self._tools) | set(self._write_handlers)
        allowed_tools = ensure_string_list(skill.get("allowedTools") or skill.get("tools"))
        disallowed_tools = ensure_string_list(skill.get("disallowedTools"))
        unknown_allowed = [item for item in allowed_tools if item and item not in known_tools]
        unknown_disallowed = [item for item in disallowed_tools if item and item not in known_tools]
        if unknown_allowed:
            status = "error"
            reasons.append("unknown allowed tools: " + ", ".join(unknown_allowed[:8]))
        elif unknown_disallowed and status == "ok":
            status = "warning"
            reasons.append("unknown disallowed tools: " + ", ".join(unknown_disallowed[:8]))

        entrypoint = str(skill.get("entrypointTool") or "").strip()
        if entrypoint:
            if entrypoint not in known_tools:
                status = "error"
                reasons.append(f"unknown entrypoint tool: {entrypoint}")
            elif entrypoint in disallowed_tools:
                status = "error"
                reasons.append(f"entrypoint tool is disallowed: {entrypoint}")
            elif allowed_tools and entrypoint not in allowed_tools:
                status = "error"
                reasons.append(f"entrypoint tool is not in allowed tools: {entrypoint}")
            elif not self._skill_dependency_visible(entrypoint, config) and status == "ok":
                status = "warning"
                reasons.append(f"entrypoint tool is unavailable: {entrypoint}")

        missing_env = [name for name in ensure_string_list(skill.get("requiresEnv")) if name and not os.environ.get(name)]
        if missing_env:
            status = "error"
            reasons.append("missing env: " + ", ".join(missing_env[:8]))
        missing_bins = [name for name in ensure_string_list(skill.get("requiresBinaries")) if name and not shutil.which(name)]
        if missing_bins:
            status = "error"
            reasons.append("missing binaries: " + ", ".join(missing_bins[:8]))

        supported_os = [item.lower() for item in ensure_string_list(skill.get("supportedOs")) if item]
        if supported_os and current_os_key() not in supported_os and "any" not in supported_os:
            status = "error"
            reasons.append(f"unsupported os: {current_os_key()}")

        if not skill.get("available", True) and status == "ok":
            status = "warning"
            reasons.append("dependencies unavailable")
        return {"status": status, "reasons": reasons}

    def visible_write_targets(self, config: AgentGatewayConfig | None = None) -> list[dict[str, Any]]:
        config = config or self.ensure_config()
        return [
            {
                "name": handler.name,
                "description": handler.description,
                "riskLevel": handler.risk_level,
                "advanced": handler.advanced,
            }
            for handler in self._write_handlers.values()
            if self._write_handler_visible(handler, config) and handler.name not in WRAPPER_ONLY_WRITE_TARGETS
        ]

    def roslyn_available(self, config: AgentGatewayConfig | None = None) -> bool:
        config = config or self.ensure_config()
        return bool(config.allow_roslyn_advanced)

    def append_audit(self, entry: dict[str, Any]) -> None:
        safe_entry = redact_sensitive({
            "timestamp": utc_now_iso(),
            **entry,
        })
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(safe_entry, ensure_ascii=False, sort_keys=True) + "\n")

    def _read_config_payload(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _serialize_user_constraints(
        self,
        snapshot: UserConstraintsSnapshot,
        include_error: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": snapshot.status,
            "path": str(snapshot.path),
            "enabled": bool(snapshot.content),
            "message": snapshot.message,
            "characterCount": len(snapshot.content),
        }
        if include_error and snapshot.error:
            payload["error"] = snapshot.error
        return payload

    def _inject_user_constraints(
        self,
        params: dict[str, Any],
        tool: AgentTool,
        snapshot: UserConstraintsSnapshot,
    ) -> dict[str, Any]:
        if not snapshot.content:
            return dict(params)
        if tool.category not in {"read/debug", "plan/preview", "supervised-write", "advanced"}:
            return dict(params)
        return self._with_user_constraints(params, snapshot)

    def _inject_user_constraints_for_apply(
        self,
        params: dict[str, Any],
        snapshot: UserConstraintsSnapshot,
    ) -> dict[str, Any]:
        if not snapshot.content:
            return dict(params)
        return self._with_user_constraints(params, snapshot)

    def _with_user_constraints(
        self,
        params: dict[str, Any],
        snapshot: UserConstraintsSnapshot,
    ) -> dict[str, Any]:
        enriched = dict(params)
        enriched["_vrcforge_user_constraints"] = {
            "source": "user_agents_md",
            "path": str(snapshot.path),
            "content": snapshot.content,
        }
        enriched.setdefault("user_constraints", snapshot.content)
        enriched.setdefault("userConstraints", snapshot.content)
        instruction = enriched.get("instruction")
        constraints_block = (
            "\n\nUser constraints from %LOCALAPPDATA%\\VRCForge\\agentic-app\\AGENTS.md:\n"
            f"{snapshot.content}"
        )
        if isinstance(instruction, str) and instruction.strip():
            if snapshot.content not in instruction:
                enriched["instruction"] = instruction.rstrip() + constraints_block
        elif "instruction" in enriched or any(
            key in enriched for key in ("avatar", "avatar_path", "avatarPath", "inventory", "changes", "adjustments")
        ):
            enriched["instruction"] = "Follow the user constraints below." + constraints_block
        return enriched

    @property
    def default_workspace_root(self) -> Path:
        app_dir = os.environ.get("VRCFORGE_APP_DIR", "").strip()
        if app_dir:
            return Path(app_dir).resolve()
        return Path.cwd().resolve()

    def _resolve_workspace_root(self, params: dict[str, Any]) -> Path:
        raw = str(params.get("workspace_root") or params.get("workspaceRoot") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return self.default_workspace_root

    def _resolve_cwd(self, params: dict[str, Any], workspace_root: Path) -> Path:
        raw = str(params.get("cwd") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return workspace_root

    def _shell_classification(
        self,
        command: str,
        cwd: Path,
        workspace_root: Path,
        risk: str,
        reasons: list[str],
    ) -> dict[str, Any]:
        return {
            "ok": risk != "reject",
            "command": command,
            "commandHash": command_hash(command),
            "risk": risk,
            "reasons": reasons,
            "cwd": str(cwd),
            "workspaceRoot": str(workspace_root),
        }

    def _low_risk_reasons(self, command_name: str, args: list[str], workspace_root: Path) -> list[str]:
        read_only = {"get-childitem", "dir", "ls", "get-content", "type", "rg", "findstr"}
        if command_name in read_only:
            if self._args_stay_in_workspace(args, workspace_root):
                return ["Read-only workspace inspection command."]
            return []

        if command_name in {"python", "node", "npm", "uv"} and args in (["--version"], ["-v"]):
            return ["Read-only environment version probe."]

        if command_name == "where" and len(args) == 1 and re.fullmatch(r"[a-zA-Z0-9_.-]+", args[0] or ""):
            return ["Read-only executable lookup."]

        if command_name == "git":
            return self._git_low_risk_reasons(args, workspace_root)

        return []

    def _args_stay_in_workspace(self, args: list[str], workspace_root: Path) -> bool:
        for arg in args:
            if not arg or arg.startswith("-"):
                continue
            cleaned = strip_quotes(arg)
            if cleaned in {".", "*"}:
                continue
            if ".." in re.split(r"[\\/]+", cleaned):
                return False
            if looks_like_absolute_path(cleaned) and not is_path_within(Path(cleaned), workspace_root):
                return False
        return True

    def _git_low_risk_reasons(self, args: list[str], workspace_root: Path) -> list[str]:
        if not args:
            return []
        if "-c" in args or any(arg.startswith("--config") for arg in args):
            return []
        if args[0] == "--no-pager":
            args = args[1:]
        if not args:
            return []

        verb = args[0]
        rest = args[1:]
        if verb == "status" and all(arg in {"--short", "-s", "--porcelain", "--branch", "-b"} for arg in rest):
            return ["Read-only git status command."]
        if verb == "log" and self._git_log_args_are_low_risk(rest):
            return ["Read-only git log command."]
        if verb == "diff" and self._git_diff_args_are_low_risk(rest, workspace_root):
            return ["Read-only git diff command."]
        if verb == "show" and "--stat" in rest and "--ext-diff" not in rest:
            return ["Read-only git show stat command."]
        return []

    def _git_log_args_are_low_risk(self, args: list[str]) -> bool:
        allowed_flags = {"--oneline", "--decorate", "--no-decorate"}
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in allowed_flags:
                index += 1
                continue
            if arg == "-n" and index + 1 < len(args) and args[index + 1].isdigit():
                index += 2
                continue
            if re.fullmatch(r"-\d{1,3}", arg):
                index += 1
                continue
            return False
        return True

    def _git_diff_args_are_low_risk(self, args: list[str], workspace_root: Path) -> bool:
        if "--ext-diff" in args or "--cached" in args:
            return False
        if args == ["--stat"] or not args:
            return True
        if "--" in args:
            path_args = args[args.index("--") + 1 :]
            return self._args_stay_in_workspace(path_args, workspace_root)
        return all(arg in {"--stat", "--name-only", "--name-status"} for arg in args)

    def _create_shell_approval(
        self,
        params: dict[str, Any],
        classification: dict[str, Any],
        agent_name: str,
    ) -> dict[str, Any]:
        session_id = str(params.get("session_id") or params.get("sessionId") or "").strip()
        turn_id = str(params.get("turn_id") or params.get("turnId") or "").strip()
        with self._lock:
            for approval in self._approvals.values():
                if (
                    approval.get("targetTool") == "vrcforge_shell_execute"
                    and approval.get("status") == "pending"
                    and approval.get("sessionId") == session_id
                    and approval.get("turnId") == turn_id
                    and turn_id
                ):
                    return redact_sensitive(dict(approval))

        arguments = {
            "command": classification["command"],
            "command_hash": classification["commandHash"],
            "cwd_hash": stable_hash(classification["cwd"]),
            "workspace_root_hash": stable_hash(classification["workspaceRoot"]),
            "cwd": classification["cwd"],
            "workspace_root": classification["workspaceRoot"],
            "session_id": session_id,
            "turn_id": turn_id,
            "timeout_seconds": int(params.get("timeout_seconds") or 120),
            "timeout_hash": stable_hash(str(int(params.get("timeout_seconds") or 120))),
            "classification_snapshot": classification,
        }
        approval = self._new_approval(
            agent_name=agent_name,
            target_tool="vrcforge_shell_execute",
            arguments=arguments,
            reason=str(params.get("reason") or "High-risk shell command requires approval."),
            preview={
                "command": classification["command"],
                "cwd": classification["cwd"],
                "workspaceRoot": classification["workspaceRoot"],
                "riskReasons": classification["reasons"],
            },
            risk_level="high",
            user_constraints=self.read_user_constraints(),
        )
        with self._lock:
            stored = self._approvals.get(approval["id"])
            if stored is not None:
                stored["sessionId"] = session_id
                stored["turnId"] = turn_id
                stored["commandHash"] = classification["commandHash"]
                stored["cwdHash"] = stable_hash(classification["cwd"])
                stored["workspaceRootHash"] = stable_hash(classification["workspaceRoot"])
        self.append_audit(
            {
                "event": "shell_approval_requested",
                "agent": agent_name,
                "approvalId": approval["id"],
                "classification": classification,
            }
        )
        return approval

    def _run_shell_command(self, command: str, cwd: Path, timeout_seconds: int = 120) -> dict[str, Any]:
        started = time.monotonic()
        started_at = utc_now_iso()
        env = os.environ.copy()
        env["GIT_PAGER"] = "cat"
        env["GIT_EXTERNAL_DIFF"] = ""
        process_args = [
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        process = subprocess.Popen(  # noqa: S603 - shell execution is the supervised capability under test.
            process_args,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=max(1, min(timeout_seconds, 600)))
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_process_tree(process)
            stdout, stderr = process.communicate()

        duration = time.monotonic() - started
        exit_code = process.returncode if process.returncode is not None else -1
        return {
            "ok": exit_code == 0 and not timed_out,
            "command": command,
            "cwd": str(cwd),
            "exitCode": exit_code,
            "timedOut": timed_out,
            "startedAt": started_at,
            "finishedAt": utc_now_iso(),
            "durationSeconds": round(duration, 3),
            "stdout": truncate_text(stdout),
            "stderr": truncate_text(stderr),
            "stdoutTruncated": len(stdout or "") > 12000,
            "stderrTruncated": len(stderr or "") > 12000,
        }

    def _plan_agent_turn(
        self,
        message: str,
        params: dict[str, Any],
        observe: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        local_plan = self._local_plan_agent_turn(message, params, observe)
        # 关键词命中（明确的技能/命令意图）直接走确定性路径：快、稳定、可测试。
        if local_plan.get("shellNeeded") or local_plan.get("skillNeeded"):
            return local_plan
        # 本地规划没认出意图时，尝试 LLM 规划；失败则回退本地结果。
        llm_plan = self._llm_plan_agent_turn(message, observe, history or [])
        if llm_plan is not None:
            return llm_plan
        return local_plan

    def _local_plan_agent_turn(self, message: str, params: dict[str, Any], observe: dict[str, Any]) -> dict[str, Any]:
        command = extract_shell_command_candidate(message, params)
        skill_route = self._match_runtime_skill(message, params) if not command else None
        summary = "Observed runtime state and prepared the next action."
        if command:
            summary = "Prepared a shell step for the requested task."
        elif skill_route:
            summary = f"Prepared {skill_route['tool']} skill call."
        elif "health" in message.lower() or "健康" in message:
            summary = "Observed runtime health. No shell step is required."
        return {
            "summary": summary,
            "reply": "",
            "planner": "deterministic-local",
            "plannerLabel": "",
            "userConstraintsApplied": bool(observe.get("userConstraints", {}).get("enabled")),
            "shellNeeded": bool(command),
            "shellCommand": command,
            "skillNeeded": bool(skill_route),
            "skillTool": skill_route.get("tool") if skill_route else "",
            "skillCategory": skill_route.get("category") if skill_route else "",
            "skillParams": skill_route.get("params") if skill_route else {},
            "skillReason": skill_route.get("reason") if skill_route else "",
            "expectedResult": "Shell output will be returned inline." if command else "Runtime observation is available.",
            "nextStep": "classify_shell" if command else "call_skill" if skill_route else "await_user_instruction",
        }

    def _llm_plan_agent_turn(
        self,
        message: str,
        observe: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        plan_fn = self.llm_plan_fn
        if plan_fn is None:
            return None
        try:
            prompt = self._build_llm_plan_prompt(message, history)
            raw_response = plan_fn(prompt)
            payload = parse_llm_plan_response(raw_response)
        except Exception:  # noqa: BLE001 - LLM 失败时静默回退到本地规划。
            return None
        if not isinstance(payload, dict):
            return None

        action = str(payload.get("action") or "").strip().lower()
        summary = str(payload.get("summary") or "").strip()
        reply = str(payload.get("reply") or "").strip()
        skill_tool = str(payload.get("skill_tool") or payload.get("skillTool") or "").strip()
        skill_params = ensure_dict(payload.get("skill_params") or payload.get("skillParams"))
        shell_command = str(payload.get("shell_command") or payload.get("shellCommand") or "").strip()

        base = {
            "planner": "llm",
            "plannerLabel": self.llm_planner_label,
            "reply": reply,
            "userConstraintsApplied": bool(observe.get("userConstraints", {}).get("enabled")),
            "shellNeeded": False,
            "shellCommand": "",
            "skillNeeded": False,
            "skillTool": "",
            "skillCategory": "",
            "skillParams": {},
            "skillReason": "",
            "expectedResult": "",
        }

        if action == "skill" and skill_tool:
            known_tool = skill_tool in self._tools or self._find_registry_skill(skill_tool) is not None
            if known_tool:
                route = self._runtime_skill_route(skill_tool, skill_params, "llm planner")
                return {
                    **base,
                    "summary": summary or f"调用 {skill_tool} 处理该请求。",
                    "skillNeeded": True,
                    "skillTool": route.get("tool") or skill_tool,
                    "skillCategory": route.get("category") or "",
                    "skillParams": route.get("params") or {},
                    "skillReason": "llm planner",
                    "expectedResult": "Skill output will be returned inline.",
                    "nextStep": "call_skill",
                }
        if action == "shell" and shell_command:
            return {
                **base,
                "summary": summary or "Prepared a shell step for the requested task.",
                "shellNeeded": True,
                "shellCommand": shell_command,
                "expectedResult": "Shell output will be returned inline.",
                "nextStep": "classify_shell",
            }
        reply_text = reply or summary
        if not reply_text:
            return None
        return {
            **base,
            "summary": reply_text,
            "reply": reply_text,
            "expectedResult": "Conversational reply.",
            "nextStep": "done",
        }

    def _build_llm_plan_prompt(self, message: str, history: list[dict[str, Any]]) -> str:
        tool_lines: list[str] = []
        for tool in self._tools.values():
            flags = []
            if tool.write:
                flags.append("write")
            if tool.advanced:
                flags.append("advanced")
            suffix = f"（{','.join(flags)}）" if flags else ""
            tool_lines.append(f"- {tool.name}{suffix}: {summarize_text(tool.description, 120)}")
        history_lines: list[str] = []
        for entry in history[-12:]:
            role = "用户" if str(entry.get("role") or "user").strip().lower() == "user" else "助手"
            text = summarize_text(str(entry.get("text") or ""), 500)
            if text:
                history_lines.append(f"{role}: {text}")
        history_block = "\n".join(history_lines) if history_lines else "（无）"
        return (
            "你是 VRCForge 桌面智能体的规划器，负责把用户的中文/英文请求转换成下一步动作。\n"
            "可选动作：\n"
            '1. 调用工具：{"action": "skill", "skill_tool": "<工具名>", "skill_params": {…}, "summary": "<一句话说明>", "reply": "<对用户说的话>"}\n'
            '2. 执行 PowerShell 命令（系统级问题，如看日志/查文件/git）：{"action": "shell", "shell_command": "<命令>", "summary": "<一句话说明>", "reply": "<对用户说的话>"}\n'
            '3. 直接回答（闲聊、解释、当前信息已足够）：{"action": "reply", "reply": "<中文回答>"}\n'
            "规则：只返回一个 JSON 对象，不要 Markdown 代码块外的文字；工具名必须严格来自下面的列表；"
            "写操作类工具会进入审批流程，可以放心规划；拿不准时选 reply 并说明你需要什么信息。\n"
            "reply 字段是直接展示给用户的对话内容：用第一人称中文，自然地说明你理解了什么、打算怎么做（例如「好的，我去看一下 D 盘根目录有什么」），不要复述 JSON 或工具名。\n\n"
            f"可用工具列表：\n{chr(10).join(tool_lines)}\n\n"
            f"最近对话：\n{history_block}\n\n"
            f"用户最新消息：{message}"
        )

    def _match_runtime_skill(self, message: str, params: dict[str, Any]) -> dict[str, Any] | None:
        explicit_tool = str(
            params.get("skill_tool")
            or params.get("skillTool")
            or params.get("tool_name")
            or params.get("toolName")
            or ""
        ).strip()
        skill_params = ensure_dict(params.get("skill_params") or params.get("skillParams"))
        if explicit_tool:
            return self._runtime_skill_route(explicit_tool, skill_params, "explicit tool request")

        text = message.strip()
        lowered = text.lower()
        direct_invocation = extract_skill_invocation(text)
        if direct_invocation:
            invocation_name, invocation_args = direct_invocation
            invocation_params = {**skill_params, "arguments": invocation_args, "rawArguments": invocation_args}
            return self._runtime_skill_route(invocation_name, invocation_params, "direct skill invocation")

        user_route = self._match_package_skill_route(lowered, text, skill_params)
        if user_route:
            return user_route

        if has_any(lowered, text, ["roslyn"]) and has_any(lowered, text, ["status", "diagnostic", "状态", "诊断", "检查"]):
            return self._runtime_skill_route("vrcforge_roslyn_status", skill_params, "roslyn status")
        if has_any(lowered, text, ["screenshot", "capture", "截图", "拍照", "截屏"]):
            return self._runtime_skill_route("vrcforge_capture_screenshot", skill_params, "screenshot capture")
        if has_any(lowered, text, ["gesture", "play mode", "game view", "捕获状态", "截图状态"]):
            return self._runtime_skill_route("vrcforge_capture_status", skill_params, "capture status")
        if has_any(lowered, text, ["skill", "skills", "能力库"]):
            if has_any(lowered, text, ["check", "validate", "validation", "inspect"]):
                return self._runtime_skill_route("vrcforge_skill_check", skill_params, "skill registry check")
            return self._runtime_skill_route("vrcforge_skill_manifest", skill_params, "skill manifest")
        if has_any(lowered, text, ["tools", "skill", "skills", "工具", "能力", "列表"]) and has_any(
            lowered,
            text,
            ["unity", "mcp", "vrcforge", "工具", "能力"],
        ):
            return self._runtime_skill_route("vrcforge_unity_tools", skill_params, "unity tool list")
        if has_any(lowered, text, ["health", "健康"]):
            return self._runtime_skill_route("vrcforge_health", skill_params, "runtime health")
        if has_any(lowered, text, ["unity", "mcp", "连接", "连上", "实例"]):
            return self._runtime_skill_route("vrcforge_unity_status", skill_params, "unity status")
        if has_any(lowered, text, ["avatar", "avatars", "角色", "模型", "工程刷新", "刷新列表"]):
            return self._runtime_skill_route("vrcforge_list_avatars", skill_params, "avatar list")
        if has_any(lowered, text, ["blendshape", "blend shape", "形态键", "表情键", "脸部", "面部"]):
            if has_any(lowered, text, ["plan", "方案", "调整", "调脸", "优化"]):
                return self._runtime_skill_route("vrcforge_plan_face_tuning", skill_params, "face tuning plan")
            return self._runtime_skill_route("vrcforge_scan_blendshapes", skill_params, "blendshape scan")
        if has_any(lowered, text, ["shader", "material", "materials", "材质", "着色器"]):
            if has_any(lowered, text, ["plan", "方案", "调整", "优化"]):
                return self._runtime_skill_route("vrcforge_plan_shader_tuning", skill_params, "shader tuning plan")
            return self._runtime_skill_route("vrcforge_scan_materials", skill_params, "material scan")
        if has_any(lowered, text, ["logs", "log", "日志"]):
            return self._runtime_skill_route("vrcforge_read_recent_logs", {"limit": 80, **skill_params}, "recent logs")
        if has_any(lowered, text, ["diagnostic", "诊断", "状态"]):
            return self._runtime_skill_route("vrcforge_health", skill_params, "runtime health")
        return None

    def _match_package_skill_route(self, lowered: str, original: str, params: dict[str, Any]) -> dict[str, Any] | None:
        registry = self.build_skill_registry()
        for skill in ensure_list(registry.get("skills")):
            if not isinstance(skill, dict):
                continue
            if not skill.get("enabled", True):
                continue
            if skill.get("disableModelInvocation"):
                continue
            source = str(skill.get("source") or "")
            skill_type = str(skill.get("skillType") or "")
            if source != "user" and skill_type != "group":
                continue
            haystacks = [
                str(skill.get("name") or "").lower(),
                str(skill.get("title") or "").lower(),
            ]
            if source == "user":
                haystacks.extend(
                    [
                        str(skill.get("description") or "").lower(),
                        str(skill.get("whenToUse") or "").lower(),
                    ]
                )
            if any(item and item in lowered for item in haystacks):
                return {
                    "tool": str(skill.get("name")),
                    "category": str(skill.get("category") or "user"),
                    "params": dict(params),
                    "reason": "user skill match",
                }
            title = str(skill.get("title") or "")
            if title and title in original:
                return {
                    "tool": str(skill.get("name")),
                    "category": str(skill.get("category") or "user"),
                    "params": dict(params),
                    "reason": "user skill match",
                }
        return None

    def _runtime_skill_route(self, tool_name: str, params: dict[str, Any], reason: str) -> dict[str, Any]:
        tool = self._tools.get(tool_name)
        if not tool:
            registry_skill = self._find_registry_skill(tool_name)
            return {
                "tool": tool_name,
                "category": str(registry_skill.get("category") or "") if registry_skill else "",
                "params": dict(params),
                "reason": reason,
            }
        return {
            "tool": tool_name,
            "category": tool.category if tool else "",
            "params": dict(params),
            "reason": reason,
        }

    def _find_registry_skill(self, skill_id: str, config: AgentGatewayConfig | None = None) -> dict[str, Any] | None:
        skill_id = normalize_skill_id(skill_id)
        for skill in ensure_list(self.build_skill_registry(config).get("skills")):
            if isinstance(skill, dict) and normalize_skill_id(str(skill.get("name") or "")) == skill_id:
                return skill
        return None

    def _execute_runtime_skill(
        self,
        tool_name: str,
        params: dict[str, Any],
        agent_name: str,
    ) -> dict[str, Any]:
        config = self.ensure_config()
        tool = self._tools.get(tool_name)
        if not tool:
            registry_skill = self._find_registry_skill(tool_name, config)
            if registry_skill:
                return self._execute_skill_package(registry_skill, params, agent_name, config)
            return {
                "ok": False,
                "status": "blocked",
                "tool": tool_name,
                "error": f"Unknown skill: {tool_name}",
            }
        if tool.name in RUNTIME_BLOCKED_SKILLS or tool.write or tool.category not in RUNTIME_DIRECT_SKILL_CATEGORIES:
            return {
                "ok": False,
                "status": "blocked",
                "tool": tool.name,
                "category": tool.category,
                "write": tool.write,
                "advanced": tool.advanced,
                "error": "This skill cannot run directly from the runtime loop.",
            }
        if not self._tool_visible(tool, config):
            return {
                "ok": False,
                "status": "blocked",
                "tool": tool.name,
                "category": tool.category,
                "write": tool.write,
                "advanced": tool.advanced,
                "error": "This skill is unavailable in the current permission mode.",
            }

        user_constraints = self.read_user_constraints()
        tool_params = self._inject_user_constraints(params, tool, user_constraints)
        try:
            result = tool.handler(tool_params)
            payload = {
                "ok": True,
                "status": "executed",
                "tool": tool.name,
                "category": tool.category,
                "write": tool.write,
                "advanced": tool.advanced,
                "summary": tool.description,
                "paramsSummary": summarize_params(params),
                "result": redact_sensitive(result),
            }
            self.append_audit(
                {
                    "event": "runtime_skill_executed",
                    "tool": tool.name,
                    "agent": agent_name,
                    "paramsSummary": summarize_params(params),
                    "status": "ok",
                }
            )
            return payload
        except Exception as exc:  # noqa: BLE001 - runtime must keep the agent loop alive.
            self.append_audit(
                {
                    "event": "runtime_skill_executed",
                    "tool": tool.name,
                    "agent": agent_name,
                    "paramsSummary": summarize_params(params),
                    "status": "error",
                    "error": str(exc),
                }
            )
            return {
                "ok": False,
                "status": "failed",
                "tool": tool.name,
                "category": tool.category,
                "write": tool.write,
                "advanced": tool.advanced,
                "summary": tool.description,
                "paramsSummary": summarize_params(params),
                "error": str(exc),
            }

    def _execute_skill_package(
        self,
        skill: dict[str, Any],
        params: dict[str, Any],
        agent_name: str,
        config: AgentGatewayConfig,
    ) -> dict[str, Any]:
        validation = ensure_dict(skill.get("validation")) or self._validate_skill(skill, config)
        status = "loaded" if skill.get("enabled", True) and validation.get("status") != "error" else "blocked"
        result = redact_sensitive(build_runtime_skill_payload(skill, params))
        payload = {
            "ok": status == "loaded",
            "status": status,
            "tool": str(skill.get("name") or ""),
            "category": str(skill.get("category") or ""),
            "write": bool(skill.get("write")),
            "advanced": bool(skill.get("advanced")),
            "summary": str(skill.get("description") or skill.get("title") or ""),
            "paramsSummary": summarize_params(params),
            "result": result,
        }
        if status != "loaded":
            payload["error"] = "; ".join(ensure_string_list(validation.get("reasons"))) or "Skill is unavailable."
            self.append_audit(
                {
                    "event": "runtime_skill_package_loaded",
                    "skill": skill.get("name"),
                    "agent": agent_name,
                    "status": payload["status"],
                    "error": payload.get("error"),
                }
            )
            return payload

        entrypoint = str(skill.get("entrypointTool") or "").strip()
        if entrypoint:
            entrypoint_result = self._execute_skill_entrypoint(skill, entrypoint, params, agent_name, config)
            payload["entrypointTool"] = entrypoint
            payload["entrypoint"] = entrypoint_result
            if entrypoint_result.get("status") == "executed":
                payload["status"] = "executed"
                payload["ok"] = True
            elif entrypoint_result.get("status") in {"blocked", "failed"}:
                payload["status"] = entrypoint_result.get("status")
                payload["ok"] = False
                payload["error"] = entrypoint_result.get("error")

        self.append_audit(
            {
                "event": "runtime_skill_package_loaded",
                "skill": skill.get("name"),
                "agent": agent_name,
                "status": payload["status"],
                "entrypointTool": entrypoint,
            }
        )
        return payload

    def _execute_skill_entrypoint(
        self,
        skill: dict[str, Any],
        entrypoint: str,
        params: dict[str, Any],
        agent_name: str,
        config: AgentGatewayConfig,
    ) -> dict[str, Any]:
        allowed_tools = ensure_string_list(skill.get("allowedTools") or skill.get("tools"))
        disallowed_tools = ensure_string_list(skill.get("disallowedTools"))
        if entrypoint in disallowed_tools:
            return {"ok": False, "status": "blocked", "tool": entrypoint, "error": "Entrypoint tool is disallowed."}
        if allowed_tools and entrypoint not in allowed_tools:
            return {"ok": False, "status": "blocked", "tool": entrypoint, "error": "Entrypoint tool is not allowed."}
        tool = self._tools.get(entrypoint)
        if not tool:
            return {"ok": False, "status": "blocked", "tool": entrypoint, "error": "Entrypoint requires approval or is not callable directly."}
        if tool.name in RUNTIME_BLOCKED_SKILLS or tool.write or tool.category not in RUNTIME_DIRECT_SKILL_CATEGORIES:
            return {"ok": False, "status": "blocked", "tool": entrypoint, "error": "Entrypoint cannot run directly from the runtime loop."}
        if not self._tool_visible(tool, config):
            return {"ok": False, "status": "blocked", "tool": entrypoint, "error": "Entrypoint is unavailable in the current permission mode."}
        tool_params = {
            key: value
            for key, value in params.items()
            if key not in {"arguments", "rawArguments", "skillArguments"}
        }
        user_constraints = self.read_user_constraints()
        tool_params = self._inject_user_constraints(tool_params, tool, user_constraints)
        try:
            result = tool.handler(tool_params)
            self.append_audit(
                {
                    "event": "runtime_skill_entrypoint_executed",
                    "skill": skill.get("name"),
                    "tool": entrypoint,
                    "agent": agent_name,
                    "status": "ok",
                }
            )
            return {
                "ok": True,
                "status": "executed",
                "tool": entrypoint,
                "category": tool.category,
                "result": redact_sensitive(result),
            }
        except Exception as exc:  # noqa: BLE001 - keep the agent loop alive.
            return {"ok": False, "status": "failed", "tool": entrypoint, "category": tool.category, "error": str(exc)}

    def _extract_token(self, headers: dict[str, str], query_params: dict[str, str]) -> str:
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return str(query_params.get("token") or "")

    def _serialize_tool(self, tool: AgentTool, config: AgentGatewayConfig) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "category": tool.category,
            "write": tool.write,
            "advanced": tool.advanced,
            "available": self._tool_visible(tool, config),
        }

    def _serialize_tool_registry_entry(self, tool: AgentTool, config: AgentGatewayConfig) -> dict[str, Any]:
        available = self._tool_visible(tool, config)
        risk = self._registry_risk_for_tool(tool)
        requires_approval = tool.write or risk in {"write_request", "advanced_write"}
        return {
            "id": self._registry_tool_id(tool.name),
            "name": tool.name,
            "title": tool.name.replace("vrcforge_", "").replace("_", " ").title(),
            "description": tool.description,
            "category": self._registry_category(tool.category, tool.name),
            "risk": risk,
            "requiresApproval": requires_approval,
            "requiresCheckpoint": requires_approval and tool.name not in {"vrcforge_execute_shell", "vrcforge_execute_approved_shell"},
            "availableInDesktop": available,
            "availableInMcp": available,
            "availableInCli": available,
            "inputsSchema": self._registry_object_schema(),
            "outputsSchema": self._registry_object_schema(),
            "fallbacks": self._registry_fallbacks_for_tool(tool),
            "source": "gateway-tool",
            "advanced": bool(tool.advanced),
            "directTool": True,
        }

    def _serialize_write_registry_entry(self, handler: AgentWriteHandler, config: AgentGatewayConfig) -> dict[str, Any]:
        visible = self._write_handler_visible(handler, config)
        available = visible and bool(config.allow_write_requests)
        return {
            "id": self._registry_tool_id(handler.name),
            "name": handler.name,
            "title": handler.name.replace("vrcforge_", "").replace("_", " ").title(),
            "description": handler.description,
            "category": self._registry_category("supervised-write", handler.name),
            "risk": "advanced_write" if handler.advanced else "write_request",
            "requiresApproval": True,
            "requiresCheckpoint": True,
            "availableInDesktop": visible,
            "availableInMcp": available,
            "availableInCli": visible,
            "inputsSchema": self._registry_object_schema(),
            "outputsSchema": self._registry_object_schema(),
            "fallbacks": ["vrcforge_request_apply"],
            "source": "write-target",
            "advanced": bool(handler.advanced),
            "directTool": False,
        }

    def _registry_tool_id(self, name: str) -> str:
        if name.startswith("vrcforge_"):
            name = name[len("vrcforge_") :]
        return "vrcforge." + name.replace("_", ".")

    def _registry_risk_for_tool(self, tool: AgentTool) -> str:
        if tool.advanced:
            return "advanced_write"
        if tool.write:
            return "write_request"
        if tool.category == "plan/preview":
            return "plan"
        return "read_only"

    def _registry_category(self, category: str, name: str) -> str:
        text = f"{category} {name}".lower()
        if "optimization" in text or "optimizer" in text or "vram" in text:
            return "optimization"
        if "health" in text or "status" in text:
            return "health"
        if "project" in text or "package_manager" in text:
            return "project"
        if "unity" in text or "compile" in text or "roslyn" in text:
            return "unity"
        if "avatar" in text or "blendshape" in text or "face" in text:
            return "avatar"
        if "material" in text or "shader" in text:
            return "material"
        if "outfit" in text or "booth" in text or "unitypackage" in text:
            return "outfit"
        if "wardrobe" in text:
            return "wardrobe"
        if "modular_avatar" in text or " ma" in text:
            return "ma"
        if "vrcfury" in text:
            return "vrcfury"
        if "skill" in text:
            return "skill"
        if "checkpoint" in text or "backup" in text or "restore" in text:
            return "checkpoint"
        if "validation" in text:
            return "validation"
        if "agent" in text or "connector" in text:
            return "agent"
        if category == "plan/preview":
            return "plan"
        if category == "supervised-write":
            return "write"
        return "tool"

    def _registry_object_schema(self) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": True}

    def _registry_fallbacks_for_tool(self, tool: AgentTool) -> list[str]:
        if tool.write:
            return ["vrcforge_request_apply"]
        if tool.category == "plan/preview":
            return ["manual-review"]
        return []

    def _tool_visible(self, tool: AgentTool, config: AgentGatewayConfig) -> bool:
        if tool.name in EXTERNAL_AGENT_INTERNAL_TOOLS:
            return False
        if tool.advanced and not self.roslyn_available(config):
            return False
        if tool.write and not config.allow_write_requests:
            return False
        return True

    def _write_handler_visible(self, handler: AgentWriteHandler, config: AgentGatewayConfig) -> bool:
        if handler.advanced and not self.roslyn_available(config):
            return False
        return True

    def _new_approval(
        self,
        agent_name: str,
        target_tool: str,
        arguments: dict[str, Any],
        reason: str,
        preview: Any,
        risk_level: str,
        user_constraints: UserConstraintsSnapshot | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        config = self.ensure_config()
        approval = {
            "id": f"appr_{now.strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(4)}",
            "createdAt": now.isoformat(),
            "expiresAt": (now + timedelta(seconds=config.approval_timeout_seconds)).isoformat(),
            "agentName": agent_name,
            "targetTool": target_tool,
            "reason": reason,
            "riskLevel": risk_level,
            "status": "pending",
            "arguments": arguments,
            "paramsSummary": summarize_params(arguments),
            "preview": preview if preview is not None else summarize_params(arguments),
        }
        if user_constraints and user_constraints.content:
            approval["userConstraintsApplied"] = True
            approval["userConstraintsPath"] = str(user_constraints.path)
        with self._lock:
            self._approvals[approval["id"]] = approval
            self.append_audit({"event": "approval_requested", "approval": approval})
        return redact_sensitive(dict(approval))

    def _set_approval_status(self, approval_id: str, status: str) -> dict[str, Any]:
        with self._lock:
            approval = self._approvals.get(approval_id)
            if not approval:
                approval = self._load_approval_from_audit(approval_id)
            if not approval:
                raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)
            approval = self._refresh_approval_expiry(approval)
            if approval.get("status") not in {"pending", "approved"} and status == "approved":
                return {"ok": False, "approval": approval, "message": f"Approval is {approval.get('status')}."}
            if approval.get("status") == "expired":
                return {"ok": False, "approval": approval, "message": "Approval has expired."}
            approval["status"] = status
            approval[f"{status}At"] = utc_now_iso()
            self._approvals[approval_id] = approval
            self.append_audit({"event": f"approval_{status}", "approval": approval})
            return {"ok": True, "approval": redact_sensitive(dict(approval))}

    def _refresh_approval_expiry(self, approval: dict[str, Any]) -> dict[str, Any]:
        if approval.get("status") != "pending":
            return approval
        expires_at = parse_iso_datetime(str(approval.get("expiresAt") or ""))
        if expires_at and expires_at < datetime.now(timezone.utc):
            approval["status"] = "expired"
            self._approvals[str(approval.get("id"))] = approval
        return approval

    def _load_approval_from_audit(self, approval_id: str) -> dict[str, Any] | None:
        for entry in reversed(self.recent_audit_logs(limit=500)):
            approval = entry.get("approval")
            if isinstance(approval, dict) and approval.get("id") == approval_id:
                return approval
        return None


def create_agent_mcp_app(gateway: AgentGateway):
    mcp = FastMCP(
        "VRCForge Agent Gateway",
        instructions=(
            "Use VRCForge tools for supervised VRChat avatar debugging. "
            "Read, plan, and preview tools run directly. Writes require an approval request."
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "testserver"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        ),
    )

    def register(name: str):
        async def tool(params: dict[str, Any] | None = None, agent_name: str = "mcp-agent") -> dict[str, Any]:
            return gateway.call_tool(name, params or {}, agent_name=agent_name)

        mcp.tool(name=name)(tool)

    for tool_name in [
        "vrcforge_agent_observe",
        "vrcforge_agent_message",
        "vrcforge_classify_shell",
        "vrcforge_execute_shell",
        "vrcforge_skill_manifest",
        "vrcforge_skill_check",
        "vrcforge_tool_registry",
        "vrcforge_external_agent_connectors",
        "vrcforge_list_skill_packages",
        "vrcforge_preflight_skill_package",
        "vrcforge_scan_project_index",
        "vrcforge_inspect_outfit_package",
        "vrcforge_plan_outfit_import",
        "vrcforge_health",
        "vrcforge_unity_status",
        "vrcforge_unity_tools",
        "vrcforge_list_avatars",
        "vrcforge_scan_blendshapes",
        "vrcforge_scan_materials",
        "vrcforge_scan_modular_avatar",
        "vrcforge_scan_vrcfury",
        "vrcforge_scan_avatar_items",
        "vrcforge_scan_fx_animator",
        "vrcforge_scan_animation_bindings",
        "vrcforge_scan_avatar_controls",
        "vrcforge_scan_wardrobe",
        "vrcforge_scan_parameters",
        "vrcforge_run_validation_report",
        "vrcforge_build_test_readiness",
        "vrcforge_optimization_plan",
        "vrcforge_optimization_validation_delta",
        *OPTIMIZATION_GATEWAY_TOOL_NAMES,
        *STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES,
        "vrcforge_create_safe_backup",
        "vrcforge_preview_restore_backup",
        "vrcforge_list_checkpoints",
        "vrcforge_preview_restore_checkpoint",
        "vrcforge_scan_avatar_performance",
        "vrcforge_scan_thry_avatar_performance",
        "vrcforge_package_manager_status",
        "vrcforge_package_install_plan",
        "vrcforge_package_install_request",
        "vrcforge_diagnose_package_install_errors",
        "vrcforge_preview_setup_outfit",
        "vrcforge_preview_add_wardrobe_outfit",
        "vrcforge_preview_add_outfit_part",
        "vrcforge_preview_add_modular_avatar_component",
        "vrcforge_preview_manage_wardrobe",
        "vrcforge_preview_ensure_expression_parameter",
        "vrcforge_preview_ensure_expression_menu_control",
        "vrcforge_preview_ensure_animator_state",
        "vrcforge_preview_create_wardrobe",
        "vrcforge_preview_add_outfit",
        "vrcforge_capture_status",
        "vrcforge_capture_screenshot",
        "vrcforge_vision_audit",
        "vrcforge_read_recent_logs",
        "vrcforge_roslyn_status",
        "vrcforge_get_compile_errors",
        "vrcforge_get_property",
        "vrcforge_get_gameobject",
        "vrcforge_find_assets",
        "vrcforge_get_asset_info",
        "vrcforge_plan_face_tuning",
        "vrcforge_plan_shader_tuning",
        "vrcforge_preview_blendshape_apply",
        "vrcforge_preview_shader_apply",
        "vrcforge_request_apply",
        "vrcforge_restore_last_backup",
        "vrcforge_request_roslyn_advanced",
    ]:
        register(tool_name)

    app = mcp.streamable_http_app()
    app.state.fastmcp_server = mcp
    return app


def tokenize_command(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return []


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def looks_like_absolute_path(value: str) -> bool:
    return bool(re.match(r"^(?:[a-zA-Z]:[\\/]|\\\\)", value))


def normalize_filesystem_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    try:
        return Path(text).resolve().as_posix().lower()
    except (OSError, RuntimeError):
        return text.rstrip("/").lower()


def is_path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def parse_llm_plan_response(raw_response: str) -> dict[str, Any] | None:
    """Extract the first JSON object from an LLM response (tolerates Markdown fences)."""
    stripped = str(raw_response or "").strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped).strip()
    start = stripped.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    for index in range(start, len(stripped)):
        if stripped[index] != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(stripped[index:])
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def summarize_text(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def truncate_text(text: str, limit: int = 12000) -> str:
    if len(text or "") <= limit:
        return text or ""
    return (text or "")[:limit] + "\n[truncated]"


def summarize_shell_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": result.get("ok"),
        "exitCode": result.get("exitCode"),
        "timedOut": result.get("timedOut"),
        "durationSeconds": result.get("durationSeconds"),
        "stdoutSummary": summarize_text(str(result.get("stdout") or "")),
        "stderrSummary": summarize_text(str(result.get("stderr") or "")),
    }


def summarize_skill_registry(registry: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": registry.get("schema"),
        "count": registry.get("count"),
        "availableCount": registry.get("availableCount"),
        "builtinCount": registry.get("builtinCount"),
        "userCount": registry.get("userCount"),
        "skills": [
            {
                "name": skill.get("name"),
                "title": skill.get("title"),
                "source": skill.get("source"),
                "skillType": skill.get("skillType"),
                "category": skill.get("category"),
                "permissionMode": skill.get("permissionMode"),
                "available": skill.get("available"),
                "allowedTools": skill.get("allowedTools"),
                "entrypointTool": skill.get("entrypointTool"),
            }
            for skill in ensure_list(registry.get("skills"))[:80]
            if isinstance(skill, dict)
        ],
    }


def extract_skill_invocation(message: str) -> tuple[str, str] | None:
    match = SKILL_INVOCATION_RE.match(str(message or ""))
    if not match:
        return None
    skill_name = normalize_skill_id(match.group(1) or "")
    if not skill_name:
        return None
    return skill_name, (match.group(2) or "").strip()


def build_runtime_skill_payload(skill: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    arguments = str(params.get("arguments") or params.get("rawArguments") or params.get("skillArguments") or "").strip()
    resolved_instructions = resolve_skill_arguments(str(skill.get("instructions") or ""), arguments)
    return {
        "name": skill.get("name"),
        "title": skill.get("title"),
        "source": skill.get("source"),
        "skillType": skill.get("skillType"),
        "category": skill.get("category"),
        "permissionMode": skill.get("permissionMode"),
        "riskLevel": skill.get("riskLevel"),
        "whenToUse": skill.get("whenToUse"),
        "inputs": skill.get("inputs"),
        "outputs": skill.get("outputs"),
        "sideEffects": skill.get("sideEffects"),
        "backupRestore": skill.get("backupRestore"),
        "allowedTools": skill.get("allowedTools"),
        "disallowedTools": skill.get("disallowedTools"),
        "entrypointTool": skill.get("entrypointTool"),
        "argumentHint": skill.get("argumentHint"),
        "arguments": arguments,
        "instructions": resolved_instructions,
        "validation": skill.get("validation"),
        "availabilityReasons": skill.get("availabilityReasons"),
        "tags": skill.get("tags"),
    }


def resolve_skill_arguments(instructions: str, arguments: str) -> str:
    text = str(instructions or "")
    if not arguments:
        return text
    text = text.replace("$ARGUMENTS", arguments).replace("{arguments}", arguments)
    parts = tokenize_command(arguments)
    for index, value in enumerate(parts, start=1):
        text = text.replace(f"${index}", strip_quotes(value))
    return text


def kill_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            check=False,
        )
        return
    process.kill()


def extract_shell_command_candidate(message: str, params: dict[str, Any]) -> str:
    explicit = str(params.get("shell_command") or params.get("shellCommand") or "").strip()
    if explicit:
        return explicit
    stripped = message.strip()
    lowered = stripped.lower()
    if lowered.startswith("/shell "):
        return stripped[7:].strip()
    if lowered.startswith("shell:"):
        return stripped[6:].strip()
    fenced = re.search(r"```(?:powershell|pwsh|shell|bash|cmd)?\s*([\s\S]+?)```", stripped, re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    inline = re.search(r"`([^`\n]+)`", stripped)
    if inline:
        return inline.group(1).strip()
    if "git status" in lowered or "工作树" in stripped or "仓库状态" in stripped:
        return "git --no-pager status --short"
    if "git log" in lowered or "最近提交" in stripped:
        return "git --no-pager log --oneline -n 10"
    if "列目录" in stripped or "文件列表" in stripped or lowered in {"ls", "dir"}:
        return "Get-ChildItem"
    return ""


def has_any(lowered_text: str, original_text: str, needles: list[str]) -> bool:
    return any((needle.lower() in lowered_text) if needle.isascii() else (needle in original_text) for needle in needles)


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def ensure_string_list(value: Any) -> list[str]:
    items = ensure_list(value)
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def normalize_skill_id(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-._")
    return text


def normalize_skill_permission(value: Any) -> str:
    text = str(value or "instruction_only").strip().lower().replace("-", "_")
    aliases = {
        "read": "read_only",
        "readonly": "read_only",
        "plan": "preview",
        "approve": "approval_required",
        "approval": "approval_required",
        "write": "approval_required",
        "advanced": "advanced_power_mode",
        "roslyn": "advanced_power_mode",
    }
    text = aliases.get(text, text)
    return text if text in SKILL_PERMISSION_MODES else "instruction_only"


def normalize_risk_level(value: Any) -> str:
    text = str(value or "low").strip().lower()
    return text if text in {"low", "medium", "high", "critical"} else "low"


def normalize_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def first_payload_value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


def current_os_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform.lower()


def title_from_name(name: str) -> str:
    text = re.sub(r"^vrcforge_", "", name or "")
    return " ".join(part.capitalize() for part in re.split(r"[_\-.]+", text) if part) or name


def parse_skill_markdown(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    metadata: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            metadata = parse_frontmatter_block(parts[1])
            body = parts[2].lstrip("\r\n")
    metadata["body"] = body.strip()
    metadata.setdefault("name", path.parent.name)
    return metadata


def parse_frontmatter_block(block: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    current_key = ""
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_key:
            payload.setdefault(current_key, [])
            if isinstance(payload[current_key], list):
                payload[current_key].append(strip_simple_yaml_scalar(stripped[2:].strip()))
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        current_key = camelize_key(key.strip())
        value = raw_value.strip()
        if not value:
            payload[current_key] = []
        elif value.startswith("[") and value.endswith("]"):
            payload[current_key] = [
                strip_simple_yaml_scalar(item.strip())
                for item in value[1:-1].split(",")
                if item.strip()
            ]
        else:
            payload[current_key] = strip_simple_yaml_scalar(value)
    return payload


def strip_simple_yaml_scalar(value: str) -> Any:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return text


def camelize_key(key: str) -> str:
    normalized = key.strip().replace("-", "_")
    aliases = {
        "permission_mode": "permissionMode",
        "risk_level": "riskLevel",
        "when_to_use": "whenToUse",
        "side_effects": "sideEffects",
        "backup_restore": "backupRestore",
        "allowed_tools": "allowedTools",
        "disallowed_tools": "disallowedTools",
        "entrypoint_tool": "entrypointTool",
        "user_invocable": "userInvocable",
        "disable_model_invocation": "disableModelInvocation",
        "argument_hint": "argumentHint",
        "requires_env": "requiresEnv",
        "requires_binaries": "requiresBinaries",
        "supported_os": "supportedOs",
        "support_files": "supportFiles",
        "test_command": "testCommand",
    }
    if normalized in aliases:
        return aliases[normalized]
    if "_" not in normalized:
        return key
    head, *tail = normalized.split("_")
    return head + "".join(part.capitalize() for part in tail)


def render_skill_markdown(skill: dict[str, Any]) -> str:
    metadata_keys = [
        ("name", "name"),
        ("title", "title"),
        ("description", "description"),
        ("category", "category"),
        ("permission-mode", "permissionMode"),
        ("risk-level", "riskLevel"),
        ("when-to-use", "whenToUse"),
        ("inputs", "inputs"),
        ("outputs", "outputs"),
        ("side-effects", "sideEffects"),
        ("backup-restore", "backupRestore"),
        ("tools", "tools"),
        ("allowed-tools", "allowedTools"),
        ("disallowed-tools", "disallowedTools"),
        ("entrypoint-tool", "entrypointTool"),
        ("user-invocable", "userInvocable"),
        ("disable-model-invocation", "disableModelInvocation"),
        ("argument-hint", "argumentHint"),
        ("requires-env", "requiresEnv"),
        ("requires-binaries", "requiresBinaries"),
        ("supported-os", "supportedOs"),
        ("support-files", "supportFiles"),
        ("test-command", "testCommand"),
        ("enabled", "enabled"),
        ("tags", "tags"),
    ]
    lines = ["---"]
    for yaml_key, key in metadata_keys:
        value = skill.get(key)
        if isinstance(value, list):
            lines.append(f"{yaml_key}:")
            for item in value:
                lines.append(f"  - {yaml_scalar(str(item))}")
        elif isinstance(value, bool):
            lines.append(f"{yaml_key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{yaml_key}: {yaml_scalar(str(value or ''))}")
    lines.append("---")
    lines.append("")
    lines.append(str(skill.get("instructions") or "").strip())
    lines.append("")
    return "\n".join(lines)


def yaml_scalar(value: str) -> str:
    text = value.replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return '""'
    if re.search(r"[:#\[\],]|^\s|\s$", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def normalize_execution_mode(value: Any) -> str:
    """Three permission tiers:

    - "approval"          受限模式（沙箱）：高风险 shell 与写操作逐项审批。
    - "auto"              自动审批：审批仍然生成并留痕，但立即自动批准执行；Roslyn 高级工具保持关闭。
    - "roslyn_full_auto"  完全权限：自动审批 + Roslyn 高级工具，首次开启需要一次性风险确认。
    """
    mode = str(value or "approval").strip().lower().replace("-", "_")
    if mode in {"roslyn_full_auto", "full_auto", "roslyn_auto", "advanced", "full", "full_permission"}:
        return "roslyn_full_auto"
    if mode in {"auto", "auto_approve", "auto_approval", "autoapprove"}:
        return "auto"
    return "approval"


def summarize_params(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            str(key): summarize_value(key, item)
            for key, item in value.items()
            if str(key).lower()
            not in {
                "token",
                "authorization",
                "api_key",
                "apikey",
                "secret",
                "user_constraints",
                "userconstraints",
                "_vrcforge_user_constraints",
            }
        }
    return {"value": summarize_value("value", value)}


def summarize_value(key: Any, value: Any) -> Any:
    key_text = str(key).lower()
    if key_text in {"token", "authorization", "api_key", "apikey", "secret"}:
        return "<redacted>"
    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(str(item) for item in value.keys())[:20], "keyCount": len(value)}
    if isinstance(value, list):
        return {"type": "list", "count": len(value)}
    if isinstance(value, str):
        if len(value) > 140:
            return value[:137] + "..."
        if "\\" in value or "/" in value:
            return Path(value).name or "<path>"
        return value
    return value


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {
                "token",
                "authorization",
                "api_key",
                "apikey",
                "secret",
                "user_constraints",
                "userconstraints",
                "_vrcforge_user_constraints",
            }:
                result[str(key)] = "<redacted>"
            elif lowered in {"arguments"} and isinstance(item, dict):
                result[str(key)] = summarize_params(item)
            else:
                result[str(key)] = redact_sensitive(item)
        return result
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

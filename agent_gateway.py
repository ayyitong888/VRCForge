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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


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
    "vrcforge_install_vpm_package": {
        "title": "VPM Package Install",
        "inputs": ["VPM package id and Unity project path."],
        "outputs": ["CLI command result and post-install package state."],
        "sideEffects": "modifies project VPM manifest and Packages after approval",
        "tags": ["package", "vpm", "write"],
    },
    "vrcforge_preview_restore_backup": {
        "title": "Backup Restore Preview",
        "inputs": ["Backup path or backup id, optional asset subset."],
        "outputs": ["Planned overwrites, changed files, and mismatch warnings."],
        "sideEffects": "none",
        "tags": ["backup", "restore", "preview"],
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
            "vrcforge_scan_parameters",
            "vrcforge_scan_avatar_performance",
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
            "vrcforge_create_safe_backup",
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_toggle_scene_object",
        ],
        "entrypointTool": "vrcforge_scan_avatar_items",
        "tags": ["builtin", "group", "wardrobe", "write"],
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
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_setup_outfit",
            "vrcforge_restore_safe_backup",
        ],
        "entrypointTool": "vrcforge_preview_setup_outfit",
        "tags": ["builtin", "group", "modular-avatar", "wardrobe", "write"],
    },
    {
        "name": "package-maintenance",
        "title": "Package Maintenance",
        "description": "Detect VPM CLIs and install addon packages such as Modular Avatar or VRCFury through approval.",
        "category": "package",
        "permissionMode": "approval_required",
        "riskLevel": "medium",
        "whenToUse": "install package, vpm, vrc-get, alcom, add modular avatar, add vrcfury",
        "inputs": ["Unity project path and VPM package id."],
        "outputs": ["CLI detection state and package install result."],
        "sideEffects": "can modify the project VPM manifest and Packages after approval",
        "backupRestore": "vpm manifest changes are revertible via the package manager CLI",
        "allowedTools": [
            "vrcforge_package_manager_status",
            "vrcforge_scan_modular_avatar",
            "vrcforge_scan_vrcfury",
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_install_vpm_package",
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
        self._lock = threading.RLock()

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
        self.config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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
        observe = self.runtime_observe(session_id=session_id)
        plan = self._plan_agent_turn(message, params, observe)

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
        result = self._run_shell_command(command, cwd, timeout_seconds=int(params.get("timeout_seconds") or 120))
        self.append_audit(
            {
                "event": "shell_approved_executed",
                "sessionId": params.get("session_id") or params.get("sessionId") or "",
                "turnId": params.get("turn_id") or params.get("turnId") or "",
                "commandHash": command_hash(command),
                "cwd": str(cwd),
                "workspaceRoot": str(workspace_root),
                "result": summarize_shell_result(result),
            }
        )
        return result

    def create_apply_request(self, params: dict[str, Any]) -> dict[str, Any]:
        config = self.ensure_config()
        if not config.allow_write_requests:
            raise AgentGatewayError("Agent Gateway write requests are disabled.", status_code=403)

        target_tool = str(params.get("target_tool") or params.get("targetTool") or "").strip()
        if not target_tool:
            raise AgentGatewayError("target_tool is required.")

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

        try:
            user_constraints = self.read_user_constraints()
            arguments = self._inject_user_constraints_for_apply(
                ensure_dict(approval.get("arguments") or {}),
                user_constraints,
            )
            result = write_handler.handler(arguments)
            with self._lock:
                approval["status"] = "applied"
                approval["appliedAt"] = utc_now_iso()
                approval["resultSummary"] = summarize_params(result if isinstance(result, dict) else {"result": result})
                self._approvals[approval_id] = approval
                self.append_audit({"event": "approval_applied", "approval": approval})
            return {"ok": True, "status": "applied", "approval": approval, "result": result}
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                approval["status"] = "failed"
                approval["failedAt"] = utc_now_iso()
                approval["error"] = str(exc)
                self._approvals[approval_id] = approval
                self.append_audit({"event": "approval_failed", "approval": approval})
            return {"ok": False, "status": "failed", "approval": approval, "error": str(exc)}

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
            "allowedTools": ["vrcforge_request_apply", "vrcforge_apply_approved"],
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
            if self._write_handler_visible(handler, config)
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
            "cwd": classification["cwd"],
            "workspace_root": classification["workspaceRoot"],
            "session_id": session_id,
            "turn_id": turn_id,
            "timeout_seconds": int(params.get("timeout_seconds") or 120),
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

    def _plan_agent_turn(self, message: str, params: dict[str, Any], observe: dict[str, Any]) -> dict[str, Any]:
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
            "planner": "deterministic-local",
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

    def _tool_visible(self, tool: AgentTool, config: AgentGatewayConfig) -> bool:
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
        "vrcforge_execute_approved_shell",
        "vrcforge_skill_manifest",
        "vrcforge_skill_check",
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
        "vrcforge_scan_parameters",
        "vrcforge_create_safe_backup",
        "vrcforge_preview_restore_backup",
        "vrcforge_scan_avatar_performance",
        "vrcforge_package_manager_status",
        "vrcforge_preview_setup_outfit",
        "vrcforge_capture_status",
        "vrcforge_capture_screenshot",
        "vrcforge_vision_audit",
        "vrcforge_read_recent_logs",
        "vrcforge_roslyn_status",
        "vrcforge_plan_face_tuning",
        "vrcforge_plan_shader_tuning",
        "vrcforge_preview_blendshape_apply",
        "vrcforge_preview_shader_apply",
        "vrcforge_request_apply",
        "vrcforge_apply_approved",
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


def is_path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()


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

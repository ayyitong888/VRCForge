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
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from optimization_service import (
    OPTIMIZATION_GATEWAY_TOOL_NAMES,
    OPTIMIZATION_TOOL_DEFINITIONS,
    STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES,
)


ToolHandler = Callable[[dict[str, Any]], Any]

ROLLBACK_POLICY_SCHEMA = "vrcforge.write_rollback_policy.v1"
ROLLBACK_COVERAGE_AUDIT_SCHEMA = "vrcforge.rollback_coverage_audit.v1"
APPLY_RECOVERY_SCHEMA = "vrcforge.interrupted_apply_recovery.v1"
UNITY_PROJECT_CHECKPOINT_SCOPE = ("Assets", "Packages", "ProjectSettings")
LOCAL_STATE_CHECKPOINT_SCOPE = ("skill-packages", "skills")
LOCAL_STATE_CHECKPOINT_TARGETS = {
    "vrcforge_import_skill_package",
    "vrcforge_export_skill_package",
    "vrcforge_set_skill_package_enabled",
    "vrcforge_uninstall_skill_package",
}
APPLY_RECOVERY_ACTIVE_STATUSES = {"applying", "needs_recovery", "restore_failed"}
APPLY_RECOVERY_EXEMPT_WRITE_TARGETS = {
    "vrcforge_restore_checkpoint",
    "vrcforge_resolve_interrupted_apply_recovery",
}
CHECKPOINT_ARCHIVE_BYTES_PER_MB = 1024 * 1024
CHECKPOINT_ARCHIVE_MAX_SIZE_MB_LIMIT = 1024 * 1024
CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB = 10 * 1024
CHECKPOINT_ARCHIVE_PROTECTED_RECENT_COUNT = 2
AUTO_APPROVAL_MANUAL_SHELL_COMMANDS = {
    "del",
    "erase",
    "rd",
    "ri",
    "rm",
    "rmdir",
    "remove-item",
}
AUTO_APPROVAL_MANUAL_WRITE_TOKENS = (
    "delete",
    "remove",
    "restore",
    "reset",
    "clear",
    "prune",
    "uninstall",
)
WRITE_PATH_KEY_MARKERS = ("path", "root", "file", "dir", "directory", "folder")
ROLLBACK_FRAMEWORK_PACKAGES = {
    "modular_avatar": {
        "label": "Modular Avatar",
        "packageIds": ["nadena.dev.modular-avatar"],
    },
    "vrcfury": {
        "label": "VRCFury",
        "packageIds": ["com.vrcfury.vrcfury"],
    },
    "ndmf": {
        "label": "NDMF",
        "packageIds": ["nadena.dev.ndmf"],
    },
}
UNITY_RESTORE_PACKAGE_CACHE_DIRS = ("Bee", "ScriptAssemblies", "PackageCache")


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
    checkpoint_archive_max_size_mb: int = CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB
    checkpoint_archive_dir: str = ""


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
# 有界 agentic 循环每轮的最大步数——这是「安全兜底」而非主要终止条件。
# 真正的终止靠模型/规划自决：拿到终止答复、发起写入审批、命中重复动作即停。
# 这个上限只在模型抽风、停不下来时兜底。对标主流 agent CLI 的做法：
#   - Codex CLI：每轮工具调用默认不设上限，靠模型给出最终消息自然结束（单轮可达
#     数十次工具调用）；只有可选的 --max-turns 限制对话轮数。
#   - OpenCode(sst)：`steps` 字段不配则不限，一直迭代到模型自己停或用户打断。
#   - OpenClaw：可配的 max-iterations 兜底，社区推荐 15-20（多数任务 <10 步即完成），
#     复杂研究类 25-30，并配合「到顶就向用户求助」而不是静默中止。
# 取 25：落在 OpenClaw 复杂任务区间，远高于常见任务步数，又不至于无界烧 token/刷审批。
# 命中上限时不静默收尾，而是诚实告知「到步数上限、先汇报、可继续」（见循环 else 分支）。
RUNTIME_AGENT_MAX_STEPS = 25
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
USER_CONSTRAINTS_INLINE_CHARACTER_LIMIT = 4000
USER_CONSTRAINTS_PREVIEW_CHARACTER_LIMIT = 240
WRAPPER_ONLY_WRITE_TARGETS = {
    "vrcforge_avatar_encryption_addon_apply",
    "vrcforge_avatar_encryption_addon_remove",
    "vrcforge_configure_optimizer_component",
    "vrcforge_install_vpm_package",
}
AVATAR_ENCRYPTION_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "vrcforge_avatar_encryption_research_report",
        "title": "Avatar Encryption Research Report",
        "permissionMode": "read_only",
        "risk": "read_only",
    },
    {
        "name": "vrcforge_avatar_encryption_scan",
        "title": "Avatar Encryption Compatibility Scan",
        "permissionMode": "read_only",
        "risk": "read_only",
    },
    {
        "name": "vrcforge_avatar_encryption_plan",
        "title": "Avatar Encryption Plan",
        "permissionMode": "preview",
        "risk": "plan",
    },
    {
        "name": "vrcforge_avatar_encryption_preview",
        "title": "Avatar Encryption Write Preview",
        "permissionMode": "preview",
        "risk": "plan",
    },
    {
        "name": "vrcforge_avatar_encryption_addon_status",
        "title": "Avatar Encryption Addon Status",
        "permissionMode": "read_only",
        "risk": "read_only",
    },
    {
        "name": "vrcforge_avatar_encryption_liltoon_apply_request",
        "title": "Avatar Encryption lilToon Apply Request",
        "permissionMode": "approval",
        "risk": "high",
    },
    {
        "name": "vrcforge_avatar_encryption_poiyomi_apply_request",
        "title": "Avatar Encryption Poiyomi Apply Request",
        "permissionMode": "approval",
        "risk": "high",
    },
    {
        "name": "vrcforge_avatar_encryption_remove_request",
        "title": "Avatar Encryption Remove Request",
        "permissionMode": "approval",
        "risk": "high",
    },
)
AVATAR_ENCRYPTION_TOOL_NAMES = tuple(str(item["name"]) for item in AVATAR_ENCRYPTION_TOOL_SPECS)
AVATAR_ENCRYPTION_READ_TOOL_NAMES = (
    "vrcforge_avatar_encryption_research_report",
    "vrcforge_avatar_encryption_scan",
)
AVATAR_ENCRYPTION_PLAN_TOOL_NAMES = (
    "vrcforge_avatar_encryption_plan",
    "vrcforge_avatar_encryption_preview",
)
AVATAR_ENCRYPTION_STATUS_TOOL_NAMES = (
    "vrcforge_avatar_encryption_addon_status",
)
AVATAR_ENCRYPTION_APPLY_REQUEST_TOOL_NAMES = (
    "vrcforge_avatar_encryption_liltoon_apply_request",
    "vrcforge_avatar_encryption_poiyomi_apply_request",
)
AVATAR_ENCRYPTION_REMOVE_REQUEST_TOOL_NAMES = (
    "vrcforge_avatar_encryption_remove_request",
)
AVATAR_ENCRYPTION_DISALLOWED_WRITE_TOOLS = (
    "vrcforge_avatar_encryption_addon_apply",
    "vrcforge_avatar_encryption_addon_remove",
)
ADJUSTMENT_CHECKPOINT_KINDS = {"face", "shader"}
ADJUSTMENT_CHECKPOINT_TARGETS = {
    "vrcforge_apply_blendshapes": "face",
    "vrcforge_run_face_tuning": "face",
    "vrcforge_undo_blendshapes": "face",
    "vrcforge_apply_shader_tuning": "shader",
    "vrcforge_restore_shader_tuning": "shader",
}

SKILL_PERMISSION_MODES = {"read_only", "preview", "approval_required", "advanced_power_mode", "instruction_only"}
SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,80}$")
SKILL_INVOCATION_RE = re.compile(r"^\s*[/$]([a-zA-Z][a-zA-Z0-9_.-]{1,80})(?:\s+(.*))?\s*$")
RUNTIME_ATTACHMENT_MAX_ITEMS = 8
RUNTIME_ATTACHMENT_DATA_URL_MAX_CHARS = 5_600_000
RUNTIME_ATTACHMENT_TEXT_MAX_CHARS = 524_288
AGENT_MEMORY_MAX_ITEMS = 120
AGENT_GOAL_MAX_ITEMS = 60
AGENT_DESKTOP_ACTION_MAX_ITEMS = 120

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
    "vrcforge_avatar_encryption_research_report": {
        "title": "Avatar Encryption Research Report",
        "inputs": ["Optional includeExternalReferences flag."],
        "outputs": ["Read-only Avatar Encryption / Anti-Rip addon boundary and connector status packet."],
        "sideEffects": "none",
        "backupRestore": "not required; research report never writes Unity assets",
        "tags": ["avatar-encryption", "anti-rip", "shader", "research", "liltoon", "poiyomi"],
    },
    "vrcforge_avatar_encryption_scan": {
        "title": "Avatar Encryption Compatibility Scan",
        "inputs": ["Optional avatar path and shader material inventory."],
        "outputs": ["Read-only lilToon/Poiyomi candidate list plus compatibility-only blocked shader families."],
        "sideEffects": "none",
        "backupRestore": "not required; scan never writes Unity assets",
        "tags": ["avatar-encryption", "anti-rip", "shader", "scan", "liltoon", "poiyomi"],
    },
    "vrcforge_avatar_encryption_plan": {
        "title": "Avatar Encryption Plan",
        "permissionMode": "preview",
        "inputs": ["Avatar path or inventory, target shader families, profile, and platform."],
        "outputs": ["Plan with lilToon/Poiyomi priorities, connector status, proof requirements, and private-addon request tools."],
        "sideEffects": "none",
        "backupRestore": "not required for planning; apply/remove request tools require approval, checkpoint, validation, and rollback proof",
        "tags": ["avatar-encryption", "anti-rip", "shader", "plan", "liltoon", "poiyomi"],
    },
    "vrcforge_avatar_encryption_preview": {
        "title": "Avatar Encryption Write Preview",
        "permissionMode": "preview",
        "inputs": ["Avatar encryption plan or the same arguments accepted by avatar-encryption.plan."],
        "outputs": ["No-write preview of private-addon request targets, request readiness, and rollback policy."],
        "sideEffects": "none",
        "backupRestore": "not required for preview; apply/remove request tools require approval, checkpoint, validation, and rollback proof",
        "tags": ["avatar-encryption", "anti-rip", "shader", "preview", "no-direct-apply"],
    },
    "vrcforge_avatar_encryption_liltoon_apply_request": {
        "title": "Avatar Encryption lilToon Apply Request",
        "permissionMode": "approval_required",
        "inputs": ["Avatar path or inventory, lilToon material targets, PC platform, profile, and creator-owned confirmation."],
        "outputs": ["Approval request for a configured private lilToon addon connector."],
        "sideEffects": "creates an approval request only; approved execution is handed to the configured private addon",
        "backupRestore": "requires explicit approval, pre-write checkpoint, private addon remove request, and checkpoint rollback",
        "tags": ["avatar-encryption", "anti-rip", "shader", "write-request", "liltoon", "rollback"],
    },
    "vrcforge_avatar_encryption_poiyomi_apply_request": {
        "title": "Avatar Encryption Poiyomi Apply Request",
        "permissionMode": "approval_required",
        "inputs": ["Avatar path or inventory, Poiyomi material targets, PC platform, profile, and creator-owned confirmation."],
        "outputs": ["Approval request for a configured private Poiyomi addon connector."],
        "sideEffects": "creates an approval request only; approved execution is handed to the configured private addon",
        "backupRestore": "requires explicit approval, pre-write checkpoint, private addon remove request, and checkpoint rollback",
        "tags": ["avatar-encryption", "anti-rip", "shader", "write-request", "poiyomi", "rollback"],
    },
    "vrcforge_avatar_encryption_remove_request": {
        "title": "Avatar Encryption Remove Request",
        "permissionMode": "approval_required",
        "inputs": ["Avatar path plus manifest path or generated output folder."],
        "outputs": ["Approval request for configured private addon removal."],
        "sideEffects": "creates an approval request only; approved execution is handed to the configured private addon",
        "backupRestore": "requires explicit approval and pre-write checkpoint; checkpoint rollback remains available if remove cannot resolve an original asset",
        "tags": ["avatar-encryption", "anti-rip", "shader", "remove-request", "rollback"],
    },
    "vrcforge_avatar_encryption_addon_status": {
        "title": "Avatar Encryption Addon Status",
        "permissionMode": "read_only",
        "inputs": ["none"],
        "outputs": ["Private addon connector configuration status."],
        "sideEffects": "none",
        "backupRestore": "not required; status check never writes Unity assets",
        "tags": ["avatar-encryption", "anti-rip", "connector", "status"],
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
    "vrcforge_read_avatar_descriptor": {
        "title": "Read Avatar Descriptor",
        "inputs": ["Avatar path."],
        "outputs": ["VRCAvatarDescriptor viewpoint, lip sync, visemes, expression assets, playable layers, and eye-look summary."],
        "sideEffects": "none",
        "tags": ["avatar-descriptor", "avatar-authoring", "read"],
    },
    "vrcforge_preview_write_avatar_descriptor": {
        "title": "Write Avatar Descriptor Preview",
        "inputs": ["Avatar path and descriptor fields to change."],
        "outputs": ["Planned descriptor field changes; no writes."],
        "sideEffects": "none",
        "tags": ["avatar-descriptor", "avatar-authoring", "preview"],
    },
    "vrcforge_write_avatar_descriptor": {
        "title": "Write Avatar Descriptor",
        "inputs": ["Avatar path and descriptor fields to change."],
        "outputs": ["Updated VRCAvatarDescriptor fields."],
        "sideEffects": "updates avatar descriptor viewpoint, lip sync, visemes, expression assets, eye look flag, or playable layer controllers after approval",
        "tags": ["avatar-descriptor", "avatar-authoring", "write"],
    },
    "vrcforge_preview_write_animation_curve": {
        "title": "Write Animation Curve Preview",
        "inputs": ["AnimationClip path, binding path, component type, property name, and curve keys or constant value."],
        "outputs": ["Planned AnimationClip binding change; no writes."],
        "sideEffects": "none",
        "tags": ["animation", "curve", "preview"],
    },
    "vrcforge_write_animation_curve": {
        "title": "Write Animation Curve",
        "inputs": ["AnimationClip path, binding path, component type, property name, and curve keys or constant value."],
        "outputs": ["Created, replaced, or deleted one AnimationClip curve binding."],
        "sideEffects": "creates or edits AnimationClip assets after approval",
        "tags": ["animation", "curve", "write"],
    },
    "vrcforge_preview_manage_expression_parameters": {
        "title": "Manage Expression Parameters Preview",
        "inputs": ["Avatar path, action, parameter name, and action-specific fields."],
        "outputs": ["Planned expression-parameter delete/rename/reorder/update; no writes."],
        "sideEffects": "none",
        "tags": ["parameter", "avatar-authoring", "preview"],
    },
    "vrcforge_manage_expression_parameters": {
        "title": "Manage Expression Parameters",
        "inputs": ["Avatar path, action, parameter name, and action-specific fields."],
        "outputs": ["Deleted, renamed, reordered, or updated existing expression parameters."],
        "sideEffects": "edits VRCExpressionParameters after approval",
        "tags": ["parameter", "avatar-authoring", "write"],
    },
    "vrcforge_preview_manage_expression_menu": {
        "title": "Manage Expression Menu Preview",
        "inputs": ["Avatar path, action, menu path, control selector, and control fields."],
        "outputs": ["Planned expression-menu control create/update/delete/reorder; no writes."],
        "sideEffects": "none",
        "tags": ["menu", "avatar-authoring", "preview"],
    },
    "vrcforge_manage_expression_menu": {
        "title": "Manage Expression Menu",
        "inputs": ["Avatar path, action, menu path, control selector, and control fields."],
        "outputs": ["Created, updated, deleted, or reordered expression menu controls."],
        "sideEffects": "edits VRCExpressionsMenu assets after approval",
        "tags": ["menu", "avatar-authoring", "write"],
    },
    "vrcforge_preview_manage_fx_animator": {
        "title": "Manage FX Animator Preview",
        "inputs": ["Avatar path or controller path, action, layer/state/transition fields."],
        "outputs": ["Planned FX layer/state/transition changes; no writes."],
        "sideEffects": "none",
        "tags": ["animator", "avatar-authoring", "preview"],
    },
    "vrcforge_manage_fx_animator": {
        "title": "Manage FX Animator",
        "inputs": ["Avatar path or controller path, action, layer/state/transition fields."],
        "outputs": ["Created, updated, or deleted FX layers, states, Any-State transitions, motions, and conditions."],
        "sideEffects": "edits AnimatorController assets after approval",
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
    "vrcforge_list_interrupted_apply_recoveries": {
        "title": "Interrupted Apply Recovery",
        "inputs": ["Optional project root, includeResolved, and limit."],
        "outputs": ["Pending crash/hang recovery records, last checkpoint, and write-blocking status."],
        "sideEffects": "none",
        "tags": ["checkpoint", "restore", "crash-recovery"],
    },
    "vrcforge_preview_interrupted_apply_recovery": {
        "title": "Interrupted Apply Recovery Preview",
        "inputs": ["Recovery id or checkpoint id."],
        "outputs": ["Recovery record plus checkpoint restore preview."],
        "sideEffects": "none",
        "tags": ["checkpoint", "restore", "crash-recovery", "preview"],
    },
    "vrcforge_export_interrupted_apply_incident_bundle": {
        "title": "Interrupted Apply Incident Bundle",
        "inputs": ["Recovery id or checkpoint id."],
        "outputs": ["Local incident bundle path with recovery, checkpoint preview, and recent audit logs."],
        "sideEffects": "writes a local support bundle under the VRCForge audit directory",
        "tags": ["checkpoint", "restore", "crash-recovery", "support"],
    },
    "vrcforge_restore_checkpoint": {
        "title": "Checkpoint Restore",
        "inputs": ["Checkpoint id and confirmRestore=true."],
        "outputs": ["Restore result, cleaned files, and checkpoint metadata."],
        "sideEffects": "restores Assets/Packages/ProjectSettings from a pre-write git checkpoint after approval",
        "tags": ["checkpoint", "restore", "write"],
    },
    "vrcforge_resolve_interrupted_apply_recovery": {
        "title": "Resolve Interrupted Apply Recovery",
        "permissionMode": "approval_required",
        "inputs": ["Recovery id and confirmResolved=true."],
        "outputs": ["Resolved recovery record."],
        "sideEffects": "marks a persisted interrupted-write recovery as manually resolved after approval",
        "tags": ["checkpoint", "restore", "crash-recovery", "write"],
    },
    "vrcforge_unity_mcp_write": {
        "title": "Supervised Unity MCP Write",
        "inputs": ["Unity MCP tool name and argument object."],
        "outputs": ["Unity MCP execution result plus the automatic pre-write checkpoint."],
        "sideEffects": "runs a VRCForge-owned static Unity MCP write only after approval and rollback checkpoint creation",
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
        "backupRestore": "not required; this read-only/plan-only tool never writes project assets",
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
        "name": "avatar-encryption-research-scan",
        "title": "Avatar Encryption Research & Scan",
        "description": "Read the optional anti-rip shader encryption research packet and scan lilToon/Poiyomi compatibility candidates.",
        "category": "avatar-encryption",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": "avatar encryption research, anti-rip boundaries, shader-family candidate scan, lilToon/Poiyomi compatibility",
        "inputs": ["Optional avatar path or material inventory."],
        "outputs": ["Research packet, lilToon/Poiyomi candidate scan, compatibility-only blocked shader families, and security boundaries."],
        "sideEffects": "none",
        "backupRestore": "not required; this skill never writes Unity assets",
        "allowedTools": [*AVATAR_ENCRYPTION_READ_TOOL_NAMES, *AVATAR_ENCRYPTION_STATUS_TOOL_NAMES, "vrcforge_scan_materials"],
        "disallowedTools": [],
        "entrypointTool": "vrcforge_avatar_encryption_scan",
        "tags": ["builtin", "group", "avatar-encryption", "anti-rip", "shader", "read-only", "liltoon", "poiyomi"],
    },
    {
        "name": "avatar-encryption-plan-preview",
        "title": "Avatar Encryption Plan & Preview",
        "description": "Build a no-write Avatar Encryption plan and generated-copy preview before any apply request is created.",
        "category": "avatar-encryption",
        "permissionMode": "preview",
        "riskLevel": "medium",
        "whenToUse": "avatar encryption plan, mesh obfuscation preview, rollback proof planning, generated asset preview",
        "inputs": ["Avatar path or material inventory, target shader families, key channel, platform, and obfuscation layers."],
        "outputs": ["No-write plan, request readiness, generated mesh/material copy preview, hard-gate blockers, and rollback requirements."],
        "sideEffects": "none",
        "backupRestore": "not required for preview; apply/remove skills require approval, checkpoint, generated manifest, validation, and rollback",
        "allowedTools": [*AVATAR_ENCRYPTION_READ_TOOL_NAMES, *AVATAR_ENCRYPTION_PLAN_TOOL_NAMES, *AVATAR_ENCRYPTION_STATUS_TOOL_NAMES, "vrcforge_scan_materials"],
        "disallowedTools": [],
        "entrypointTool": "vrcforge_avatar_encryption_plan",
        "tags": ["builtin", "group", "avatar-encryption", "anti-rip", "shader", "preview", "no-direct-apply"],
    },
    {
        "name": "avatar-encryption-liltoon-apply-request",
        "title": "Avatar Encryption lilToon Apply Request",
        "description": "Request supervised lilToon Avatar Encryption apply through the dedicated approval/checkpoint path.",
        "category": "avatar-encryption",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "lilToon avatar encryption apply request, creator-owned clothes/accessory mesh obfuscation",
        "inputs": ["Creator-owned confirmation, avatar path or inventory, lilToon targets, PC platform, and safe layers."],
        "outputs": ["Approval request for a configured private addon connector; direct connector execution stays hidden."],
        "sideEffects": "creates an approval request only; approved execution is handed to the configured private addon after checkpoint",
        "backupRestore": "requires explicit approval, pre-write checkpoint, generated manifest, remove request, validation/visual proof, and rollback",
        "allowedTools": [
            *AVATAR_ENCRYPTION_READ_TOOL_NAMES,
            *AVATAR_ENCRYPTION_PLAN_TOOL_NAMES,
            *AVATAR_ENCRYPTION_STATUS_TOOL_NAMES,
            "vrcforge_avatar_encryption_liltoon_apply_request",
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_list_checkpoints",
            "vrcforge_preview_restore_checkpoint",
            "vrcforge_restore_checkpoint",
        ],
        "disallowedTools": list(AVATAR_ENCRYPTION_DISALLOWED_WRITE_TOOLS),
        "entrypointTool": "vrcforge_avatar_encryption_liltoon_apply_request",
        "tags": ["builtin", "group", "avatar-encryption", "anti-rip", "shader", "write-request", "liltoon", "rollback"],
    },
    {
        "name": "avatar-encryption-poiyomi-apply-request",
        "title": "Avatar Encryption Poiyomi Apply Request",
        "description": "Request supervised Poiyomi Avatar Encryption apply through the dedicated approval/checkpoint path.",
        "category": "avatar-encryption",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "Poiyomi avatar encryption apply request, creator-owned clothes/accessory mesh obfuscation",
        "inputs": ["Creator-owned confirmation, avatar path or inventory, Poiyomi targets, PC platform, and safe layers."],
        "outputs": ["Approval request for a configured private addon connector; direct connector execution stays hidden."],
        "sideEffects": "creates an approval request only; approved execution is handed to the configured private addon after checkpoint",
        "backupRestore": "requires explicit approval, pre-write checkpoint, generated manifest, remove request, validation/visual proof, and rollback",
        "allowedTools": [
            *AVATAR_ENCRYPTION_READ_TOOL_NAMES,
            *AVATAR_ENCRYPTION_PLAN_TOOL_NAMES,
            *AVATAR_ENCRYPTION_STATUS_TOOL_NAMES,
            "vrcforge_avatar_encryption_poiyomi_apply_request",
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_list_checkpoints",
            "vrcforge_preview_restore_checkpoint",
            "vrcforge_restore_checkpoint",
        ],
        "disallowedTools": list(AVATAR_ENCRYPTION_DISALLOWED_WRITE_TOOLS),
        "entrypointTool": "vrcforge_avatar_encryption_poiyomi_apply_request",
        "tags": ["builtin", "group", "avatar-encryption", "anti-rip", "shader", "write-request", "poiyomi", "rollback"],
    },
    {
        "name": "avatar-encryption-remove-rollback",
        "title": "Avatar Encryption Remove & Rollback",
        "description": "Request supervised Avatar Encryption removal, generated asset cleanup, and checkpoint rollback verification.",
        "category": "avatar-encryption",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "remove avatar encryption, restore original meshes/materials, rollback encrypted avatar changes",
        "inputs": ["Manifest path or output folder, avatar path, delete-generated-assets flag, and remove confirmation."],
        "outputs": ["Approval request for configured private addon removal plus checkpoint rollback tools for hard recovery."],
        "sideEffects": "creates an approval request only; approved execution is handed to the configured private addon after checkpoint",
        "backupRestore": "normal cleanup uses the manifest remove request; hard recovery uses vrcforge_restore_checkpoint",
        "allowedTools": [
            "vrcforge_avatar_encryption_remove_request",
            *AVATAR_ENCRYPTION_STATUS_TOOL_NAMES,
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_list_checkpoints",
            "vrcforge_preview_restore_checkpoint",
            "vrcforge_restore_checkpoint",
        ],
        "disallowedTools": list(AVATAR_ENCRYPTION_DISALLOWED_WRITE_TOOLS),
        "entrypointTool": "vrcforge_avatar_encryption_remove_request",
        "tags": ["builtin", "group", "avatar-encryption", "anti-rip", "shader", "remove-request", "rollback"],
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
            "vrcforge_list_interrupted_apply_recoveries",
            "vrcforge_preview_interrupted_apply_recovery",
            "vrcforge_export_interrupted_apply_incident_bundle",
            "vrcforge_resolve_interrupted_apply_recovery",
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
        "whenToUse": "avatar descriptor, animation curve, create/delete/rename parameter, menu control CRUD, FX layer state transition CRUD",
        "inputs": ["Avatar path plus descriptor, parameter, menu, animation-curve, or animator authoring arguments."],
        "outputs": ["Read/preview or approved writes for reusable avatar authoring assets."],
        "sideEffects": "can update avatar descriptor, create/edit animation clips, and manage expression parameters, menus, FX controllers, generated clips, and animator transitions after approval",
        "backupRestore": "uses gateway checkpoint before approved writes",
        "allowedTools": [
            "vrcforge_scan_avatar_controls",
            "vrcforge_scan_fx_animator",
            "vrcforge_scan_parameters",
            "vrcforge_read_avatar_descriptor",
            "vrcforge_preview_write_avatar_descriptor",
            "vrcforge_preview_write_animation_curve",
            "vrcforge_preview_manage_expression_parameters",
            "vrcforge_preview_manage_expression_menu",
            "vrcforge_preview_manage_fx_animator",
            "vrcforge_preview_ensure_expression_parameter",
            "vrcforge_preview_ensure_expression_menu_control",
            "vrcforge_preview_ensure_animator_state",
            "vrcforge_request_apply",
            "vrcforge_apply_approved",
            "vrcforge_write_avatar_descriptor",
            "vrcforge_write_animation_curve",
            "vrcforge_manage_expression_parameters",
            "vrcforge_manage_expression_menu",
            "vrcforge_manage_fx_animator",
            "vrcforge_ensure_expression_parameter",
            "vrcforge_ensure_expression_menu_control",
            "vrcforge_ensure_animator_state",
        ],
        "entrypointTool": "vrcforge_read_avatar_descriptor",
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
        self._cancelled_runtime_turns: set[str] = set()
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
        # 当用户把检查点存档目录迁出 C 盘后，这里缓存覆盖后的绝对路径，
        # 让 checkpoint_store_dir 走新位置；为空时回落到 audit_dir 下默认目录。
        self._checkpoint_store_override: Path | None = None

    def configure_paths(self, config_path: Path, audit_dir: Path) -> None:
        with self._lock:
            self.config_path = config_path
            self.audit_dir = audit_dir
            self._approvals.clear()
            self._runtime_sessions.clear()
            self._cancelled_runtime_turns.clear()

    @property
    def agent_memory_log_path(self) -> Path:
        return self.audit_dir / "agent-memory.jsonl"

    @property
    def agent_goal_log_path(self) -> Path:
        return self.audit_dir / "agent-goals.jsonl"

    @property
    def desktop_action_log_path(self) -> Path:
        return self.audit_dir / "desktop-actions.jsonl"

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
                "checkpoint_archive_max_size_mb": CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB,
                "checkpoint_archive_dir": "",
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
                checkpoint_archive_max_size_mb=normalize_checkpoint_archive_max_size_mb(
                    raw.get("checkpoint_archive_max_size_mb")
                ),
                checkpoint_archive_dir=normalize_checkpoint_archive_dir(
                    raw.get("checkpoint_archive_dir")
                ),
            )
            self._sync_checkpoint_store_override(config)
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
            "checkpoint_archive_max_size_mb": normalize_checkpoint_archive_max_size_mb(
                config.checkpoint_archive_max_size_mb
            ),
            "checkpoint_archive_dir": normalize_checkpoint_archive_dir(
                config.checkpoint_archive_dir
            ),
        }
        atomic_write_json(self.config_path, payload)
        self._sync_checkpoint_store_override(config)

    def _sync_checkpoint_store_override(self, config: AgentGatewayConfig) -> None:
        """根据配置里的迁移目录刷新内存覆盖路径，供 checkpoint_store_dir 读取。"""
        raw = normalize_checkpoint_archive_dir(config.checkpoint_archive_dir)
        if raw:
            try:
                self._checkpoint_store_override = Path(raw)
            except (TypeError, ValueError):
                self._checkpoint_store_override = None
        else:
            self._checkpoint_store_override = None

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
        permission_context = self.permission_audit_context(config)
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
            "fullPermission": permission_context["fullPermission"],
            "permissionLabel": permission_context["permissionLabel"],
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

    def permission_audit_context(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self.ensure_config()
        mode = normalize_execution_mode(config.execution_mode)
        full_permission = mode == "roslyn_full_auto"
        return {
            "permissionMode": mode,
            "fullPermission": full_permission,
            "permissionLabel": "full permission" if full_permission else ("auto approval" if mode == "auto" else "step approval"),
            "perActionApproval": mode == "approval",
            "autoApprove": mode in {"auto", "roslyn_full_auto"},
            "autoApproveDangerousRequiresApproval": mode == "auto",
        }

    def _auto_approval_block_reason(self, approval: dict[str, Any], config: AgentGatewayConfig | None = None) -> str:
        config = config or self.ensure_config()
        mode = normalize_execution_mode(config.execution_mode)
        if mode == "roslyn_full_auto":
            return ""
        if mode != "auto":
            return "Current permission mode does not auto-approve."
        explicit_reason = str(approval.get("explicitApprovalReason") or "").strip()
        if approval.get("requiresExplicitApproval"):
            return explicit_reason or "This approval requires manual confirmation in Auto Approve mode."
        if str(approval.get("targetTool") or "") == "vrcforge_shell_execute":
            arguments = ensure_dict(approval.get("arguments"))
            classification = ensure_dict(arguments.get("classification_snapshot") or approval.get("preview"))
            return self._shell_auto_manual_approval_reason(classification)
        return ""

    def permission_state(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self.ensure_config()
        mode = normalize_execution_mode(config.execution_mode)
        permission_context = self.permission_audit_context(config)
        return {
            "executionMode": mode,
            "perActionApproval": permission_context["perActionApproval"],
            "autoApprove": permission_context["autoApprove"],
            "autoApproveDangerousRequiresApproval": permission_context["autoApproveDangerousRequiresApproval"],
            "roslynFullAuto": mode == "roslyn_full_auto",
            "fullPermission": permission_context["fullPermission"],
            "permissionLabel": permission_context["permissionLabel"],
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
                    **self.permission_audit_context(config),
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
        client_turn_id = str(params.get("client_turn_id") or params.get("clientTurnId") or "").strip()
        history = [entry for entry in ensure_list(params.get("history")) if isinstance(entry, dict)]
        attachments = normalize_runtime_attachments(params.get("attachments"))
        params["_runtimeAttachments"] = attachments
        if history:
            self._restore_runtime_session(session_id, history, now)
        observe = self.runtime_observe(session_id=session_id)
        if attachments:
            observe["turn"] = {"attachments": attachments}
        self.llm_reasoning_trace = {}
        self._append_runtime_run(
            {
                "event": "runtime_turn_started",
                "status": "running",
                "agent": agent_name,
                "sessionId": session_id,
                "turnId": turn_id,
                "clientTurnId": client_turn_id,
                "messageSummary": summarize_text(message),
                "attachmentCount": len(attachments),
                "provider": params.get("provider") or "",
                "providerLabel": params.get("providerLabel") or params.get("provider_label") or "",
                "model": params.get("model") or "",
                "projectRoot": params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "",
            }
        )

        # --- Bounded agentic loop ------------------------------------------------
        # 真正的多步循环：每步规划一个动作 → 执行 → 把结果回灌 loop_state → 再规划，
        # 直到拿到终止答复 / 发起写入审批 / 命中步数上限。读类技能直接执行；写类意图
        # 路由到 call_tool，由既有审批/检查点/回滚模型负责安全——循环只负责「提议」，
        # 不绕过审批直接落地（遵守 AGENTS 非协商项）。
        param_command = str(params.get("shell_command") or params.get("shellCommand") or "").strip()
        loop_state: list[dict[str, Any]] = []
        steps: list[dict[str, Any]] = []
        seen_actions: set[tuple[str, str]] = set()
        shell_payload: dict[str, Any] | None = None
        skill_payload: dict[str, Any] | None = None
        write_payload: dict[str, Any] | None = None
        approval_id = ""
        first_plan: dict[str, Any] | None = None
        last_plan: dict[str, Any] = {}
        iterations = 0
        cap_reached = False

        for step_index in range(RUNTIME_AGENT_MAX_STEPS):
            if self._runtime_cancel_requested(session_id=session_id, turn_id=turn_id, client_turn_id=client_turn_id):
                last_plan = {
                    "summary": "Runtime turn was cancelled by the user.",
                    "reply": "Request cancelled.",
                    "planner": "runtime",
                    "nextStep": "cancelled",
                }
                break
            plan = self._plan_agent_turn(message, params, observe, history, loop_state=loop_state)
            iterations += 1
            last_plan = plan
            if first_plan is None:
                first_plan = plan

            # 仅首步采用调用方直接给的 shell 命令，避免后续步骤反复重放同一条命令。
            command = param_command if step_index == 0 else ""
            if not command:
                command = str(plan.get("shellCommand") or "").strip()

            if command:
                action_kind = "shell"
                action_key = ("shell", command)
            elif plan.get("writeNeeded") and plan.get("writeTool"):
                action_kind = "write"
                action_key = (
                    "write",
                    json.dumps(plan.get("writeParams"), ensure_ascii=False, sort_keys=True, default=str),
                )
            elif plan.get("skillNeeded") and plan.get("skillTool"):
                action_kind = "skill"
                action_key = (
                    "skill",
                    f"{plan.get('skillTool')}::"
                    + json.dumps(plan.get("skillParams"), ensure_ascii=False, sort_keys=True, default=str),
                )
            else:
                # 没有工具动作（终止答复 / 未连接 / 让用户选模型）→ 结束本轮。
                break

            # 防重复：同一动作本轮已经跑过 → 停，避免死循环。
            if action_key in seen_actions:
                break
            seen_actions.add(action_key)

            step_tool = ""
            if action_kind == "shell":
                step_tool = "shell"
                step_payload = self.execute_shell(
                    {
                        "command": command,
                        "cwd": params.get("cwd"),
                        "workspace_root": params.get("workspace_root") or params.get("workspaceRoot"),
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "client_turn_id": client_turn_id,
                        "reason": plan.get("summary") or "Agent shell step",
                    },
                    agent_name=agent_name,
                )
                shell_payload = step_payload
                loop_state.append(
                    {
                        "tool": "shell",
                        "kind": "shell",
                        "status": step_payload.get("status"),
                        "result": summarize_shell_result(step_payload.get("result"))
                        if step_payload.get("result")
                        else None,
                    }
                )
            elif action_kind == "write":
                step_tool = str(plan.get("writeTool") or "")
                step_payload = self._execute_write_request(
                    step_tool, ensure_dict(plan.get("writeParams")), agent_name
                )
                write_payload = step_payload
                loop_state.append(
                    {
                        "tool": step_tool,
                        "kind": "write",
                        "status": step_payload.get("status"),
                        "result": step_payload.get("result"),
                    }
                )
            else:  # skill
                step_tool = str(plan.get("skillTool") or "")
                step_payload = self._execute_runtime_skill(
                    step_tool, ensure_dict(plan.get("skillParams")), agent_name
                )
                skill_payload = step_payload
                loop_state.append(
                    {
                        "tool": step_tool,
                        "kind": "skill",
                        "status": step_payload.get("status"),
                        "result": step_payload.get("result"),
                    }
                )

            steps.append(
                {
                    "index": step_index,
                    "kind": action_kind,
                    "tool": step_tool,
                    "summary": plan.get("summary") or "",
                    "status": step_payload.get("status") or "",
                }
            )

            step_approval = str(
                step_payload.get("approval_id") or step_payload.get("approvalId") or ""
            ).strip()
            if step_approval:
                approval_id = approval_id or step_approval
                break  # 进入审批等待 → 本轮收尾。
            if action_kind == "write":
                break  # 写入提议是本轮的终点（等审批/检查点/回滚）。
            if not plan.get("continueLoop"):
                break
            if str(plan.get("nextStep") or "") == "done":
                break
        else:
            # 跑满 RUNTIME_AGENT_MAX_STEPS 都没自然终止 → 命中安全兜底上限。
            # 对标 OpenCode 的「到顶强制总结」/ OpenClaw 的「到顶向用户求助」：
            # 不静默收尾，下面在 reply 里诚实告知「到步数上限、先汇报、可继续」。
            cap_reached = True

        reasoning_trace = ensure_dict(self.llm_reasoning_trace)
        first_plan = first_plan or last_plan or {}
        # 单步（含纯回复/未连接）保持与历史一致的顶层 plan 形状；多步才综合成 loop 计划。
        top_plan = first_plan if iterations <= 1 else self._summarize_loop_plan(
            message, first_plan, last_plan, steps
        )
        if cap_reached and isinstance(top_plan, dict):
            top_plan["stepLimitReached"] = True
            top_plan["nextStep"] = "paused"
            base_reply = str(top_plan.get("reply") or "").rstrip()
            notice = (
                f"（已到本轮 {RUNTIME_AGENT_MAX_STEPS} 步上限，先停下来汇报：上面是这一轮做到的部分。"
                "需要的话再说一声，我接着往下做。）"
            )
            top_plan["reply"] = f"{base_reply}\n\n{notice}".strip() if base_reply else notice

        turn = {
            "id": turn_id,
            "createdAt": now,
            "message": message,
            "observe": summarize_params(observe),
            "plan": top_plan,
        }
        if client_turn_id:
            turn["clientTurnId"] = client_turn_id
        if attachments:
            turn["attachments"] = attachments
        if steps:
            turn["steps"] = steps
        if int(reasoning_trace.get("itemCount") or 0) > 0:
            turn["reasoning"] = reasoning_trace
        if shell_payload is not None:
            turn["shell"] = shell_payload
        if skill_payload is not None:
            turn["skill"] = skill_payload
        if write_payload is not None:
            turn["write"] = write_payload

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
                "attachmentCount": len(attachments),
                "plan": top_plan,
                "stepCount": len(steps),
                "shellStatus": shell_payload.get("status") if shell_payload else "none",
                "skillStatus": skill_payload.get("status") if skill_payload else "none",
                "skillTool": skill_payload.get("tool") if skill_payload else "",
                "writeStatus": write_payload.get("status") if write_payload else "none",
            }
        )
        self._append_runtime_run(
            self._runtime_run_from_turn(
                event="runtime_turn_completed",
                status="cancelled" if str(top_plan.get("nextStep") or "") == "cancelled" else "completed",
                agent_name=agent_name,
                session_id=session_id,
                turn_id=turn_id,
                client_turn_id=client_turn_id,
                message=message,
                attachments=attachments,
                params=params,
                top_plan=top_plan,
                steps=steps,
                shell_payload=shell_payload,
                skill_payload=skill_payload,
                write_payload=write_payload,
                approval_id=approval_id,
            )
        )

        payload = {
            "ok": True,
            "session_id": session_id,
            "sessionId": session_id,
            "turn_id": turn_id,
            "turnId": turn_id,
            "observe": observe,
            "plan": top_plan,
        }
        if client_turn_id:
            payload["clientTurnId"] = client_turn_id
        if attachments:
            payload["attachments"] = attachments
        if steps:
            payload["steps"] = steps
        if int(reasoning_trace.get("itemCount") or 0) > 0:
            payload["reasoning"] = reasoning_trace
        if shell_payload is not None:
            payload["shell"] = shell_payload
        if skill_payload is not None:
            payload["skill"] = skill_payload
        if write_payload is not None:
            payload["write"] = write_payload
        if approval_id:
            payload["approval_id"] = approval_id
            payload["approvalId"] = approval_id
        # 结果回显：优先写入结果，其次 shell 结果（保持既有契约）。
        if write_payload is not None and write_payload.get("result") is not None:
            payload["result"] = write_payload["result"]
        elif shell_payload is not None and shell_payload.get("result"):
            payload["result"] = shell_payload["result"]
        return payload

    def _execute_write_request(
        self,
        tool_name: str,
        params: dict[str, Any],
        agent_name: str,
    ) -> dict[str, Any]:
        """Route an avatar/Unity write through the supervised tool path.

        The loop never auto-applies writes: write handlers are converted into an
        approval request, and approved execution later creates the pre-write
        checkpoint and calls the registered handler. We surface the approval id so
        the turn can stop and wait. Direct tools remain supported for legacy
        request wrappers, but write-handler ids must not be sent through
        `call_tool` because they are not direct tools.
        """
        if not tool_name:
            return {"ok": False, "status": "blocked", "tool": "", "error": "No write tool was resolved."}
        try:
            if tool_name in self._write_handlers:
                outcome = self.create_apply_request(
                    {
                        "target_tool": tool_name,
                        "arguments": params,
                        "reason": f"Agent proposed supervised write: {tool_name}",
                        "agent_name": agent_name,
                        "requires_explicit_approval": True,
                        "disable_auto_approval": True,
                        "explicit_approval_reason": (
                            "Agent-proposed Unity/project write requires explicit user approval."
                        ),
                        "preview": {
                            "summary": f"Agent proposed {tool_name}.",
                            "paramsSummary": summarize_params(params),
                        },
                    }
                )
            else:
                outcome = self.call_tool(tool_name, params, agent_name=agent_name)
        except AgentGatewayError as exc:
            return {
                "ok": False,
                "status": "unavailable",
                "tool": tool_name,
                "paramsSummary": summarize_params(params),
                "error": str(exc),
            }
        outcome = ensure_dict(outcome)
        approval = extract_approval_id(outcome)
        if not approval:
            approval_record = ensure_dict(outcome.get("approval"))
            if not approval_record:
                approval_record = ensure_dict(ensure_dict(outcome.get("result")).get("approval"))
            approval = str(approval_record.get("id") or "").strip()
        if approval:
            status = "approval_pending"
        elif outcome.get("ok"):
            status = "executed"
        else:
            status = "failed"
        payload: dict[str, Any] = {
            "ok": bool(outcome.get("ok")),
            "status": status,
            "tool": tool_name,
            "paramsSummary": summarize_params(params),
            "result": outcome.get("result") if "result" in outcome else outcome,
        }
        if approval:
            payload["approval_id"] = approval
            payload["approvalId"] = approval
        if outcome.get("error"):
            payload["error"] = outcome["error"]
        return payload

    def _summarize_loop_plan(
        self,
        message: str,
        first_plan: dict[str, Any],
        last_plan: dict[str, Any],
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Synthesize the top-level plan for a multi-step turn.

        The user-facing fields (reply/summary/planner/nextStep) come from the final
        plan — the turn's actual outcome (e.g. "I proposed adding the object on the
        only model"). The per-action flags are reset to False because each concrete
        tool action lives in `steps`; leaving them set would invite a re-fire.
        """
        plan = dict(last_plan or {})
        plan["shellNeeded"] = False
        plan["shellCommand"] = ""
        plan["skillNeeded"] = False
        plan["skillTool"] = ""
        plan["writeNeeded"] = False
        plan["multiStep"] = True
        plan["stepCount"] = len(steps)
        plan["steps"] = steps
        if not plan.get("planner"):
            plan["planner"] = first_plan.get("planner") or "deterministic-local"
        if not plan.get("reply"):
            plan["reply"] = last_plan.get("reply") or last_plan.get("summary") or ""
        return plan

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
        goals = [
            goal
            for goal in self.list_agent_goals(limit=8, session_id=session_id or "").get("goals", [])
            if str(goal.get("status") or "") in {"active", "paused"}
        ]
        memories = self.list_agent_memory(limit=12).get("memories", [])
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
            "goals": {
                "count": len(goals),
                "items": [
                    {
                        "goalId": goal.get("goalId"),
                        "status": goal.get("status"),
                        "title": goal.get("title"),
                        "summary": goal.get("summary"),
                        "projectRoot": goal.get("projectRoot"),
                    }
                    for goal in goals[:8]
                ],
            },
            "memory": {
                "count": len(memories),
                "items": [
                    {
                        "memoryId": memory.get("memoryId"),
                        "scope": memory.get("scope"),
                        "kind": memory.get("kind"),
                        "text": memory.get("text"),
                        "projectRoot": memory.get("projectRoot"),
                    }
                    for memory in memories[:12]
                ],
            },
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

    def _runtime_run_from_turn(
        self,
        *,
        event: str,
        status: str,
        agent_name: str,
        session_id: str,
        turn_id: str,
        client_turn_id: str,
        message: str,
        attachments: list[dict[str, Any]],
        params: dict[str, Any],
        top_plan: dict[str, Any],
        steps: list[dict[str, Any]],
        shell_payload: dict[str, Any] | None,
        skill_payload: dict[str, Any] | None,
        write_payload: dict[str, Any] | None,
        approval_id: str,
    ) -> dict[str, Any]:
        approval_ids = []
        if approval_id:
            approval_ids.append(approval_id)
        for payload in (shell_payload, skill_payload, write_payload):
            record = ensure_dict(payload)
            extracted = str(record.get("approval_id") or record.get("approvalId") or "").strip()
            if extracted and extracted not in approval_ids:
                approval_ids.append(extracted)
            nested = ensure_dict(record.get("approval"))
            nested_id = str(nested.get("id") or "").strip()
            if nested_id and nested_id not in approval_ids:
                approval_ids.append(nested_id)
        return {
            "event": event,
            "status": status,
            "agent": agent_name,
            "sessionId": session_id,
            "turnId": turn_id,
            "clientTurnId": client_turn_id,
            "messageSummary": summarize_text(message),
            "attachmentCount": len(attachments),
            "provider": params.get("provider") or "",
            "providerLabel": params.get("providerLabel") or params.get("provider_label") or "",
            "model": params.get("model") or "",
            "projectRoot": params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "",
            "planSummary": summarize_text(str(top_plan.get("summary") or top_plan.get("reply") or "")),
            "planner": top_plan.get("planner") or "",
            "nextStep": top_plan.get("nextStep") or "",
            "stepCount": len(steps),
            "steps": steps,
            "approvalIds": approval_ids,
            "shellStatus": shell_payload.get("status") if shell_payload else "none",
            "skillStatus": skill_payload.get("status") if skill_payload else "none",
            "skillTool": skill_payload.get("tool") if skill_payload else "",
            "writeStatus": write_payload.get("status") if write_payload else "none",
            "writeTool": write_payload.get("tool") if write_payload else "",
        }

    def request_runtime_cancel(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        turn_id = str(params.get("turn_id") or params.get("turnId") or "").strip()
        client_turn_id = str(params.get("client_turn_id") or params.get("clientTurnId") or "").strip()
        session_id = str(params.get("session_id") or params.get("sessionId") or "").strip()
        reason = str(params.get("reason") or "user_stop").strip()[:200]
        target_id = turn_id or client_turn_id
        if not target_id and not session_id:
            raise AgentGatewayError("turnId, clientTurnId, or sessionId is required.", status_code=400)
        with self._lock:
            if session_id:
                self._cancelled_runtime_turns.add(session_id)
            if turn_id:
                self._cancelled_runtime_turns.add(turn_id)
            if client_turn_id:
                self._cancelled_runtime_turns.add(client_turn_id)
        event = {
            "event": "runtime_turn_cancel_requested",
            "status": "cancel_requested",
            "sessionId": session_id,
            "turnId": turn_id,
            "clientTurnId": client_turn_id,
            "reason": reason,
        }
        self._append_runtime_run(event)
        return {"ok": True, "status": "cancel_requested", "event": event}

    def _runtime_cancel_requested(
        self,
        *,
        session_id: str = "",
        turn_id: str = "",
        client_turn_id: str = "",
    ) -> bool:
        candidates = [item for item in (session_id, turn_id, client_turn_id) if item]
        if not candidates:
            return False
        with self._lock:
            return any(item in self._cancelled_runtime_turns for item in candidates)

    def record_runtime_queue_event(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        client_turn_id = str(params.get("client_turn_id") or params.get("clientTurnId") or "").strip()
        if not client_turn_id:
            raise AgentGatewayError("clientTurnId is required.", status_code=400)
        event = {
            "event": "runtime_turn_queued",
            "status": "queued",
            "sessionId": str(params.get("session_id") or params.get("sessionId") or "").strip(),
            "clientTurnId": client_turn_id,
            "messageSummary": summarize_text(str(params.get("message") or "")),
            "attachmentCount": len(ensure_list(params.get("attachments"))),
            "provider": params.get("provider") or "",
            "providerLabel": params.get("providerLabel") or params.get("provider_label") or "",
            "model": params.get("model") or "",
            "projectRoot": params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "",
        }
        self._append_runtime_run(event)
        return {"ok": True, "status": "queued", "event": event}

    def list_runtime_runs(
        self,
        *,
        limit: int = 50,
        session_id: str = "",
        project_root: str = "",
        client_turn_id: str = "",
    ) -> dict[str, Any]:
        events = self._read_runtime_run_events(limit=max(limit * 8, 100))
        session_id = session_id.strip()
        project_root = project_root.strip()
        client_turn_id = client_turn_id.strip()
        def event_approval_ids(event: dict[str, Any]) -> set[str]:
            ids = {str(event.get("approvalId") or "").strip()}
            ids.update(str(item).strip() for item in ensure_list(event.get("approvalIds")))
            return {item for item in ids if item}

        related_approval_ids: set[str] = set()
        if session_id:
            for event in events:
                if str(event.get("sessionId") or "") == session_id:
                    related_approval_ids.update(event_approval_ids(event))

        runs_by_key: dict[str, dict[str, Any]] = {}
        event_count_by_key: dict[str, int] = {}
        filtered_events: list[dict[str, Any]] = []
        for event in events:
            related_by_approval = bool(related_approval_ids.intersection(event_approval_ids(event)))
            if session_id and str(event.get("sessionId") or "") != session_id and not related_by_approval:
                continue
            if client_turn_id and str(event.get("clientTurnId") or "") != client_turn_id:
                continue
            if project_root and str(event.get("projectRoot") or "") not in {"", project_root}:
                continue
            filtered_events.append(event)
            key = (
                str(event.get("clientTurnId") or "").strip()
                or str(event.get("turnId") or "").strip()
                or f"event:{event.get('id') or len(filtered_events)}"
            )
            event_count_by_key[key] = event_count_by_key.get(key, 0) + 1
            previous = runs_by_key.get(key, {})
            merged = {**previous, **event}
            merged["eventCount"] = event_count_by_key[key]
            merged["lastEvent"] = event.get("event") or ""
            runs_by_key[key] = merged
        runs = sorted(
            runs_by_key.values(),
            key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or item.get("timestamp") or ""),
            reverse=True,
        )[: max(1, min(limit, 200))]
        return {
            "ok": True,
            "schema": "vrcforge.runtime_runs.v1",
            "runs": [redact_sensitive(item) for item in runs],
            "events": [redact_sensitive(item) for item in filtered_events[-max(1, min(limit, 200)):]],
            "count": len(runs),
        }

    def request_desktop_action(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        action = re.sub(r"[^a-z0-9_.-]+", "_", str(params.get("action") or "").strip().lower()).strip("_")
        if action not in {"screenshot", "annotation", "browser", "desktop_rescue", "computer_use"}:
            raise AgentGatewayError("Unsupported desktop action.", status_code=400)
        project_root = str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "").strip()
        session_id = str(params.get("sessionId") or params.get("session_id") or "").strip()
        client_turn_id = str(params.get("clientTurnId") or params.get("client_turn_id") or "").strip()
        prompt = summarize_text(str(params.get("prompt") or params.get("message") or ""), 800)
        status = "requested"
        result: dict[str, Any] = {}
        error = ""
        if action == "screenshot" and "vrcforge_capture_screenshot" in self._tools:
            try:
                result = self.call_tool("vrcforge_capture_screenshot", ensure_dict(params.get("params")), agent_name="desktop-agent")
                status = "executed" if result.get("ok") else "failed"
                error = str(result.get("error") or "")
            except Exception as exc:  # noqa: BLE001 - explicit desktop actions should return actionable errors.
                status = "failed"
                error = str(exc)
        elif action in {"desktop_rescue", "computer_use"}:
            status = "unavailable"
            error = "Desktop control bridge is not connected. Launch this action from a configured desktop skill/provider."
        else:
            status = "recorded"
        event = {
            "event": "desktop_action",
            "status": status,
            "action": action,
            "sessionId": session_id,
            "clientTurnId": client_turn_id,
            "projectRoot": project_root,
            "promptSummary": prompt,
            "resultSummary": summarize_params(result) if result else {},
            "error": error,
        }
        self._append_jsonl(self.desktop_action_log_path, "vrcforge.desktop_action.v1", event)
        return {"ok": status not in {"failed"}, "schema": "vrcforge.desktop_action.v1", "status": status, "action": action, "event": redact_sensitive(event), "result": redact_sensitive(result), "error": error}

    def list_desktop_actions(self, *, limit: int = 50, session_id: str = "", project_root: str = "") -> dict[str, Any]:
        events = self._read_jsonl(self.desktop_action_log_path, limit=max(limit, 50))
        filtered = []
        for event in events:
            if session_id and str(event.get("sessionId") or "") != session_id:
                continue
            if project_root and str(event.get("projectRoot") or "") not in {"", project_root}:
                continue
            filtered.append(redact_sensitive(event))
        filtered = filtered[-max(1, min(limit, AGENT_DESKTOP_ACTION_MAX_ITEMS)) :]
        filtered.reverse()
        return {"ok": True, "schema": "vrcforge.desktop_actions.v1", "actions": filtered, "count": len(filtered)}

    def create_agent_goal(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        title = summarize_text(str(params.get("title") or params.get("goal") or "").strip(), 240)
        if not title:
            raise AgentGatewayError("Goal title is required.", status_code=400)
        goal_id = f"goal_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        event = {
            "event": "goal_created",
            "status": "active",
            "goalId": goal_id,
            "title": title,
            "summary": summarize_text(str(params.get("summary") or ""), 1000),
            "projectRoot": str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "").strip(),
            "sessionId": str(params.get("sessionId") or params.get("session_id") or "").strip(),
            "approvalPolicy": "uses_vrcforge_approval_checkpoint_rollback",
        }
        self._append_jsonl(self.agent_goal_log_path, "vrcforge.agent_goal.v1", event)
        return {"ok": True, "goal": self._project_agent_goals()[goal_id]}

    def update_agent_goal(self, goal_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        goal_id = str(goal_id or "").strip()
        if not goal_id:
            raise AgentGatewayError("goalId is required.", status_code=400)
        status = str(params.get("status") or "").strip().lower()
        allowed = {"active", "paused", "completed", "cancelled"}
        if status not in allowed:
            raise AgentGatewayError("Goal status must be active, paused, completed, or cancelled.", status_code=400)
        current = self._project_agent_goals()
        if goal_id not in current:
            raise AgentGatewayError(f"Goal was not found: {goal_id}", status_code=404)
        event = {
            "event": "goal_updated",
            "status": status,
            "goalId": goal_id,
            "summary": summarize_text(str(params.get("summary") or params.get("note") or ""), 1000),
            "projectRoot": str(params.get("projectRoot") or current[goal_id].get("projectRoot") or ""),
            "sessionId": str(params.get("sessionId") or current[goal_id].get("sessionId") or ""),
        }
        self._append_jsonl(self.agent_goal_log_path, "vrcforge.agent_goal.v1", event)
        return {"ok": True, "goal": self._project_agent_goals()[goal_id]}

    def list_agent_goals(self, *, limit: int = 50, project_root: str = "", session_id: str = "") -> dict[str, Any]:
        goals = list(self._project_agent_goals().values())
        if project_root:
            goals = [goal for goal in goals if str(goal.get("projectRoot") or "") in {"", project_root}]
        if session_id:
            goals = [goal for goal in goals if str(goal.get("sessionId") or "") in {"", session_id}]
        goals.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        goals = goals[: max(1, min(limit, AGENT_GOAL_MAX_ITEMS))]
        return {"ok": True, "schema": "vrcforge.agent_goals.v1", "goals": [redact_sensitive(goal) for goal in goals], "count": len(goals)}

    def create_agent_memory(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        text = summarize_text(str(params.get("text") or params.get("content") or "").strip(), 2000)
        if not text:
            raise AgentGatewayError("Memory text is required.", status_code=400)
        scope = str(params.get("scope") or "project").strip().lower()
        if scope not in {"user", "project"}:
            raise AgentGatewayError("Memory scope must be user or project.", status_code=400)
        memory_id = f"mem_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        event = {
            "event": "memory_created",
            "status": "active",
            "memoryId": memory_id,
            "scope": scope,
            "kind": summarize_text(str(params.get("kind") or "preference"), 80),
            "text": text,
            "projectRoot": str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "").strip(),
            "source": summarize_text(str(params.get("source") or "user"), 120),
        }
        self._append_jsonl(self.agent_memory_log_path, "vrcforge.agent_memory.v1", event)
        return {"ok": True, "memory": self._project_agent_memory()[memory_id]}

    def delete_agent_memory(self, memory_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        memory_id = str(memory_id or "").strip()
        if not memory_id:
            raise AgentGatewayError("memoryId is required.", status_code=400)
        current = self._project_agent_memory(include_deleted=True)
        if memory_id not in current:
            raise AgentGatewayError(f"Memory was not found: {memory_id}", status_code=404)
        event = {
            "event": "memory_deleted",
            "status": "deleted",
            "memoryId": memory_id,
            "reason": summarize_text(str(params.get("reason") or ""), 500),
        }
        self._append_jsonl(self.agent_memory_log_path, "vrcforge.agent_memory.v1", event)
        return {"ok": True, "memory": self._project_agent_memory(include_deleted=True)[memory_id]}

    def clear_agent_memory(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        project_root = str(params.get("projectRoot") or params.get("project_root") or "").strip()
        scope = str(params.get("scope") or "").strip().lower()
        current = self._project_agent_memory()
        cleared = 0
        for memory_id, memory in current.items():
            if project_root and str(memory.get("projectRoot") or "") != project_root:
                continue
            if scope and str(memory.get("scope") or "") != scope:
                continue
            self._append_jsonl(
                self.agent_memory_log_path,
                "vrcforge.agent_memory.v1",
                {
                    "event": "memory_deleted",
                    "status": "deleted",
                    "memoryId": memory_id,
                    "reason": summarize_text(str(params.get("reason") or "clear"), 500),
                },
            )
            cleared += 1
        return {"ok": True, "cleared": cleared}

    def list_agent_memory(self, *, limit: int = 50, project_root: str = "", scope: str = "") -> dict[str, Any]:
        memories = list(self._project_agent_memory().values())
        if project_root:
            memories = [
                memory
                for memory in memories
                if str(memory.get("scope") or "") == "user" or str(memory.get("projectRoot") or "") in {"", project_root}
            ]
        if scope:
            memories = [memory for memory in memories if str(memory.get("scope") or "") == scope]
        memories.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        memories = memories[: max(1, min(limit, AGENT_MEMORY_MAX_ITEMS))]
        return {"ok": True, "schema": "vrcforge.agent_memory_list.v1", "memories": [redact_sensitive(memory) for memory in memories], "count": len(memories)}

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
            self.append_audit({"event": "shell_rejected", "classification": classification, "agent": agent_name, **self.permission_audit_context()})
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

        result = self._run_shell_command(
            command,
            Path(classification["cwd"]),
            timeout_seconds=int(params.get("timeout_seconds") or 120),
            cancel_ids=[
                str(params.get("session_id") or params.get("sessionId") or ""),
                str(params.get("turn_id") or params.get("turnId") or ""),
                str(params.get("client_turn_id") or params.get("clientTurnId") or ""),
            ],
        )
        self.append_audit(
            {
                "event": "shell_executed",
                "agent": agent_name,
                "classification": classification,
                "result": summarize_shell_result(result),
                **self.permission_audit_context(),
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

        result = self._run_shell_command(
            command,
            cwd,
            timeout_seconds=timeout_seconds,
            cancel_ids=[
                str(params.get("session_id") or params.get("sessionId") or ""),
                str(params.get("turn_id") or params.get("turnId") or ""),
                str(params.get("client_turn_id") or params.get("clientTurnId") or ""),
            ],
        )
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
                **self.permission_audit_context(),
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
        requires_explicit_approval = bool(
            params.get("requires_explicit_approval")
            or params.get("requiresExplicitApproval")
            or params.get("disable_auto_approval")
            or params.get("disableAutoApproval")
        )
        execution_mode = normalize_execution_mode(config.execution_mode)
        full_permission_auto = execution_mode == "roslyn_full_auto"
        permission_context = self.permission_audit_context(config)
        auto_policy_reason = self._write_auto_manual_approval_reason(target_tool, arguments, preview)
        requires_explicit_for_mode = (
            requires_explicit_approval or (execution_mode == "auto" and bool(auto_policy_reason))
        ) and not full_permission_auto
        explicit_approval_reason = str(
            params.get("explicit_approval_reason")
            or params.get("explicitApprovalReason")
            or auto_policy_reason
            or "This write request requires explicit user approval."
        ).strip()
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
            requires_explicit_approval=requires_explicit_for_mode,
            explicit_approval_reason=explicit_approval_reason,
        )
        if full_permission_auto and (requires_explicit_approval or auto_policy_reason):
            self.append_audit(
                {
                    "event": "approval_explicit_requirement_overridden_by_full_permission",
                    "approvalId": approval.get("id"),
                    "mode": execution_mode,
                    **permission_context,
                    "reason": explicit_approval_reason,
                    "targetTool": target_tool,
                }
            )
        if self.auto_approval_enabled(config) and not requires_explicit_for_mode:
            auto_payload = self._auto_execute_approval(approval)
            if auto_payload is not None:
                return auto_payload
        if self.auto_approval_enabled(config) and requires_explicit_for_mode:
            self.append_audit(
                {
                    "event": "approval_auto_approval_suppressed",
                    "approvalId": approval.get("id"),
                    "mode": execution_mode,
                    **permission_context,
                    "reason": explicit_approval_reason,
                    "targetTool": target_tool,
                }
            )
        return {
            "ok": True,
            "status": "pending",
            "approval": approval,
            "message": (
                "Apply request requires explicit user approval."
                if requires_explicit_for_mode
                else "Apply request is waiting for user approval."
            ),
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
        block_reason = self._auto_approval_block_reason(approval)
        permission_context = self.permission_audit_context()
        if block_reason:
            self.append_audit(
                {
                    "event": "approval_auto_approval_suppressed",
                    "approvalId": approval_id,
                    "mode": permission_context["permissionMode"],
                    **permission_context,
                    "reason": block_reason,
                    "targetTool": approval.get("targetTool"),
                }
            )
            return None
        approved = self.approve(approval_id)
        if not approved.get("ok"):
            return None
        self.append_audit(
            {
                "event": "approval_auto_approved",
                "approvalId": approval_id,
                "mode": permission_context["permissionMode"],
                **permission_context,
                "targetTool": approval.get("targetTool"),
                "agent": approval.get("agentName") or "",
                "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
            }
        )
        applied = self.apply_approved({"approval_id": approval_id})
        payload: dict[str, Any] = {
            "ok": bool(applied.get("ok")),
            "status": "executed" if applied.get("ok") else "failed",
            "autoApproved": True,
            "fullPermission": permission_context["fullPermission"],
            "permissionMode": permission_context["permissionMode"],
            "permissionLabel": permission_context["permissionLabel"],
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
            if target_tool == "vrcforge_shell_execute":
                write_handler = AgentWriteHandler(
                    "vrcforge_shell_execute",
                    "Execute an approved high-risk shell command.",
                    "high",
                    self.execute_shell_payload,
                )
            else:
                write_handler = self._write_handlers.get(target_tool)
            if not write_handler:
                raise AgentGatewayError(f"Write target is no longer available: {target_tool}", status_code=404)

            active_recoveries = self._active_apply_recoveries()
            if active_recoveries and target_tool not in APPLY_RECOVERY_EXEMPT_WRITE_TARGETS:
                self.append_audit(
                    {
                        "event": "approval_blocked_by_interrupted_apply_recovery",
                        "approvalId": approval_id,
                        "targetTool": target_tool,
                        "recoveries": active_recoveries,
                    }
                )
                return {
                    "ok": False,
                    "status": "blocked_recovery",
                    "approval": approval,
                    "recoveries": active_recoveries,
                    "recovery": active_recoveries[0],
                    "error": "A previous write did not finish cleanly. Restore or resolve the interrupted apply recovery before running another write.",
                }

            approval["status"] = "applying"
            self._approvals[approval_id] = approval
            permission_context = self.permission_audit_context()
            self.append_audit({"event": "approval_applying", "approval": approval, **permission_context})
            self._append_runtime_run(
                {
                    "event": "approval_applying",
                    "status": "applying",
                    "approvalId": approval_id,
                    "approvalIds": [approval_id],
                    **permission_context,
                    "targetTool": target_tool,
                    "agent": approval.get("agentName") or "",
                    "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
                }
            )

        checkpoint: dict[str, Any] | None = None
        recovery: dict[str, Any] | None = None
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
                if checkpoint.get("ok"):
                    recovery = self._start_apply_recovery(approval, arguments, checkpoint)
            result = write_handler.handler(arguments)
            if isinstance(result, dict) and result.get("ok") is False:
                message = (
                    result.get("error")
                    or result.get("message")
                    or result.get("reason")
                    or f"{target_tool} returned ok=false."
                )
                raise AgentGatewayError(str(message))
            with self._lock:
                approval["status"] = "applied"
                approval["appliedAt"] = utc_now_iso()
                approval["resultSummary"] = summarize_params(result if isinstance(result, dict) else {"result": result})
                self._approvals[approval_id] = approval
                permission_context = self.permission_audit_context()
                self.append_audit({"event": "approval_applied", "approval": approval, **permission_context})
                self._append_runtime_run(
                    {
                        "event": "approval_applied",
                        "status": "applied",
                        "approvalId": approval_id,
                        "approvalIds": [approval_id],
                        **permission_context,
                        "targetTool": target_tool,
                        "agent": approval.get("agentName") or "",
                        "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
                        "checkpointId": ensure_dict(checkpoint).get("id") if checkpoint else "",
                        "checkpointIds": [ensure_dict(checkpoint).get("id")] if checkpoint and ensure_dict(checkpoint).get("id") else [],
                        "resultSummary": approval.get("resultSummary") or "",
                    }
                )
            if recovery:
                self._finish_apply_recovery(
                    recovery,
                    status="applied",
                    resolution="write_completed",
                    result_summary=summarize_params(result if isinstance(result, dict) else {"result": result}),
                )
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
                permission_context = self.permission_audit_context()
                self.append_audit({"event": "approval_failed", "approval": approval, **permission_context})
                self._append_runtime_run(
                    {
                        "event": "approval_failed",
                        "status": "failed",
                        "approvalId": approval_id,
                        "approvalIds": [approval_id],
                        **permission_context,
                        "targetTool": target_tool,
                        "agent": approval.get("agentName") or "",
                        "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
                        "checkpointId": ensure_dict(checkpoint).get("id") if checkpoint else "",
                        "checkpointIds": [ensure_dict(checkpoint).get("id")] if checkpoint and ensure_dict(checkpoint).get("id") else [],
                        "error": str(exc),
                    }
                )
            if recovery:
                self._finish_apply_recovery(
                    recovery,
                    status="needs_recovery",
                    resolution="write_failed_after_checkpoint",
                    error=str(exc),
                )
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

    def checkpoint_archive_usage(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self.ensure_config()
        archives = self._checkpoint_archive_files()
        total_bytes = sum(item["sizeBytes"] for item in archives)
        protected_ids = self._protected_checkpoint_archive_ids(include_recent=True)
        labels = self._checkpoint_archive_labels()
        items = [
            {
                "checkpointId": item["checkpointId"],
                "path": str(item["path"]),
                "sizeBytes": item["sizeBytes"],
                "sizeMb": round(item["sizeBytes"] / CHECKPOINT_ARCHIVE_BYTES_PER_MB, 2),
                "modifiedAt": item["modifiedAt"],
                "protected": item["checkpointId"] in protected_ids,
                "label": labels.get(item["checkpointId"], ""),
            }
            for item in sorted(archives, key=lambda x: x["modifiedAt"], reverse=True)
        ]
        return {
            "ok": True,
            "schema": "vrcforge.checkpoint_archive_storage.v2",
            "directory": str(self.checkpoint_store_dir),
            "defaultDirectory": str(self.default_checkpoint_store_dir),
            "relocated": getattr(self, "_checkpoint_store_override", None) is not None,
            "sizeBytes": total_bytes,
            "sizeMb": round(total_bytes / CHECKPOINT_ARCHIVE_BYTES_PER_MB, 2),
            "archiveCount": len(archives),
            "protectedCount": sum(1 for item in items if item["protected"]),
            "maxSizeMb": normalize_checkpoint_archive_max_size_mb(config.checkpoint_archive_max_size_mb),
            "archives": items[:500],
        }

    def _checkpoint_archive_labels(self) -> dict[str, str]:
        """checkpointId -> 简短标签，便于前端列表辨认存档来源。"""
        labels: dict[str, str] = {}
        for entry in self._read_checkpoint_entries(limit=1000):
            cid = str(entry.get("id") or "").strip()
            if not cid or cid in labels:
                continue
            label = str(
                entry.get("targetTool")
                or entry.get("reason")
                or entry.get("strategy")
                or ""
            ).strip()
            created = str(entry.get("createdAt") or "").strip()
            labels[cid] = (f"{label} · {created}" if label and created else label or created)
        return labels

    def delete_checkpoint_archives(self, checkpoint_ids: Any) -> dict[str, Any]:
        """删除用户在面板里勾选的存档；活跃恢复检查点强制保护，不会被删。"""
        requested = {
            str(cid).strip()
            for cid in (checkpoint_ids or [])
            if str(cid).strip()
        }
        archives = self._checkpoint_archive_files()
        protected_ids = self._protected_checkpoint_archive_ids(include_recent=True)
        deleted: list[dict[str, Any]] = []
        protected_skipped: list[str] = []
        for archive in archives:
            cid = archive["checkpointId"]
            if cid not in requested:
                continue
            if cid in protected_ids:
                protected_skipped.append(cid)
                continue
            path = archive["path"]
            try:
                path.unlink()
            except OSError as exc:
                self.append_audit(
                    {
                        "event": "checkpoint_archive_delete_failed",
                        "path": str(path),
                        "error": str(exc),
                    }
                )
                continue
            deleted.append(
                {
                    "path": str(path),
                    "checkpointId": cid,
                    "sizeBytes": archive["sizeBytes"],
                }
            )
            self._remove_empty_checkpoint_archive_parents(path.parent)
        if deleted:
            self.append_audit(
                {
                    "event": "checkpoint_archive_deleted",
                    "deletedCount": len(deleted),
                    "deletedBytes": sum(item["sizeBytes"] for item in deleted),
                    "protectedSkipped": protected_skipped,
                }
            )
        usage = self.checkpoint_archive_usage()
        return {
            "ok": True,
            "schema": "vrcforge.checkpoint_archive_delete.v1",
            "directory": str(self.checkpoint_store_dir),
            "requestedCount": len(requested),
            "deletedCount": len(deleted),
            "deletedBytes": sum(item["sizeBytes"] for item in deleted),
            "protectedSkipped": protected_skipped,
            "deleted": deleted[:50],
            "sizeBytes": usage["sizeBytes"],
            "sizeMb": usage["sizeMb"],
            "archiveCount": usage["archiveCount"],
        }

    def relocate_checkpoint_archives(self, target_directory: Any) -> dict[str, Any]:
        """把检查点存档目录迁到新位置：先复制 ZIP、改写 checkpoints.jsonl 中的
        archivePath、再切换配置、最后删除旧文件。任何一步崩溃都不会让回滚失效，
        因为旧目录在改写+切配置成功前始终保持可用。"""
        config = self.ensure_config()
        raw = normalize_checkpoint_archive_dir(target_directory)
        if not raw:
            return {"ok": False, "code": "directory_required", "error": "checkpoint archive directory required"}
        # 安全闸：有未结的写入恢复/回滚时拒绝迁移，避免迁移途中回滚找不到旧存档。
        if self._active_apply_recoveries():
            return {
                "ok": False,
                "code": "active_recovery",
                "error": "an apply rollback is still pending; resolve it before relocating",
            }
        new_dir = Path(raw)
        if not new_dir.is_absolute():
            return {"ok": False, "code": "not_absolute", "error": "directory must be an absolute path"}
        current_dir = self.checkpoint_store_dir
        try:
            current_resolved = current_dir.resolve()
        except OSError:
            current_resolved = current_dir
        try:
            new_resolved = new_dir.resolve()
        except OSError:
            new_resolved = new_dir
        if new_resolved == current_resolved:
            # 目录没变，仅确保配置持久化。
            config.checkpoint_archive_dir = str(new_resolved)
            self.save_config(config)
            usage = self.checkpoint_archive_usage(config)
            return {
                "ok": True,
                "schema": "vrcforge.checkpoint_archive_relocate.v1",
                "unchanged": True,
                "directory": str(self.checkpoint_store_dir),
                "copiedCount": 0,
                "rewrittenCount": 0,
                "removedOldCount": 0,
                "sizeBytes": usage["sizeBytes"],
                "archiveCount": usage["archiveCount"],
            }
        # 禁止新旧目录互相嵌套，否则复制/删除会自噬。
        if current_resolved == new_resolved or current_resolved in new_resolved.parents or new_resolved in current_resolved.parents:
            return {"ok": False, "code": "nested", "error": "new directory must not nest with the current one"}
        try:
            new_resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "code": "mkdir_failed", "error": str(exc)}
        probe = new_resolved / ".vrcforge-write-test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return {"ok": False, "code": "not_writable", "error": str(exc)}

        # 1) 复制全部 ZIP（保持相对结构），同时记录 checkpointId -> 新绝对路径。
        id_to_new_path: dict[str, str] = {}
        copied = 0
        if current_dir.is_dir():
            for src in current_dir.rglob("*.zip"):
                if not src.is_file():
                    continue
                rel = src.relative_to(current_dir)
                dst = new_resolved / rel
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                except OSError as exc:
                    self.append_audit(
                        {"event": "checkpoint_archive_relocate_copy_failed", "path": str(src), "error": str(exc)}
                    )
                    return {"ok": False, "code": "copy_failed", "error": f"{src}: {exc}"}
                id_to_new_path[src.stem] = str(dst)
                copied += 1

        # 2) 改写 checkpoints.jsonl 中已复制存档的 archivePath（按 checkpointId 精确映射）。
        rewritten = self._rewrite_checkpoint_archive_paths(id_to_new_path)

        # 3) 切换配置 + 内存覆盖，从此 checkpoint_store_dir 指向新目录。
        config.checkpoint_archive_dir = str(new_resolved)
        self.save_config(config)

        # 4) 复制与改写都成功后，再清理旧目录里的 ZIP（尽力而为）。
        removed_old = 0
        if current_dir.is_dir():
            for src in list(current_dir.rglob("*.zip")):
                try:
                    src.unlink()
                    removed_old += 1
                    self._remove_old_relocate_parents(src.parent, current_resolved)
                except OSError:
                    continue
            try:
                if not any(current_dir.iterdir()):
                    current_dir.rmdir()
            except OSError:
                pass

        self.append_audit(
            {
                "event": "checkpoint_archive_relocated",
                "from": str(current_resolved),
                "to": str(new_resolved),
                "copiedCount": copied,
                "rewrittenCount": rewritten,
                "removedOldCount": removed_old,
            }
        )
        usage = self.checkpoint_archive_usage(config)
        return {
            "ok": True,
            "schema": "vrcforge.checkpoint_archive_relocate.v1",
            "directory": str(self.checkpoint_store_dir),
            "from": str(current_resolved),
            "to": str(new_resolved),
            "copiedCount": copied,
            "rewrittenCount": rewritten,
            "removedOldCount": removed_old,
            "sizeBytes": usage["sizeBytes"],
            "archiveCount": usage["archiveCount"],
        }

    def _rewrite_checkpoint_archive_paths(self, id_to_new_path: dict[str, str]) -> int:
        """按 checkpointId 把 checkpoints.jsonl 里命中的 archivePath 改写成新路径。"""
        if not id_to_new_path:
            return 0
        path = self.checkpoint_log_path
        if not path.exists():
            return 0
        lines = path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        changed = 0
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                out.append(line)
                continue
            if isinstance(record, dict):
                stored = record.get("archivePath")
                if isinstance(stored, str) and stored:
                    cid = str(record.get("id") or Path(stored).stem)
                    new_path = id_to_new_path.get(cid)
                    if new_path and new_path != stored:
                        record["archivePath"] = new_path
                        changed += 1
                        out.append(json.dumps(record, ensure_ascii=False))
                        continue
            out.append(line)
        if changed:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        return changed

    def _remove_old_relocate_parents(self, start: Path, root: Path) -> None:
        current = start
        while True:
            try:
                resolved = current.resolve()
            except OSError:
                break
            if resolved == root or root not in resolved.parents:
                break
            try:
                if any(current.iterdir()):
                    break
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def prune_checkpoint_archives(
        self,
        max_size_mb: int | None = None,
        *,
        protected_checkpoint_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        config = self.ensure_config()
        normalized_max = normalize_checkpoint_archive_max_size_mb(
            config.checkpoint_archive_max_size_mb if max_size_mb is None else max_size_mb
        )
        archives = self._checkpoint_archive_files()
        total_bytes = sum(item["sizeBytes"] for item in archives)
        protected_ids = set(protected_checkpoint_ids or set())
        protected_ids.update(self._protected_checkpoint_archive_ids(include_recent=True, archives=archives))
        if normalized_max <= 0:
            return {
                **self.checkpoint_archive_usage(config),
                "maxSizeMb": normalized_max,
                "limitEnabled": False,
                "deletedCount": 0,
                "deletedBytes": 0,
                "protectedCount": len(protected_ids),
            }

        target_bytes = normalized_max * CHECKPOINT_ARCHIVE_BYTES_PER_MB
        deleted: list[dict[str, Any]] = []
        remaining_bytes = total_bytes
        for archive in sorted(archives, key=lambda item: item["modifiedAt"]):
            if remaining_bytes <= target_bytes:
                break
            if archive["checkpointId"] in protected_ids:
                continue
            path = archive["path"]
            try:
                path.unlink()
            except OSError as exc:
                self.append_audit(
                    {
                        "event": "checkpoint_archive_prune_failed",
                        "path": str(path),
                        "error": str(exc),
                    }
                )
                continue
            deleted.append({"path": str(path), "checkpointId": archive["checkpointId"], "sizeBytes": archive["sizeBytes"]})
            remaining_bytes -= archive["sizeBytes"]
            self._remove_empty_checkpoint_archive_parents(path.parent)

        summary = {
            "ok": True,
            "schema": "vrcforge.checkpoint_archive_prune.v1",
            "directory": str(self.checkpoint_store_dir),
            "maxSizeMb": normalized_max,
            "limitEnabled": True,
            "initialBytes": total_bytes,
            "remainingBytes": max(0, remaining_bytes),
            "remainingMb": round(max(0, remaining_bytes) / CHECKPOINT_ARCHIVE_BYTES_PER_MB, 2),
            "archiveCount": len(self._checkpoint_archive_files()),
            "deletedCount": len(deleted),
            "deletedBytes": sum(item["sizeBytes"] for item in deleted),
            "protectedCount": len(protected_ids),
            "deleted": deleted[:20],
        }
        if deleted:
            self.append_audit({"event": "checkpoint_archives_pruned", **summary})
        return summary

    def list_interrupted_apply_recoveries(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        include_resolved = bool(params.get("includeResolved") or params.get("include_resolved"))
        limit = max(1, min(int(params.get("limit") or 50), 500))
        project_filter = str(params.get("project_root") or params.get("projectRoot") or "").strip()
        recoveries = self._coalesced_apply_recoveries(include_resolved=include_resolved)
        if project_filter:
            normalized = normalize_filesystem_path(project_filter)
            recoveries = [
                recovery for recovery in recoveries
                if normalize_filesystem_path(str(recovery.get("projectRoot") or "")) == normalized
            ]
        recoveries = recoveries[:limit]
        active = [recovery for recovery in recoveries if self._apply_recovery_blocks_writes(recovery)]
        return {
            "ok": True,
            "schema": APPLY_RECOVERY_SCHEMA,
            "recoveries": recoveries,
            "count": len(recoveries),
            "activeCount": len(active),
            "blockingWrites": bool(active),
            "restoreTool": "vrcforge_restore_checkpoint",
            "resolveTool": "vrcforge_resolve_interrupted_apply_recovery",
        }

    def preview_interrupted_apply_recovery(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        recovery = self._select_apply_recovery(params, include_resolved=bool(params.get("includeResolved") or params.get("include_resolved")))
        if not recovery:
            return {"ok": False, "schema": APPLY_RECOVERY_SCHEMA, "error": "interrupted apply recovery was not found."}
        checkpoint_id = str(recovery.get("checkpointId") or recovery.get("checkpoint_id") or "").strip()
        checkpoint_preview = (
            self.preview_restore_checkpoint({"checkpointId": checkpoint_id})
            if checkpoint_id
            else {"ok": False, "error": "recovery has no checkpointId."}
        )
        payload = {
            "ok": True,
            "schema": APPLY_RECOVERY_SCHEMA,
            "recovery": recovery,
            "checkpointPreview": checkpoint_preview,
            "blockingWrites": self._apply_recovery_blocks_writes(recovery),
            "restoreRequest": {
                "targetTool": "vrcforge_restore_checkpoint",
                "arguments": {"checkpointId": checkpoint_id, "confirmRestore": True},
            },
            "manualResolveRequest": {
                "targetTool": "vrcforge_resolve_interrupted_apply_recovery",
                "arguments": {"recoveryId": str(recovery.get("id") or ""), "confirmResolved": True},
            },
        }
        return payload

    def export_interrupted_apply_incident_bundle(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        recovery = self._select_apply_recovery(params, include_resolved=True)
        if not recovery:
            return {"ok": False, "schema": APPLY_RECOVERY_SCHEMA, "error": "interrupted apply recovery was not found."}
        preview = self.preview_interrupted_apply_recovery({"recoveryId": recovery.get("id"), "includeResolved": True})
        generated_at = utc_now_iso()
        bundle = {
            "schema": "vrcforge.interrupted_apply_incident_bundle.v1",
            "generatedAt": generated_at,
            "recovery": recovery,
            "preview": preview,
            "recentAuditLogs": self.recent_audit_logs(limit=80),
        }
        bundle_dir = self.audit_dir / "incident-bundles"
        filename = f"{recovery.get('id') or 'recovery'}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        bundle_path = bundle_dir / filename
        atomic_write_json(bundle_path, bundle)
        self.append_audit({"event": "apply_recovery_incident_bundle_exported", "recoveryId": recovery.get("id"), "path": str(bundle_path)})
        return {"ok": True, "schema": bundle["schema"], "path": str(bundle_path), "bundle": bundle}

    def resolve_interrupted_apply_recovery(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if params.get("confirm_resolved") is not True and params.get("confirmResolved") is not True:
            return {"ok": False, "schema": APPLY_RECOVERY_SCHEMA, "error": "confirmResolved=true is required to resolve an interrupted apply recovery."}
        recovery = self._select_apply_recovery(params, include_resolved=True)
        if not recovery:
            return {"ok": False, "schema": APPLY_RECOVERY_SCHEMA, "error": "interrupted apply recovery was not found."}
        if not self._apply_recovery_blocks_writes(recovery):
            return {"ok": True, "schema": APPLY_RECOVERY_SCHEMA, "status": "already_resolved", "recovery": recovery}
        resolution_note = str(params.get("note") or params.get("reason") or "User confirmed the interrupted write was handled outside VRCForge.").strip()
        resolved = self._finish_apply_recovery(
            recovery,
            status="dismissed",
            resolution="manual_confirmed",
            note=resolution_note,
        )
        return {"ok": True, "schema": APPLY_RECOVERY_SCHEMA, "status": "resolved", "recovery": resolved}

    def list_adjustment_checkpoints(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        limit = max(1, min(int(params.get("limit") or 50), 500))
        include_deleted = bool(params.get("includeDeleted") or params.get("include_deleted"))
        kind_filter = self._normalize_adjustment_checkpoint_kind(params.get("kind"), required=False)
        raw_project_filter = str(params.get("project_root") or params.get("projectRoot") or "").strip()
        project_filter = normalize_filesystem_path(raw_project_filter) if raw_project_filter else ""
        avatar_filter = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
        entries = self._read_adjustment_checkpoint_entries()
        if not include_deleted:
            entries = [entry for entry in entries if not entry.get("deletedAt")]
        if kind_filter:
            entries = [entry for entry in entries if entry.get("kind") == kind_filter]
        if project_filter:
            entries = [
                entry for entry in entries
                if normalize_filesystem_path(str(entry.get("projectRoot") or "")) == project_filter
            ]
        if avatar_filter:
            entries = [entry for entry in entries if str(entry.get("avatarPath") or "") == avatar_filter]
        entries = entries[:limit]
        return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoints": entries, "count": len(entries)}

    def get_adjustment_checkpoint(self, entry_id: str) -> dict[str, Any]:
        entry = self._load_adjustment_checkpoint(entry_id)
        if not entry:
            return {"ok": False, "error": "adjustment checkpoint was not found."}
        return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoint": entry}

    def create_adjustment_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        kind = self._normalize_adjustment_checkpoint_kind(params.get("kind"), required=True)
        checkpoint = self._resolve_or_create_adjustment_base_checkpoint(params)
        if not checkpoint.get("ok"):
            return checkpoint
        entry = self._build_adjustment_checkpoint_entry(params, checkpoint, kind=kind, existing={})
        entries = self._read_adjustment_checkpoint_entries()
        requested_id = str(params.get("id") or "").strip()
        if requested_id and any(item.get("id") == requested_id for item in entries) and not bool(params.get("overwrite")):
            return {"ok": False, "error": "adjustment checkpoint id already exists; pass overwrite=true or use overwrite endpoint."}
        if requested_id:
            entry["id"] = requested_id
            entries = [item for item in entries if item.get("id") != requested_id]
        entries.insert(0, entry)
        self._write_adjustment_checkpoint_entries(entries)
        self.append_audit({"event": "adjustment_checkpoint_created", "checkpoint": entry})
        return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoint": entry, "baseCheckpoint": checkpoint}

    def update_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any]) -> dict[str, Any]:
        entries = self._read_adjustment_checkpoint_entries()
        for index, entry in enumerate(entries):
            if entry.get("id") != entry_id:
                continue
            updated = dict(entry)
            self._apply_adjustment_checkpoint_metadata(updated, params)
            if "kind" in params:
                updated["kind"] = self._normalize_adjustment_checkpoint_kind(params.get("kind"), required=True)
            if "checkpointId" in params or "checkpoint_id" in params:
                checkpoint = self._load_checkpoint(str(params.get("checkpointId") or params.get("checkpoint_id") or "").strip())
                if not checkpoint:
                    return {"ok": False, "error": "checkpointId was not found."}
                updated["checkpointId"] = str(checkpoint.get("id") or "")
                updated["targetTool"] = str(checkpoint.get("targetTool") or updated.get("targetTool") or "")
            updated["updatedAt"] = utc_now_iso()
            entries[index] = updated
            self._write_adjustment_checkpoint_entries(entries)
            self.append_audit({"event": "adjustment_checkpoint_updated", "checkpoint": updated})
            return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoint": updated}
        return {"ok": False, "error": "adjustment checkpoint was not found."}

    def delete_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        hard_delete = bool(params.get("hardDelete") or params.get("hard_delete"))
        entries = self._read_adjustment_checkpoint_entries()
        for index, entry in enumerate(entries):
            if entry.get("id") != entry_id:
                continue
            deleted = dict(entry)
            if hard_delete:
                entries.pop(index)
            else:
                deleted["deletedAt"] = utc_now_iso()
                deleted["updatedAt"] = deleted["deletedAt"]
                entries[index] = deleted
            self._write_adjustment_checkpoint_entries(entries)
            self.append_audit({"event": "adjustment_checkpoint_deleted", "checkpoint": deleted, "hardDelete": hard_delete})
            return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoint": deleted, "hardDelete": hard_delete}
        return {"ok": False, "error": "adjustment checkpoint was not found."}

    def select_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        entries = self._read_adjustment_checkpoint_entries()
        selected_entry: dict[str, Any] | None = None
        slot = self._normalize_adjustment_selection_slot(params.get("slot") or params.get("compareSlot") or params.get("compare_slot"))
        for entry in entries:
            if entry.get("id") == entry_id and not entry.get("deletedAt"):
                selected_entry = dict(entry)
                break
        if not selected_entry:
            return {"ok": False, "error": "adjustment checkpoint was not found."}
        kind = str(selected_entry.get("kind") or "")
        compare_group = str(params.get("compareGroup") or params.get("compare_group") or selected_entry.get("compareGroup") or kind)
        now = utc_now_iso()
        updated_entries: list[dict[str, Any]] = []
        for entry in entries:
            current = dict(entry)
            if current.get("kind") == kind and str(current.get("compareGroup") or kind) == compare_group:
                selected_slots = [
                    item for item in ensure_string_list(current.get("selectedSlots"))
                    if item.upper() != slot
                ]
                if current.get("id") == entry_id:
                    selected_slots.append(slot)
                    current["selectedSlots"] = selected_slots
                    current["selectedAt"] = now
                    current["selected"] = True
                    current["selectionSlot"] = slot
                else:
                    current["selectedSlots"] = selected_slots
                    if not selected_slots:
                        current.pop("selectedAt", None)
                        current["selected"] = False
                        current.pop("selectionSlot", None)
            updated_entries.append(current)
        self._write_adjustment_checkpoint_entries(updated_entries)
        selected = self._load_adjustment_checkpoint(entry_id) or selected_entry
        self.append_audit({"event": "adjustment_checkpoint_selected", "checkpoint": selected, "slot": slot})
        return {
            "ok": True,
            "schema": "vrcforge.adjustment_checkpoint_timeline.v1",
            "checkpoint": selected,
            "selection": {"kind": kind, "compareGroup": compare_group, "slot": slot, "checkpointId": selected.get("checkpointId")},
        }

    def overwrite_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any]) -> dict[str, Any]:
        entries = self._read_adjustment_checkpoint_entries()
        for index, entry in enumerate(entries):
            if entry.get("id") != entry_id:
                continue
            checkpoint = self._resolve_or_create_adjustment_base_checkpoint({**entry, **params})
            if not checkpoint.get("ok"):
                return checkpoint
            updated = self._build_adjustment_checkpoint_entry(
                params,
                checkpoint,
                kind=self._normalize_adjustment_checkpoint_kind(params.get("kind") or entry.get("kind"), required=True),
                existing=entry,
            )
            updated["id"] = entry_id
            revisions = ensure_list(entry.get("revisions"))
            revisions.append(
                {
                    "checkpointId": entry.get("checkpointId"),
                    "overwrittenAt": utc_now_iso(),
                    "label": entry.get("label"),
                }
            )
            updated["revisions"] = revisions
            updated["overwriteCount"] = len(revisions)
            entries[index] = updated
            self._write_adjustment_checkpoint_entries(entries)
            self.append_audit({"event": "adjustment_checkpoint_overwritten", "checkpoint": updated})
            return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "checkpoint": updated, "baseCheckpoint": checkpoint}
        return {"ok": False, "error": "adjustment checkpoint was not found."}

    def preview_restore_adjustment_checkpoint(self, entry_id: str) -> dict[str, Any]:
        entry = self._load_adjustment_checkpoint(entry_id)
        if not entry or entry.get("deletedAt"):
            return {"ok": False, "error": "adjustment checkpoint was not found."}
        preview = self.preview_restore_checkpoint({"checkpointId": str(entry.get("checkpointId") or "")})
        preview["adjustmentCheckpoint"] = entry
        return preview

    def get_selected_adjustment_checkpoints(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        kind_filter = self._normalize_adjustment_checkpoint_kind(params.get("kind"), required=False)
        compare_group = str(params.get("compareGroup") or params.get("compare_group") or "").strip()
        entries = [
            entry for entry in self._read_adjustment_checkpoint_entries()
            if not entry.get("deletedAt") and ensure_string_list(entry.get("selectedSlots"))
        ]
        if kind_filter:
            entries = [entry for entry in entries if entry.get("kind") == kind_filter]
        if compare_group:
            entries = [entry for entry in entries if str(entry.get("compareGroup") or entry.get("kind") or "") == compare_group]
        selections: dict[str, dict[str, Any]] = {}
        for entry in entries:
            key_base = f"{entry.get('kind')}:{entry.get('compareGroup') or entry.get('kind')}"
            for slot in ensure_string_list(entry.get("selectedSlots")):
                selections[f"{key_base}:{slot.upper()}"] = entry
        return {"ok": True, "schema": "vrcforge.adjustment_checkpoint_timeline.v1", "selections": selections, "count": len(selections)}

    def _normalize_adjustment_selection_slot(self, value: Any) -> str:
        slot = str(value or "current").strip().upper()
        if slot in {"A", "B", "CURRENT"}:
            return slot
        raise AgentGatewayError("selection slot must be A, B, or current.", status_code=400)

    def preview_restore_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        checkpoint = self._load_checkpoint(str(params.get("checkpoint_id") or params.get("checkpointId") or "").strip())
        if not checkpoint:
            return {"ok": False, "error": "checkpoint_id was not found."}
        available = self._checkpoint_available(checkpoint)
        if not available.get("ok"):
            return available
        if checkpoint.get("strategy") == "local_state_archive":
            return self._preview_local_state_checkpoint(checkpoint)
        if checkpoint.get("strategy") == "archive":
            return self._preview_archive_checkpoint(checkpoint)
        git_root = Path(str(checkpoint["gitRoot"]))
        ref = str(checkpoint["checkpointRef"])
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        diff = self._run_git(git_root, ["diff", "--name-status", ref, "--", *pathspecs])
        status = self._run_git(git_root, ["status", "--porcelain", "--", *pathspecs])
        payload = {
            "ok": diff["ok"] and status["ok"],
            "checkpoint": checkpoint,
            "changedFiles": [line for line in diff["stdout"].splitlines() if line.strip()],
            "workingTreeStatus": [line for line in status["stdout"].splitlines() if line.strip()],
            "error": diff.get("error") or status.get("error") or "",
        }
        payload["rollbackCoverageAudit"] = self._build_checkpoint_rollback_coverage_audit(checkpoint, phase="preview")
        return payload

    def restore_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        checkpoint = self._load_checkpoint(str(params.get("checkpoint_id") or params.get("checkpointId") or "").strip())
        if not checkpoint:
            return {"ok": False, "error": "checkpoint_id was not found."}
        if params.get("confirm_restore") is not True and params.get("confirmRestore") is not True:
            return {"ok": False, "error": "confirmRestore=true is required to restore a checkpoint."}
        available = self._checkpoint_available(checkpoint)
        if not available.get("ok"):
            return available
        local_state_restore = checkpoint.get("strategy") == "local_state_archive"
        if local_state_restore:
            payload = self._restore_local_state_checkpoint(checkpoint)
        elif checkpoint.get("strategy") == "archive":
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
        if payload.get("ok") and not local_state_restore:
            cache_cleanup = self._cleanup_checkpoint_restore_unity_caches(checkpoint)
            payload["unityCacheCleanup"] = cache_cleanup
            if cache_cleanup.get("errors"):
                payload["unityCacheCleanupWarning"] = "; ".join(ensure_string_list(cache_cleanup.get("errors")))
        if payload.get("ok") and not local_state_restore and self.checkpoint_restore_handler is not None:
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
        elif payload.get("ok") and local_state_restore:
            payload["status"] = "restored"
        if payload.get("ok"):
            payload["rollbackCoverageAudit"] = self._build_checkpoint_rollback_coverage_audit(
                checkpoint,
                phase="restore",
                restore_payload=payload,
            )
            resolved_recoveries = self._resolve_apply_recoveries_for_checkpoint(
                str(checkpoint.get("id") or ""),
                resolution="checkpoint_restored",
                restore_payload=payload,
            )
            if resolved_recoveries:
                payload["resolvedApplyRecoveries"] = resolved_recoveries
        self.append_audit({"event": "checkpoint_restored", **payload})
        return payload

    def _create_pre_write_checkpoint(self, approval: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any] | None:
        target_tool = str(approval.get("targetTool") or "")
        if not target_tool or target_tool in APPLY_RECOVERY_EXEMPT_WRITE_TARGETS:
            return None
        checkpoint_id = f"ckpt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        base_record = {
            "id": checkpoint_id,
            "createdAt": utc_now_iso(),
            "approvalId": str(approval.get("id") or ""),
            "targetTool": target_tool,
            "status": "unavailable",
        }
        if target_tool in LOCAL_STATE_CHECKPOINT_TARGETS:
            return self._create_local_state_checkpoint(base_record)
        project_root = self._resolve_checkpoint_project_root(arguments)
        if project_root is None:
            record = {
                **base_record,
                "ok": False,
                "blocking": True,
                "status": "failed",
                "error": "No Unity project root was available for checkpointing.",
            }
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
                warning = str(prepare_result.get("error") or "Unity could not prepare a rollback checkpoint.")
                record["unityPrepareWarning"] = warning
                record["warnings"] = [
                    *ensure_string_list(record.get("warnings")),
                    "Unity prepare checkpoint failed; using file-level checkpoint fallback.",
                ]

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
        record["rollbackCoverageAudit"] = self._build_checkpoint_rollback_coverage_audit(record, phase="checkpoint")
        self._append_checkpoint(record)
        self.append_audit({"event": "checkpoint_created", "checkpoint": record})
        self.prune_checkpoint_archives(protected_checkpoint_ids={checkpoint_id})
        return record

    def _create_archive_checkpoint(self, project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
        checkpoint_id = str(record["id"])
        project_key = self._checkpoint_project_key(project_root)
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
            fsync_file_path(temp_path)
            os.replace(temp_path, archive_path)
            fsync_directory_best_effort(archive_path.parent)
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
        record["rollbackCoverageAudit"] = self._build_checkpoint_rollback_coverage_audit(record, phase="checkpoint")
        self._append_checkpoint(record)
        self.append_audit({"event": "checkpoint_created", "checkpoint": record})
        self.prune_checkpoint_archives(protected_checkpoint_ids={checkpoint_id})
        return record

    def _create_local_state_checkpoint(self, record: dict[str, Any]) -> dict[str, Any]:
        checkpoint_id = str(record["id"])
        archive_dir = self.checkpoint_store_dir / "local-state"
        archive_path = archive_dir / f"{checkpoint_id}.zip"
        temp_path = archive_path.with_suffix(".zip.tmp")
        roots = self._local_state_checkpoint_roots()
        state_roots: list[dict[str, Any]] = []
        file_count = 0
        total_bytes = 0
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            if temp_path.exists():
                temp_path.unlink()
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                for scope, root in roots.items():
                    resolved = root.resolve()
                    root_file_count = 0
                    if resolved.exists():
                        if resolved.is_symlink() or not resolved.is_dir():
                            raise ValueError(f"Local state root is not a regular directory: {resolved}")
                        for source in sorted(resolved.rglob("*")):
                            if source.is_symlink():
                                raise ValueError(f"Refusing to checkpoint symlinked local state path: {source}")
                            if not source.is_file():
                                continue
                            relative = f"{scope}/{source.relative_to(resolved).as_posix()}"
                            archive.write(source, relative)
                            size = source.stat().st_size
                            file_count += 1
                            root_file_count += 1
                            total_bytes += size
                    state_roots.append(
                        {
                            "id": scope,
                            "path": str(resolved),
                            "exists": resolved.exists(),
                            "fileCount": root_file_count,
                        }
                    )
            fsync_file_path(temp_path)
            os.replace(temp_path, archive_path)
            fsync_directory_best_effort(archive_path.parent)
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
                    "strategy": "local_state_archive",
                    "archivePath": str(archive_path),
                    "pathspecs": list(LOCAL_STATE_CHECKPOINT_SCOPE),
                    "stateRoots": state_roots,
                    "error": f"Local state checkpoint failed: {exc}",
                }
            )
            self._append_checkpoint(record)
            return record

        record.update(
            {
                "ok": True,
                "status": "ready",
                "strategy": "local_state_archive",
                "archivePath": str(archive_path),
                "pathspecs": list(LOCAL_STATE_CHECKPOINT_SCOPE),
                "stateRoots": state_roots,
                "fileCount": file_count,
                "uncompressedBytes": total_bytes,
            }
        )
        record["rollbackCoverageAudit"] = self._build_checkpoint_rollback_coverage_audit(record, phase="checkpoint")
        self._append_checkpoint(record)
        self.append_audit({"event": "checkpoint_created", "checkpoint": record})
        return record

    def _checkpoint_project_key(self, project_root: Path) -> str:
        return hashlib.sha256(normalize_filesystem_path(str(project_root)).encode("utf-8")).hexdigest()[:16]

    def _resolve_checkpoint_archive_path(self, checkpoint: dict[str, Any], expected_strategy: str) -> Path:
        strategy = str(checkpoint.get("strategy") or "")
        if strategy != expected_strategy:
            raise ValueError("Checkpoint strategy does not match archive type.")
        checkpoint_id = str(checkpoint.get("id") or "").strip()
        if not checkpoint_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", checkpoint_id) or checkpoint_id in {".", ".."}:
            raise ValueError("Checkpoint id is invalid.")
        raw_archive = str(checkpoint.get("archivePath") or "").strip()
        if not raw_archive:
            raise ValueError("Checkpoint archive path is missing.")

        archive_path = Path(raw_archive).resolve()
        store_root = self.checkpoint_store_dir.resolve()
        if not is_path_within(archive_path, store_root):
            raise ValueError("Checkpoint archive is outside configured storage.")
        if archive_path.name != f"{checkpoint_id}.zip":
            raise ValueError("Checkpoint archive filename does not match checkpoint id.")

        if expected_strategy == "archive":
            project_root_text = str(checkpoint.get("projectRoot") or "").strip()
            if not project_root_text:
                raise ValueError("Checkpoint project root is missing.")
            expected_parent = (store_root / self._checkpoint_project_key(Path(project_root_text).resolve())).resolve()
            if archive_path.parent != expected_parent:
                raise ValueError("Checkpoint archive does not match the recorded project root.")
        elif expected_strategy == "local_state_archive":
            expected_parent = (store_root / "local-state").resolve()
            if archive_path.parent != expected_parent:
                raise ValueError("Local state archive is outside its managed storage folder.")
        return archive_path

    def _normalize_project_archive_member(self, name: str, allowed_roots: set[str]) -> str:
        text = str(name or "").replace("\\", "/")
        member = PurePosixPath(text)
        parts = member.parts
        if (
            len(parts) < 2
            or member.is_absolute()
            or Path(str(name)).is_absolute()
            or looks_like_absolute_path(text)
            or parts[0] not in allowed_roots
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError(f"Unsafe archive member: {name}")
        return member.as_posix()

    def _preview_archive_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        project_root = Path(str(checkpoint["projectRoot"])).resolve()
        archive_path = self._resolve_checkpoint_archive_path(checkpoint, "archive")
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        allowed = set(UNITY_PROJECT_CHECKPOINT_SCOPE)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                archived: dict[str, tuple[int, int]] = {}
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    name = self._normalize_project_archive_member(info.filename, allowed)
                    if name in archived:
                        raise ValueError(f"Duplicate archive member: {name}")
                    archived[name] = (info.file_size, info.CRC)
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
            return {
                "ok": True,
                "checkpoint": checkpoint,
                "changedFiles": changed,
                "workingTreeStatus": changed,
                "rollbackCoverageAudit": self._build_checkpoint_rollback_coverage_audit(checkpoint, phase="preview"),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checkpoint": checkpoint, "error": str(exc)}

    def _preview_local_state_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        archive_path = self._resolve_checkpoint_archive_path(checkpoint, "local_state_archive")
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                archived = {
                    info.filename: (info.file_size, info.CRC)
                    for info in archive.infolist()
                    if not info.is_dir()
                }
                for name in archived:
                    self._validate_local_state_archive_member(name)
            current = self._local_state_archive_contents()
            changed = [f"M\t{name}" for name in sorted(archived.keys() & current.keys()) if archived[name] != current[name]]
            changed.extend(f"D\t{name}" for name in sorted(archived.keys() - current.keys()))
            changed.extend(f"A\t{name}" for name in sorted(current.keys() - archived.keys()))
            return {
                "ok": True,
                "checkpoint": checkpoint,
                "changedFiles": changed,
                "workingTreeStatus": changed,
                "rollbackCoverageAudit": self._build_checkpoint_rollback_coverage_audit(checkpoint, phase="preview"),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "checkpoint": checkpoint, "error": str(exc)}

    def _restore_archive_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        project_root = Path(str(checkpoint["projectRoot"])).resolve()
        archive_path = self._resolve_checkpoint_archive_path(checkpoint, "archive")
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        allowed = set(UNITY_PROJECT_CHECKPOINT_SCOPE)
        if not pathspecs or any(name not in allowed for name in pathspecs):
            return {"ok": False, "checkpoint": checkpoint, "error": "Archive checkpoint pathspecs are unsafe."}
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                members = [info for info in archive.infolist() if not info.is_dir()]
                archived: dict[str, zipfile.ZipInfo] = {}
                for info in members:
                    name = self._normalize_project_archive_member(info.filename, allowed)
                    if name in archived:
                        raise ValueError(f"Duplicate archive member: {name}")
                    archived[name] = info
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
                    target = (project_root / Path(*PurePosixPath(relative).parts)).resolve()
                    if not is_path_within(target, project_root):
                        raise ValueError(f"Unsafe restore target: {target}")
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
                        flush_and_fsync(destination)
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

    def _restore_local_state_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        archive_path = self._resolve_checkpoint_archive_path(checkpoint, "local_state_archive")
        roots = self._local_state_checkpoint_roots()
        state_roots = {
            str(item.get("id") or ""): ensure_dict(item)
            for item in ensure_list(checkpoint.get("stateRoots"))
            if isinstance(item, dict)
        }
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                members = [info for info in archive.infolist() if not info.is_dir()]
                for info in members:
                    self._validate_local_state_archive_member(info.filename)

                current = self._local_state_archive_contents()
                restored: list[str] = []
                deleted: list[str] = []
                app_state_root = self.user_constraints_path.parent.resolve()

                for scope, root in roots.items():
                    target_root = root.resolve()
                    if not is_path_within(target_root, app_state_root):
                        raise ValueError(f"Unsafe local state restore root: {target_root}")
                    if target_root.exists():
                        if target_root.is_symlink() or not target_root.is_dir():
                            raise ValueError(f"Local state restore root is not a regular directory: {target_root}")
                        shutil.rmtree(target_root)
                    deleted.extend(name for name in sorted(current) if name == scope or name.startswith(scope + "/"))
                    if state_roots.get(scope, {}).get("exists"):
                        target_root.mkdir(parents=True, exist_ok=True)

                for info in members:
                    parts = PurePosixPath(info.filename).parts
                    scope = parts[0]
                    root = roots[scope].resolve()
                    target = (root / Path(*parts[1:])).resolve()
                    if not is_path_within(target, root):
                        raise ValueError(f"Unsafe local state restore target: {target}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp_target = target.with_name(target.name + ".vrcforge-restore-tmp")
                    with archive.open(info, "r") as source, temp_target.open("wb") as destination:
                        shutil.copyfileobj(source, destination, length=1024 * 1024)
                        flush_and_fsync(destination)
                    os.replace(temp_target, target)
                    restored.append(info.filename)

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
            return {"ok": False, "checkpoint": checkpoint, "error": f"Local state restore failed: {exc}"}

    def _build_checkpoint_rollback_coverage_audit(
        self,
        checkpoint: dict[str, Any],
        phase: str,
        restore_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        restore_payload = restore_payload or {}
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        if checkpoint.get("strategy") == "local_state_archive":
            return self._build_local_state_rollback_coverage_audit(checkpoint, phase, restore_payload)
        touches_assets = self._checkpoint_touches_top_level(checkpoint, "Assets")
        touches_packages = self._checkpoint_touches_top_level(checkpoint, "Packages")
        touches_project_settings = self._checkpoint_touches_top_level(checkpoint, "ProjectSettings")
        project_root = Path(str(checkpoint.get("projectRoot") or "")).resolve() if checkpoint.get("projectRoot") else None
        stored_framework_snapshot = self._stored_checkpoint_framework_package_snapshot(checkpoint)
        framework_snapshot = (
            stored_framework_snapshot
            if phase != "checkpoint" and stored_framework_snapshot
            else self._checkpoint_framework_package_snapshot(project_root)
        )

        blocking_gaps: list[str] = []
        todos: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []

        def add_check(check_id: str, title: str, status: str, details: dict[str, Any]) -> None:
            checks.append({"id": check_id, "title": title, "status": status, **details})
            if status == "missing":
                blocking_gaps.append(check_id)

        add_check(
            "scene_prefab_component_state",
            "Scene, prefab, and serialized component state",
            "covered" if touches_assets else "missing",
            {
                "pathspec": "Assets",
                "covers": [
                    "scene files",
                    "prefabs",
                    "serialized Unity components",
                    "Modular Avatar and VRCFury components saved under Assets",
                    "NDMF plugin component settings saved under Assets",
                ],
            },
        )
        add_check(
            "generated_assets",
            "Generated assets",
            "covered" if touches_assets else "missing",
            {
                "pathspec": "Assets",
                "covers": [
                    "VRCForge generated assets under Assets",
                    "optimizer, wardrobe, shader, and import artifacts saved as project assets",
                ],
            },
        )
        add_check(
            "packages_manifest",
            "Packages manifest and lock state",
            "covered" if touches_packages else "missing",
            {
                "pathspec": "Packages",
                "covers": [
                    "Packages/manifest.json",
                    "Packages/packages-lock.json",
                    "MA, VRCF, NDMF, and optimizer package dependency versions",
                ],
                "frameworkPackages": framework_snapshot,
            },
        )
        add_check(
            "project_settings",
            "Project settings",
            "covered" if touches_project_settings else "missing",
            {
                "pathspec": "ProjectSettings",
                "covers": ["Unity project settings that can affect import, build, and validation behavior"],
            },
        )

        cache_cleanup = ensure_dict(restore_payload.get("unityCacheCleanup"))
        cache_status = "planned" if touches_packages else "skipped"
        cache_details: dict[str, Any] = {
            "requiresPackagesRestore": touches_packages,
            "targets": [f"Library/{name}" for name in UNITY_RESTORE_PACKAGE_CACHE_DIRS],
        }
        if phase == "restore":
            if not touches_packages:
                cache_status = "skipped"
            elif not cache_cleanup:
                cache_status = "missing"
            elif cache_cleanup.get("ok"):
                cache_status = "passed"
                cache_details["deleted"] = ensure_string_list(cache_cleanup.get("deleted"))
            else:
                cache_status = "warning"
                cache_details["errors"] = ensure_string_list(cache_cleanup.get("errors"))
        add_check(
            "package_cache_generated_folders",
            "Package cache and generated compiler folders",
            cache_status,
            cache_details,
        )

        reload_result = ensure_dict(restore_payload.get("unityReload"))
        if phase == "restore":
            if not self.checkpoint_restore_handler:
                reload_status = "missing"
            elif reload_result.get("ok"):
                reload_status = "passed"
            else:
                reload_status = "warning"
        else:
            reload_status = "planned" if self.checkpoint_restore_handler else "missing"
        add_check(
            "unity_reload_after_restore",
            "Unity reload after restore",
            reload_status,
            {
                "tool": "vrc_reload_after_checkpoint_restore",
                "reason": "Restored scenes/assets must be reloaded before MA/VRCF/NDMF scanners or validation can be trusted.",
            },
        )

        validation_status = "todo"
        validation_details = {
            "required": True,
            "tools": ["vrcforge_run_validation_report", "vrcforge_build_test_readiness"],
            "covers": [
                "Unity compile status",
                "package dependency status",
                "MA/VRCF conflict context",
                "generated residue",
                "avatar hierarchy, FX, menu, parameter, material, and performance scanners where available",
            ],
        }
        post_restore_validation = ensure_dict(restore_payload.get("postRestoreValidation"))
        if phase == "restore" and post_restore_validation:
            validation_status = "passed" if post_restore_validation.get("ok") else "warning"
            validation_details["result"] = post_restore_validation
        else:
            todos.append(
                {
                    "id": "run_post_restore_validation",
                    "status": "todo",
                    "required": True,
                    "reason": "Rollback proof must run read-only validation after restore, especially for MA/VRCF/NDMF-heavy avatars.",
                    "tools": validation_details["tools"],
                }
            )
        add_check("validation_after_restore", "Validation after restore", validation_status, validation_details)

        if blocking_gaps:
            gate_status = "blocked"
        elif todos:
            gate_status = "todo"
        else:
            gate_status = "ready"
        return {
            "ok": not blocking_gaps,
            "schema": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
            "phase": phase,
            "gateStatus": gate_status,
            "pathspecs": pathspecs,
            "checks": checks,
            "blockingGaps": blocking_gaps,
            "todos": todos,
            "caveats": [
                "Raw Unity MCP writes outside VRCForge cannot be checkpointed by this audit.",
                "Unity reload confirms restored files are reloaded; semantic avatar safety still requires the post-restore validation TODO.",
            ],
        }

    def _build_local_state_rollback_coverage_audit(
        self,
        checkpoint: dict[str, Any],
        phase: str,
        restore_payload: dict[str, Any],
    ) -> dict[str, Any]:
        pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
        restored_files = ensure_string_list(restore_payload.get("restoredFiles"))
        deleted_files = ensure_string_list(restore_payload.get("deletedFiles"))
        checks = [
            {
                "id": "local_skill_package_store",
                "title": "Community skill package store",
                "status": "covered" if "skill-packages" in pathspecs else "missing",
                "pathspec": "skill-packages",
                "covers": ["installed .vsk package versions", "skill package registry", "installed package metadata"],
            },
            {
                "id": "local_projected_user_skills",
                "title": "Projected user skills",
                "status": "covered" if "skills" in pathspecs else "missing",
                "pathspec": "skills",
                "covers": ["SKILL.md projections created by .vsk imports", "user skill enable/disable metadata"],
            },
        ]
        blocking_gaps = [str(item["id"]) for item in checks if item["status"] == "missing"]
        todos: list[dict[str, Any]] = []
        if phase == "restore":
            checks.append(
                {
                    "id": "local_state_restore_applied",
                    "title": "Local state restore applied",
                    "status": "passed" if restore_payload.get("ok") else "missing",
                    "restoredFileCount": len(restored_files),
                    "deletedFileCount": len(deleted_files),
                }
            )
        else:
            todos.append(
                {
                    "id": "preview_or_restore_local_state_checkpoint",
                    "status": "todo",
                    "required": True,
                    "reason": "Preview or restore this checkpoint to verify the exact skill-package/user-skill delta.",
                    "tools": ["vrcforge_preview_restore_checkpoint", "vrcforge_restore_checkpoint"],
                }
            )
        gate_status = "blocked" if blocking_gaps else ("todo" if todos else "ready")
        return {
            "ok": not blocking_gaps,
            "schema": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
            "phase": phase,
            "gateStatus": gate_status,
            "pathspecs": pathspecs,
            "checks": checks,
            "blockingGaps": blocking_gaps,
            "todos": todos,
            "caveats": [
                "This checkpoint covers VRCForge local skill-package state, not Unity project files.",
                "Unity project writes still require the Unity project checkpoint policy.",
            ],
        }

    def _checkpoint_framework_package_snapshot(self, project_root: Path | None) -> dict[str, Any]:
        packages: dict[str, Any] = {
            key: {
                "label": info["label"],
                "packageIds": list(info["packageIds"]),
                "detected": False,
                "manifestDependency": False,
                "lockDependency": False,
                "versions": [],
            }
            for key, info in ROLLBACK_FRAMEWORK_PACKAGES.items()
        }
        if project_root is None:
            return {"ok": False, "projectReadable": False, "packages": packages}

        packages_dir = project_root / "Packages"
        manifest_deps, manifest_error = self._read_package_dependency_file(packages_dir / "manifest.json")
        lock_deps, lock_error = self._read_package_dependency_file(packages_dir / "packages-lock.json")
        for key, info in ROLLBACK_FRAMEWORK_PACKAGES.items():
            item = packages[key]
            versions: list[str] = []
            for package_id in info["packageIds"]:
                if package_id in manifest_deps:
                    item["manifestDependency"] = True
                    versions.append(str(manifest_deps[package_id]))
                if package_id in lock_deps:
                    item["lockDependency"] = True
                    versions.append(str(lock_deps[package_id]))
            item["detected"] = bool(item["manifestDependency"] or item["lockDependency"])
            item["versions"] = sorted({version for version in versions if version})

        return {
            "ok": not (manifest_error or lock_error),
            "projectReadable": packages_dir.is_dir(),
            "manifestPath": "Packages/manifest.json",
            "manifestReadable": bool(manifest_deps) or (packages_dir / "manifest.json").is_file(),
            "manifestError": manifest_error,
            "lockPath": "Packages/packages-lock.json",
            "lockReadable": bool(lock_deps) or (packages_dir / "packages-lock.json").is_file(),
            "lockError": lock_error,
            "packages": packages,
        }

    def _read_package_dependency_file(self, path: Path) -> tuple[dict[str, Any], str]:
        if not path.is_file():
            return {}, ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            return {}, str(exc)
        dependencies = payload.get("dependencies") if isinstance(payload, dict) else {}
        if not isinstance(dependencies, dict):
            return {}, ""
        result: dict[str, Any] = {}
        for key, value in dependencies.items():
            if isinstance(value, dict):
                result[str(key)] = value.get("version") or value.get("source") or ""
            else:
                result[str(key)] = value
        return result, ""

    def _stored_checkpoint_framework_package_snapshot(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        audit = ensure_dict(checkpoint.get("rollbackCoverageAudit"))
        for item in audit.get("checks") or []:
            if isinstance(item, dict) and item.get("id") == "packages_manifest":
                return ensure_dict(item.get("frameworkPackages"))
        return {}

    def _local_state_checkpoint_roots(self) -> dict[str, Path]:
        app_state_root = self.user_constraints_path.parent
        return {
            "skill-packages": app_state_root / "skill-packages",
            "skills": self.user_skills_dir,
        }

    def _local_state_archive_contents(self) -> dict[str, tuple[int, int]]:
        result: dict[str, tuple[int, int]] = {}
        for scope, root in self._local_state_checkpoint_roots().items():
            if not root.is_dir():
                continue
            for source in sorted(root.rglob("*")):
                if source.is_symlink() or not source.is_file():
                    continue
                crc = 0
                with source.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        crc = zlib.crc32(chunk, crc)
                relative = f"{scope}/{source.relative_to(root).as_posix()}"
                result[relative] = (source.stat().st_size, crc & 0xFFFFFFFF)
        return result

    def _validate_local_state_archive_member(self, name: str) -> None:
        parts = PurePosixPath(str(name)).parts
        if len(parts) < 2 or parts[0] not in LOCAL_STATE_CHECKPOINT_SCOPE or ".." in parts:
            raise ValueError(f"Unsafe local state archive member: {name}")
        if PurePosixPath(str(name)).is_absolute() or Path(str(name)).is_absolute():
            raise ValueError(f"Unsafe local state archive member: {name}")

    def _cleanup_checkpoint_restore_unity_caches(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        if not self._checkpoint_touches_packages(checkpoint):
            return {"ok": True, "skipped": True, "reason": "checkpoint does not restore Packages", "deleted": [], "errors": []}
        project_root = Path(str(checkpoint.get("projectRoot") or "")).resolve()
        library_root = (project_root / "Library").resolve()
        deleted: list[str] = []
        errors: list[str] = []
        for name in UNITY_RESTORE_PACKAGE_CACHE_DIRS:
            target = (library_root / name).resolve()
            if not is_path_within(target, library_root):
                errors.append(f"Unsafe Unity cache path skipped: {target}")
                continue
            if not target.exists():
                continue
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                deleted.append(str(target))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{target}: {exc}")
        return {
            "ok": not errors,
            "skipped": False,
            "reason": "checkpoint restores Packages",
            "deleted": deleted,
            "errors": errors,
        }

    def _checkpoint_touches_packages(self, checkpoint: dict[str, Any]) -> bool:
        return self._checkpoint_touches_top_level(checkpoint, "Packages")

    def _checkpoint_touches_top_level(self, checkpoint: dict[str, Any], top_level: str) -> bool:
        for pathspec in ensure_string_list(checkpoint.get("pathspecs")):
            parts = Path(str(pathspec).replace("\\", "/")).parts
            if top_level in parts:
                return True
        return False

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
        if checkpoint.get("strategy") == "local_state_archive":
            pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
            try:
                archive_path = self._resolve_checkpoint_archive_path(checkpoint, "local_state_archive")
            except ValueError as exc:
                return {"ok": False, "checkpoint": checkpoint, "error": f"Local state checkpoint metadata is invalid: {exc}"}
            if not archive_path.is_file() or not pathspecs:
                return {"ok": False, "checkpoint": checkpoint, "error": "Local state checkpoint metadata is incomplete."}
            try:
                with zipfile.ZipFile(archive_path, "r") as archive:
                    if archive.testzip() is not None:
                        raise ValueError("archive CRC validation failed")
                    for info in archive.infolist():
                        if not info.is_dir():
                            self._validate_local_state_archive_member(info.filename)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "checkpoint": checkpoint, "error": f"Local state checkpoint is unreadable: {exc}"}
            return {"ok": True}
        if checkpoint.get("strategy") == "archive":
            pathspecs = ensure_string_list(checkpoint.get("pathspecs"))
            try:
                archive_path = self._resolve_checkpoint_archive_path(checkpoint, "archive")
            except ValueError as exc:
                return {"ok": False, "checkpoint": checkpoint, "error": f"Archive checkpoint metadata is invalid: {exc}"}
            if not archive_path.is_file() or not pathspecs:
                return {"ok": False, "checkpoint": checkpoint, "error": "Archive checkpoint metadata is incomplete."}
            try:
                with zipfile.ZipFile(archive_path, "r") as archive:
                    if archive.testzip() is not None:
                        raise ValueError("archive CRC validation failed")
                    allowed = set(UNITY_PROJECT_CHECKPOINT_SCOPE)
                    for info in archive.infolist():
                        if not info.is_dir():
                            self._normalize_project_archive_member(info.filename, allowed)
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
            flush_and_fsync(handle)
        self._maybe_record_adjustment_checkpoint(record)

    def _checkpoint_archive_files(self) -> list[dict[str, Any]]:
        root = self.checkpoint_store_dir
        if not root.is_dir():
            return []
        archives: list[dict[str, Any]] = []
        for path in root.rglob("*.zip"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            archives.append(
                {
                    "path": path,
                    "checkpointId": path.stem,
                    "sizeBytes": stat.st_size,
                    "modifiedAt": stat.st_mtime,
                }
            )
        return archives

    def _protected_checkpoint_archive_ids(
        self,
        *,
        include_recent: bool = False,
        archives: list[dict[str, Any]] | None = None,
    ) -> set[str]:
        protected: set[str] = set()
        for recovery in self._active_apply_recoveries():
            checkpoint_id = str(recovery.get("checkpointId") or recovery.get("checkpoint_id") or "").strip()
            if checkpoint_id:
                protected.add(checkpoint_id)
        if include_recent:
            candidates = archives if archives is not None else self._checkpoint_archive_files()
            for archive in sorted(candidates, key=lambda item: item["modifiedAt"], reverse=True)[
                :CHECKPOINT_ARCHIVE_PROTECTED_RECENT_COUNT
            ]:
                protected.add(str(archive["checkpointId"]))
        return protected

    def _remove_empty_checkpoint_archive_parents(self, start: Path) -> None:
        root = self.checkpoint_store_dir.resolve()
        current = start
        while True:
            try:
                resolved = current.resolve()
            except OSError:
                break
            if resolved == root or root not in resolved.parents:
                break
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

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

    def _read_apply_recovery_entries(self, limit: int = 1000) -> list[dict[str, Any]]:
        if not self.apply_recovery_log_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.apply_recovery_log_path.read_text(encoding="utf-8").splitlines()[-max(1, limit):]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("schema") == APPLY_RECOVERY_SCHEMA:
                entries.append(payload)
        return entries

    def _append_apply_recovery_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        payload = redact_sensitive(
            {
                "schema": APPLY_RECOVERY_SCHEMA,
                "updatedAt": now,
                **entry,
            }
        )
        if not payload.get("createdAt"):
            payload["createdAt"] = now
        self.apply_recovery_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.apply_recovery_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            flush_and_fsync(handle)
        return payload

    def _coalesced_apply_recoveries(self, *, include_resolved: bool = False) -> list[dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for entry in self._read_apply_recovery_entries(limit=2000):
            recovery_id = str(entry.get("id") or "").strip()
            if not recovery_id:
                continue
            previous = states.get(recovery_id, {})
            merged = {**previous, **entry}
            merged["blockingWrites"] = self._apply_recovery_blocks_writes(merged)
            states[recovery_id] = merged
        recoveries = list(states.values())
        if not include_resolved:
            recoveries = [recovery for recovery in recoveries if self._apply_recovery_blocks_writes(recovery)]
        return sorted(
            recoveries,
            key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
            reverse=True,
        )

    def _active_apply_recoveries(self) -> list[dict[str, Any]]:
        return self._coalesced_apply_recoveries(include_resolved=False)

    def _apply_recovery_blocks_writes(self, recovery: dict[str, Any]) -> bool:
        return str(recovery.get("status") or "") in APPLY_RECOVERY_ACTIVE_STATUSES

    def _select_apply_recovery(self, params: dict[str, Any], *, include_resolved: bool = False) -> dict[str, Any] | None:
        requested_id = str(
            params.get("recovery_id")
            or params.get("recoveryId")
            or params.get("id")
            or ""
        ).strip()
        checkpoint_id = str(params.get("checkpoint_id") or params.get("checkpointId") or "").strip()
        recoveries = self._coalesced_apply_recoveries(include_resolved=include_resolved)
        if requested_id:
            for recovery in recoveries:
                if recovery.get("id") == requested_id:
                    return recovery
            return None
        if checkpoint_id:
            for recovery in recoveries:
                if str(recovery.get("checkpointId") or "") == checkpoint_id:
                    return recovery
            return None
        return recoveries[0] if recoveries else None

    def _start_apply_recovery(
        self,
        approval: dict[str, Any],
        arguments: dict[str, Any],
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        target_tool = str(approval.get("targetTool") or "")
        recovery_id = f"recovery_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        error_text = " ".join(
            str(value or "")
            for value in (
                approval.get("reason"),
                checkpoint.get("error"),
                arguments.get("error"),
                arguments.get("message"),
            )
        )
        record = {
            "id": recovery_id,
            "status": "applying",
            "resolution": "",
            "createdAt": utc_now_iso(),
            "approvalId": str(approval.get("id") or ""),
            "targetTool": target_tool,
            "riskLevel": str(approval.get("riskLevel") or ""),
            "projectRoot": str(checkpoint.get("projectRoot") or arguments.get("projectRoot") or arguments.get("project_root") or ""),
            "avatarPath": str(arguments.get("avatarPath") or arguments.get("avatar_path") or ""),
            "checkpointId": str(checkpoint.get("id") or ""),
            "checkpoint": checkpoint,
            "argumentsSummary": summarize_params(arguments),
            "incidentKind": self._classify_apply_recovery_incident(error_text, target_tool),
            "restoreTool": "vrcforge_restore_checkpoint",
            "resolveTool": "vrcforge_resolve_interrupted_apply_recovery",
            "blockingWrites": True,
        }
        saved = self._append_apply_recovery_entry(record)
        self.append_audit({"event": "apply_recovery_started", "recovery": saved})
        return saved

    def _finish_apply_recovery(
        self,
        recovery: dict[str, Any],
        *,
        status: str,
        resolution: str,
        error: str = "",
        note: str = "",
        result_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = " ".join([str(error or ""), str(note or ""), str(recovery.get("targetTool") or "")])
        record: dict[str, Any] = {
            "id": str(recovery.get("id") or ""),
            "status": status,
            "resolution": resolution,
            "resolvedAt": utc_now_iso() if status not in APPLY_RECOVERY_ACTIVE_STATUSES else "",
            "approvalId": str(recovery.get("approvalId") or ""),
            "targetTool": str(recovery.get("targetTool") or ""),
            "projectRoot": str(recovery.get("projectRoot") or ""),
            "avatarPath": str(recovery.get("avatarPath") or ""),
            "checkpointId": str(recovery.get("checkpointId") or ""),
            "checkpoint": ensure_dict(recovery.get("checkpoint")),
            "incidentKind": self._classify_apply_recovery_incident(text, str(recovery.get("targetTool") or "")),
            "restoreTool": "vrcforge_restore_checkpoint",
            "resolveTool": "vrcforge_resolve_interrupted_apply_recovery",
            "blockingWrites": status in APPLY_RECOVERY_ACTIVE_STATUSES,
        }
        if error:
            record["error"] = error
        if note:
            record["note"] = note
        if result_summary is not None:
            record["resultSummary"] = result_summary
        saved = self._append_apply_recovery_entry(record)
        self.append_audit({"event": "apply_recovery_updated", "recovery": saved})
        return saved

    def _resolve_apply_recoveries_for_checkpoint(
        self,
        checkpoint_id: str,
        *,
        resolution: str,
        restore_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not checkpoint_id:
            return []
        resolved: list[dict[str, Any]] = []
        for recovery in self._active_apply_recoveries():
            if str(recovery.get("checkpointId") or "") != checkpoint_id:
                continue
            resolved.append(
                self._finish_apply_recovery(
                    recovery,
                    status="restored",
                    resolution=resolution,
                    result_summary=summarize_params(restore_payload or {}),
                )
            )
        return resolved

    def _classify_apply_recovery_incident(self, text: str, target_tool: str = "") -> str:
        lowered = f"{text or ''} {target_tool or ''}".lower()
        if any(token in lowered for token in ("timeout", "timed out", "hang", "hung", "not responding")):
            return "unity_timeout_or_hang"
        if any(token in lowered for token in ("crash", "crashed", "exited", "exit", "process died", "quit")):
            return "unity_process_exit"
        if any(token in lowered for token in ("modal", "dialog", "busy", "locked", "license")):
            return "unity_modal_or_busy"
        if any(token in lowered for token in ("mcp", "bridge", "connect", "disconnected", "unavailable", "offline")):
            return "unity_bridge_unavailable"
        if any(token in lowered for token in ("package", "manifest", "dependency", "compile", "compiler")):
            return "package_or_compile_conflict"
        return "write_interrupted"

    def _read_adjustment_checkpoint_entries(self) -> list[dict[str, Any]]:
        if not self.adjustment_checkpoint_log_path.exists():
            return []
        try:
            payload = json.loads(self.adjustment_checkpoint_log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw_entries = payload.get("checkpoints") if isinstance(payload, dict) else []
        if not isinstance(raw_entries, list):
            return []
        entries = [item for item in raw_entries if isinstance(item, dict)]
        return sorted(entries, key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)

    def _write_adjustment_checkpoint_entries(self, entries: list[dict[str, Any]]) -> None:
        self.adjustment_checkpoint_log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "vrcforge.adjustment_checkpoint_timeline.v1",
            "updatedAt": utc_now_iso(),
            "checkpoints": entries,
        }
        self.adjustment_checkpoint_log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_adjustment_checkpoint(self, entry_id: str) -> dict[str, Any] | None:
        if not entry_id:
            return None
        for entry in self._read_adjustment_checkpoint_entries():
            if entry.get("id") == entry_id:
                return entry
        return None

    def _normalize_adjustment_checkpoint_kind(self, value: Any, *, required: bool) -> str:
        kind = str(value or "").strip().lower().replace("_", "-")
        if kind in {"blendshape", "face-tuning", "facial", "face"}:
            return "face"
        if kind in {"material", "shader-material", "shader-tuning", "shader"}:
            return "shader"
        if required:
            raise AgentGatewayError("kind must be one of: face, shader.", status_code=400)
        return ""

    def _resolve_or_create_adjustment_base_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        checkpoint_id = str(params.get("checkpointId") or params.get("checkpoint_id") or "").strip()
        if checkpoint_id:
            checkpoint = self._load_checkpoint(checkpoint_id)
            if not checkpoint:
                return {"ok": False, "error": "checkpointId was not found."}
            return checkpoint
        target_tool = str(params.get("targetTool") or params.get("target_tool") or "vrcforge_manual_adjustment_checkpoint")
        if target_tool == "vrcforge_restore_checkpoint":
            return {"ok": False, "error": "restore checkpoints cannot be used as adjustment snapshots."}
        fake_approval = {"id": str(params.get("approvalId") or ""), "targetTool": target_tool}
        arguments = {
            "projectRoot": str(params.get("projectRoot") or params.get("project_root") or "").strip(),
            "avatarPath": str(params.get("avatarPath") or params.get("avatar_path") or "").strip(),
        }
        return self._create_pre_write_checkpoint(fake_approval, arguments) or {"ok": False, "error": "checkpoint creation was skipped."}

    def _build_adjustment_checkpoint_entry(
        self,
        params: dict[str, Any],
        checkpoint: dict[str, Any],
        *,
        kind: str,
        existing: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        entry = {
            "id": existing.get("id") or f"adj_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
            "schema": "vrcforge.adjustment_checkpoint.v1",
            "kind": kind,
            "createdAt": existing.get("createdAt") or now,
            "updatedAt": now,
            "checkpointId": str(checkpoint.get("id") or ""),
            "targetTool": str(checkpoint.get("targetTool") or params.get("targetTool") or params.get("target_tool") or ""),
            "projectRoot": str(params.get("projectRoot") or params.get("project_root") or checkpoint.get("projectRoot") or existing.get("projectRoot") or ""),
            "avatarPath": str(params.get("avatarPath") or params.get("avatar_path") or existing.get("avatarPath") or ""),
            "label": str(params.get("label") or existing.get("label") or self._default_adjustment_checkpoint_label(kind, checkpoint)),
            "description": str(params.get("description") if "description" in params else existing.get("description") or ""),
            "tags": self._normalize_tags(params.get("tags") if "tags" in params else existing.get("tags")),
            "compareGroup": str(params.get("compareGroup") or params.get("compare_group") or existing.get("compareGroup") or kind),
            "source": str(params.get("source") or existing.get("source") or "manual"),
            "checkpoint": {
                "id": checkpoint.get("id"),
                "status": checkpoint.get("status"),
                "ok": checkpoint.get("ok"),
                "strategy": checkpoint.get("strategy"),
                "createdAt": checkpoint.get("createdAt"),
                "targetTool": checkpoint.get("targetTool"),
            },
            "restoreTool": "vrcforge_restore_checkpoint",
            "manualCrud": {"create": True, "read": True, "update": True, "delete": True, "overwrite": True},
        }
        if existing.get("revisions"):
            entry["revisions"] = ensure_list(existing.get("revisions"))
            entry["overwriteCount"] = int(existing.get("overwriteCount") or len(entry["revisions"]))
        return entry

    def _apply_adjustment_checkpoint_metadata(self, entry: dict[str, Any], params: dict[str, Any]) -> None:
        for source_key, target_key in (
            ("label", "label"),
            ("description", "description"),
            ("avatarPath", "avatarPath"),
            ("avatar_path", "avatarPath"),
            ("projectRoot", "projectRoot"),
            ("project_root", "projectRoot"),
            ("compareGroup", "compareGroup"),
            ("compare_group", "compareGroup"),
        ):
            if source_key in params:
                entry[target_key] = str(params.get(source_key) or "")
        if "tags" in params:
            entry["tags"] = self._normalize_tags(params.get("tags"))

    def _normalize_tags(self, value: Any) -> list[str]:
        if isinstance(value, list):
            raw = value
        elif isinstance(value, str):
            raw = re.split(r"[,;\s]+", value)
        else:
            raw = []
        tags: list[str] = []
        for item in raw:
            tag = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(item or "").strip()).strip("-")
            if tag and tag not in tags:
                tags.append(tag[:48])
        return tags[:24]

    def _default_adjustment_checkpoint_label(self, kind: str, checkpoint: dict[str, Any]) -> str:
        target_tool = str(checkpoint.get("targetTool") or "")
        prefix = "Face" if kind == "face" else "Shader"
        if target_tool:
            return f"{prefix} checkpoint before {target_tool}"
        return f"{prefix} checkpoint"

    def _maybe_record_adjustment_checkpoint(self, record: dict[str, Any]) -> None:
        target_tool = str(record.get("targetTool") or "")
        kind = ADJUSTMENT_CHECKPOINT_TARGETS.get(target_tool)
        if not kind or not record.get("ok") or not record.get("id"):
            return
        entries = self._read_adjustment_checkpoint_entries()
        checkpoint_id = str(record.get("id") or "")
        if any(entry.get("checkpointId") == checkpoint_id for entry in entries):
            return
        entry = self._build_adjustment_checkpoint_entry(
            {"source": "automatic", "projectRoot": record.get("projectRoot") or ""},
            record,
            kind=kind,
            existing={},
        )
        entry["source"] = "automatic"
        entries.insert(0, entry)
        self._write_adjustment_checkpoint_entries(entries)

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
            if handler.name in WRAPPER_ONLY_WRITE_TARGETS:
                continue
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
    def runtime_run_log_path(self) -> Path:
        return self.audit_dir / "runtime-runs.jsonl"

    @property
    def checkpoint_log_path(self) -> Path:
        return self.audit_dir / "checkpoints.jsonl"

    @property
    def adjustment_checkpoint_log_path(self) -> Path:
        return self.audit_dir / "adjustment-checkpoints.json"

    @property
    def apply_recovery_log_path(self) -> Path:
        return self.audit_dir / "apply-recoveries.jsonl"

    @property
    def checkpoint_store_dir(self) -> Path:
        override = getattr(self, "_checkpoint_store_override", None)
        if override is not None:
            return override
        return self.audit_dir / "checkpoint-archives"

    @property
    def default_checkpoint_store_dir(self) -> Path:
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
                "rollbackPolicy": self._write_handler_rollback_policy(handler),
            }
            for handler in self._write_handlers.values()
            if self._write_handler_visible(handler, config) and handler.name not in WRAPPER_ONLY_WRITE_TARGETS
        ]

    def _write_handler_rollback_policy(self, handler: AgentWriteHandler) -> dict[str, Any]:
        if handler.name == "vrcforge_restore_checkpoint":
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "checkpoint_restore",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [*UNITY_PROJECT_CHECKPOINT_SCOPE, *LOCAL_STATE_CHECKPOINT_SCOPE],
                "restoreTool": "vrcforge_restore_checkpoint",
                "coverageAudit": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
                "postRestoreValidationRequired": True,
                "note": "Restores a previously captured Unity project or VRCForge local-state checkpoint.",
            }
        if handler.name == "vrcforge_resolve_interrupted_apply_recovery":
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "interrupted_apply_recovery_resolution",
                "approvalRequired": True,
                "preWriteCheckpointRequired": False,
                "checkpointScope": [],
                "restoreTool": "vrcforge_restore_checkpoint",
                "coverageAudit": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
                "recoveryLedger": APPLY_RECOVERY_SCHEMA,
                "postRestoreValidationRequired": False,
                "note": "Marks a persisted interrupted-write recovery as manually resolved after the user confirms the Unity project state was handled.",
            }
        if handler.name in LOCAL_STATE_CHECKPOINT_TARGETS:
            return {
                "schema": ROLLBACK_POLICY_SCHEMA,
                "required": True,
                "kind": "local_state_archive",
                "approvalRequired": True,
                "preWriteCheckpointRequired": True,
                "checkpointScope": list(LOCAL_STATE_CHECKPOINT_SCOPE),
                "restoreTool": "vrcforge_restore_checkpoint",
                "coverageAudit": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
                "stateRoots": ["VRCForge skill package store", "projected user skills"],
                "postRestoreValidationRequired": True,
                "note": "Community skill package writes are checkpointed as VRCForge local app state before mutation.",
            }
        return {
            "schema": ROLLBACK_POLICY_SCHEMA,
            "required": True,
            "kind": "unity_project_checkpoint",
            "approvalRequired": True,
            "preWriteCheckpointRequired": True,
            "checkpointScope": list(UNITY_PROJECT_CHECKPOINT_SCOPE),
            "restoreTool": "vrcforge_restore_checkpoint",
            "coverageAudit": ROLLBACK_COVERAGE_AUDIT_SCHEMA,
            "postRestoreValidationRequired": True,
            "generatedResidueAuditRequired": True,
            "ecosystemCoverageRequired": ["Modular Avatar", "VRCFury", "NDMF", "MA2BT-Pro"],
            "note": "Every Unity project write must be restorable through the approval-time checkpoint boundary.",
        }

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

    def _append_runtime_run(self, entry: dict[str, Any]) -> None:
        safe_entry = redact_sensitive(
            {
                "schema": "vrcforge.runtime_run.v1",
                "id": f"runevt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
                "createdAt": utc_now_iso(),
                "updatedAt": utc_now_iso(),
                **entry,
            }
        )
        self.runtime_run_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.runtime_run_log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(safe_entry, ensure_ascii=False, sort_keys=True) + "\n")

    def _append_jsonl(self, path: Path, schema: str, entry: dict[str, Any]) -> dict[str, Any]:
        safe_entry = redact_sensitive(
            {
                "schema": schema,
                "id": f"evt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
                "createdAt": utc_now_iso(),
                "updatedAt": utc_now_iso(),
                **entry,
            }
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(safe_entry, ensure_ascii=False, sort_keys=True) + "\n")
        return safe_entry

    def _read_jsonl(self, path: Path, *, limit: int = 500) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        for line in lines[-max(1, min(limit, 5000)) :]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def _project_agent_goals(self) -> dict[str, dict[str, Any]]:
        goals: dict[str, dict[str, Any]] = {}
        for event in self._read_jsonl(self.agent_goal_log_path, limit=2000):
            goal_id = str(event.get("goalId") or "").strip()
            if not goal_id:
                continue
            previous = goals.get(goal_id, {})
            merged = {
                **previous,
                **event,
                "id": goal_id,
                "goalId": goal_id,
                "createdAt": previous.get("createdAt") or event.get("createdAt"),
                "updatedAt": event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt"),
            }
            if event.get("title"):
                merged["title"] = event.get("title")
            goals[goal_id] = merged
        return goals

    def _project_agent_memory(self, *, include_deleted: bool = False) -> dict[str, dict[str, Any]]:
        memories: dict[str, dict[str, Any]] = {}
        deleted: set[str] = set()
        for event in self._read_jsonl(self.agent_memory_log_path, limit=4000):
            memory_id = str(event.get("memoryId") or "").strip()
            if not memory_id:
                continue
            if str(event.get("status") or "") == "deleted" or event.get("event") == "memory_deleted":
                deleted.add(memory_id)
            previous = memories.get(memory_id, {})
            merged = {
                **previous,
                **event,
                "id": memory_id,
                "memoryId": memory_id,
                "createdAt": previous.get("createdAt") or event.get("createdAt"),
                "updatedAt": event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt"),
            }
            memories[memory_id] = merged
        if include_deleted:
            return memories
        return {memory_id: memory for memory_id, memory in memories.items() if memory_id not in deleted}

    def _read_runtime_run_events(self, *, limit: int = 400) -> list[dict[str, Any]]:
        if not self.runtime_run_log_path.exists():
            return []
        try:
            lines = self.runtime_run_log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        for line in lines[-max(1, min(limit, 2000)):]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

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
        return self._with_user_constraints(params, snapshot, include_content=False, append_instruction=False)

    def _with_user_constraints(
        self,
        params: dict[str, Any],
        snapshot: UserConstraintsSnapshot,
        *,
        include_content: bool | None = None,
        append_instruction: bool = True,
    ) -> dict[str, Any]:
        enriched = dict(params)
        if include_content is None:
            include_content = len(snapshot.content) <= USER_CONSTRAINTS_INLINE_CHARACTER_LIMIT
        enriched["_vrcforge_user_constraints"] = self._user_constraints_payload(
            snapshot,
            include_content=include_content,
        )
        if include_content:
            enriched.setdefault("user_constraints", snapshot.content)
            enriched.setdefault("userConstraints", snapshot.content)
        instruction = enriched.get("instruction")
        constraints_block = self._user_constraints_instruction_block(snapshot, include_content=include_content)
        if not append_instruction:
            return enriched
        if isinstance(instruction, str) and instruction.strip():
            if constraints_block.strip() not in instruction:
                enriched["instruction"] = instruction.rstrip() + constraints_block
        elif "instruction" in enriched or any(
            key in enriched for key in ("avatar", "avatar_path", "avatarPath", "inventory", "changes", "adjustments")
        ):
            enriched["instruction"] = "Follow the user constraints below." + constraints_block
        return enriched

    def _user_constraints_payload(
        self,
        snapshot: UserConstraintsSnapshot,
        *,
        include_content: bool,
    ) -> dict[str, Any]:
        content_hash = hashlib.sha256(snapshot.content.encode("utf-8")).hexdigest() if snapshot.content else ""
        preview = snapshot.content[:USER_CONSTRAINTS_PREVIEW_CHARACTER_LIMIT]
        payload: dict[str, Any] = {
            "source": "user_agents_md",
            "path": str(snapshot.path),
            "contentHash": content_hash,
            "contentLength": len(snapshot.content),
            "contentPreview": preview,
            "contentInline": bool(include_content),
        }
        if include_content:
            payload["content"] = snapshot.content
        else:
            payload["contentRedacted"] = True
        return payload

    def _user_constraints_instruction_block(
        self,
        snapshot: UserConstraintsSnapshot,
        *,
        include_content: bool,
    ) -> str:
        if include_content:
            return (
                "\n\nUser constraints from %LOCALAPPDATA%\\VRCForge\\agentic-app\\AGENTS.md:\n"
                f"{snapshot.content}"
            )
        content_hash = hashlib.sha256(snapshot.content.encode("utf-8")).hexdigest() if snapshot.content else ""
        return (
            "\n\nUser constraints are active in %LOCALAPPDATA%\\VRCForge\\agentic-app\\AGENTS.md "
            f"(sha256={content_hash}, characters={len(snapshot.content)}). "
            "The full text is kept out of tool parameters to avoid oversized Unity/MCP command lines."
        )

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

    def _shell_auto_manual_approval_reason(self, classification: dict[str, Any]) -> str:
        command = str(classification.get("command") or "")
        tokens = [strip_quotes(token).lower() for token in tokenize_command(command)]
        if any(token in AUTO_APPROVAL_MANUAL_SHELL_COMMANDS for token in tokens):
            return "Delete/removal shell commands require manual approval in Auto Approve mode."
        reasons = " ".join(str(reason or "").lower() for reason in ensure_list(classification.get("reasons")))
        if "outside the workspace root" in reasons or "parent path traversal" in reasons:
            return "Shell commands that reference paths outside the workspace require manual approval in Auto Approve mode."
        return ""

    def _write_auto_manual_approval_reason(self, target_tool: str, arguments: dict[str, Any], preview: Any = None) -> str:
        target_lower = str(target_tool or "").lower()
        if any(token in target_lower for token in AUTO_APPROVAL_MANUAL_WRITE_TOKENS):
            return "Delete, remove, restore, reset, or uninstall write requests require manual approval in Auto Approve mode."

        for key, value in iter_param_leaf_values(arguments):
            key_lower = key.lower()
            text_value = str(value or "").strip()
            value_lower = text_value.lower()
            if any(token in key_lower for token in AUTO_APPROVAL_MANUAL_WRITE_TOKENS) and value not in {False, None, "", 0, "false", "False"}:
                return "Delete, remove, restore, reset, or uninstall write requests require manual approval in Auto Approve mode."
            if key_lower.split(".")[-1] in {"action", "operation", "mode"} and value_lower in AUTO_APPROVAL_MANUAL_WRITE_TOKENS:
                return "Delete, remove, restore, reset, or uninstall write requests require manual approval in Auto Approve mode."

        project_root = extract_project_root(arguments)
        if project_root:
            for key, value in iter_param_leaf_values(arguments):
                key_lower = key.lower()
                if not any(marker in key_lower for marker in WRITE_PATH_KEY_MARKERS):
                    continue
                if key_lower.endswith("projectroot") or key_lower.endswith("project_root") or key_lower.endswith("projectpath"):
                    continue
                text_value = str(value or "").strip()
                if looks_like_absolute_path(text_value) and not is_path_within(Path(text_value), project_root):
                    return "Write requests that reference paths outside the selected project require manual approval in Auto Approve mode."

        if isinstance(preview, dict):
            preview_root = project_root or extract_project_root(preview)
            if preview_root:
                for key, value in iter_param_leaf_values(preview):
                    key_lower = key.lower()
                    if not any(marker in key_lower for marker in WRITE_PATH_KEY_MARKERS):
                        continue
                    text_value = str(value or "").strip()
                    if looks_like_absolute_path(text_value) and not is_path_within(Path(text_value), preview_root):
                        return "Write requests that reference paths outside the selected project require manual approval in Auto Approve mode."
        return ""

    def _low_risk_reasons(self, command_name: str, args: list[str], workspace_root: Path) -> list[str]:
        read_only = {"get-childitem", "dir", "ls", "get-content", "type", "rg", "findstr"}
        if command_name in read_only:
            if self._read_command_args_are_low_risk(command_name, args, workspace_root):
                return ["Read-only workspace inspection command."]
            return []

        if command_name in {"python", "node", "npm", "uv"} and args in (["--version"], ["-v"]):
            return ["Read-only environment version probe."]

        if command_name == "where" and len(args) == 1 and re.fullmatch(r"[a-zA-Z0-9_.-]+", args[0] or ""):
            return ["Read-only executable lookup."]

        if command_name == "git":
            return self._git_low_risk_reasons(args, workspace_root)

        return []

    def _read_command_args_are_low_risk(self, command_name: str, args: list[str], workspace_root: Path) -> bool:
        if command_name == "rg":
            for arg in args:
                lowered = arg.lower()
                if lowered == "--pre" or lowered.startswith("--pre="):
                    return False
                if lowered == "--pre-glob" or lowered.startswith("--pre-glob="):
                    return False
        return self._args_stay_in_workspace(args, workspace_root)

    def _args_stay_in_workspace(self, args: list[str], workspace_root: Path) -> bool:
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if not arg or arg.startswith("-"):
                if arg in {"--pre", "--pre-glob", "--output"}:
                    return False
                if arg in {"--glob", "-g", "--pathspec-from-file"}:
                    skip_next = True
                continue
            cleaned = strip_quotes(arg)
            if cleaned in {".", "*"}:
                continue
            lowered = cleaned.lower()
            if lowered.startswith(("~", "$", "%userprofile%", "%home%")):
                return False
            if cleaned.startswith(("/", "\\")) and not cleaned.startswith(("./", ".\\", "../", "..\\")):
                return False
            if ".." in re.split(r"[\\/]+", cleaned):
                return False
            if looks_like_absolute_path(cleaned) and not is_path_within(Path(cleaned), workspace_root):
                return False
            if any(separator in cleaned for separator in ("/", "\\")):
                candidate = Path(cleaned)
                if not candidate.is_absolute():
                    candidate = workspace_root / cleaned
                if not is_path_within(candidate, workspace_root):
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
        if verb == "show" and self._git_show_args_are_low_risk(rest, workspace_root):
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

    def _git_show_args_are_low_risk(self, args: list[str], workspace_root: Path) -> bool:
        if "--stat" not in args:
            return False
        if any(arg == "--ext-diff" or arg.startswith("--output") or arg == "--output" for arg in args):
            return False
        allowed_flags = {"--stat", "--no-ext-diff"}
        if "--" in args:
            split_index = args.index("--")
            before_paths = args[:split_index]
            path_args = args[split_index + 1 :]
        else:
            before_paths = args
            path_args = []
        for arg in before_paths:
            if arg in allowed_flags:
                continue
            if arg.startswith("-"):
                return False
            if any(separator in arg for separator in ("/", "\\")) and not self._args_stay_in_workspace([arg], workspace_root):
                return False
        return self._args_stay_in_workspace(path_args, workspace_root) if path_args else True

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
        auto_manual_reason = ""
        if normalize_execution_mode(self.ensure_config().execution_mode) == "auto":
            auto_manual_reason = self._shell_auto_manual_approval_reason(classification)
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
            requires_explicit_approval=bool(auto_manual_reason),
            explicit_approval_reason=auto_manual_reason,
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

    def _run_shell_command(
        self,
        command: str,
        cwd: Path,
        timeout_seconds: int = 120,
        cancel_ids: list[str] | None = None,
    ) -> dict[str, Any]:
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
        cancelled = False
        deadline = time.monotonic() + max(1, min(timeout_seconds, 600))
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel_ids and self._runtime_cancel_requested(
                    session_id=cancel_ids[0] if len(cancel_ids) > 0 else "",
                    turn_id=cancel_ids[1] if len(cancel_ids) > 1 else "",
                    client_turn_id=cancel_ids[2] if len(cancel_ids) > 2 else "",
                ):
                    cancelled = True
                    kill_process_tree(process)
                    stdout, stderr = process.communicate()
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    kill_process_tree(process)
                    stdout, stderr = process.communicate()
                    break

        duration = time.monotonic() - started
        exit_code = process.returncode if process.returncode is not None else -1
        return {
            "ok": exit_code == 0 and not timed_out and not cancelled,
            "command": command,
            "cwd": str(cwd),
            "exitCode": exit_code,
            "timedOut": timed_out,
            "cancelled": cancelled,
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
        loop_state: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        loop_state = loop_state or []
        local_plan = self._local_plan_agent_turn(message, params, observe, loop_state)
        # 关键词命中（明确的技能/命令/写入意图）直接走确定性路径：快、稳定、可测试。
        if (
            local_plan.get("shellNeeded")
            or local_plan.get("skillNeeded")
            or local_plan.get("writeNeeded")
        ):
            return local_plan
        # 确定性兜底已经给出明确的终止答复（例如「多个模型让用户选」「没找到模型」），
        # 这是确定结论，不交给 LLM 再编一遍。
        if local_plan.get("deterministicTerminal"):
            return local_plan
        # 本地规划没认出意图时，尝试 LLM 规划。
        llm_plan = self._llm_plan_agent_turn(message, observe, history or [], loop_state)
        if llm_plan is not None:
            return llm_plan
        # 走到这里：确定性兜底没认出意图，LLM 也没产出可执行规划。
        # 注意——生产里 llm_plan_fn 始终挂着 wrapper：没连 Provider / API Key 缺失 /
        # provider 报错时，wrapper 会 raise，被 _llm_plan_agent_turn 吞掉返回 None。
        # 所以这里不能只在 `llm_plan_fn is None` 时才诚实，否则会退回那个看似
        # 「已规划」却什么都没干的空兜底（正是 A5 要砍的「做了做了」假象）。
        # 统一走诚实终止：明确告知「这条没法自动规划」。
        return self._disconnected_local_plan(local_plan)

    def _disconnected_local_plan(self, local_plan: dict[str, Any]) -> dict[str, Any]:
        plan = dict(local_plan)
        plan.update(
            {
                "summary": "No actionable plan: deterministic fallback missed and the model planner produced nothing.",
                "reply": (
                    "这条我没法自动规划——通常是还没接上可用的模型 Provider"
                    "（或 API Key 没配 / provider 暂时不可用）。"
                    "你可以在设置里连一个供应商；或者给我更明确的指令——"
                    "比如「检查 Unity 状态」「列出模型」「往模型里加个对象」，我就能直接动手。"
                ),
                "planner": "deterministic-local",
                "plannerLabel": "",
                "deterministicTerminal": True,
                "providerConnected": False,
                "continueLoop": False,
                "nextStep": "done",
            }
        )
        return plan

    def _local_plan_agent_turn(
        self,
        message: str,
        params: dict[str, Any],
        observe: dict[str, Any],
        loop_state: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        loop_state = loop_state or []
        constraints_applied = bool(observe.get("userConstraints", {}).get("enabled"))
        command = extract_shell_command_candidate(message, params)
        meta_plan = self._plan_runtime_meta_question(message, constraints_applied, params)
        if meta_plan is not None:
            return meta_plan
        # 写入意图（往模型里加对象/新建/创建）优先：先扫描→单模型自动选中→发起写入审批，
        # 而不是反问「加到哪个模型上」或只回一句「做了做了」。
        if not command:
            write_plan = self._plan_write_intent(message, params, loop_state, constraints_applied)
            if write_plan is not None:
                return write_plan
        skill_route = self._match_runtime_skill(message, params) if not command else None
        summary = "Observed runtime state and prepared the next action."
        if command:
            summary = "Prepared a shell step for the requested task."
        elif skill_route:
            summary = f"Prepared {skill_route['tool']} skill call."
        elif "health" in message.lower() or "健康" in message:
            summary = "Observed runtime health. No shell step is required."
        plan = {
            "summary": summary,
            "reply": "",
            "planner": "deterministic-local",
            "plannerLabel": "",
            "userConstraintsApplied": constraints_applied,
            "shellNeeded": bool(command),
            "shellCommand": command,
            "skillNeeded": bool(skill_route),
            "skillTool": skill_route.get("tool") if skill_route else "",
            "skillCategory": skill_route.get("category") if skill_route else "",
            "skillParams": skill_route.get("params") if skill_route else {},
            "skillReason": skill_route.get("reason") if skill_route else "",
            "writeNeeded": False,
            "writeTool": "",
            "writeParams": {},
            # 单次读技能/命令即可满足请求时，turn 到此完成，不再无谓地多跑一圈。
            "continueLoop": False,
            "expectedResult": "Shell output will be returned inline." if command else "Runtime observation is available.",
            "nextStep": "classify_shell" if command else "call_skill" if skill_route else "await_user_instruction",
        }
        return plan

    def _plan_runtime_meta_question(
        self,
        message: str,
        constraints_applied: bool,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        text = str(message or "").strip()
        lowered = text.lower()
        asks_provider_or_model = has_any(
            lowered,
            text,
            [
                "provider",
                "model",
                "which model",
                "what model",
                "model name",
                "provider name",
                "供应商",
                "厂商",
                "模型",
                "模型名",
            ],
        )
        asks_current_or_previous = has_any(
            lowered,
            text,
            [
                "used",
                "using",
                "this response",
                "last response",
                "previous response",
                "current",
                "上一条",
                "上条",
                "刚才",
                "这次",
                "当前",
                "用了",
                "使用",
            ],
        )
        asks_catalog = has_any(
            lowered,
            text,
            [
                "available models",
                "list models",
                "model list",
                "可用模型",
                "模型列表",
                "列出模型",
            ],
        )
        if not asks_provider_or_model or not asks_current_or_previous or asks_catalog:
            return None

        params = params or {}
        provider_label = str(params.get("providerLabel") or params.get("provider_label") or params.get("provider") or "").strip()
        model = str(params.get("model") or "").strip()
        label = f"{provider_label} · {model}" if provider_label and model else provider_label or model or str(self.llm_planner_label or "").strip()
        if label:
            reply = f"上一条使用的是 {label}。"
            summary = "Answered the provider/model follow-up from runtime metadata."
        else:
            reply = "当前还没有可确认的模型调用记录。"
            summary = "No confirmed provider/model metadata is available yet."
        return {
            "summary": summary,
            "reply": reply,
            "planner": "deterministic-local",
            "plannerLabel": label,
            "userConstraintsApplied": constraints_applied,
            "shellNeeded": False,
            "shellCommand": "",
            "skillNeeded": False,
            "skillTool": "",
            "skillCategory": "",
            "skillParams": {},
            "skillReason": "",
            "writeNeeded": False,
            "writeTool": "",
            "writeParams": {},
            "deterministicTerminal": True,
            "continueLoop": False,
            "expectedResult": "Runtime provider/model metadata is returned inline.",
            "nextStep": "done",
        }

    # ------------------------------------------------------------------
    # 写入意图：扫描 → 单模型自动解析 → 发起写入审批
    # ------------------------------------------------------------------

    def _plan_write_intent(
        self,
        message: str,
        params: dict[str, Any],
        loop_state: list[dict[str, Any]],
        constraints_applied: bool,
    ) -> dict[str, Any] | None:
        intent = detect_avatar_write_intent(message)
        if not intent:
            return None

        def _base(**overrides: Any) -> dict[str, Any]:
            plan = {
                "summary": "",
                "reply": "",
                "planner": "deterministic-local",
                "plannerLabel": "",
                "userConstraintsApplied": constraints_applied,
                "shellNeeded": False,
                "shellCommand": "",
                "skillNeeded": False,
                "skillTool": "",
                "skillCategory": "",
                "skillParams": {},
                "writeNeeded": False,
                "writeTool": "",
                "writeParams": {},
                "writeIntent": intent.get("kind"),
                "continueLoop": False,
                "expectedResult": "",
                "nextStep": "await_user_instruction",
            }
            plan.update(overrides)
            return plan

        # 1) 用户已显式给出目标模型/对象路径 → 直接发起写入审批。
        explicit_target = str(
            params.get("avatar_path")
            or params.get("avatarPath")
            or intent.get("target")
            or ""
        ).strip()

        # 2) 否则从 loop_state 里找已扫描到的模型列表。
        scanned = self._avatars_from_loop_state(loop_state)
        already_scanned = scanned is not None

        if not explicit_target and not already_scanned:
            # 先扫描：调用只读的 vrcforge_list_avatars，结果回灌后再决定下一步。
            route = self._runtime_skill_route(
                "vrcforge_list_avatars", dict(params), "avatar write intent: scan first"
            )
            return _base(
                summary="Scanning the open project for avatars before the requested write.",
                reply="先扫描一下当前工程里有哪些模型，再决定往哪个上面加。",
                skillNeeded=True,
                skillTool=route.get("tool") or "vrcforge_list_avatars",
                skillCategory=route.get("category") or "",
                skillParams=route.get("params") or {},
                skillReason="avatar write intent: scan first",
                continueLoop=True,
                expectedResult="Avatar list will be returned and re-planned against.",
                nextStep="call_skill",
            )

        target = explicit_target
        if not target and already_scanned:
            avatars = scanned or []
            if len(avatars) == 0:
                return _base(
                    summary="No avatar was found in the open project.",
                    reply="扫了一圈，当前工程里没有可写入的模型。请先在 Unity 里打开带模型的场景，或告诉我模型路径。",
                    deterministicTerminal=True,
                    nextStep="done",
                )
            if len(avatars) > 1:
                listed = "\n".join(f"- {path}" for path in avatars[:12])
                return _base(
                    summary="Multiple avatars found; need the user to choose one.",
                    reply=f"工程里有多个模型，告诉我加到哪个上面：\n{listed}",
                    deterministicTerminal=True,
                    nextStep="done",
                )
            # 恰好一个模型 → 自动选中，不反问。
            target = avatars[0]

        write_params = self._build_avatar_write_params(intent, target, params)
        return _base(
            summary=f"Prepared a supervised Unity write on {target}.",
            reply=(
                f"工程里只有 {target} 这一个模型，直接选它。"
                f"我来发起一个加对象的写入请求，走审批/检查点后再真正落地。"
            ),
            writeNeeded=True,
            writeTool="vrcforge_create_gameobject",
            writeParams=write_params,
            resolvedAvatar=target,
            continueLoop=False,
            expectedResult="A supervised write approval will be created.",
            nextStep="request_write",
        )

    def _avatars_from_loop_state(self, loop_state: list[dict[str, Any]]) -> list[str] | None:
        """Return avatar paths from the most recent list_avatars step, or None if not scanned yet."""
        for step in reversed(loop_state):
            if not isinstance(step, dict):
                continue
            if str(step.get("tool") or "") != "vrcforge_list_avatars":
                continue
            if step.get("status") not in (None, "executed", "ok"):
                # 扫描失败：当作「已尝试但拿不到」，避免无限重扫。
                return []
            return extract_avatar_paths(step.get("result"))
        return None

    def _build_avatar_write_params(
        self,
        intent: dict[str, Any],
        target: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        object_name = str(intent.get("objectName") or "GameObject").strip() or "GameObject"
        # Use the concrete static GameObject primitive. Approved execution maps
        # this to Unity MCP `vrc_create_gameobject`; no dynamic C#/Roslyn path is involved.
        request = {
            "name": object_name,
            "parentPath": target,
            "preview": False,
            "writeIntent": intent.get("kind"),
            "targetAvatar": target,
        }
        for key in (
            "projectPath",
            "project_path",
            "projectRoot",
            "project_root",
            "unityHost",
            "unity_host",
            "unityPort",
            "unity_port",
        ):
            if params.get(key) not in (None, ""):
                request[key] = params.get(key)
        return request

    def _llm_plan_agent_turn(
        self,
        message: str,
        observe: dict[str, Any],
        history: list[dict[str, Any]],
        loop_state: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        plan_fn = self.llm_plan_fn
        if plan_fn is None:
            return None
        try:
            prompt = self._build_llm_plan_prompt(self._message_with_runtime_context(message, observe), history, loop_state or [], observe=observe)
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
            "writeNeeded": False,
            "writeTool": "",
            "writeParams": {},
            # 工具型动作执行后，把结果回灌给 LLM 再决定下一步（真正的多步循环）。
            "continueLoop": False,
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
                    "continueLoop": True,
                    "expectedResult": "Skill output will be returned inline.",
                    "nextStep": "call_skill",
                }
        if action == "shell" and shell_command:
            return {
                **base,
                "summary": summary or "Prepared a shell step for the requested task.",
                "shellNeeded": True,
                "shellCommand": shell_command,
                "continueLoop": True,
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

    def _message_with_runtime_context(self, message: str, observe: dict[str, Any]) -> str:
        lines = [message]
        attachments = ensure_list((observe.get("turn") or {}).get("attachments"))
        if attachments:
            lines.append("\nCurrent attachments:")
            for attachment in attachments[:RUNTIME_ATTACHMENT_MAX_ITEMS]:
                if not isinstance(attachment, dict):
                    continue
                name = summarize_text(str(attachment.get("name") or "attachment"), 120)
                kind = str(attachment.get("payloadKind") or "metadata")
                if attachment.get("text"):
                    lines.append(f"- {name} (text): {summarize_text(str(attachment.get('text') or ''), 1200)}")
                else:
                    lines.append(f"- {name} ({kind}, {attachment.get('type') or 'file'}, {attachment.get('size') or 0} bytes)")
        memories = ensure_list(ensure_dict(observe.get("memory")).get("items"))
        if memories:
            lines.append("\nExplicit memory (user-visible and user-clearable):")
            for memory in memories[:12]:
                if isinstance(memory, dict) and memory.get("text"):
                    lines.append(f"- [{memory.get('scope')}/{memory.get('kind')}] {summarize_text(str(memory.get('text')), 500)}")
        goals = ensure_list(ensure_dict(observe.get("goals")).get("items"))
        if goals:
            lines.append("\nLong-running goals:")
            for goal in goals[:8]:
                if isinstance(goal, dict) and goal.get("title"):
                    lines.append(f"- [{goal.get('status')}] {summarize_text(str(goal.get('title')), 240)} {summarize_text(str(goal.get('summary') or ''), 360)}")
        return "\n".join(lines)

    def _build_llm_plan_prompt(
        self,
        message: str,
        history: list[dict[str, Any]],
        loop_state: list[dict[str, Any]] | None = None,
        observe: dict[str, Any] | None = None,
    ) -> str:
        observe = observe or {}
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
        step_lines: list[str] = []
        for index, step in enumerate(loop_state or [], start=1):
            if not isinstance(step, dict):
                continue
            label = str(step.get("tool") or step.get("kind") or "step")
            status = str(step.get("status") or "")
            result_text = summarize_text(json.dumps(step.get("result"), ensure_ascii=False, default=str), 600) if step.get("result") is not None else ""
            line = f"{index}. {label}"
            if status:
                line += f"（{status}）"
            if result_text:
                line += f" → {result_text}"
            step_lines.append(line)
        steps_block = "\n".join(step_lines) if step_lines else "（本轮尚未执行任何工具）"
        return (
            "你是 VRCForge 桌面智能体的规划器，负责把用户的中文/英文请求转换成下一步动作。\n"
            "这是一个多步循环：你每次只产出一个动作；工具执行后结果会回灌给你，由你决定下一步，"
            "直到信息足够后再用 reply 收尾。\n"
            "可选动作：\n"
            '1. 调用工具：{"action": "skill", "skill_tool": "<工具名>", "skill_params": {…}, "summary": "<一句话说明>", "reply": "<对用户说的话>"}\n'
            '2. 执行 PowerShell 命令（系统级问题，如看日志/查文件/git）：{"action": "shell", "shell_command": "<命令>", "summary": "<一句话说明>", "reply": "<对用户说的话>"}\n'
            '3. 直接回答（闲聊、解释、当前信息已足够、或要收尾）：{"action": "reply", "reply": "<中文回答>"}\n'
            "规则：只返回一个 JSON 对象，不要 Markdown 代码块外的文字；工具名必须严格来自下面的列表；"
            "写操作类工具会进入审批流程，可以放心规划；"
            "如果『已执行步骤』里某个工具刚刚已经给出了你需要的结果，不要重复调用同一个工具——改为基于结果继续下一步或 reply 收尾；"
            # 自纠回环（对标 Codex/OpenClaw 的 tool-error recovery）：失败要读错误、修正后重试或换路，绝不假装成功。
            "如果『已执行步骤』里某一步失败或报错（status 是 failed/error，或结果里带 error/异常/traceback）："
            "先读懂错误原因；能靠改参数解决就用『不同的参数』重试（不要原样重复同一个调用），"
            "换个工具或思路能绕过就绕过；确实做不到时用 reply 如实说明卡在哪、需要用户补什么——"
            "绝不能在没真正做完时假装已完成（严禁「做了做了」式的虚假收尾）；"
            "拿不准时选 reply 并说明你需要什么信息。\n"
            "reply 字段是直接展示给用户的对话内容：用第一人称中文，自然地说明你理解了什么、打算怎么做（例如「好的，我去看一下 D 盘根目录有什么」），不要复述 JSON 或工具名。\n\n"
            f"可用工具列表：\n{chr(10).join(tool_lines)}\n\n"
            f"最近对话：\n{history_block}\n\n"
            f"本轮已执行步骤+结果：\n{steps_block}\n\n"
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

        if "skills" in lowered and (
            "list" in lowered
            or "show" in lowered
            or "available" in lowered
            or "what" in lowered
            or "which" in lowered
            or "列" in text
            or "鍒" in text
        ):
            return self._runtime_skill_route("vrcforge_skill_manifest", skill_params, "skill manifest")

        if has_any(lowered, text, ["roslyn"]) and has_any(lowered, text, ["status", "diagnostic", "状态", "诊断", "检查"]):
            return self._runtime_skill_route("vrcforge_roslyn_status", skill_params, "roslyn status")
        if has_any(lowered, text, ["screenshot", "capture", "截图", "拍照", "截屏"]):
            return self._runtime_skill_route("vrcforge_capture_screenshot", skill_params, "screenshot capture")
        if has_any(lowered, text, ["gesture", "play mode", "game view", "捕获状态", "截图状态"]):
            return self._runtime_skill_route("vrcforge_capture_status", skill_params, "capture status")
        if has_any(lowered, text, ["skill", "skills", "能力库"]):
            if has_any(lowered, text, ["check", "validate", "validation", "inspect"]):
                return self._runtime_skill_route("vrcforge_skill_check", skill_params, "skill registry check")
            if has_any(
                lowered,
                text,
                [
                    "available",
                    "manifest",
                    "list",
                    "show",
                    "what tools",
                    "which tools",
                    "tool list",
                    "skill list",
                    "鍒椾竴",
                    "鍒椾竴涓",
                    "列出",
                    "列表",
                    "有哪些",
                    "能看到的工具",
                    "可用工具",
                    "能力库",
                ],
            ):
                return self._runtime_skill_route("vrcforge_skill_manifest", skill_params, "skill manifest")
        if has_any(lowered, text, ["tools", "skill", "skills", "工具", "能力", "列表"]) and has_any(
            lowered,
            text,
            ["unity", "mcp", "vrcforge", "工具", "能力"],
        ):
            if has_any(
                lowered,
                text,
                [
                    "available",
                    "list",
                    "show",
                    "what tools",
                    "which tools",
                    "tool list",
                    "列出",
                    "列表",
                    "有哪些",
                    "能看到",
                    "可用工具",
                ],
            ):
                return self._runtime_skill_route("vrcforge_unity_tools", skill_params, "unity tool list")
        if has_any(lowered, text, ["health", "健康"]):
            return self._runtime_skill_route("vrcforge_health", skill_params, "runtime health")
        if has_any(lowered, text, ["unity", "mcp", "连接", "连上", "实例"]):
            return self._runtime_skill_route("vrcforge_unity_status", skill_params, "unity status")
        if has_any(lowered, text, ["avatar encryption", "shader encryption", "anti-rip", "antirip", "encrypt", "encryption"]):
            if has_any(lowered, text, ["research", "report", "notes"]):
                return self._runtime_skill_route("vrcforge_avatar_encryption_research_report", skill_params, "avatar encryption research report")
            if has_any(lowered, text, ["scan", "inventory", "materials"]):
                return self._runtime_skill_route("vrcforge_avatar_encryption_scan", skill_params, "avatar encryption scan")
            if has_any(lowered, text, ["preview", "would write", "rollback"]):
                return self._runtime_skill_route("vrcforge_avatar_encryption_preview", skill_params, "avatar encryption preview")
            return self._runtime_skill_route("vrcforge_avatar_encryption_plan", skill_params, "avatar encryption plan")
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
            "rollbackPolicy": self._write_handler_rollback_policy(handler),
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
        if "avatar_encryption" in text or "avatar-encryption" in text or "anti-rip" in text or "antirip" in text:
            return "avatar-encryption"
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
        requires_explicit_approval: bool = False,
        explicit_approval_reason: str = "",
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        config = self.ensure_config()
        permission_context = self.permission_audit_context(config)
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
            "permissionMode": permission_context["permissionMode"],
            "fullPermission": permission_context["fullPermission"],
            "permissionLabel": permission_context["permissionLabel"],
        }
        if requires_explicit_approval:
            approval["requiresExplicitApproval"] = True
            approval["autoApprovalBlocked"] = True
            approval["explicitApprovalReason"] = explicit_approval_reason or "This write request requires explicit user approval."
        if user_constraints and user_constraints.content:
            approval["userConstraintsApplied"] = True
            approval["userConstraintsPath"] = str(user_constraints.path)
        with self._lock:
            self._approvals[approval["id"]] = approval
            self.append_audit({"event": "approval_requested", "approval": approval, **permission_context})
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
            if approval.get("status") != "pending" and status == "rejected":
                return {"ok": False, "approval": approval, "message": f"Approval is {approval.get('status')}."}
            if approval.get("status") == "expired":
                return {"ok": False, "approval": approval, "message": "Approval has expired."}
            approval["status"] = status
            approval[f"{status}At"] = utc_now_iso()
            self._approvals[approval_id] = approval
            permission_context = self.permission_audit_context()
            self.append_audit({"event": f"approval_{status}", "approval": approval, **permission_context})
            self._append_runtime_run(
                {
                    "event": f"approval_{status}",
                    "status": status,
                    "approvalId": approval_id,
                    "approvalIds": [approval_id],
                    **permission_context,
                    "targetTool": approval.get("targetTool") or "",
                    "agent": approval.get("agentName") or "",
                    "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
                    "messageSummary": summarize_text(str(approval.get("reason") or "")),
                }
            )
            return {"ok": True, "approval": redact_sensitive(dict(approval))}

    def request_approval_revision(self, approval_id: str, *, reason: str = "", note: str = "") -> dict[str, Any]:
        with self._lock:
            approval = self._approvals.get(approval_id)
            if not approval:
                approval = self._load_approval_from_audit(approval_id)
            if not approval:
                raise AgentGatewayError(f"Approval was not found: {approval_id}", status_code=404)
            approval = self._refresh_approval_expiry(approval)
            status = str(approval.get("status") or "")
            if status != "pending":
                return {"ok": False, "approval": redact_sensitive(dict(approval)), "message": f"Approval is {status}."}
            approval["status"] = "revision_requested"
            approval["revisionRequestedAt"] = utc_now_iso()
            approval["revisionReason"] = reason.strip()
            approval["revisionNote"] = note.strip()
            self._approvals[approval_id] = approval
            self.append_audit({"event": "approval_revision_requested", "approval": approval})
            self._append_runtime_run(
                {
                    "event": "approval_revision_requested",
                    "status": "revision_requested",
                    "approvalId": approval_id,
                    "approvalIds": [approval_id],
                    "targetTool": approval.get("targetTool") or "",
                    "agent": approval.get("agentName") or "",
                    "projectRoot": ensure_dict(approval.get("arguments")).get("projectRoot") or "",
                    "messageSummary": summarize_text(note or reason),
                }
            )
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
        return None


def create_agent_mcp_app(gateway: AgentGateway):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

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
        *AVATAR_ENCRYPTION_TOOL_NAMES,
        "vrcforge_create_safe_backup",
        "vrcforge_preview_restore_backup",
        "vrcforge_list_checkpoints",
        "vrcforge_preview_restore_checkpoint",
        "vrcforge_list_interrupted_apply_recoveries",
        "vrcforge_preview_interrupted_apply_recovery",
        "vrcforge_export_interrupted_apply_incident_bundle",
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
        "vrcforge_read_avatar_descriptor",
        "vrcforge_preview_write_avatar_descriptor",
        "vrcforge_preview_write_animation_curve",
        "vrcforge_preview_manage_expression_parameters",
        "vrcforge_preview_manage_expression_menu",
        "vrcforge_preview_manage_fx_animator",
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


def extract_project_root(payload: dict[str, Any]) -> Path | None:
    raw = str(payload.get("projectRoot") or payload.get("project_root") or payload.get("projectPath") or payload.get("project_path") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def iter_param_leaf_values(value: Any, prefix: str = "", *, max_items: int = 200) -> Iterator[tuple[str, Any]]:
    if max_items <= 0:
        return
    if isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                break
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from iter_param_leaf_values(item, next_prefix, max_items=max_items - index - 1)
        return
    if isinstance(value, list):
        for index, item in enumerate(value[:max_items]):
            next_prefix = f"{prefix}.{index}" if prefix else str(index)
            yield from iter_param_leaf_values(item, next_prefix, max_items=max_items - index - 1)
        return
    yield prefix, value


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        flush_and_fsync(handle)
    temp_path.replace(path)
    fsync_directory_best_effort(path.parent)


def flush_and_fsync(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def fsync_file_path(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def fsync_directory_best_effort(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


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


def normalize_runtime_attachments(value: Any) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for raw in ensure_list(value)[:RUNTIME_ATTACHMENT_MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        try:
            size = int(raw.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        item: dict[str, Any] = {
            "id": summarize_text(str(raw.get("id") or ""), 120),
            "name": summarize_text(str(raw.get("name") or "attachment"), 240),
            "type": summarize_text(str(raw.get("type") or "application/octet-stream"), 120),
            "size": max(0, size),
            "payloadKind": summarize_text(str(raw.get("payloadKind") or raw.get("payload_kind") or "metadata"), 32),
            "truncated": bool(raw.get("truncated")),
            "error": summarize_text(str(raw.get("error") or ""), 240),
        }
        data_url = str(raw.get("dataUrl") or raw.get("data_url") or "")
        text = str(raw.get("text") or "")
        if data_url:
            item["dataUrl"] = data_url[:RUNTIME_ATTACHMENT_DATA_URL_MAX_CHARS]
            item["payloadKind"] = "data_url"
            item["payloadHash"] = stable_hash(data_url)
            item["replayable"] = True
            if len(data_url) > RUNTIME_ATTACHMENT_DATA_URL_MAX_CHARS:
                item["truncated"] = True
        elif text:
            item["text"] = text[:RUNTIME_ATTACHMENT_TEXT_MAX_CHARS]
            item["payloadKind"] = "text"
            item["payloadHash"] = stable_hash(text)
            item["replayable"] = True
            if len(text) > RUNTIME_ATTACHMENT_TEXT_MAX_CHARS:
                item["truncated"] = True
        else:
            item["payloadKind"] = "metadata"
            item["payloadHash"] = stable_hash(json.dumps({k: item.get(k) for k in ("name", "type", "size")}, sort_keys=True))
            item["replayable"] = False
        attachments.append(item)
    return attachments


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


_WRITE_INTENT_CN_VERB = re.compile(r"加个|加一个|加上|添加|新建|新增|创建|建个|建一个|挂个|挂一个|放个|增加")
_WRITE_INTENT_EN_VERB = re.compile(r"\b(add|create|new|insert|spawn|make)\b")
_WRITE_INTENT_EN_NOUN = re.compile(r"\b(game ?object|objects?|obj|empty|child)\b")
_WRITE_INTENT_CN_NOUN = ("对象", "物体", "节点")
_OBJECT_NAME_RE = re.compile(
    r"(?:叫做|叫作|叫|名为|命名为|named|name[d]?|called)\s*[\"'“”‘’]?([A-Za-z0-9_\-一-鿿]+)"
)


def detect_avatar_write_intent(message: str) -> dict[str, Any] | None:
    """Detect a 'create/add a scene object on a model' write intent.

    Returns a structured intent dict, or None for read/other intents. Kept narrow
    on purpose: it must NOT hijack read requests ("检查状态"/"list ...") or the
    outfit/wardrobe workflows. The win is that this routes the request into the
    scan→single-model-resolve→supervised-write loop instead of a chat reply.
    """
    text = (message or "").strip()
    if not text:
        return None
    lowered = text.lower()
    has_object_noun = bool(_WRITE_INTENT_EN_NOUN.search(lowered)) or any(
        noun in text for noun in _WRITE_INTENT_CN_NOUN
    )
    has_verb = bool(_WRITE_INTENT_EN_VERB.search(lowered)) or bool(_WRITE_INTENT_CN_VERB.search(text))
    explicit_phrase = bool(re.search(r"new\s*obj(ect)?", lowered))
    if not (explicit_phrase or (has_verb and has_object_noun)):
        return None
    name_match = _OBJECT_NAME_RE.search(text)
    return {
        "kind": "add_object",
        "objectName": name_match.group(1) if name_match else "GameObject",
        "target": "",
    }


def extract_avatar_paths(result: Any) -> list[str]:
    """Pull avatar paths out of a (possibly nested) vrcforge_list_avatars result."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        path = str(value or "").strip()
        if path and path not in seen:
            seen.add(path)
            found.append(path)

    def _visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("avatars", "avatarList") and isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            _add(
                                item.get("avatarPath")
                                or item.get("avatar_path")
                                or item.get("path")
                                or item.get("name")
                            )
                        elif isinstance(item, str):
                            _add(item)
                elif key in ("avatarPaths", "avatar_paths") and isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            _add(item)
                else:
                    _visit(value)
        elif isinstance(node, list):
            for item in node:
                _visit(item)

    _visit(result)
    return found


def extract_approval_id(obj: Any) -> str:
    """Recursively search a tool result for an approval id (approval_id/approvalId)."""
    found = ""

    def _visit(node: Any) -> None:
        nonlocal found
        if found:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if found:
                    return
                if key in ("approval_id", "approvalId") and str(value or "").strip():
                    found = str(value).strip()
                    return
                _visit(value)
        elif isinstance(node, list):
            for item in node:
                if found:
                    return
                _visit(item)

    _visit(obj)
    return found


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


def normalize_checkpoint_archive_max_size_mb(value: Any) -> int:
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        return CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB
    if amount <= 0:
        return 0
    return min(amount, CHECKPOINT_ARCHIVE_MAX_SIZE_MB_LIMIT)


def normalize_checkpoint_archive_dir(value: Any) -> str:
    """检查点存档迁移目录：仅做去空白，存在性/可写性在迁移时再校验。"""
    return str(value or "").strip()


def summarize_params(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            str(key): summarize_value(key, item)
            for key, item in value.items()
            if str(key).lower()
            not in {
                "token",
                "app_token",
                "artifact_sig",
                "artifact_signature",
                "artifact_token",
                "authorization",
                "api_key",
                "apikey",
                "access_token",
                "approval_token",
                "refresh_token",
                "secret",
                "user_constraints",
                "userconstraints",
                "_vrcforge_user_constraints",
            }
        }
    return {"value": summarize_value("value", value)}


def summarize_value(key: Any, value: Any) -> Any:
    key_text = str(key).lower()
    if key_text in {
        "token",
        "app_token",
        "artifact_sig",
        "artifact_signature",
        "artifact_token",
        "authorization",
        "api_key",
        "apikey",
        "access_token",
        "approval_token",
        "refresh_token",
        "secret",
    }:
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
                "app_token",
                "artifact_sig",
                "artifact_signature",
                "artifact_token",
                "authorization",
                "api_key",
                "apikey",
                "access_token",
                "approval_token",
                "refresh_token",
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

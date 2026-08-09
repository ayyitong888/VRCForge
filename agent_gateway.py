from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
import zipfile
import zlib
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable, Iterator, Sequence

from agent_memory_store import AgentMemoryStore
import agent_command_safety as command_safety
import runtime_planner_service as planner_policy
from agent_shell_service import (
    SHELL_RUNNER_NATIVE as SHELL_OWNER_RUNNER_NATIVE,
    SHELL_RUNNER_POWERSHELL as SHELL_OWNER_RUNNER_POWERSHELL,
    AgentShellPorts,
    AgentShellService,
    ShellApprovalPorts,
    ShellApprovalRequest,
    ShellProcessPorts,
    summarize_shell_result as summarize_owned_shell_result,
)

if TYPE_CHECKING:
    from agent_skill_registry import AgentSkillRegistryService
from agent_goal_service import (
    AgentGoalService,
    GoalApprovalStatePorts,
    GoalEventPorts,
    GoalStorePorts,
)
from agent_question_service import (
    AgentQuestionPersistence,
    AgentQuestionPersistencePorts,
    AgentQuestionScopePorts,
    AgentQuestionService,
    GoalQuestionResolutionPort,
)
from desktop_computer_use_service import DesktopComputerUsePorts, DesktopComputerUseService
from desktop_worker import DesktopActionBrokerError
from optimization_service import (
    OPTIMIZATION_GATEWAY_TOOL_NAMES,
    OPTIMIZATION_TOOL_DEFINITIONS,
    STABLE_OPTIMIZATION_APPLY_REQUEST_GATEWAY_NAMES,
)
from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts
from agent_runtime_run_ledger import AgentRuntimeRunLedger, AgentRuntimeRunLedgerPorts
from agent_runtime_skill_executor import AgentRuntimeSkillExecutor, AgentRuntimeSkillExecutorPorts
from runtime_planner_service import RuntimePlannerService
from background_goal_runtime import (
    RepeatedFailureGuard,
    classify_runtime_step_failure,
)
from approved_unity_execution import (
    bind_approved_unity_execution,
    create_approved_unity_execution_plan,
    freeze_approved_unity_execution_plan,
    validate_frozen_approved_unity_execution_plan,
)
from agent_mcp_2026 import Mcp2026Router, create_agent_mcp_2026_asgi_app
from unity_mcp_core_client import capture_unity_mcp_core_call_audits


ToolHandler = Callable[[dict[str, Any]], Any]
RiskLevelResolver = Callable[[dict[str, Any]], str]
ManualApprovalResolver = Callable[[dict[str, Any], Any], str]
CheckpointPrepareHandler = Callable[[Path, dict[str, Any]], dict[str, Any]]
WriteRequestPreparer = Callable[
    [dict[str, Any], Any],
    tuple[dict[str, Any], Any],
]
ApprovedUnityExecutionPlanBuilder = Callable[
    [dict[str, Any]],
    Sequence[tuple[str, dict[str, Any]]],
]

RUNTIME_SKILL_SUPPORT_MAX_FILES = 16
RUNTIME_SKILL_SUPPORT_MAX_FILE_BYTES = 64 * 1024
RUNTIME_SKILL_SUPPORT_MAX_TOTAL_BYTES = 256 * 1024
PROJECTED_SKILL_STATE_NAME = ".vrcforge-package-state.json"
PROJECTED_SKILL_STATE_MAX_BYTES = 4 * 1024
LEGACY_PROJECTED_SKILL_STATE_SCHEMA = "vrcforge.projected-skill-state.v1"
PROJECTED_SKILL_STATE_SCHEMA = "vrcforge.projected-skill-state.v2"

ROLLBACK_POLICY_SCHEMA = "vrcforge.write_rollback_policy.v1"
ROLLBACK_COVERAGE_AUDIT_SCHEMA = "vrcforge.rollback_coverage_audit.v1"
APPLY_RECOVERY_SCHEMA = "vrcforge.interrupted_apply_recovery.v1"
CONTEXT_USAGE_SCHEMA = "vrcforge.context_usage.v1"
RUNTIME_CONTEXT_COMPACTION_SCHEMA = "vrcforge.runtime_context_compaction.v1"
RUNTIME_CONTEXT_COMPACTION_TRIGGER_RATIO = 0.85
RUNTIME_CONTEXT_COMPACTION_HARD_RATIO = 0.95
RUNTIME_CONTEXT_COMPACTION_TARGET_RATIO = 0.50
UNITY_PROJECT_CHECKPOINT_SCOPE = ("Assets", "Packages", "ProjectSettings")
LOCAL_STATE_CHECKPOINT_SCOPE = ("skill-packages", "skills")
PROJECT_CHAT_CHECKPOINT_TARGET = "vrcforge_repair_project_chat_store"
PROJECT_CHAT_CHECKPOINT_MEMBER = ".vrcforge/chat-transcripts.json"
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
CHECKPOINT_RECORD_SCHEMA = "vrcforge.checkpoint.v1"
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
UNITY_RESTORE_GENERATED_CACHE_DIRS = ("Bee", "ScriptAssemblies")
# Unity Package Manager owns this tree and may hold files open; partial deletion corrupts the project cache.
UNITY_RESTORE_PRESERVED_CACHE_DIRS = ("PackageCache",)


class AgentGatewayError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class AgentDesktopGatewayError(AgentGatewayError, DesktopActionBrokerError):
    """Desktop boundary error understood by both Gateway and worker callers."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        AgentGatewayError.__init__(self, message, status_code)


def _split_lf_jsonl_lines(data: bytes, *, keepends: bool = False) -> list[bytes]:
    """Split JSONL only on its LF record delimiter, never on VT/FF."""

    if not data:
        return []
    chunks = data.split(b"\n")
    lines = [chunk + b"\n" if keepends else chunk for chunk in chunks[:-1]]
    if chunks[-1]:
        lines.append(chunks[-1])
    return lines


def _load_strict_json(text: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is not supported: {value}")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite JSON number is not supported: {value}")
        return parsed

    try:
        return json.loads(text, parse_constant=reject_constant, parse_float=parse_finite_float)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the supported limit") from exc


def _checkpoint_record_state(payload: Any) -> str:
    """Return valid, unknown_schema, or invalid for one checkpoint record."""

    if not isinstance(payload, dict):
        return "invalid"
    schema = payload.get("schema")
    if schema is not None and (not isinstance(schema, str) or not schema.strip()):
        return "invalid"
    if isinstance(schema, str) and schema != CHECKPOINT_RECORD_SCHEMA:
        return "unknown_schema"
    # Versionless legacy records and the current schema share these durable
    # fields. They are required by listing, restore selection, and audit UI.
    return (
        "valid"
        if all(
            isinstance(payload.get(field), str) and bool(str(payload.get(field)).strip())
            for field in ("id", "createdAt", "targetTool", "status")
        )
        else "invalid"
    )


@dataclass
class AgentGatewayConfig:
    enabled: bool = False
    require_token: bool = True
    token: str = ""
    approval_token: str = ""
    token_created_at: str = ""
    token_rotated_at: str = ""
    allow_write_requests: bool = True
    allow_roslyn_advanced: bool = False
    approval_timeout_seconds: int = 600
    execution_mode: str = "approval"
    roslyn_risk_acknowledged: bool = False
    developer_options_enabled: bool = False
    developer_options_ever_enabled: bool = False
    computer_use_enabled: bool = False
    computer_use_ever_enabled: bool = False
    background_goal_notifications_enabled: bool = True
    checkpoint_archive_max_size_mb: int = CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB
    checkpoint_archive_dir: str = ""
    project_category_allow_rules: list[dict[str, str]] = field(default_factory=list)


@dataclass
class AgentTool:
    name: str
    description: str
    category: str
    handler: ToolHandler
    write: bool = False
    advanced: bool = False
    requires_user_activation: bool = False


@dataclass
class AgentWriteHandler:
    name: str
    description: str
    risk_level: str
    handler: ToolHandler
    advanced: bool = False
    risk_level_resolver: RiskLevelResolver | None = None
    request_preparer: WriteRequestPreparer | None = None
    manual_approval_resolver: ManualApprovalResolver | None = None
    checkpoint_prepare_handler: CheckpointPrepareHandler | None = None
    requires_approved_execution_context: bool = False
    approved_execution_plan_builder: ApprovedUnityExecutionPlanBuilder | None = None
    # A category may be remembered only when the handler opts in.  This keeps
    # future approval rules narrowly tied to an explicitly reviewed tool.
    approval_category: str = ""
    allow_future_category: bool = False


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
# 这个上限只在规划器停不下来时兜底。25 步为复杂的只读调查保留足够空间，
# 同时限制无界 token 消耗、重复工具调用和审批噪声；常规任务应在此之前自然结束。
# 命中上限时不静默收尾，而是诚实告知「到步数上限、先汇报、可继续」（见循环 else 分支）。
RUNTIME_AGENT_MAX_STEPS = 25
RUNTIME_AGENT_MAX_TOOL_CALLS = 3
EXPOSURE_LAYER_PLANNING = "planning"
EXPOSURE_LAYER_EXECUTION = "execution"
RUNTIME_BLOCKED_SKILLS = {
    "vrcforge_agent_message",
    "vrcforge_execute_shell",
    "vrcforge_execute_approved_shell",
    "vrcforge_request_apply",
    "vrcforge_apply_approved",
    "vrcforge_restore_last_backup",
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
    "vrcforge_repair_project_chat_store",
}
SCOPED_ALLOW_RULE_FORBIDDEN_TOKENS = (
    "delete",
    "remove",
    "restore",
    "shell",
    "package",
    "uninstall",
)
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
DESKTOP_VISION_MAX_WIDTH = 1280
DESKTOP_VISION_MAX_HEIGHT = 720
# 视觉委托分析结果的展示/回灌上限：这是"给规划器看的图片描述"，不是原始载荷，
# 必须保持有界，避免一次识图把上下文塞爆。
RUNTIME_VISION_ANALYSIS_MAX_CHARS = 4_000
AGENT_MEMORY_MAX_ITEMS = 120
AGENT_GOAL_MAX_ITEMS = 60
# Goal 唤醒调度的护栏：重复间隔必须落在 [5 分钟, 7 天]，
# 防止误配置把网关变成高频自动执行器。
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
    "vrcforge_inspect_chat_attachment": {
        "title": "Chat Attachment Inspection",
        "inputs": ["Vault payloadHash plus optional entryPath for a bounded single-entry text extract."],
        "outputs": ["Attachment metadata, guarded archive listing or entry text, or image header dimensions."],
        "sideEffects": "reads the local attachment vault only; bytes never enter the prompt",
        "tags": ["attachment", "vault", "inspection"],
    },
    "vrcforge_import_chat_image": {
        "title": "Chat Image Import",
        "inputs": ["Vault payloadHash, Unity project path, and optional Assets/ target folder."],
        "outputs": ["Copied asset path and asset database refresh result."],
        "sideEffects": "writes one image file under Assets/ after approval",
        "tags": ["attachment", "vault", "import"],
    },
    "vrcforge_import_chat_archive": {
        "title": "Chat Archive Import",
        "inputs": ["Vault payloadHash, Unity project path, and optional managed Assets/ target folder."],
        "outputs": ["Supervised outfit import result or conservative managed ZIP extraction result."],
        "sideEffects": "re-verifies the content hash and archive guards, then imports or extracts safe asset types after approval and checkpoint",
        "tags": ["attachment", "vault", "archive", "import", "write"],
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
    "vrcforge_know_yourself": {
        "title": "Know Yourself",
        "inputs": [
            "Optional editorFocusConfirmed plus the report's editorFocusScope after "
            "the user explicitly activates the intended Unity editor."
        ],
        "outputs": [
            "Ordered Unity work-start evidence, current abilities, capability groups, "
            "structured gaps, boundaries, and the next safe action."
        ],
        "sideEffects": "none",
        "tags": ["self-check", "work-start", "unity", "readiness"],
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
    "vrcforge_export_vrm": {
        "title": "Unity Avatar to VRM 1.0",
        "whenToUse": "Convert the current or explicitly named loaded Unity Humanoid or VRChat avatar into a VRM 1.0 file.",
        "inputs": ["Avatar path, required author metadata and confirmRights=true, optional title/version, managed output path, and overwrite flag."],
        "outputs": ["Validated VRM 1.0 file metadata, restrictive default license profile, exporter identity, byte length, and VRMC_vrm validation status."],
        "sideEffects": "writes or replaces one .vrm file under Assets/VRCForge/Exports after approval",
        "backupRestore": "uses the gateway checkpoint before export; dependency or validation failure does not replace the destination",
        "tags": ["avatar", "vrm", "export", "checkpoint", "write"],
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
        "name": "know-yourself",
        "title": "Know Yourself",
        "description": (
            "Understand VRCForge readiness, current capabilities, missing preparation, "
            "and safe operating boundaries before starting Unity project work."
        ),
        "category": "work-start",
        "permissionMode": "read_only",
        "riskLevel": "low",
        "whenToUse": (
            "after connecting a provider, before opening or changing a Unity project, "
            "after dependency installation, or whenever the agent must explain what it "
            "can and cannot currently do"
        ),
        "inputs": [
            "Optional editorFocusConfirmed and the current editorFocusScope only after "
            "the user explicitly clicks inside the intended Unity editor window."
        ],
        "outputs": [
            "One ordered readiness sequence, evidence freshness, current and post-baseline "
            "abilities, bounded tool and capability groups, structured gaps, write "
            "boundaries, and one next safe action."
        ],
        "sideEffects": (
            "none; this Skill only reads existing VRCForge, Doctor, registry, and Unity "
            "MCP state"
        ),
        "backupRestore": "not required; the Skill never writes, installs, launches, closes, or repairs",
        "allowedTools": ["vrcforge_know_yourself"],
        "entrypointTool": "vrcforge_know_yourself",
        "instructions": (
            "Run this Skill before the first project operation and whenever the selected "
            "project, dependencies, Unity process, connection, permissions, or Skill "
            "registry changes. Treat only observed evidence in the current report as fact. "
            "If dependencies are installed and the report requests editor activation, ask "
            "the user to click once inside the intended Unity editor window, then run the "
            "Skill again with editorFocusConfirmed=true and the exact editorFocusScope "
            "returned by the prior report. A stale or claimed acknowledgement never proves "
            "readiness: require fresh bridge, selected-instance, compile, and required-tool "
            "readback. Complete the recommended read-only baseline before task planning or "
            "any guarded write request. Explain abilities only from the returned bounded "
            "tools and capability groups, state structured blockers and unavailable "
            "reasons, and preserve approval, checkpoint, validation, and rollback "
            "boundaries for every later write."
        ),
        "tags": ["builtin", "group", "self-check", "work-start", "unity", "readiness"],
    },
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
        *,
        desktop_capture_dir: Path | None = None,
        desktop_actions_changed: Callable[[], None] | None = None,
        desktop_controller_factory: Callable[[Path], Any] | None = None,
        shell_process_ports: ShellProcessPorts | None = None,
        skill_package_write_lock: AbstractContextManager[object] | None = None,
        background_activity_started: Callable[[str], Any] | None = None,
    ) -> None:
        self.config_path = config_path
        self.audit_dir = audit_dir
        self.public_base_url = public_base_url.rstrip("/")
        self._tools: dict[str, AgentTool] = {}
        self._write_handlers: dict[str, AgentWriteHandler] = {}
        self._approvals: dict[str, dict[str, Any]] = {}
        self._skill_package_write_lock_bound = skill_package_write_lock is not None
        self._skill_package_write_lock = skill_package_write_lock or nullcontext()
        self.checkpoint_project_root_resolver: Callable[[], str] | None = None
        self.checkpoint_prepare_handler: Callable[[Path], dict[str, Any]] | None = None
        self.checkpoint_restore_prepare_handler: Callable[[Path], dict[str, Any]] | None = None
        self.checkpoint_restore_handler: Callable[[Path, dict[str, Any]], dict[str, Any]] | None = None
        self._lock = threading.RLock()
        self._runtime_session_state = AgentRuntimeSessionState(
            AgentRuntimeSessionStatePorts(shared_state_lock=self._lock)
        )
        self._runtime_run_ledger = AgentRuntimeRunLedger(
            AgentRuntimeRunLedgerPorts(
                log_path=lambda: self.audit_dir / "runtime-runs.jsonl",
                shared_state_lock=self._lock,
                now=utc_now_iso,
                normalize_path=command_safety.normalize_filesystem_path,
                normalize_visual_accent=DesktopComputerUseService.normalize_visual_accent,
                summarize_text=summarize_text,
                redact=redact_sensitive,
                ensure_append_boundary=self._ensure_jsonl_append_boundary_locked,
                flush_and_fsync=flush_and_fsync,
                error_factory=lambda detail, status: AgentGatewayError(detail, status_code=status),
            )
        )
        self._agent_memory_store = AgentMemoryStore(
            lambda: self.agent_memory_log_path,
            lambda: self.audit_dir / "memory-review" / "accepted-audit.jsonl",
            lock=self._lock,
        )
        self._goal = AgentGoalService(
            GoalStorePorts(
                log_path=lambda: self.audit_dir / "agent-goals.jsonl",
                result_dir=lambda: self.audit_dir / "agent-goal-results",
                run_dir=lambda: self.audit_dir / "agent-goal-runs",
                append_event=self._append_jsonl,
                read_events=lambda path: self._read_jsonl(path, limit=0),
                shared_state_lock=self._lock,
                normalize_path=command_safety.normalize_filesystem_path,
            ),
            GoalApprovalStatePorts(
                get=lambda approval_id: self._approvals.get(approval_id),
                items=lambda: list(self._approvals.items()),
                ids=lambda: set(self._approvals),
            ),
            GoalEventPorts(
                redact=redact_sensitive,
                redact_persistence=redact_background_goal_persistence,
                summarize=summarize_text,
            ),
        )
        self._questions = AgentQuestionService(
            AgentQuestionPersistence(
                AgentQuestionPersistencePorts(
                    log_path=lambda: self.audit_dir / "agent-questions.jsonl",
                    shared_state_lock=self._lock,
                    redact=redact_sensitive,
                )
            ),
            AgentQuestionScopePorts(
                normalize_path=command_safety.normalize_filesystem_path,
                summarize=summarize_text,
                redact_goal_persistence=redact_background_goal_persistence,
            ),
            GoalQuestionResolutionPort(
                resolve=lambda question_id, continuation_prompt: self._goal.resolve_agent_goal_question(
                    question_id,
                    continuation_prompt=continuation_prompt,
                )
            ),
        )
        # In-progress approved writes, keyed by approval id. This is a global,
        # deliberately conservative lane: even writes for different projects
        # remain serialized until per-project and shared-storage locking has its
        # own proof. It closes the gap before durable apply recovery exists.
        self._in_flight_apply_writes: dict[str, dict[str, Any]] = {}
        self._background_project_read_leases: set[str] = set()
        if background_activity_started is not None and not callable(background_activity_started):
            raise TypeError("background_activity_started must be callable")
        self._background_activity_started = background_activity_started
        self.apply_lifecycle_observer_fn: Callable[[str, dict[str, Any]], Any] | None = None
        self._runtime_planner: RuntimePlannerService | None = None
        # Optional vision-analysis hook injected by the host server. Receives
        # (message, image_attachments) and returns a dict:
        #   {"status": "analyzed", "text", "provider", "providerLabel",
        #    "model", "source", "usage"}
        # or {"status": "unconfigured", "reason"}. The vision call is a
        # separate provider request: its token usage is recorded on the vision
        # run step only and must NEVER be merged into text-planner context usage
        # (the chat context meter).
        self.vision_analyze_fn: Callable[[str, list[dict[str, Any]]], Any] | None = None
        # Host-owned and deliberately optional.  The gateway treats every
        # result other than the exact string ``allow_auto`` as manual review.
        self.scoped_approval_reviewer_fn: Callable[[dict[str, Any]], str] | None = None
        # 审计 JSONL 追加锁：见 append_audit。
        self._audit_append_lock = threading.Lock()
        # User-authored Skill CRUD and Doctor quarantine operate on the same
        # directory tree.  Keep that domain separate from the broad gateway
        # state lock so a repair cannot act on a stale manifest snapshot.
        user_skill_lock = threading.RLock()
        # Checkpoint archives, their JSONL projection, and storage relocation
        # form one consistency domain. Creation calls pruning recursively, so
        # this must be re-entrant.
        self._checkpoint_storage_lock = threading.RLock()
        # The host replaces this with the writer lock for project chat
        # transcripts. Checkpoint operations always acquire storage first and
        # this lock second; chat writers must never enter checkpoint storage
        # while holding their writer lock.
        self._project_chat_checkpoint_lock = threading.RLock()
        # 当用户把检查点存档目录迁出 C 盘后，这里缓存覆盖后的绝对路径，
        # 让 checkpoint_store_dir 走新位置；为空时回落到 audit_dir 下默认目录。
        self._checkpoint_store_override: Path | None = None
        # Checkpoint/recovery owns no lifecycle resources; it resolves this
        # facade late so host callback, path, and lock replacements remain visible.
        from agent_checkpoint_recovery import AgentCheckpointRecoveryService

        self._checkpoint_recovery = AgentCheckpointRecoveryService(self)
        # Approval/write transactions likewise resolve the authoritative host
        # registries, locks, hooks, and persistence paths late. The service
        # creates no second approval state or lifecycle resource.
        from agent_approval_transactions import AgentApprovalTransactionService, ApprovalGoalPorts

        self._approval_transactions = AgentApprovalTransactionService(
            self,
            ApprovalGoalPorts(
                deny_approval=self._goal.deny_agent_goal_approval,
                attach_terminal_resolution=self._goal.attach_linked_goal_resolution,
                delivery_for_approval=self._goal.raw_delivery_for_approval,
                reconcile_missing_approvals=self._goal.reconcile_missing_approvals,
            ),
            runtime_run_append=self._runtime_run_ledger.append,
        )

        def find_pending_shell_approval(session_id: str, turn_id: str) -> dict[str, Any] | None:
            with self._lock:
                return next(
                    (
                        approval
                        for approval in self._approvals.values()
                        if approval.get("status") == "pending"
                        and approval.get("targetTool") == "vrcforge_shell_execute"
                        and approval.get("sessionId") == session_id
                        and approval.get("turnId") == turn_id
                    ),
                    None,
                )

        def create_shell_approval(request: ShellApprovalRequest) -> dict[str, Any]:
            return self._new_approval(
                agent_name=request.agent_name,
                target_tool=request.target_tool,
                arguments=request.arguments,
                reason=request.reason,
                preview=request.preview,
                risk_level=request.risk_level,
                user_constraints=request.user_constraints,
                requires_explicit_approval=request.requires_explicit_approval,
                explicit_approval_reason=request.explicit_approval_reason,
                goal_delivery_id=request.goal_delivery_id,
            )

        def update_shell_approval_metadata(approval_id: str, metadata: dict[str, Any]) -> None:
            with self._lock:
                stored = self._approvals.get(approval_id)
                if stored is not None:
                    stored.update(metadata)

        def find_shell_approval(approval_id: str) -> dict[str, Any] | None:
            with self._lock:
                approval = self._approvals.get(approval_id)
            return approval or self._load_approval_from_audit(approval_id)

        def default_shell_workspace_root() -> Path:
            app_dir = os.environ.get("VRCFORGE_APP_DIR", "").strip()
            return Path(app_dir).resolve() if app_dir else Path.cwd().resolve()

        # The service owns every child process for the gateway lifetime. Its
        # authority is limited to the caller-supplied cwd, approval identity is
        # still owned by the approval transaction service, and Dashboard owns
        # start/shutdown for the app lifecycle.
        self._shell = AgentShellService(
            AgentShellPorts(
                approvals=ShellApprovalPorts(
                    find_pending_shell=find_pending_shell_approval,
                    create=create_shell_approval,
                    update_metadata=update_shell_approval_metadata,
                    find=find_shell_approval,
                    apply=lambda approval_id: self.apply_approved({"approval_id": approval_id}),
                    auto_enabled=self.auto_approval_enabled,
                    auto_execute=self._auto_execute_approval,
                    execution_mode=lambda: self.ensure_config().execution_mode,
                    read_user_constraints=self.read_user_constraints,
                    redact=redact_sensitive,
                ),
                append_audit=self.append_audit,
                permission_audit_context=self.permission_audit_context,
                cancellation_requested=(
                    lambda session_id, turn_id, client_turn_id: self._runtime_session_state.cancel_requested(
                        session_id=session_id,
                        turn_id=turn_id,
                        client_turn_id=client_turn_id,
                    )
                ),
                default_workspace_root=default_shell_workspace_root,
                error_factory=lambda detail, status: AgentGatewayError(detail, status_code=status),
            ),
            process_ports=shell_process_ports,
        )
        from agent_skill_registry import (
            AgentSkillRegistryPorts,
            AgentSkillRegistryService,
            SkillToolDescriptor,
            SkillWriteHandlerDescriptor,
        )

        def skill_tools() -> tuple[SkillToolDescriptor, ...]:
            return tuple(
                SkillToolDescriptor(
                    name=tool.name,
                    description=tool.description,
                    category=tool.category,
                    write=tool.write,
                    advanced=tool.advanced,
                    requires_user_activation=tool.requires_user_activation,
                )
                for tool in self._tools.values()
            )

        def skill_write_handlers() -> tuple[SkillWriteHandlerDescriptor, ...]:
            return tuple(
                SkillWriteHandlerDescriptor(
                    name=handler.name,
                    description=handler.description,
                    risk_level=handler.risk_level,
                    advanced=handler.advanced,
                )
                for handler in self._write_handlers.values()
            )

        self._skills = AgentSkillRegistryService(
            AgentSkillRegistryPorts(
                config_path=lambda: self.config_path,
                ensure_config=self.ensure_config,
                list_tools=skill_tools,
                list_write_handlers=skill_write_handlers,
                tool_visible=lambda name, config: bool(
                    (tool := self._tools.get(name)) is not None
                    and self._tool_visible(tool, config)
                ),
                write_handler_visible=lambda name, config: bool(
                    (handler := self._write_handlers.get(name)) is not None
                    and self._write_handler_visible(handler, config)
                ),
                computer_use_model_invocable=lambda config: self._desktop.computer_use_model_invocable(config),
                append_audit=self.append_audit,
                user_skill_lock=user_skill_lock,
                local_state_write_guard=self.local_state_write_guard,
            )
        )
        self._desktop = DesktopComputerUseService(
            audit_dir,
            desktop_capture_dir or audit_dir / "desktop-captures",
            DesktopComputerUsePorts(
                shared_state_lock=self._lock,
                ensure_config=self.ensure_config,
                runtime_cancel_requested=self._runtime_session_state.cancel_requested,
                signal_background_activity=self._signal_background_activity,
                has_tool=lambda name: name in self._tools,
                call_tool=lambda name, params, agent_name: self.call_tool(
                    name,
                    params,
                    agent_name=agent_name,
                ),
                run_vision_analysis=self._run_vision_analysis,
                build_screenshot_attachment=lambda path, root: desktop_screenshot_attachment(
                    path,
                    allowed_root=root,
                ),
                append_jsonl=self._append_jsonl,
                read_jsonl=lambda path, limit: self._read_jsonl(path, limit=limit),
                summarize_text=summarize_text,
                summarize_params=summarize_params,
                redact_sensitive=redact_sensitive,
                normalize_filesystem_path=command_safety.normalize_filesystem_path,
                normalize_execution_mode=normalize_execution_mode,
                error_factory=lambda detail, status: AgentDesktopGatewayError(
                    detail,
                    status,
                ),
                on_actions_changed=desktop_actions_changed,
                controller_factory=desktop_controller_factory,
            ),
        )
        self._runtime_skill_executor = AgentRuntimeSkillExecutor(
            AgentRuntimeSkillExecutorPorts(
                ensure_config=self.ensure_config,
                tool_for_name=lambda name: self._tools.get(name),
                package_write_lock=self._skill_package_write_lock,
                prepare_runtime_skill=self.skills.prepare_runtime_skill,
                package_audit_context=lambda skill: self._runtime_skill_package_audit_context_locked(skill),
                computer_use_model_invocable=self._desktop.computer_use_model_invocable,
                tool_visible=self._tool_visible,
                tool_params_audit=self._tool_params_audit,
                read_user_constraints=self.read_user_constraints,
                inject_user_constraints=self._inject_user_constraints,
                append_audit=self.append_audit,
                redact=redact_sensitive,
                summarize_params=summarize_params,
                ensure_string_list=ensure_string_list,
                build_runtime_skill_payload=build_runtime_skill_payload,
                blocked_skills=frozenset(RUNTIME_BLOCKED_SKILLS),
                direct_categories=frozenset(RUNTIME_DIRECT_SKILL_CATEGORIES),
            )
        )

    @property
    def desktop(self) -> DesktopComputerUseService:
        return self._desktop

    @property
    def shell(self) -> AgentShellService:
        return self._shell

    @property
    def skills(self) -> AgentSkillRegistryService:
        return self._skills

    @property
    def goal(self) -> AgentGoalService:
        return self._goal

    @property
    def runtime_planner(self) -> RuntimePlannerService:
        planner = self._runtime_planner
        if planner is None:
            raise RuntimeError("runtime planner is not bound")
        return planner

    @property
    def questions(self) -> AgentQuestionService:
        return self._questions

    @property
    def runtime_sessions(self) -> AgentRuntimeSessionState:
        return self._runtime_session_state

    @property
    def runtime_runs(self) -> AgentRuntimeRunLedger:
        return self._runtime_run_ledger

    @property
    def runtime_skills(self) -> AgentRuntimeSkillExecutor:
        return self._runtime_skill_executor

    def bind_runtime_planner(self, planner: RuntimePlannerService) -> None:
        if not isinstance(planner, RuntimePlannerService):
            raise TypeError("runtime planner must be a RuntimePlannerService")
        if self._runtime_planner is not None:
            raise RuntimeError("runtime planner is already bound")
        self._runtime_planner = planner

    def configure_paths(self, config_path: Path, audit_dir: Path) -> None:
        with self._lock:
            self.config_path = config_path
            self.audit_dir = audit_dir
            self._approvals.clear()
            self._runtime_session_state.clear()
            self._desktop.configure_paths(audit_dir)

    def bind_project_chat_checkpoint_lock(self, lock: Any) -> None:
        """Share the host's project-chat writer lock with checkpoint I/O."""

        if lock is None or not hasattr(lock, "__enter__") or not hasattr(lock, "__exit__"):
            raise ValueError("project chat checkpoint lock must be a context manager")
        self._project_chat_checkpoint_lock = lock

    def _signal_background_activity(self, reason: str) -> None:
        callback = self._background_activity_started
        if callback is None:
            return
        try:
            callback(str(reason or "activity"))
        except Exception:
            # Optional background cancellation must never reject the
            # interactive operation that owns this boundary.
            pass

    def _observe_apply_lifecycle(
        self,
        stage: str,
        approval: dict[str, Any],
        *,
        checkpoint: dict[str, Any] | None = None,
        result: Any = None,
        arguments_digest: str = "",
    ) -> None:
        return self._approval_transactions._impl__observe_apply_lifecycle(
            stage,
            approval,
            checkpoint=checkpoint,
            result=result,
            arguments_digest=arguments_digest,
        )

    @property
    def agent_memory_log_path(self) -> Path:
        return self.audit_dir / "agent-memory.jsonl"

    @property
    def agent_memory_store(self) -> AgentMemoryStore:
        return self._agent_memory_store

    @property
    def agent_progress_log_path(self) -> Path:
        return self.audit_dir / "agent-progress.jsonl"

    @property
    def desktop_action_log_path(self) -> Path:
        return self._desktop.desktop_action_log_path

    @property
    def desktop_bridge_log_path(self) -> Path:
        return self._desktop.desktop_bridge_log_path

    def register_tool(
        self,
        name: str,
        description: str,
        category: str,
        handler: ToolHandler,
        write: bool = False,
        advanced: bool = False,
        requires_user_activation: bool = False,
    ) -> None:
        self._tools[name] = AgentTool(
            name=name,
            description=description,
            category=category,
            handler=handler,
            write=write,
            advanced=advanced,
            requires_user_activation=requires_user_activation,
        )

    def register_write_handler(
        self,
        name: str,
        description: str,
        risk_level: str,
        handler: ToolHandler,
        advanced: bool = False,
        risk_level_resolver: RiskLevelResolver | None = None,
        request_preparer: WriteRequestPreparer | None = None,
        manual_approval_resolver: ManualApprovalResolver | None = None,
        checkpoint_prepare_handler: CheckpointPrepareHandler | None = None,
        requires_approved_execution_context: bool = False,
        approved_execution_plan_builder: ApprovedUnityExecutionPlanBuilder | None = None,
        approval_category: str = "",
        allow_future_category: bool = False,
    ) -> None:
        return self._approval_transactions._impl_register_write_handler(
            name,
            description,
            risk_level,
            handler,
            advanced,
            risk_level_resolver,
            request_preparer,
            manual_approval_resolver,
            checkpoint_prepare_handler,
            requires_approved_execution_context,
            approved_execution_plan_builder,
            approval_category,
            allow_future_category,
        )

    def ensure_config(self) -> AgentGatewayConfig:
        with self._lock:
            raw = self._read_config_payload()
            changed = False

            if not raw.get("token"):
                raw["token"] = secrets.token_urlsafe(32)
                now = utc_now_iso()
                raw["token_created_at"] = now
                raw["token_rotated_at"] = now
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
                "developer_options_enabled": False,
                "developer_options_ever_enabled": False,
                "computer_use_enabled": False,
                "computer_use_ever_enabled": False,
                "background_goal_notifications_enabled": True,
                "checkpoint_archive_max_size_mb": CHECKPOINT_ARCHIVE_DEFAULT_MAX_SIZE_MB,
                "checkpoint_archive_dir": "",
                "project_category_allow_rules": [],
                "token_created_at": "",
                "token_rotated_at": "",
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
                token_created_at=str(raw.get("token_created_at") or ""),
                token_rotated_at=str(raw.get("token_rotated_at") or ""),
                allow_write_requests=bool(raw.get("allow_write_requests", True)),
                allow_roslyn_advanced=bool(raw.get("allow_roslyn_advanced", False)),
                approval_timeout_seconds=int(raw.get("approval_timeout_seconds", 600)),
                execution_mode=normalize_execution_mode(raw.get("execution_mode")),
                roslyn_risk_acknowledged=bool(raw.get("roslyn_risk_acknowledged", False)),
                developer_options_enabled=bool(raw.get("developer_options_enabled", False)),
                developer_options_ever_enabled=bool(raw.get("developer_options_ever_enabled", False)),
                computer_use_enabled=bool(raw.get("computer_use_enabled", False)),
                computer_use_ever_enabled=bool(raw.get("computer_use_ever_enabled", False)),
                background_goal_notifications_enabled=bool(
                    raw.get("background_goal_notifications_enabled", True)
                ),
                checkpoint_archive_max_size_mb=normalize_checkpoint_archive_max_size_mb(
                    raw.get("checkpoint_archive_max_size_mb")
                ),
                checkpoint_archive_dir=normalize_checkpoint_archive_dir(
                    raw.get("checkpoint_archive_dir")
                ),
                project_category_allow_rules=self._normalize_project_category_allow_rules(
                    raw.get("project_category_allow_rules")
                ),
            )
            self._sync_checkpoint_store_override(config)
            if changed:
                self.save_config(config)
            return config

    def save_config(self, config: AgentGatewayConfig) -> None:
        with self._lock:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            if not config.token:
                now = utc_now_iso()
                config.token = secrets.token_urlsafe(32)
                config.token_created_at = config.token_created_at or now
                config.token_rotated_at = config.token_rotated_at or now
            if not config.approval_token:
                config.approval_token = secrets.token_urlsafe(32)
            payload = {
                "enabled": bool(config.enabled),
                "require_token": bool(config.require_token),
                "token": config.token,
                "approval_token": config.approval_token,
                "token_created_at": str(config.token_created_at or ""),
                "token_rotated_at": str(config.token_rotated_at or ""),
                "allow_write_requests": bool(config.allow_write_requests),
                "allow_roslyn_advanced": bool(config.allow_roslyn_advanced),
                "approval_timeout_seconds": int(config.approval_timeout_seconds),
                "execution_mode": normalize_execution_mode(config.execution_mode),
                "roslyn_risk_acknowledged": bool(config.roslyn_risk_acknowledged),
                "developer_options_enabled": bool(config.developer_options_enabled),
                "developer_options_ever_enabled": bool(config.developer_options_ever_enabled),
                "computer_use_enabled": bool(config.computer_use_enabled),
                "computer_use_ever_enabled": bool(config.computer_use_ever_enabled),
                "background_goal_notifications_enabled": bool(
                    config.background_goal_notifications_enabled
                ),
                "checkpoint_archive_max_size_mb": normalize_checkpoint_archive_max_size_mb(
                    config.checkpoint_archive_max_size_mb
                ),
                "checkpoint_archive_dir": normalize_checkpoint_archive_dir(
                    config.checkpoint_archive_dir
                ),
                "project_category_allow_rules": self._normalize_project_category_allow_rules(
                    config.project_category_allow_rules
                ),
            }
            atomic_write_json(self.config_path, payload)
            self._sync_checkpoint_store_override(config)

    @staticmethod
    def _normalize_project_category_allow_rules(raw: Any) -> list[dict[str, str]]:
        if not isinstance(raw, list):
            return []
        rules: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            project_root = str(item.get("projectRoot") or item.get("project_root") or "").strip()
            category = str(item.get("category") or "").strip()
            if not project_root or not category:
                continue
            key = (command_safety.normalize_filesystem_path(project_root), category)
            if not key[0] or key in seen:
                continue
            seen.add(key)
            rules.append({"projectRoot": key[0], "category": category})
        return rules

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
        return self._approval_transactions._impl_authenticate_approval(
            headers,
            query_params,
            client_host,
        )

    def build_manifest(self, exposure_layer: str = EXPOSURE_LAYER_EXECUTION) -> dict[str, Any]:
        exposure_layer = normalize_exposure_layer(exposure_layer)
        config = self.ensure_config()
        permission_context = self.permission_audit_context(config)
        user_constraints = self.read_user_constraints()
        tools = [
            self._serialize_tool(tool, config)
            for tool in self._tools.values()
            if self._tool_visible(tool, config, exposure_layer)
            and (not tool.requires_user_activation or self._desktop.computer_use_model_invocable(config))
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
            "advancedSettings": self.advanced_settings_state(config),
            "approvalTimeoutSeconds": config.approval_timeout_seconds,
            "exposureLayer": exposure_layer,
            "tools": tools,
            "toolCount": len(tools),
            "writeTargets": self.visible_write_targets(config, exposure_layer),
            "skills": self.skills.build_skill_registry(config, exposure_layer)["skills"],
            "userConstraints": self._serialize_user_constraints(user_constraints),
        }

    def build_tool_registry(
        self,
        config: AgentGatewayConfig | None = None,
        exposure_layer: str = EXPOSURE_LAYER_EXECUTION,
    ) -> dict[str, Any]:
        exposure_layer = normalize_exposure_layer(exposure_layer)
        config = config or self.ensure_config()
        tools: list[dict[str, Any]] = []
        for tool in self._tools.values():
            if tool.name in EXTERNAL_AGENT_INTERNAL_TOOLS:
                continue
            if not self._tool_visible(tool, config, exposure_layer):
                continue
            tools.append(self._serialize_tool_registry_entry(tool, config))
        for handler in self._write_handlers.values():
            if handler.name in WRAPPER_ONLY_WRITE_TARGETS:
                continue
            if not self._write_handler_visible(handler, config, exposure_layer):
                continue
            tools.append(self._serialize_write_registry_entry(handler, config))
        tools.sort(key=lambda item: (str(item.get("category") or ""), str(item.get("id") or "")))
        categories = sorted({str(item.get("category") or "misc") for item in tools})
        return {
            "ok": True,
            "schema": "vrcforge.tool_registry.v1",
            "generatedAt": utc_now_iso(),
            "exposureLayer": exposure_layer,
            "count": len(tools),
            "categories": categories,
            "tools": tools,
        }

    def build_health(self) -> dict[str, Any]:
        config = self.ensure_config()
        user_constraints = self.read_user_constraints()
        pending = [item for item in self.list_approvals(include_expired=False) if item.get("status") == "pending"]
        skills = self.skills.build_skill_registry(config)
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
                "defaultRunner": SHELL_OWNER_RUNNER_NATIVE,
                "fallbackRunner": SHELL_OWNER_RUNNER_POWERSHELL,
                "shell": "powershell",
                "shellRole": "fallback",
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
            "runtimeSessions": self._runtime_session_state.session_count(),
        }

    def auto_approval_enabled(self, config: AgentGatewayConfig | None = None) -> bool:
        return self._approval_transactions._impl_auto_approval_enabled(
            config,
        )

    def permission_audit_context(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        return self._approval_transactions._impl_permission_audit_context(
            config,
        )

    def _auto_approval_block_reason(self, approval: dict[str, Any], config: AgentGatewayConfig | None = None) -> str:
        return self._approval_transactions._impl__auto_approval_block_reason(
            approval,
            config,
        )

    def permission_state(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        return self._approval_transactions._impl_permission_state(
            config,
        )

    def update_permission_state(
        self,
        execution_mode: str,
        acknowledge_roslyn_risk: bool = False,
    ) -> dict[str, Any]:
        return self._approval_transactions._impl_update_permission_state(
            execution_mode,
            acknowledge_roslyn_risk,
        )

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
        request_id = f"call_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(4)}"
        started_at = time.perf_counter()
        params_summary = self._tool_params_audit(name, params)
        user_constraints = self.read_user_constraints()
        tool_params = self._inject_user_constraints(params, tool, user_constraints)
        core_call_audits: list[dict[str, Any]] = []
        try:
            with capture_unity_mcp_core_call_audits() as core_call_audits:
                result = tool.handler(tool_params)
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            result_summary = summarize_params(result if isinstance(result, dict) else {"result": result})
            request_trace = (
                {"gatewayRequestId": request_id, "unityCoreCallAudits": core_call_audits}
                if core_call_audits
                else None
            )
            audit_event = {
                "event": "tool_call",
                "requestId": request_id,
                "tool": name,
                "agent": agent_name,
                "paramsSummary": params_summary,
                "resultSummary": result_summary,
                "durationMs": duration_ms,
                "status": "ok",
            }
            if request_trace is not None:
                audit_event["requestTrace"] = request_trace
            self.append_audit(audit_event)
            response = {
                "ok": True,
                "requestId": request_id,
                "tool": name,
                "agent": agent_name,
                "result": result,
                "resultSummary": result_summary,
                "durationMs": duration_ms,
            }
            if request_trace is not None:
                response["requestTrace"] = request_trace
            return response
        except Exception as exc:  # noqa: BLE001 - tool errors must be returned to external agents.
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            request_trace = (
                {"gatewayRequestId": request_id, "unityCoreCallAudits": core_call_audits}
                if core_call_audits
                else None
            )
            audit_event = {
                "event": "tool_call",
                "requestId": request_id,
                "tool": name,
                "agent": agent_name,
                "paramsSummary": params_summary,
                "resultSummary": {"status": "error"},
                "durationMs": duration_ms,
                "status": "error",
                "error": str(exc),
            }
            if request_trace is not None:
                audit_event["requestTrace"] = request_trace
            self.append_audit(audit_event)
            response = {
                "ok": False,
                "requestId": request_id,
                "tool": name,
                "agent": agent_name,
                "error": str(exc),
                "resultSummary": {"status": "error"},
                "durationMs": duration_ms,
            }
            if request_trace is not None:
                response["requestTrace"] = request_trace
            return response

    def _run_vision_analysis(
        self,
        message: str,
        image_attachments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Delegate image attachments to the host-configured vision model.

        路由矩阵（见 docs/ROADMAP.local.md「Dedicated Vision Model Profile」）：
        - hook 返回 analyzed → 结果作为带标签的 vision run step 注入本轮；
        - hook 返回 unconfigured / hook 缺失 → 诚实提示（绝不静默丢弃附件，
          也绝不把图片原始字节发给不支持视觉的模型）；
        - hook 抛错 → error 状态 + 有界错误信息，同样以提示收尾。
        视觉调用的 token 用量只记录在返回的 payload/step 上，绝不写入
        文本规划器的聊天上下文用量。
        """
        names = [
            summarize_text(str(item.get("name") or "image"), 120)
            for item in image_attachments[:RUNTIME_ATTACHMENT_MAX_ITEMS]
        ]
        base: dict[str, Any] = {
            "schema": "vrcforge.vision_analysis.v1",
            "imageCount": len(image_attachments),
            "imageNames": names,
        }
        hook = self.vision_analyze_fn
        if hook is None:
            return {
                **base,
                "status": "unconfigured",
                "reason": "vision_hook_missing",
                "notice": (
                    "（当前主模型无法识图：本轮附带的图片没有被读取。"
                    "可在 设置 > 模型与 Provider 里配置一个视觉模型，之后图片会自动交给它分析。）"
                ),
            }
        try:
            raw = hook(message, image_attachments)
        except Exception as exc:  # noqa: BLE001 - 视觉委托失败必须诚实上报，不静默。
            return {
                **base,
                "status": "error",
                "error": summarize_text(str(exc), 500),
                "notice": (
                    "（视觉模型调用失败，图片内容未能分析："
                    f"{summarize_text(str(exc), 200)}。图片没有被静默丢弃，可稍后重试或检查视觉模型配置。）"
                ),
            }
        result = ensure_dict(raw)
        status = str(result.get("status") or "").strip() or "error"
        if status == "analyzed":
            text = summarize_text(str(result.get("text") or ""), RUNTIME_VISION_ANALYSIS_MAX_CHARS)
            usage = ensure_dict(result.get("usage"))
            return {
                **base,
                "status": "analyzed",
                "text": text,
                "provider": str(result.get("provider") or ""),
                "providerLabel": str(result.get("providerLabel") or result.get("provider_label") or ""),
                "model": str(result.get("model") or ""),
                "source": str(result.get("source") or "visionProfile"),
                "usage": usage,
            }
        reason = str(result.get("reason") or "no_vision_model")
        return {
            **base,
            "status": "unconfigured",
            "reason": reason,
            "notice": (
                "（当前主模型无法识图，也没有配置可用的视觉模型：本轮附带的图片没有被读取。"
                "可在 设置 > 模型与 Provider 里配置一个视觉模型，之后图片会自动交给它分析。）"
            ),
        }

    def runtime_message(
        self,
        params: dict[str, Any] | None = None,
        agent_name: str = "desktop-agent",
    ) -> dict[str, Any]:
        params = dict(params or {})
        self._signal_background_activity("runtime_message")
        try:
            with self.runtime_planner.bind_turn(params) as metadata:
                params["_contextCompactionLimit"] = metadata.verified_context_limit
                params["_plannerAttemptLabel"] = metadata.planner_label
                with self._desktop.runtime_turn_context(params):
                    return self._runtime_message_impl(params, agent_name=agent_name)
        except DesktopActionBrokerError as exc:
            raise AgentGatewayError(str(exc), status_code=exc.status_code) from exc

    def _runtime_message_impl(
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
        goal_delivery_id = str(params.get("goal_delivery_id") or params.get("goalDeliveryId") or "").strip()
        self._desktop.bind_runtime_identity(
            session_id=session_id,
            turn_id=turn_id,
            client_turn_id=client_turn_id,
        )
        history = [entry for entry in ensure_list(params.get("history")) if isinstance(entry, dict)]
        attachments = normalize_runtime_attachments(params.get("attachments"))
        params["_runtimeAttachments"] = attachments
        if history:
            self._runtime_session_state.restore_session(session_id, history, now)
        project_root = str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "").strip()
        observe = self.runtime_observe(session_id=session_id, project_root=project_root)
        if attachments:
            observe["turn"] = {"attachments": attachments}
        # 图片附件 → 视觉委托：一轮最多分析一次，结果（或诚实提示）注入本轮。
        vision_payload: dict[str, Any] | None = None
        image_attachments = runtime_image_attachments(attachments)
        if image_attachments:
            vision_payload = self._run_vision_analysis(message, image_attachments)
            turn_context = ensure_dict(observe.get("turn"))
            turn_context["visionAnalysis"] = vision_payload
            observe["turn"] = turn_context
        reasoning_trace: dict[str, Any] = {}
        context_usage: dict[str, Any] = {}
        self._runtime_session_state.set_stream_context(
            {
                "sessionId": session_id,
                "turnId": turn_id,
                "clientTurnId": client_turn_id,
            }
        )
        self._runtime_run_ledger.append(
            {
                "event": "runtime_turn_started",
                "status": "running",
                "agent": agent_name,
                "sessionId": session_id,
                "turnId": turn_id,
                "clientTurnId": client_turn_id,
                "goalDeliveryId": goal_delivery_id,
                "messageSummary": summarize_text(message),
                "attachmentCount": len(attachments),
                "provider": params.get("provider") or "",
                "providerLabel": params.get("providerLabel") or params.get("provider_label") or "",
                "model": params.get("model") or "",
                "projectRoot": project_root,
                "computerUseRequested": bool(params.get("_computerUseRequested")),
                "computerUseVisualTheme": str(params.get("_computerUseVisualTheme") or "light"),
                "computerUseVisualAccent": self._runtime_run_ledger.normalize_visual_accent(
                    params.get("_computerUseVisualAccent")
                ),
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
        if vision_payload is not None:
            # 带标签的视觉分析 run step：记录真实执行的 provider/model 与该次
            # 调用自己的 token 用量（不进聊天 contextUsage）。
            steps.append(
                {
                    "index": 0,
                    "kind": "vision",
                    "tool": "vision_analysis",
                    "summary": summarize_text(str(vision_payload.get("text") or vision_payload.get("notice") or ""), 240),
                    "status": vision_payload.get("status") or "",
                    "provider": vision_payload.get("provider") or "",
                    "providerLabel": vision_payload.get("providerLabel") or "",
                    "model": vision_payload.get("model") or "",
                    "source": vision_payload.get("source") or "",
                    "usage": ensure_dict(vision_payload.get("usage")),
                    "imageCount": vision_payload.get("imageCount") or 0,
                }
            )
        successful_actions: set[tuple[str, str]] = set()
        repeated_failure_guard = RepeatedFailureGuard()
        shell_payload: dict[str, Any] | None = None
        skill_payload: dict[str, Any] | None = None
        write_payload: dict[str, Any] | None = None
        approval_id = ""
        first_plan: dict[str, Any] | None = None
        last_plan: dict[str, Any] = {}
        iterations = 0
        cap_reached = False
        tool_call_cap_reached = False
        tool_calls_used = 0
        runtime_exposure_layer = EXPOSURE_LAYER_PLANNING
        remaining_action: dict[str, Any] | None = None
        runtime_compaction: dict[str, Any] | None = None
        runtime_compaction_attempted = False
        runtime_compaction_usage_checkpoint: dict[str, Any] | None = None

        def discard_runtime_compaction_for_cancel() -> None:
            nonlocal runtime_compaction
            if runtime_compaction_usage_checkpoint is not None:
                context_usage.clear()
                context_usage.update(runtime_compaction_usage_checkpoint)
            if runtime_compaction is not None:
                runtime_compaction = planner_policy.runtime_compaction_cancelled_view(runtime_compaction)

        if bool(params.get("_computerUseRequested")) and not self._runtime_session_state.desktop_bootstrap_completed(
            session_id
        ):
            bootstrap_params: dict[str, Any] = {
                "action": "computer_use",
                "prompt": "Discover applications and windows for this user-started Computer Use turn.",
                "sessionId": session_id,
                "clientTurnId": client_turn_id,
                "params": {
                    "operation": "sequence",
                    "steps": [
                        {"operation": "list_apps", "limit": 80},
                        {"operation": "list_windows", "limit": 30},
                    ],
                },
            }
            if project_root:
                bootstrap_params["projectRoot"] = project_root
            bootstrap_payload = self._runtime_skill_executor.execute(
                "vrcforge_agent_desktop_action",
                bootstrap_params,
                agent_name,
            )
            tool_calls_used += 1
            skill_payload = bootstrap_payload
            bootstrap_step: dict[str, Any] = {
                "tool": "vrcforge_agent_desktop_action",
                "kind": "skill",
                "status": bootstrap_payload.get("status"),
                "result": bootstrap_payload.get("result"),
            }
            bootstrap_vision = self._desktop_action_vision_analysis(message, bootstrap_payload.get("result"))
            if bootstrap_vision is not None:
                bootstrap_step["desktopVision"] = bootstrap_vision
            loop_state.append(bootstrap_step)
            self._runtime_session_state.record_desktop_bootstrap(
                session_id,
                now=utc_now_iso(),
                status_summary=summarize_text(str(bootstrap_payload.get("status") or "unknown"), 80),
                result_summary=summarize_params(bootstrap_payload.get("result")),
            )
            steps.append(
                {
                    "index": len(steps),
                    "kind": "skill",
                    "tool": "vrcforge_agent_desktop_action",
                    "summary": "Discovered the initial desktop applications and windows.",
                    "status": bootstrap_payload.get("status") or "",
                }
            )

        for step_index in range(RUNTIME_AGENT_MAX_STEPS):
            if self._runtime_session_state.consume_cancel_request(
                session_id=session_id,
                turn_id=turn_id,
                client_turn_id=client_turn_id,
            ):
                discard_runtime_compaction_for_cancel()
                last_plan = {
                    "summary": "Runtime turn was cancelled by the user.",
                    "reply": "Request cancelled.",
                    "planner": "runtime",
                    "nextStep": "cancelled",
                }
                break
            if step_index > 0:
                usage_before_compaction = dict(context_usage)
                history, compaction_result, compaction_blocked = self.runtime_planner.maybe_compact_runtime_history(
                    message=message,
                    params=params,
                    observe=observe,
                    history=history,
                    loop_state=loop_state,
                    context_usage=context_usage,
                    attempt_compaction=not runtime_compaction_attempted,
                    runtime_exposure_layer=runtime_exposure_layer,
                )
                if compaction_result is not None:
                    runtime_compaction_attempted = True
                    if compaction_result.get("applied"):
                        if runtime_compaction_usage_checkpoint is None:
                            runtime_compaction_usage_checkpoint = usage_before_compaction
                        runtime_compaction = compaction_result
                    elif runtime_compaction is None or not runtime_compaction.get("applied"):
                        runtime_compaction = compaction_result
                    elif compaction_blocked:
                        runtime_compaction = {**runtime_compaction, "blocked": True}
                if self._runtime_session_state.consume_cancel_request(
                    session_id=session_id,
                    turn_id=turn_id,
                    client_turn_id=client_turn_id,
                ):
                    discard_runtime_compaction_for_cancel()
                    last_plan = {
                        "summary": "Runtime turn was cancelled by the user.",
                        "reply": "Request cancelled.",
                        "planner": "runtime",
                        "nextStep": "cancelled",
                    }
                    break
                if compaction_blocked:
                    last_plan = {
                        "summary": "Context compaction could not create enough safe headroom.",
                        "reply": "VRCForge paused before another model request because the context was at its safety limit and compaction did not create enough headroom. The original conversation is still intact; retry manual compaction or switch to a model with a larger verified context window.",
                        "planner": "runtime",
                        "nextStep": "context_compaction_required",
                    }
                    iterations += 1
                    break
            plan = self.runtime_planner.plan_agent_turn(
                message,
                params,
                observe,
                history,
                loop_state=loop_state,
                context_usage=context_usage,
                reasoning_trace=reasoning_trace,
                exposure_layer=runtime_exposure_layer,
            )
            iterations += 1
            if self._runtime_session_state.consume_cancel_request(
                session_id=session_id,
                turn_id=turn_id,
                client_turn_id=client_turn_id,
            ):
                discard_runtime_compaction_for_cancel()
                last_plan = {
                    "summary": "Runtime turn was cancelled by the user.",
                    "reply": "Request cancelled.",
                    "planner": "runtime",
                    "nextStep": "cancelled",
                }
                break
            last_plan = plan
            if first_plan is None:
                first_plan = plan

            planned_tool_name = str(plan.get("writeTool") or plan.get("skillTool") or "").strip()
            planned_tool = self._tools.get(planned_tool_name)
            planning_selected_write = bool(
                plan.get("enterExecution")
                or planned_tool_name in self._write_handlers
                or (planned_tool is not None and planned_tool.write)
            )
            if runtime_exposure_layer == EXPOSURE_LAYER_PLANNING and planning_selected_write:
                runtime_exposure_layer = EXPOSURE_LAYER_EXECUTION
                loop_state.append(
                    {
                        "tool": "exposure_layer",
                        "kind": "phase",
                        "status": "entered_execution",
                        "result": {"exposureLayer": runtime_exposure_layer},
                    }
                )
                steps.append(
                    {
                        "index": len(steps),
                        "kind": "phase",
                        "tool": "exposure_layer",
                        "summary": "Entered execution mode after an explicit project-change request.",
                        "status": "entered_execution",
                    }
                )
                continue

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

            # A turn may still ask the planner for a terminal reply after the
            # third tool result, but a fourth tool call is never executed.
            if tool_calls_used >= RUNTIME_AGENT_MAX_TOOL_CALLS:
                tool_call_cap_reached = True
                remaining_action = {
                    "kind": action_kind,
                    "tool": str(plan.get("writeTool") or plan.get("skillTool") or ("shell" if action_kind == "shell" else "")),
                    "summary": str(plan.get("summary") or "").strip(),
                }
                break

            # A successful action is never replayed within the same turn. A
            # failed action may be retried, but only until the bounded failure
            # guard observes the same tool, arguments, and failure class three
            # times in succession.
            if action_key in successful_actions:
                break

            step_tool = ""
            tool_calls_used += 1
            if action_kind == "shell":
                step_tool = "shell"
                step_payload = self.shell.execute(
                    {
                        "command": command,
                        "cwd": params.get("cwd"),
                        "workspace_root": params.get("workspace_root") or params.get("workspaceRoot"),
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "client_turn_id": client_turn_id,
                        "goalDeliveryId": goal_delivery_id,
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
                        "result": summarize_owned_shell_result(step_payload.get("result"))
                        if step_payload.get("result")
                        else None,
                    }
                )
            elif action_kind == "write":
                step_tool = str(plan.get("writeTool") or "")
                step_payload = self._execute_write_request(
                    step_tool,
                    ensure_dict(plan.get("writeParams")),
                    agent_name,
                    goal_delivery_id=goal_delivery_id,
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
                step_params = ensure_dict(plan.get("skillParams"))
                if step_tool == "vrcforge_agent_desktop_action" or step_tool.startswith("vrcforge_progress_") or step_tool == "vrcforge_ask_user":
                    step_params.setdefault("sessionId", session_id)
                    if goal_delivery_id:
                        step_params.setdefault("goalDeliveryId", goal_delivery_id)
                    if project_root:
                        step_params.setdefault("projectRoot", project_root)
                if step_tool == "vrcforge_agent_desktop_action":
                    step_params.setdefault("clientTurnId", client_turn_id)
                if step_tool in {
                    "vrcforge_skill_manifest",
                    "vrcforge_skill_check",
                    "vrcforge_tool_registry",
                }:
                    step_params.setdefault("exposureLayer", runtime_exposure_layer)
                step_payload = self._runtime_skill_executor.execute(
                    step_tool, step_params, agent_name
                )
                skill_payload = step_payload
                loop_step = {
                    "tool": step_tool,
                    "kind": "skill",
                    "status": step_payload.get("status"),
                    "result": step_payload.get("result"),
                }
                if step_tool == "vrcforge_agent_desktop_action":
                    desktop_vision = self._desktop_action_vision_analysis(message, step_payload.get("result"))
                    if desktop_vision is not None:
                        loop_step["desktopVision"] = desktop_vision
                        steps.append(
                            {
                                "index": len(steps),
                                "kind": "vision",
                                "tool": "desktop_vision_analysis",
                                "summary": summarize_text(str(desktop_vision.get("text") or desktop_vision.get("notice") or desktop_vision.get("error") or ""), 240),
                                "status": desktop_vision.get("status") or "",
                                "provider": desktop_vision.get("provider") or "",
                                "providerLabel": desktop_vision.get("providerLabel") or "",
                                "model": desktop_vision.get("model") or "",
                                "source": desktop_vision.get("source") or "",
                                "usage": ensure_dict(desktop_vision.get("usage")),
                                "imageCount": desktop_vision.get("imageCount") or 0,
                            }
                        )
                loop_state.append(loop_step)

            steps.append(
                {
                    # len(steps)：有视觉前置步时循环步顺延，无视觉步时与 step_index 一致。
                    "index": len(steps),
                    "kind": action_kind,
                    "tool": step_tool,
                    "summary": plan.get("summary") or "",
                    "status": step_payload.get("status") or "",
                }
            )

            step_failure_class = runtime_step_failure_class(step_payload)
            if step_failure_class:
                if action_kind == "shell":
                    failure_arguments: Any = {
                        "command": command,
                        "cwd": params.get("cwd"),
                        "workspaceRoot": params.get("workspace_root") or params.get("workspaceRoot"),
                    }
                elif action_kind == "write":
                    failure_arguments = ensure_dict(plan.get("writeParams"))
                else:
                    failure_arguments = step_params
                if repeated_failure_guard.record_failure(
                    step_tool,
                    failure_arguments,
                    step_failure_class,
                ):
                    suppression = repeated_failure_guard.snapshot()
                    steps[-1]["loopSuppressed"] = True
                    steps[-1]["failureClass"] = step_failure_class
                    last_plan = {
                        **plan,
                        "summary": "Repeated tool failure was suppressed.",
                        "reply": (
                            "VRCForge stopped after the same tool call failed three times. "
                            "Review the reported error or change the inputs before retrying."
                        ),
                        "nextStep": "loop_suppressed",
                        "loopSuppression": suppression,
                    }
                    break
            else:
                repeated_failure_guard.record_success()
                successful_actions.add(action_key)

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
            # 不静默收尾：在 reply 里诚实告知「到步数上限、先汇报、可继续」。
            cap_reached = True

        reasoning_trace = ensure_dict(reasoning_trace)
        context_usage = ensure_dict(context_usage)
        first_plan = first_plan or last_plan or {}
        # 单步（含纯回复/未连接）保持与历史一致的顶层 plan 形状；多步才综合成 loop 计划。
        terminal_override = str(last_plan.get("nextStep") or "") in {
            "cancelled",
            "context_compaction_required",
        }
        top_plan = last_plan if terminal_override else (
            first_plan if iterations <= 1 else self._summarize_loop_plan(
                message, first_plan, last_plan, steps
            )
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
        if tool_call_cap_reached and isinstance(top_plan, dict):
            top_plan["stepLimitReached"] = True
            top_plan["toolCallLimitReached"] = True
            top_plan["toolCallCount"] = tool_calls_used
            top_plan["nextStep"] = "paused"
            top_plan["remainingAction"] = remaining_action or {}
            base_reply = str(top_plan.get("reply") or "").rstrip()
            remaining_label = str((remaining_action or {}).get("tool") or (remaining_action or {}).get("kind") or "next action")
            notice = (
                f"（已到本轮 {RUNTIME_AGENT_MAX_TOOL_CALLS} 次工具调用上限，先停下来汇报。"
                f"尚未执行：{remaining_label}。需要的话再说一声，我接着往下做。）"
            )
            top_plan["reply"] = f"{base_reply}\n\n{notice}".strip() if base_reply else notice

        # Non-analyzed image state is rendered by the structured vision step.
        if (
            vision_payload is not None
            and str(vision_payload.get("status") or "") != "analyzed"
            and isinstance(top_plan, dict)
        ):
            # The transcript renders vision availability as a structured step.
            # Keep fallback notices out of assistant text to avoid duplicate
            # "image not analyzed" messaging in the user-facing chat flow.
            top_plan["visionStatus"] = vision_payload.get("status")

        turn = {
            "id": turn_id,
            "createdAt": now,
            "message": message,
            "observe": summarize_params(observe),
            "plan": top_plan,
        }
        if client_turn_id:
            turn["clientTurnId"] = client_turn_id
        if goal_delivery_id:
            turn["goalDeliveryId"] = goal_delivery_id
        if attachments:
            turn["attachments"] = attachments
        if vision_payload is not None:
            turn["vision"] = vision_payload
        if steps:
            turn["steps"] = steps
        if int(reasoning_trace.get("itemCount") or 0) > 0:
            turn["reasoning"] = reasoning_trace
        if context_usage:
            turn["contextUsage"] = context_usage
        if runtime_compaction:
            turn["contextCompaction"] = planner_policy.runtime_compaction_audit_view(runtime_compaction)
        if shell_payload is not None:
            turn["shell"] = shell_payload
        if skill_payload is not None:
            turn["skill"] = skill_payload
        if write_payload is not None:
            turn["write"] = write_payload

        self._runtime_session_state.append_turn(
            session_id,
            now=now,
            updated_at=utc_now_iso(),
            turn=turn,
        )

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
                "contextUsage": context_usage,
                "contextCompaction": planner_policy.runtime_compaction_audit_view(runtime_compaction),
                "goalDeliveryId": goal_delivery_id,
            }
        )
        self._runtime_run_ledger.append(
            self._runtime_run_ledger.build_run_from_turn(
                event="runtime_turn_completed",
                status=self._runtime_run_ledger.turn_run_status(
                    top_plan=top_plan,
                    shell_payload=shell_payload,
                    skill_payload=skill_payload,
                    write_payload=write_payload,
                    approval_id=approval_id,
                ),
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
                context_usage=context_usage,
                context_compaction=planner_policy.runtime_compaction_audit_view(runtime_compaction),
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
        if goal_delivery_id:
            payload["goalDeliveryId"] = goal_delivery_id
        if attachments:
            payload["attachments"] = attachments
        if vision_payload is not None:
            payload["vision"] = vision_payload
        if steps:
            payload["steps"] = steps
        if int(reasoning_trace.get("itemCount") or 0) > 0:
            payload["reasoning"] = reasoning_trace
        if context_usage:
            payload["contextUsage"] = context_usage
        payload["exposureLayer"] = runtime_exposure_layer
        if runtime_compaction:
            payload["contextCompaction"] = runtime_compaction
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
        self._runtime_session_state.clear_stream_context()
        return payload

    def _execute_write_request(
        self,
        tool_name: str,
        params: dict[str, Any],
        agent_name: str,
        *,
        goal_delivery_id: str = "",
    ) -> dict[str, Any]:
        return self._approval_transactions._impl__execute_write_request(
            tool_name,
            params,
            agent_name,
            goal_delivery_id=goal_delivery_id,
        )

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

    def runtime_observe(self, session_id: str | None = None, project_root: str = "") -> dict[str, Any]:
        config = self.ensure_config()
        user_constraints = self.read_user_constraints()
        session_summary = self._runtime_session_state.session_summary(session_id or "")
        project_root = str(project_root or "").strip()
        pending = [
            item
            for item in self.list_approvals(include_expired=False, project_root=project_root)
            if item.get("status") == "pending"
        ]
        goals = [
            goal
            for goal in self._goal.list_agent_goals(
                limit=8,
                session_id=session_id or "",
                project_root=project_root,
            ).get("goals", [])
            if str(goal.get("status") or "") in {"active", "paused"}
        ]
        memories = self.list_agent_memory(
            limit=12,
            project_root=project_root,
            scope="" if project_root else "user",
        ).get("memories", [])
        return {
            "ok": True,
            "runtime": {
                "alive": True,
                "executionMode": normalize_execution_mode(config.execution_mode),
                "gatewayEnabled": config.enabled,
            },
            "workspaceRoot": str(self.shell.default_workspace_root),
            "userConstraints": self._serialize_user_constraints(user_constraints, include_error=True),
            "approvalQueue": {
                "pendingCount": len(pending),
            },
            "shellExecutor": {
                "available": True,
                "defaultRunner": SHELL_OWNER_RUNNER_NATIVE,
                "fallbackRunner": SHELL_OWNER_RUNNER_POWERSHELL,
                "shell": "powershell",
                "shellRole": "fallback",
                "timeoutSeconds": 120,
            },
            "deterministicPlanner": {
                "available": True,
            },
            "tools": {
                "count": len(self.build_manifest().get("tools", [])),
            },
            "skills": summarize_skill_registry(self.skills.build_skill_registry()),
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
                "turnCount": session_summary["turnCount"],
                "restoredFromTranscript": session_summary["restoredFromTranscript"],
            },
        }

    def get_runtime_session(self, session_id: str) -> dict[str, Any]:
        session = self._runtime_session_state.get_session(session_id)
        if not session:
            raise AgentGatewayError(f"Runtime session was not found: {session_id}", status_code=404)
        return {"ok": True, "session": session}

    def request_runtime_cancel(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        turn_id = str(params.get("turn_id") or params.get("turnId") or "").strip()
        client_turn_id = str(params.get("client_turn_id") or params.get("clientTurnId") or "").strip()
        session_id = str(params.get("session_id") or params.get("sessionId") or "").strip()
        reason = str(params.get("reason") or "user_stop").strip()[:200]
        target_id = turn_id or client_turn_id
        if not target_id and not session_id:
            raise AgentGatewayError("turnId, clientTurnId, or sessionId is required.", status_code=400)
        self._runtime_session_state.mark_cancel_requested(
            session_id=session_id,
            turn_id=turn_id,
            client_turn_id=client_turn_id,
        )
        event = {
            "event": "runtime_turn_cancel_requested",
            "status": "cancel_requested",
            "sessionId": session_id,
            "turnId": turn_id,
            "clientTurnId": client_turn_id,
            "reason": reason,
        }
        cancelled_desktop_action_ids: list[str] = []
        resolved_client_turn_id = client_turn_id
        if turn_id and not resolved_client_turn_id:
            matching_run = next(
                (
                    run
                    for run in self._runtime_run_ledger.list_runs(limit=200).get("runs", [])
                    if str(run.get("turnId") or "") == turn_id
                ),
                None,
            )
            resolved_client_turn_id = str((matching_run or {}).get("clientTurnId") or "")
        for action in self._desktop.list_active_desktop_actions(limit=32).get("actions", []):
            action_id = str(action.get("actionId") or "")
            same_turn = bool(
                resolved_client_turn_id
                and str(action.get("clientTurnId") or "") == resolved_client_turn_id
            )
            same_session = bool(
                session_id
                and not (turn_id or client_turn_id)
                and str(action.get("sessionId") or "") == session_id
            )
            if not action_id or not (same_turn or same_session):
                continue
            try:
                self._desktop.request_desktop_action_cancel(action_id, {"reason": reason})
                cancelled_desktop_action_ids.append(action_id)
            except AgentGatewayError:
                continue
        if cancelled_desktop_action_ids:
            event["cancelledDesktopActionIds"] = cancelled_desktop_action_ids
        self._runtime_run_ledger.append(event)
        return {
            "ok": True,
            "status": "cancel_requested",
            "event": event,
            "cancelledDesktopActionIds": cancelled_desktop_action_ids,
        }

    @staticmethod
    def _desktop_action_operations(params: dict[str, Any]) -> list[str]:
        return DesktopComputerUseService.desktop_action_operations(params)

    @classmethod
    def _desktop_action_is_replay_safe(cls, params: dict[str, Any]) -> bool:
        return DesktopComputerUseService.desktop_action_is_replay_safe(params)

    @classmethod
    def _desktop_action_is_interactive(cls, params: dict[str, Any]) -> bool:
        return DesktopComputerUseService.desktop_action_is_interactive(params)

    @classmethod
    def _desktop_action_params_audit(cls, params: dict[str, Any]) -> dict[str, Any]:
        return DesktopComputerUseService.desktop_action_params_audit(params)

    def _tool_params_audit(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "vrcforge_agent_desktop_action":
            return summarize_params(params)
        desktop_params = ensure_dict(params.get("params"))
        try:
            wait_timeout_ms = int(params.get("waitTimeoutMs") or 60_000)
        except (TypeError, ValueError):
            wait_timeout_ms = 60_000
        return {
            "action": str(params.get("action") or ""),
            "waitForCompletion": params.get("waitForCompletion") is not False,
            "waitTimeoutMs": wait_timeout_ms,
            "desktop": self._desktop_action_params_audit(desktop_params),
        }

    @classmethod
    def _desktop_action_result_audit(cls, result: dict[str, Any]) -> dict[str, Any]:
        return DesktopComputerUseService.desktop_action_result_audit(result)

    @staticmethod
    def _desktop_action_result_payload(value: Any) -> dict[str, Any]:
        return DesktopComputerUseService.desktop_action_result_payload(value)

    def _desktop_action_vision_analysis(self, message: str, value: Any) -> dict[str, Any] | None:
        return self._desktop.desktop_action_vision_analysis(message, value)


    def advanced_settings_state(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        config = config or self.ensure_config()
        return {
            "developerOptionsEnabled": bool(config.developer_options_enabled),
            "developerOptionsEverEnabled": bool(config.developer_options_ever_enabled),
            "computerUseEnabled": bool(config.computer_use_enabled and config.developer_options_enabled),
            "computerUseEverEnabled": bool(config.computer_use_ever_enabled),
            "backgroundGoalNotificationsEnabled": bool(config.background_goal_notifications_enabled),
            "roslynFullAutoEverEnabled": bool(config.roslyn_risk_acknowledged),
        }

    def update_advanced_settings(
        self,
        *,
        developer_options_enabled: bool,
        computer_use_enabled: bool,
        background_goal_notifications_enabled: bool | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            config = self.ensure_config()
            previous = self.advanced_settings_state(config)
            config.developer_options_enabled = bool(developer_options_enabled)
            if config.developer_options_enabled:
                config.developer_options_ever_enabled = True
            config.computer_use_enabled = bool(computer_use_enabled and config.developer_options_enabled)
            if config.computer_use_enabled:
                config.computer_use_ever_enabled = True
            if background_goal_notifications_enabled is not None:
                config.background_goal_notifications_enabled = bool(
                    background_goal_notifications_enabled
                )
            self.save_config(config)
            updated = self.advanced_settings_state(config)
        self.append_audit(
            {
                "event": "advanced_settings_updated",
                "previous": previous,
                "updated": updated,
            }
        )
        return {"ok": True, "schema": "vrcforge.advanced_settings.v1", "settings": updated}

    def replace_agent_progress(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        raw_items = ensure_list(params.get("items") or params.get("plan"))
        project_root = str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "").strip()
        session_id = str(params.get("sessionId") or params.get("session_id") or "").strip()
        normalized_items: list[dict[str, Any]] = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            title = summarize_text(str(item.get("title") or item.get("step") or item.get("content") or "").strip(), 240)
            if not title:
                continue
            status = str(item.get("status") or "pending").strip().lower()
            if status not in {"pending", "in_progress", "running", "completed", "cancelled", "blocked", "deleted"}:
                status = "pending"
            normalized_items.append(
                {
                    "progressId": summarize_text(str(item.get("progressId") or item.get("id") or f"progress-{index + 1}"), 120),
                    "title": title,
                    "summary": summarize_text(str(item.get("summary") or item.get("description") or ""), 1000),
                    "status": status,
                    "order": int(item.get("order") if isinstance(item.get("order"), int) else index),
                    "owner": summarize_text(str(item.get("owner") or "agent"), 80),
                }
            )
        event = {
            "event": "progress_replaced",
            "projectRoot": project_root,
            "sessionId": session_id,
            "items": normalized_items,
        }
        self._append_jsonl(self.agent_progress_log_path, "vrcforge.agent_progress.v1", event)
        return self.list_agent_progress(limit=50, project_root=project_root, session_id=session_id)

    def create_agent_progress(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        title = summarize_text(str(params.get("title") or params.get("step") or params.get("content") or "").strip(), 240)
        if not title:
            raise AgentGatewayError("Progress title is required.", status_code=400)
        progress_id = f"progress_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        event = {
            "event": "progress_created",
            "status": str(params.get("status") or "pending").strip().lower() or "pending",
            "progressId": progress_id,
            "title": title,
            "summary": summarize_text(str(params.get("summary") or params.get("description") or ""), 1000),
            "projectRoot": str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "").strip(),
            "sessionId": str(params.get("sessionId") or params.get("session_id") or "").strip(),
            "owner": summarize_text(str(params.get("owner") or "agent"), 80),
            "order": int(params.get("order")) if isinstance(params.get("order"), int) else 0,
        }
        self._append_jsonl(self.agent_progress_log_path, "vrcforge.agent_progress.v1", event)
        return {"ok": True, "progress": self._find_agent_progress(progress_id, event)}

    @staticmethod
    def _require_agent_item_scope(existing: dict[str, Any], params: dict[str, Any], *, label: str) -> None:
        requested_session = str(params.get("sessionId") or params.get("session_id") or "").strip()
        existing_session = str(existing.get("sessionId") or "").strip()
        if requested_session and requested_session != existing_session:
            raise AgentGatewayError(f"{label} does not belong to this session.", status_code=404)
        requested_project = str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "").strip()
        existing_project = str(existing.get("projectRoot") or "").strip()
        if requested_project and command_safety.normalize_filesystem_path(
            requested_project
        ) != command_safety.normalize_filesystem_path(existing_project):
            raise AgentGatewayError(f"{label} does not belong to this project.", status_code=404)

    def update_agent_progress(self, progress_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        progress_id = str(progress_id or "").strip()
        if not progress_id:
            raise AgentGatewayError("progressId is required.", status_code=400)
        existing = self._find_agent_progress(progress_id, params)
        self._require_agent_item_scope(existing, params, label="Progress item")
        status = str(params.get("status") or existing.get("status") or "pending").strip().lower()
        if status not in {"pending", "in_progress", "running", "completed", "cancelled", "blocked", "deleted"}:
            raise AgentGatewayError("Progress status must be pending, in_progress, running, completed, cancelled, blocked, or deleted.", status_code=400)
        event = {
            "event": "progress_updated",
            "status": status,
            "progressId": progress_id,
            "title": summarize_text(str(params.get("title") or existing.get("title") or ""), 240),
            "summary": summarize_text(str(params.get("summary") or params.get("description") or existing.get("summary") or ""), 1000),
            "projectRoot": str(params.get("projectRoot") or existing.get("projectRoot") or ""),
            "sessionId": str(params.get("sessionId") or existing.get("sessionId") or ""),
            "owner": summarize_text(str(params.get("owner") or existing.get("owner") or "agent"), 80),
            "order": int(params.get("order")) if isinstance(params.get("order"), int) else int(existing.get("order") or 0),
        }
        self._append_jsonl(self.agent_progress_log_path, "vrcforge.agent_progress.v1", event)
        return {"ok": True, "progress": self._find_agent_progress(progress_id, event)}

    def _find_agent_progress(self, progress_id: str, params: dict[str, Any]) -> dict[str, Any]:
        matches = [
            item
            for item in self._project_agent_progress(include_deleted=True).values()
            if str(item.get("progressId") or "") == progress_id
        ]
        requested_session = str(params.get("sessionId") or params.get("session_id") or "").strip()
        requested_project = str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "").strip()
        if requested_session:
            matches = [item for item in matches if str(item.get("sessionId") or "") == requested_session]
        if requested_project:
            normalized_project = command_safety.normalize_filesystem_path(requested_project)
            matches = [
                item
                for item in matches
                if command_safety.normalize_filesystem_path(str(item.get("projectRoot") or ""))
                == normalized_project
            ]
        if not matches:
            raise AgentGatewayError(f"Progress item was not found: {progress_id}", status_code=404)
        if len(matches) > 1:
            raise AgentGatewayError("Progress item id is ambiguous; include sessionId and projectRoot.", status_code=409)
        return matches[0]

    def delete_agent_progress(self, progress_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        return self.update_agent_progress(progress_id, {**params, "status": "deleted"})

    def list_agent_progress(self, *, limit: int = 50, project_root: str = "", session_id: str = "") -> dict[str, Any]:
        progress = list(self._project_agent_progress().values())
        if project_root:
            normalized_project_root = command_safety.normalize_filesystem_path(project_root)
            progress = [
                item
                for item in progress
                if command_safety.normalize_filesystem_path(str(item.get("projectRoot") or ""))
                == normalized_project_root
            ]
        if session_id:
            progress = [item for item in progress if str(item.get("sessionId") or "") == session_id]
        progress.sort(key=lambda item: (int(item.get("order") or 0), str(item.get("createdAt") or "")))
        progress = progress[: max(1, min(limit, AGENT_GOAL_MAX_ITEMS))]
        return {"ok": True, "schema": "vrcforge.agent_progress.v1", "items": [redact_sensitive(item) for item in progress], "count": len(progress)}

    def create_agent_memory(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            payload = self._agent_memory_store.create_agent_memory(params)
        except ValueError as exc:
            raise AgentGatewayError(str(exc), status_code=400) from exc
        except OSError as exc:
            raise AgentGatewayError("Memory storage is unavailable.", status_code=503) from exc
        return {**payload, "memory": redact_sensitive(payload["memory"])}

    def delete_agent_memory(self, memory_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            payload = self._agent_memory_store.delete_agent_memory(memory_id, params)
        except ValueError as exc:
            raise AgentGatewayError(str(exc), status_code=400) from exc
        except KeyError as exc:
            raise AgentGatewayError(f"Memory was not found: {memory_id}", status_code=404) from exc
        except OSError as exc:
            raise AgentGatewayError("Memory storage is unavailable.", status_code=503) from exc
        return {**payload, "memory": redact_sensitive(payload["memory"])}

    def clear_agent_memory(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self._agent_memory_store.clear_agent_memory(params)
        except ValueError as exc:
            raise AgentGatewayError(str(exc), status_code=400) from exc
        except OSError as exc:
            raise AgentGatewayError("Memory storage is unavailable.", status_code=503) from exc

    def list_agent_memory(self, *, limit: int = 50, project_root: str = "", scope: str = "") -> dict[str, Any]:
        try:
            payload = self._agent_memory_store.list_agent_memory(
                limit=min(limit, AGENT_MEMORY_MAX_ITEMS),
                project_root=project_root,
                scope=scope,
            )
        except (ValueError, OSError) as exc:
            raise AgentGatewayError("Memory storage is unavailable.", status_code=503) from exc
        memories = [redact_sensitive(memory) for memory in payload["memories"]]
        return {**payload, "memories": memories, "count": len(memories)}


    def create_apply_request(
        self,
        params: dict[str, Any],
        *,
        internal_wrapper: bool = False,
        include_arguments_digest: bool = False,
    ) -> dict[str, Any]:
        return self._approval_transactions._impl_create_apply_request(
            params,
            internal_wrapper=internal_wrapper,
            include_arguments_digest=include_arguments_digest,
        )

    def _auto_execute_approval(self, approval: dict[str, Any]) -> dict[str, Any] | None:
        return self._approval_transactions._impl__auto_execute_approval(
            approval,
        )

    def _matching_project_category_allow_rule(
        self,
        approval: dict[str, Any],
        write_handler: AgentWriteHandler,
        config: AgentGatewayConfig | None = None,
    ) -> dict[str, str] | None:
        return self._approval_transactions._impl__matching_project_category_allow_rule(
            approval,
            write_handler,
            config,
        )

    @staticmethod
    def _write_handler_allows_future_category(
        write_handler: AgentWriteHandler, approval: dict[str, Any]
    ) -> bool:
        if not write_handler.allow_future_category or not write_handler.approval_category:
            return False
        target_tool = str(approval.get("targetTool") or write_handler.name).lower()
        if any(token in target_tool for token in SCOPED_ALLOW_RULE_FORBIDDEN_TOKENS):
            return False
        if normalize_risk_level(str(approval.get("riskLevel") or write_handler.risk_level)) in {"high", "critical"}:
            return False
        return not bool(approval.get("requiresExplicitApproval"))

    def _scoped_rule_execute_approval(
        self, approval: dict[str, Any], rule: dict[str, str]
    ) -> dict[str, Any] | None:
        return self._approval_transactions._impl__scoped_rule_execute_approval(
            approval,
            rule,
        )

    def apply_approved(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._approval_transactions._impl_apply_approved(
            params,
        )

    def list_approvals(
        self,
        include_expired: bool = True,
        project_root: str = "",
        global_only: bool = False,
    ) -> list[dict[str, Any]]:
        return self._approval_transactions._impl_list_approvals(
            include_expired,
            project_root,
            global_only,
        )

    def approve(
        self,
        approval_id: str,
        *,
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> dict[str, Any]:
        return self._approval_transactions._impl_approve(
            approval_id,
            expected_project_root=expected_project_root,
            global_only=global_only,
        )

    def approve_with_project_category_rule(
        self,
        approval_id: str,
        *,
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> dict[str, Any]:
        return self._approval_transactions._impl_approve_with_project_category_rule(
            approval_id,
            expected_project_root=expected_project_root,
            global_only=global_only,
        )

    def reject(
        self,
        approval_id: str,
        *,
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> dict[str, Any]:
        return self._approval_transactions._impl_reject(
            approval_id,
            expected_project_root=expected_project_root,
            global_only=global_only,
        )

    def recent_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._approval_transactions._impl_recent_audit_logs(
            limit,
        )

    def list_checkpoints(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_list_checkpoints(
            params,
        )

    def inspect_checkpoint_storage(self) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_inspect_checkpoint_storage()

    def _inspect_checkpoint_storage_locked(self) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__inspect_checkpoint_storage_locked()

    def repair_checkpoint_storage(self, *, expected_snapshot: str = "") -> dict[str, Any]:
        return self._checkpoint_recovery._impl_repair_checkpoint_storage(
            expected_snapshot=expected_snapshot,
        )

    def _list_checkpoints_locked(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__list_checkpoints_locked(
            params,
        )

    def checkpoint_archive_usage(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_checkpoint_archive_usage(
            config,
        )

    def _checkpoint_archive_usage_locked(self, config: AgentGatewayConfig | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__checkpoint_archive_usage_locked(
            config,
        )

    def _checkpoint_archive_labels(self) -> dict[str, str]:
        return self._checkpoint_recovery._impl__checkpoint_archive_labels()

    def delete_checkpoint_archives(self, checkpoint_ids: Any) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_delete_checkpoint_archives(
            checkpoint_ids,
        )

    def _delete_checkpoint_archives_locked(self, checkpoint_ids: Any) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__delete_checkpoint_archives_locked(
            checkpoint_ids,
        )

    def relocate_checkpoint_archives(self, target_directory: Any) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_relocate_checkpoint_archives(
            target_directory,
        )

    def _relocate_checkpoint_archives_locked(self, target_directory: Any) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__relocate_checkpoint_archives_locked(
            target_directory,
        )

    def _rewrite_checkpoint_archive_paths(self, id_to_new_path: dict[str, str]) -> int:
        return self._checkpoint_recovery._impl__rewrite_checkpoint_archive_paths(
            id_to_new_path,
        )

    def _remove_old_relocate_parents(self, start: Path, root: Path) -> None:
        return self._checkpoint_recovery._impl__remove_old_relocate_parents(
            start,
            root,
        )

    def prune_checkpoint_archives(
        self,
        max_size_mb: int | None = None,
        *,
        protected_checkpoint_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_prune_checkpoint_archives(
            max_size_mb,
            protected_checkpoint_ids=protected_checkpoint_ids,
        )

    def _prune_checkpoint_archives_locked(
        self,
        max_size_mb: int | None = None,
        *,
        protected_checkpoint_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__prune_checkpoint_archives_locked(
            max_size_mb,
            protected_checkpoint_ids=protected_checkpoint_ids,
        )

    def list_interrupted_apply_recoveries(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_list_interrupted_apply_recoveries(
            params,
        )

    def preview_interrupted_apply_recovery(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_preview_interrupted_apply_recovery(
            params,
        )

    def export_interrupted_apply_incident_bundle(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_export_interrupted_apply_incident_bundle(
            params,
        )

    def resolve_interrupted_apply_recovery(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_resolve_interrupted_apply_recovery(
            params,
        )

    def list_adjustment_checkpoints(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_list_adjustment_checkpoints(
            params,
        )

    def get_adjustment_checkpoint(self, entry_id: str) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_get_adjustment_checkpoint(
            entry_id,
        )

    def create_adjustment_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_create_adjustment_checkpoint(
            params,
        )

    def update_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_update_adjustment_checkpoint(
            entry_id,
            params,
        )

    def delete_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_delete_adjustment_checkpoint(
            entry_id,
            params,
        )

    def select_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_select_adjustment_checkpoint(
            entry_id,
            params,
        )

    def overwrite_adjustment_checkpoint(self, entry_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_overwrite_adjustment_checkpoint(
            entry_id,
            params,
        )

    def preview_restore_adjustment_checkpoint(self, entry_id: str) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_preview_restore_adjustment_checkpoint(
            entry_id,
        )

    def get_selected_adjustment_checkpoints(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_get_selected_adjustment_checkpoints(
            params,
        )

    def _normalize_adjustment_selection_slot(self, value: Any) -> str:
        return self._checkpoint_recovery._impl__normalize_adjustment_selection_slot(
            value,
        )

    def preview_restore_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_preview_restore_checkpoint(
            params,
        )

    def _preview_restore_checkpoint_locked(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__preview_restore_checkpoint_locked(
            params,
        )

    def restore_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl_restore_checkpoint(
            params,
        )

    def _restore_checkpoint_locked(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__restore_checkpoint_locked(
            params,
        )

    def _call_write_handler(
        self,
        write_handler: AgentWriteHandler,
        target_tool: str,
        approval_id: str,
        checkpoint: dict[str, Any] | None,
        arguments: dict[str, Any],
        handler_arguments_digest: str,
        frozen_execution_plan: dict[str, Any],
    ) -> Any:
        return self._approval_transactions._impl__call_write_handler(
            write_handler,
            target_tool,
            approval_id,
            checkpoint,
            arguments,
            handler_arguments_digest,
            frozen_execution_plan,
        )

    def _create_pre_write_checkpoint(self, approval: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any] | None:
        return self._approval_transactions._impl__create_pre_write_checkpoint(
            approval,
            arguments,
        )

    def _create_pre_write_checkpoint_locked(
        self,
        approval: dict[str, Any],
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self._approval_transactions._impl__create_pre_write_checkpoint_locked(
            approval,
            arguments,
        )

    def _create_project_chat_checkpoint(self, project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__create_project_chat_checkpoint(
            project_root,
            record,
        )

    def _create_project_chat_checkpoint_locked(self, project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__create_project_chat_checkpoint_locked(
            project_root,
            record,
        )

    def _create_archive_checkpoint(self, project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__create_archive_checkpoint(
            project_root,
            record,
        )

    def _create_local_state_checkpoint(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__create_local_state_checkpoint(
            record,
        )

    def _checkpoint_project_key(self, project_root: Path) -> str:
        return self._checkpoint_recovery._impl__checkpoint_project_key(
            project_root,
        )

    def _resolve_checkpoint_archive_path(self, checkpoint: dict[str, Any], expected_strategy: str) -> Path:
        return self._checkpoint_recovery._impl__resolve_checkpoint_archive_path(
            checkpoint,
            expected_strategy,
        )

    def _normalize_project_archive_member(self, name: str, allowed_roots: set[str]) -> str:
        return self._checkpoint_recovery._impl__normalize_project_archive_member(
            name,
            allowed_roots,
        )

    def _project_chat_checkpoint_source(self, checkpoint: dict[str, Any]) -> Path:
        return self._checkpoint_recovery._impl__project_chat_checkpoint_source(
            checkpoint,
        )

    def _read_project_chat_checkpoint_bytes(self, checkpoint: dict[str, Any]) -> bytes | None:
        return self._checkpoint_recovery._impl__read_project_chat_checkpoint_bytes(
            checkpoint,
        )

    def _preview_project_chat_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__preview_project_chat_checkpoint(
            checkpoint,
        )

    def _preview_project_chat_checkpoint_locked(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__preview_project_chat_checkpoint_locked(
            checkpoint,
        )

    def _restore_project_chat_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__restore_project_chat_checkpoint(
            checkpoint,
        )

    def _restore_project_chat_checkpoint_locked(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__restore_project_chat_checkpoint_locked(
            checkpoint,
        )

    def _preview_archive_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__preview_archive_checkpoint(
            checkpoint,
        )

    def _preview_local_state_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__preview_local_state_checkpoint(
            checkpoint,
        )

    def _restore_archive_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__restore_archive_checkpoint(
            checkpoint,
        )

    def _restore_local_state_checkpoint(
        self,
        checkpoint: dict[str, Any],
        expected_current_state_digest: str = "",
    ) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__restore_local_state_checkpoint(
            checkpoint,
            expected_current_state_digest,
        )

    def _build_checkpoint_rollback_coverage_audit(
        self,
        checkpoint: dict[str, Any],
        phase: str,
        restore_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__build_checkpoint_rollback_coverage_audit(
            checkpoint,
            phase,
            restore_payload,
        )

    def _build_local_state_rollback_coverage_audit(
        self,
        checkpoint: dict[str, Any],
        phase: str,
        restore_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__build_local_state_rollback_coverage_audit(
            checkpoint,
            phase,
            restore_payload,
        )

    def _build_project_chat_rollback_coverage_audit(
        self,
        checkpoint: dict[str, Any],
        phase: str,
        restore_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__build_project_chat_rollback_coverage_audit(
            checkpoint,
            phase,
            restore_payload,
        )

    def _checkpoint_framework_package_snapshot(self, project_root: Path | None) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__checkpoint_framework_package_snapshot(
            project_root,
        )

    def _read_package_dependency_file(self, path: Path) -> tuple[dict[str, Any], str]:
        return self._checkpoint_recovery._impl__read_package_dependency_file(
            path,
        )

    def _stored_checkpoint_framework_package_snapshot(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__stored_checkpoint_framework_package_snapshot(
            checkpoint,
        )

    def _local_state_checkpoint_roots(self) -> dict[str, Path]:
        return self._checkpoint_recovery._impl__local_state_checkpoint_roots()

    def _local_state_archive_contents(self) -> dict[str, tuple[int, int]]:
        return self._checkpoint_recovery._impl__local_state_archive_contents()

    def _validate_local_state_archive_member(self, name: str) -> None:
        return self._checkpoint_recovery._impl__validate_local_state_archive_member(
            name,
        )

    def _cleanup_checkpoint_restore_unity_caches(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__cleanup_checkpoint_restore_unity_caches(
            checkpoint,
        )

    def _checkpoint_touches_packages(self, checkpoint: dict[str, Any]) -> bool:
        return self._checkpoint_recovery._impl__checkpoint_touches_packages(
            checkpoint,
        )

    def _checkpoint_touches_top_level(self, checkpoint: dict[str, Any], top_level: str) -> bool:
        return self._checkpoint_recovery._impl__checkpoint_touches_top_level(
            checkpoint,
            top_level,
        )

    def _resolve_checkpoint_project_root(self, arguments: dict[str, Any]) -> Path | None:
        return self._checkpoint_recovery._impl__resolve_checkpoint_project_root(
            arguments,
        )

    def _checkpoint_available(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__checkpoint_available(
            checkpoint,
        )

    def _append_checkpoint(self, record: dict[str, Any]) -> None:
        return self._checkpoint_recovery._impl__append_checkpoint(
            record,
        )

    def _checkpoint_archive_files(self) -> list[dict[str, Any]]:
        return self._checkpoint_recovery._impl__checkpoint_archive_files()

    def _protected_checkpoint_archive_ids(
        self,
        *,
        include_recent: bool = False,
        archives: list[dict[str, Any]] | None = None,
    ) -> set[str]:
        return self._checkpoint_recovery._impl__protected_checkpoint_archive_ids(
            include_recent=include_recent,
            archives=archives,
        )

    def _remove_empty_checkpoint_archive_parents(self, start: Path) -> None:
        return self._checkpoint_recovery._impl__remove_empty_checkpoint_archive_parents(
            start,
        )

    def _read_checkpoint_entries(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._checkpoint_recovery._impl__read_checkpoint_entries(
            limit,
        )

    def _load_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        return self._checkpoint_recovery._impl__load_checkpoint(
            checkpoint_id,
        )

    def _read_apply_recovery_entries(self, limit: int = 1000) -> list[dict[str, Any]]:
        return self._checkpoint_recovery._impl__read_apply_recovery_entries(
            limit,
        )

    def _append_apply_recovery_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__append_apply_recovery_entry(
            entry,
        )

    def _coalesced_apply_recoveries(self, *, include_resolved: bool = False) -> list[dict[str, Any]]:
        return self._checkpoint_recovery._impl__coalesced_apply_recoveries(
            include_resolved=include_resolved,
        )

    def _active_apply_recoveries(self) -> list[dict[str, Any]]:
        return self._checkpoint_recovery._impl__active_apply_recoveries()

    @contextmanager
    def local_state_write_guard(self) -> Iterator[None]:
        """Serialize direct local-state writes with checkpoint/recovery I/O."""

        with self._checkpoint_storage_lock:
            active = [
                recovery
                for recovery in self._active_apply_recoveries()
                if str(recovery.get("targetTool") or "")
                in LOCAL_STATE_CHECKPOINT_TARGETS
            ]
            if active:
                raise AgentGatewayError(
                    "A skill-package recovery is active. Restore or resolve it before changing local skill state.",
                    status_code=409,
                )
            yield

    def has_in_flight_project_write(self) -> bool:
        return self._approval_transactions._impl_has_in_flight_project_write()

    def try_acquire_background_project_read(self, token: str) -> bool:
        return self._approval_transactions._impl_try_acquire_background_project_read(
            token,
        )

    def release_background_project_read(self, token: str) -> bool:
        return self._approval_transactions._impl_release_background_project_read(
            token,
        )

    @staticmethod
    def _validated_memory_evidence_for_applied_write(
        approval: dict[str, Any],
        arguments: dict[str, Any],
        result: Any,
    ) -> dict[str, Any] | None:
        """Project one allowlisted write readback into a prose-safe evidence row."""

        if str(approval.get("targetTool") or "") != "vrcforge_set_gameobject_active":
            return None
        if not isinstance(result, dict):
            return None
        requested_active = arguments.get("active", arguments.get("isActive"))
        if not isinstance(requested_active, bool):
            return None
        object_path = str(
            arguments.get("gameObjectPath")
            or arguments.get("game_object_path")
            or ""
        ).strip()
        normalized_object_path = "/".join(
            segment.strip()
            for segment in object_path.replace("\\", "/").split("/")
            if segment.strip()
        )
        if not normalized_object_path:
            return None
        if (
            result.get("ok") is False
            or result.get("action") != "set_gameobject_active"
            or result.get("preview") is not False
            or not isinstance(result.get("newActive"), bool)
            or result.get("newActive") is not requested_active
        ):
            return None
        project_root = str(arguments.get("projectRoot") or "").strip()
        approval_id = str(approval.get("id") or "").strip()
        completed_at = str(approval.get("appliedAt") or "").strip()
        if not project_root or not approval_id or not completed_at:
            return None
        object_ref = hashlib.sha256(
            normalized_object_path.encode("utf-8")
        ).hexdigest()[:16]
        summary = (
            f"Set scene object ref {object_ref} active state to enabled."
            if requested_active
            else f"Set scene object ref {object_ref} active state to disabled."
        )
        return {
            "schema": "vrcforge.memory_evidence.v1",
            "applied": True,
            "validated": True,
            "projectRoot": project_root,
            "summary": summary,
            "summaryDigest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            "objectRef": object_ref,
            "sourceId": approval_id,
            "revision": completed_at,
            "completedAt": completed_at,
        }

    def _apply_recovery_blocks_writes(self, recovery: dict[str, Any]) -> bool:
        return self._approval_transactions._impl__apply_recovery_blocks_writes(
            recovery,
        )

    def _select_apply_recovery(self, params: dict[str, Any], *, include_resolved: bool = False) -> dict[str, Any] | None:
        return self._checkpoint_recovery._impl__select_apply_recovery(
            params,
            include_resolved=include_resolved,
        )

    def _start_apply_recovery(
        self,
        approval: dict[str, Any],
        arguments: dict[str, Any],
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        return self._approval_transactions._impl__start_apply_recovery(
            approval,
            arguments,
            checkpoint,
        )

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
        return self._approval_transactions._impl__finish_apply_recovery(
            recovery,
            status=status,
            resolution=resolution,
            error=error,
            note=note,
            result_summary=result_summary,
        )

    def _resolve_apply_recoveries_for_checkpoint(
        self,
        checkpoint_id: str,
        *,
        resolution: str,
        restore_payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._approval_transactions._impl__resolve_apply_recoveries_for_checkpoint(
            checkpoint_id,
            resolution=resolution,
            restore_payload=restore_payload,
        )

    def _classify_apply_recovery_incident(self, text: str, target_tool: str = "") -> str:
        return self._checkpoint_recovery._impl__classify_apply_recovery_incident(
            text,
            target_tool,
        )

    def _read_adjustment_checkpoint_entries(self) -> list[dict[str, Any]]:
        return self._checkpoint_recovery._impl__read_adjustment_checkpoint_entries()

    def _write_adjustment_checkpoint_entries(self, entries: list[dict[str, Any]]) -> None:
        return self._checkpoint_recovery._impl__write_adjustment_checkpoint_entries(
            entries,
        )

    def _load_adjustment_checkpoint(self, entry_id: str) -> dict[str, Any] | None:
        return self._checkpoint_recovery._impl__load_adjustment_checkpoint(
            entry_id,
        )

    def _normalize_adjustment_checkpoint_kind(self, value: Any, *, required: bool) -> str:
        return self._checkpoint_recovery._impl__normalize_adjustment_checkpoint_kind(
            value,
            required=required,
        )

    def _resolve_or_create_adjustment_base_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__resolve_or_create_adjustment_base_checkpoint(
            params,
        )

    def _build_adjustment_checkpoint_entry(
        self,
        params: dict[str, Any],
        checkpoint: dict[str, Any],
        *,
        kind: str,
        existing: dict[str, Any],
    ) -> dict[str, Any]:
        return self._checkpoint_recovery._impl__build_adjustment_checkpoint_entry(
            params,
            checkpoint,
            kind=kind,
            existing=existing,
        )

    def _apply_adjustment_checkpoint_metadata(self, entry: dict[str, Any], params: dict[str, Any]) -> None:
        return self._checkpoint_recovery._impl__apply_adjustment_checkpoint_metadata(
            entry,
            params,
        )

    def _normalize_tags(self, value: Any) -> list[str]:
        return self._checkpoint_recovery._impl__normalize_tags(
            value,
        )

    def _default_adjustment_checkpoint_label(self, kind: str, checkpoint: dict[str, Any]) -> str:
        return self._checkpoint_recovery._impl__default_adjustment_checkpoint_label(
            kind,
            checkpoint,
        )

    def _maybe_record_adjustment_checkpoint(self, record: dict[str, Any]) -> None:
        return self._checkpoint_recovery._impl__maybe_record_adjustment_checkpoint(
            record,
        )

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

    @property
    def audit_log_path(self) -> Path:
        return self.audit_dir / "approvals.jsonl"

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

    def visible_write_targets(
        self,
        config: AgentGatewayConfig | None = None,
        exposure_layer: str = EXPOSURE_LAYER_EXECUTION,
    ) -> list[dict[str, Any]]:
        return self._approval_transactions._impl_visible_write_targets(
            config,
            exposure_layer,
        )

    def _write_handler_rollback_policy(self, handler: AgentWriteHandler) -> dict[str, Any]:
        return self._approval_transactions._impl__write_handler_rollback_policy(
            handler,
        )

    def roslyn_available(self, config: AgentGatewayConfig | None = None) -> bool:
        return False

    def append_audit(self, entry: dict[str, Any]) -> None:
        safe_entry = redact_sensitive({
            "timestamp": utc_now_iso(),
            **entry,
        })
        # 子代理 worker 线程与请求线程会并发追加审计行；
        # 加锁保证每行 JSONL 原子落盘，不互相穿插。
        with self._audit_append_lock:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log_path.open("a", encoding="utf-8") as log_file:
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
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_jsonl_append_boundary_locked(path)
            with path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(safe_entry, ensure_ascii=False, sort_keys=True) + "\n")
                flush_and_fsync(log_file)
        return safe_entry

    @staticmethod
    def _ensure_jsonl_append_boundary_locked(path: Path) -> None:
        """Keep a crash-truncated tail from consuming the next valid event."""

        try:
            if not path.exists() or path.stat().st_size == 0:
                return
            with path.open("r+b") as handle:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) not in {b"\n", b"\r"}:
                    handle.seek(0, os.SEEK_END)
                    handle.write(b"\n")
                    flush_and_fsync(handle)
        except OSError:
            # The following append remains authoritative and will surface its
            # own I/O failure.  This helper must not hide it.
            return

    def _read_jsonl(self, path: Path, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            if not path.exists():
                return []
            try:
                lines = path.read_bytes().splitlines()
            except OSError:
                return []
        if limit <= 0:
            selected_lines = lines
        else:
            selected_lines = lines[-max(1, min(limit, 5000)) :]
        events: list[dict[str, Any]] = []
        for raw_line in selected_lines:
            try:
                line = raw_line.decode("utf-8")
                payload = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def _project_agent_progress(self, *, include_deleted: bool = False) -> dict[str, dict[str, Any]]:
        progress: dict[str, dict[str, Any]] = {}
        deleted: set[str] = set()

        def projection_key(progress_id: str, session_id: str, project_root: str) -> str:
            normalized_root = (
                command_safety.normalize_filesystem_path(project_root) if project_root else ""
            )
            return f"{session_id}\0{normalized_root}\0{progress_id}"

        for event in self._read_jsonl(self.agent_progress_log_path, limit=0):
            event_name = str(event.get("event") or "")
            if event_name == "progress_replaced":
                session_id = str(event.get("sessionId") or "")
                project_root = str(event.get("projectRoot") or "")
                normalized_project_root = (
                    command_safety.normalize_filesystem_path(project_root) if project_root else ""
                )
                for existing_key, existing in list(progress.items()):
                    existing_project = str(existing.get("projectRoot") or "")
                    same_session = str(existing.get("sessionId") or "") == session_id
                    same_project = (
                        command_safety.normalize_filesystem_path(existing_project)
                        == normalized_project_root
                        if normalized_project_root and existing_project
                        else existing_project == project_root
                    )
                    if same_session and same_project:
                        deleted.add(existing_key)
                for item in ensure_list(event.get("items")):
                    if not isinstance(item, dict):
                        continue
                    progress_id = str(item.get("progressId") or item.get("id") or "").strip()
                    if not progress_id:
                        continue
                    item_key = projection_key(progress_id, session_id, project_root)
                    deleted.discard(item_key)
                    previous = progress.get(item_key, {})
                    progress[item_key] = {
                        **previous,
                        **item,
                        "id": progress_id,
                        "progressId": progress_id,
                        "projectRoot": project_root,
                        "sessionId": session_id,
                        "createdAt": previous.get("createdAt") or event.get("createdAt"),
                        "updatedAt": event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt"),
                    }
                continue
            progress_id = str(event.get("progressId") or "").strip()
            if not progress_id:
                continue
            event_session = str(event.get("sessionId") or "")
            event_project = str(event.get("projectRoot") or "")
            item_key = projection_key(progress_id, event_session, event_project)
            if str(event.get("status") or "") == "deleted" or event_name == "progress_deleted":
                deleted.add(item_key)
            previous = progress.get(item_key, {})
            progress[item_key] = {
                **previous,
                **event,
                "id": progress_id,
                "progressId": progress_id,
                "createdAt": previous.get("createdAt") or event.get("createdAt"),
                "updatedAt": event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt"),
            }
        if include_deleted:
            return progress
        return {item_key: item for item_key, item in progress.items() if item_key not in deleted and str(item.get("status") or "") != "deleted"}

    def _project_agent_memory(self, *, include_deleted: bool = False) -> dict[str, dict[str, Any]]:
        return self._agent_memory_store.project(include_deleted=include_deleted)

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
        return self._approval_transactions._impl__inject_user_constraints_for_apply(
            params,
            snapshot,
        )

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


    def _write_auto_manual_approval_reason(self, target_tool: str, arguments: dict[str, Any], preview: Any = None) -> str:
        return self._approval_transactions._impl__write_auto_manual_approval_reason(
            target_tool,
            arguments,
            preview,
        )



















    def _runtime_skill_package_audit_context_locked(self, skill: dict[str, Any]) -> dict[str, Any]:
        """Resolve immutable installed-package identity for a projected skill.

        Projected user skills intentionally remain plain ``SKILL.md`` files, so
        package identity is read from the validated package registry at runtime.
        The projection must still match the package lock before signer identity is
        attached; edited or hand-authored skills therefore keep the legacy audit
        shape instead of being misattributed to a signed package.
        """

        skill_name = normalize_skill_id(str(skill.get("name") or ""))
        storage_value = str(skill.get("storagePath") or "").strip()
        if not skill_name or not storage_value:
            return {}

        package_store = self.user_constraints_path.parent / "skill-packages"
        registry_path = package_store / "registry.json"
        if not registry_path.is_file() or registry_path.is_symlink():
            return {}

        try:
            # Local import keeps the gateway usable in minimal environments that
            # do not expose package management, while the desktop/backend build
            # uses the same validator as import, trust, and revocation flows.
            from skill_packages import SkillPackageService

            storage_path = Path(storage_value)
            storage_resolved = storage_path.resolve(strict=True)
            skills_root = self.skills.user_skills_dir.resolve(strict=True)
            storage_resolved.relative_to(skills_root)
            if not storage_path.is_file() or storage_path.is_symlink():
                return {}
            service = SkillPackageService(package_store, vrcforge_version="0.0.0")
            return service.runtime_audit_context(
                skill_name,
                storage_resolved,
                ensure_string_list(skill.get("supportFiles")),
            )
        except Exception:  # noqa: BLE001 - enrichment must not break legacy skill execution.
            return {}

    def _extract_token(self, headers: dict[str, str], query_params: dict[str, str]) -> str:
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return str(query_params.get("token") or "")

    def _serialize_tool(self, tool: AgentTool, config: AgentGatewayConfig) -> dict[str, Any]:
        model_invocable = not tool.requires_user_activation or self._desktop.computer_use_model_invocable(config)
        return {
            "name": tool.name,
            "description": tool_usage_description(tool.name, tool.description, write=tool.write),
            "category": tool.category,
            "write": tool.write,
            "advanced": tool.advanced,
            "available": self._tool_visible(tool, config),
            "requiresUserActivation": tool.requires_user_activation,
            "modelInvocable": model_invocable,
        }

    def _serialize_tool_registry_entry(self, tool: AgentTool, config: AgentGatewayConfig) -> dict[str, Any]:
        available = self._tool_visible(tool, config)
        model_invocable = not tool.requires_user_activation or self._desktop.computer_use_model_invocable(config)
        risk = self._registry_risk_for_tool(tool)
        requires_approval = tool.write or risk in {"write_request", "advanced_write"}
        return {
            "id": self._registry_tool_id(tool.name),
            "name": tool.name,
            "title": tool.name.replace("vrcforge_", "").replace("_", " ").title(),
            "description": tool_usage_description(tool.name, tool.description, write=tool.write),
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
            "requiresUserActivation": tool.requires_user_activation,
            "modelInvocable": model_invocable,
        }

    def _serialize_write_registry_entry(self, handler: AgentWriteHandler, config: AgentGatewayConfig) -> dict[str, Any]:
        visible = self._write_handler_visible(handler, config)
        available = visible and bool(config.allow_write_requests)
        return {
            "id": self._registry_tool_id(handler.name),
            "name": handler.name,
            "title": handler.name.replace("vrcforge_", "").replace("_", " ").title(),
            "description": tool_usage_description(handler.name, handler.description, write=True),
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

    def _tool_visible(
        self,
        tool: AgentTool,
        config: AgentGatewayConfig,
        exposure_layer: str = EXPOSURE_LAYER_EXECUTION,
    ) -> bool:
        exposure_layer = normalize_exposure_layer(exposure_layer)
        if tool.name in EXTERNAL_AGENT_INTERNAL_TOOLS:
            return False
        if tool.advanced and not self.roslyn_available(config):
            return False
        if tool.write and not config.allow_write_requests:
            return False
        if exposure_layer == EXPOSURE_LAYER_PLANNING and tool.write:
            return False
        return True

    def _write_handler_visible(
        self,
        handler: AgentWriteHandler,
        config: AgentGatewayConfig,
        exposure_layer: str = EXPOSURE_LAYER_EXECUTION,
    ) -> bool:
        if normalize_exposure_layer(exposure_layer) == EXPOSURE_LAYER_PLANNING:
            return False
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
        goal_delivery_id: str = "",
        approved_execution_plan: dict[str, Any] | None = None,
        allow_future_eligible: bool = False,
    ) -> dict[str, Any]:
        return self._approval_transactions._impl__new_approval(
            agent_name,
            target_tool,
            arguments,
            reason,
            preview,
            risk_level,
            user_constraints,
            requires_explicit_approval,
            explicit_approval_reason,
            goal_delivery_id,
            approved_execution_plan,
            allow_future_eligible,
        )

    def _approval_project_root(self, approval: dict[str, Any]) -> str:
        return self._approval_transactions._impl__approval_project_root(
            approval,
        )

    def _ensure_approval_scope(
        self,
        approval: dict[str, Any],
        *,
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> None:
        return self._approval_transactions._impl__ensure_approval_scope(
            approval,
            expected_project_root=expected_project_root,
            global_only=global_only,
        )

    def _set_approval_status(
        self,
        approval_id: str,
        status: str,
        *,
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> dict[str, Any]:
        return self._approval_transactions._impl__set_approval_status(
            approval_id,
            status,
            expected_project_root=expected_project_root,
            global_only=global_only,
        )

    def request_approval_revision(
        self,
        approval_id: str,
        *,
        reason: str = "",
        note: str = "",
        expected_project_root: str = "",
        global_only: bool = False,
    ) -> dict[str, Any]:
        return self._approval_transactions._impl_request_approval_revision(
            approval_id,
            reason=reason,
            note=note,
            expected_project_root=expected_project_root,
            global_only=global_only,
        )

    def _refresh_approval_expiry(self, approval: dict[str, Any]) -> dict[str, Any]:
        return self._approval_transactions._impl__refresh_approval_expiry(
            approval,
        )

    def _load_approval_from_audit(self, approval_id: str) -> dict[str, Any] | None:
        return self._approval_transactions._impl__load_approval_from_audit(
            approval_id,
        )

    def _reconcile_unrecoverable_linked_approval(self, approval_id: str) -> bool:
        return self._approval_transactions._impl__reconcile_unrecoverable_linked_approval(
            approval_id,
        )


def create_agent_mcp_app(
    gateway: AgentGateway,
    *,
    on_pending_approval: Callable[[dict[str, Any]], None] | None = None,
):
    def list_tools(params: Mapping[str, Any]) -> list[dict[str, Any]]:
        exposure_layer = normalize_exposure_layer(params.get("exposureLayer"))
        manifest = gateway.build_manifest(exposure_layer)
        tools = manifest.get("tools")
        if not isinstance(tools, list):
            raise RuntimeError("Agent Gateway manifest did not return a tool list.")
        return [dict(tool) for tool in tools if isinstance(tool, dict)]

    def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = gateway.call_tool(name, arguments, agent_name="mcp-agent")
        if name == "vrcforge_request_apply" and isinstance(result, dict):
            request_result = ensure_dict(result.get("result"))
            approval = ensure_dict(request_result.get("approval"))
            if str(request_result.get("status") or approval.get("status") or "") == "pending" and on_pending_approval:
                try:
                    on_pending_approval(redact_sensitive(dict(approval)))
                except Exception:
                    # UI notification is advisory; it must not alter an MCP
                    # response that has already durably created an approval.
                    pass
        return result

    def validate_bearer(token: str) -> bool:
        config = gateway.ensure_config()
        return bool(config.enabled and config.token and hmac.compare_digest(token, config.token))

    router = Mcp2026Router(
        list_tools,
        call_tool,
        server_name="VRCForge Agent Gateway",
        server_version="1.4.0",
    )
    return create_agent_mcp_2026_asgi_app(
        router,
        bearer_validator=validate_bearer,
        route_path="/mcp",
    )






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


















def summarize_text(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def desktop_screenshot_attachment(path: Path, *, allowed_root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    root = allowed_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Desktop screenshot is outside the managed capture directory.") from exc
    bitmap = resolved.read_bytes()
    if len(bitmap) < 54 or bitmap[:2] != b"BM":
        raise ValueError("Desktop screenshot is not a supported BMP file.")
    pixel_offset = struct.unpack_from("<I", bitmap, 10)[0]
    width, signed_height, planes, bit_count, compression = struct.unpack_from("<iiHHI", bitmap, 18)
    if width <= 0 or signed_height == 0 or planes != 1 or bit_count != 32 or compression != 0:
        raise ValueError("Desktop screenshot must be an uncompressed 32-bit BMP.")
    height = abs(signed_height)
    source_stride = width * 4
    if pixel_offset + source_stride * height > len(bitmap):
        raise ValueError("Desktop screenshot pixel data is incomplete.")
    scale = min(1.0, DESKTOP_VISION_MAX_WIDTH / width, DESKTOP_VISION_MAX_HEIGHT / height)
    target_width = max(1, int(width * scale))
    target_height = max(1, int(height * scale))
    top_down = signed_height < 0
    scanlines = bytearray()
    for target_y in range(target_height):
        source_y = min(height - 1, int(target_y * height / target_height))
        if not top_down:
            source_y = height - 1 - source_y
        row_offset = pixel_offset + source_y * source_stride
        scanlines.append(0)
        for target_x in range(target_width):
            source_x = min(width - 1, int(target_x * width / target_width))
            offset = row_offset + source_x * 4
            blue, green, red = bitmap[offset : offset + 3]
            scanlines.extend((red, green, blue))
    header = struct.pack(">IIBBBBB", target_width, target_height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), 6)) + _png_chunk(b"IEND", b"")
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    if len(data_url) > RUNTIME_ATTACHMENT_DATA_URL_MAX_CHARS:
        raise ValueError("Desktop screenshot is too large for bounded vision analysis.")
    return {
        "id": f"desktop-{stable_hash(str(resolved))[:16]}",
        "name": "current-desktop.png",
        "type": "image/png",
        "size": len(png),
        "payloadKind": "data_url",
        "dataUrl": data_url,
        "replayable": False,
    }


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
        declared_kind = str(raw.get("payloadKind") or raw.get("payload_kind") or "")
        vault_hash = str(raw.get("payloadHash") or raw.get("payload_hash") or "").strip().lower()
        inline_vault_hash = str(raw.get("vaultPayloadHash") or raw.get("vault_payload_hash") or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", inline_vault_hash):
            item["vaultPayloadHash"] = inline_vault_hash
            item["vaultKind"] = summarize_text(str(raw.get("vaultKind") or raw.get("vault_kind") or ""), 32)
        if declared_kind == "vault_file" and re.fullmatch(r"[0-9a-f]{64}", vault_hash):
            # 1.3.2 vault 附件：字节永远不进 prompt/transcript，只保留可复读的
            # 内容寻址引用；检查/物化走 vrcforge_inspect_chat_attachment 与
            # 受监督导入通道。
            item["payloadKind"] = "vault_file"
            item["payloadHash"] = vault_hash
            item["vaultKind"] = summarize_text(str(raw.get("vaultKind") or raw.get("vault_kind") or ""), 32)
            item["replayable"] = True
            attachments.append(item)
            continue
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


def runtime_image_attachments(attachments: Any) -> list[dict[str, Any]]:
    """Return the subset of normalized attachments that are inline images.

    Only bounded data-url payloads with an image MIME type qualify: metadata
    or text attachments never trigger vision delegation, and nothing here
    reads project files on its own (attaching an image stays an explicit
    user action).
    """
    images: list[dict[str, Any]] = []
    for attachment in ensure_list(attachments):
        if not isinstance(attachment, dict):
            continue
        if str(attachment.get("payloadKind") or "") != "data_url":
            continue
        data_url = str(attachment.get("dataUrl") or "")
        mime = str(attachment.get("type") or "").strip().lower()
        if mime.startswith("image/") or data_url.startswith("data:image/"):
            images.append(attachment)
    return images




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


def build_runtime_skill_payload(
    skill: dict[str, Any],
    params: dict[str, Any],
    *,
    support_files: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
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
        "supportFiles": list(support_files or []),
        "validation": skill.get("validation"),
        "availabilityReasons": skill.get("availabilityReasons"),
        "tags": skill.get("tags"),
    }


def _path_is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return True


def _path_has_link_like_parent(path: Path, root: Path) -> bool:
    current = path
    while True:
        if _path_is_link_like(current):
            return True
        if current == root:
            return False
        if root not in current.parents:
            return True
        current = current.parent


def resolve_skill_arguments(instructions: str, arguments: str) -> str:
    text = str(instructions or "")
    if not arguments:
        return text
    text = text.replace("$ARGUMENTS", arguments).replace("{arguments}", arguments)
    parts = command_safety.tokenize_command(arguments)
    for index, value in enumerate(parts, start=1):
        text = text.replace(f"${index}", command_safety.strip_quotes(value))
    return text






_WRITE_INTENT_CN_VERB = re.compile(r"加个|加一个|加上|添加|新建|新增|创建|建个|建一个|挂个|挂一个|放个|增加")
_WRITE_INTENT_EN_VERB = re.compile(r"\b(add|create|new|insert|spawn|make)\b")
_WRITE_INTENT_EN_NOUN = re.compile(r"\b(game ?object|objects?|obj|empty|child)\b")
_WRITE_INTENT_CN_NOUN = ("对象", "物体", "节点")
_OBJECT_NAME_RE = re.compile(
    r"(?:叫做|叫作|叫|名为|命名为|named|name[d]?|called)\s*[\"'“”‘’]?([A-Za-z0-9_\-一-鿿]+)"
)
_SCENE_ROOT_TARGET_RE = re.compile(
    r"(?:活动场景(?:的)?根节点|场景(?:的)?根节点|\b(?:the\s+)?active\s+scene\s+root\b|\b(?:the\s+)?scene\s+root\b)",
    re.IGNORECASE,
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
    scene_root_target = bool(_SCENE_ROOT_TARGET_RE.search(text))
    return {
        "kind": "add_object",
        "objectName": name_match.group(1) if name_match else "GameObject",
        "target": "",
        "targetMode": "scene_root" if scene_root_target else "",
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


def normalize_exposure_layer(value: Any) -> str:
    layer = str(value or EXPOSURE_LAYER_PLANNING).strip().lower()
    if layer not in {EXPOSURE_LAYER_PLANNING, EXPOSURE_LAYER_EXECUTION}:
        raise AgentGatewayError("exposureLayer must be planning or execution.", status_code=400)
    return layer


def tool_usage_description(name: str, summary: str, *, write: bool) -> str:
    text = str(summary or name).strip()
    if all(section in text for section in ("When to use:", "When NOT to use:", "Negative example:")):
        return text
    when_not = (
        "Do not use while planning, for hypothetical or quoted requests, or without an explicit project change request and approval."
        if write
        else "Do not use for general questions, quoted examples, hypothetical requests, or when the user forbids inspection."
    )
    negative = (
        f"Explain {name} conceptually, but do not modify the project."
        if write
        else f"Mention {name} without inspecting the current project."
    )
    return f"When to use: {text}\nWhen NOT to use: {when_not}\nNegative example: {negative}"


def parse_skill_markdown(path: Path, *, max_bytes: int | None = None) -> dict[str, Any]:
    if max_bytes is None:
        text = path.read_text(encoding="utf-8-sig")
    else:
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise AgentGatewayError(
                f"Skill manifest exceeds the {max_bytes}-byte limit.",
                status_code=400,
            )
        text = data.decode("utf-8-sig")
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


def runtime_step_failure_class(payload: Any) -> str:
    """Return one bounded failure class for a runtime tool step."""

    return classify_runtime_step_failure(payload)


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
    - "auto"              自动审批：审批仍然生成并留痕，但删除和项目外路径仍需确认。
    - "roslyn_full_auto"  兼容旧配置名的完全权限：自动审批所有请求，不启用动态代码执行。
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


_BACKGROUND_GOAL_SECRET_FIELDS = {
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
_BACKGROUND_GOAL_PATH_FIELDS = {
    "cwd",
    "directory",
    "file",
    "path",
    "project_path",
    "project_root",
    "projectpath",
    "projectroot",
    "workspace_path",
    "workspace_root",
    "workspacepath",
    "workspaceroot",
}


def redact_background_goal_persistence(value: Any, *, depth: int = 0) -> Any:
    """Redact blocked background output before it enters durable run state."""

    if depth >= 8:
        return "<truncated>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:64]:
            key_text = str(key)
            lowered = key_text.lower()
            if lowered in _BACKGROUND_GOAL_SECRET_FIELDS:
                result[key_text] = "<redacted>"
            elif lowered in _BACKGROUND_GOAL_PATH_FIELDS and isinstance(item, str) and item:
                result[key_text] = "<path redacted>"
            else:
                result[key_text] = redact_background_goal_persistence(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [redact_background_goal_persistence(item, depth=depth + 1) for item in value[:32]]
    if isinstance(value, str):
        return planner_policy.sanitize_planner_observation_text(value, 2_000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return planner_policy.sanitize_planner_observation_text(value, 240)


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


def _parse_utc_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed

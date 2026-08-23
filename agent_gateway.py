from __future__ import annotations

import base64
import contextvars
import hashlib
from itertools import count
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
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Sequence

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
from agent_shell_process_supervisor import ShellSessionPorts

if TYPE_CHECKING:
    from agent_approval_transactions import AgentApprovalTransactionService
    from agent_checkpoint_recovery import AgentCheckpointRecoveryService
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
from agent_runtime_followup_queue import AgentRuntimeFollowupQueue, FollowupQueuePorts
from agent_runtime_run_ledger import AgentRuntimeRunLedger, AgentRuntimeRunLedgerPorts
from agent_runtime_event_projection import project_runtime_turn_event
from agent_runtime_skill_executor import AgentRuntimeSkillExecutor, AgentRuntimeSkillExecutorPorts
from agent_task_loop import (
    AgentTaskLoop,
    approval_task_context,
    canonical_action_id,
    prepare_approval_task_continuation,
    prepare_shell_task_continuation,
    prepare_sub_agent_task_continuation,
)
from agent_tool_result_contract import completion_gate_plan, normalize_agent_tool_result
from external_tool_result_contract import (
    build_external_tool_error,
    external_exception_details,
    external_exception_raw_result,
    external_write_failure_view,
)
from agent_budget_policy import freeze_agent_budget_policy
from agent_general_no_progress import general_read_observation_key
from general_agent_tools import extract_explicit_local_roots
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
from avatar_composition_workflow_skills import AVATAR_COMPOSITION_WORKFLOW_SKILLS
from unity_mcp_core_client import capture_unity_mcp_core_call_audits


ToolHandler = Callable[[dict[str, Any]], Any]
RiskLevelResolver = Callable[[dict[str, Any]], str]
ManualApprovalResolver = Callable[[dict[str, Any], Any], str]
CheckpointPrepareHandler = Callable[[Path, dict[str, Any]], dict[str, Any]]
CompletionVerificationPrepareHandler = Callable[[dict[str, Any]], dict[str, Any]]
CompletionVerificationFinalizeHandler = Callable[
    [dict[str, Any], dict[str, Any], Any],
    Any,
]
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
_RUNTIME_TASK_LINK_AUTHORITY = object()
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
EXTERNAL_MCP_CONNECTION_IDLE_SECONDS = 90
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
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        *,
        cause_code: str = "agent_gateway_rejected",
        failure_layer: str = "unknown",
        failure_phase: str = "",
        operation_kind: str = "unknown",
        tool: str = "",
        tool_routing_started: bool | None = None,
        mutation_started: bool | None = None,
        committed: bool | None = None,
        commit_state: str = "",
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.cause_code = cause_code
        self.failure_layer = failure_layer
        self.failure_phase = failure_phase
        self.retryable = retryable
        self.external_error = build_external_tool_error(
            error=message,
            error_code=cause_code,
            failure_layer=failure_layer,
            failure_phase=failure_phase,
            operation_kind=operation_kind,
            tool=tool,
            tool_routing_started=tool_routing_started,
            mutation_started=mutation_started,
            committed=committed,
            commit_state=commit_state,
            retryable=retryable,
            checkpoint_recovery_required=(False if mutation_started is False else None),
            temporary_cleanup_required=(False if mutation_started is False else None),
            details=details,
        )


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
    agent_budget_policy: dict[str, Any] = field(default_factory=dict)


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
    verification_profile: str = ""
    verification_prepare_handler: CompletionVerificationPrepareHandler | None = None
    verification_finalize_handler: CompletionVerificationFinalizeHandler | None = None
    requires_approved_execution_context: bool = False
    approved_execution_plan_builder: ApprovedUnityExecutionPlanBuilder | None = None
    # A category may be remembered only when the handler opts in.  This keeps
    # future approval rules narrowly tied to an explicitly reviewed tool.
    approval_category: str = ""
    allow_future_category: bool = False
    external_mcp_capability: str = ""
    # False only for handlers that own an atomic, receipt-backed rollback
    # transaction outside an existing Unity project (for example, creation of
    # a brand-new project at an absent path). Existing project writes keep the
    # default checkpoint requirement.
    pre_write_checkpoint_required: bool = True


@dataclass
class UserConstraintsSnapshot:
    path: Path
    content: str = ""
    status: str = "ok"
    message: str = "No user constraints configured."
    error: str = ""


RUNTIME_DIRECT_SKILL_CATEGORIES = {"read/debug", "plan/preview"}
# Interactive work stops on the model's final assistant response. Automation
# may explicitly set maxAgenticTurns; tool calls remain telemetry only and
# completion remains Runtime-verified.
RUNTIME_PLANNER_ARGUMENT_MAX_ATTEMPTS = 2
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
    "vrcforge_agent_message",
    "vrcforge_apply_approved",
    "vrcforge_execute_approved_shell",
    "vrcforge_vision_audit_multi",
}
EXTERNAL_MCP_INTERNAL_LOOP_TOOLS = EXTERNAL_AGENT_INTERNAL_TOOLS | {
    "vrcforge_ask_user",
    "vrcforge_classify_shell",
    "vrcforge_create_goal",
    "vrcforge_delegate_subagent",
    "vrcforge_execute_shell",
    "vrcforge_get_goal",
    "vrcforge_request_apply",
    "vrcforge_shell_process",
    "vrcforge_tool_registry",
    "vrcforge_update_goal",
}
EXTERNAL_MCP_CONFIRMATION_MAX_PENDING = 128
USER_CONSTRAINTS_INLINE_CHARACTER_LIMIT = 4000
USER_CONSTRAINTS_PREVIEW_CHARACTER_LIMIT = 240
WRAPPER_ONLY_WRITE_TARGETS = {
    "vrcforge_avatar_encryption_addon_apply",
    "vrcforge_avatar_encryption_addon_remove",
    "vrcforge_configure_optimizer_component",
    "vrcforge_install_vpm_package",
    "vrcforge_repair_project_chat_store",
    "vrcforge_shell_execute",
}
# This handler already owns a typed no-write preparer that seals one vrc-get
# binary, argv, package version, project identity, bounded process policy, and
# post-install readback.  The external MCP facade may therefore use it as a
# real tool while every generic/internal wrapper remains hidden.
EXTERNAL_MCP_TYPED_WRAPPER_CAPABILITIES = {
    "vrcforge_install_vpm_package": "sealed_vrc_get_install_v1",
}
EXTERNAL_MCP_TYPED_WRAPPER_WRITES = frozenset(EXTERNAL_MCP_TYPED_WRAPPER_CAPABILITIES)

# External MCP is its own catalogue. Only explicitly mapped public handlers
# enter it; chat, memory, generic file/Web access, planner controls, and
# runtime-management tools remain internal-only.
EXTERNAL_MCP_DEFAULT_TOOL_BLOCK = "core"
EXTERNAL_MCP_TOOL_BLOCK_BRANCHES: dict[str, tuple[str, ...]] = {
    "integrations": (
        "integrations/modular-avatar",
        "integrations/vrcfury",
        "integrations/gesture-manager",
    ),
    "skills": ("skills/vsk",),
}
EXTERNAL_MCP_TOOL_BLOCK_ROOTS = (
    "core",
    "project",
    "avatar",
    "assets",
    "materials",
    "integrations",
    "skills",
    "optimization",
    "checkpoint",
    "diagnostics",
    "encryption",
)
EXTERNAL_MCP_TOOL_BLOCKS = frozenset(
    {
        "core",
        "project",
        "avatar",
        "assets",
        "materials",
        "integrations/modular-avatar",
        "integrations/vrcfury",
        "integrations/gesture-manager",
        "skills/vsk",
        "optimization",
        "checkpoint",
        "diagnostics",
        "encryption",
    }
)
EXTERNAL_MCP_READ_TOOL_BLOCKS: dict[str, frozenset[str]] = {
    "core": frozenset(
        {
            "vrcforge_external_tool_blocks",
            "vrcforge_health",
            "vrcforge_unity_status",
            "vrcforge_unity_tools",
            "vrcforge_get_compile_errors",
            "vrcforge_list_avatars",
            "vrcforge_get_gameobject",
            "vrcforge_get_property",
        }
    ),
    "project": frozenset(
        {
            "vrcforge_diagnose_package_install_errors",
            "vrcforge_core_upgrade_status",
            "vrcforge_package_install_plan",
            "vrcforge_package_manager_status",
            "vrcforge_project_lifecycle_status",
            "vrcforge_project_create_plan",
            "vrcforge_project_catalog_registration_status",
            "vrcforge_scan_project_index",
        }
    ),
    "avatar": frozenset(
        {
            "vrcforge_plan_face_tuning",
            "vrcforge_preview_blendshape_apply",
            "vrcforge_preview_write_avatar_descriptor",
            "vrcforge_read_avatar_descriptor",
            "vrcforge_scan_avatar_controls",
            "vrcforge_scan_avatar_items",
            "vrcforge_scan_avatar_performance",
            "vrcforge_scan_blendshapes",
            "vrcforge_preview_atomic_reference_rename",
            "vrcforge_preview_constraint_sources",
            "vrcforge_preview_ensure_animator_state",
            "vrcforge_preview_ensure_expression_menu_control",
            "vrcforge_preview_ensure_expression_parameter",
            "vrcforge_preview_manage_expression_menu",
            "vrcforge_preview_manage_expression_parameters",
            "vrcforge_preview_manage_fx_animator",
            "vrcforge_preview_scene_object_duplicate",
            "vrcforge_preview_write_animation_curve",
            "vrcforge_inspect_skinned_mesh_bone_usage",
            "vrcforge_scan_animation_bindings",
            "vrcforge_scan_fx_animator",
            "vrcforge_scan_inbound_reference_closure",
            "vrcforge_scan_parameters",
            "vrcforge_avatar_upload_readiness",
            "vrcforge_get_avatar_upload_status",
            "vrcforge_preview_unity_constraint_conversion",
        }
    ),
    "assets": frozenset(
        {
            "vrcforge_get_unitypackage_import_status",
            "vrcforge_inspect_outfit_package",
            "vrcforge_plan_outfit_import",
            "vrcforge_preview_add_outfit",
            "vrcforge_preview_add_outfit_part",
            "vrcforge_preview_add_wardrobe_outfit",
            "vrcforge_preview_create_wardrobe",
            "vrcforge_preview_manage_wardrobe",
            "vrcforge_scan_wardrobe",
            "vrcforge_find_assets",
            "vrcforge_get_asset_info",
            "vrcforge_preview_scene_object_prefab",
            "vrcforge_preview_project_asset_duplicate",
        }
    ),
    "materials": frozenset(
        {
            "vrcforge_plan_shader_tuning",
            "vrcforge_preview_material_shader_assignment",
            "vrcforge_preview_shader_apply",
            "vrcforge_scan_materials",
            "vrcforge_preview_texture_import_settings",
        }
    ),
    "integrations/modular-avatar": frozenset(
        {
            "vrcforge_inspect_modular_avatar_component",
            "vrcforge_preview_add_modular_avatar_component",
            "vrcforge_preview_setup_outfit",
            "vrcforge_scan_modular_avatar",
        }
    ),
    "integrations/vrcfury": frozenset(
        {
            "vrcforge_preview_component_feature",
            "vrcforge_scan_vrcfury",
        }
    ),
    "integrations/gesture-manager": frozenset(
        {
            "vrcforge_gesture_manager_status",
        }
    ),
    "skills/vsk": frozenset({"vrcforge_preflight_skill_package"}),
    "optimization": frozenset(
        {
            "vrcforge_optimization_aao_hidden_body_cut_plan",
            "vrcforge_optimization_aao_trace_plan",
            "vrcforge_optimization_baseline_scan",
            "vrcforge_optimization_dependency_doctor",
            "vrcforge_optimization_lac_profile_plan",
            "vrcforge_optimization_ma2bt_convertibility_plan",
            "vrcforge_optimization_ma2bt_skipped_reasons",
            "vrcforge_optimization_ma_responsive_layer_audit",
            "vrcforge_optimization_material_slot_audit",
            "vrcforge_optimization_mesh_triangle_audit",
            "vrcforge_optimization_meshia_simplify_plan",
            "vrcforge_optimization_parameter_animator_usage",
            "vrcforge_optimization_parameter_behavior_regression",
            "vrcforge_optimization_parameter_budget_audit",
            "vrcforge_optimization_parameter_compressibility_plan",
            "vrcforge_optimization_parameter_inventory",
            "vrcforge_optimization_parameter_menu_map",
            "vrcforge_optimization_parameter_path_to_skill",
            "vrcforge_optimization_parameter_vrcfury_compressor_plan",
            "vrcforge_optimization_performance_tools_report",
            "vrcforge_optimization_physbone_audit",
            "vrcforge_optimization_physbone_reduce_plan",
            "vrcforge_optimization_plan",
            "vrcforge_optimization_profile_diff",
            "vrcforge_optimization_rollback_verify",
            "vrcforge_optimization_shader_adapter_registry",
            "vrcforge_scan_thry_avatar_performance",
            "vrcforge_optimization_target_profile",
            "vrcforge_optimization_texture_vram_audit",
            "vrcforge_optimization_ttt_atlas_plan",
            "vrcforge_optimization_upload_gate_audit",
            "vrcforge_optimization_upload_gate_fix_plan",
            "vrcforge_optimization_validation_delta",
            "vrcforge_optimization_visual_regression_plan",
            "vrcforge_optimization_vrcfury_compatibility_report",
            "vrcforge_preview_parameter_bit_packing",
        }
    ),
    "checkpoint": frozenset(
        {
            "vrcforge_list_checkpoints",
            "vrcforge_preview_restore_backup",
            "vrcforge_preview_restore_checkpoint",
            "vrcforge_preview_interrupted_apply_recovery",
            "vrcforge_export_interrupted_apply_incident_bundle",
            "vrcforge_list_interrupted_apply_recoveries",
        }
    ),
    "diagnostics": frozenset(
        {
            "vrcforge_build_test_readiness",
            "vrcforge_capture_status",
            "vrcforge_get_build_test_status",
            "vrcforge_inspect_primitive_basis_fixture",
            "vrcforge_read_vrchat_sdk_builder_alerts",
            "vrcforge_read_recent_logs",
            "vrcforge_run_validation_report",
            "vrcforge_vision_audit",
        }
    ),
    "encryption": frozenset(
        {
            "vrcforge_avatar_encryption_addon_status",
            "vrcforge_avatar_encryption_plan",
            "vrcforge_avatar_encryption_preview",
            "vrcforge_avatar_encryption_research_report",
            "vrcforge_avatar_encryption_scan",
        }
    ),
}


# The texture-import preview and apply endpoints are two views of the same
# atomic operation. Keep one public schema so internal/external agents cannot
# drift into guessing different field names for the shared Core handler.
TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "projectPath",
        "textureAssetPath",
        "platform",
        "maxTextureSize",
        "format",
        "compression",
        "crunch",
        "quality",
    ],
    "properties": {
        "projectPath": {
            "type": "string",
            "description": "Exact existing Unity project root bound to this one-texture operation.",
        },
        "textureAssetPath": {
            "type": "string",
            "pattern": "^Assets/",
            "description": "Exact persistent project-relative texture path under Assets/.",
        },
        "platform": {
            "type": "string",
            "enum": ["default", "standalone", "android", "ios"],
            "description": "Exact TextureImporter platform settings to inspect or change.",
        },
        "maxTextureSize": {
            "type": "integer",
            "enum": [32, 64, 128, 256, 512, 1024, 2048, 4096, 8192],
        },
        "format": {
            "type": "string",
            "enum": [
                "automatic",
                "rgb24",
                "rgba32",
                "dxt1",
                "dxt5",
                "dxt1_crunched",
                "dxt5_crunched",
                "bc7",
                "etc_rgb4",
                "etc2_rgb4",
                "etc2_rgba8",
                "etc_rgb4_crunched",
                "etc2_rgba8_crunched",
                "astc_4x4",
                "astc_6x6",
                "astc_8x8",
                "pvrtc_rgb4",
                "pvrtc_rgba4",
            ],
        },
        "compression": {
            "type": "string",
            "enum": ["uncompressed", "normal", "high", "low"],
        },
        "crunch": {"type": "boolean"},
        "quality": {"type": "integer", "minimum": 0, "maximum": 100},
    },
}

_PROJECT_PATH_PROPERTY = {
    "type": "string",
    "description": "Exact existing Unity project root bound to this tool call.",
}
_AVATAR_PATH_PROPERTY = {
    "type": "string",
    "description": "Exact loaded-scene avatar hierarchy path.",
}

SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "sourceScenePath", "sourceObjectPath"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "sourceScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$"},
        "sourceObjectPath": {"type": "string"},
        "targetParentScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$"},
        "targetParentPath": {"type": "string"},
        "targetName": {"type": "string"},
        "preserveWorldTransform": {"type": "boolean", "default": False},
        "overwrite": {"type": "boolean", "const": False},
    },
}

AVATAR_DESCRIPTOR_WRITE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "avatarPath"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "avatarPath": _AVATAR_PATH_PROPERTY,
        "viewPosition": {"type": "object", "additionalProperties": False, "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}}},
        "lipSync": {"type": "string"},
        "visemeSkinnedMeshPath": {"type": "string"},
        "visemeBlendShapes": {"type": "array", "items": {"type": "string"}},
        "expressionParametersPath": {"type": "string", "pattern": "^Assets/"},
        "expressionsMenuPath": {"type": "string", "pattern": "^Assets/"},
        "baseAnimationLayers": {"type": "array", "items": {"type": "object"}},
        "specialAnimationLayers": {"type": "array", "items": {"type": "object"}},
        "eyeLookSettingsSourceAvatarPath": {"type": "string"},
        "eyeLookEnabled": {"type": "boolean"},
    },
}

ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "clipPath", "propertyName"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "action": {"type": "string", "enum": ["set_curve", "delete_curve", "retarget_curve"], "default": "set_curve"},
        "clipPath": {"type": "string", "pattern": "^Assets/"},
        "bindingPath": {"type": "string"},
        "objectPath": {"type": "string"},
        "componentType": {"type": "string", "default": "GameObject"},
        "propertyName": {"type": "string"},
        "sourceBindingPath": {"type": "string"},
        "sourceComponentType": {"type": "string"},
        "sourcePropertyName": {"type": "string"},
        "deleteSource": {"type": "boolean", "default": True},
        "overwriteExisting": {"type": "boolean", "default": False},
        "keys": {"type": "array", "items": {"type": "object"}},
        "constantFloat": {"type": "number"},
    },
}

EXPRESSION_PARAMETERS_MANAGE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "avatarPath", "action"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "avatarPath": _AVATAR_PATH_PROPERTY,
        "action": {"type": "string", "enum": ["update", "delete", "rename", "reorder"]},
        "parameterName": {"type": "string"},
        "newName": {"type": "string"},
        "orderNames": {"type": "array", "items": {"type": "string"}},
        "valueType": {"type": "string"},
        "defaultValue": {"type": "number"},
        "saved": {"type": "boolean"},
        "networkSynced": {"type": "boolean"},
    },
}

EXPRESSION_MENU_MANAGE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "avatarPath", "action"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "avatarPath": _AVATAR_PATH_PROPERTY,
        "action": {"type": "string", "enum": ["create", "update", "delete", "reorder"]},
        "assetDir": {"type": "string", "pattern": "^Assets/"},
        "menuPath": {"type": "string"},
        "controlName": {"type": "string"},
        "controlIndex": {"type": "integer", "minimum": 0},
        "newName": {"type": "string"},
        "controlType": {"type": "string"},
        "controlFloat": {"type": "number"},
        "value": {"type": "number"},
        "parameterName": {"type": "string"},
        "iconAssetPath": {"type": "string", "pattern": "^Assets/"},
        "subMenuAssetPath": {"type": "string", "pattern": "^Assets/"},
        "createSubMenu": {"type": "boolean"},
        "subParameters": {"type": "array", "items": {"type": "string"}},
        "orderNames": {"type": "array", "items": {"type": "string"}},
    },
}

MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "action"],
    "properties": {
        "projectPath": {"type": "string", "description": "Exact Unity project root."},
        "avatarPath": {"type": "string", "description": "Avatar hierarchy path used to resolve its FX controller."},
        "controllerPath": {"type": "string", "pattern": "^Assets/", "description": "Exact AnimatorController asset path; overrides avatar FX resolution."},
        "fxControllerPath": {"type": "string", "pattern": "^Assets/", "description": "Compatibility alias for controllerPath."},
        "action": {
            "type": "string",
            "enum": ["ensure_layer", "delete_layer", "ensure_state", "update_state", "delete_state", "ensure_transition", "delete_transition", "delete_parameter"],
            "description": "One exact FX mutation. delete_parameter refuses parameters still referenced anywhere in the controller.",
        },
        "assetDir": {"type": "string", "pattern": "^Assets/"},
        "layerName": {"type": "string"},
        "stateName": {"type": "string"},
        "destinationStateName": {"type": "string"},
        "newName": {"type": "string"},
        "writeDefaults": {"type": "boolean"},
        "motionClipPath": {"type": "string", "pattern": "^Assets/"},
        "speed": {"type": "number"},
        "hasExitTime": {"type": "boolean"},
        "exitTime": {"type": "number"},
        "duration": {"type": "number", "minimum": 0},
        "canTransitionToSelf": {"type": "boolean"},
        "transitionIndex": {"type": "integer", "minimum": 0},
        "conditions": {
            "type": "array",
            "maxItems": 64,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["parameterName", "mode", "threshold"],
                "properties": {
                    "parameterName": {"type": "string"},
                    "mode": {"type": "string"},
                    "threshold": {"type": "number"},
                },
            },
        },
        "parameterName": {"type": "string"},
        "conditionMode": {"type": "string"},
        "threshold": {"type": "number"},
    },
    "oneOf": [
        {
            "type": "object",
            "required": ["action", "parameterName"],
            "properties": {"action": {"type": "string", "const": "delete_parameter"}},
        },
        {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["ensure_layer", "delete_layer", "ensure_state", "update_state", "delete_state", "ensure_transition", "delete_transition"],
                }
            },
        },
    ],
}


# Unity read schemas are shared by the internal and external tool catalogues.
# Keeping only the task-disambiguating fields here makes lazy-loaded blocks
# useful without inflating every agent turn with handler implementation detail.
UNITY_READ_TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "vrcforge_build_test_readiness": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "avatarPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "avatarPath": _AVATAR_PATH_PROPERTY,
            "includeQuest": {"type": "boolean", "default": True},
            "maxErrors": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        },
    },
    "vrcforge_capture_status": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "requirePlayMode": {"type": "boolean", "default": False},
            "captureMode": {"type": "string", "enum": ["auto", "scene_view", "game_view"], "default": "auto"},
        },
    },
    "vrcforge_get_gameobject": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string", "description": "Exact hierarchy path or unique scene GameObject name."},
        },
    },
    "vrcforge_get_property": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "componentType", "propertyPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "componentType": {"type": "string"},
            "propertyPath": {"type": "string"},
            "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
            "maxItems": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 50},
        },
    },
    "vrcforge_preview_scene_object_duplicate": SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_write_avatar_descriptor": AVATAR_DESCRIPTOR_WRITE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_write_animation_curve": ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_manage_expression_parameters": EXPRESSION_PARAMETERS_MANAGE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_manage_expression_menu": EXPRESSION_MENU_MANAGE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_list_avatars": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Optional exact Unity project root; omit only when the active project is authoritative.",
            },
        },
    },
    "vrcforge_read_avatar_descriptor": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Optional exact Unity project root; omit only when the active project is authoritative.",
            },
            "avatarPath": {
                "type": "string",
                "description": "Optional exact loaded-scene Avatar Descriptor hierarchy path.",
            },
        },
    },
    "vrcforge_scan_blendshapes": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Optional exact Unity project root; omit only when the active project is authoritative.",
            },
            "avatarPath": {
                "type": "string",
                "description": "Optional exact loaded-scene avatar hierarchy path.",
            },
        },
    },
    "vrcforge_scan_parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Optional exact Unity project root; omit only when the active project is authoritative.",
            },
            "avatarPath": {
                "type": "string",
                "description": "Optional exact loaded-scene avatar hierarchy path.",
            },
        },
    },
    "vrcforge_external_tool_blocks": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "block": {
                "type": "string",
                "enum": sorted(EXTERNAL_MCP_TOOL_BLOCKS),
                "description": "Optional exact leaf block to expand with compact tool names and read/write modes.",
            },
        },
    },
    "vrcforge_preflight_skill_package": {
        "type": "object",
        "additionalProperties": False,
        "required": ["packagePath"],
        "properties": {
            "packagePath": {
                "type": "string",
                "description": "Exact local path to the existing .vsk package to inspect without importing it.",
            },
        },
    },
    "vrcforge_get_build_test_status": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "jobId"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact Unity project root that owns the existing Build & Test job."},
            "jobId": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$", "description": "Exact jobId returned by vrcforge_build_test_avatar."},
        },
    },
    "vrcforge_gesture_manager_status": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Optional exact avatar hierarchy path when more than one Gesture Manager is connected."},
            "includeParameters": {"type": "boolean", "default": False, "description": "Return every runtime parameter. Prefer parameterNames or parameterPrefix for a focused read."},
            "parameterNames": {"type": "array", "items": {"type": "string"}, "maxItems": 128, "description": "Exact runtime parameter names to return while retaining total counts."},
            "parameterPrefix": {"type": "string", "maxLength": 256, "description": "Return runtime parameters whose names start with this exact prefix."},
        },
    },
    "vrcforge_read_vrchat_sdk_builder_alerts": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "avatarPath"],
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Exact Unity project root whose already-open SDK Builder cache will be inspected.",
            },
            "avatarPath": {
                "type": "string",
                "description": "Exact loaded-scene Avatar Descriptor hierarchy path that must match the SDK Builder selection.",
            },
        },
    },
    "vrcforge_preview_texture_import_settings": TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_manage_fx_animator": MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA,
    "vrcforge_avatar_upload_readiness": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "avatarPath", "uploadMode", "buildType", "platforms", "metadata", "thumbnail"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root."},
            "avatarPath": {"type": "string", "description": "Exact loaded-scene Avatar Descriptor hierarchy path."},
            "uploadMode": {"type": "string", "enum": ["create", "update"]},
            "buildType": {"type": "string", "const": "build_and_upload"},
            "platforms": {"type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "string", "enum": ["StandaloneWindows64", "Android", "iOS"]}},
            "metadata": {"$ref": "#/$defs/avatarUploadMetadata"},
            "thumbnail": {"$ref": "#/$defs/avatarUploadThumbnail"},
        },
        "$defs": {
            "avatarStyle": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["id", "name"],
                "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
            },
            "avatarUploadMetadata": {
                "type": "object",
                "additionalProperties": False,
                "required": ["mode"],
                "properties": {
                    "mode": {"type": "string", "enum": ["preserve_remote", "replace"]},
                    "name": {"type": "string", "maxLength": 64},
                    "description": {"type": "string", "maxLength": 256},
                    "visibility": {"type": "string", "enum": ["private", "public"]},
                    "primaryStyle": {"$ref": "#/$defs/avatarStyle"},
                    "secondaryStyle": {"$ref": "#/$defs/avatarStyle"},
                    "contentWarnings": {"type": "array", "maxItems": 5, "uniqueItems": True, "items": {"type": "string", "enum": ["content_sex", "content_adult", "content_violence", "content_gore", "content_horror"]}},
                    "authorTags": {"type": "array", "maxItems": 10, "uniqueItems": True, "items": {"type": "string", "minLength": 1, "maxLength": 64}},
                },
            },
            "avatarUploadThumbnail": {
                "type": "object",
                "additionalProperties": False,
                "required": ["mode"],
                "properties": {"mode": {"type": "string", "enum": ["keep", "replace"]}, "path": {"type": "string"}, "sha256": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"}},
            },
        },
    },
    "vrcforge_get_avatar_upload_status": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "jobId"],
        "properties": {
            "projectPath": {"type": "string"},
            "jobId": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"},
        },
    },
    "vrcforge_preview_unity_constraint_conversion": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "scenePath", "avatarPath", "gameObjectPath", "componentType", "componentIndex"],
        "properties": {
            "projectPath": {"type": "string"},
            "scenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$"},
            "avatarPath": {"type": "string"},
            "gameObjectPath": {"type": "string"},
            "componentType": {"type": "string", "enum": ["UnityEngine.Animations.PositionConstraint", "UnityEngine.Animations.RotationConstraint", "UnityEngine.Animations.ScaleConstraint", "UnityEngine.Animations.ParentConstraint", "UnityEngine.Animations.AimConstraint", "UnityEngine.Animations.LookAtConstraint"]},
            "componentIndex": {"type": "integer", "minimum": 0, "maximum": 31},
        },
    },
    "vrcforge_scan_fx_animator": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Scene hierarchy path of the avatar root."},
            "controllerPath": {"type": "string", "description": "Project-relative AnimatorController asset path; overrides the avatar FX controller."},
        },
    },
    "vrcforge_scan_animation_bindings": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Scene hierarchy path of the avatar root."},
            "controllerPath": {"type": "string", "description": "Project-relative AnimatorController asset path."},
            "clipPaths": {"type": "array", "items": {"type": "string"}, "description": "Optional exact project-relative AnimationClip asset paths."},
            "includeAllProjectClips": {"type": "boolean", "description": "Include unrelated project clips; normally leave false."},
            "includeBindingDetails": {"type": "boolean", "description": "Return full per-binding arrays for a narrow clip selection."},
            "maxClips": {"type": "integer", "minimum": 1, "description": "Maximum clips to scan."},
        },
    },
    "vrcforge_scan_avatar_controls": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Scene hierarchy path of the avatar root."},
        },
    },
    "vrcforge_inspect_skinned_mesh_bone_usage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["gameObjectPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "gameObjectPath": {"type": "string", "description": "Exact scene hierarchy path of the SkinnedMeshRenderer GameObject."},
            "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
            "minimumWeight": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.000001},
        },
    },
    "vrcforge_inspect_modular_avatar_component": {
        "type": "object",
        "additionalProperties": False,
        "required": ["gameObjectPath", "componentType"],
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Optional scene hierarchy path of the containing avatar root."},
            "gameObjectPath": {"type": "string", "description": "Exact scene hierarchy path of the component carrier."},
            "componentType": {
                "type": "string",
                "enum": ["MergeArmature", "BoneProxy", "MenuInstaller", "MergeAnimator", "Parameters"],
                "description": "Supported Modular Avatar component family to inspect.",
            },
        },
    },
    "vrcforge_scan_inbound_reference_closure": {
        "type": "object",
        "additionalProperties": False,
        "required": ["avatarPath"],
        "anyOf": [
            {"required": ["targetPaths"]},
            {"required": ["targetComponentSelectors"]},
        ],
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Exact scene hierarchy path of the avatar root."},
            "targetPaths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact GameObject roots being considered for deletion.",
            },
            "targetComponentSelectors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["objectPath", "componentType"],
                    "properties": {
                        "objectPath": {"type": "string"},
                        "componentType": {"type": "string"},
                        "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
                    },
                },
                "description": "Exact removable components; a GameObject path alone is not treated as a component reference.",
            },
            "includeProjectAssets": {"type": "boolean", "default": True},
            "includeAnimationBindings": {"type": "boolean", "default": True},
            "includeIndirectParameterEdges": {"type": "boolean", "default": True},
            "maxResults": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 1000},
        },
    },
}

EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "vrcforge_duplicate_scene_object": SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_write_avatar_descriptor": AVATAR_DESCRIPTOR_WRITE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_write_animation_curve": ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_manage_expression_parameters": EXPRESSION_PARAMETERS_MANAGE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_manage_expression_menu": EXPRESSION_MENU_MANAGE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_remove_component": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "componentType"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "componentType": {"type": "string"},
            "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
        },
    },
    "vrcforge_reparent_gameobject": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "newParentPath": {"type": "string"},
            "worldPositionStays": {"type": "boolean", "default": True},
        },
    },
    "vrcforge_set_gameobject_active": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "active"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "active": {"type": "boolean"},
        },
    },
    "vrcforge_set_property": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "componentType", "propertyPath", "value"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "componentType": {"type": "string"},
            "propertyPath": {"type": "string"},
            "value": {"description": "Exact JSON value to assign to the field or property."},
            "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
        },
    },
    "vrcforge_save_current_scene": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Exact existing Unity project root whose current saved scene must be dirty.",
            },
            "scenePath": {
                "type": "string",
                "pattern": "^Assets/.*\\.unity$",
                "description": "Optional exact current Assets/... .unity path used as an identity check.",
            },
        },
    },
    "vrcforge_import_skill_package": {
        "type": "object",
        "additionalProperties": False,
        "required": ["packagePath"],
        "properties": {
            "packagePath": {"type": "string", "description": "Exact local path to the existing .vsk package."},
            "source": {"type": "string", "maxLength": 160, "description": "Optional audit label for this local import."},
            "projectToUserSkills": {"type": "boolean", "default": True, "description": "Project the installed package into the user Skill directory."},
        },
    },
    "vrcforge_export_skill_package": {
        "type": "object",
        "additionalProperties": False,
        "required": ["skillName", "outputPath"],
        "properties": {
            "skillName": {"type": "string", "description": "Exact installed user Skill name to export."},
            "outputPath": {"type": "string", "description": "Exact new local .vsk destination; it must not already exist."},
            "release": {"type": "boolean", "default": False, "description": "Sign a release package instead of creating a development package."},
            "privateKeyPath": {"type": "string", "description": "Local Ed25519 private-key file path required only for release export. Key material is never accepted inline."},
        },
    },
    "vrcforge_set_texture_import_settings": TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA,
    "vrcforge_manage_fx_animator": MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA,
    "vrcforge_set_material_shader": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "rendererPath", "slotIndex", "shaderName"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this material write."},
            "rendererPath": {"type": "string", "description": "Exact loaded-scene renderer hierarchy path used to resolve the material slot."},
            "rendererComponentId": {"type": "string", "description": "Optional exact renderer identity returned by the preview."},
            "materialAssetPath": {"type": "string", "description": "Optional exact Assets/... .mat path used to disambiguate the selected slot."},
            "slotIndex": {"type": "integer", "minimum": 0, "description": "Zero-based material slot index on the selected renderer."},
            "shaderName": {"type": "string", "description": "Exact installed Unity shader name to assign."},
            "shaderAssetPath": {"type": "string", "description": "Optional exact Assets/... or Packages/... shader asset path used to bind the preview."},
        },
    },
    "vrcforge_set_constraint_sources": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "scenePath", "gameObjectPath", "constraintKind", "componentIndex", "sources"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this scene write."},
            "scenePath": {"type": "string", "description": "Exact Assets/... .unity scene containing the constraint."},
            "gameObjectPath": {"type": "string", "description": "Exact hierarchy path of the GameObject carrying the constraint."},
            "constraintKind": {"type": "string", "enum": ["position", "rotation", "scale", "parent", "aim", "look_at"]},
            "componentIndex": {"type": "integer", "minimum": 0, "maximum": 31},
            "sources": {
                "type": "array",
                "maxItems": 64,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sourcePath", "weight"],
                    "properties": {
                        "sourcePath": {"type": "string", "description": "Exact source Transform hierarchy path."},
                        "weight": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                },
            },
        },
    },
    "vrcforge_save_scene_object_as_prefab": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "sourceScenePath", "sourceObjectPath", "prefabAssetPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this create-new asset write."},
            "sourceScenePath": {"type": "string", "description": "Exact Assets/... .unity scene containing the source object."},
            "sourceObjectPath": {"type": "string", "description": "Exact hierarchy path of the source scene object."},
            "prefabAssetPath": {"type": "string", "description": "Exact absent Assets/VRCForge/Generated/... .prefab destination."},
        },
    },
    "vrcforge_build_parameter_bit_packed_clone": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "sourceScenePath", "sourceAvatarPath", "outputCloneName"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this source-preserving optimization."},
            "sourceScenePath": {"type": "string", "description": "Exact Assets/... .unity scene containing the source Avatar."},
            "sourceAvatarPath": {"type": "string", "description": "Exact source Avatar hierarchy path; the source is preserved."},
            "outputCloneName": {"type": "string", "description": "Exact new sibling clone name for the packed result."},
        },
    },
    "vrcforge_atomic_reference_rename": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "operationKind", "scenePath", "avatarPath"],
        "oneOf": [
            {"properties": {"operationKind": {"const": "game_object"}}, "required": ["targetObjectPath", "newName"]},
            {"properties": {"operationKind": {"const": "parameter"}}, "required": ["oldParameterName", "newParameterName"]},
        ],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this complete reference migration."},
            "operationKind": {"type": "string", "enum": ["game_object", "parameter"]},
            "scenePath": {"type": "string", "description": "Exact Assets/... .unity scene containing the Avatar."},
            "avatarPath": {"type": "string", "description": "Exact Avatar root hierarchy path that bounds the migration."},
            "targetObjectPath": {"type": "string", "description": "Exact descendant hierarchy path for a game_object migration."},
            "newName": {"type": "string", "description": "New leaf GameObject name for a game_object migration."},
            "oldParameterName": {"type": "string", "description": "Exact existing expression/Animator parameter name."},
            "newParameterName": {"type": "string", "description": "Exact replacement parameter name."},
        },
    },
    "vrcforge_delete_gameobject": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "anyOf": [
            {"required": ["gameObjectPath"]},
            {"required": ["globalObjectId"]},
        ],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external write call."},
            "gameObjectPath": {"type": "string", "description": "Exact hierarchy path when it is unique."},
            "globalObjectId": {"type": "string", "description": "Exact Unity GlobalObjectId; prefer this when hierarchy names are duplicated."},
            "preview": {"type": "boolean", "default": False},
        },
    },
    "vrcforge_instantiate_prefab": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "anyOf": [
            {"required": ["assetPath"]},
            {"required": ["guid"]},
        ],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external write call."},
            "assetPath": {"type": "string", "description": "Exact project-relative prefab asset path, including Packages/... prefabs."},
            "guid": {"type": "string", "description": "Exact prefab asset GUID when assetPath is omitted."},
            "parentPath": {"type": "string", "description": "Optional exact hierarchy path of the parent. Omit or pass an empty string for the scene root."},
            "name": {"type": "string", "description": "Optional exact instance name override."},
            "worldPositionStays": {"type": "boolean", "default": True},
            "preview": {"type": "boolean", "default": False},
        },
    },
    "vrcforge_build_test_avatar": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "avatarPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this local build."},
            "avatarPath": {"type": "string", "description": "Exact loaded-scene hierarchy path of the avatar to Build & Test locally."},
        },
    },
    "vrcforge_build_and_upload_avatar": {
        **UNITY_READ_TOOL_INPUT_SCHEMAS.get("vrcforge_avatar_upload_readiness", {}),
        "required": [
            "projectPath", "avatarPath", "uploadMode", "buildType", "platforms", "metadata", "thumbnail",
            "expectedAvatarGlobalObjectId", "expectedCurrentPipelineId", "expectedSdkUserId", "expectedPlatform", "readinessDigest",
        ],
        "properties": {
            **UNITY_READ_TOOL_INPUT_SCHEMAS.get("vrcforge_avatar_upload_readiness", {}).get("properties", {}),
            "expectedAvatarGlobalObjectId": {"type": "string"},
            "expectedCurrentPipelineId": {"type": "string"},
            "expectedSdkUserId": {"type": "string"},
            "expectedPlatform": {"type": "string"},
            "readinessDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
        },
    },
    "vrcforge_convert_unity_constraint": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "scenePath", "avatarPath", "gameObjectPath", "componentType", "componentIndex", "expectedSceneGuid", "expectedSceneFileDigest", "expectedAvatarGlobalObjectId", "expectedComponentGlobalObjectId", "expectedBeforeDigest"],
        "properties": {
            **UNITY_READ_TOOL_INPUT_SCHEMAS.get("vrcforge_preview_unity_constraint_conversion", {}).get("properties", {}),
            "expectedSceneGuid": {"type": "string"},
            "expectedSceneFileDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            "expectedAvatarGlobalObjectId": {"type": "string"},
            "expectedComponentGlobalObjectId": {"type": "string"},
            "expectedBeforeDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
        },
    },
    "vrcforge_install_unity_core": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Exact existing Unity project root that will receive the Core bundled with this running VRCForge build.",
            },
        },
    },
    "vrcforge_gesture_manager_set_parameter": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "parameterName", "value"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external write call."},
            "avatarPath": {"type": "string", "description": "Optional exact avatar hierarchy path; required only when multiple managers are connected."},
            "parameterName": {"type": "string", "description": "Exact existing Gesture Manager runtime parameter, for example VelocityZ or Grounded."},
            "value": {"type": "number", "description": "Runtime value; the tool reads back the applied value."},
        },
    },
    "vrcforge_gesture_manager_enter_play_mode": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external editor-state call."},
            "avatarPath": {"type": "string", "description": "Optional exact active avatar hierarchy path; required when multiple active avatars exist."},
        },
    },
    "vrcforge_capture_screenshot": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this capture."},
            "avatarPath": {"type": "string", "description": "Optional exact avatar hierarchy path; omit only when the active avatar is unambiguous."},
            "angle": {
                "type": "string",
                "enum": ["front", "side_left", "side_right", "back"],
                "description": "One fixed view angle. Call once per angle for a multi-angle audit.",
            },
            "framing": {
                "type": "string",
                "enum": ["face", "avatar"],
                "description": "face frames the head; avatar frames the complete avatar including feet and tail. Named angles default to face for compatibility.",
            },
            "width": {"type": "integer", "minimum": 256, "maximum": 2048, "default": 960},
            "height": {"type": "integer", "minimum": 256, "maximum": 2048, "default": 960},
            "requirePlayMode": {"type": "boolean", "default": False},
            "captureMode": {
                "type": "string",
                "enum": ["auto", "scene_view", "game_view"],
                "default": "auto",
                "description": "scene_view captures the Unity Scene view even during Gesture Manager Play Mode.",
            },
        },
    },
    "vrcforge_select_scene_object": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external write call."},
            "gameObjectPath": {"type": "string", "description": "Exact loaded-scene hierarchy path to select and show in Inspector."},
        },
    },
    "vrcforge_set_play_mode": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "isPlaying"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external write call."},
            "isPlaying": {"type": "boolean", "description": "True to enter Play Mode; false to exit Play Mode."},
        },
    },
}


def canonical_unity_read_tool_input_schema(tool_name: str) -> dict[str, Any]:
    """Return the one model-facing schema shared by internal and external Agents."""

    name = str(tool_name or "").strip()
    registered = UNITY_READ_TOOL_INPUT_SCHEMAS.get(name)
    if isinstance(registered, Mapping):
        return dict(registered)
    if name.startswith("vrcforge_preview_"):
        write_name = "vrcforge_" + name.removeprefix("vrcforge_preview_")
        paired = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS.get(write_name)
        if isinstance(paired, Mapping):
            return dict(paired)
    hinted = planner_policy.planner_tool_input_schema(name)
    if hinted:
        return dict(hinted)
    return {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": True,
    }


def canonical_unity_write_tool_input_schema(tool_name: str) -> dict[str, Any]:
    """Return the one write schema shared by the internal loop and external MCP."""

    name = str(tool_name or "").strip()
    registered = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS.get(name)
    if isinstance(registered, Mapping):
        return dict(registered)
    hinted = planner_policy.planner_tool_input_schema(name)
    if hinted:
        return dict(hinted)
    return {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": True,
    }

EXTERNAL_MCP_WRITE_TOOL_BLOCKS: dict[str, frozenset[str]] = {
    "project": frozenset(
        {
            "vrcforge_create_project",
            "vrcforge_install_unity_core",
            "vrcforge_restore_unity_core",
            "vrcforge_install_vpm_package",
            "vrcforge_refresh_asset_database",
            "vrcforge_register_project",
            "vrcforge_register_project_catalog",
            "vrcforge_rollback_project_catalog_registration",
            "vrcforge_rollback_project_lifecycle",
            "vrcforge_select_project",
            "vrcforge_set_play_mode",
        }
    ),
    "avatar": frozenset(
        {
            "vrcforge_atomic_reference_rename",
            "vrcforge_build_and_upload_avatar",
            "vrcforge_apply_blendshapes",
            "vrcforge_run_face_tuning",
            "vrcforge_undo_blendshapes",
            "vrcforge_write_avatar_descriptor",
            "vrcforge_add_component",
            "vrcforge_apply_clothing_fx",
            "vrcforge_apply_tuning_preset",
            "vrcforge_create_gameobject",
            "vrcforge_delete_gameobject",
            "vrcforge_duplicate_scene_object",
            "vrcforge_ensure_animator_state",
            "vrcforge_ensure_expression_menu_control",
            "vrcforge_ensure_expression_parameter",
            "vrcforge_export_vrm",
            "vrcforge_manage_expression_menu",
            "vrcforge_manage_expression_parameters",
            "vrcforge_manage_fx_animator",
            "vrcforge_reapply_tuning_history",
            "vrcforge_remove_component",
            "vrcforge_rename_gameobject",
            "vrcforge_reparent_gameobject",
            "vrcforge_rollback_parameters",
            "vrcforge_save_current_scene",
            "vrcforge_save_new_scene",
            "vrcforge_select_scene_object",
            "vrcforge_set_gameobject_active",
            "vrcforge_set_constraint_sources",
            "vrcforge_convert_unity_constraint",
            "vrcforge_set_property",
            "vrcforge_toggle_scene_object",
            "vrcforge_write_animation_curve",
        }
    ),
    "assets": frozenset(
        {
            "vrcforge_add_outfit",
            "vrcforge_add_outfit_part",
            "vrcforge_add_wardrobe_outfit",
            "vrcforge_import_outfit_package",
            "vrcforge_create_wardrobe",
            "vrcforge_manage_wardrobe",
            "vrcforge_instantiate_prefab",
            "vrcforge_unpack_prefab",
            "vrcforge_duplicate_project_asset",
            "vrcforge_save_scene_object_as_prefab",
        }
    ),
    "materials": frozenset(
        {
            "vrcforge_apply_shader_tuning",
            "vrcforge_apply_shader_tuning_preset",
            "vrcforge_reapply_shader_tuning_history",
            "vrcforge_restore_shader_tuning",
            "vrcforge_set_texture_import_settings",
            "vrcforge_set_material_shader",
        }
    ),
    "integrations/modular-avatar": frozenset(
        {
            "vrcforge_add_modular_avatar_component",
            "vrcforge_setup_outfit",
        }
    ),
    "integrations/vrcfury": frozenset({"vrcforge_create_component_feature"}),
    "integrations/gesture-manager": frozenset(
        {
            "vrcforge_gesture_manager_enter_play_mode",
            "vrcforge_gesture_manager_set_parameter",
        }
    ),
    "skills/vsk": frozenset(
        {
            "vrcforge_import_skill_package",
            "vrcforge_export_skill_package",
        }
    ),
    "optimization": frozenset(
        {
            "vrcforge_apply_parameter_optimization",
            "vrcforge_build_parameter_bit_packed_clone",
        }
    ),
    "checkpoint": frozenset(
        {
            "vrcforge_create_safe_backup",
            "vrcforge_restore_checkpoint",
            "vrcforge_restore_safe_backup",
            "vrcforge_resolve_interrupted_apply_recovery",
        }
    ),
    "diagnostics": frozenset({"vrcforge_build_test_avatar", "vrcforge_capture_screenshot"}),
}


def _external_mcp_tool_block(name: str, *, write: bool) -> str:
    catalogue = EXTERNAL_MCP_WRITE_TOOL_BLOCKS if write else EXTERNAL_MCP_READ_TOOL_BLOCKS
    matches = [block for block, names in catalogue.items() if name in names]
    if len(matches) > 1:
        raise RuntimeError(f"External MCP tool is assigned to multiple blocks: {name}")
    return matches[0] if matches else ""


def normalize_external_mcp_tool_blocks(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset({EXTERNAL_MCP_DEFAULT_TOOL_BLOCK})
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw = list(value)
    else:
        raise AgentGatewayError("toolBlocks must be an array of block names.", status_code=400)
    blocks = {str(item or "").strip().lower() for item in raw if str(item or "").strip()}
    if "*" in blocks:
        return EXTERNAL_MCP_TOOL_BLOCKS
    known = EXTERNAL_MCP_TOOL_BLOCKS | frozenset(EXTERNAL_MCP_TOOL_BLOCK_BRANCHES)
    unknown = blocks - known
    if unknown:
        raise AgentGatewayError(
            f"Unknown external MCP tool block: {sorted(unknown)[0]}",
            status_code=400,
        )
    expanded: set[str] = set()
    for block in blocks or {EXTERNAL_MCP_DEFAULT_TOOL_BLOCK}:
        expanded.update(EXTERNAL_MCP_TOOL_BLOCK_BRANCHES.get(block, (block,)))
    return frozenset(expanded)


def external_mcp_typed_wrapper_allowed(handler: AgentWriteHandler) -> bool:
    required_capability = EXTERNAL_MCP_TYPED_WRAPPER_CAPABILITIES.get(handler.name, "")
    return bool(
        required_capability
        and handler.external_mcp_capability == required_capability
    )
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
        "title": "Scene/Game View Capture",
        "inputs": ["Capture angle, face/avatar framing, dimensions, optional avatar path, and auto/scene_view/game_view mode."],
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
    "vrcforge_build_test_avatar": {
        "title": "Local Avatar Build & Test",
        "permissionMode": "controlled_write",
        "inputs": ["Exact Unity project path and exact loaded avatar hierarchy path."],
        "outputs": ["Local-only SDK job identity and authoritative Core start status."],
        "sideEffects": "starts VRChat SDK BuildAndTest; may create a local bundle, launch a local test client, assign a local blueprint ID, and dirty the scene; never uploads or publishes",
        "backupRestore": "pre-write checkpoint required; rollback is never automatic and remains user-approved",
        "tags": ["validation", "build-test", "local-only", "write"],
    },
    "vrcforge_get_build_test_status": {
        "title": "Local Build & Test Status",
        "permissionMode": "read_only",
        "inputs": ["Exact project path and existing Build & Test jobId."],
        "outputs": ["Authoritative Core job status, progress, SDK errors, Console delta, write state, and local bundle facts."],
        "sideEffects": "none; polls an existing job only",
        "backupRestore": "not required; status polling never restores or retries",
        "tags": ["validation", "build-test", "status", "read-only"],
    },
    "vrcforge_avatar_upload_readiness": {
        "title": "Avatar Upload Readiness",
        "permissionMode": "read_only",
        "inputs": ["Exact avatar identity, create/update mode, current platform, requested private/public metadata, styles, warnings, tags, and thumbnail."],
        "outputs": ["Bound readiness digest, account/platform state, metadata request, public-SDK capability coverage, and explicit unknown SDK-panel fields."],
        "sideEffects": "none; never builds, uploads, changes metadata, or invokes SDK-panel Auto Fix",
        "backupRestore": "not required; readiness is read-only",
        "tags": ["avatar", "upload", "readiness", "metadata", "read-only"],
    },
    "vrcforge_read_vrchat_sdk_builder_alerts": {
        "title": "VRChat SDK Builder Alerts",
        "permissionMode": "read_only",
        "inputs": ["Exact Unity project path and the exact Avatar Descriptor selected in an already-validated SDK Builder panel."],
        "outputs": ["Cached Review Any Alerts entries with original SDK messages, scope, severity/blocker state, target identity, and Select/Auto Fix availability."],
        "sideEffects": "none; never opens or refreshes the SDK panel, changes selection, invokes Select/Auto Fix, builds, or uploads",
        "backupRestore": "not required; cached alert inspection is read-only and fails closed when the cache cannot be proven exact",
        "tags": ["vrchat-sdk", "builder", "alerts", "upload-blocker", "read-only"],
    },
    "vrcforge_build_and_upload_avatar": {
        "title": "Build And Upload Avatar",
        "permissionMode": "manual_confirmation_required",
        "inputs": ["Readiness-bound avatar/account/platform identity, create/update mode, explicit private/public metadata, style IDs and names, content warnings, tags, thumbnail mode/hash, and readiness digest."],
        "outputs": ["Job identity, exact build/upload phase events, SDK errors, Console delta, bundle facts, remote metadata before/requested/after, and per-surface commit state."],
        "sideEffects": "may create or update a remote VRChat avatar record, visibility, metadata, thumbnail, and bundle; never retries or rolls back remotely",
        "backupRestore": "local checkpoint cannot undo remote changes; every call requires one exact manual confirmation",
        "tags": ["avatar", "upload", "publish", "remote-write", "manual-confirmation"],
    },
    "vrcforge_get_avatar_upload_status": {
        "title": "Avatar Upload Status",
        "permissionMode": "read_only",
        "inputs": ["Exact project path and existing upload jobId."],
        "outputs": ["Authoritative Core job state, phase events, remote commit uncertainty, metadata readback, SDK errors, and Console delta."],
        "sideEffects": "none; polls an existing job only",
        "backupRestore": "not required; status polling never retries or restores",
        "tags": ["avatar", "upload", "status", "read-only"],
    },
    "vrcforge_preview_unity_constraint_conversion": {
        "title": "Unity Constraint Conversion Preview",
        "permissionMode": "read_only",
        "inputs": ["Exact saved scene, avatar, host path, Unity IConstraint type, and component index."],
        "outputs": ["Bound scene/component identities, sources, weight, state, SDK replacement type, and before digest."],
        "sideEffects": "none",
        "backupRestore": "not required; preview is read-only",
        "tags": ["avatar", "constraint", "vrchat-sdk", "preview"],
    },
    "vrcforge_convert_unity_constraint": {
        "title": "Convert Unity Constraint",
        "permissionMode": "controlled_write",
        "inputs": ["One preview-bound Unity IConstraint and exact scene/avatar/component identities."],
        "outputs": ["SDK-equivalent VRChat constraint readback, animation-rebinding coverage, Console delta, and commit state."],
        "sideEffects": "replaces one Unity constraint, may rebind referenced animation curves, and saves the scene",
        "backupRestore": "pre-write checkpoint required; no automatic rollback",
        "tags": ["avatar", "constraint", "vrchat-sdk", "write"],
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
        "inputs": ["Avatar path, descriptor fields to change, and optional source avatar path for complete Eye Look settings migration."],
        "outputs": ["Updated VRCAvatarDescriptor fields."],
        "sideEffects": "updates avatar descriptor viewpoint, lip sync, visemes, expression assets, eye look flag, or playable layer controllers after approval",
        "tags": ["avatar-descriptor", "avatar-authoring", "write"],
    },
    "vrcforge_preview_write_animation_curve": {
        "title": "Write Animation Curve Preview",
        "inputs": ["AnimationClip target binding plus curve data, or source binding fields for one lossless retarget."],
        "outputs": ["Planned AnimationClip binding change; no writes."],
        "sideEffects": "none",
        "tags": ["animation", "curve", "preview"],
    },
    "vrcforge_write_animation_curve": {
        "title": "Write Animation Curve",
        "inputs": ["AnimationClip target binding plus curve data, or source binding fields for one lossless retarget."],
        "outputs": ["Created, replaced, deleted, copied, or retargeted one AnimationClip curve binding."],
        "sideEffects": "creates or edits one AnimationClip binding; destination overwrite is denied unless explicitly enabled",
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
        "sideEffects": "modifies project VPM state through the sealed vrc-get adapter after checkpoint safety; VCC/ALCOM remain UI handoffs",
        "tags": ["package", "vpm", "write"],
    },
    "vrcforge_package_install_plan": {
        "title": "VPM Package Install Plan",
        "permissionMode": "preview",
        "inputs": ["VPM package id, Unity project path, optional preferred package manager."],
        "outputs": ["ALCOM/VCC UI handoff, sealed vrc-get command installer, or backend-neutral fallback plan."],
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
    *AVATAR_COMPOSITION_WORKFLOW_SKILLS,
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
            "after dependency installation, whenever the agent must explain what it "
            "can and cannot currently do, or for any VRCForge, Unity, MCP, bridge, "
            "editor-plugin, or Provider connection problem such as 'cannot connect', "
            "'not connected', or 'what should I do'"
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
            "registry changes. When the user asks what to do about a VRCForge-stack "
            "connection problem, run this Skill before filesystem, Shell, or repair tools. "
            "Do not use it for ordinary Internet, GitHub, or unrelated network support. "
            "Treat only observed evidence in the current report as fact. "
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
        "tags": ["builtin", "group", "self-check", "work-start", "unity", "mcp", "connection", "readiness"],
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
        "description": "Scan, try the installed dependency version first, diagnose compatibility failures, plan an available update when needed, and request one stable delegated avatar optimizer step at a time.",
        "category": "optimization",
        "permissionMode": "approval_required",
        "riskLevel": "high",
        "whenToUse": "request LAC apply, request AAO trace, request MA2BT conversion, report a missing optimizer dependency, diagnose an installed-version failure, check an available update, or request an approved optimizer install",
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
            "vrcforge_diagnose_package_install_errors",
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
        "description": "Run host shell commands and supervise Unity-project writes through approval and rollback.",
        "category": "debug",
        "permissionMode": "unity_project_writes_approval",
        "riskLevel": "high",
        "whenToUse": "shell command, terminal debug, file inspection, approved command execution",
        "inputs": ["Command, workspace root, cwd, and approval id."],
        "outputs": ["Risk classification, shell output, or pending approval."],
        "sideEffects": "host commands run directly; possible Unity-project writes require approval",
        "backupRestore": "Unity-project writes use the existing checkpoint and rollback transaction",
        "allowedTools": [
            "vrcforge_classify_shell",
            "vrcforge_execute_shell",
            "vrcforge_shell_process",
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
        shell_session_ports: ShellSessionPorts | None = None,
        skill_package_write_lock: AbstractContextManager[object] | None = None,
        background_activity_started: Callable[[str], Any] | None = None,
        runtime_turn_completed: Callable[[dict[str, Any]], None] | None = None,
        runtime_status_changed: Callable[[dict[str, Any]], None] | None = None,
        runtime_timeline_changed: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config_path = config_path
        self.audit_dir = audit_dir
        self.public_base_url = public_base_url.rstrip("/")
        self._tools: dict[str, AgentTool] = {}
        self._write_handlers: dict[str, AgentWriteHandler] = {}
        # Explicit per-instance additions are used only when a host registers a
        # reviewed Unity-facing handler that is not in the built-in external
        # catalogue. Internal registration alone never exposes a tool.
        self._external_mcp_tool_block_overrides: dict[tuple[str, bool], str] = {}
        self._approvals: dict[str, dict[str, Any]] = {}
        # External MCP confirmation proposals are bounded to this gateway
        # lifetime, protected by the gateway lock, authenticated by the MCP
        # bearer boundary, and consumed exactly once.
        self._external_mcp_write_confirmations: dict[str, dict[str, Any]] = {}
        # MCP over HTTP has no durable socket. A successfully authenticated
        # request marks the client connected for a bounded idle window; the
        # desktop refreshes faster than this window, so the indicator turns on
        # after a real client request and turns off after the client goes idle.
        self._external_mcp_last_seen_epoch = 0.0
        self._external_mcp_last_seen_at: str | None = None
        self._skill_package_write_lock_bound = skill_package_write_lock is not None
        self._skill_package_write_lock = skill_package_write_lock or nullcontext()
        self._lock = threading.RLock()
        self._runtime_shell_completion_ids: set[str] = set()
        self._runtime_shell_completion_order: list[str] = []
        self._runtime_continuation_accepting = True
        self._runtime_continuations_inflight: set[str] = set()
        self._runtime_continuation_condition = threading.Condition(self._lock)
        self._tool_agent_context: contextvars.ContextVar[str] = contextvars.ContextVar(
            "vrcforge_tool_agent",
            default="",
        )
        self._tool_owner_context: contextvars.ContextVar[str] = contextvars.ContextVar(
            "vrcforge_tool_owner",
            default="",
        )
        self._runtime_session_state = AgentRuntimeSessionState(
            AgentRuntimeSessionStatePorts(shared_state_lock=self._lock)
        )
        self._runtime_followup_queue = AgentRuntimeFollowupQueue(
            FollowupQueuePorts(path=self.audit_dir / "runtime-followups.json", lock=self._lock)
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
        self._memory_preferences_provider: Callable[[], Mapping[str, Any]] = lambda: {
            "memoryEnabled": True,
            "crossSessionEnabled": True,
        }
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
        if runtime_turn_completed is not None and not callable(runtime_turn_completed):
            raise TypeError("runtime_turn_completed must be callable")
        # App-lifetime, in-process notification only. It carries the already
        # redacted runtime turn projection and grants no execution authority.
        self._runtime_turn_completed = runtime_turn_completed or (lambda _payload: None)
        if runtime_status_changed is not None and not callable(runtime_status_changed):
            raise TypeError("runtime_status_changed must be callable")
        # App-lifetime, in-process notification only. It emits an allowlisted
        # phase plus bounded identifiers through the already
        # authenticated dashboard event channel. It carries no prompt,
        # reasoning, arguments, credentials, or execution authority.
        self._runtime_status_changed = runtime_status_changed or (lambda _payload: None)
        if runtime_timeline_changed is not None and not callable(runtime_timeline_changed):
            raise TypeError("runtime_timeline_changed must be callable")
        # Safe live timeline projection only. The callback receives the same
        # bounded event that is persisted on the final turn; raw arguments,
        # prompts, credentials, and model reasoning never cross this seam.
        self._runtime_timeline_changed = runtime_timeline_changed or (lambda _payload: None)
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
        project_chat_checkpoint_lock = threading.RLock()
        # 当用户把检查点存档目录迁出 C 盘后，这里缓存覆盖后的绝对路径，
        # 让 checkpoint_store_dir 走新位置；为空时回落到 audit_dir 下默认目录。
        self._checkpoint_store_override: Path | None = None
        from agent_checkpoint_recovery import (
            AgentCheckpointRecoveryService,
            CheckpointApprovalRecoveryPorts,
            CheckpointRecoveryPorts,
            CheckpointRecoveryState,
            CheckpointSkillsPort,
        )

        self._checkpoint_recovery_owner = AgentCheckpointRecoveryService(
            CheckpointRecoveryPorts(
                state=CheckpointRecoveryState(
                    checkpoint_storage_lock=self._checkpoint_storage_lock,
                    skill_package_write_lock=self._skill_package_write_lock,
                ),
                approval=CheckpointApprovalRecoveryPorts(
                    apply_recovery_blocks_writes=lambda recovery: self.approval_transactions._apply_recovery_blocks_writes(recovery),
                    create_pre_write_checkpoint=lambda approval, arguments: self.approval_transactions._create_pre_write_checkpoint(
                        approval, arguments
                    ),
                    finish_apply_recovery=lambda recovery, **kwargs: self.approval_transactions._finish_apply_recovery(
                        recovery, **kwargs
                    ),
                    resolve_apply_recoveries_for_checkpoint=lambda checkpoint_id, **kwargs: self.approval_transactions._resolve_apply_recoveries_for_checkpoint(
                        checkpoint_id, **kwargs
                    ),
                ),
                project_chat_checkpoint_lock=project_chat_checkpoint_lock,
                checkpoint_log_path=lambda: self.audit_dir / "checkpoints.jsonl",
                adjustment_checkpoint_log_path=lambda: self.audit_dir / "adjustment-checkpoints.json",
                apply_recovery_log_path=lambda: self.audit_dir / "apply-recoveries.jsonl",
                checkpoint_store_dir=lambda: self.checkpoint_store_dir,
                default_checkpoint_store_dir=lambda: self.audit_dir / "checkpoint-archives",
                audit_dir=lambda: self.audit_dir,
                user_constraints_path=lambda: self.user_constraints_path,
                skills=CheckpointSkillsPort(
                    write_lock=user_skill_lock,
                    user_skills_dir=lambda: self.skills.user_skills_dir,
                ),
                ensure_config=self.ensure_config,
                save_config=self.save_config,
                append_audit=self.append_audit,
                recent_audit_logs=lambda limit=100: self.approval_transactions.recent_audit_logs(limit),
                run_git=self._run_git,
                ensure_jsonl_append_boundary_locked=self._ensure_jsonl_append_boundary_locked,
            )
        )
        from agent_approval_transactions import (
            AgentApprovalTransactionService,
            ApprovalCheckpointRecoveryPorts,
            ApprovalGoalPorts,
            ApprovalSkillsPort,
            ApprovalTransactionPorts,
            ApprovalTransactionState,
        )

        self._approval_transaction_owner = AgentApprovalTransactionService(
            ApprovalTransactionPorts(
                state=ApprovalTransactionState(
                    shared_state_lock=self._lock,
                    approvals=self._approvals,
                    write_handlers=self._write_handlers,
                    in_flight_apply_writes=self._in_flight_apply_writes,
                    background_project_read_leases=self._background_project_read_leases,
                    checkpoint_storage_lock=self._checkpoint_storage_lock,
                    skill_package_write_lock=self._skill_package_write_lock,
                    skill_package_write_lock_bound=self._skill_package_write_lock_bound,
                ),
                checkpoint=ApprovalCheckpointRecoveryPorts(
                    active_apply_recoveries=self._checkpoint_recovery_owner._active_apply_recoveries,
                    append_apply_recovery_entry=self._checkpoint_recovery_owner._append_apply_recovery_entry,
                    append_checkpoint=self._checkpoint_recovery_owner._append_checkpoint,
                    build_checkpoint_rollback_coverage_audit=self._checkpoint_recovery_owner._build_checkpoint_rollback_coverage_audit,
                    classify_apply_recovery_incident=self._checkpoint_recovery_owner._classify_apply_recovery_incident,
                    create_archive_checkpoint=self._checkpoint_recovery_owner._create_archive_checkpoint,
                    create_local_state_checkpoint=self._checkpoint_recovery_owner._create_local_state_checkpoint,
                    create_project_chat_checkpoint=self._checkpoint_recovery_owner._create_project_chat_checkpoint,
                    resolve_checkpoint_project_root=self._checkpoint_recovery_owner._resolve_checkpoint_project_root,
                    prune_checkpoint_archives=self._checkpoint_recovery_owner.prune_checkpoint_archives,
                    project_chat_checkpoint_lock=lambda: self._checkpoint_recovery_owner.project_chat_checkpoint_lock,
                ),
                audit_log_path=lambda: self.audit_dir / "approvals.jsonl",
                skills=ApprovalSkillsPort(write_lock=user_skill_lock),
                shell_manual_approval_reason=lambda classification: self.shell.manual_approval_reason(classification),
                shell_execute_payload=lambda params: self.shell.execute_payload(params),
                checkpoint_pathspecs=self._checkpoint_pathspecs,
                is_unity_project_root=self._is_unity_project_root,
                normalize_project_category_allow_rules=self._normalize_project_category_allow_rules,
                run_git=self._run_git,
                signal_background_activity=self._signal_background_activity,
                tool_params_audit=self._tool_params_audit,
                validated_memory_evidence_for_applied_write=self._validated_memory_evidence_for_applied_write,
                with_user_constraints=self._with_user_constraints,
                write_handler_allows_future_category=self._write_handler_allows_future_category,
                write_handler_visible=self._write_handler_visible,
                append_audit=self.append_audit,
                authenticate=self.authenticate,
                call_tool=self.call_tool,
                ensure_config=self.ensure_config,
                read_user_constraints=self.read_user_constraints,
                roslyn_available=self.roslyn_available,
                save_config=self.save_config,
            ),
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
            return self.approval_transactions._new_approval(
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
                task_context=approval_task_context(
                    request.task_context,
                    tool=request.target_tool,
                    arguments=request.arguments,
                ),
            )

        def update_shell_approval_metadata(approval_id: str, metadata: dict[str, Any]) -> None:
            with self._lock:
                stored = self._approvals.get(approval_id)
                if stored is not None:
                    stored.update(metadata)

        def find_shell_approval(approval_id: str) -> dict[str, Any] | None:
            with self._lock:
                approval = self._approvals.get(approval_id)
            return approval or self.approval_transactions._load_approval_from_audit(approval_id)

        def default_shell_workspace_root() -> Path:
            app_dir = os.environ.get("VRCFORGE_APP_DIR", "").strip()
            return Path(app_dir).resolve() if app_dir else Path.cwd().resolve()

        def shell_session_finished(event: dict[str, Any]) -> None:
            safe_event = {
                "event": "shell_process_finished",
                "shellSessionId": str(event.get("shellSessionId") or ""),
                "status": str(event.get("status") or "unknown"),
                "exitCode": event.get("exitCode"),
                "timedOut": bool(event.get("timedOut")),
                "cancelled": bool(event.get("cancelled")),
                "terminationFailed": bool(event.get("terminationFailed")),
            }
            self.append_audit(safe_event)
            runtime_session_id = str(event.get("runtimeSessionId") or "")
            turn_id = str(event.get("turnId") or "")
            client_turn_id = str(event.get("clientTurnId") or "")
            if not (runtime_session_id or turn_id or client_turn_id):
                return
            status = (
                "cancelled"
                if safe_event["cancelled"]
                else "completed"
                if safe_event["status"] == "finished" and safe_event["exitCode"] == 0
                else "failed"
            )
            self._runtime_run_ledger.append(
                {
                    **safe_event,
                    "event": "runtime_shell_process_finished",
                    "status": status,
                    "sessionId": runtime_session_id,
                    "turnId": turn_id,
                    "clientTurnId": client_turn_id,
                }
            )
            if isinstance(event.get("taskSeed"), dict):
                try:
                    durable_terminal_event = {
                        **safe_event,
                        "runtimeSessionId": runtime_session_id,
                        "turnId": turn_id,
                        "clientTurnId": client_turn_id,
                        "result": (
                            summarize_owned_shell_result(ensure_dict(event.get("result")))
                            if isinstance(event.get("result"), dict)
                            else {}
                        ),
                    }
                    staged = self._runtime_run_ledger.stage_shell_continuation(
                        shell_session_id=safe_event["shellSessionId"],
                        task_seed=dict(event["taskSeed"]),
                        terminal_event=durable_terminal_event,
                    )
                    if staged:
                        self._dispatch_runtime_shell_continuation(safe_event["shellSessionId"])
                except Exception:  # noqa: BLE001 - process completion is already terminal.
                    self.append_audit(
                        {
                            "event": "runtime_shell_task_continuation_failed",
                            "shellSessionId": safe_event["shellSessionId"],
                            "status": "failed",
                        }
                    )

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
                    apply=lambda approval_id: self.approval_transactions.apply_approved({"approval_id": approval_id}),
                    auto_enabled=self.approval_transactions.auto_approval_enabled,
                    auto_execute=self.approval_transactions._auto_execute_approval,
                    execution_mode=lambda: self.ensure_config().execution_mode,
                    read_user_constraints=self.read_user_constraints,
                    redact=redact_sensitive,
                ),
                append_audit=self.append_audit,
                permission_audit_context=self.approval_transactions.permission_audit_context,
                cancellation_requested=(
                    lambda session_id, turn_id, client_turn_id: self._runtime_session_state.cancel_requested(
                        session_id=session_id,
                        turn_id=turn_id,
                        client_turn_id=client_turn_id,
                    )
                ),
                default_workspace_root=default_shell_workspace_root,
                is_unity_project_root=self._is_unity_project_root,
                error_factory=lambda detail, status: AgentGatewayError(detail, status_code=status),
                session_finished=shell_session_finished,
            ),
            process_ports=shell_process_ports,
            session_ports=shell_session_ports,
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
        def invoke_runtime_tool(
            tool: AgentTool,
            tool_params: dict[str, Any],
            tool_agent_name: str,
            tool_owner_id: str,
        ) -> Any:
            if tool.name in {
                "vrcforge_delegate_subagent",
                "vrcforge_vision_audit_multi",
            }:
                tool_params = dict(tool_params)
                tool_params["_runtimeTaskLinkAuthority"] = _RUNTIME_TASK_LINK_AUTHORITY
            agent_token = self._tool_agent_context.set(tool_agent_name)
            owner_token = self._tool_owner_context.set(tool_owner_id)
            try:
                return tool.handler(tool_params)
            finally:
                self._tool_owner_context.reset(owner_token)
                self._tool_agent_context.reset(agent_token)

        self._runtime_skill_executor = AgentRuntimeSkillExecutor(
            AgentRuntimeSkillExecutorPorts(
                ensure_config=self.ensure_config,
                tool_for_name=lambda name: self._tools.get(name),
                package_write_lock=self._skill_package_write_lock,
                prepare_runtime_skill=self.skills.prepare_runtime_skill,
                package_audit_context=lambda skill: self._runtime_skill_package_audit_context_locked(skill),
                computer_use_model_invocable=self._desktop.computer_use_model_invocable,
                tool_visible=self._tool_runtime_visible,
                tool_params_audit=self._tool_params_audit,
                read_user_constraints=self.read_user_constraints,
                inject_user_constraints=self._inject_user_constraints,
                append_audit=self.append_audit,
                redact=redact_sensitive,
                summarize_params=summarize_params,
                ensure_string_list=ensure_string_list,
                build_runtime_skill_payload=build_runtime_skill_payload,
                invoke_tool=invoke_runtime_tool,
                blocked_skills=frozenset(RUNTIME_BLOCKED_SKILLS),
                direct_categories=frozenset(RUNTIME_DIRECT_SKILL_CATEGORIES),
                direct_write_tools=frozenset({"vrcforge_shell_process"}),
            )
        )

    @staticmethod
    def consume_runtime_task_link(
        params: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """Consume a task link injected only by the in-process runtime loop."""

        trusted = params.pop("_runtimeTaskLinkAuthority", None) is _RUNTIME_TASK_LINK_AUTHORITY
        task_seed = params.pop("_taskSeed", None)
        runtime_session_id = str(params.pop("_runtimeSessionId", "") or "").strip()
        params.pop("_runtimeClientTurnId", None)
        if not trusted:
            return None, ""
        return (dict(task_seed) if isinstance(task_seed, dict) and task_seed else None), runtime_session_id

    @property
    def desktop(self) -> DesktopComputerUseService:
        return self._desktop

    @property
    def shell(self) -> AgentShellService:
        return self._shell

    @staticmethod
    def _runtime_shell_owner(turn_id: str, client_turn_id: str, session_id: str) -> str:
        scope = session_id or client_turn_id or turn_id or "runtime"
        origin = turn_id or (f"client:{client_turn_id}" if client_turn_id else "session")
        scope_bytes = json.dumps(scope, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        origin_bytes = json.dumps(origin, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        scope_digest = hashlib.sha256(scope_bytes).hexdigest()
        origin_digest = hashlib.sha256(origin_bytes).hexdigest()
        return f"runtime-session:{scope_digest}|origin:{origin_digest}"

    def _emit_runtime_status(
        self,
        phase: str,
        *,
        session_id: str,
        turn_id: str,
        client_turn_id: str,
    ) -> None:
        allowed_phases = {
            "preparing",
            "waiting_for_model",
            "running_tool",
            "waiting_for_approval",
            "verifying",
        }
        if phase not in allowed_phases or not client_turn_id:
            return
        payload = {
            "sessionId": summarize_text(session_id, 160),
            "turnId": summarize_text(turn_id, 160),
            "clientTurnId": summarize_text(client_turn_id, 160),
            "phase": phase,
        }
        try:
            self._runtime_status_changed(payload)
        except Exception:
            # A presentation-only event must never fail or delay the Runtime.
            return

    def execute_shell_tool(self, params: dict[str, Any], *, unity_project_access: bool = False) -> dict[str, Any]:
        agent_name = self._tool_agent_context.get() or "external-agent"
        owner_id = self._tool_owner_context.get() or f"agent:{agent_name}"
        trusted = dict(params or {})
        for key in (
            "agent_name", "agentName", "agent_id", "agentId",
            "runtime_session_id", "runtimeSessionId", "owner_session_id", "ownerSessionId",
            "_trusted_owner_id", "_trustedOwnerId", "unity_project_access", "_unityProjectAccess",
        ):
            trusted.pop(key, None)
        trusted["_trusted_owner_id"] = owner_id
        classification = self.shell.classify(trusted)
        if classification.get("protectionScope") == "host" and classification.get("risk") == "low":
            trusted.setdefault("yieldMs", 10_000)
            trusted.setdefault("timeout", 30 * 60)
        return self.shell.execute(trusted, agent_name=agent_name, unity_project_access=unity_project_access)

    def control_shell_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_name = self._tool_agent_context.get() or "external-agent"
        owner_id = self._tool_owner_context.get() or f"agent:{agent_name}"
        trusted = dict(params or {})
        for key in (
            "agent_name", "agentName", "agent_id", "agentId",
            "runtime_session_id", "runtimeSessionId", "owner_session_id", "ownerSessionId",
            "_trusted_owner_id", "_trustedOwnerId",
        ):
            trusted.pop(key, None)
        trusted["_trusted_owner_id"] = owner_id
        return self.shell.process(trusted, agent_name=agent_name)

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

    @property
    def approval_transactions(self) -> AgentApprovalTransactionService:
        return self._approval_transaction_owner

    @property
    def checkpoint_recovery(self) -> AgentCheckpointRecoveryService:
        return self._checkpoint_recovery_owner

    def bind_runtime_planner(self, planner: RuntimePlannerService) -> None:
        if not isinstance(planner, RuntimePlannerService):
            raise TypeError("runtime planner must be a RuntimePlannerService")
        if self._runtime_planner is not None:
            raise RuntimeError("runtime planner is already bound")
        self._runtime_planner = planner

    def reconcile_runtime_shell_continuations(self) -> dict[str, Any]:
        """Recover only never-dispatched Shell continuations after startup.

        A durable ``dispatching`` owner may already have produced external side
        effects before its process stopped, so restart always closes that state
        as ``interrupted`` instead of replaying it.
        """

        with self._lock:
            accepting = self._runtime_continuation_accepting
        if not accepting:
            return {
                "ok": False,
                "schema": "vrcforge.runtime_shell_continuation_reconcile.v1",
                "pendingDispatched": 0,
                "delivered": 0,
                "interrupted": 0,
            }
        states = self._runtime_run_ledger.shell_continuation_states(limit=0)
        interrupted = 0
        dispatched = 0
        delivered = 0
        for state in states:
            if str(state.get("shellContinuationState") or "") != "dispatching":
                continue
            shell_session_id = str(state.get("shellSessionId") or "").strip()
            if self._runtime_run_ledger.interrupt_shell_continuation(
                shell_session_id,
                reason="process_restart_after_dispatch_claim",
            ):
                interrupted += 1
                self.record_interrupted_runtime_task(
                    task_seed=ensure_dict(state.get("continuationTaskSeed")),
                    continuation_source="shell_process_finished",
                    owned_id=shell_session_id,
                    summary=(
                        "VRCForge restarted after this background Shell continuation was claimed. "
                        "Its result is ambiguous; inspect the current state before retrying."
                    ),
                )
        for state in states:
            if str(state.get("shellContinuationState") or "") != "pending":
                continue
            shell_session_id = str(state.get("shellSessionId") or "").strip()
            dispatched += 1
            if self._dispatch_runtime_shell_continuation(shell_session_id):
                delivered += 1
            else:
                interrupted += 1
        return {
            "ok": True,
            "schema": "vrcforge.runtime_shell_continuation_reconcile.v1",
            "pendingDispatched": dispatched,
            "delivered": delivered,
            "interrupted": interrupted,
        }

    def start_runtime_continuations(self) -> None:
        """Open continuation admission for one app-owned backend lifecycle."""

        with self._lock:
            if self._runtime_continuations_inflight:
                raise RuntimeError("Cannot restart runtime continuations while work is active.")
            self._runtime_continuation_accepting = True

    def shutdown_runtime_continuations(self, timeout_seconds: float = 5.0) -> dict[str, Any]:
        """Stop new continuation dispatch and boundedly drain real callback owners."""

        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("continuation shutdown timeout must be non-negative") from exc
        if timeout != timeout or timeout < 0:
            raise ValueError("continuation shutdown timeout must be non-negative")
        deadline = time.monotonic() + min(timeout, 30.0)
        with self._lock:
            self._runtime_continuation_accepting = False
            while self._runtime_continuations_inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._runtime_continuation_condition.wait(remaining)
            timed_out = sorted(self._runtime_continuations_inflight)
        return {
            "ok": not timed_out,
            "shutdown": True,
            "timedOutShellSessionIds": timed_out,
        }

    def _ensure_runtime_continuation_accepting(self) -> None:
        """Fail closed before a resumed background task can start another action."""

        with self._lock:
            if not self._runtime_continuation_accepting:
                raise AgentGatewayError(
                    "The task continuation was interrupted because app shutdown started.",
                    status_code=409,
                )

    def configure_paths(self, config_path: Path, audit_dir: Path) -> None:
        with self._lock:
            self.config_path = config_path
            self.audit_dir = audit_dir
            self._approvals.clear()
            self._runtime_session_state.clear()
            self._desktop.configure_paths(audit_dir)

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

    @property
    def agent_memory_log_path(self) -> Path:
        return self.audit_dir / "agent-memory.jsonl"

    @property
    def agent_memory_store(self) -> AgentMemoryStore:
        return self._agent_memory_store

    def set_memory_preferences_provider(
        self,
        provider: Callable[[], Mapping[str, Any]],
    ) -> None:
        if not callable(provider):
            raise TypeError("memory preferences provider must be callable")
        self._memory_preferences_provider = provider

    def memory_preferences(self) -> dict[str, bool]:
        try:
            raw = self._memory_preferences_provider()
        except Exception:
            raw = {}
        memory_enabled = bool(raw.get("memoryEnabled", False))
        cross_session_enabled = bool(raw.get("crossSessionEnabled", False))
        return {
            "memoryEnabled": memory_enabled,
            "crossSessionEnabled": memory_enabled and cross_session_enabled,
        }

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

    def register_external_mcp_unity_tool(self, name: str, block: str) -> None:
        """Explicitly share one already-registered Unity handler with external MCP."""

        normalized_name = str(name or "").strip()
        normalized_block = str(block or "").strip().lower()
        if normalized_block not in EXTERNAL_MCP_TOOL_BLOCKS:
            raise ValueError(f"Unknown external MCP tool block: {normalized_block or 'missing'}")
        has_read = normalized_name in self._tools
        has_write = normalized_name in self._write_handlers
        if has_read == has_write:
            raise ValueError(
                "External MCP sharing requires exactly one registered read tool or write handler."
            )
        self._external_mcp_tool_block_overrides[(normalized_name, has_write)] = normalized_block

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
                "agent_budget_policy": {},
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
                agent_budget_policy=dict(raw.get("agent_budget_policy") or {}) if isinstance(raw.get("agent_budget_policy"), dict) else {},
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
                "agent_budget_policy": dict(config.agent_budget_policy or {}),
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

    def build_manifest(self, exposure_layer: str = EXPOSURE_LAYER_EXECUTION) -> dict[str, Any]:
        exposure_layer = normalize_exposure_layer(exposure_layer)
        config = self.ensure_config()
        permission_context = self.approval_transactions.permission_audit_context(config)
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
            "writeTargets": self.approval_transactions.visible_write_targets(config, exposure_layer),
            "skills": self.skills.build_skill_registry(config, exposure_layer)["skills"],
            "userConstraints": self._serialize_user_constraints(user_constraints),
        }

    def build_external_mcp_tools(
        self,
        exposure_layer: str = EXPOSURE_LAYER_EXECUTION,
        tool_blocks: Any = None,
    ) -> list[dict[str, Any]]:
        """Return the independent, block-scoped external Unity tool surface."""

        layer = normalize_exposure_layer(exposure_layer)
        selected_blocks = normalize_external_mcp_tool_blocks(tool_blocks)
        config = self.ensure_config()
        tools: list[dict[str, Any]] = []
        for tool in self._tools.values():
            block = self._external_mcp_read_tool_block(tool, config)
            if not block or block not in selected_blocks:
                continue
            serialized = self._serialize_tool(tool, config)
            serialized["_meta"] = {
                **dict(serialized.get("_meta") or {}),
                "permission": "ReadOnly",
                "toolBlock": block,
            }
            tools.append(serialized)
        if layer == EXPOSURE_LAYER_EXECUTION and config.allow_write_requests:
            for handler in self._write_handlers.values():
                block = self._external_mcp_write_handler_block(handler, config)
                if not block or block not in selected_blocks:
                    continue
                tools.append(self._serialize_external_mcp_write_handler(handler, block))
        tools.sort(key=lambda item: str(item.get("name") or ""))
        return tools

    def external_mcp_tool_block_for_name(self, name: str, *, write: bool) -> str:
        """Return the canonical external Unity block reused by the internal Unity tree."""

        normalized = str(name or "").strip()
        config = self.ensure_config()
        if write:
            handler = self._write_handlers.get(normalized)
            return self._external_mcp_write_handler_block(handler, config) if handler else ""
        tool = self._tools.get(normalized)
        return self._external_mcp_read_tool_block(tool, config) if tool else ""

    def external_mcp_tool_block_index(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a compact external-only tree; definitions stay lazy per block."""

        descriptions = {
            "core": "Connection, compile state, avatar roots, and tool-block discovery.",
            "project": "Project lifecycle, manager catalogues, and package management.",
            "avatar": "Hierarchy, components, descriptors, animation, parameters, and menus.",
            "assets": "Assets, prefabs, outfit packages, and wardrobes.",
            "materials": "Materials, shaders, textures, and tuning.",
            "integrations": "Installed Unity plugin adapters; expand this branch before loading a family.",
            "integrations/modular-avatar": "Modular Avatar inspection, Setup Outfit, and atomic component authoring.",
            "integrations/vrcfury": "VRCFury inspection and public-API-backed Toggle or Armature Link authoring.",
            "integrations/gesture-manager": "Gesture Manager Play Mode status, menu identity, and atomic runtime parameters.",
            "skills": "User Skill package operations; expand this branch before loading a package format.",
            "skills/vsk": "Read-only preflight and atomic import/export for local .vsk Skill packages.",
            "optimization": "Avatar performance audits and optimization applies.",
            "checkpoint": "Checkpoint, backup, recovery, and explicit restore tools.",
            "diagnostics": "Screenshots, validation, logs, and runtime diagnostics.",
            "encryption": "Optional private avatar-encryption compatibility tools.",
        }
        config = self.ensure_config()
        requested_block = str((params or {}).get("block") or "").strip().casefold()
        if requested_block and requested_block not in EXTERNAL_MCP_TOOL_BLOCKS:
            raise AgentGatewayError(
                f"Unknown external MCP tool block: {requested_block}",
                status_code=400,
            )

        def counts(block: str) -> tuple[int, int]:
            read_count = sum(
                1
                for tool in self._tools.values()
                if self._external_mcp_read_tool_block(tool, config) == block
            )
            write_count = sum(
                1
                for handler in self._write_handlers.values()
                if self._external_mcp_write_handler_block(handler, config) == block
            )
            return read_count, write_count

        def compact_tools(block: str) -> list[dict[str, str]]:
            items = [
                {"name": tool.name, "shortName": tool.name.removeprefix("vrcforge_"), "mode": "read"}
                for tool in self._tools.values()
                if self._external_mcp_read_tool_block(tool, config) == block
            ]
            items.extend(
                {"name": handler.name, "shortName": handler.name.removeprefix("vrcforge_"), "mode": "write"}
                for handler in self._write_handlers.values()
                if self._external_mcp_write_handler_block(handler, config) == block
            )
            return sorted(items, key=lambda item: item["name"])

        children: list[dict[str, Any]] = []
        for index, block in enumerate(EXTERNAL_MCP_TOOL_BLOCK_ROOTS, start=1):
            descendants = EXTERNAL_MCP_TOOL_BLOCK_BRANCHES.get(block, ())
            if descendants:
                branch_children = []
                total_read = 0
                total_write = 0
                for child_index, child in enumerate(descendants, start=1):
                    read_count, write_count = counts(child)
                    total_read += read_count
                    total_write += write_count
                    branch_children.append(
                        {
                            "index": f"external.{index}.{child_index}",
                            "block": child,
                            "description": descriptions[child],
                            "readToolCount": read_count,
                            "writeToolCount": write_count,
                            "toolNames": [item["name"] for item in compact_tools(child)],
                            **({"tools": compact_tools(child)} if requested_block == child else {}),
                            "loadWith": {"method": "tools/list", "toolBlocks": [child]},
                        }
                    )
                children.append(
                    {
                        "index": f"external.{index}",
                        "block": block,
                        "description": descriptions[block],
                        "readToolCount": total_read,
                        "writeToolCount": total_write,
                        "children": branch_children,
                    }
                )
                continue
            read_count, write_count = counts(block)
            children.append(
                {
                    "index": f"external.{index}",
                    "block": block,
                    "description": descriptions[block],
                    "readToolCount": read_count,
                    "writeToolCount": write_count,
                    "toolNames": [item["name"] for item in compact_tools(block)],
                    **({"tools": compact_tools(block)} if requested_block == block else {}),
                    "loadWith": {"method": "tools/list", "toolBlocks": [block]},
                }
            )
        return {
            "ok": True,
            "schema": "vrcforge.external_tool_blocks.v1",
            "root": "external",
            "definitionsLoaded": False,
            "selectedBlock": requested_block,
            "children": (
                [item for item in children if item.get("block") == requested_block]
                if requested_block
                and not any(
                    requested_block in descendants
                    for descendants in EXTERNAL_MCP_TOOL_BLOCK_BRANCHES.values()
                )
                else [
                    {
                        **item,
                        "children": (
                            list(item.get("children", []))
                            if not requested_block
                            else [
                                child
                                for child in item.get("children", [])
                                if child.get("block") == requested_block
                            ]
                        ),
                    }
                    for item in children
                    if not requested_block or any(
                        child.get("block") == requested_block
                        for child in item.get("children", [])
                    )
                ]
            ),
        }

    def _external_mcp_read_tool_block(
        self,
        tool: AgentTool,
        config: AgentGatewayConfig,
    ) -> str:
        block = self._external_mcp_tool_block_overrides.get(
            (tool.name, False),
            _external_mcp_tool_block(tool.name, write=False),
        )
        if not block:
            return ""
        if tool.name in EXTERNAL_MCP_INTERNAL_LOOP_TOOLS:
            return ""
        if tool.name.startswith("vrcforge_progress_"):
            return ""
        if tool.name.endswith("_request"):
            return ""
        if tool.write or tool.advanced or tool.requires_user_activation:
            return ""
        return block

    def _external_mcp_read_tool_visible(
        self,
        tool: AgentTool,
        config: AgentGatewayConfig,
    ) -> bool:
        return bool(self._external_mcp_read_tool_block(tool, config))

    def _external_mcp_write_handler_block(
        self,
        handler: AgentWriteHandler,
        config: AgentGatewayConfig,
    ) -> str:
        del config
        block = self._external_mcp_tool_block_overrides.get(
            (handler.name, True),
            _external_mcp_tool_block(handler.name, write=True),
        )
        if not block or handler.advanced:
            return ""
        if (
            handler.name in WRAPPER_ONLY_WRITE_TARGETS
            and not external_mcp_typed_wrapper_allowed(handler)
        ):
            return ""
        return block

    def _serialize_external_mcp_write_handler(
        self,
        handler: AgentWriteHandler,
        block: str,
    ) -> dict[str, Any]:
        risk_level = normalize_risk_level(handler.risk_level)
        return {
            "name": handler.name,
            "title": handler.name.replace("vrcforge_", "").replace("_", " ").title(),
            "description": tool_usage_description(
                handler.name,
                handler.description,
                write=True,
            ),
            "category": self._registry_category("supervised-write", handler.name),
            "write": True,
            "riskLevel": risk_level,
            "requiresApproval": False,
            "inputSchema": canonical_unity_write_tool_input_schema(handler.name),
            "outputSchema": {"type": "object", "additionalProperties": True},
            "_meta": {
                "permission": "Write",
                "toolBlock": block,
                "confirmationPolicy": "risk_based",
                "baseRiskLevel": risk_level,
                "checkpointPolicy": (
                    "required_before_mutation"
                    if handler.pre_write_checkpoint_required
                    else "handler_managed_atomic_receipt"
                ),
            },
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
        pending = [item for item in self.approval_transactions.list_approvals(include_expired=False) if item.get("status") == "pending"]
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
            "permission": self.approval_transactions.permission_state(config),
            "userConstraints": self._serialize_user_constraints(user_constraints, include_error=True),
            "shellExecutor": {
                "status": "ok",
                "defaultRunner": SHELL_OWNER_RUNNER_NATIVE,
                "fallbackRunner": SHELL_OWNER_RUNNER_POWERSHELL,
                "shell": "powershell",
                "shellRole": "fallback",
                "timeoutSeconds": 120,
            },
            "planner": {
                "mode": "provider_only",
                "providerRequired": True,
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

    def mark_external_mcp_activity(self) -> None:
        """Record a successful authentication at the existing MCP boundary."""

        now_epoch = time.time()
        now_iso = datetime.fromtimestamp(now_epoch, timezone.utc).isoformat()
        with self._lock:
            self._external_mcp_last_seen_epoch = now_epoch
            self._external_mcp_last_seen_at = now_iso

    def external_mcp_activity_status(self) -> dict[str, Any]:
        """Return a truthful bounded connection signal for the desktop UI."""

        config = self.ensure_config()
        now_epoch = time.time()
        with self._lock:
            last_seen_epoch = self._external_mcp_last_seen_epoch
            last_seen_at = self._external_mcp_last_seen_at
        age_seconds = max(0.0, now_epoch - last_seen_epoch) if last_seen_epoch > 0 else None
        connected = bool(
            config.enabled
            and age_seconds is not None
            and age_seconds <= EXTERNAL_MCP_CONNECTION_IDLE_SECONDS
        )
        return {
            "gatewayEnabled": config.enabled,
            "connected": connected,
            "lastSeenAt": last_seen_at,
            "ageSeconds": age_seconds,
            "idleTimeoutSeconds": EXTERNAL_MCP_CONNECTION_IDLE_SECONDS,
        }

    @staticmethod
    def _external_mcp_visible_value(value: Any) -> Any:
        forbidden = {
            "approval",
            "approvalId",
            "approval_id",
            "fullPermission",
            "permissionLabel",
            "permissionMode",
            "plannerObservation",
            "taskCompletion",
            "taskContinuation",
            "terminalPlan",
        }
        if isinstance(value, Mapping):
            return {
                str(key): AgentGateway._external_mcp_visible_value(item)
                for key, item in value.items()
                if str(key) not in forbidden
            }
        if isinstance(value, list):
            return [AgentGateway._external_mcp_visible_value(item) for item in value]
        return value

    def _call_external_mcp_read_tool(
        self,
        tool: AgentTool,
        params: dict[str, Any],
        *,
        agent_name: str,
    ) -> dict[str, Any]:
        """Call one external read tool with the same canonical outcome as the internal Agent."""

        request_id = (
            f"mcpread_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_"
            f"{secrets.token_hex(4)}"
        )
        started_at = time.perf_counter()
        params_summary = self._tool_params_audit(tool.name, params)
        user_constraints = self.read_user_constraints()
        tool_params = self._inject_user_constraints(params, tool, user_constraints)
        core_call_audits: list[dict[str, Any]] = []
        try:
            with capture_unity_mcp_core_call_audits() as core_call_audits:
                agent_token = self._tool_agent_context.set(agent_name)
                owner_token = self._tool_owner_context.set(f"agent:{agent_name}")
                try:
                    raw_result = tool.handler(tool_params)
                finally:
                    self._tool_owner_context.reset(owner_token)
                    self._tool_agent_context.reset(agent_token)
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            request_trace = (
                {"gatewayRequestId": request_id, "unityCoreCallAudits": core_call_audits}
                if core_call_audits
                else None
            )
            outcome = normalize_agent_tool_result(
                raw_result,
                fallback_summary=tool.description,
                write=False,
            )
            outcome_status = str(outcome.get("status") or "failed")
            explicit_failure = outcome_status == "failed"
            status = "failed" if explicit_failure else outcome_status
            result_summary = summarize_params(
                raw_result if isinstance(raw_result, dict) else {"result": raw_result}
            )
            self.append_audit(
                {
                    "event": "external_mcp_tool_call",
                    "requestId": request_id,
                    "tool": tool.name,
                    "agent": agent_name,
                    "paramsSummary": params_summary,
                    "resultSummary": result_summary,
                    "durationMs": duration_ms,
                    "status": status,
                    **({"requestTrace": request_trace} if request_trace is not None else {}),
                }
            )
            payload: dict[str, Any] = {
                "ok": not explicit_failure,
                "status": status,
                "tool": tool.name,
                "result": self._external_mcp_visible_value(raw_result),
                "outcome": self._external_mcp_visible_value(outcome),
                "gatewayContext": {
                    "requestId": request_id,
                    "durationMs": duration_ms,
                    "redactionPolicy": "sensitive_fields_only",
                    **({"requestTrace": request_trace} if request_trace is not None else {}),
                },
            }
            if explicit_failure and isinstance(raw_result, Mapping):
                error_value: Any = ""
                for key in ("error", "message", "reason"):
                    if key in raw_result:
                        error_value = raw_result[key]
                        break
                error_object = build_external_tool_error(
                    error=error_value,
                    failure_layer="external_mcp_read_handler",
                    failure_phase="tool_returned_rejection",
                    operation_kind="read",
                    tool=tool.name,
                    tool_routing_started=True,
                    mutation_started=False,
                    committed=False,
                    retryable=False,
                    checkpoint_recovery_required=False,
                    temporary_cleanup_required=False,
                    raw_result=raw_result,
                )
                payload["error"] = self._external_mcp_visible_value(error_object["error"])
                payload["errorDetails"] = self._external_mcp_visible_value(error_object)
            return redact_sensitive(payload)
        except Exception as exc:  # noqa: BLE001 - preserve the complete external error contract.
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            request_trace = (
                {"gatewayRequestId": request_id, "unityCoreCallAudits": core_call_audits}
                if core_call_audits
                else None
            )
            legacy_details = external_exception_details(exc)
            raw_result = external_exception_raw_result(legacy_details)
            error_object = build_external_tool_error(
                exception=exc,
                raw_result=raw_result,
                failure_layer="external_mcp_read_handler",
                failure_phase="tool_handler_exception",
                operation_kind="read",
                tool=tool.name,
                tool_routing_started=None,
                mutation_started=False,
                committed=False,
                checkpoint_recovery_required=False,
                temporary_cleanup_required=False,
            )
            self.append_audit(
                {
                    "event": "external_mcp_tool_call",
                    "requestId": request_id,
                    "tool": tool.name,
                    "agent": agent_name,
                    "paramsSummary": params_summary,
                    "resultSummary": {"status": "error"},
                    "durationMs": duration_ms,
                    "status": "error",
                    "error": str(exc),
                    **({"requestTrace": request_trace} if request_trace is not None else {}),
                }
            )
            payload = {
                "ok": False,
                "status": "failed",
                "tool": tool.name,
                "result": self._external_mcp_visible_value(raw_result),
                "error": self._external_mcp_visible_value(error_object["error"]),
                "errorDetails": self._external_mcp_visible_value(error_object),
                "gatewayContext": {
                    "requestId": request_id,
                    "durationMs": duration_ms,
                    "redactionPolicy": "sensitive_fields_only",
                    **({"requestTrace": request_trace} if request_trace is not None else {}),
                },
            }
            payload["outcome"] = self._external_mcp_visible_value(
                normalize_agent_tool_result(
                    payload,
                    fallback_summary=tool.description,
                    write=False,
                )
            )
            return redact_sensitive(payload)

    def call_external_mcp_tool(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        agent_name: str = "mcp-agent",
    ) -> dict[str, Any]:
        """Dispatch a real external tool without running VRCForge's Agent loop."""

        config = self.ensure_config()
        if not config.enabled:
            raise AgentGatewayError("Agent Gateway is disabled in config/agent_gateway.json.", status_code=403)
        arguments = dict(params or {})
        write_handler = self._write_handlers.get(name)
        if write_handler is None:
            tool = self._tools.get(name)
            if tool is None or not self._external_mcp_read_tool_visible(tool, config):
                raise AgentGatewayError(f"Unknown or unavailable MCP tool: {name}", status_code=404)
            return self._call_external_mcp_read_tool(
                tool,
                arguments,
                agent_name=agent_name,
            )
        if (
            not self._external_mcp_write_handler_block(write_handler, config)
            or not config.allow_write_requests
        ):
            raise AgentGatewayError(f"Unknown or unavailable MCP tool: {name}", status_code=404)

        confirmation = arguments.pop("confirmation", None)
        request_arguments_digest = stable_hash(
            json.dumps(arguments, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        )
        if confirmation is not None:
            if not isinstance(confirmation, Mapping):
                return self._invalid_external_mcp_confirmation(
                    name,
                    "confirmation must be an object returned by the first tool call.",
                )
            return self._confirm_external_mcp_write(
                name,
                request_arguments_digest,
                dict(confirmation),
                agent_name=agent_name,
            )

        try:
            prepared = self.approval_transactions.prepare_external_mcp_write(name, arguments)
        except AgentGatewayError as exc:
            return self._external_mcp_no_write_error(name, "write_preparation", exc)
        except Exception as exc:  # noqa: BLE001 - preserve the redacted causal chain for the calling Agent.
            return self._external_mcp_no_write_error(
                name,
                "write_preparation",
                exc,
            )
        if prepared.get("requiresUserConfirmation"):
            try:
                return self._propose_external_mcp_write(
                    prepared,
                    request_arguments_digest=request_arguments_digest,
                )
            except AgentGatewayError as exc:
                return self._external_mcp_no_write_error(
                    name,
                    "confirmation_proposal",
                    exc,
                )
        try:
            applied = self.approval_transactions.execute_prepared_external_mcp_write(
                prepared,
                agent_name=agent_name,
            )
        except AgentGatewayError as exc:
            return self._external_mcp_no_write_error(name, "transaction_start", exc)
        return self._external_mcp_write_result(name, applied)

    def _propose_external_mcp_write(
        self,
        prepared: Mapping[str, Any],
        *,
        request_arguments_digest: str,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        config = self.ensure_config()
        ttl_seconds = max(30, min(int(config.approval_timeout_seconds or 600), 900))
        expires_at = now + timedelta(seconds=ttl_seconds)
        operation_id = f"mcpop_{now.strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(8)}"
        record = {
            "operationId": operation_id,
            "targetTool": str(prepared.get("targetTool") or ""),
            "argumentsDigest": request_arguments_digest,
            "preparedArgumentsDigest": str(prepared.get("argumentsDigest") or ""),
            "createdAt": now.isoformat(),
            "createdEpoch": now.timestamp(),
            "expiresAt": expires_at.isoformat(),
            "expiresEpoch": expires_at.timestamp(),
            "prepared": dict(prepared),
        }
        with self._lock:
            self._prune_external_mcp_confirmations_locked(now.timestamp())
            if len(self._external_mcp_write_confirmations) >= EXTERNAL_MCP_CONFIRMATION_MAX_PENDING:
                raise AgentGatewayError(
                    "Too many external MCP confirmations are pending. Let one expire or finish it first.",
                    status_code=429,
                )
            self._external_mcp_write_confirmations[operation_id] = record
        confirmation = {
            "schema": "vrcforge.external_write_confirmation.v1",
            "operationId": operation_id,
            "targetTool": record["targetTool"],
            "argumentsDigest": record["argumentsDigest"],
            "expiresAt": record["expiresAt"],
            "requiredDecision": ["approve", "reject"],
            "decisionPlacement": "arguments.confirmation.decision",
        }
        self.append_audit(
            {
                "event": "external_mcp_confirmation_proposed",
                "operation": confirmation,
                "riskLevel": str(prepared.get("riskLevel") or ""),
            }
        )
        return {
            "ok": True,
            "status": "user_confirmation_required",
            "tool": record["targetTool"],
            "riskLevel": str(prepared.get("riskLevel") or ""),
            "reason": str(prepared.get("confirmationReason") or ""),
            "preview": redact_sensitive(prepared.get("preview")),
            "confirmation": confirmation,
            "resubmit": {
                "placement": "arguments.confirmation",
                "decisionField": "arguments.confirmation.decision",
                "preserveOtherArgumentsExactly": True,
            },
            "mutationStarted": False,
            "committed": False,
            "commitState": "not_started",
            "message": (
                "No write has started. The external Agent must show this risk to the user and "
                "repeat the same tool call with all original arguments unchanged, add the returned "
                "confirmation object at arguments.confirmation, and set "
                "arguments.confirmation.decision to approve or reject."
            ),
        }

    def _confirm_external_mcp_write(
        self,
        target_tool: str,
        request_arguments_digest: str,
        confirmation: Mapping[str, Any],
        *,
        agent_name: str,
    ) -> dict[str, Any]:
        operation_id = str(confirmation.get("operationId") or "").strip()
        supplied_digest = str(confirmation.get("argumentsDigest") or "").strip()
        decision = str(
            confirmation.get("decision")
            or confirmation.get("userDecision")
            or ""
        ).strip().casefold()
        now_epoch = datetime.now(timezone.utc).timestamp()
        with self._lock:
            self._prune_external_mcp_confirmations_locked(now_epoch)
            record = self._external_mcp_write_confirmations.get(operation_id)
            if record is None:
                return self._invalid_external_mcp_confirmation(
                    target_tool,
                    "The confirmation operation is missing, expired, or already consumed.",
                )
            expected_target = str(record.get("targetTool") or "")
            expected_digest = str(record.get("argumentsDigest") or "")
            actual_target = str(target_tool or "")
            actual_digest = str(request_arguments_digest or "")
            if (
                not supplied_digest
                or not hmac.compare_digest(supplied_digest, expected_digest)
                or not hmac.compare_digest(actual_digest, expected_digest)
                or actual_target != expected_target
                or str(confirmation.get("targetTool") or expected_target) != expected_target
            ):
                return self._invalid_external_mcp_confirmation(
                    actual_target,
                    "The confirmation is not bound to these exact tool arguments.",
                )
            if decision not in {"approve", "reject"}:
                return self._invalid_external_mcp_confirmation(
                    actual_target,
                    "The external Agent must return the user's decision as approve or reject.",
                )
            self._external_mcp_write_confirmations.pop(operation_id, None)

        if decision == "reject":
            self.append_audit(
                {
                    "event": "external_mcp_confirmation_rejected",
                    "operationId": operation_id,
                    "targetTool": expected_target,
                    "argumentsDigest": supplied_digest,
                }
            )
            return {
                "ok": True,
                "status": "rejected",
                "tool": expected_target,
                "operationId": operation_id,
                "mutationStarted": False,
                "committed": False,
                "commitState": "not_started",
                "message": "The user rejected the external MCP write. No mutation occurred.",
            }

        stored_prepared = ensure_dict(record.get("prepared"))
        self.append_audit(
            {
                "event": "external_mcp_confirmation_accepted",
                "operationId": operation_id,
                "targetTool": str(stored_prepared.get("targetTool") or ""),
                "argumentsDigest": supplied_digest,
            }
        )
        try:
            applied = self.approval_transactions.execute_prepared_external_mcp_write(
                stored_prepared,
                agent_name=agent_name,
            )
        except AgentGatewayError as exc:
            result = self._external_mcp_no_write_error(
                str(stored_prepared.get("targetTool") or ""),
                "transaction_start",
                exc,
            )
            result["operationId"] = operation_id
            return result
        result = self._external_mcp_write_result(
            str(stored_prepared.get("targetTool") or ""),
            applied,
        )
        result["operationId"] = operation_id
        return result

    def _prune_external_mcp_confirmations_locked(self, now_epoch: float) -> None:
        expired = [
            operation_id
            for operation_id, record in self._external_mcp_write_confirmations.items()
            if float(record.get("expiresEpoch") or 0) <= now_epoch
        ]
        for operation_id in expired:
            self._external_mcp_write_confirmations.pop(operation_id, None)

    def _invalid_external_mcp_confirmation(self, tool: str, error: str) -> dict[str, Any]:
        error_object = build_external_tool_error(
            error=error,
            error_code="external_confirmation_invalid",
            failure_layer="external_mcp_confirmation",
            failure_phase="confirmation_validation",
            operation_kind="write",
            tool=tool,
            tool_routing_started=False,
            mutation_started=False,
            committed=False,
            commit_state="not_started",
            retryable=False,
            checkpoint_recovery_required=False,
            temporary_cleanup_required=False,
        )
        payload = {
            "ok": False,
            "status": "invalid_confirmation",
            "tool": tool,
            "mutationStarted": False,
            "committed": False,
            "commitState": "not_started",
            "error": error_object["error"],
            "errorDetails": error_object,
            "writeFailure": external_write_failure_view(error_object),
        }
        payload["outcome"] = normalize_agent_tool_result(
            payload,
            fallback_summary="External confirmation validation failed.",
            write=True,
        )
        return payload

    def _external_mcp_no_write_error(
        self,
        tool: str,
        failure_layer: str,
        error: str | BaseException,
    ) -> dict[str, Any]:
        exception = error if isinstance(error, BaseException) else None
        error_text = str(error)
        exception_code = ""
        if exception is not None:
            for attribute in ("cause_code", "error_code", "code"):
                value = getattr(exception, attribute, None)
                normalized_value = str(value or "").strip()
                if normalized_value and normalized_value not in {
                    "agent_gateway_rejected",
                    "external_tool_rejected",
                }:
                    exception_code = normalized_value
                    break
        error_object = build_external_tool_error(
            error=error_text,
            error_code=exception_code or f"external_{failure_layer}_rejected",
            failure_layer=failure_layer,
            failure_phase="before_write_handler",
            operation_kind="write",
            tool=tool,
            tool_routing_started=False,
            mutation_started=False,
            committed=False,
            commit_state="not_started",
            retryable=False,
            checkpoint_recovery_required=False,
            temporary_cleanup_required=False,
            exception=exception,
        )
        payload = {
            "ok": False,
            "status": "failed",
            "tool": tool,
            "result": None,
            "error": error_object["error"],
            "errorDetails": error_object,
            "writeFailure": external_write_failure_view(error_object),
        }
        payload["outcome"] = normalize_agent_tool_result(
            payload,
            fallback_summary="External write preparation failed.",
            write=True,
        )
        return payload

    def _external_mcp_write_result(
        self,
        tool: str,
        applied: Mapping[str, Any],
    ) -> dict[str, Any]:
        had_canonical_outcome = isinstance(applied.get("outcome"), Mapping)
        outcome = (
            dict(applied["outcome"])
            if isinstance(applied.get("outcome"), Mapping)
            else normalize_agent_tool_result(
                applied,
                fallback_summary=f"{tool} completed.",
                write=True,
            )
        )
        outcome_status = str(outcome.get("status") or "failed")
        status = str(applied.get("status") or outcome_status)
        if outcome_status == "failed" and not had_canonical_outcome:
            status = "failed"
        elif status == "applied":
            status = "executed"
        payload: dict[str, Any] = {
            "ok": outcome_status != "failed",
            "status": status,
            "tool": tool,
            "result": None,
            "outcome": self._external_mcp_visible_value(outcome),
        }
        for key in (
            "result",
            "writeFailure",
            "requestTrace",
            "error",
            "message",
            "errorDetails",
            "consoleVerification",
        ):
            if key in applied:
                payload[key] = self._external_mcp_visible_value(applied[key])
        if outcome_status == "failed" and "errorDetails" not in payload:
            error_object = build_external_tool_error(
                error=payload.get("error") or "External MCP write was rejected.",
                failure_layer="external_mcp_write_execution",
                failure_phase="write_result",
                operation_kind="write",
                tool=tool,
                tool_routing_started=None,
                raw_result=applied,
            )
            payload["errorDetails"] = self._external_mcp_visible_value(error_object)
            payload.setdefault(
                "writeFailure",
                self._external_mcp_visible_value(external_write_failure_view(error_object)),
            )
        if outcome_status == "failed":
            payload["outcome"] = self._external_mcp_visible_value(
                normalize_agent_tool_result(
                    payload,
                    fallback_summary=f"{tool} failed.",
                    write=True,
                )
            )
        return redact_sensitive(payload)

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
                agent_token = self._tool_agent_context.set(agent_name)
                owner_token = self._tool_owner_context.set(f"agent:{agent_name}")
                try:
                    result = tool.handler(tool_params)
                finally:
                    self._tool_owner_context.reset(owner_token)
                    self._tool_agent_context.reset(agent_token)
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            outcome = normalize_agent_tool_result(
                result,
                fallback_summary=tool.description,
                write=tool.write,
            )
            outcome_status = str(outcome["status"])
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
                "status": outcome_status,
            }
            if request_trace is not None:
                audit_event["requestTrace"] = request_trace
            self.append_audit(audit_event)
            response = {
                "ok": outcome_status != "failed",
                "status": outcome_status,
                "requestId": request_id,
                "tool": name,
                "agent": agent_name,
                "result": result,
                "resultSummary": result_summary,
                "durationMs": duration_ms,
                "outcome": outcome,
            }
            if outcome_status == "failed":
                response["error"] = outcome["summary"]
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
            error_object = build_external_tool_error(
                exception=exc,
                failure_layer="agent_tool_handler",
                failure_phase="tool_handler_exception",
                operation_kind="write" if tool.write else "read",
                tool=name,
                tool_routing_started=True,
                mutation_started=None if tool.write else False,
                committed=None if tool.write else False,
            )
            response = {
                "ok": False,
                "status": "failed",
                "requestId": request_id,
                "tool": name,
                "agent": agent_name,
                "error": str(exc),
                "errorDetails": error_object,
                "resultSummary": {"status": "error"},
                "durationMs": duration_ms,
            }
            response["outcome"] = normalize_agent_tool_result(
                response,
                fallback_summary=tool.description,
                write=tool.write,
            )
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
        - hook 返回 unconfigured / hook 缺失 → 诚实提示；
        - hook 返回 error → 保留 provider/model/source 和重试处置；明确拒图
          会丢弃原始图片字节，只有瞬时 Provider 失败才保留供有界重试；
        - hook 抛错 → 未分类 error 状态 + 有界错误信息，图片不保留。
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
                "errorType": "provider_failure",
                "retryable": False,
                "retainImages": False,
                "notice": (
                    "（视觉模型调用失败，图片内容未能分析："
                    f"{summarize_text(str(exc), 200)}。原始图片不会保留；请检查所选视觉模型配置后重新附图。）"
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
        if status == "error":
            error_type = str(result.get("errorType") or result.get("error_type") or "provider_failure")
            retryable = bool(result.get("retryable")) and error_type == "transient_provider_failure"
            retain_images = retryable and bool(result.get("retainImages") or result.get("retain_images"))
            error = summarize_text(str(result.get("error") or "Visual provider request failed."), 500)
            retry_notice = (
                "图片已保留，可在稍后重试同一视觉请求。"
                if retain_images
                else "原始图片已从回灌上下文丢弃；如需重试请重新附图。"
            )
            return {
                **base,
                "status": "error",
                "error": error,
                "errorType": error_type,
                "retryable": retryable,
                "retainImages": retain_images,
                "provider": str(result.get("provider") or ""),
                "providerLabel": str(result.get("providerLabel") or result.get("provider_label") or ""),
                "model": str(result.get("model") or ""),
                "source": str(result.get("source") or ""),
                "notice": f"（所选视觉 Provider/模型请求失败：{summarize_text(error, 200)}。{retry_notice}）",
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

    def resume_runtime_task_after_approval(
        self,
        approval: dict[str, Any] | None,
        execution: dict[str, Any] | None = None,
        *,
        rejected: bool = False,
        revision_requested: bool = False,
    ) -> dict[str, Any] | None:
        """Resume only a task-linked approval, after its transaction is terminal.

        Approval owns the write transaction.  This method owns the subsequent
        task decision and never calls the approved handler again.
        """

        prepared = prepare_approval_task_continuation(
            approval,
            execution,
            rejected=rejected,
            revision_requested=revision_requested,
        )
        if prepared is None:
            return None
        params = ensure_dict(prepared.get("params"))
        continuation = ensure_dict(prepared.get("taskContinuation"))
        self._signal_background_activity("approval_task_continuation")
        agent_name = str(prepared.get("agentName") or "desktop-agent")
        try:
            with self.runtime_planner.bind_turn(params) as metadata:
                params["_contextCompactionLimit"] = metadata.verified_context_limit
                params["_plannerAttemptLabel"] = metadata.planner_label
                with self._desktop.runtime_turn_context(params):
                    return self._runtime_message_impl(
                        params,
                        agent_name=agent_name,
                        task_continuation=continuation,
                    )
        except DesktopActionBrokerError as exc:
            raise AgentGatewayError(str(exc), status_code=exc.status_code) from exc

    def resume_runtime_task_after_shell(
        self,
        event: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Resume a task once from a terminal background Shell event."""

        event = event if isinstance(event, dict) else {}
        shell_session_id = str(event.get("shellSessionId") or "").strip()
        if not shell_session_id:
            return None
        prepared = prepare_shell_task_continuation(
            event.get("taskSeed") if isinstance(event.get("taskSeed"), dict) else None,
            event,
        )
        if prepared is None:
            return None
        with self._lock:
            if shell_session_id in self._runtime_shell_completion_ids:
                return None
            self._runtime_shell_completion_ids.add(shell_session_id)
            self._runtime_shell_completion_order.append(shell_session_id)
            while len(self._runtime_shell_completion_order) > 512:
                expired = self._runtime_shell_completion_order.pop(0)
                self._runtime_shell_completion_ids.discard(expired)
        params = ensure_dict(prepared.get("params"))
        continuation = ensure_dict(prepared.get("taskContinuation"))
        terminal_result = ensure_dict(event.get("result"))
        if terminal_result:
            terminal_result_summary = (
                dict(terminal_result)
                if "stdoutSummary" in terminal_result or "stderrSummary" in terminal_result
                else summarize_owned_shell_result(terminal_result)
            )
            continuation["plannerObservation"] = {
                "tool": "shell",
                "kind": "shell",
                "status": str(event.get("status") or ""),
                "result": terminal_result_summary,
                "outcome": ensure_dict(ensure_dict(continuation.get("completion")).get("outcome")),
            }
        self._signal_background_activity("shell_task_continuation")
        agent_name = str(prepared.get("agentName") or "desktop-agent")
        try:
            with self.runtime_planner.bind_turn(params) as metadata:
                params["_contextCompactionLimit"] = metadata.verified_context_limit
                params["_plannerAttemptLabel"] = metadata.planner_label
                with self._desktop.runtime_turn_context(params):
                    return self._runtime_message_impl(
                        params,
                        agent_name=agent_name,
                        task_continuation=continuation,
                        continuation_shutdown_guard=True,
                    )
        except DesktopActionBrokerError as exc:
            raise AgentGatewayError(str(exc), status_code=exc.status_code) from exc

    def record_interrupted_runtime_task(
        self,
        *,
        task_seed: dict[str, Any] | None,
        continuation_source: str,
        owned_id: str,
        summary: str,
    ) -> dict[str, Any] | None:
        """Persist a UI-only ambiguous terminal without resampling or executing tools."""

        seed = task_seed if isinstance(task_seed, dict) else {}
        source = str(continuation_source or "").strip()
        if source not in {"shell_process_finished", "sub_agent_finished"}:
            return None
        session_id = str(seed.get("sessionId") or "").strip()[:180]
        stable_id = str(owned_id or "").strip()[:100]
        if not session_id or not stable_id:
            return None
        original_client_turn_id = str(seed.get("clientTurnId") or "").strip()
        suffix = "shell" if source == "shell_process_finished" else "subagent"
        client_turn_id = (
            f"{original_client_turn_id}:{suffix}:{stable_id}"
            if original_client_turn_id
            else f"{suffix}:{stable_id}"
        )[:240]
        notice = summarize_text(
            summary
            or (
                "The task continuation was interrupted after dispatch began. Its external result "
                "may already exist, so inspect the current state before retrying."
            ),
            1200,
        )
        requested_action_id = str(seed.get("requestedActionId") or "").strip()[:80]
        requested_kind = str(seed.get("requestedKind") or "").strip()[:32]
        requested_tool = str(seed.get("requestedTool") or "").strip()[:160]
        projected = project_runtime_turn_event(
            {
                "continuationSource": source,
                "sessionId": session_id,
                "turnId": f"interrupted:{suffix}:{stable_id}"[:180],
                "clientTurnId": client_turn_id,
                "plan": {
                    "summary": notice,
                    "reply": notice,
                    "planner": "runtime",
                    "nextStep": "needs_user_action",
                    "taskCompletion": {
                        "status": "needs_user_action",
                        "taskId": str(seed.get("taskId") or "")[:80],
                        "actionId": requested_action_id,
                        "kind": requested_kind,
                        "tool": requested_tool,
                        "evidenceActionIds": [],
                    },
                },
            }
        )
        if projected is None:
            return None
        self._runtime_run_ledger.append(
            {
                "event": "runtime_turn_completed",
                "status": "needs_user_action",
                "sessionId": session_id,
                "turnId": projected["turnId"],
                "clientTurnId": client_turn_id,
                "actionId": requested_action_id,
                "kind": requested_kind,
                "tool": requested_tool,
                "continuationEvent": projected,
            }
        )
        try:
            self._runtime_turn_completed(projected)
        except Exception:  # noqa: BLE001 - reconnect reads the durable UI-only projection.
            pass
        return projected

    def _dispatch_runtime_shell_continuation(self, shell_session_id: str) -> bool:
        """Claim, dispatch and terminally record one durable Shell continuation."""

        stable_id = str(shell_session_id or "").strip()[:100]
        if not stable_id:
            return False
        with self._lock:
            if (
                not self._runtime_continuation_accepting
                or stable_id in self._runtime_continuations_inflight
            ):
                return False
            self._runtime_continuations_inflight.add(stable_id)
        try:
            return self._dispatch_runtime_shell_continuation_owned(stable_id)
        finally:
            with self._lock:
                self._runtime_continuations_inflight.discard(stable_id)
                self._runtime_continuation_condition.notify_all()

    def _dispatch_runtime_shell_continuation_owned(self, shell_session_id: str) -> bool:
        """Dispatch one continuation after its app-lifecycle ownership is reserved."""

        claimed = self._runtime_run_ledger.claim_shell_continuation(shell_session_id)
        if claimed is None:
            return False
        event = {
            **ensure_dict(claimed.get("terminalEvent")),
            "taskSeed": ensure_dict(claimed.get("taskSeed")),
        }
        try:
            continuation = self.resume_runtime_task_after_shell(event)
            if continuation is None:
                raise AgentGatewayError(
                    "The durable Shell continuation could not be reconstructed.",
                    status_code=409,
                )
            self._runtime_turn_completed(continuation)
        except Exception as exc:  # noqa: BLE001 - never replay a claimed external action.
            try:
                interrupted = self._runtime_run_ledger.interrupt_shell_continuation(
                    shell_session_id,
                    reason=f"dispatch_failed:{type(exc).__name__}",
                )
                if interrupted:
                    self.record_interrupted_runtime_task(
                        task_seed=ensure_dict(claimed.get("taskSeed")),
                        continuation_source="shell_process_finished",
                        owned_id=shell_session_id,
                        summary=(
                            "The background Shell continuation was interrupted after dispatch began. "
                            "The command result may already exist; inspect it before retrying."
                        ),
                    )
            finally:
                self.append_audit(
                    {
                        "event": "runtime_shell_task_continuation_failed",
                        "shellSessionId": str(shell_session_id or "")[:100],
                        "status": "interrupted",
                        "failureClass": type(exc).__name__,
                    }
                )
            return False
        try:
            return self._runtime_run_ledger.deliver_shell_continuation(shell_session_id)
        except Exception as exc:  # noqa: BLE001 - dispatch already ran; restart must not replay it.
            self.append_audit(
                {
                    "event": "runtime_shell_task_continuation_delivery_record_failed",
                    "shellSessionId": str(shell_session_id or "")[:100],
                    "status": "dispatching",
                    "failureClass": type(exc).__name__,
                }
            )
            return False

    def resume_runtime_task_after_sub_agent(
        self,
        event: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Resume one original task from a durable sub-agent terminal event."""

        event = event if isinstance(event, dict) else {}
        task_id = str(event.get("subAgentTaskId") or "").strip()
        if not task_id:
            return None
        prepared = prepare_sub_agent_task_continuation(
            event.get("taskSeed") if isinstance(event.get("taskSeed"), dict) else None,
            event,
        )
        if prepared is None:
            return None
        completion_key = f"subagent:{task_id}"
        with self._lock:
            if completion_key in self._runtime_shell_completion_ids:
                return None
            self._runtime_shell_completion_ids.add(completion_key)
            self._runtime_shell_completion_order.append(completion_key)
            while len(self._runtime_shell_completion_order) > 512:
                expired = self._runtime_shell_completion_order.pop(0)
                self._runtime_shell_completion_ids.discard(expired)
        params = ensure_dict(prepared.get("params"))
        continuation = ensure_dict(prepared.get("taskContinuation"))
        result = ensure_dict(event.get("result"))
        result_summary = result.get("summaryText") or result.get("summary") or event.get("summary")
        if isinstance(result_summary, dict):
            result_summary = json.dumps(result_summary, ensure_ascii=False, sort_keys=True, default=str)
        continuation["plannerObservation"] = {
            "tool": "vrcforge_delegate_subagent",
            "kind": "skill",
            "status": str(event.get("status") or ""),
            "result": {
                "taskId": task_id,
                "status": str(event.get("status") or ""),
                "summary": summarize_text(str(event.get("summary") or ""), 1000),
                "resultSummary": summarize_text(str(result_summary or ""), 1600),
            },
            "outcome": ensure_dict(ensure_dict(continuation.get("completion")).get("outcome")),
        }
        planner_evidence = result.get("plannerEvidence")
        if isinstance(planner_evidence, dict):
            continuation["plannerObservation"]["result"]["plannerEvidence"] = planner_evidence
        self._signal_background_activity("sub_agent_task_continuation")
        agent_name = str(prepared.get("agentName") or "desktop-agent")
        try:
            with self.runtime_planner.bind_turn(params) as metadata:
                params["_contextCompactionLimit"] = metadata.verified_context_limit
                params["_plannerAttemptLabel"] = metadata.planner_label
                with self._desktop.runtime_turn_context(params):
                    return self._runtime_message_impl(
                        params,
                        agent_name=agent_name,
                        task_continuation=continuation,
                        continuation_shutdown_guard=True,
                    )
        except DesktopActionBrokerError as exc:
            raise AgentGatewayError(str(exc), status_code=exc.status_code) from exc

    def _runtime_message_impl(
        self,
        params: dict[str, Any] | None = None,
        agent_name: str = "desktop-agent",
        *,
        task_continuation: dict[str, Any] | None = None,
        continuation_shutdown_guard: bool = False,
    ) -> dict[str, Any]:
        owned_params = params if isinstance(params, dict) else {}
        response_payload: dict[str, Any] | None = None
        try:
            response_payload = self._runtime_message_impl_body(
                owned_params,
                agent_name=agent_name,
                task_continuation=task_continuation,
                continuation_shutdown_guard=continuation_shutdown_guard,
            )
            return response_payload
        finally:
            resolved_session_id = str(owned_params.get("_resolvedRuntimeSessionId") or "")
            resolved_client_turn_id = str(owned_params.get("_resolvedRuntimeClientTurnId") or "")
            late_steers = self._runtime_session_state.finish_turn(
                session_id=resolved_session_id,
                turn_id=str(owned_params.get("_resolvedRuntimeTurnId") or ""),
                client_turn_id=resolved_client_turn_id,
            )
            # A steer can win the exact-turn CAS after the final model boundary
            # but before turn ownership is released. It must become a durable
            # follow-up instead of disappearing with the hot mailbox.
            deferred: list[dict[str, Any]] = []
            followup_outcomes: list[dict[str, Any]] = []
            for item in late_steers:
                input_id = str(item.get("inputId") or "")[:180]
                followup_lane_id = str(item.get("followupLaneId") or resolved_session_id)[:180]
                try:
                    queued = self._runtime_followup_queue.enqueue(
                        session_id=followup_lane_id,
                        client_turn_id=input_id,
                        target_client_turn_id=resolved_client_turn_id,
                        message=str(item.get("message") or ""),
                    )
                except Exception:  # noqa: BLE001 - persistence failure becomes an authoritative bounded outcome.
                    queued = {
                        "accepted": False,
                        "status": "backpressure",
                        "reason": "durable_store_unavailable",
                    }
                if queued.get("accepted") is True:
                    deferred.append({
                        "inputId": input_id,
                        "queueId": queued.get("queueId"),
                        "sequence": queued.get("sequence"),
                        "status": queued.get("status") or "pending",
                    })
                followup_outcomes.append({
                    "inputId": input_id,
                    "targetClientTurnId": resolved_client_turn_id[:180],
                    "followupLaneId": followup_lane_id,
                    "status": str(queued.get("status") or ("queued" if queued.get("accepted") is True else "rejected"))[:40],
                    "reason": str(queued.get("reason") or ("queued_followup" if queued.get("accepted") is True else "followup_rejected"))[:80],
                })
            if response_payload is not None and followup_outcomes:
                response_payload["deferredSteerFollowupOutcomes"] = followup_outcomes
                if deferred:
                    response_payload["deferredSteerFollowups"] = deferred

    def _runtime_message_impl_body(
        self,
        params: dict[str, Any] | None = None,
        agent_name: str = "desktop-agent",
        *,
        task_continuation: dict[str, Any] | None = None,
        continuation_shutdown_guard: bool = False,
    ) -> dict[str, Any]:
        params = params or {}
        if continuation_shutdown_guard:
            self._ensure_runtime_continuation_accepting()
        message = str(params.get("message") or "").strip()
        if not message:
            raise AgentGatewayError("message is required.")

        now = utc_now_iso()
        session_id = str(params.get("session_id") or params.get("sessionId") or "").strip()
        if not session_id:
            session_id = f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        turn_id = f"turn_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        client_turn_id = str(params.get("client_turn_id") or params.get("clientTurnId") or "").strip()
        params["_resolvedRuntimeSessionId"] = session_id
        params["_resolvedRuntimeTurnId"] = turn_id
        params["_resolvedRuntimeClientTurnId"] = client_turn_id
        if not self._runtime_session_state.begin_turn(
            session_id=session_id,
            turn_id=turn_id,
            client_turn_id=client_turn_id,
        ):
            raise AgentGatewayError(
                "A runtime turn with this sessionId and clientTurnId is already active.",
                status_code=409,
            )
        goal_delivery_id = str(params.get("goal_delivery_id") or params.get("goalDeliveryId") or "").strip()
        chat_id = str(params.get("chat_id") or params.get("chatId") or "").strip()
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
        project_context_active = (
            params.get("_projectContextActive") is not False
            if "_projectContextActive" in params
            else bool(project_root)
        )
        general_allowed_roots: list[str] = []
        root_candidates = [
            project_root,
            str(params.get("workspace_root") or params.get("workspaceRoot") or "").strip(),
            str(params.get("cwd") or "").strip(),
        ]
        if not project_root:
            root_candidates.extend(extract_explicit_local_roots(message))
        seen_general_roots: set[str] = set()
        for candidate in root_candidates:
            if not candidate:
                continue
            try:
                resolved = Path(candidate).expanduser().resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                continue
            normalized = os.path.normcase(str(resolved))
            if normalized in seen_general_roots:
                continue
            seen_general_roots.add(normalized)
            general_allowed_roots.append(str(resolved))
        continuation_context = ensure_dict(ensure_dict(task_continuation).get("context"))
        continuation_completion = ensure_dict(ensure_dict(task_continuation).get("completion"))
        if continuation_context and continuation_completion:
            task_loop = AgentTaskLoop.from_approval_context(
                continuation_context,
                continuation_completion,
                execution=ensure_dict(task_continuation).get("execution"),
            )
        else:
            task_loop = AgentTaskLoop(
                message,
                session_id=session_id,
                turn_id=turn_id,
                client_turn_id=client_turn_id,
                project_root=project_root,
                agent_name=agent_name,
                provider=str(params.get("provider") or ""),
                provider_label=str(params.get("providerLabel") or ""),
                model=str(params.get("model") or ""),
                context_limit=(
                    int(params.get("_requestedContextLimit"))
                    if isinstance(params.get("_requestedContextLimit"), int)
                    else None
                ),
                history=history,
                budget_policy=freeze_agent_budget_policy(
                    params.get("agentBudgetPolicy")
                    or params.get("budgetPolicy")
                    or ({"maxAgenticTurns": params.get("maxAgenticTurns")} if params.get("maxAgenticTurns") is not None else None)
                    or getattr(self.ensure_config(), "agent_budget_policy", None)
                    or getattr(self.ensure_config(), "advanced_settings", None)
                ),
            )
        observe = self.runtime_observe(session_id=session_id, project_root=project_root)
        if attachments:
            observe["turn"] = {"attachments": attachments}
        # 图片附件 → 视觉委托：一轮最多分析一次，结果（或诚实提示）注入本轮。
        vision_payload: dict[str, Any] | None = None
        image_attachments = runtime_image_attachments(attachments)
        if image_attachments:
            vision_payload = self._run_vision_analysis(message, image_attachments)
            if (
                str(vision_payload.get("status") or "") == "error"
                and not bool(vision_payload.get("retainImages"))
            ):
                attachments, discarded_count = discard_runtime_image_payloads(attachments)
                params["_runtimeAttachments"] = attachments
                vision_payload["discardedImageCount"] = discarded_count
            turn_context = ensure_dict(observe.get("turn"))
            if attachments:
                turn_context["attachments"] = attachments
            turn_context["visionAnalysis"] = vision_payload
            observe["turn"] = turn_context
        reasoning_trace: dict[str, Any] = {}
        prior_provider_request_count = (
            task_loop.provider_request_count if continuation_context else 0
        )
        context_usage: dict[str, Any] = {}
        self._runtime_session_state.set_stream_context(
            {
                "sessionId": session_id,
                "turnId": turn_id,
                "clientTurnId": client_turn_id,
            }
        )
        self._emit_runtime_status(
            "preparing",
            session_id=session_id,
            turn_id=turn_id,
            client_turn_id=client_turn_id,
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
        loop_state: list[dict[str, Any]] = (
            task_loop.planner_observations() if continuation_context else []
        )
        continuation_observation = ensure_dict(
            ensure_dict(task_continuation).get("plannerObservation")
        )
        if continuation_observation:
            loop_state.append(continuation_observation)
        steps: list[dict[str, Any]] = (
            task_loop.historical_steps() if continuation_context else []
        )
        timeline: list[dict[str, Any]] = []

        def append_timeline_event(
            kind: str,
            *,
            label: str = "",
            summary: str = "",
            status: str = "",
            tool: str = "",
            phase: str = "",
            subagent_status: str = "",
            action_id: str = "",
        ) -> None:
            """Persist the visible runtime order without prompts, CoT, or raw arguments."""

            sequence = len(timeline)
            payload: dict[str, Any] = {}
            for key, value, limit in (
                ("label", label, 160),
                ("summary", summary, 1000),
                ("status", status, 80),
                ("tool", tool, 160),
                ("phase", phase, 80),
                ("subagentStatus", subagent_status, 40),
                ("actionId", action_id, 96),
            ):
                bounded = summarize_text(str(value or ""), limit)
                if bounded:
                    payload[key] = bounded
            event = {
                "id": f"timeline-{turn_id}-{sequence}",
                "sequence": sequence,
                "timestamp": utc_now_iso(),
                "kind": kind,
                "payload": payload,
            }
            timeline.append(event)
            try:
                self._runtime_timeline_changed(
                    {
                        "sessionId": summarize_text(session_id, 160),
                        "turnId": summarize_text(turn_id, 160),
                        "clientTurnId": summarize_text(client_turn_id, 160),
                        "timelineEvent": event,
                    }
                )
            except Exception:
                # Presentation must never fail or delay the Runtime.
                pass
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
                    "errorType": vision_payload.get("errorType") or "",
                    "retryable": bool(vision_payload.get("retryable")),
                    "retainImages": bool(vision_payload.get("retainImages")),
                }
            )
        # Suppress only an immediately repeated successful action. A distinct
        # successful action is a state-observation boundary, so A -> B -> A is
        # allowed for read-after-change verification within the bounded budget.
        last_successful_action_id = ""
        repeated_failure_guard = RepeatedFailureGuard()
        shell_payload: dict[str, Any] | None = None
        skill_payload: dict[str, Any] | None = None
        write_payload: dict[str, Any] | None = (
            ensure_dict(ensure_dict(task_continuation).get("execution")) or None
        )
        approval_id = str(ensure_dict(task_continuation).get("approvalId") or "").strip()
        if continuation_context and write_payload is not None:
            completed_action_id = str(
                continuation_context.get("requestedActionId") or ""
            ).strip()
            completed_step = next(
                (
                    step
                    for step in reversed(steps)
                    if not completed_action_id
                    or str(step.get("actionId") or "").strip()
                    == completed_action_id
                ),
                None,
            )
            if completed_step is not None:
                if write_payload.get("result") is not None:
                    completed_step["result"] = write_payload.get("result")
                completion_outcome = ensure_dict(
                    continuation_completion.get("outcome")
                )
                if completion_outcome:
                    completed_step["outcome"] = completion_outcome
                if write_payload.get("error"):
                    completed_step["error"] = str(write_payload.get("error"))
        first_plan: dict[str, Any] | None = None
        last_plan: dict[str, Any] = {}
        iterations = 0
        cap_reached = False
        consumed_steer_input_ids: list[str] = []
        consumed_steer_seen: set[str] = set()

        def record_consumed_steers(items: list[dict[str, Any]]) -> None:
            for item in items:
                input_id = str(item.get("inputId") or "").strip()
                if input_id and input_id not in consumed_steer_seen and len(consumed_steer_input_ids) < 256:
                    consumed_steer_seen.add(input_id)
                    consumed_steer_input_ids.append(input_id)
        tool_calls_used = task_loop.tool_calls_used if continuation_context else 0
        runtime_exposure_layer = (
            task_loop.exposure_layer if continuation_context else EXPOSURE_LAYER_PLANNING
        )
        remaining_action: dict[str, Any] | None = None
        runtime_compaction: dict[str, Any] | None = None
        runtime_compaction_attempted = False
        planner_argument_failures = 0
        completion_claim_correction_attempted = False
        general_no_progress_attempts = 0
        last_general_read_key = ""
        unresolved_planner_argument_failure: dict[str, Any] | None = None
        runtime_compaction_usage_checkpoint: dict[str, Any] | None = None
        unresolved_completion_outcomes: dict[tuple[str, str], dict[str, Any]] = {}
        unresolved_completion_action_keys: dict[str, tuple[str, str]] = {}
        if continuation_context and str(continuation_completion.get("status") or "").casefold() == "completed":
            completed_requested_action_id = str(
                continuation_context.get("requestedActionId") or ""
            ).strip()
            if completed_requested_action_id:
                last_successful_action_id = completed_requested_action_id

        def discard_runtime_compaction_for_cancel() -> None:
            nonlocal runtime_compaction
            if runtime_compaction_usage_checkpoint is not None:
                context_usage.clear()
                context_usage.update(runtime_compaction_usage_checkpoint)
            if runtime_compaction is not None:
                runtime_compaction = planner_policy.runtime_compaction_cancelled_view(runtime_compaction)

        def enter_runtime_execution() -> bool:
            nonlocal runtime_exposure_layer
            if runtime_exposure_layer != EXPOSURE_LAYER_PLANNING:
                return False
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
            return True

        def record_planner_argument_failure(
            *,
            action_kind: str,
            tool_name: str,
            action_id: str,
            validation: dict[str, Any],
        ) -> bool:
            nonlocal planner_argument_failures, unresolved_planner_argument_failure
            planner_argument_failures += 1
            issues = [
                {
                    "path": summarize_text(str(item.get("path") or ""), 120),
                    "code": summarize_text(str(item.get("code") or ""), 80),
                    "expected": summarize_text(str(item.get("expected") or ""), 120),
                }
                for item in ensure_list(validation.get("issues"))[:8]
                if isinstance(item, dict)
            ]
            summary = summarize_text(
                str(
                    validation.get("summary")
                    or "Tool arguments do not match the registered shallow schema."
                ),
                600,
            )
            outcome = normalize_agent_tool_result(
                {
                    "ok": False,
                    "status": "failed",
                    "error": {
                        "type": "input",
                        "code": "planner_invalid_response",
                        "summary": summary,
                        "likelyCauses": [summary],
                        "nextActions": [
                            "Correct the tool arguments to match the registered schema before retrying."
                        ],
                        "retryable": True,
                    },
                },
                fallback_summary=summary,
                write=action_kind == "write",
            )
            loop_state.append(
                {
                    "tool": tool_name,
                    "kind": action_kind,
                    "actionId": action_id,
                    "status": "failed",
                    "result": {
                        "code": "planner_invalid_response",
                        "summary": summary,
                        "issues": issues,
                    },
                    "outcome": outcome,
                }
            )
            steps.append(
                {
                    "index": len(steps),
                    "kind": "planner_validation",
                    "tool": tool_name,
                    "summary": summary,
                    "status": "failed",
                }
            )
            unresolved_planner_argument_failure = {
                "actionKind": action_kind,
                "tool": tool_name,
                "actionId": action_id,
                "summary": summary,
            }
            return planner_argument_failures >= RUNTIME_PLANNER_ARGUMENT_MAX_ATTEMPTS

        if bool(params.get("_computerUseRequested")) and not self._runtime_session_state.desktop_bootstrap_completed(
            session_id
        ):
            bootstrap_tool = "vrcforge_agent_desktop_action"
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
                bootstrap_tool,
                bootstrap_params,
                agent_name,
                owner_id=self._runtime_shell_owner(turn_id, client_turn_id, session_id),
            )
            skill_payload = bootstrap_payload
            bootstrap_action_id = canonical_action_id(
                "skill",
                bootstrap_tool,
                bootstrap_params,
            )
            task_loop.require_action(
                kind="skill",
                tool=bootstrap_tool,
                arguments=bootstrap_params,
                verification_profile="canonical_tool_result",
            )
            bootstrap_outcome = ensure_dict(bootstrap_payload.get("outcome"))
            if str(bootstrap_outcome.get("status") or "").strip().casefold() == "ok":
                # A successful canonical tool envelope is the verifier for this
                # read-only bootstrap. Record that result explicitly so the
                # authenticated journey can distinguish it from an unverified
                # caller-supplied action.
                bootstrap_outcome = {
                    **bootstrap_outcome,
                    "verification": {
                        "state": "passed",
                        "checks": [
                            {"kind": "canonical_tool_result", "state": "passed"}
                        ],
                    },
                }
            bootstrap_action = task_loop.record_action(
                kind="skill",
                tool=bootstrap_tool,
                arguments=bootstrap_params,
                raw_result=bootstrap_payload,
                outcome=bootstrap_outcome,
                action_id=bootstrap_action_id,
                pre_provider=True,
            )
            bootstrap_payload["outcome"] = bootstrap_action["outcome"]
            if bootstrap_action.get("status") == "completed":
                last_successful_action_id = bootstrap_action_id
            bootstrap_step: dict[str, Any] = {
                "tool": bootstrap_tool,
                "kind": "skill",
                "actionId": bootstrap_action_id,
                "preProvider": True,
                "status": bootstrap_payload.get("status"),
                "result": bootstrap_payload.get("result"),
                "outcome": bootstrap_action["outcome"],
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
                    "tool": bootstrap_tool,
                    "summary": "Discovered the initial desktop applications and windows.",
                    "status": bootstrap_payload.get("status") or "",
                    "actionId": bootstrap_action_id,
                    "preProvider": True,
                }
            )
            if bootstrap_payload.get("result") is not None:
                steps[-1]["result"] = bootstrap_payload.get("result")
            if bootstrap_outcome:
                steps[-1]["outcome"] = bootstrap_outcome

        # A final assistant response is the ordinary termination boundary.
        # Optional model-turn limits support automation. Tool-call count is
        # telemetry only; normal turns end at the model's final response.
        for step_index in count():
            params["_internalToolBlocks"] = sorted(
                self._runtime_session_state.internal_tool_blocks(session_id)
            )
            budget_decision = task_loop.budget_policy.check(
                model_turns_used=task_loop.model_turns_used,
                tool_calls_used=tool_calls_used,
                remaining_action=remaining_action,
            )
            if budget_decision.get("paused"):
                cap_reached = True
                last_plan = {
                    "summary": "Runtime paused at a bounded budget.",
                    "reply": "Runtime paused before the next action; the task is not complete.",
                    "planner": "runtime",
                    **budget_decision,
                }
                break
            if continuation_shutdown_guard:
                self._ensure_runtime_continuation_accepting()
            continuation_terminal_plan = ensure_dict(
                ensure_dict(task_continuation).get("terminalPlan")
            )
            if step_index == 0 and continuation_terminal_plan:
                last_plan = continuation_terminal_plan
                if str(continuation_completion.get("status") or "").casefold() == "completed":
                    last_plan["completionSatisfied"] = True
                    last_plan["completionActionIds"] = task_loop.completed_action_ids()
                iterations += 1
                break
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
                # A steer arriving while the previous tool was running must
                # be incorporated before this model request.  The post-plan
                # drain below remains necessary for input that races with an
                # in-flight provider call; this pre-boundary drain prevents a
                # stale provider request after a completed tool batch.
                steer_inputs = self._runtime_session_state.drain_steer(
                    session_id=session_id,
                    client_turn_id=client_turn_id,
                )
                if steer_inputs:
                    record_consumed_steers(steer_inputs)
                    history.extend(
                        {
                            "role": "user",
                            "text": str(item.get("message") or ""),
                            "createdAt": utc_now_iso(),
                        }
                        for item in steer_inputs
                    )
                    loop_state.append(
                        {
                            "tool": "user_steer",
                            "kind": "user_interjection",
                            "status": "received",
                            "result": {"count": len(steer_inputs)},
                        }
                    )
            self._emit_runtime_status(
                "waiting_for_model",
                session_id=session_id,
                turn_id=turn_id,
                client_turn_id=client_turn_id,
            )
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
            task_loop.model_turns_used += 1
            if continuation_shutdown_guard:
                self._ensure_runtime_continuation_accepting()
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

            # A user steer is accepted only for this exact active turn. Drain
            # after the current model boundary and before any newly planned
            # action executes, then re-plan from the synthetic user messages.
            steer_inputs = self._runtime_session_state.drain_steer(
                session_id=session_id,
                client_turn_id=client_turn_id,
            )
            if steer_inputs:
                record_consumed_steers(steer_inputs)
                history.extend(
                    {
                        "role": "user",
                        "text": str(item.get("message") or ""),
                        "createdAt": utc_now_iso(),
                    }
                    for item in steer_inputs
                )
                loop_state.append(
                    {
                        "tool": "user_steer",
                        "kind": "user_interjection",
                        "status": "received",
                        "result": {"count": len(steer_inputs)},
                    }
                )
                continue

            planner_argument_validation = ensure_dict(plan.get("argumentValidation"))
            if (
                planner_argument_validation
                and planner_argument_validation.get("ok") is not True
            ):
                correction_exhausted = record_planner_argument_failure(
                    action_kind=str(
                        planner_argument_validation.get("actionKind") or "skill"
                    ),
                    tool_name=str(planner_argument_validation.get("tool") or ""),
                    action_id=str(
                        planner_argument_validation.get("actionId") or ""
                    ),
                    validation=planner_argument_validation,
                )
                if plan.get("enterExecution") is True:
                    enter_runtime_execution()
                if correction_exhausted:
                    last_plan = {
                        **plan,
                        "summary": "The model repeated invalid tool arguments.",
                        "reply": (
                            "The model returned invalid tool arguments twice, so VRCForge stopped "
                            "without executing the tool. Retry with corrected parameters."
                        ),
                        "plannerFailed": True,
                        "continueLoop": False,
                        "nextStep": "planner_failed",
                    }
                    break
                continue

            if any(
                (
                    str(plan.get("shellCommand") or params.get("shell_command") or params.get("shellCommand") or "").strip(),
                    plan.get("writeNeeded") and plan.get("writeTool"),
                    plan.get("skillNeeded") and plan.get("skillTool"),
                )
            ):
                append_timeline_event(
                    "planner",
                    label="Agent update",
                    summary=str(plan.get("reply") or plan.get("summary") or ""),
                    status="planned",
                )

            planned_tool_name = str(plan.get("writeTool") or plan.get("skillTool") or "").strip()
            planned_tool = self._tools.get(planned_tool_name)
            planning_selected_write = bool(
                plan.get("enterExecution")
                or planned_tool_name in self._write_handlers
                or (planned_tool is not None and planned_tool.write)
            )
            if runtime_exposure_layer == EXPOSURE_LAYER_PLANNING and planning_selected_write:
                enter_runtime_execution()
                continue

            # Caller-supplied shell text is only an argument source after the
            # Provider has independently selected the Shell action.  It must
            # never bypass a failed/missing Provider plan and create an
            # approval on its own.
            command = str(plan.get("shellCommand") or "").strip()
            if not command and step_index == 0 and plan.get("shellNeeded") is True:
                command = param_command
            shell_step_params = ensure_dict(plan.get("shellParams"))
            shell_protection_scope = ""
            if command and not project_root:
                shell_protection_scope = str(
                    self.shell.classify(
                        {
                            **shell_step_params,
                            "command": command,
                            "cwd": shell_step_params.get("cwd") or params.get("cwd") or "",
                            "workspace_root": (
                                params.get("workspace_root")
                                or params.get("workspaceRoot")
                                or ""
                            ),
                            "projectRoot": "",
                        }
                    ).get("protectionScope")
                    or ""
                )
            if (
                command
                and not project_root
                and shell_protection_scope != "unity_project"
            ):
                # Projectless chat uses the complete host Shell lane.  Freeze
                # the effective defaults before deriving action identity,
                # completion requirements, execution, and the async task seed.
                shell_step_params.setdefault("yieldMs", 10_000)
                shell_step_params.setdefault("timeout", 30 * 60)

            if command:
                action_kind = "shell"
                action_key = (
                    "shell",
                    command + "::" + json.dumps(shell_step_params, ensure_ascii=False, sort_keys=True, default=str),
                )
            elif plan.get("writeNeeded") and plan.get("writeTool"):
                action_kind = "write"
                action_key = (
                    "write",
                    f"{plan.get('writeTool')}::"
                    + json.dumps(
                        plan.get("writeParams"),
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                )
            elif plan.get("skillNeeded") and plan.get("skillTool"):
                action_kind = "skill"
                action_key = (
                    "skill",
                    f"{plan.get('skillTool')}::"
                    + json.dumps(plan.get("skillParams"), ensure_ascii=False, sort_keys=True, default=str),
                )
            elif (
                not project_context_active
                and not completion_claim_correction_attempted
                and str(plan.get("planner") or "").strip().casefold() == "llm"
                and task_loop.completed_action_ids()
                and ensure_dict(
                    task_loop.gate_terminal(plan).get("completionGate")
                ).get("reason") == "completion_claim_unbound"
            ):
                completed_action_ids = task_loop.completed_action_ids()
                correction_summary = (
                    "The terminal reply was not bound to the completed action evidence. "
                    "Reassess whether more read-only inspection is needed. If the task is "
                    "actually complete, return completion_claim with satisfied=true and "
                    "evidence_action_ids exactly equal to: "
                    + ", ".join(completed_action_ids)
                )
                loop_state.append(
                    {
                        "tool": "runtime_completion_gate",
                        "kind": "verification",
                        "status": "needs_correction",
                        "outcome": {
                            "status": "needs_correction",
                            "summary": correction_summary,
                            "verification": {
                                "state": "not_required",
                                "checks": [],
                            },
                        },
                    }
                )
                completion_claim_correction_attempted = True
                continue
            else:
                # 没有工具动作（终止答复 / 未连接 / 让用户选模型）→ 结束本轮。
                break

            policy_tool = (
                "vrcforge_shell_execute"
                if action_kind == "shell"
                else str(plan.get("writeTool") or plan.get("skillTool") or "").strip()
            )
            skill_policy_reason = task_loop.skill_policy_block_reason(policy_tool)
            if skill_policy_reason:
                blocked_outcome = normalize_agent_tool_result(
                    {
                        "ok": False,
                        "status": "blocked",
                        "error": {
                            "type": "skill_policy",
                            "code": skill_policy_reason,
                            "likelyCauses": [
                                "The loaded Skill does not grant this tool to its instruction path."
                            ],
                            "nextActions": [
                                "Choose one of the Skill's allowed tools or stop and explain the mismatch."
                            ],
                            "retryable": True,
                        },
                    },
                    fallback_summary="The loaded Skill does not allow that tool.",
                    write=action_kind in {"write", "shell"},
                )
                loop_state.append(
                    {
                        "tool": policy_tool,
                        "kind": action_kind,
                        "status": "blocked",
                        "outcome": blocked_outcome,
                    }
                )
                steps.append(
                    {
                        "index": len(steps),
                        "kind": "policy",
                        "tool": policy_tool,
                        "summary": blocked_outcome.get("summary") or "",
                        "status": "blocked",
                    }
                )
                continue

            # Only a consecutive semantic replay is suppressed. A distinct
            # successful action moves the observation boundary and permits the
            # same read again for verification. Failed actions remain governed
            # by the bounded repeated-failure guard below.
            planned_tool = str(
                plan.get("writeTool")
                or plan.get("skillTool")
                or (
                    "unity_shell"
                    if action_kind == "shell"
                    and "unity_project_access"
                    in ensure_list(plan.get("toolCapabilities"))
                    else "shell"
                    if action_kind == "shell"
                    else ""
                )
            )
            planned_arguments: dict[str, Any] = (
                dict(ensure_dict(plan.get("writeParams")))
                if action_kind == "write"
                else dict(ensure_dict(plan.get("skillParams")))
                if action_kind == "skill"
                else {"command": command, **shell_step_params}
            )
            if action_kind in {"skill", "write"}:
                execution_argument_validation = self.runtime_planner.validate_tool_arguments(
                    planned_tool,
                    planned_arguments,
                    exposure_layer=runtime_exposure_layer,
                )
                if execution_argument_validation.get("ok") is not True:
                    correction_exhausted = record_planner_argument_failure(
                        action_kind=action_kind,
                        tool_name=planned_tool,
                        action_id=planner_policy.planner_argument_validation_id(
                            action_kind,
                            planned_tool,
                            planned_arguments,
                        ),
                        validation=execution_argument_validation,
                    )
                    if correction_exhausted:
                        last_plan = {
                            **plan,
                            "summary": "The model repeated invalid tool arguments.",
                            "reply": (
                                "The model returned invalid tool arguments twice, so VRCForge stopped "
                                "without executing the tool. Retry with corrected parameters."
                            ),
                            "plannerFailed": True,
                            "plannerFailure": {
                                "code": "planner_invalid_response",
                                "phase": "post_tool" if loop_state else "initial",
                                "retryable": True,
                            },
                            "continueLoop": False,
                            "nextStep": "planner_failed",
                        }
                        break
                    continue
            planned_action_id = canonical_action_id(
                action_kind,
                planned_tool,
                planned_arguments,
            )
            general_read_key = (
                general_read_observation_key(
                    kind=action_kind,
                    tool=planned_tool,
                    arguments=planned_arguments,
                )
                if not project_context_active
                else ""
            )
            semantic_general_replay = bool(
                general_read_key and general_read_key == last_general_read_key
            )
            consecutive_general_replay = bool(
                not project_context_active
                and planned_action_id == last_successful_action_id
            )
            if semantic_general_replay or consecutive_general_replay:
                general_no_progress_attempts += 1
                correction_summary = (
                    "The proposed action would repeat an already successful observation and was not executed. "
                    "Use a materially different read/find/search/file-inspection step, run a command that can add new evidence, "
                    "or finish only when the existing evidence really answers the user."
                )
                loop_state.append(
                    {
                        "tool": "runtime_no_progress",
                        "kind": "verification",
                        "status": "suppressed",
                        "result": {
                            "code": "duplicate_observation",
                            "actionId": planned_action_id,
                            "summary": correction_summary,
                        },
                        "outcome": {
                            "status": "needs_correction",
                            "summary": correction_summary,
                            "verification": {"state": "not_required", "checks": []},
                        },
                    }
                )
                if general_no_progress_attempts >= 3:
                    last_plan = {
                        **plan,
                        "summary": "The model repeated an observation without making progress.",
                        "reply": (
                            "VRCForge stopped this turn because repeated inspection proposals added no new evidence. "
                            "The task failed to make progress and is not marked complete."
                        ),
                        "plannerFailed": True,
                        "plannerFailure": {
                            "code": "planner_no_progress",
                            "phase": "post_tool",
                            "retryable": True,
                        },
                        "continueLoop": False,
                        "nextStep": "planner_failed",
                    }
                    break
                continue
            if planned_action_id == last_successful_action_id:
                break

            # Delegation remains an ordinary Runtime tool call. Its separate
            # created/started/completed/failed lifecycle is projected only by
            # the durable Sub Agent registry, so no synthetic lifecycle event
            # is emitted here.
            timeline_kind = (
                "command"
                if action_kind == "shell"
                else "file_edit"
                if action_kind == "write"
                else "tool_call"
            )
            append_timeline_event(
                timeline_kind,
                label=("Run command" if action_kind == "shell" else planned_tool or action_kind),
                summary=str(plan.get("summary") or ""),
                status="started",
                tool=planned_tool,
                action_id=planned_action_id,
            )

            completion_requirement = ensure_dict(plan.get("completionRequirement"))
            if completion_requirement:
                task_loop.require_action(
                    kind=str(completion_requirement.get("kind") or action_kind),
                    tool=str(
                        completion_requirement.get("tool")
                        or plan.get("writeTool")
                        or plan.get("skillTool")
                        or ("shell" if action_kind == "shell" else "")
                    ),
                    arguments=(
                        completion_requirement.get("arguments")
                        if isinstance(completion_requirement.get("arguments"), dict)
                        else None
                    ),
                    verification_profile=str(
                        completion_requirement.get("verificationProfile") or ""
                    ),
                )
            elif action_kind != "skill":
                task_loop.require_action(
                    kind=action_kind,
                    tool=planned_tool,
                    arguments=planned_arguments,
                )

            step_tool = ""
            action_arguments: Any = {}
            if continuation_shutdown_guard:
                self._ensure_runtime_continuation_accepting()
            self._emit_runtime_status(
                "running_tool",
                session_id=session_id,
                turn_id=turn_id,
                client_turn_id=client_turn_id,
            )
            tool_calls_used += 1
            if action_kind == "shell":
                step_tool = "unity_shell" if "unity_project_access" in ensure_list(plan.get("toolCapabilities")) else "shell"
                general_shell_root = general_allowed_roots[0] if general_allowed_roots else ""
                shell_workspace_root = (
                    params.get("workspace_root")
                    or params.get("workspaceRoot")
                    or (project_root if project_context_active else general_shell_root)
                )
                shell_cwd = (
                    shell_step_params.get("cwd")
                    or params.get("cwd")
                    or shell_workspace_root
                )
                explicit_shell_location = bool(
                    shell_step_params.get("cwd")
                    or params.get("cwd")
                    or params.get("workspace_root")
                    or params.get("workspaceRoot")
                )
                action_arguments = {
                    "command": command,
                    "cwd": shell_cwd,
                    "workspaceRoot": shell_workspace_root,
                    "options": shell_step_params,
                }
                step_payload = self.shell.execute(
                    {
                        **shell_step_params,
                        "command": command,
                        "cwd": shell_cwd,
                        "workspace_root": shell_workspace_root,
                        "projectRoot": project_root if project_context_active else "",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "client_turn_id": client_turn_id,
                        "_trusted_owner_id": self._runtime_shell_owner(
                            turn_id,
                            client_turn_id,
                            session_id,
                        ),
                        "goalDeliveryId": goal_delivery_id,
                        "reason": plan.get("summary") or "Agent shell step",
                    },
                    agent_name=agent_name,
                    task_context=task_loop.approval_seed(
                        tool_calls_used=tool_calls_used,
                        exposure_layer=runtime_exposure_layer,
                        requested_kind="shell",
                        requested_tool=step_tool,
                        requested_arguments={"command": command, **shell_step_params},
                        provider_request_count=prior_provider_request_count + int(context_usage.get("requestCount") or 0),
                        continue_after_approval=bool(plan.get("continueLoop")),
                    ),
                    unity_project_access=step_tool == "unity_shell",
                )
                # Runtime turns already have a trusted owner; external control
                # capability must not enter model context or durable projections.
                step_payload.pop("controlToken", None)
                if not isinstance(step_payload.get("outcome"), dict):
                    shell_failure_fallback = "Shell command did not complete successfully."
                    shell_outcome = normalize_agent_tool_result(
                        step_payload,
                        fallback_summary=shell_failure_fallback,
                        write=False,
                    )
                    if (
                        shell_outcome.get("status") == "ok"
                        and shell_outcome.get("summary") == shell_failure_fallback
                    ):
                        shell_outcome["summary"] = "Shell command completed successfully."
                    step_payload["outcome"] = shell_outcome
                shell_payload = step_payload
                shell_observation = (
                    summarize_owned_shell_result(step_payload.get("result"))
                    if step_payload.get("result")
                    else {
                        "status": step_payload.get("status"),
                        "sessionId": step_payload.get("sessionId"),
                        "session": ensure_dict(step_payload.get("session")),
                    }
                )
                loop_state.append(
                    {
                        "tool": "shell",
                        "kind": "shell",
                        "status": step_payload.get("status"),
                        "result": shell_observation,
                        "outcome": step_payload.get("outcome"),
                    }
                )
            elif action_kind == "write":
                step_tool = str(plan.get("writeTool") or "")
                action_arguments = dict(planned_arguments)
                if step_tool in {
                    "vrcforge_edit_file",
                    "vrcforge_write_file",
                    "vrcforge_delete_path",
                    "vrcforge_move_path",
                    "vrcforge_apply_patch",
                }:
                    action_arguments["_generalAllowedRoots"] = list(general_allowed_roots)
                step_payload = self.approval_transactions._execute_write_request(
                    step_tool,
                    action_arguments,
                    agent_name,
                    goal_delivery_id=goal_delivery_id,
                    task_context=task_loop.approval_seed(
                        tool_calls_used=tool_calls_used,
                        exposure_layer=runtime_exposure_layer,
                        requested_tool=step_tool,
                        requested_arguments=action_arguments,
                        provider_request_count=prior_provider_request_count + int(context_usage.get("requestCount") or 0),
                        continue_after_approval=bool(plan.get("continueLoop")),
                    ),
                )
                write_payload = step_payload
                loop_state.append(
                    {
                        "tool": step_tool,
                        "kind": "write",
                        "status": step_payload.get("status"),
                        "result": step_payload.get("result"),
                        "outcome": step_payload.get("outcome"),
                    }
                )
            else:  # skill
                step_tool = str(plan.get("skillTool") or "")
                step_params = dict(planned_arguments)
                action_arguments = dict(planned_arguments)
                if step_tool in {
                    "vrcforge_list_directory",
                    "vrcforge_read_text_file",
                    "vrcforge_find_files",
                    "vrcforge_search_text",
                }:
                    step_params["_generalAllowedRoots"] = list(general_allowed_roots)
                if step_tool == "vrcforge_delegate_subagent":
                    action_arguments = dict(step_params)
                    task_loop.require_action(
                        kind="skill",
                        tool=step_tool,
                        arguments=action_arguments,
                    )
                    step_params["_runtimeSessionId"] = session_id
                    step_params["_runtimeClientTurnId"] = client_turn_id
                    step_params["_taskSeed"] = task_loop.approval_seed(
                        tool_calls_used=tool_calls_used,
                        exposure_layer=runtime_exposure_layer,
                        requested_kind="skill",
                        requested_tool=step_tool,
                        requested_arguments=action_arguments,
                        provider_request_count=prior_provider_request_count + int(context_usage.get("requestCount") or 0),
                        continue_after_approval=bool(plan.get("continueLoop")),
                    )
                if step_tool == "vrcforge_vision_audit_multi":
                    step_params["_runtimeSessionId"] = session_id
                    step_params["_taskSeed"] = task_loop.approval_seed(
                        tool_calls_used=tool_calls_used,
                        exposure_layer=runtime_exposure_layer,
                        requested_kind="skill",
                        requested_tool=step_tool,
                        requested_arguments=action_arguments,
                        provider_request_count=prior_provider_request_count + int(context_usage.get("requestCount") or 0),
                        continue_after_approval=bool(plan.get("continueLoop")),
                    )
                if (
                    step_tool == "vrcforge_agent_desktop_action"
                    or step_tool.startswith("vrcforge_progress_")
                    or step_tool == "vrcforge_ask_user"
                    or step_tool in {
                        "vrcforge_list_internal_tool_blocks",
                        "vrcforge_load_internal_tool_block",
                        "vrcforge_unload_internal_tool_block",
                    }
                ):
                    step_params.setdefault("sessionId", session_id)
                    if step_tool in {
                        "vrcforge_list_internal_tool_blocks",
                        "vrcforge_load_internal_tool_block",
                        "vrcforge_unload_internal_tool_block",
                    }:
                        step_params.setdefault("exposureLayer", runtime_exposure_layer)
                        step_params.setdefault("projectContextActive", project_context_active)
                    if goal_delivery_id:
                        step_params.setdefault("goalDeliveryId", goal_delivery_id)
                    if project_root:
                        step_params.setdefault("projectRoot", project_root)
                if step_tool in {"vrcforge_get_goal", "vrcforge_create_goal", "vrcforge_update_goal"}:
                    # Goal ownership comes from the active App turn, never from
                    # model-supplied scope fields. The turn id also prevents one
                    # runtime turn from counting the same blocked report twice.
                    step_params["sessionId"] = session_id
                    step_params["chatId"] = chat_id
                    step_params["projectRoot"] = project_root
                    step_params["turnId"] = turn_id
                if step_tool == "vrcforge_shell_process":
                    step_params["_trusted_owner_id"] = self._runtime_shell_owner(
                        turn_id,
                        client_turn_id,
                        session_id,
                    )
                if step_tool == "vrcforge_agent_desktop_action":
                    step_params.setdefault("clientTurnId", client_turn_id)
                if step_tool in {
                    "vrcforge_skill_manifest",
                    "vrcforge_skill_check",
                    "vrcforge_tool_registry",
                }:
                    step_params.setdefault("exposureLayer", runtime_exposure_layer)
                step_payload = self._runtime_skill_executor.execute(
                    step_tool,
                    step_params,
                    agent_name,
                    owner_id=self._runtime_shell_owner(turn_id, client_turn_id, session_id),
                )
                skill_payload = step_payload
                loop_step = {
                    "tool": step_tool,
                    "kind": "skill",
                    "status": step_payload.get("status"),
                    "result": step_payload.get("result"),
                    "outcome": step_payload.get("outcome"),
                }
                if (
                    str(step_payload.get("status") or "").strip().casefold() == "loaded"
                    and not str(step_payload.get("entrypointTool") or "").strip()
                ):
                    loaded_skill = ensure_dict(step_payload.get("result"))
                    active_skill_policy = task_loop.activate_skill_policy(
                        name=str(loaded_skill.get("name") or step_tool),
                        instructions=str(loaded_skill.get("instructions") or ""),
                        allowed_tools=loaded_skill.get("allowedTools"),
                        disallowed_tools=loaded_skill.get("disallowedTools"),
                    )
                    loop_step["skillContext"] = {
                        "name": str(loaded_skill.get("name") or step_tool)[:160],
                        "instructions": str(loaded_skill.get("instructions") or "")[:6000],
                        "allowedTools": active_skill_policy.get("allowedTools") or [],
                        "disallowedTools": active_skill_policy.get("disallowedTools") or [],
                    }
                    plan["continueLoop"] = True
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

            loaded_skill_context_only = bool(
                action_kind == "skill"
                and str(step_payload.get("status") or "").strip().casefold() == "loaded"
                and not str(step_payload.get("entrypointTool") or "").strip()
            )
            if loaded_skill_context_only:
                task_loop.require_action(kind="action", tool="*")
                task_action = {
                    "actionId": "",
                    "status": "prepared",
                    "outcome": ensure_dict(step_payload.get("outcome")),
                }
            else:
                task_record_tool = (
                    str(step_payload.get("entrypointTool") or "").strip()
                    if action_kind == "skill"
                    else ""
                ) or step_tool
                if action_kind == "skill" and not completion_requirement:
                    task_loop.require_action(
                        kind=action_kind,
                        tool=task_record_tool,
                        arguments=action_arguments,
                    )
                task_action = task_loop.record_action(
                    kind=action_kind,
                    tool=task_record_tool,
                    arguments=action_arguments,
                    raw_result=step_payload,
                    outcome=ensure_dict(step_payload.get("outcome")),
                    action_id=(
                        ""
                        if task_record_tool != step_tool
                        else str(step_payload.get("taskActionId") or planned_action_id)
                    ),
                    correction_for_action_id=str(plan.get("correctionForActionId") or ""),
                )
                step_payload["outcome"] = task_action["outcome"]
                if loop_state:
                    loop_state[-1]["actionId"] = task_action["actionId"]
                    loop_state[-1]["outcome"] = task_action["outcome"]
                    if task_action.get("correctedActionId"):
                        loop_state[-1]["correctionForActionId"] = task_action[
                            "correctedActionId"
                        ]

            steps.append(
                {
                    # len(steps)：有视觉前置步时循环步顺延，无视觉步时与 step_index 一致。
                    "index": len(steps),
                    "kind": action_kind,
                    "tool": step_tool,
                    "summary": plan.get("summary") or "",
                    "status": step_payload.get("status") or "",
                    "actionId": task_action["actionId"],
                }
            )
            if step_payload.get("result") is not None:
                steps[-1]["result"] = step_payload.get("result")
            step_timeline_outcome = ensure_dict(step_payload.get("outcome"))
            if step_timeline_outcome:
                steps[-1]["outcome"] = step_timeline_outcome
            if step_payload.get("error"):
                steps[-1]["error"] = str(step_payload.get("error"))

            result_outcome = ensure_dict(step_payload.get("outcome"))
            result_summary = str(
                result_outcome.get("summary")
                or step_payload.get("error")
                or steps[-1].get("summary")
                or ""
            )
            result_status = str(
                result_outcome.get("status")
                or step_payload.get("status")
                or task_action.get("status")
                or "completed"
            )
            append_timeline_event(
                "tool_result",
                label=step_tool or action_kind,
                summary=result_summary,
                status=result_status,
                tool=step_tool,
                action_id=planned_action_id,
            )

            if (
                str(plan.get("planner") or "").strip().casefold() != "llm"
                and plan.get("continueLoop") is not True
                and task_action.get("status") == "completed"
            ):
                plan["completionSatisfied"] = True
                plan["completionActionIds"] = task_loop.completed_action_ids()

            step_approval = str(
                step_payload.get("approval_id") or step_payload.get("approvalId") or ""
            ).strip()
            step_waits_for_approval = bool(
                step_approval
                and str(step_payload.get("status") or "").strip().casefold()
                in {"approval_pending", "pending", "pending_approval"}
            )
            if step_approval:
                approval_id = approval_id or step_approval
            step_failure_class = runtime_step_failure_class(step_payload)
            step_outcome = ensure_dict(step_payload.get("outcome"))
            step_outcome_status = str(step_outcome.get("status") or "").strip()
            task_action_id = str(task_action.get("actionId") or "").strip()
            correction_action_id = str(
                task_action.get("correctedActionId") or ""
            ).strip()
            if step_outcome_status == "needs_user_action":
                unresolved_completion_outcomes[action_key] = step_outcome
                if task_action_id:
                    unresolved_completion_action_keys[task_action_id] = action_key
                requires_direct_user_input = bool(
                    step_waits_for_approval
                    or step_tool == "vrcforge_ask_user"
                )
                if (
                    not requires_direct_user_input
                    and str(plan.get("planner") or "").strip().casefold() != "llm"
                ):
                    # A deterministic first call may discover a structured
                    # condition that the model can resolve with a different
                    # read/diagnostic action. Re-feed it once; an LLM-selected
                    # needs-user-action result remains terminal for this turn.
                    plan["continueLoop"] = True
                else:
                    gated_plan = completion_gate_plan(plan, step_outcome)
                    if gated_plan is not None:
                        last_plan = gated_plan
                        break
            elif step_outcome_status == "failed":
                unresolved_completion_outcomes[action_key] = step_outcome
                if task_action_id:
                    unresolved_completion_action_keys[task_action_id] = action_key
                if str(plan.get("planner") or "").strip().casefold() != "llm":
                    # Deterministic routing owns fast first selection, not the
                    # failure verdict. Re-feed the structured result to the
                    # model so it can correct arguments or choose a diagnostic
                    # action instead of terminating at the first tool error.
                    plan["continueLoop"] = True
            elif not step_failure_class:
                unresolved_completion_outcomes.pop(action_key, None)
                if task_action_id:
                    unresolved_completion_action_keys.pop(task_action_id, None)
                if correction_action_id:
                    superseded_key = unresolved_completion_action_keys.pop(
                        correction_action_id,
                        None,
                    )
                    if superseded_key is not None:
                        unresolved_completion_outcomes.pop(superseded_key, None)
            if step_failure_class:
                if action_kind == "shell":
                    failure_arguments: Any = {
                        "command": command,
                        "cwd": shell_step_params.get("cwd") or params.get("cwd"),
                        "workspaceRoot": params.get("workspace_root") or params.get("workspaceRoot"),
                        "shellParams": shell_step_params,
                    }
                elif action_kind == "write":
                    failure_arguments = action_arguments
                else:
                    # Runtime-only session/task ownership fields are injected
                    # into step_params after canonical planning. They must not
                    # change the identity of an otherwise identical failure.
                    failure_arguments = action_arguments
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
                last_successful_action_id = planned_action_id
                general_no_progress_attempts = 0
                last_general_read_key = general_read_key
                if (
                    unresolved_planner_argument_failure is not None
                    and task_action.get("status") == "completed"
                    and action_kind
                    == str(unresolved_planner_argument_failure.get("actionKind") or "")
                    and str(unresolved_planner_argument_failure.get("tool") or "")
                    in {
                        step_tool,
                        str(
                            plan.get("writeDisplayTool")
                            or plan.get("skillDisplayTool")
                            or ""
                        ),
                    }
                ):
                    unresolved_planner_argument_failure = None

            if step_waits_for_approval:
                self._emit_runtime_status(
                    "waiting_for_approval",
                    session_id=session_id,
                    turn_id=turn_id,
                    client_turn_id=client_turn_id,
                )
                break  # 进入审批等待 → 本轮收尾。
            if task_action.get("status") == "running":
                # Background Shell and sub-agent tasks own the next decision
                # after their durable terminal event.  Never continue against
                # a seed that predates later tool calls.
                break
            if action_kind == "shell" and str(step_payload.get("status") or "") == "running":
                # A task-linked background process owns every subsequent
                # decision.  Continuing here would execute tools after the
                # frozen task seed and could replay them when the process
                # later resumes the task.
                break
            if action_kind == "write" and not plan.get("continueLoop"):
                break
            if not plan.get("continueLoop"):
                break
            if str(plan.get("nextStep") or "") == "done":
                break
        reasoning_trace = ensure_dict(reasoning_trace)
        context_usage = ensure_dict(context_usage)
        if prior_provider_request_count:
            context_usage["priorRequestCount"] = prior_provider_request_count
            context_usage["requestCount"] = (
                prior_provider_request_count
                + int(context_usage.get("requestCount") or 0)
            )
        first_plan = first_plan or last_plan or {}
        # 单步（含纯回复/未连接）保持与历史一致的顶层 plan 形状；多步才综合成 loop 计划。
        terminal_override = str(last_plan.get("nextStep") or "") in {
            "cancelled",
            "context_compaction_required",
            "loop_suppressed",
        }
        top_plan = last_plan if terminal_override else (
            first_plan if iterations <= 1 else self._summarize_loop_plan(
                message, first_plan, last_plan, steps
            )
        )
        if cap_reached and isinstance(top_plan, dict):
            top_plan["stepLimitReached"] = True
            top_plan["nextStep"] = "paused"
            top_plan["reason"] = "model_turn_budget_exhausted"
            base_reply = str(top_plan.get("reply") or "").rstrip()
            notice = (
                f"（已到本轮显式配置的 {task_loop.budget_policy.max_model_turns} 次模型轮次上限，先停下来汇报：上面是这一轮做到的部分。"
                "需要的话再说一声，我接着往下做。）"
            )
            top_plan["reply"] = f"{base_reply}\n\n{notice}".strip() if base_reply else notice
        terminal_status = str(top_plan.get("nextStep") or "").strip()
        if unresolved_planner_argument_failure is not None and terminal_status not in {
            "cancelled",
            "context_compaction_required",
            "loop_suppressed",
        }:
            top_plan = {
                **top_plan,
                "summary": "The model did not complete a valid correction for rejected tool arguments.",
                "reply": (
                    "The proposed tool arguments were invalid and no corrected tool action completed, "
                    "so this task is not marked done."
                ),
                "plannerFailed": True,
                "plannerFailure": {
                    "code": "planner_invalid_response",
                    "phase": "post_tool",
                    "retryable": True,
                },
                "unresolvedArgumentValidation": dict(
                    unresolved_planner_argument_failure
                ),
                "continueLoop": False,
                "nextStep": "planner_failed",
            }
            terminal_status = "planner_failed"
        if unresolved_completion_outcomes and terminal_status not in {
            "cancelled",
            "context_compaction_required",
            "loop_suppressed",
        }:
            gated_plan = completion_gate_plan(
                top_plan,
                next(iter(unresolved_completion_outcomes.values())),
            )
            if gated_plan is not None:
                top_plan = gated_plan
        self._emit_runtime_status(
            "verifying",
            session_id=session_id,
            turn_id=turn_id,
            client_turn_id=client_turn_id,
        )
        if isinstance(top_plan, dict):
            top_plan = task_loop.gate_terminal(top_plan)

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

        final_reply = str(top_plan.get("reply") or top_plan.get("summary") or "").strip()
        if final_reply:
            append_timeline_event(
                "assistant",
                label="Assistant",
                summary=final_reply,
                status=str(top_plan.get("nextStep") or "done"),
            )

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
        if timeline:
            turn["timeline"] = timeline
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
        continuation_source = str(
            ensure_dict(task_continuation).get("source") or ""
        ).strip()
        continuation_event = project_runtime_turn_event(
            {
                "continuationSource": continuation_source,
                "sessionId": session_id,
                "turnId": turn_id,
                "clientTurnId": client_turn_id,
                "plan": top_plan,
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
                continuation_event=continuation_event,
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
            "task": ensure_dict(top_plan.get("task")) or task_loop.snapshot(),
        }
        if timeline:
            payload["timeline"] = timeline
        if continuation_source:
            payload["continuationSource"] = continuation_source
        if approval_id and continuation_context:
            payload["resumedApprovalId"] = approval_id
        if client_turn_id:
            payload["clientTurnId"] = client_turn_id
        if consumed_steer_input_ids:
            payload["consumedSteerInputIds"] = consumed_steer_input_ids
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

    def submit_runtime_steer(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        session_id = str(params.get("session_id") or params.get("sessionId") or "").strip()
        target_client_turn_id = str(
            params.get("target_client_turn_id")
            or params.get("targetClientTurnId")
            or ""
        ).strip()
        input_id = str(params.get("client_turn_id") or params.get("clientTurnId") or "").strip()
        message = str(params.get("message") or "").strip()
        followup_lane_id = str(params.get("lane_id") or params.get("laneId") or params.get("followupLaneId") or "").strip()
        result = self._runtime_session_state.submit_steer(
            session_id=session_id,
            target_client_turn_id=target_client_turn_id,
            input_id=input_id,
            message=message,
            followup_lane_id=followup_lane_id,
        )
        return {
            "ok": result.get("accepted") is True,
            **result,
            "sessionId": session_id,
            "targetClientTurnId": target_client_turn_id,
            "clientTurnId": input_id,
        }

    def list_runtime_followups(self, *, session_id: str = "", include_terminal: bool = True) -> list[dict[str, Any]]:
        return self._runtime_followup_queue.list(session_id=session_id, include_terminal=include_terminal)

    def enqueue_runtime_followup(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._runtime_followup_queue.enqueue(
            session_id=str(params.get("sessionId") or params.get("session_id") or ""),
            client_turn_id=str(params.get("clientTurnId") or params.get("client_turn_id") or ""),
            target_client_turn_id=str(params.get("targetClientTurnId") or params.get("target_client_turn_id") or ""),
            message=str(params.get("message") or ""),
            attachments=params.get("attachments") if isinstance(params.get("attachments"), list) else [],
            envelope={"provider": params.get("provider"), "model": params.get("model"), "projectPath": params.get("projectPath"), "projectRoot": params.get("projectRoot"), "projectType": params.get("projectType"), "providerLabel": params.get("providerLabel")},
        )

    def claim_runtime_followups(self, *, session_id: str = "", owner_id: str = "", limit: int = 8, queue_id: str = "") -> list[dict[str, Any]]:
        return self._runtime_followup_queue.claim(session_id=session_id, owner_id=owner_id, limit=limit, queue_id=queue_id)

    def ack_runtime_followup(self, queue_id: str, session_id: str = "", claim_token: str = "") -> bool:
        return self._runtime_followup_queue.ack(queue_id=queue_id, session_id=session_id, claim_token=claim_token)

    def cancel_runtime_followup(self, queue_id: str, session_id: str = "", claim_token: str = "") -> bool:
        return self._runtime_followup_queue.cancel(queue_id=queue_id, session_id=session_id, claim_token=claim_token)

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
            plan["planner"] = first_plan.get("planner") or "llm"
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
            for item in self.approval_transactions.list_approvals(include_expired=False, project_root=project_root)
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
        memory_preferences = self.memory_preferences()
        memories = (
            self.list_agent_memory(
                limit=12,
                project_root=project_root,
                scope="" if project_root else "user",
            ).get("memories", [])
            if memory_preferences["crossSessionEnabled"]
            else []
        )
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
            "planner": {
                "mode": "provider_only",
                "providerRequired": True,
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
        cancelled_shell_session_ids: list[str] = []
        resolved_turn_id = turn_id
        resolved_client_turn_id = client_turn_id
        matching_run = None
        if turn_id or client_turn_id:
            matching_run = next(
                (
                    run
                    for run in self._runtime_run_ledger.list_runs(limit=200).get("runs", [])
                    if (
                        turn_id
                        and str(run.get("turnId") or "") == turn_id
                    )
                    or (
                        client_turn_id
                        and str(run.get("clientTurnId") or "") == client_turn_id
                    )
                ),
                None,
            )
        if not resolved_turn_id:
            resolved_turn_id = str((matching_run or {}).get("turnId") or "")
        if resolved_turn_id and not resolved_client_turn_id:
            resolved_client_turn_id = str((matching_run or {}).get("clientTurnId") or "")
        resolved_session_id = session_id or str((matching_run or {}).get("sessionId") or "")
        shell_owner_ids: set[str] = set()
        if resolved_turn_id:
            shell_owner_ids.add(
                self._runtime_shell_owner(resolved_turn_id, resolved_client_turn_id, resolved_session_id)
            )
        elif resolved_client_turn_id:
            shell_owner_ids.add(self._runtime_shell_owner("", resolved_client_turn_id, resolved_session_id))
        elif resolved_session_id:
            shell_owner_ids.add(resolved_session_id)
            shell_owner_ids.add(self._runtime_shell_owner("", "", resolved_session_id))
            for run in self._runtime_run_ledger.list_runs(limit=200).get("runs", []):
                if str(run.get("sessionId") or "") != resolved_session_id:
                    continue
                run_turn_id = str(run.get("turnId") or "")
                run_client_turn_id = str(run.get("clientTurnId") or "")
                shell_owner_ids.add(
                    self._runtime_shell_owner(run_turn_id, run_client_turn_id, resolved_session_id)
                )
        for shell_owner_id in shell_owner_ids:
            cancelled_shell_session_ids.extend(self.shell.cancel_owner(shell_owner_id))
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
        if cancelled_shell_session_ids:
            event["cancelledShellSessionIds"] = cancelled_shell_session_ids
        self._runtime_run_ledger.append(event)
        return {
            "ok": True,
            "status": "cancel_requested",
            "event": event,
            "cancelledDesktopActionIds": cancelled_desktop_action_ids,
            "cancelledShellSessionIds": cancelled_shell_session_ids,
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
            "maxAgenticTurns": freeze_agent_budget_policy(config.agent_budget_policy).max_model_turns,
        }

    def update_advanced_settings(
        self,
        *,
        developer_options_enabled: bool,
        computer_use_enabled: bool,
        background_goal_notifications_enabled: bool | None = None,
        max_agentic_turns: int | None = None,
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
            if max_agentic_turns is not None:
                config.agent_budget_policy = {"maxAgenticTurns": max(1, min(int(max_agentic_turns), 4096))}
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
        preferences = self.memory_preferences()
        if not preferences["memoryEnabled"]:
            raise AgentGatewayError("Memory is disabled in Settings.", status_code=409)
        if not preferences["crossSessionEnabled"]:
            raise AgentGatewayError("Cross-conversation Memory is disabled in Settings.", status_code=409)
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


    @staticmethod
    def _write_handler_allows_future_category(
        write_handler: AgentWriteHandler, approval: dict[str, Any]
    ) -> bool:
        if not write_handler.allow_future_category or not write_handler.approval_category:
            return False
        target_tool = str(approval.get("targetTool") or write_handler.name).lower()
        general_file_category = write_handler.approval_category.startswith("general-file-")
        if (
            not general_file_category
            and any(token in target_tool for token in SCOPED_ALLOW_RULE_FORBIDDEN_TOKENS)
        ):
            return False
        if normalize_risk_level(str(approval.get("riskLevel") or write_handler.risk_level)) in {"high", "critical"}:
            return False
        return True

    @contextmanager
    def local_state_write_guard(self) -> Iterator[None]:
        """Serialize direct local-state writes with checkpoint/recovery I/O."""

        with self._checkpoint_storage_lock:
            active = [
                recovery
                for recovery in self.checkpoint_recovery._active_apply_recoveries()
                if str(recovery.get("targetTool") or "")
                in LOCAL_STATE_CHECKPOINT_TARGETS
            ]
            if active:
                raise AgentGatewayError(
                    "A skill-package recovery is active. Restore or resolve it before changing local skill state.",
                    status_code=409,
                )
            yield

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
        serialized = {
            "name": tool.name,
            "description": tool_usage_description(tool.name, tool.description, write=tool.write),
            "category": tool.category,
            "write": tool.write,
            "advanced": tool.advanced,
            "available": self._tool_visible(tool, config),
            "requiresUserActivation": tool.requires_user_activation,
            "modelInvocable": model_invocable,
        }
        serialized["inputSchema"] = canonical_unity_read_tool_input_schema(tool.name)
        return serialized

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
            "inputsSchema": canonical_unity_read_tool_input_schema(tool.name),
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
            "requiresCheckpoint": bool(handler.pre_write_checkpoint_required),
            "rollbackPolicy": self.approval_transactions._write_handler_rollback_policy(handler),
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
        if tool.name in EXTERNAL_AGENT_INTERNAL_TOOLS:
            return False
        return self._tool_runtime_visible(tool, config, exposure_layer)

    def _tool_runtime_visible(
        self,
        tool: AgentTool,
        config: AgentGatewayConfig,
        exposure_layer: str = EXPOSURE_LAYER_EXECUTION,
    ) -> bool:
        """Apply permission visibility without exposing task-internal tools via MCP."""

        exposure_layer = normalize_exposure_layer(exposure_layer)
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

def create_agent_mcp_app(
    gateway: AgentGateway,
    *,
    on_pending_approval: Callable[[dict[str, Any]], None] | None = None,
):
    def list_tools(params: Mapping[str, Any]) -> list[dict[str, Any]]:
        exposure_layer = normalize_exposure_layer(params.get("exposureLayer"))
        return gateway.build_external_mcp_tools(
            exposure_layer,
            tool_blocks=params.get("toolBlocks"),
        )

    def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return gateway.call_external_mcp_tool(name, arguments, agent_name="mcp-agent")

    def validate_bearer(token: str) -> bool:
        config = gateway.ensure_config()
        authenticated = bool(config.enabled and config.token and hmac.compare_digest(token, config.token))
        if authenticated:
            gateway.mark_external_mcp_activity()
        return authenticated

    router = Mcp2026Router(
        list_tools,
        call_tool,
        server_name="VRCForge Agent Gateway",
        server_version="1.7.8",
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


def discard_runtime_image_payloads(
    attachments: Any,
) -> tuple[list[dict[str, Any]], int]:
    """Drop only inline image bytes while retaining bounded attachment identity."""

    projected: list[dict[str, Any]] = []
    discarded_count = 0
    for raw in ensure_list(attachments):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        data_url = str(item.get("dataUrl") or "")
        mime = str(item.get("type") or "").strip().lower()
        is_inline_image = (
            str(item.get("payloadKind") or "") == "data_url"
            and (mime.startswith("image/") or data_url.startswith("data:image/"))
        )
        if is_inline_image:
            item.pop("dataUrl", None)
            item["payloadKind"] = "metadata"
            item["replayable"] = False
            item["discardedAfterVisionError"] = True
            discarded_count += 1
        projected.append(item)
    return projected, discarded_count




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
                "control_token",
                "controltoken",
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
        "control_token",
        "controltoken",
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
                "control_token",
                "controltoken",
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
    "control_token",
    "controltoken",
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

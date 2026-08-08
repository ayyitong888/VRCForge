from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import ctypes
import hashlib
import hmac
import json
import math
import mimetypes
import os
import subprocess
import re
import secrets
import shutil
import socket
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path, PurePosixPath
from threading import Lock, RLock, Thread
from typing import Any, Callable, Literal, Mapping
from urllib.parse import urlsplit

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bounded_process import BoundedProcessResult, run_bounded_process
from agent_command_safety import normalize_filesystem_path

try:
    import psutil
except Exception:  # pragma: no cover - source installs may not include psutil.
    psutil = None  # type: ignore[assignment]

from agent_gateway import (
    AgentGateway,
    AgentGatewayError,
    PROJECTED_SKILL_STATE_MAX_BYTES,
    PROJECTED_SKILL_STATE_NAME,
    PROJECTED_SKILL_STATE_SCHEMA,
    create_agent_mcp_app,
    ensure_dict,
    normalize_bool,
    normalize_checkpoint_archive_max_size_mb,
    normalize_exposure_layer,
    parse_skill_markdown,
    redact_background_goal_persistence,
    redact_sensitive,
    summarize_text,
)
from desktop_computer_use_service import DESKTOP_BRIDGE_ACTION_TYPES
from agent_question_service import (
    AgentQuestionPersistence,
    AgentQuestionPersistencePorts,
    AgentQuestionScopePorts,
    AgentQuestionService,
    AgentQuestionServiceError,
    GoalQuestionResolutionPort,
)
from agent_goal_service import AgentGoalService, AgentGoalServiceError
from approval_auto_review import review_saved_project_category_approval
from agent_goal_store import GOAL_DELIVERY_RESULT_SCHEMA
from authoritative_unity_writes import (
    AuthoritativeUnityWriteError,
    authoritative_unity_write_has_strict_result,
    prepare_authoritative_unity_write,
    validate_authoritative_unity_write_result,
)
from constraint_source_write import (
    TOOL_NAME as CONSTRAINT_SOURCE_TOOL,
    build_wrapper_arguments as build_constraint_source_wrapper_arguments,
)
from component_feature_write import (
    TOOL_NAME as COMPONENT_FEATURE_TOOL,
    build_wrapper_arguments as build_component_feature_wrapper_arguments,
)
from backend_owner_lease import BackendOwnerLease
from backend_listener_adoption import (
    BackendListenerAdoptionError,
    backend_listener_adoption_requested,
    load_backend_listener_adoption,
)
from background_goal_delivery import (
    BackgroundGoalDeliveryCoordinator,
    BackgroundGoalDeliveryError,
    GoalEventPort,
    GoalLifecyclePort,
    RuntimeExecutionPort,
)
from background_goal_runtime import (
    PHASE_TIMEOUT_SECONDS,
    ProviderPreflightCache,
    RuntimeLaneBudget,
)
from chat_attachment_vault import (
    ARCHIVE_MAX_BYTES,
    INSPECTION_SCHEMA as CHAT_ATTACHMENT_INSPECTION_SCHEMA,
    ChatAttachmentVault,
    ChatAttachmentVaultError,
    extract_archive_entry_text,
    guard_vault_archive,
    inspect_image_bytes,
    is_vault_payload_hash,
)
from material_shader_assignment import (
    TOOL_NAME as MATERIAL_SHADER_ASSIGNMENT_TOOL,
    build_wrapper_arguments as build_material_shader_wrapper_arguments,
)
from atomic_reference_rename import (
    TOOL_NAME as ATOMIC_REFERENCE_RENAME_TOOL,
    build_wrapper_arguments as build_atomic_reference_rename_wrapper_arguments,
)
from parameter_bit_packing import (
    TOOL_NAME as PARAMETER_BIT_PACKING_TOOL,
    build_wrapper_arguments as build_parameter_bit_packing_wrapper_arguments,
)
from scene_object_copy import (
    DUPLICATE_TOOL_NAME as DUPLICATE_SCENE_OBJECT_TOOL,
    PREFAB_TOOL_NAME as SAVE_SCENE_OBJECT_AS_PREFAB_TOOL,
    build_wrapper_arguments as build_scene_object_copy_wrapper_arguments,
)
from texture_import_settings import (
    TOOL_NAME as TEXTURE_IMPORT_SETTINGS_TOOL,
    build_wrapper_arguments as build_texture_import_settings_wrapper_arguments,
)
from context_compaction import ContextCompactionInputError, compact_context
from dashboard_composition import DashboardCompositionContext
from developer_options_guard import DeveloperOptionsChallengeError, DeveloperOptionsGuard
from diagnostic_logging import (
    DiagnosticLogManager,
    format_log_line as format_diagnostic_log_line,
    install_standard_stream_capture,
    parse_log_line as parse_diagnostic_log_line,
    parse_log_timestamp as parse_diagnostic_log_timestamp,
)
from diagnostic_privacy import DiagnosticPrivacy
from diagnostic_safety import (
    TRACE_REQUIRES_DEVELOPER_OPTIONS,
    advanced_security_state,
    available_log_levels,
    build_safety_posture,
    changed_safety_flags,
    permission_security_state,
)
from doctor_readiness_report_service import DoctorReadinessReportPorts, DoctorReadinessReportService
from know_yourself_readiness_service import KnowYourselfReadinessPorts, KnowYourselfReadinessService
from doctor_service import (
    DoctorRule,
    DoctorService,
    DoctorServiceError,
    PhaseLog,
    sanitize_doctor_text,
    sanitize_doctor_value,
)
from model_provider_adapters import (
    ProviderApiTypeError,
    ProviderCredentialError,
    normalize_provider_api_type,
    provider_model_descriptor,
    validate_provider_api_key,
)
from provider_runtime_adapters import ProviderRuntimeRequest
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
    install_prepared_calls,
    prepared_call,
    prepared_evidence,
)
from prepared_file_imports import (
    capture_directory,
    capture_regular_file,
    cleanup_owned_import,
    copy_approved_file_create_new,
    prepare_project_asset_target,
)
from prepared_archive_imports import (
    cleanup_owned_zip_materialization,
    execute_zip_extract,
    execute_zip_member_materialization,
    prepare_zip_extract,
    prepare_zip_member_materialization,
)
from prepared_loose_outfit_import import execute_loose_outfit_import, prepare_loose_outfit_import
from prepared_blendshape_writes import (
    BLENDSHAPE_UNDO_LOCK,
    canonical_sha256 as blendshape_evidence_sha256,
    require_exact_evidence as require_exact_blendshape_evidence,
)
from prepared_shader_tuning_writes import (
    SHADER_UNDO_LOCK,
    canonical_sha256 as shader_evidence_sha256,
    require_exact_evidence as require_exact_shader_evidence,
)
from prepared_file_imports import verify_directory, verify_regular_file
from unity_execution_plans_scene import (
    SCENE_EXECUTION_PLAN_TARGETS,
    build_scene_execution_plan,
)
from unity_execution_plans_tuning import (
    TUNING_EXECUTION_PLAN_TARGETS,
    build_tuning_execution_plan,
)
from unity_execution_plans_workflows import (
    WORKFLOW_EXECUTION_PLAN_TARGETS,
    build_workflow_execution_plan,
)
from external_agent_connector_installer import (
    ConnectorInstallError,
    connector_client_statuses,
    install_connector,
    resolve_stdio_bridge,
    uninstall_connector,
)
from external_agent_connectors import ExternalAgentConnectorOptions, build_connector_bundle
from memory_review_dashboard_adapter import (
    MEMORY_REVIEW_AUDIT_SCAN_MAX_BYTES,
    MEMORY_REVIEW_AUDIT_SCAN_MAX_ROWS,
    MemoryReviewDashboardAdapter,
)
from memory_consolidation_sources import MemoryScope, project_scope_key
from memory_review_composition import MemoryReviewComposition, MemoryReviewCompositionPorts, build_memory_review_composition
from memory_review_host import build_memory_review_router
from memory_review_provider import invoke_memory_review_provider
from memory_review_runtime import MemoryReviewRuntimeCoordinator
from optimization_service import (
    OPTIMIZATION_APPLY_REQUEST_DEFINITIONS,
    OPTIMIZATION_GATEWAY_TOOL_NAMES,
    OPTIMIZER_DEPENDENCIES,
    OPTIMIZATION_TOOL_DEFINITIONS,
    STABLE_OPTIMIZATION_APPLY_REQUEST_DEFINITIONS,
    build_optimization_report,
    build_optimization_tool_result,
    normalize_tool_name,
)
from optimization_apply_preview import (
    OptimizationApplyPreviewError,
    OptimizationApplyPreviewPorts,
    OptimizationApplyPreviewService,
    confirmed_ttt_material_paths,
    meshia_relative_vertex_count,
    normalize_optimizer_profile_id,
)
from optimization_validation_delta import build_optimization_validation_delta
from optimization_workflow_service import (
    OptimizationWorkflowPorts,
    OptimizationWorkflowService,
    OptimizerProofStore,
    OptimizerProofStorePorts,
)
from avatar_tuning_workflow_service import (
    AvatarTuningApprovedWriteHandlers,
    AvatarTuningError,
    AvatarTuningLiveContext,
    AvatarTuningPreparedPorts,
    AvatarTuningPreparedService,
    AvatarTuningStorePaths,
    AvatarTuningStorePorts,
    AvatarTuningStoreService,
    AvatarTuningUndoStore,
    AvatarTuningWorkflowPorts,
    AvatarTuningWorkflowService,
    PreparedFaceTuningState,
)
from package_install_workflow_service import (
    PackageDetectionPorts,
    PackageDetectionService,
    PackageInstallApprovedWriteHandler,
    PackageInstallWorkflowPorts,
    PackageInstallWorkflowService,
    PackageManagerDiscoveryPorts,
    PackageManagerDiscoveryService,
    VpmPackageInstallExecutionPorts,
    VpmPackageInstallExecutor,
    VpmPackageInstallPreparationPorts,
    VpmPackageInstallPreparer,
    resolve_project_path,
)
from wardrobe_outfit_workflow_service import (
    AddModularAvatarComponentApprovedWritePorts,
    AddModularAvatarComponentApprovedWriteService,
    AddModularAvatarComponentPreviewPorts,
    AddModularAvatarComponentPreviewService,
    AddOutfitPartApprovedWritePorts,
    AddOutfitPartApprovedWriteService,
    AddOutfitPartPreviewPorts,
    AddOutfitPartPreviewService,
    AddWardrobeOutfitApprovedWritePorts,
    AddWardrobeOutfitApprovedWriteService,
    AddWardrobeOutfitPreviewPorts,
    AddWardrobeOutfitPreviewService,
    ClothingFxReadPorts,
    ClothingFxReadService,
    ManageWardrobeApprovedWritePorts,
    ManageWardrobeApprovedWriteService,
    ManageWardrobePreviewPorts,
    ManageWardrobePreviewService,
    SetupOutfitApprovedWritePorts,
    SetupOutfitApprovedWriteService,
    SetupOutfitPreviewPorts,
    SetupOutfitPreviewService,
    WardrobeArtifactReadPorts,
    WardrobeArtifactReadService,
    WardrobeOutfitApprovedWriteHandlers,
    WardrobeOutfitWorkflowError,
    WardrobeOutfitWorkflowPorts,
    WardrobeOutfitWorkflowService,
    build_add_outfit_part_request as build_owned_add_outfit_part_request,
    build_add_modular_avatar_component_request as build_owned_add_modular_avatar_component_request,
    build_add_wardrobe_outfit_request as build_owned_add_wardrobe_outfit_request,
    build_manage_wardrobe_request as build_owned_manage_wardrobe_request,
    validate_add_modular_avatar_component_request as validate_owned_add_modular_avatar_component_request,
)
from outfit_import_planner import (
    build_outfit_import_plan,
    build_post_import_outfit_validation,
    detect_magenta_materials,
)
from mcp_trigger_selection import SelectionReceiptAuthority, plan_mcp_tool_selection, tools_for_exposure_layer
from outfit_package_inspector import inspect_outfit_package, is_safe_archive_path, normalize_archive_name
from path_to_skill import DEFAULT_MIN_VRCFORGE_VERSION, PathToSkillError, build_path_to_skill_source
from primitive_basis_live_attestation import (
    PrimitiveBasisLiveSession,
    load_packaged_live_session_from_stdin,
)
from primitive_basis_live_runtime import (
    LiveRuntimeCallbacks,
    ModelPartCompositionLiveRuntime,
    PrimitiveBasisLiveRuntimeError,
)
from project_memory_index import scan_project_memory
from runtime_settings_safety import (
    load_runtime_settings_safely,
    read_runtime_settings_document_safely,
    runtime_settings_diagnostic,
)
from session_store_integrity import (
    SESSION_STORE_INTEGRITY_SCHEMA,
    SessionStoreTarget,
    is_valid_chat_record,
    load_strict_json,
    path_has_link_like_segment,
    repair_session_store,
    scan_session_store,
    scan_session_stores,
)
from shader_adapter_registry import (
    PRIMARY_AVATAR_ENCRYPTION_ADAPTER_IDS,
    normalize_shader_family_id,
    shader_adapter_definition,
    shader_family_label,
)
from skill_packages import SkillPackageError, SkillPackageService, _load_json_bytes
from skill_package_controller import SkillPackageController
from skill_package_governance import SkillPackageGovernanceService
from skill_package_projection import SkillPackageProjectionService
from path_to_skill_controller import PathToSkillDashboardController
from project_catalog_discovery import ProjectCatalogDiscovery
from project_snapshot_selection_service import ProjectSnapshotSelectionPorts, ProjectSnapshotSelectionService
from provider_model_catalog_service import (
    ProviderModelCatalogPolicyPorts,
    ProviderModelCatalogService,
)
from provider_configuration_service import (
    ProviderApiConfig,
    ProviderConfigurationPersistencePorts,
    ProviderConfigurationPolicyPorts,
    ProviderConfigurationService,
)
from provider_test_integration_service import (
    ProviderProbePolicyPorts,
    ProviderTestIntegrationService,
    ProviderTestServicePorts,
    ProviderTextProbeRunner,
)
from provider_vision_service import (
    ProviderVisionPolicyPorts,
    ProviderVisionSdkRunner,
    ProviderVisionService,
    ProviderVisionStatePorts,
    VisionModelConfig,
    VisionProfileConfig,
)
from shader_vision_protection_service import (
    ProtectionWorkflowPorts,
    ShaderVisionProtectionService,
    ShaderWorkflowPorts,
    VisionAuditWorkflowPorts,
)
from sub_agent_delegate import build_sub_agent_role_handlers, build_sub_agent_roles
from sub_agent_collaboration_service import SubAgentCollaborationPorts, SubAgentCollaborationService
from vrchat_blendshape_agent import (
    BlendshapePlan,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_MVP_EXPORT_PATH,
    DEFAULT_SETTINGS_PATH,
    McpResult,
    SelectedAvatar,
    Settings,
    UnityMcpError,
    build_planning_payload,
    build_anthropic_request_payload,
    build_gemini_generate_config,
    build_openai_compatible_request_payload,
    create_blendshape_plan,
    create_material_tuning_plan,
    create_shader_visual_review,
    export_blendshapes,
    filter_planning_payload_to_face_blendshapes,
    is_face_related_blendshape,
    get_provider_defaults,
    invoke_unity_mcp,
    load_export_payload,
    load_settings,
    mock_execute_payload,
    normalize_base_url,
    normalize_provider_name,
    normalize_reasoning_effort,
    model_rejects_fixed_temperature,
    reasoning_effort_variants,
    reasoning_variants_descriptor,
    provider_display_name,
    provider_requires_api_key,
    read_plan_json,
    request_llm_plan,
    request_llm_plan_with_metadata,
    render_apply_payload_json,
    render_preview,
    render_summary,
    save_plan,
    save_result,
    save_text,
    extract_json_block,
    try_parse_json,
    validate_plan,
    resolve_avatar_selection,
)
from unity_mcp_core_client import (
    UnityMcpCoreClient,
    UnityMcpCoreError,
    load_unity_mcp_core_connection,
)
from unity_status_service import UnityStatusPorts, UnityStatusService


def resolve_runtime_path(env_name: str, default: Path) -> Path:
    value = os.environ.get(env_name, "").strip()
    if not value:
        return default.resolve()
    return Path(value).expanduser().resolve()


def default_runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if executable.parent.name.lower() == "backend":
            return executable.parent.parent
        return executable.parent
    return Path(__file__).resolve().parent


def default_user_data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data).expanduser() / "VRCForge" / "agentic-app"
    return default_runtime_root()


ROOT_DIR = resolve_runtime_path("VRCFORGE_APP_DIR", default_runtime_root())
PORTABLE_MODE = bool(getattr(sys, "frozen", False)) or any(
    os.environ.get(name, "").strip()
    for name in (
        "VRCFORGE_APP_DIR",
        "VRCFORGE_USER_DATA_DIR",
        "VRCFORGE_CONFIG_DIR",
        "VRCFORGE_CONFIG_PATH",
        "VRCFORGE_LOG_DIR",
        "VRCFORGE_ARTIFACTS_DIR",
        "VRCFORGE_DASHBOARD_DIR",
        "VRCFORGE_SETTINGS_PATH",
    )
)
USER_DATA_DIR = resolve_runtime_path("VRCFORGE_USER_DATA_DIR", default_user_data_root())
DASHBOARD_DIR = resolve_runtime_path("VRCFORGE_DASHBOARD_DIR", ROOT_DIR / "dashboard")
CONFIG_DIR = resolve_runtime_path("VRCFORGE_CONFIG_DIR", USER_DATA_DIR / "config")
LOG_DIR = resolve_runtime_path("VRCFORGE_LOG_DIR", USER_DATA_DIR / "logs") if PORTABLE_MODE else ROOT_DIR / "artifacts" / "dashboard"
ARTIFACTS_DIR = resolve_runtime_path("VRCFORGE_ARTIFACTS_DIR", USER_DATA_DIR / "artifacts") if PORTABLE_MODE else ROOT_DIR / "artifacts"
DASHBOARD_ARTIFACTS_DIR = ARTIFACTS_DIR / "dashboard"
PARAMETER_SNAPSHOT_DIR = DASHBOARD_ARTIFACTS_DIR / "parameter_snapshots"
TUNING_HISTORY_PATH = DASHBOARD_ARTIFACTS_DIR / "tuning_history.json"
TUNING_PRESETS_PATH = DASHBOARD_ARTIFACTS_DIR / "tuning_presets.json"
TUNING_LOCKS_PATH = DASHBOARD_ARTIFACTS_DIR / "tuning_locks.json"
SHADER_TUNING_HISTORY_PATH = DASHBOARD_ARTIFACTS_DIR / "shader_tuning_history.json"
SHADER_TUNING_PRESETS_PATH = DASHBOARD_ARTIFACTS_DIR / "shader_tuning_presets.json"
SHADER_TUNING_LOCKS_PATH = DASHBOARD_ARTIFACTS_DIR / "shader_tuning_locks.json"
TOOLS_DIR = ROOT_DIR / "tools"
CONFIG_PATH = resolve_runtime_path("VRCFORGE_CONFIG_PATH", CONFIG_DIR / "config.json")
CONFIG_DOCUMENT_LOCK = RLock()
RUNTIME_SETTINGS_PATH = resolve_runtime_path(
    "VRCFORGE_SETTINGS_PATH",
    CONFIG_DIR / "settings.json" if PORTABLE_MODE else ROOT_DIR / DEFAULT_SETTINGS_PATH,
)
PROJECT_SELECTION_SCHEMA = "vrcforge.selected_project.v1"
LOCAL_LOG_PATH = LOG_DIR / "dashboard.log"
LOG_RETENTION = timedelta(days=5)
AGENT_GATEWAY_CONFIG_PATH = CONFIG_DIR / "agent_gateway.json"
AGENT_GATEWAY_AUDIT_DIR = DASHBOARD_ARTIFACTS_DIR / "agent_gateway"
DIAGNOSTICS_CONFIG_PATH = CONFIG_DIR / "diagnostics.json"
INTERACTION_LOG_PATH = LOG_DIR / "interactions.jsonl"
SUPPORT_BUNDLE_DIR = DASHBOARD_ARTIFACTS_DIR / "support-bundles"
PROJECT_MEMORY_INDEX_DIR = USER_DATA_DIR / "project-indexes"
SUB_AGENT_TASK_DIR = DASHBOARD_ARTIFACTS_DIR / "sub-agents"


def read_vrcforge_version() -> str:
    try:
        value = (ROOT_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return os.environ.get("VRCFORGE_VERSION", "").strip() or "0.0.0-dev"
    return value or os.environ.get("VRCFORGE_VERSION", "").strip() or "0.0.0-dev"


def resolve_app_session_token() -> str:
    token = os.environ.get("VRCFORGE_APP_SESSION_TOKEN", "").strip()
    if token:
        return token
    token_path = CONFIG_DIR / "app-session-token"
    try:
        if token_path.exists():
            existing = token_path.read_text(encoding="utf-8").strip()
            if len(existing) >= 32:
                return existing
        generated = secrets.token_urlsafe(32)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(generated, encoding="utf-8")
        return generated
    except OSError:
        return secrets.token_urlsafe(32)


def app_auth_disabled_for_test_process() -> bool:
    if os.environ.get("VRCFORGE_DISABLE_APP_AUTH", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return "pytest" in sys.modules


APP_SESSION_TOKEN = resolve_app_session_token()
APP_AUTH_REQUIRED = bool(APP_SESSION_TOKEN) and not app_auth_disabled_for_test_process()
MCP_TRIGGER_SELECTION_RECEIPTS = SelectionReceiptAuthority(ttl_seconds=900, max_receipts=256)
APP_DASHBOARD_SESSION_COOKIE = "vrcforge_dashboard_session"
APP_INTERNAL_SHUTDOWN_PATH = "/api/app/runtime/shutdown"
PRIMITIVE_BASIS_LIVE_SESSION = (
    None
    if backend_listener_adoption_requested()
    else load_packaged_live_session_from_stdin()
)
PRIMITIVE_BASIS_LIVE_RUNTIME: ModelPartCompositionLiveRuntime | None = None
APP_ALLOWED_ORIGINS = {
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://127.0.0.1:8757",
    "http://localhost:8757",
    "http://[::1]:8757",
    "http://127.0.0.1:1420",
    "http://localhost:1420",
}
VRCFORGE_UNITY_TOOL_REGISTRY = (
    "vrc_add_component",
    "vrc_add_modular_avatar_component",
    "vrc_add_outfit_part",
    "vrc_add_wardrobe_outfit",
    "vrc_apply_blendshapes",
    "vrc_apply_clothing_fx",
    "vrc_apply_material_tuning",
    "vrc_apply_parameter_optimization",
    "vrc_atomic_reference_rename",
    "vrc_build_parameter_bit_packed_clone",
    "vrc_capture_scene_view",
    "vrc_create_component_feature",
    "vrc_create_gameobject",
    "vrc_create_safe_backup",
    "vrc_delete_gameobject",
    "vrc_duplicate_scene_object",
    "vrc_ensure_animator_state",
    "vrc_ensure_expression_menu_control",
    "vrc_ensure_expression_parameter",
    "vrc_export_blendshapes",
    "vrc_export_vrm",
    "vrc_find_assets",
    "vrc_get_asset_info",
    "vrc_get_compile_errors",
    "vrc_get_gameobject",
    "vrc_get_property",
    "vrc_import_unitypackage",
    "vrc_inspect_modular_avatar_component",
    "vrc_inspect_primitive_basis_fixture",
    "vrc_instantiate_prefab",
    "vrc_manage_expression_menu",
    "vrc_manage_expression_parameters",
    "vrc_manage_fx_animator",
    "vrc_manage_wardrobe",
    "vrc_prepare_checkpoint",
    "vrc_read_avatar_descriptor",
    "vrc_refresh_asset_database",
    "vrc_reload_after_checkpoint_restore",
    "vrc_reload_primitive_basis_fixture",
    "vrc_remove_component",
    "vrc_rename_gameobject",
    "vrc_reparent_gameobject",
    "vrc_restore_safe_backup",
    "vrc_rollback_avatar_parameters",
    "vrc_save_scene_object_as_prefab",
    "vrc_scan_animation_bindings",
    "vrc_scan_avatar_controls",
    "vrc_scan_avatar_items",
    "vrc_scan_avatar_materials",
    "vrc_scan_avatar_parameters",
    "vrc_scan_avatar_performance",
    "vrc_scan_fx_animator",
    "vrc_scan_thry_avatar_performance",
    "vrc_scan_wardrobe",
    "vrc_set_constraint_sources",
    "vrc_set_gameobject_active",
    "vrc_set_material_shader",
    "vrc_set_property",
    "vrc_set_texture_import_settings",
    "vrc_setup_outfit",
    "vrc_toggle_scene_object",
    "vrc_unpack_prefab",
    "vrc_write_animation_curve",
    "vrc_write_avatar_descriptor",
)
# The Core registry is the installation acceptance fact: all 64 registered
# VRCForge tools must be discoverable from the project-scoped descriptor.
REQUIRED_VRCFORGE_UNITY_TOOLS = VRCFORGE_UNITY_TOOL_REGISTRY
VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS = frozenset(
    {
        "vrcforge_apply_blendshapes",
        "vrcforge_run_face_tuning",
        "vrcforge_apply_shader_tuning",
        "vrcforge_restore_shader_tuning",
        "vrcforge_undo_blendshapes",
        "vrcforge_apply_clothing_fx",
        "vrcforge_apply_parameter_optimization",
        "vrcforge_rollback_parameters",
        "vrcforge_setup_outfit",
        "vrcforge_add_wardrobe_outfit",
        "vrcforge_manage_wardrobe",
        "vrcforge_add_outfit_part",
        "vrcforge_add_modular_avatar_component",
        "vrcforge_create_wardrobe",
        "vrcforge_ensure_expression_parameter",
        "vrcforge_ensure_expression_menu_control",
        "vrcforge_ensure_animator_state",
        "vrcforge_write_avatar_descriptor",
        "vrcforge_write_animation_curve",
        "vrcforge_manage_expression_parameters",
        "vrcforge_manage_expression_menu",
        "vrcforge_manage_fx_animator",
        "vrcforge_add_outfit",
        "vrcforge_import_outfit_package",
        "vrcforge_import_chat_image",
        "vrcforge_import_chat_archive",
        "vrcforge_add_component",
        "vrcforge_remove_component",
        "vrcforge_set_property",
        "vrcforge_create_gameobject",
        "vrcforge_rename_gameobject",
        "vrcforge_reparent_gameobject",
        "vrcforge_delete_gameobject",
        "vrcforge_set_gameobject_active",
        "vrcforge_instantiate_prefab",
        "vrcforge_unpack_prefab",
        "vrcforge_configure_optimizer_component",
        "vrcforge_create_safe_backup",
        "vrcforge_capture_screenshot",
        "vrcforge_capture_multi_screenshot",
        "vrcforge_restore_safe_backup",
        "vrcforge_unity_mcp_write",
        "vrcforge_export_vrm",
        "vrcforge_toggle_scene_object",
    }
)
VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST = frozenset(
    {
        "vrc_apply_blendshapes",
        "vrc_apply_material_tuning",
        "vrc_apply_clothing_fx",
        "vrc_apply_parameter_optimization",
        "vrc_rollback_avatar_parameters",
        "vrc_set_material_shader",
        "vrc_duplicate_scene_object",
        "vrc_save_scene_object_as_prefab",
        "vrc_set_texture_import_settings",
        "vrc_set_constraint_sources",
        "vrc_create_component_feature",
        PARAMETER_BIT_PACKING_TOOL,
        ATOMIC_REFERENCE_RENAME_TOOL,
        "vrc_toggle_scene_object",
        "vrc_setup_outfit",
        "vrc_add_wardrobe_outfit",
        "vrc_manage_wardrobe",
        "vrc_add_outfit_part",
        "vrc_add_modular_avatar_component",
        "vrc_write_avatar_descriptor",
        "vrc_write_animation_curve",
        "vrc_manage_expression_parameters",
        "vrc_manage_expression_menu",
        "vrc_manage_fx_animator",
        "vrc_export_vrm",
        "vrc_add_component",
        "vrc_remove_component",
        "vrc_set_property",
        "vrc_create_gameobject",
        "vrc_rename_gameobject",
        "vrc_reparent_gameobject",
        "vrc_delete_gameobject",
        "vrc_set_gameobject_active",
        "vrc_instantiate_prefab",
        "vrc_unpack_prefab",
    }
)

ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
DASHBOARD_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

MATERIAL_SEMANTIC_PROPERTIES = {
    "base_color",
    "shade_color",
    "shadow_strength",
    "shadow_softness",
    "smoothness",
    "specular_strength",
    "rim_color",
    "rim_strength",
    "emission_color",
    "emission_strength",
    "matcap_strength",
    "outline_color",
    "outline_width",
    "normal_strength",
}

MATERIAL_COLOR_PROPERTIES = {
    "base_color",
    "shade_color",
    "rim_color",
    "emission_color",
    "outline_color",
}

MATERIAL_NUMERIC_RANGES = {
    "outline_width": (0.0, 0.25),
    "normal_strength": (0.0, 2.0),
    "emission_strength": (0.0, 2.0),
}

AVATAR_ENCRYPTION_SCHEMA = "vrcforge.avatar_encryption.v1"
AVATAR_ENCRYPTION_ADDON_VERSION = "1.0.1"
AVATAR_ENCRYPTION_PRIMARY_SHADER_FAMILIES = PRIMARY_AVATAR_ENCRYPTION_ADAPTER_IDS
AVATAR_ENCRYPTION_RECOMMENDED_PROFILE = "standard"
AVATAR_ENCRYPTION_BENCHMARK_TRIANGLES = (50_000, 100_000, 200_000)
AVATAR_ENCRYPTION_ADDON_APPLY_TOOL = "vrcforge_avatar_encryption_addon_apply"
AVATAR_ENCRYPTION_ADDON_REMOVE_TOOL = "vrcforge_avatar_encryption_addon_remove"
AVATAR_ENCRYPTION_ADDON_URL_ENV = "VRCFORGE_AVATAR_ENCRYPTION_ADDON_URL"
AVATAR_ENCRYPTION_ADDON_TOKEN_ENV = "VRCFORGE_AVATAR_ENCRYPTION_ADDON_TOKEN"
AVATAR_ENCRYPTION_PROFILES: dict[str, dict[str, Any]] = {
    "lite": {
        "id": "lite",
        "label": "Lite",
        "uiTitle": "轻量保护",
        "uiDescription": "最快，适合低端 Windows PC。",
        "icon": "shield",
        "recommended": False,
        "gpuCost": "lowest",
        "deviceFit": "Windows / low-end PC",
        "protectionLevel": "Low-overhead Avatar Encryption.",
        "plainProtection": "Low-overhead encryption.",
        "applyStatus": "available",
        "costWeight": 0.6,
    },
    "standard": {
        "id": "standard",
        "label": "Standard",
        "uiTitle": "标准保护",
        "uiDescription": "默认推荐，保护和流畅度平衡。",
        "icon": "shield",
        "recommended": True,
        "gpuCost": "balanced",
        "deviceFit": "PC default",
        "protectionLevel": "Recommended Avatar Encryption.",
        "plainProtection": "Recommended encryption.",
        "applyStatus": "available",
        "costWeight": 2.0,
    },
    "paranoid": {
        "id": "paranoid",
        "label": "Paranoid",
        "uiTitle": "最高保护",
        "uiDescription": "最强，适合高端 PC。",
        "icon": "shield",
        "recommended": False,
        "gpuCost": "highest",
        "deviceFit": "high-end PC",
        "protectionLevel": "Highest preview mode; additional proof is still required.",
        "plainProtection": "Highest preview mode.",
        "applyStatus": "blocked_until_blendshape_proof",
        "costWeight": 5.5,
    },
}


def runtime_settings_path() -> str:
    return str(RUNTIME_SETTINGS_PATH)


class DashboardRequest(BaseModel):
    instruction: str | None = Field(default=None, description="Natural language instruction for LLM planning.")
    avatar: str | None = Field(default=None, description="Exact or partial avatar path/name.")
    model: str | None = Field(default=None, description="Optional model override.")
    reference_image_path: str | None = Field(default=None, description="Optional local path or artifact URL for a face reference image.")
    reference_image_data_url: str | None = Field(default=None, description="Optional browser-uploaded image as a data URL.")
    source_reference_image_paths: list[str] = Field(default_factory=list, description="Optional before/current-face image paths or artifact URLs.")
    source_reference_image_data_urls: list[str] = Field(default_factory=list, description="Optional before/current-face uploaded images as data URLs.")
    target_reference_image_paths: list[str] = Field(default_factory=list, description="Optional target face image paths or artifact URLs.")
    target_reference_image_data_urls: list[str] = Field(default_factory=list, description="Optional target face uploaded images as data URLs.")
    source_mode: Literal["unity_live_export", "configured_export", "custom_export", "mvp_sample"] = "mvp_sample"
    export_json: str | None = Field(default=None, description="Optional local export JSON path.")
    plan_json: str | None = Field(default=None, description="Optional local plan JSON path.")
    settings_path: str = Field(default_factory=runtime_settings_path)
    mock_execute: bool = True
    min_confidence: float | None = None
    allow_low_confidence: bool = False
    save_artifacts: bool = True
    unity_host: str | None = None
    unity_port: int | None = None
    unity_instance: str | None = None
    project_path: str | None = Field(default=None, alias="projectPath")

    model_config = {"populate_by_name": True}


class ConnectionRequest(BaseModel):
    settings_path: str = Field(default_factory=runtime_settings_path)
    unity_host: str | None = None
    unity_port: int | None = None
    unity_instance: str | None = None
    project_path: str | None = Field(default=None, alias="projectPath")

    model_config = {"populate_by_name": True}


class DashboardStateRequest(BaseModel):
    settings_path: str = Field(default_factory=runtime_settings_path)
    project_path: str | None = Field(default=None, alias="projectPath")
    unity_host: str | None = None
    unity_port: int | None = None
    unity_instance: str | None = None

    model_config = {"populate_by_name": True}


class ProjectActionRequest(BaseModel):
    project_path: str | None = Field(default=None, alias="projectPath")

    model_config = {"populate_by_name": True}


class ProjectInstallRequest(BaseModel):
    project_path: str | None = Field(default=None, alias="projectPath")
    launch_unity: bool = False

    model_config = {"populate_by_name": True}


class UnityMcpRepairRequest(BaseModel):
    project_path: str = Field(default="", alias="projectPath")
    unity_editor_path: str = Field(default="", alias="unityEditorPath")
    allow_unity_relaunch: bool = Field(default=False, alias="allowUnityRelaunch")
    wait_seconds: int = Field(default=90, alias="waitSeconds", ge=5, le=360)
    close_timeout_seconds: int = Field(default=60, alias="closeTimeoutSeconds", ge=5, le=180)

    model_config = {"populate_by_name": True}


class DoctorFixRequest(BaseModel):
    mode: Literal["safe", "force"] = "safe"
    project_path: str = Field(default="", alias="projectPath")

    model_config = {"populate_by_name": True}


class ApiConfigRequest(BaseModel):
    provider: str = DEFAULT_LLM_PROVIDER
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None
    # ``None`` means an on-disk/request legacy configuration: preserve its
    # provider's historical transport instead of treating it as new ``auto``.
    api_type: str | None = None
    # Model-aware reasoning variant; empty means provider default/no override.
    thinking_level: str = ""


class ApiModelListRequest(ApiConfigRequest):
    pass


class ReasoningVariantsRequest(BaseModel):
    provider: str = DEFAULT_LLM_PROVIDER
    model: str = ""
    # Omitted remains a legacy request, preserving the historical transport.
    api_type: str | None = None


class VisionConfigRequest(BaseModel):
    """Standalone vision-model profile (independent from the chat provider).

    Follows the same key storage/redaction rules as the main API config.
    `enabled=False` keeps the saved profile but turns off delegation.
    """

    provider: str = ""
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None
    enabled: bool = True


class DiagnosticsConfigRequest(BaseModel):
    log_level: Literal["error", "warn", "info", "debug", "trace"] | None = Field(default=None, alias="logLevel")
    debug_logging: bool | None = Field(default=None, alias="debugLogging")

    model_config = {"populate_by_name": True}


class SupportBundleRequest(BaseModel):
    include_full_paths: bool = Field(default=False, alias="includeFullPaths")
    log_limit: int = Field(default=200, alias="logLimit", ge=1, le=500)


class AvatarSceneScanRequest(ConnectionRequest):
    pass


class AvatarBlendshapeListRequest(DashboardRequest):
    pass


class ManualBlendshapeItem(BaseModel):
    renderer_path: str
    blendshape_name: str
    target_weight: float = Field(ge=0.0, le=100.0)
    previous_weight: float | None = Field(default=None, ge=0.0, le=100.0)


class ManualBlendshapeApplyRequest(DashboardRequest):
    adjustments: list[ManualBlendshapeItem] = Field(default_factory=list)


class UndoBlendshapeRequest(ConnectionRequest):
    avatar_path: str


class TuningPresetCreateRequest(BaseModel):
    history_id: str
    name: str
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    max_presets: int = Field(default=10, ge=1, le=100)


class TuningPresetRenameRequest(BaseModel):
    name: str


class TuningPresetDuplicateRequest(BaseModel):
    name: str | None = None
    max_presets: int = Field(default=10, ge=1, le=100)


class TuningLocksUpdateRequest(BaseModel):
    avatar_path: str | None = None
    locked_blendshapes: list[dict[str, Any]] = Field(default_factory=list)


class TuningLocksAiSelectRequest(DashboardRequest):
    avatar_path: str | None = None
    action: Literal["lock", "unlock"] = "lock"
    selection_instruction: str = ""
    candidate_blendshapes: list[dict[str, Any]] = Field(default_factory=list)
    current_locked_blendshapes: list[dict[str, Any]] = Field(default_factory=list)


class AvatarScopedConnectionRequest(ConnectionRequest):
    avatar_path: str | None = Field(default=None, alias="avatarPath")
    project_path: str | None = Field(default=None, alias="projectPath")

    model_config = {"populate_by_name": True}


class ShaderMaterialScanRequest(AvatarScopedConnectionRequest):
    category_overrides: dict[str, str] = Field(default_factory=dict)


class ShaderMaterialPlanRequest(DashboardRequest):
    avatar_path: str | None = Field(default=None, alias="avatarPath")
    inventory: dict[str, Any] | None = None
    category_overrides: dict[str, str] = Field(default_factory=dict)
    locked_materials: list[str] = Field(default_factory=list)
    locked_properties: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ShaderMaterialApplyRequest(ShaderMaterialPlanRequest):
    changes: list[dict[str, Any]] = Field(default_factory=list)
    history_id: str | None = None


class ShaderMaterialRestoreRequest(AvatarScopedConnectionRequest):
    pass


class ShaderTuningPresetCreateRequest(BaseModel):
    history_id: str
    name: str
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    max_presets: int = Field(default=10, ge=1, le=100)


class ShaderTuningPresetRenameRequest(BaseModel):
    name: str


class ShaderTuningPresetDuplicateRequest(BaseModel):
    name: str | None = None
    max_presets: int = Field(default=10, ge=1, le=100)


class ShaderTuningLocksUpdateRequest(BaseModel):
    avatar_path: str | None = None
    locked_materials: list[str] = Field(default_factory=list)
    locked_properties: list[str] = Field(default_factory=list)


class ShaderVisionReviewRequest(DashboardRequest):
    avatar_path: str | None = None
    goal: str | None = None
    before_image_paths: list[str] = Field(default_factory=list)
    after_image_paths: list[str] = Field(default_factory=list)


class AvatarEncryptionResearchRequest(BaseModel):
    include_external_references: bool = Field(default=True, alias="includeExternalReferences")

    model_config = {"populate_by_name": True}


class AvatarEncryptionScanRequest(AvatarScopedConnectionRequest):
    inventory: dict[str, Any] | None = None
    include_compatibility: bool = Field(default=True, alias="includeCompatibility")

    model_config = {"populate_by_name": True}


class AvatarEncryptionPlanRequest(AvatarEncryptionScanRequest):
    target_shader_families: list[str] = Field(
        default_factory=lambda: list(AVATAR_ENCRYPTION_PRIMARY_SHADER_FAMILIES),
        alias="targetShaderFamilies",
    )
    material_ids: list[str] = Field(default_factory=list, alias="materialIds")
    renderer_paths: list[str] = Field(default_factory=list, alias="rendererPaths")
    targets: list[dict[str, Any]] = Field(default_factory=list)
    profile: str = AVATAR_ENCRYPTION_RECOMMENDED_PROFILE
    protection_profile: str | None = Field(default=None, alias="protectionProfile")
    platform: str = "pc"
    target_platform: str | None = Field(default=None, alias="targetPlatform")
    confirm_creator_owned_assets: bool = Field(default=False, alias="confirmCreatorOwnedAssets")

    model_config = {"populate_by_name": True}


class AvatarEncryptionPreviewRequest(AvatarEncryptionPlanRequest):
    plan: dict[str, Any] | None = None


class AvatarEncryptionApplyRequest(AvatarEncryptionPreviewRequest):
    target_shader_family: str | None = Field(default=None, alias="targetShaderFamily")
    output_folder: str = Field(default="Assets/VRCForgeGenerated/AvatarEncryption", alias="outputFolder")
    preview_unity_write: bool = Field(default=False, alias="previewUnityWrite")
    save_assets: bool = Field(default=True, alias="saveAssets")

    model_config = {"populate_by_name": True}


class AvatarEncryptionRemoveRequest(AvatarScopedConnectionRequest):
    manifest_path: str | None = Field(default=None, alias="manifestPath")
    output_folder: str = Field(default="Assets/VRCForgeGenerated/AvatarEncryption", alias="outputFolder")
    delete_generated_assets: bool = Field(default=True, alias="deleteGeneratedAssets")
    confirm_remove: bool = Field(default=False, alias="confirmRemove")
    preview_unity_write: bool = Field(default=False, alias="previewUnityWrite")
    save_assets: bool = Field(default=True, alias="saveAssets")

    model_config = {"populate_by_name": True}


class AdjustmentCheckpointCreateRequest(BaseModel):
    kind: Literal["face", "shader"]
    id: str | None = None
    label: str = ""
    description: str = ""
    checkpoint_id: str | None = Field(default=None, alias="checkpointId")
    project_root: str | None = Field(default=None, alias="projectRoot")
    avatar_path: str | None = Field(default=None, alias="avatarPath")
    tags: list[str] = Field(default_factory=list)
    compare_group: str = Field(default="", alias="compareGroup")
    overwrite: bool = False

    model_config = {"populate_by_name": True}


class AdjustmentCheckpointUpdateRequest(BaseModel):
    kind: Literal["face", "shader"] | None = None
    label: str | None = None
    description: str | None = None
    checkpoint_id: str | None = Field(default=None, alias="checkpointId")
    project_root: str | None = Field(default=None, alias="projectRoot")
    avatar_path: str | None = Field(default=None, alias="avatarPath")
    tags: list[str] | None = None
    compare_group: str | None = Field(default=None, alias="compareGroup")

    model_config = {"populate_by_name": True}


class AdjustmentCheckpointOverwriteRequest(AdjustmentCheckpointCreateRequest):
    kind: Literal["face", "shader"] | None = None


class AdjustmentCheckpointSelectRequest(BaseModel):
    slot: Literal["A", "B", "current"] = "current"
    compare_group: str = Field(default="", alias="compareGroup")

    model_config = {"populate_by_name": True}


class InterruptedApplyRecoveryResolveRequest(BaseModel):
    confirm_resolved: bool = Field(default=False, alias="confirmResolved")
    note: str = ""

    model_config = {"populate_by_name": True}


class ClothingToggleRequest(ConnectionRequest):
    object_path: str
    active: bool


class VisionCaptureRequest(ConnectionRequest):
    avatar_path: str | None = None
    width: int = Field(default=960, ge=256, le=2048)
    height: int = Field(default=960, ge=256, le=2048)
    require_play_mode: bool = False


class VisionCaptureStatusRequest(ConnectionRequest):
    require_play_mode: bool = False


class VisionAuditRequest(ConnectionRequest):
    image_path: str | None = None


class ClothingApplyFxRequest(AvatarScopedConnectionRequest):
    """Trigger full FX asset authoring for detected clothing objects."""
    items: list[dict] = Field(default_factory=list, description="Clothing items from /api/clothes/scan or /api/clothes/generate-fx.")
    dry_run: bool = Field(default=True, description="If true return the MCP apply payload without executing in Unity.")


class ParameterApplyOptimizationRequest(AvatarScopedConnectionRequest):
    """Apply selected Int->Bool parameter optimizations to VRCExpressionParameters."""
    suggestions: list[dict] = Field(default_factory=list, description="Suggestions from /api/parameters/optimize.")
    dry_run: bool = Field(default=True, description="If true return the MCP apply payload without executing in Unity.")


class ParameterRollbackRequest(AvatarScopedConnectionRequest):
    """Restore VRCExpressionParameters from a snapshot saved before optimization."""
    snapshot_path: str | None = Field(default=None, description="Snapshot JSON path returned by /api/parameters/apply-optimization.")


class VisionCaptureMultiRequest(ConnectionRequest):
    avatar_path: str | None = None
    angles: list[str] = Field(default_factory=lambda: ["front", "side_left", "side_right", "back"])
    width: int = Field(default=960, ge=256, le=2048)
    height: int = Field(default=960, ge=256, le=2048)
    require_play_mode: bool = False


class VisionAuditMultiRequest(ConnectionRequest):
    image_paths: list[str] = Field(default_factory=list)


class AgentToolRequest(BaseModel):
    agent_name: str = "external-agent"
    params: dict[str, Any] = Field(default_factory=dict)


class AgentSessionRequest(BaseModel):
    agent_name: str = "external-agent"


class AgentRuntimeMessageRequest(BaseModel):
    agent_name: str = "desktop-agent"
    session_id: str | None = None
    client_turn_id: str | None = Field(default=None, alias="clientTurnId")
    goal_delivery_id: str | None = Field(default=None, alias="goalDeliveryId")
    message: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    shell_command: str | None = None
    skill_tool: str | None = None
    skill_params: dict[str, Any] = Field(default_factory=dict)
    cwd: str | None = None
    workspace_root: str | None = None
    project_path: str | None = Field(default=None, alias="projectPath")
    project_root: str | None = Field(default=None, alias="projectRoot")
    provider: str | None = None
    provider_label: str | None = Field(default=None, alias="providerLabel")
    model: str | None = None
    context_limit: int | None = Field(default=None, alias="contextLimit", gt=0, le=10_000_000)
    history: list[dict[str, Any]] = Field(default_factory=list)
    computer_use_requested: bool = Field(default=False, alias="computerUseRequested")
    computer_use_grant_id: str | None = Field(default=None, alias="computerUseGrantId")
    computer_use_visual_theme: str | None = Field(default=None, alias="computerUseVisualTheme")
    computer_use_visual_accent: str | None = Field(default=None, alias="computerUseVisualAccent")

    model_config = {"populate_by_name": True}


class AgentRuntimeCancelRequest(BaseModel):
    session_id: str | None = Field(default=None, alias="sessionId")
    turn_id: str | None = Field(default=None, alias="turnId")
    client_turn_id: str | None = Field(default=None, alias="clientTurnId")
    reason: str = "user_stop"

    model_config = {"populate_by_name": True}


class ComputerUseTurnGrantRequest(BaseModel):
    session_id: str | None = Field(default=None, alias="sessionId")
    client_turn_id: str = Field(alias="clientTurnId")
    project_root: str | None = Field(default=None, alias="projectRoot")

    model_config = {"populate_by_name": True}


class AgentRuntimeQueueRequest(BaseModel):
    session_id: str | None = Field(default=None, alias="sessionId")
    client_turn_id: str = Field(alias="clientTurnId")
    message: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    provider: str | None = None
    provider_label: str | None = Field(default=None, alias="providerLabel")
    model: str | None = None
    project_path: str | None = Field(default=None, alias="projectPath")
    project_root: str | None = Field(default=None, alias="projectRoot")

    model_config = {"populate_by_name": True}


class AgentDesktopActionRequest(BaseModel):
    action: Literal["screenshot", "annotation", "browser", "desktop_rescue", "computer_use"]
    prompt: str = ""
    session_id: str | None = Field(default=None, alias="sessionId")
    client_turn_id: str | None = Field(default=None, alias="clientTurnId")
    project_path: str | None = Field(default=None, alias="projectPath")
    project_root: str | None = Field(default=None, alias="projectRoot")
    params: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class DesktopBridgeRegisterRequest(BaseModel):
    name: str = ""
    provider: str = ""
    capabilities: list[str] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class DesktopBridgeHeartbeatRequest(BaseModel):
    bridge_id: str = Field(alias="bridgeId")
    bridge_credential: str = Field(alias="bridgeCredential")

    model_config = {"populate_by_name": True}


class DesktopActionClaimRequest(BaseModel):
    bridge_id: str = Field(alias="bridgeId")
    bridge_credential: str = Field(alias="bridgeCredential")
    actions: list[str] = Field(default_factory=list)
    claim_request_id: str = Field(default="", alias="claimRequestId")

    model_config = {"populate_by_name": True}


class DesktopActionCompleteRequest(BaseModel):
    bridge_id: str = Field(alias="bridgeId")
    bridge_credential: str = Field(alias="bridgeCredential")
    action_id: str = Field(alias="actionId")
    status: Literal["completed", "failed", "cancelled"] = "completed"
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""

    model_config = {"populate_by_name": True}


class DesktopActionCancelRequest(BaseModel):
    reason: str = ""


class AgentGoalCreateRequest(BaseModel):
    title: str = ""
    goal: str = ""
    summary: str = ""
    wake_at: str | None = Field(default=None, alias="wakeAt")
    wake_every_minutes: int | None = Field(default=None, alias="wakeEveryMinutes")
    session_id: str | None = Field(default=None, alias="sessionId")
    chat_id: str | None = Field(default=None, alias="chatId")
    project_path: str | None = Field(default=None, alias="projectPath")
    project_root: str | None = Field(default=None, alias="projectRoot")

    model_config = {"populate_by_name": True}


class AgentGoalUpdateRequest(BaseModel):
    status: Literal["active", "paused", "completed", "cancelled"]
    summary: str = ""
    note: str = ""
    wake_at: str | None = Field(default=None, alias="wakeAt")
    wake_every_minutes: int | None = Field(default=None, alias="wakeEveryMinutes")
    session_id: str | None = Field(default=None, alias="sessionId")
    chat_id: str | None = Field(default=None, alias="chatId")
    project_root: str | None = Field(default=None, alias="projectRoot")

    model_config = {"populate_by_name": True}


class AgentGoalWakeRequest(BaseModel):
    session_id: str | None = Field(default=None, alias="sessionId")
    chat_id: str | None = Field(default=None, alias="chatId")
    project_root: str | None = Field(default=None, alias="projectRoot")

    model_config = {"populate_by_name": True}


class AgentGoalOwnerBindRequest(BaseModel):
    session_id: str | None = Field(default=None, alias="sessionId")
    chat_id: str = Field(alias="chatId")
    project_root: str | None = Field(default=None, alias="projectRoot")

    model_config = {"populate_by_name": True}


class AgentGoalDeliveryMaterializeRequest(BaseModel):
    chat_id: str = Field(alias="chatId")
    expected_revision: int | None = Field(default=None, alias="expectedRevision")

    model_config = {"populate_by_name": True}


class AgentGoalDeliveryDeferRequest(BaseModel):
    expected_revision: int | None = Field(default=None, alias="expectedRevision")

    model_config = {"populate_by_name": True}


class AgentGoalBackgroundAcknowledgeItem(BaseModel):
    delivery_id: str = Field(alias="deliveryId")
    expected_revision: int | None = Field(default=None, alias="expectedRevision")

    model_config = {"populate_by_name": True}


class AgentGoalBackgroundAcknowledgeRequest(BaseModel):
    chat_id: str = Field(alias="chatId")
    delivery_ids: list[str] = Field(default_factory=list, alias="deliveryIds")
    deliveries: list[AgentGoalBackgroundAcknowledgeItem] = Field(default_factory=list)
    kind: Literal["recap", "toast", "provider"] = "recap"

    model_config = {"populate_by_name": True}


class AgentProgressItemRequest(BaseModel):
    id: str | None = None
    progress_id: str | None = Field(default=None, alias="progressId")
    title: str = ""
    step: str = ""
    content: str = ""
    summary: str = ""
    description: str = ""
    status: str = "pending"
    order: int | None = None
    owner: str = "agent"
    session_id: str | None = Field(default=None, alias="sessionId")
    project_path: str | None = Field(default=None, alias="projectPath")
    project_root: str | None = Field(default=None, alias="projectRoot")
    goal_delivery_id: str | None = Field(default=None, alias="goalDeliveryId")

    model_config = {"populate_by_name": True}


class AgentProgressReplaceRequest(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    plan: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str | None = Field(default=None, alias="sessionId")
    project_path: str | None = Field(default=None, alias="projectPath")
    project_root: str | None = Field(default=None, alias="projectRoot")
    goal_delivery_id: str | None = Field(default=None, alias="goalDeliveryId")

    model_config = {"populate_by_name": True}


class AgentQuestionCreateRequest(BaseModel):
    header: str = ""
    question: str = ""
    prompt: str = ""
    options: list[Any] = Field(default_factory=list)
    choices: list[Any] = Field(default_factory=list)
    owner: str = "agent"
    session_id: str | None = Field(default=None, alias="sessionId")
    project_path: str | None = Field(default=None, alias="projectPath")
    project_root: str | None = Field(default=None, alias="projectRoot")
    goal_delivery_id: str | None = Field(default=None, alias="goalDeliveryId")

    model_config = {"populate_by_name": True}


class AgentQuestionAnswerRequest(BaseModel):
    answer: str = ""
    value: str = ""
    option_id: str = Field(default="", alias="optionId")
    selected_option_id: str = Field(default="", alias="selectedOptionId")
    session_id: str | None = Field(default=None, alias="sessionId")
    project_root: str | None = Field(default=None, alias="projectRoot")

    model_config = {"populate_by_name": True}


class AgentMemoryCreateRequest(BaseModel):
    text: str = ""
    content: str = ""
    scope: Literal["user", "project"] = "project"
    kind: str = "preference"
    source: str = "user"
    project_path: str | None = Field(default=None, alias="projectPath")
    project_root: str | None = Field(default=None, alias="projectRoot")

    model_config = {"populate_by_name": True}


class AgentMemoryDeleteRequest(BaseModel):
    reason: str = ""


class AgentMemoryClearRequest(BaseModel):
    scope: Literal["user", "project"]
    reason: str = "clear"
    project_root: str | None = Field(default=None, alias="projectRoot")

    model_config = {"populate_by_name": True}


class AgentApprovalRevisionRequest(BaseModel):
    reason: str = ""
    note: str = ""
    expected_project_root: str | None = Field(default=None, alias="expectedProjectRoot")
    global_only: bool = Field(default=False, alias="globalOnly")

    model_config = {"populate_by_name": True}


class AgentApprovalScopeRequest(BaseModel):
    expected_project_root: str | None = Field(default=None, alias="expectedProjectRoot")
    global_only: bool = Field(default=False, alias="globalOnly")
    allow_future_category: bool = Field(default=False, alias="allowFutureCategory")

    model_config = {"populate_by_name": True}


class AgentPermissionRequest(BaseModel):
    execution_mode: str = Field(default="approval")
    acknowledge_roslyn_risk: bool = Field(default=False)


class PrimitiveBasisLiveStartRequest(BaseModel):
    project_path: str = Field(alias="projectPath", min_length=1, max_length=4096)

    model_config = {"populate_by_name": True}


class AdvancedSettingsRequest(BaseModel):
    developer_options_enabled: bool = Field(default=False, alias="developerOptionsEnabled")
    computer_use_enabled: bool = Field(default=False, alias="computerUseEnabled")
    background_goal_notifications_enabled: bool | None = Field(
        default=None,
        alias="backgroundGoalNotificationsEnabled",
    )
    developer_challenge_id: str | None = Field(default=None, alias="developerChallengeId", max_length=128)

    model_config = {"populate_by_name": True}


class AgentNotesRequest(BaseModel):
    content: str = Field(default="", max_length=262144)


class ChatTranscriptsRequest(BaseModel):
    chats: list[dict[str, Any]] = Field(default_factory=list)
    source_revisions: list[dict[str, Any]] = Field(default_factory=list, alias="sourceRevisions")

    model_config = {"populate_by_name": True}


class ChatAttachmentImportRequest(BaseModel):
    payload_hash: str = Field(alias="payloadHash")
    project_path: str = Field(default="", alias="projectPath")
    target_folder: str = Field(default="", alias="targetFolder")
    selected_unitypackage: str = Field(default="", alias="selectedUnityPackage")
    selected_prefab: str = Field(default="", alias="selectedPrefab")
    base_avatar_name: str = Field(default="", alias="baseAvatarName")
    max_entries: int = Field(default=5000, alias="maxEntries", ge=1, le=50000)

    model_config = {"populate_by_name": True}


class ChatAttachmentUploadBeginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    chat_id: str = Field(alias="chatId", min_length=1, max_length=256)
    declared_type: str = Field(default="application/octet-stream", alias="declaredType", max_length=256)
    size: int = Field(ge=1, le=ARCHIVE_MAX_BYTES)

    model_config = {"populate_by_name": True}


class ChatAttachmentUploadFinishRequest(BaseModel):
    upload_id: str = Field(alias="uploadId", min_length=16, max_length=128)

    model_config = {"populate_by_name": True}


class ProjectPrefsRequest(BaseModel):
    custom_paths: list[str] = Field(default_factory=list, alias="customPaths")
    hidden_paths: list[str] = Field(default_factory=list, alias="hiddenPaths")

    model_config = {"populate_by_name": True}


class ExternalAgentConnectorRequest(BaseModel):
    server_name: str = Field(default="vrcforge", alias="serverName")
    mcp_url: str = Field(default="http://127.0.0.1:8757/mcp", alias="mcpUrl")
    token_env_var: str = Field(default="VRCFORGE_AGENT_TOKEN", alias="tokenEnvVar")
    skills_projection_dir: str | None = Field(default=None, alias="skillsProjectionDir")

    model_config = {"populate_by_name": True}


class ExternalAgentGatewayUpdateRequest(BaseModel):
    enabled: bool | None = None
    allow_write_requests: bool | None = Field(default=None, alias="allowWriteRequests")
    revoke_token: bool = Field(default=False, alias="revokeToken")
    checkpoint_archive_max_size_mb: int | None = Field(default=None, alias="checkpointArchiveMaxSizeMb")
    delete_checkpoint_archive_ids: list[str] | None = Field(default=None, alias="deleteCheckpointArchiveIds")
    checkpoint_archive_directory: str | None = Field(default=None, alias="checkpointArchiveDirectory")

    model_config = {"populate_by_name": True}


class ExternalAgentConnectorActionRequest(BaseModel):
    client: Literal["codex", "codexApp", "codexCli", "claudeCode", "claudeCowork", "generic"]
    project_path: str | None = Field(default=None, alias="projectPath")
    config_path: str | None = Field(default=None, alias="configPath")

    model_config = {"populate_by_name": True}


class SkillPackagePathRequest(BaseModel):
    package_path: str = Field(alias="packagePath")
    allow_downgrade: bool = Field(default=False, alias="allowDowngrade")
    dev_mode: bool = Field(default=False, alias="devMode")
    project_to_user_skills: bool = Field(default=True, alias="projectToUserSkills")
    dry_run: bool = Field(default=False, alias="dryRun")

    model_config = {"populate_by_name": True}


class SkillPackageSafeModeRequest(BaseModel):
    enabled: bool
    reason: str | None = None

    model_config = {"populate_by_name": True}


class SkillPackageSignerRequest(BaseModel):
    signer_fingerprint: str = Field(alias="signerFingerprint")
    reason: str | None = None

    model_config = {"populate_by_name": True}


class SkillPackageBlockRequest(BaseModel):
    package_id: str | None = Field(default=None, alias="packageId")
    package_sha256: str | None = Field(default=None, alias="packageSha256")
    lock_sha256: str | None = Field(default=None, alias="lockSha256")
    reason: str | None = None

    model_config = {"populate_by_name": True}


class SkillPackageExportRequest(BaseModel):
    skill_name: str = Field(alias="skillName")
    output_path: str = Field(alias="outputPath")
    release: bool = False
    private_key_path: str | None = Field(default=None, alias="privateKeyPath")
    private_key_pem: str | None = Field(default=None, alias="privateKeyPem")

    model_config = {"populate_by_name": True}


class PathToSkillCaptureRequest(BaseModel):
    summary: dict[str, Any] = Field(default_factory=dict)
    package_id: str | None = Field(default=None, alias="packageId")
    skill_name: str | None = Field(default=None, alias="skillName")
    title: str | None = None
    version: str = "1.0.0"
    author: str = "VRCForge User"
    min_vrcforge_version: str | None = Field(default=None, alias="minVrcforgeVersion")
    output_path: str | None = Field(default=None, alias="outputPath")
    write_source: bool = Field(default=False, alias="writeSource")
    use_temp_output: bool = Field(default=True, alias="useTempOutput")
    export_vsk: bool = Field(default=False, alias="exportVsk")
    confirm_export: bool = Field(default=False, alias="confirmExport")
    package_output_path: str | None = Field(default=None, alias="packageOutputPath")

    model_config = {"populate_by_name": True}


class SkillPackageStateRequest(BaseModel):
    enabled: bool
    sync_projected_skill: bool = Field(default=True, alias="syncProjectedSkill")

    model_config = {"populate_by_name": True}


class SkillPackageUninstallRequest(BaseModel):
    remove_projected_skill: bool = Field(default=True, alias="removeProjectedSkill")

    model_config = {"populate_by_name": True}


class ValidationReportRequest(BaseModel):
    avatar_path: str = Field(default="", alias="avatarPath")
    project_path: str = Field(default="", alias="projectPath")
    include_quest: bool = Field(default=True, alias="includeQuest")
    include_sources: bool = Field(default=False, alias="includeSources")
    include_readiness: bool = Field(default=True, alias="includeReadiness")
    gate_build: bool = Field(default=True, alias="gateBuild")
    max_errors: int = Field(default=50, alias="maxErrors")

    model_config = {"populate_by_name": True}


class BuildTestReadinessRequest(BaseModel):
    avatar_path: str = Field(default="", alias="avatarPath")
    project_path: str = Field(default="", alias="projectPath")
    include_quest: bool = Field(default=True, alias="includeQuest")
    max_errors: int = Field(default=50, alias="maxErrors")

    model_config = {"populate_by_name": True}


class OptimizationPlanRequest(BaseModel):
    avatar_path: str = Field(default="", alias="avatarPath")
    project_path: str = Field(default="", alias="projectPath")
    target_profile: str = Field(default="pc_conservative", alias="targetProfile")
    custom_profile: dict[str, Any] = Field(default_factory=dict, alias="customProfile")
    include_quest: bool = Field(default=True, alias="includeQuest")
    max_errors: int = Field(default=50, alias="maxErrors")

    model_config = {"populate_by_name": True}


class OptimizationToolRequest(OptimizationPlanRequest):
    tool: str = Field(default="", alias="tool")

    model_config = {"populate_by_name": True}


class OptimizationApplyRequest(BaseModel):
    tool: str = Field(default="", alias="tool")
    avatar_path: str = Field(default="", alias="avatarPath")
    project_path: str = Field(default="", alias="projectPath")
    target_profile: str = Field(default="pc_conservative", alias="targetProfile")
    profile: str = Field(default="", alias="profile")
    options: dict[str, Any] = Field(default_factory=dict)
    install_missing_dependencies: bool = Field(default=False, alias="installMissingDependencies")
    allow_experimental: bool = Field(default=False, alias="allowExperimental")
    include_prerelease: bool = Field(default=False, alias="includePrerelease")

    model_config = {"populate_by_name": True}


class OptimizationValidationDeltaRequest(BaseModel):
    before_validation: dict[str, Any] = Field(default_factory=dict, alias="beforeValidation")
    after_validation: dict[str, Any] = Field(default_factory=dict, alias="afterValidation")
    rollback_validation: dict[str, Any] = Field(default_factory=dict, alias="rollbackValidation")
    optimizer_tool: str = Field(default="", alias="optimizerTool")
    approval_id: str = Field(default="", alias="approvalId")
    checkpoint_id: str = Field(default="", alias="checkpointId")

    model_config = {"populate_by_name": True}


class ProjectIndexScanRequest(BaseModel):
    project_path: str = Field(alias="projectPath")
    max_files: int = Field(default=100000, alias="maxFiles", ge=1, le=250000)

    model_config = {"populate_by_name": True}


class OutfitPackageInspectRequest(BaseModel):
    package_path: str = Field(alias="packagePath")
    max_entries: int = Field(default=5000, alias="maxEntries", ge=1, le=50000)

    model_config = {"populate_by_name": True}


class OutfitImportPlanRequest(BaseModel):
    package_path: str = Field(alias="packagePath")
    project_path: str = Field(default="", alias="projectPath")
    target_folder: str = Field(default="", alias="targetFolder")
    selected_unitypackage: str = Field(default="", alias="selectedUnityPackage")
    selected_prefab: str = Field(default="", alias="selectedPrefab")
    base_avatar_name: str = Field(default="", alias="baseAvatarName")
    max_entries: int = Field(default=5000, alias="maxEntries", ge=1, le=50000)

    model_config = {"populate_by_name": True}


class PackageInstallDiagnosticsRequest(BaseModel):
    project_path: str = Field(default="", alias="projectPath")
    package_id: str = Field(default="", alias="packageId")
    stdout_summary: str = Field(default="", alias="stdoutSummary")
    stderr_summary: str = Field(default="", alias="stderrSummary")
    log_text: str = Field(default="", alias="logText")
    max_compile_errors: int = Field(default=30, alias="maxCompileErrors", ge=1, le=200)

    model_config = {"populate_by_name": True}


class PackageInstallPlanRequest(BaseModel):
    project_path: str = Field(default="", alias="projectPath")
    package_id: str = Field(default="", alias="packageId")
    repository: str = Field(default="", alias="repository")
    preferred_manager: str = Field(default="", alias="preferredManager")
    allow_agent_managed_download: bool = Field(default=False, alias="allowAgentManagedDownload")
    include_prerelease: bool = Field(default=False, alias="includePrerelease")
    package_version: str = Field(default="", alias="packageVersion")

    model_config = {"populate_by_name": True}


class SubAgentCreateRequest(BaseModel):
    role: str = Field(default="project_index_review")
    task: str = Field(default="")
    display_name: str = Field(default="", alias="displayName")
    parent_chat_id: str = Field(default="", alias="parentChatId")
    parent_session_id: str = Field(default="", alias="parentSessionId")
    project_path: str = Field(default="", alias="projectPath")
    params: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class SubAgentMergeRequest(BaseModel):
    decision: str = Field(default="adopted")
    chat_id: str = Field(default="", alias="chatId")
    expected_revision: int | None = Field(default=None, alias="expectedRevision")

    model_config = {"populate_by_name": True}


class SubAgentHandoffAckRequest(BaseModel):
    expected_revision: int | None = Field(default=None, alias="expectedRevision")

    model_config = {"populate_by_name": True}


class ProviderTestRequest(ApiConfigRequest):
    capability: Literal["text", "structured", "vision"] = "text"


class McpSelectionAcceptanceRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    visible_tools: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=64,
        alias="visibleTools",
    )
    exposure_layer: Literal["planning", "execution"] = Field(default="planning", alias="exposureLayer")

    model_config = {"populate_by_name": True}


class McpSelectionAcceptanceVerifyRequest(McpSelectionAcceptanceRequest):
    result: dict[str, Any]


class AgentCompactRequest(BaseModel):
    history: list[dict[str, Any]] = Field(default_factory=list)
    source_digest: str = Field(default="", alias="sourceDigest")
    trigger: Literal["manual", "auto"] = "manual"
    phase: Literal["standalone", "pre_turn", "mid_turn"] = "standalone"
    language: str = ""
    provider: str = ""
    model: str = ""
    target_tokens: int | None = Field(default=None, alias="targetTokens")
    real_context_limit: int | None = Field(default=None, alias="realContextLimit")

    model_config = {"populate_by_name": True}


def _current_provider_vision_main_config() -> VisionModelConfig:
    config = PROVIDER_CONFIGURATION.current_api_config()
    return VisionModelConfig(
        provider=config.provider,
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
    )


def _current_provider_vision_profile_config() -> VisionProfileConfig:
    config = PROVIDER_CONFIGURATION.current_vision_config()
    return VisionProfileConfig(
        provider=config.provider,
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        enabled=config.enabled,
    )


@dataclass
class DashboardState:
    settings_path: Path
    project_roots: list[Path] = field(default_factory=list)
    unity_editor_path: str = ""
    status_push_interval_seconds: float = 2.5
    selected_project_path: str = ""
    unity_host: str = "127.0.0.1"
    unity_port: int = 0
    unity_instance: str = ""


@dataclass
class DashboardRuntimeState:
    current_avatar_name: str = ""
    current_avatar_path: str = ""
    scene_avatars: list[dict[str, Any]] = field(default_factory=list)
    manual_undo_stack: dict[str, list[list[dict[str, Any]]]] = field(default_factory=dict)
    shader_undo_stack: dict[str, list[list[dict[str, Any]]]] = field(default_factory=dict)
    latest_parameter_snapshot_path: str = ""
    latest_screenshot_path: str = ""
    latest_screenshot_url: str = ""


class DashboardEventBus:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def send_to_client(self, websocket: WebSocket, event_type: str, payload: Any) -> None:
        try:
            await websocket.send_json(build_event_message(event_type, payload))
        except (WebSocketDisconnect, RuntimeError):
            await self.disconnect(websocket)
        except Exception as exc:  # noqa: BLE001 - stale websocket clients should not spam full stack traces.
            await self.disconnect(websocket)
            emit_log("warn", "socket", "Dropped stale websocket client.", {"error": str(exc)})

    async def broadcast(self, event_type: str, payload: Any) -> None:
        if not self._clients:
            return

        message = build_event_message(event_type, payload)
        stale_clients: list[WebSocket] = []
        for websocket in list(self._clients):
            try:
                await asyncio.wait_for(websocket.send_json(message), timeout=2.0)
            except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
                stale_clients.append(websocket)
            except Exception as exc:  # noqa: BLE001
                emit_log("warn", "socket", "Dropped stale websocket client during broadcast.", {"error": str(exc)})
                stale_clients.append(websocket)

        for websocket in stale_clients:
            self._clients.discard(websocket)

    def broadcast_from_sync(self, event_type: str, payload: Any) -> None:
        if self._loop is None or self._loop.is_closed():
            return

        asyncio.run_coroutine_threadsafe(self.broadcast(event_type, payload), self._loop)


class AgentMcpMount:
    def __init__(self) -> None:
        self.app = None

    async def __call__(self, scope, receive, send) -> None:
        if self.app is None:
            response = JSONResponse({"ok": False, "error": "Agent MCP app is not ready."}, status_code=503)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


app = FastAPI(title="VRCForge Dashboard", version=read_vrcforge_version())
# The Tauri desktop webview runs on a different origin (tauri://localhost /
# http://tauri.localhost in production, http://127.0.0.1:1420 in dev), so
# without CORS headers every fetch() to this loopback server is blocked by
# the webview and the app shows "核心未连接" with zero skills/projects.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://127.0.0.1:1420",
        "http://localhost:1420",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
app.mount("/artifacts", StaticFiles(directory=str(DASHBOARD_ARTIFACTS_DIR)), name="artifacts")
app.mount("/runtime-artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="runtime_artifacts")

EVENT_BUS = DashboardEventBus()
UVICORN_SERVER_LOCK = Lock()
CURRENT_UVICORN_SERVER: uvicorn.Server | None = None
DIAGNOSTIC_PRIVACY = DiagnosticPrivacy(CONFIG_DIR)
DIAGNOSTIC_LOGGER = DiagnosticLogManager(LOG_DIR, DIAGNOSTICS_CONFIG_PATH, DIAGNOSTIC_PRIVACY)
DEVELOPER_OPTIONS_GUARD = DeveloperOptionsGuard()
RECENT_LOGS = DIAGNOSTIC_LOGGER.recent_entries
LOCAL_LOG_LOCK = DIAGNOSTIC_LOGGER.lock
ADVANCED_SETTINGS_TRANSITION_LOCK = Lock()
TUNING_STORE_LOCK = Lock()
UNITY_MCP_REPAIR_LOCK = Lock()
SKILL_PACKAGE_WRITE_LOCK = Lock()
_SKILL_PACKAGE_CONTROLLER = SkillPackageController(sys.modules[__name__])
_SKILL_PACKAGE_GOVERNANCE = SkillPackageGovernanceService(sys.modules[__name__])
_SKILL_PACKAGE_PROJECTION = SkillPackageProjectionService(sys.modules[__name__])
_PATH_TO_SKILL_CONTROLLER = PathToSkillDashboardController(sys.modules[__name__])
_PROJECT_CATALOG_DISCOVERY = ProjectCatalogDiscovery(sys.modules[__name__])
PROVIDER_MODEL_CATALOG = ProviderModelCatalogService(
    ProviderModelCatalogPolicyPorts(
        validate_provider_api_key=validate_provider_api_key,
        provider_requires_api_key=provider_requires_api_key,
        provider_display_name=provider_display_name,
        provider_model_descriptor=provider_model_descriptor,
        resolve_vertex_project_location=lambda value: resolve_vertex_project_location(
            value
        ),
    )
)
PROVIDER_CONFIGURATION = ProviderConfigurationService(
    ProviderConfigurationPersistencePorts(
        config_path=CONFIG_PATH,
        load_runtime_settings=lambda: load_runtime_settings_safely(
            RUNTIME_SETTINGS_PATH,
            loader=load_settings,
        ),
        atomic_write_json=lambda path, payload: atomic_write_json(path, payload),
        path_is_reparse_or_link=lambda path: _path_is_reparse_or_link(path),
    ),
    ProviderConfigurationPolicyPorts(
        default_provider=DEFAULT_LLM_PROVIDER,
        normalize_provider_name=normalize_provider_name,
        get_provider_defaults=get_provider_defaults,
        normalize_base_url=normalize_base_url,
        normalize_provider_api_type=normalize_provider_api_type,
        normalize_reasoning_effort=normalize_reasoning_effort,
        reasoning_effort_variants=reasoning_effort_variants,
        validate_provider_api_key=validate_provider_api_key,
        provider_display_name=provider_display_name,
        provider_auth_label=lambda provider: provider_auth_label(provider),
        provider_requires_api_key=provider_requires_api_key,
        provider_config_descriptor=PROVIDER_MODEL_CATALOG.provider_config_descriptor,
    ),
    CONFIG_DOCUMENT_LOCK,
)
PROVIDER_TEXT_PROBE = ProviderTextProbeRunner(
    ProviderProbePolicyPorts(
        validate_provider_api_key=validate_provider_api_key,
        normalize_provider_api_type=normalize_provider_api_type,
        resolve_vertex_project_location=lambda value: resolve_vertex_project_location(
            value
        ),
        build_gemini_generate_config=build_gemini_generate_config,
        build_anthropic_request_payload=build_anthropic_request_payload,
        build_openai_compatible_request_payload=(
            build_openai_compatible_request_payload
        ),
        model_rejects_fixed_temperature=model_rejects_fixed_temperature,
        settings_factory=Settings,
        runtime_request_factory=ProviderRuntimeRequest,
    )
)
PROVIDER_TESTS = ProviderTestIntegrationService(
    ProviderTestServicePorts(
        resolve_api_request=PROVIDER_CONFIGURATION.resolve_api_request,
        normalize_provider_name=normalize_provider_name,
        provider_display_name=provider_display_name,
        provider_config_descriptor=PROVIDER_MODEL_CATALOG.provider_config_descriptor,
        provider_requires_api_key=provider_requires_api_key,
        extract_json_block=extract_json_block,
    ),
    PROVIDER_TEXT_PROBE,
)
PROVIDER_VISION_POLICY = ProviderVisionPolicyPorts(
    normalize_provider_name=normalize_provider_name,
    provider_requires_api_key=provider_requires_api_key,
    provider_display_name=provider_display_name,
    validate_provider_api_key=validate_provider_api_key,
    resolve_vertex_project_location=lambda value: resolve_vertex_project_location(value),
    model_rejects_fixed_temperature=model_rejects_fixed_temperature,
)
PROVIDER_VISION = ProviderVisionService(
    ProviderVisionStatePorts(
        main_config=_current_provider_vision_main_config,
        profile_config=_current_provider_vision_profile_config,
    ),
    PROVIDER_VISION_POLICY,
    ProviderVisionSdkRunner(PROVIDER_VISION_POLICY),
)
_PROJECT_SNAPSHOT_SELECTION = ProjectSnapshotSelectionService(
    ProjectSnapshotSelectionPorts(
        build_snapshot=lambda: build_project_snapshot_payload(),
        selected_project_path=lambda: DASHBOARD_STATE.selected_project_path,
        unity_editor_path=lambda: DASHBOARD_STATE.unity_editor_path,
        normalize_path=lambda value: normalize_path_string(value),
        is_unity_project_path=lambda path: is_unity_project_path(path),
        atomic_write_json=lambda path, payload: atomic_write_json(path, payload),
        utc_now_iso=lambda: utc_now_iso(),
        broadcast_projects=lambda payload: EVENT_BUS.broadcast_from_sync("projects", payload),
    ),
    cache_path=USER_DATA_DIR / "project-cache.json",
    selection_path=CONFIG_DIR / "selected-project.json",
    selection_schema=PROJECT_SELECTION_SCHEMA,
)
# STOPGAP: Migration-only owner for the three root compatibility facades below.
# Remove it in the final 1.5 typed-composition seam-retirement gate.
_UNITY_STATUS = UnityStatusService(
    UnityStatusPorts(
        load_settings=lambda: load_dashboard_settings(ConnectionRequest(settings_path=str(DASHBOARD_STATE.settings_path))),
        selected_project_path=lambda: DASHBOARD_STATE.selected_project_path,
        normalize_path=lambda value: normalize_path_string(value),
        core_installed=lambda project_root: vrcforge_mcp_core_installed(project_root),
        required_tools=tuple(REQUIRED_VRCFORGE_UNITY_TOOLS),
    )
)
# STOPGAP: Migration-only owner for the root Doctor report facade below.
# Remove it in the final 1.5 typed-composition seam-retirement gate.
_DOCTOR_READINESS_REPORT = DoctorReadinessReportService(
    DoctorReadinessReportPorts(
        build_health=lambda: build_agentic_app_health(),
        serialize_api_config=PROVIDER_CONFIGURATION.serialize_app_api_config,
        safe_agent_health=lambda: safe_agent_health(),
        safe_agent_manifest=lambda: safe_agent_manifest(),
        safe_permission_state=lambda: safe_permission_state(),
        selected_project_path_from_health=lambda health: _selected_project_path_from_health(health),
        doctor_check=lambda *args, **kwargs: _doctor_check(*args, **kwargs),
        doctor_check_from_component=lambda *args, **kwargs: _doctor_check_from_component(*args, **kwargs),
        package_doctor_check=lambda *args, **kwargs: _package_doctor_check(*args, **kwargs),
        status_from_counts=lambda errors, warnings: _status_from_counts(errors, warnings),
        check_skill_registry=lambda: AGENT_GATEWAY.check_skill_registry(),
        list_checkpoints=lambda params: AGENT_GATEWAY.list_checkpoints(params),
        checkpoint_paths=lambda: (str(AGENT_GATEWAY.checkpoint_log_path), str(AGENT_GATEWAY.checkpoint_store_dir)),
        package_manager_status=lambda params: PACKAGE_INSTALL_WORKFLOWS.package_manager_status(params),
        merge_registered_checks=lambda checks: _merge_registered_doctor_checks(checks),
        doctor_summary=lambda checks: _doctor_summary(checks),
        doctor_sections=lambda checks: _doctor_sections(checks),
        redact_local_path=lambda value: _redact_local_path(value),
        version=lambda: app.version,
    )
)
# STOPGAP: Migration-only owner for the root Know Yourself compatibility facade below.
# Remove it in the final 1.5 typed-composition seam-retirement gate.
_KNOW_YOURSELF_READINESS = KnowYourselfReadinessService(
    KnowYourselfReadinessPorts(
        load_settings_for_params=lambda params: load_dashboard_settings(build_agent_connection_request(params)),
        build_unity_status=lambda settings: build_unity_status_snapshot(settings),
        build_doctor_report=lambda: build_app_doctor_report(),
        selected_project_path=lambda: DASHBOARD_STATE.selected_project_path,
        unity_editor_path=lambda: DASHBOARD_STATE.unity_editor_path,
        parse_editor_version=lambda version_file: parse_editor_version(version_file),
        list_running_unity_processes_strict=lambda: list_running_unity_processes(require_discovery_evidence=True),
        process_matches_project=lambda process, project_root: unity_process_exactly_matches_project(process, project_root),
        read_compile_errors=lambda params: read_agent_compile_errors(params),
        normalize_path=lambda value: normalize_path_string(value),
        build_tool_registry=lambda: AGENT_GATEWAY.build_tool_registry(),
        build_skill_registry=lambda: AGENT_GATEWAY.build_skill_registry(),
        permission_state=lambda: AGENT_GATEWAY.permission_state(),
        ensure_dict=lambda value: ensure_dict(value),
        normalize_bool=lambda value, default: normalize_bool(value, default),
    )
)
CURRENT_UNITY_STATUS: dict[str, Any] | None = None
LAST_STATUS_FINGERPRINT = ""
LAST_STATUS_CONNECTED: bool | None = None
STATUS_MONITOR_TASK: asyncio.Task[None] | None = None
BACKGROUND_GOAL_MONITOR_TASK: asyncio.Task[None] | None = None
BACKGROUND_GOAL_WAKE_DRAIN_TASKS: set[asyncio.Task[None]] = set()
AGENT_MCP_INIT_TASK: asyncio.Task[None] | None = None
DASHBOARD_STATE: DashboardState | None = None
DASHBOARD_RUNTIME = DashboardRuntimeState()
AGENT_GATEWAY = AgentGateway(
    config_path=AGENT_GATEWAY_CONFIG_PATH,
    audit_dir=AGENT_GATEWAY_AUDIT_DIR,
    desktop_capture_dir=AGENT_GATEWAY_AUDIT_DIR / "desktop-captures",
    desktop_actions_changed=lambda: EVENT_BUS.broadcast_from_sync(
        "agentDesktopActions",
        {"changed": True},
    ),
)
AGENT_GOALS: AgentGoalService = AGENT_GATEWAY.goal
# STOPGAP(1.5): this app-lifetime owner shares the established durable state
# lock and Goal resolver without a Gateway forwarding layer. The final typed
# app composition must inject it into routes/tools and remove this root symbol.
AGENT_QUESTIONS = AgentQuestionService(
    AgentQuestionPersistence(
        AgentQuestionPersistencePorts(
            log_path=lambda: AGENT_GATEWAY.audit_dir / "agent-questions.jsonl",
            shared_state_lock=AGENT_GATEWAY._lock,
            redact=redact_sensitive,
        )
    ),
    AgentQuestionScopePorts(
        normalize_path=normalize_filesystem_path,
        summarize=summarize_text,
        redact_goal_persistence=redact_background_goal_persistence,
    ),
    GoalQuestionResolutionPort(
        resolve=lambda question_id, continuation_prompt: AGENT_GOALS.resolve_agent_goal_question(
            question_id,
            continuation_prompt=continuation_prompt,
        )
    ),
)
# 1.5 strangler seam: keep the existing facade globals authoritative until
# each consumer migrates. Late-bound providers preserve test/host monkeypatches
# and prevent an extracted domain from accidentally creating a second runtime.
DASHBOARD_COMPOSITION_CONTEXT = DashboardCompositionContext(
    dashboard_state=lambda: DASHBOARD_STATE,
    runtime_state=lambda: DASHBOARD_RUNTIME,
    event_bus=lambda: EVENT_BUS,
    agent_gateway=lambda: AGENT_GATEWAY,
)
RUNTIME_LANE_BUDGET = RuntimeLaneBudget()
BACKGROUND_GOAL_PREFLIGHT = ProviderPreflightCache(
    lambda provider, base_url: probe_background_goal_provider(provider, base_url)
)


async def broadcast_background_goal_state(_state: dict[str, Any]) -> None:
    await EVENT_BUS.broadcast("agentGoals", AGENT_GOALS.list_agent_goals())
    await EVENT_BUS.broadcast("agentGoalBackground", AGENT_GOALS.agent_goal_background_state())


BACKGROUND_GOAL_COORDINATOR = BackgroundGoalDeliveryCoordinator(
    goal=AGENT_GOALS,
    approval=AGENT_GOALS,
    runtime=RuntimeExecutionPort(
        execute=AGENT_GATEWAY.runtime_message,
        request_cancel=AGENT_GATEWAY.request_runtime_cancel,
    ),
    events=GoalEventPort(state_changed=broadcast_background_goal_state),
    lifecycle=GoalLifecyclePort(
        lane_budget=RUNTIME_LANE_BUDGET,
        preflight=BACKGROUND_GOAL_PREFLIGHT,
    ),
)


async def drain_timed_out_goal_wake(worker: asyncio.Task[dict[str, Any]]) -> None:
    try:
        payload = await worker
    except BaseException:
        return
    delivery = ensure_dict(payload.get("delivery")) if isinstance(payload, dict) else {}
    delivery_id = str(delivery.get("deliveryId") or "")
    if not delivery_id:
        return
    try:
        state = await asyncio.to_thread(
            AGENT_GOALS.defer_agent_goal_delivery_wake_timeout,
            delivery_id,
        )
        await broadcast_background_goal_state(state)
    except Exception as exc:  # noqa: BLE001 - startup recovery remains the final fallback.
        emit_log("warn", "agent", "Timed-out goal wake drain had a warning.", {"error": str(exc)})


def track_timed_out_goal_wake(worker: asyncio.Task[dict[str, Any]]) -> None:
    drain_task = asyncio.create_task(drain_timed_out_goal_wake(worker))
    BACKGROUND_GOAL_WAKE_DRAIN_TASKS.add(drain_task)
    drain_task.add_done_callback(BACKGROUND_GOAL_WAKE_DRAIN_TASKS.discard)
BACKEND_OWNER_LEASE = BackendOwnerLease(lambda: AGENT_GATEWAY.audit_dir / "backend-owner.lock")
# 子代理角色与执行全部由 sub_agent_delegate 域模块提供：
# 统一经 AGENT_GATEWAY.execute_runtime_skill 的 allowlist 路径分发，
# 组合根只负责把 gateway 绑进去。
# STOPGAP: this composition singleton is removed with all remaining 1.5 seams.
# The service itself exclusively owns the durable registry, handlers and workers.
_SUB_AGENT_COLLABORATION = SubAgentCollaborationService(
    SubAgentCollaborationPorts(
        artifact_dir=SUB_AGENT_TASK_DIR,
        gateway=AGENT_GATEWAY,
        lane_budget=RUNTIME_LANE_BUDGET,
        build_roles=build_sub_agent_roles,
        build_handlers=build_sub_agent_role_handlers,
    )
)
AGENT_MCP_MOUNT = AgentMcpMount()
AGENT_MCP_APP = None
AGENT_MCP_CONTEXT = None


def _notify_mcp_pending_approval(_approval: dict[str, Any]) -> None:
    """Schedule the narrow external-MCP pending-approval UI refresh."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()}))


async def initialize_agent_mcp_mount() -> None:
    global AGENT_MCP_APP
    global AGENT_MCP_CONTEXT

    try:
        app_payload = create_agent_mcp_app(AGENT_GATEWAY, on_pending_approval=_notify_mcp_pending_approval)
        AGENT_MCP_APP = app_payload
        AGENT_MCP_CONTEXT = None
        AGENT_MCP_MOUNT.app = app_payload
        emit_log("info", "agent", "Agent MCP app initialized.", {"mcpPath": "/mcp"})
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - external MCP must not block the desktop agent.
        AGENT_MCP_APP = None
        AGENT_MCP_CONTEXT = None
        AGENT_MCP_MOUNT.app = None
        emit_log("warn", "agent", "Agent MCP app failed to initialize; desktop normal-agent mode remains available.", {"error": str(exc)})
    finally:
        AGENT_MCP_CONTEXT = None
        AGENT_MCP_APP = None
        AGENT_MCP_MOUNT.app = None


@app.middleware("http")
async def authorize_local_requests(request: Request, call_next):
    started_at = time.perf_counter()
    status_code = 500
    error_message = ""
    is_preflight = is_cors_preflight_request(request)
    transport_component = request_transport_component(request)
    if not is_preflight and (request.url.path == "/mcp" or request.url.path.startswith("/mcp/")):
        try:
            authenticate_agent_request(request, allow_disabled=False)
        except HTTPException as exc:
            status_code = exc.status_code
            record_debug_interaction(
                {
                    "kind": "http",
                    "direction": "inbound",
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "durationMs": round((time.perf_counter() - started_at) * 1000, 2),
                    "error": str(exc.detail),
                    "client": request.client.host if request.client else "",
                },
                component=transport_component,
            )
            return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
    if not is_preflight and artifact_route_requires_auth(request):
        try:
            authenticate_artifact_request(request)
        except HTTPException as exc:
            status_code = exc.status_code
            record_debug_interaction(
                {
                    "kind": "http",
                    "direction": "inbound",
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "durationMs": round((time.perf_counter() - started_at) * 1000, 2),
                    "error": str(exc.detail),
                    "client": request.client.host if request.client else "",
                },
                component=transport_component,
            )
            return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
    if not is_preflight and app_route_requires_auth(request):
        try:
            authenticate_app_request(request)
        except HTTPException as exc:
            status_code = exc.status_code
            record_debug_interaction(
                {
                    "kind": "http",
                    "direction": "inbound",
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "durationMs": round((time.perf_counter() - started_at) * 1000, 2),
                    "error": str(exc.detail),
                    "client": request.client.host if request.client else "",
                },
                component=transport_component,
            )
            return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as exc:  # noqa: BLE001
        error_message = str(exc)
        raise
    finally:
        if request.url.path.startswith("/api/") or request.url.path == "/mcp" or request.url.path.startswith("/mcp/"):
            record_debug_interaction(
                {
                    "kind": "http",
                    "direction": "inbound",
                    "method": request.method,
                    "path": request.url.path,
                    "query": dict(request.query_params),
                    "status": status_code,
                    "durationMs": round((time.perf_counter() - started_at) * 1000, 2),
                    "error": error_message,
                    "client": request.client.host if request.client else "",
                },
                component=transport_component,
            )


@app.on_event("startup")
async def on_startup() -> None:
    global STATUS_MONITOR_TASK
    global BACKGROUND_GOAL_MONITOR_TASK
    global AGENT_MCP_INIT_TASK

    EVENT_BUS.set_loop(asyncio.get_running_loop())
    AGENT_GATEWAY.shell.start()
    BACKGROUND_GOAL_COORDINATOR.start()
    load_project_snapshot_cache()
    await asyncio.to_thread(DIAGNOSTIC_LOGGER.cleanup)
    await asyncio.to_thread(reconcile_diagnostic_trace_policy)
    await emit_safety_posture_snapshot("startup")
    if BACKEND_OWNER_LEASE.owned:
        try:
            await asyncio.to_thread(_SUB_AGENT_COLLABORATION.reconcile_startup, refresh_from_disk=True)
        except Exception as exc:  # noqa: BLE001 - optional user-data recovery must not block startup.
            emit_log("warn", "subagent", "Sub-agent startup reconciliation had a warning.", {"error": str(exc)})
        try:
            await asyncio.to_thread(AGENT_GOALS.reconcile_stale_agent_goal_deliveries)
            await asyncio.to_thread(
                AGENT_GOALS.reconcile_agent_goal_watchdogs,
                finalize_orphans=True,
            )
        except Exception as exc:  # noqa: BLE001 - optional user-data recovery must not block startup.
            emit_log("warn", "agent", "Goal delivery startup reconciliation had a warning.", {"error": str(exc)})
        try:
            await MEMORY_REVIEW.host.reconcile_startup(MEMORY_REVIEW.authorized_project_roots())
            await asyncio.to_thread(MEMORY_REVIEW.service.ensure_automatic_capture_watermark)
        except Exception:  # noqa: BLE001 - review recovery remains isolated from core startup.
            emit_log("warn", "agent", "Memory Review startup reconciliation had a bounded warning.", {"failureClass": "reconcile"})
    if AGENT_GATEWAY.desktop.embedded_worker_enabled():
        try:
            await asyncio.to_thread(AGENT_GATEWAY.desktop.start_embedded_worker)
        except Exception as exc:  # noqa: BLE001 - desktop control must not block core startup.
            emit_log("warn", "desktop", "Embedded desktop executor failed to start.", {"error": str(exc)})
    if AGENT_MCP_INIT_TASK is None or AGENT_MCP_INIT_TASK.done():
        AGENT_MCP_INIT_TASK = asyncio.create_task(initialize_agent_mcp_mount())
    if not PROVIDER_CONFIGURATION.config_path.exists():
        PROVIDER_CONFIGURATION.save_api_config(
            PROVIDER_CONFIGURATION.current_api_config()
        )
    if STATUS_MONITOR_TASK is None or STATUS_MONITOR_TASK.done():
        STATUS_MONITOR_TASK = asyncio.create_task(status_monitor_loop())
    if BACKGROUND_GOAL_MONITOR_TASK is None or BACKGROUND_GOAL_MONITOR_TASK.done():
        BACKGROUND_GOAL_MONITOR_TASK = asyncio.create_task(background_goal_monitor_loop())

    await emit_log_async(
        "info",
        "dashboard",
        "Dashboard server started.",
        {
            "projectRoots": [str(path) for path in DASHBOARD_STATE.project_roots],
            "unityEditorPath": DASHBOARD_STATE.unity_editor_path,
            "provider": PROVIDER_CONFIGURATION.current_api_config().provider,
            "model": PROVIDER_CONFIGURATION.current_api_config().model,
        },
    )


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global STATUS_MONITOR_TASK
    global BACKGROUND_GOAL_MONITOR_TASK
    global AGENT_MCP_INIT_TASK
    global AGENT_MCP_APP
    global AGENT_MCP_CONTEXT

    await emit_safety_posture_snapshot("normal_shutdown")

    try:
        shell_shutdown = await asyncio.to_thread(AGENT_GATEWAY.shell.shutdown)
        if shell_shutdown.pending_count:
            emit_log(
                "warn",
                "agent",
                "Shell process shutdown reached its bounded deadline.",
                {
                    "processSnapshotCount": shell_shutdown.snapshot_count,
                    "terminatedProcessCount": shell_shutdown.terminated_count,
                    "pendingProcessCount": shell_shutdown.pending_count,
                },
            )
    except Exception as exc:  # noqa: BLE001 - shutdown remains best-effort after stopping admission.
        emit_log(
            "warn",
            "agent",
            "Shell process shutdown had a warning.",
            {"error": str(exc)},
        )

    live_session = PRIMITIVE_BASIS_LIVE_SESSION
    if live_session is not None:
        try:
            live_session.close()
        finally:
            install_primitive_basis_live_runtime(None)

    try:
        await asyncio.to_thread(AGENT_GATEWAY.desktop.stop_embedded_worker)
    except Exception as exc:  # noqa: BLE001 - backend shutdown must remain best-effort.
        emit_log("warn", "desktop", "Embedded desktop executor shutdown had a warning.", {"error": str(exc)})

    if AGENT_MCP_INIT_TASK is not None and not AGENT_MCP_INIT_TASK.done():
        AGENT_MCP_INIT_TASK.cancel()
        try:
            await AGENT_MCP_INIT_TASK
        except asyncio.CancelledError:
            pass
    AGENT_MCP_INIT_TASK = None
    if STATUS_MONITOR_TASK is not None:
        STATUS_MONITOR_TASK.cancel()
        try:
            await STATUS_MONITOR_TASK
        except asyncio.CancelledError:
            pass
        STATUS_MONITOR_TASK = None
    try:
        goal_shutdown = await BACKGROUND_GOAL_COORDINATOR.shutdown()
        if goal_shutdown.pending_count:
            emit_log(
                "warn",
                "agent",
                "Background goal drain shutdown reached its bounded deadline.",
                {
                    "drainSnapshotCount": goal_shutdown.snapshot_count,
                    "pendingDrainCount": goal_shutdown.pending_count,
                },
            )
    except Exception as exc:  # noqa: BLE001 - shutdown remains best-effort after stopping admission.
        emit_log(
            "warn",
            "agent",
            "Background goal drain shutdown had a warning.",
            {"error": str(exc)},
        )
    if BACKGROUND_GOAL_MONITOR_TASK is not None:
        BACKGROUND_GOAL_MONITOR_TASK.cancel()
        try:
            await BACKGROUND_GOAL_MONITOR_TASK
        except asyncio.CancelledError:
            pass
        BACKGROUND_GOAL_MONITOR_TASK = None
    await MEMORY_REVIEW.host.shutdown()
    wake_drains = list(BACKGROUND_GOAL_WAKE_DRAIN_TASKS)
    for task in wake_drains:
        task.cancel()
    if wake_drains:
        await asyncio.gather(*wake_drains, return_exceptions=True)
    BACKGROUND_GOAL_WAKE_DRAIN_TASKS.clear()
    AGENT_MCP_CONTEXT = None
    AGENT_MCP_MOUNT.app = None
    AGENT_MCP_APP = None


@app.get("/")
def read_dashboard() -> FileResponse:
    response = FileResponse(DASHBOARD_DIR / "index.html")
    attach_dashboard_session_cookie(response)
    return response


@app.get("/dashboard")
@app.get("/dashboard/")
def read_dashboard_alias() -> FileResponse:
    return read_dashboard()


def build_public_health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "schema": "vrcforge.public_health.v1",
        "version": app.version,
        "portableMode": PORTABLE_MODE,
        "authRequired": APP_AUTH_REQUIRED,
    }


def health_request_has_app_auth(request: Request) -> bool:
    if not APP_AUTH_REQUIRED:
        return True
    try:
        authenticate_app_request(request)
    except HTTPException:
        return False
    return True


def build_full_health_payload() -> dict[str, Any]:
    settings = load_runtime_settings_safely(
        RUNTIME_SETTINGS_PATH,
        llm_override=PROVIDER_CONFIGURATION.serialize_api_config(include_secret=True),
        loader=load_settings,
    )
    components = build_health_components(settings)
    return {
        "ok": not any(component["status"] == "error" for component in components.values()),
        "version": app.version,
        "portableMode": PORTABLE_MODE,
        "projectRoot": str(ROOT_DIR),
        "settingsPath": str(RUNTIME_SETTINGS_PATH),
        "configPath": str(CONFIG_PATH),
        "paths": {
            "programDir": str(ROOT_DIR),
            "userDataDir": str(USER_DATA_DIR),
            "configDir": str(CONFIG_DIR),
            "logsDir": str(LOG_DIR),
            "artifactsDir": str(ARTIFACTS_DIR),
            "dashboardDir": str(DASHBOARD_DIR),
        },
        "components": components,
        "defaults": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "baseUrl": settings.llm_base_url,
            "sourceMode": "unity_live_export",
            "exportJson": str(DEFAULT_MVP_EXPORT_PATH),
            "planJson": "",
            "mockExecute": False,
            "minConfidence": settings.min_confidence,
            "unityHost": DASHBOARD_STATE.unity_host,
            "unityPort": DASHBOARD_STATE.unity_port,
            "unityInstance": DASHBOARD_STATE.unity_instance,
        },
        "state": serialize_dashboard_state(),
        "apiConfig": PROVIDER_CONFIGURATION.serialize_api_config(include_secret=False),
        "projects": project_snapshot_payload(use_cache=True, refresh_async=False),
        "logRetentionHours": int(LOG_RETENTION.total_seconds() // 3600),
        "unityStatus": CURRENT_UNITY_STATUS,
    }


@app.get("/api/health")
def read_health(request: Request) -> dict[str, Any]:
    if not health_request_has_app_auth(request):
        return build_public_health_payload()
    return build_full_health_payload()


@app.get("/api/app/session")
def read_app_session(request: Request) -> dict[str, Any]:
    validate_app_session_handshake_request(request, dev_only=True)
    return {
        "ok": True,
        "authRequired": APP_AUTH_REQUIRED,
        "appSessionToken": APP_SESSION_TOKEN,
    }


@app.get("/api/app/session-challenge")
def read_app_session_challenge(request: Request, nonce: str = "") -> dict[str, Any]:
    validate_app_session_handshake_request(request, dev_only=False)
    nonce_value = normalize_app_session_challenge_nonce(nonce)
    return {
        "ok": True,
        "schema": "vrcforge.app_session_challenge.v1",
        "signature": app_session_challenge_signature(nonce_value),
    }


@app.post(APP_INTERNAL_SHUTDOWN_PATH, status_code=202)
def request_internal_runtime_shutdown(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    supplied_token = extract_bearer_token(request)
    if (
        not APP_SESSION_TOKEN
        or not supplied_token
        or not hmac.compare_digest(supplied_token, APP_SESSION_TOKEN)
    ):
        raise HTTPException(status_code=401, detail="App session token is missing or invalid.")
    if request_transport_component(request) != "ipc":
        raise HTTPException(status_code=403, detail="Runtime shutdown requires a valid Tauri bridge request proof.")
    server = current_owned_uvicorn_server()
    if server is None:
        raise HTTPException(status_code=503, detail="The managed runtime server is unavailable.")
    # Starlette executes response background tasks only after the response body
    # has been sent, so the shell can receive the acknowledgement before the
    # owned server begins its normal shutdown lifecycle.
    background_tasks.add_task(signal_owned_uvicorn_server_exit, server)
    return {
        "ok": True,
        "schema": "vrcforge.runtime_shutdown.v1",
        "scheduled": True,
    }


@app.get("/api/app/bootstrap")
def read_agentic_app_bootstrap(refreshProjects: bool = False) -> dict[str, Any]:  # noqa: N803 - query param is camelCase for the app API.
    return build_agentic_app_bootstrap_payload(refresh_projects=refreshProjects)


def build_agentic_app_bootstrap_payload(*, refresh_projects: bool = False) -> dict[str, Any]:
    return {
        "ok": True,
        "app": {
            "name": "VRCForge",
            "version": app.version,
            "surface": "tauri-agentic-desktop",
            "browserRequired": False,
            "legacyDashboardDebugOnly": True,
        },
        "health": build_bootstrap_app_health(refresh_projects=refresh_projects),
        "apiConfig": PROVIDER_CONFIGURATION.serialize_app_api_config(),
        "visionConfig": PROVIDER_CONFIGURATION.serialize_app_vision_config(),
        "agentManifest": safe_agent_manifest(),
        "agentHealth": safe_agent_health(),
        "permission": safe_permission_state(),
        "advancedSettings": AGENT_GATEWAY.advanced_settings_state(),
        # Pending writes form one App-wide inbox. Each decision still rechecks
        # the approval's own exact projectRoot before execution.
        "approvals": safe_approval_list(),
    }


def run_workspace_git(root: Path, args: list[str], timeout_seconds: int = 10) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": "git executable was not found.", "returncode": 127}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "git command timed out.", "returncode": 124}
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "returncode": proc.returncode,
    }


def parse_workspace_numstat(stdout: str) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        additions_raw, deletions_raw, path = parts[0], parts[1], "\t".join(parts[2:]).strip()
        binary = additions_raw == "-" or deletions_raw == "-"
        additions = 0 if binary else int(additions_raw or "0")
        deletions = 0 if binary else int(deletions_raw or "0")
        stats[path] = {"additions": additions, "deletions": deletions, "binary": binary}
    return stats


WORKSPACE_DIFF_PATCH_MAX_CHARS = 40000


def build_workspace_diff_summary(root: str = "", include_patch: bool = False) -> dict[str, Any]:
    requested_root = Path(root).expanduser() if root.strip() else ROOT_DIR
    try:
        requested_root = requested_root.resolve()
    except OSError as exc:
        return {
            "ok": False,
            "schema": "vrcforge.workspace_diff.v1",
            "requestedRoot": str(requested_root),
            "status": "missing",
            "fileCount": 0,
            "additions": 0,
            "deletions": 0,
            "files": [],
            "statusLines": [],
            "error": str(exc),
        }
    if not requested_root.exists():
        return {
            "ok": False,
            "schema": "vrcforge.workspace_diff.v1",
            "requestedRoot": str(requested_root),
            "status": "missing",
            "fileCount": 0,
            "additions": 0,
            "deletions": 0,
            "files": [],
            "statusLines": [],
            "error": "workspace root does not exist.",
        }

    rev_parse = run_workspace_git(requested_root, ["rev-parse", "--show-toplevel"])
    if not rev_parse["ok"]:
        return {
            "ok": False,
            "schema": "vrcforge.workspace_diff.v1",
            "requestedRoot": str(requested_root),
            "status": "not_git",
            "fileCount": 0,
            "additions": 0,
            "deletions": 0,
            "files": [],
            "statusLines": [],
            "error": (rev_parse.get("stderr") or "workspace is not a git repository.").strip(),
        }

    git_root = Path(rev_parse["stdout"].strip()).resolve()
    status = run_workspace_git(git_root, ["status", "--short"])
    numstat = run_workspace_git(git_root, ["diff", "--numstat", "HEAD"])
    shortstat = run_workspace_git(git_root, ["diff", "--shortstat", "HEAD"])
    branch = run_workspace_git(git_root, ["branch", "--show-current"])
    patch = run_workspace_git(git_root, ["diff", "--patch", "--stat", "HEAD"], timeout_seconds=15) if include_patch else {"ok": True, "stdout": "", "stderr": ""}
    if not status["ok"]:
        return {
            "ok": False,
            "schema": "vrcforge.workspace_diff.v1",
            "requestedRoot": str(requested_root),
            "gitRoot": str(git_root),
            "status": "error",
            "fileCount": 0,
            "additions": 0,
            "deletions": 0,
            "files": [],
            "statusLines": [],
            "error": (status.get("stderr") or "git status failed.").strip(),
        }

    numstat_by_path = parse_workspace_numstat(numstat["stdout"] if numstat["ok"] else "")
    files: list[dict[str, Any]] = []
    additions = 0
    deletions = 0
    for raw in [line for line in status["stdout"].splitlines() if line.strip()]:
        status_code = raw[:2].strip() or raw[:2]
        path = raw[3:].strip() if len(raw) > 3 else raw.strip()
        lookup_path = path.split(" -> ")[-1].strip()
        path_stats = numstat_by_path.get(lookup_path, {})
        file_additions = int(path_stats.get("additions") or 0)
        file_deletions = int(path_stats.get("deletions") or 0)
        additions += file_additions
        deletions += file_deletions
        files.append(
            {
                "status": status_code,
                "path": path,
                "raw": raw,
                "additions": file_additions,
                "deletions": file_deletions,
                "binary": bool(path_stats.get("binary")),
            }
        )

    patch_text = patch["stdout"] if include_patch and patch["ok"] else ""
    patch_truncated = len(patch_text) > WORKSPACE_DIFF_PATCH_MAX_CHARS
    if patch_truncated:
        patch_text = patch_text[:WORKSPACE_DIFF_PATCH_MAX_CHARS] + "\n\n[diff truncated]"

    return {
        "ok": True,
        "schema": "vrcforge.workspace_diff.v1",
        "requestedRoot": str(requested_root),
        "gitRoot": str(git_root),
        "branch": branch["stdout"].strip() if branch["ok"] else "",
        "status": "changed" if files else "clean",
        "fileCount": len(files),
        "additions": additions,
        "deletions": deletions,
        "files": files,
        "statusLines": [line for line in status["stdout"].splitlines() if line.strip()],
        "shortstat": shortstat["stdout"].strip() if shortstat["ok"] else "",
        "patch": patch_text,
        "patchTruncated": patch_truncated,
        "error": "" if numstat["ok"] and patch["ok"] else ((numstat.get("stderr") or "") + "\n" + (patch.get("stderr") or "")).strip(),
    }


@app.get("/api/app/workspace/diff")
def read_workspace_diff(root: str = "", includePatch: bool = False) -> dict[str, Any]:
    return build_workspace_diff_summary(root, include_patch=includePatch)


@app.get("/api/app/runtime/snapshot")
def read_app_runtime_snapshot(
    sessionId: str = "",
    projectRoot: str = "",
    includePatch: bool = False,
    globalOnly: bool = False,
) -> dict[str, Any]:
    scoped_ledgers = bool(str(sessionId or "").strip() or str(projectRoot or "").strip())
    workspace_diff = build_workspace_diff_summary(projectRoot, include_patch=includePatch)
    if scoped_ledgers:
        runs = AGENT_GATEWAY.list_runtime_runs(limit=40, session_id=sessionId, project_root=projectRoot)
        desktop_actions = AGENT_GATEWAY.list_desktop_actions(limit=8, session_id=sessionId, project_root=projectRoot)
        goals = AGENT_GOALS.list_agent_goals(limit=8, session_id=sessionId, project_root=projectRoot)
        progress = AGENT_GATEWAY.list_agent_progress(limit=12, session_id=sessionId, project_root=projectRoot)
        questions = AGENT_QUESTIONS.list(limit=6, session_id=sessionId, project_root=projectRoot)
        memory = AGENT_GATEWAY.list_agent_memory(limit=8, project_root=projectRoot)
    else:
        runs = {"ok": True, "schema": "vrcforge.runtime_runs.v1", "runs": [], "events": [], "count": 0}
        desktop_actions = {"ok": True, "schema": "vrcforge.desktop_actions.v1", "actions": [], "count": 0}
        goals = {"ok": True, "schema": "vrcforge.agent_goals.v1", "goals": [], "count": 0}
        progress = {"ok": True, "schema": "vrcforge.agent_progress.v1", "items": [], "count": 0}
        questions = {"ok": True, "schema": "vrcforge.agent_questions.v1", "questions": [], "count": 0}
        memory = {"ok": True, "schema": "vrcforge.agent_memory_list.v1", "memories": [], "count": 0}
    memory_review_summary = MEMORY_REVIEW.runtime_summary(projectRoot)
    approval_items = AGENT_GATEWAY.list_approvals()
    return {
        "ok": True,
        "schema": "vrcforge.desktop_runtime_snapshot.v1",
        "workspaceDiff": workspace_diff,
        # Approvals form one App-wide inbox. Their own projectRoot travels with
        # each item and is rechecked on every decision, so switching chats must
        # not hide a pending write or change its execution scope.
        "approvals": {"approvals": approval_items, "count": len(approval_items)},
        "runs": runs,
        "desktopActions": desktop_actions,
        "activeDesktopActions": AGENT_GATEWAY.list_active_desktop_actions(limit=8),
        "desktopBridge": AGENT_GATEWAY.desktop_bridge_status(),
        "goals": goals,
        "progress": progress,
        "questions": questions,
        "memory": memory,
        "memoryReview": memory_review_summary,
    }


@app.post("/api/app/unity/readiness/refresh")
async def refresh_app_unity_readiness() -> dict[str, Any]:
    global CURRENT_UNITY_STATUS
    global LAST_STATUS_CONNECTED
    global LAST_STATUS_FINGERPRINT

    snapshot = await asyncio.to_thread(build_unity_status_snapshot)
    fingerprint = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    CURRENT_UNITY_STATUS = snapshot
    LAST_STATUS_FINGERPRINT = fingerprint
    LAST_STATUS_CONNECTED = bool(snapshot.get("connected"))
    await EVENT_BUS.broadcast("unity_status", snapshot)
    return {
        "ok": True,
        "schema": "vrcforge.unity_readiness_refresh.v1",
        "unityStatus": snapshot,
        "health": build_bootstrap_app_health(refresh_projects=False),
    }


@app.get("/api/app/permission")
def read_agentic_app_permission() -> dict[str, Any]:
    return {"ok": True, "permission": AGENT_GATEWAY.permission_state()}


@app.post("/api/app/permission")
async def update_agentic_app_permission(request: AgentPermissionRequest) -> dict[str, Any]:
    before = permission_security_state(AGENT_GATEWAY.permission_state())
    try:
        payload = AGENT_GATEWAY.update_permission_state(
            request.execution_mode,
            acknowledge_roslyn_risk=request.acknowledge_roslyn_risk,
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    after = permission_security_state(payload.get("permission"))
    changed_flags = changed_safety_flags(before, after)
    if changed_flags:
        await emit_log_async(
            "info",
            "security",
            "Permission safety flags changed.",
            {
                "before": before,
                "after": after,
                "changedFlags": changed_flags,
                "source": "app_settings_api",
                "strongConfirmationCompleted": bool(
                    request.acknowledge_roslyn_risk and after.get("fullPermission")
                ),
            },
            essential=True,
        )
    await EVENT_BUS.broadcast("agentPermission", payload["permission"])
    return payload


@app.get("/api/app/advanced-settings")
def read_agentic_app_advanced_settings() -> dict[str, Any]:
    return {
        "ok": True,
        "schema": "vrcforge.advanced_settings.v1",
        "settings": AGENT_GATEWAY.advanced_settings_state(),
    }


@app.post("/api/app/advanced-settings/developer-challenge")
def create_developer_options_challenge() -> dict[str, object]:
    return DEVELOPER_OPTIONS_GUARD.create()


@app.delete("/api/app/advanced-settings/developer-challenge/{challenge_id}")
def cancel_developer_options_challenge(challenge_id: str) -> dict[str, Any]:
    if not DEVELOPER_OPTIONS_GUARD.valid_id(challenge_id):
        raise HTTPException(status_code=404, detail="Developer Options challenge was not found.")
    return {
        "ok": True,
        "schema": "vrcforge.developer_options_challenge.v1",
        "cancelled": DEVELOPER_OPTIONS_GUARD.cancel(challenge_id),
    }


@app.post("/api/app/advanced-settings")
async def update_agentic_app_advanced_settings(request: AdvancedSettingsRequest) -> dict[str, Any]:
    payload = await asyncio.to_thread(update_agentic_app_advanced_settings_guarded, request)
    await EVENT_BUS.broadcast("advancedSettings", payload["settings"])
    return payload


def update_agentic_app_advanced_settings_guarded(request: AdvancedSettingsRequest) -> dict[str, Any]:
    with ADVANCED_SETTINGS_TRANSITION_LOCK:
        current = AGENT_GATEWAY.advanced_settings_state()
        before = advanced_security_state(current)
        enabling_developer_options = bool(request.developer_options_enabled) and not bool(
            current.get("developerOptionsEnabled")
        )
        strong_confirmation_completed = False
        if enabling_developer_options:
            try:
                DEVELOPER_OPTIONS_GUARD.consume(request.developer_challenge_id or "")
            except DeveloperOptionsChallengeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            strong_confirmation_completed = True
        previous_log_level = DIAGNOSTIC_LOGGER.log_level
        trace_downgraded = not request.developer_options_enabled and previous_log_level == "trace"
        if trace_downgraded:
            DIAGNOSTIC_LOGGER.update_config(log_level="debug")
        try:
            update_fields: dict[str, Any] = {
                "developer_options_enabled": request.developer_options_enabled,
                "computer_use_enabled": request.computer_use_enabled,
            }
            if request.background_goal_notifications_enabled is not None:
                update_fields["background_goal_notifications_enabled"] = (
                    request.background_goal_notifications_enabled
                )
            payload = AGENT_GATEWAY.update_advanced_settings(**update_fields)
        except Exception:
            if trace_downgraded:
                try:
                    actual = AGENT_GATEWAY.advanced_settings_state()
                    if actual.get("developerOptionsEnabled"):
                        DIAGNOSTIC_LOGGER.update_config(log_level=previous_log_level)
                except Exception:  # noqa: BLE001 - keep the safer Debug level if rollback state is uncertain.
                    pass
            raise
        after = advanced_security_state(payload.get("settings"))
        changed_flags = changed_safety_flags(before, after)
        if changed_flags:
            emit_log(
                "info",
                "security",
                "Advanced safety flags changed.",
                {
                    "before": before,
                    "after": after,
                    "changedFlags": changed_flags,
                    "source": "app_settings_api",
                    "strongConfirmationCompleted": strong_confirmation_completed,
                },
                essential=True,
            )
        return payload


@app.post("/api/app/agent/message")
async def app_agent_runtime_message(runtime_request: AgentRuntimeMessageRequest) -> dict[str, Any]:
    verified_context_limit = verified_runtime_context_limit(runtime_request)
    runtime_params = agent_runtime_request_payload(runtime_request, verified_context_limit)
    try:
        if runtime_request.computer_use_requested:
            AGENT_GATEWAY.require_computer_use_enabled()
        if runtime_request.goal_delivery_id:
            provider, base_url = background_goal_provider_endpoint(runtime_request)
            payload = await BACKGROUND_GOAL_COORDINATOR.execute(
                delivery_id=runtime_request.goal_delivery_id,
                begin_params={
                    "clientTurnId": runtime_request.client_turn_id,
                    "provider": runtime_request.provider,
                    "providerLabel": runtime_request.provider_label,
                    "model": runtime_request.model,
                },
                runtime_params=runtime_params,
                agent_name=runtime_request.agent_name,
                provider=provider,
                base_url=base_url,
            )
        else:
            payload = await asyncio.to_thread(
                AGENT_GATEWAY.runtime_message,
                runtime_params,
                agent_name=runtime_request.agent_name,
            )
    except BackgroundGoalDeliveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentRuntimeTurn", payload)
    await EVENT_BUS.broadcast("agentRuntimeRuns", AGENT_GATEWAY.list_runtime_runs(limit=30, session_id=payload.get("sessionId") or payload.get("session_id") or ""))
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    if runtime_request.goal_delivery_id:
        await broadcast_background_goal_state({})
    return payload


def agent_runtime_request_payload(
    runtime_request: AgentRuntimeMessageRequest,
    verified_context_limit: int | None,
) -> dict[str, Any]:
    return {
        "session_id": runtime_request.session_id,
        "clientTurnId": runtime_request.client_turn_id,
        "goalDeliveryId": runtime_request.goal_delivery_id,
        "message": runtime_request.message,
        "attachments": runtime_request.attachments,
        "shell_command": runtime_request.shell_command,
        "skill_tool": runtime_request.skill_tool,
        "skill_params": runtime_request.skill_params,
        "cwd": runtime_request.cwd,
        "workspace_root": runtime_request.workspace_root,
        "projectPath": runtime_request.project_path,
        "projectRoot": runtime_request.project_root,
        "provider": runtime_request.provider,
        "providerLabel": runtime_request.provider_label,
        "model": runtime_request.model,
        "_contextCompactionLimit": verified_context_limit,
        "history": runtime_request.history,
        "_computerUseRequested": runtime_request.computer_use_requested,
        "_computerUseGrantId": runtime_request.computer_use_grant_id,
        "_computerUseVisualTheme": runtime_request.computer_use_visual_theme,
        "_computerUseVisualAccent": runtime_request.computer_use_visual_accent,
    }


def verified_runtime_context_limit(runtime_request: AgentRuntimeMessageRequest) -> int | None:
    if runtime_request.context_limit is None:
        return None
    settings = load_dashboard_settings(ConnectionRequest())
    raw_requested_provider = str(runtime_request.provider or "").strip()
    if not raw_requested_provider:
        return None
    try:
        requested_provider = normalize_provider_name(raw_requested_provider)
        configured_provider = normalize_provider_name(str(settings.llm_provider or ""))
    except RuntimeError:
        return None
    requested_model = str(runtime_request.model or "").strip().lower()
    configured_model = str(settings.llm_model or "").strip().lower()
    if requested_model.startswith("models/"):
        requested_model = requested_model[7:]
    if configured_model.startswith("models/"):
        configured_model = configured_model[7:]
    if not requested_model:
        return None
    if requested_provider != configured_provider or requested_model != configured_model:
        return None
    return runtime_request.context_limit


def probe_background_goal_provider(provider: str, base_url: str) -> bool:
    """Perform a bounded loopback reachability check without sending a prompt."""

    target = str(base_url or "").rstrip("/")
    _ = provider
    request = urllib.request.Request(target, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - caller admits loopback URLs only.
            status = int(getattr(response, "status", 200) or 200)
            return 100 <= status <= 599
    except urllib.error.HTTPError as exc:
        # Any bounded client response proves the local listener is reachable.
        return 100 <= int(exc.code or 0) <= 599
    except (OSError, TimeoutError, urllib.error.URLError, ValueError):
        return False


def background_goal_provider_endpoint(runtime_request: AgentRuntimeMessageRequest) -> tuple[str, str]:
    configured = PROVIDER_CONFIGURATION.current_api_config()
    requested_provider = normalize_provider_name(runtime_request.provider or configured.provider)
    if requested_provider != normalize_provider_name(configured.provider):
        return requested_provider, ""
    return requested_provider, str(configured.base_url or "")


@app.post("/api/app/agent/computer-use/grants")
def app_issue_computer_use_turn_grant(request: ComputerUseTurnGrantRequest) -> dict[str, Any]:
    try:
        return AGENT_GATEWAY.issue_computer_use_turn_grant(
            {
                "sessionId": request.session_id,
                "clientTurnId": request.client_turn_id,
                "projectRoot": request.project_root,
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/app/agent/compact")
def app_agent_compact(request: AgentCompactRequest) -> dict[str, Any]:
    settings = load_dashboard_settings(ConnectionRequest())
    summarizer: Callable[[str], Any] | None = None
    if not provider_requires_api_key(settings.llm_provider) or str(settings.llm_api_key or "").strip():
        summarizer = lambda prompt: request_llm_plan(settings, prompt)
    try:
        return compact_context(
            request.history,
            summarizer=summarizer,
            source_digest=request.source_digest,
            trigger=request.trigger,
            phase=request.phase,
            language=request.language,
            provider=settings.llm_provider,
            model=settings.llm_model,
            target_tokens=request.target_tokens,
            real_context_limit=request.real_context_limit,
        )
    except ContextCompactionInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/app/agent/session/{session_id}")
def app_agent_runtime_session(session_id: str) -> dict[str, Any]:
    try:
        return AGENT_GATEWAY.get_runtime_session(session_id)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/app/agent/runs")
def app_agent_runtime_runs(
    limit: int = 50,
    sessionId: str = "",
    projectRoot: str = "",
    clientTurnId: str = "",
) -> dict[str, Any]:
    return AGENT_GATEWAY.list_runtime_runs(
        limit=limit,
        session_id=sessionId,
        project_root=projectRoot,
        client_turn_id=clientTurnId,
    )


@app.post("/api/app/agent/runs/cancel")
async def app_agent_runtime_cancel(cancel_request: AgentRuntimeCancelRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.request_runtime_cancel(
            {
                "session_id": cancel_request.session_id,
                "turnId": cancel_request.turn_id,
                "clientTurnId": cancel_request.client_turn_id,
                "reason": cancel_request.reason,
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentRuntimeCancel", payload)
    await EVENT_BUS.broadcast("agentRuntimeRuns", AGENT_GATEWAY.list_runtime_runs(limit=30, session_id=cancel_request.session_id or ""))
    return payload


@app.post("/api/app/agent/runs/queue")
async def app_agent_runtime_queue(queue_request: AgentRuntimeQueueRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.record_runtime_queue_event(
            {
                "session_id": queue_request.session_id,
                "clientTurnId": queue_request.client_turn_id,
                "message": queue_request.message,
                "attachments": queue_request.attachments,
                "provider": queue_request.provider,
                "providerLabel": queue_request.provider_label,
                "model": queue_request.model,
                "projectPath": queue_request.project_path,
                "projectRoot": queue_request.project_root,
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentRuntimeQueue", payload)
    await EVENT_BUS.broadcast("agentRuntimeRuns", AGENT_GATEWAY.list_runtime_runs(limit=30, session_id=queue_request.session_id or ""))
    return payload


@app.get("/api/app/agent/desktop-actions")
def app_agent_desktop_actions(limit: int = 50, sessionId: str = "", projectRoot: str = "") -> dict[str, Any]:
    return AGENT_GATEWAY.list_desktop_actions(limit=limit, session_id=sessionId, project_root=projectRoot)


@app.get("/api/app/agent/desktop-actions/{action_id}/result")
def app_agent_desktop_action_result(action_id: str) -> dict[str, Any]:
    try:
        return AGENT_GATEWAY.get_desktop_action_result(action_id)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/app/agent/desktop-actions")
async def app_agent_desktop_action(request: AgentDesktopActionRequest) -> dict[str, Any]:
    try:
        if request.action in DESKTOP_BRIDGE_ACTION_TYPES:
            AGENT_GATEWAY.require_computer_use_enabled()
        payload = await asyncio.to_thread(
            AGENT_GATEWAY.request_desktop_action,
            {
                "action": request.action,
                "prompt": request.prompt,
                "sessionId": request.session_id,
                "clientTurnId": request.client_turn_id,
                "projectPath": request.project_path,
                "projectRoot": request.project_root,
                "params": request.params,
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentDesktopActions", AGENT_GATEWAY.list_desktop_actions(limit=30, session_id=request.session_id or ""))
    return payload


@app.get("/api/app/agent/desktop-bridge")
def app_agent_desktop_bridge_status() -> dict[str, Any]:
    return {
        **AGENT_GATEWAY.desktop_bridge_status(),
        "embeddedExecutor": AGENT_GATEWAY.desktop.embedded_worker_status(),
    }


@app.post("/api/app/agent/desktop-bridge/register")
async def app_agent_desktop_bridge_register(request: DesktopBridgeRegisterRequest) -> dict[str, Any]:
    try:
        payload = await asyncio.to_thread(
            AGENT_GATEWAY.register_desktop_bridge,
            {
                "name": request.name,
                "provider": request.provider,
                "capabilities": request.capabilities,
                "operations": request.operations,
            },
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return payload


@app.post("/api/app/agent/desktop-bridge/heartbeat")
async def app_agent_desktop_bridge_heartbeat(request: DesktopBridgeHeartbeatRequest) -> dict[str, Any]:
    try:
        payload = await asyncio.to_thread(
            AGENT_GATEWAY.heartbeat_desktop_bridge,
            {"bridgeId": request.bridge_id, "bridgeCredential": request.bridge_credential},
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return payload


@app.post("/api/app/agent/desktop-actions/claim")
async def app_agent_desktop_action_claim(request: DesktopActionClaimRequest) -> dict[str, Any]:
    try:
        payload = await asyncio.to_thread(
            AGENT_GATEWAY.claim_desktop_action,
            {
                "bridgeId": request.bridge_id,
                "bridgeCredential": request.bridge_credential,
                "actions": request.actions,
                "claimRequestId": request.claim_request_id,
            },
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if payload.get("action"):
        await EVENT_BUS.broadcast("agentDesktopActions", AGENT_GATEWAY.list_desktop_actions(limit=30))
    return payload


@app.post("/api/app/agent/desktop-actions/complete")
async def app_agent_desktop_action_complete(request: DesktopActionCompleteRequest) -> dict[str, Any]:
    try:
        payload = await asyncio.to_thread(
            AGENT_GATEWAY.complete_desktop_action,
            {
                "bridgeId": request.bridge_id,
                "bridgeCredential": request.bridge_credential,
                "actionId": request.action_id,
                "status": request.status,
                "result": request.result,
                "error": request.error,
            },
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentDesktopActions", AGENT_GATEWAY.list_desktop_actions(limit=30))
    return payload


@app.post("/api/app/agent/desktop-actions/{action_id}/cancel")
async def app_agent_desktop_action_cancel(action_id: str, request: DesktopActionCancelRequest) -> dict[str, Any]:
    try:
        payload = await asyncio.to_thread(
            AGENT_GATEWAY.request_desktop_action_cancel,
            action_id,
            {"reason": request.reason},
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentDesktopActions", AGENT_GATEWAY.list_desktop_actions(limit=30))
    return payload


@app.get("/api/app/agent/goals")
def app_agent_goals(limit: int = 50, sessionId: str = "", projectRoot: str = "") -> dict[str, Any]:
    return AGENT_GOALS.list_agent_goals(limit=limit, session_id=sessionId, project_root=projectRoot)


@app.get("/api/app/agent/goals/due")
def app_due_agent_goals(limit: int = 20, sessionId: str = "", projectRoot: str = "") -> dict[str, Any]:
    return AGENT_GOALS.list_due_agent_goals(limit=limit, session_id=sessionId, project_root=projectRoot)


@app.post("/api/app/agent/goals")
async def app_create_agent_goal(request: AgentGoalCreateRequest) -> dict[str, Any]:
    params: dict[str, Any] = {
        "title": request.title,
        "goal": request.goal,
        "summary": request.summary,
        "sessionId": request.session_id,
        "chatId": request.chat_id,
        "projectPath": request.project_path,
        "projectRoot": request.project_root,
    }
    # 只有显式提供时才透传唤醒字段：Goal owner 用“键是否存在”区分“清除”与“保持不变”。
    if "wake_at" in request.model_fields_set:
        params["wakeAt"] = request.wake_at
    if "wake_every_minutes" in request.model_fields_set:
        params["wakeEveryMinutes"] = request.wake_every_minutes
    try:
        payload = AGENT_GOALS.create_agent_goal(params)
    except AgentGoalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentGoals", AGENT_GOALS.list_agent_goals(limit=30, session_id=request.session_id or ""))
    return payload


@app.post("/api/app/agent/goals/{goal_id}/wake")
async def app_wake_agent_goal(goal_id: str, request: AgentGoalWakeRequest) -> dict[str, Any]:
    worker = asyncio.create_task(
        asyncio.to_thread(
            AGENT_GOALS.wake_agent_goal,
            goal_id,
            {
                "sessionId": request.session_id,
                "chatId": request.chat_id,
                "projectRoot": request.project_root,
            },
        )
    )
    try:
        payload = await asyncio.wait_for(
            asyncio.shield(worker),
            timeout=PHASE_TIMEOUT_SECONDS["wake"],
        )
    except TimeoutError as exc:
        track_timed_out_goal_wake(worker)
        raise HTTPException(status_code=504, detail="Background goal wake timed out.") from exc
    except asyncio.CancelledError:
        track_timed_out_goal_wake(worker)
        raise
    except AgentGoalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentGoals", AGENT_GOALS.list_agent_goals(limit=30, session_id=request.session_id or ""))
    return payload


@app.post("/api/app/agent/goals/{goal_id}/bind-owner")
async def app_bind_agent_goal_owner(goal_id: str, request: AgentGoalOwnerBindRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GOALS.bind_agent_goal_owner(
            goal_id,
            {
                "sessionId": request.session_id,
                "chatId": request.chat_id,
                "projectRoot": request.project_root,
            },
        )
    except AgentGoalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentGoals", AGENT_GOALS.list_agent_goals(limit=30, session_id=request.session_id or ""))
    return payload


@app.get("/api/app/agent/goals/deliveries/recoverable")
def app_recoverable_agent_goal_deliveries(limit: int = 20, chatId: str = "") -> dict[str, Any]:
    return AGENT_GOALS.list_recoverable_agent_goal_deliveries(limit=limit, chat_id=chatId)


@app.get("/api/app/agent/goals/background")
def app_agent_goal_background_state(chatId: str = "") -> dict[str, Any]:
    return AGENT_GOALS.agent_goal_background_state(chat_id=chatId)


@app.post("/api/app/agent/goals/background/ack")
async def app_acknowledge_agent_goal_background_state(
    request: AgentGoalBackgroundAcknowledgeRequest,
) -> dict[str, Any]:
    try:
        payload = AGENT_GOALS.acknowledge_agent_goal_background_state(
            chat_id=request.chat_id,
            delivery_ids=[
                {
                    "deliveryId": item.delivery_id,
                    "expectedRevision": item.expected_revision,
                }
                for item in request.deliveries
            ]
            or request.delivery_ids,
            kind=request.kind,
        )
    except AgentGoalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentGoalBackground", payload)
    return payload


@app.post("/api/app/agent/goals/deliveries/{delivery_id}/materialized")
async def app_materialize_agent_goal_delivery(
    delivery_id: str,
    request: AgentGoalDeliveryMaterializeRequest,
) -> dict[str, Any]:
    try:
        payload = AGENT_GOALS.materialize_agent_goal_delivery(
            delivery_id,
            {"chatId": request.chat_id, "expectedRevision": request.expected_revision},
        )
    except AgentGoalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return payload


@app.post("/api/app/agent/goals/deliveries/{delivery_id}/defer")
async def app_defer_agent_goal_delivery(
    delivery_id: str,
    request: AgentGoalDeliveryDeferRequest,
) -> dict[str, Any]:
    try:
        payload = AGENT_GOALS.defer_agent_goal_delivery_handoff(
            delivery_id,
            expected_revision=request.expected_revision,
        )
    except AgentGoalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await broadcast_background_goal_state({})
    return payload


@app.post("/api/app/agent/goals/{goal_id}")
async def app_update_agent_goal(goal_id: str, request: AgentGoalUpdateRequest) -> dict[str, Any]:
    params: dict[str, Any] = {
        "status": request.status,
        "summary": request.summary,
        "note": request.note,
        "sessionId": request.session_id,
        "chatId": request.chat_id,
        "projectRoot": request.project_root,
    }
    if "wake_at" in request.model_fields_set:
        params["wakeAt"] = request.wake_at
    if "wake_every_minutes" in request.model_fields_set:
        params["wakeEveryMinutes"] = request.wake_every_minutes
    try:
        payload = AGENT_GOALS.update_agent_goal(goal_id, params)
    except AgentGoalServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentGoals", AGENT_GOALS.list_agent_goals(limit=30, session_id=request.session_id or ""))
    return payload


@app.get("/api/app/agent/progress")
def app_agent_progress(limit: int = 50, sessionId: str = "", projectRoot: str = "") -> dict[str, Any]:
    return AGENT_GATEWAY.list_agent_progress(limit=limit, session_id=sessionId, project_root=projectRoot)


@app.post("/api/app/agent/progress/replace")
async def app_replace_agent_progress(request: AgentProgressReplaceRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.replace_agent_progress(
            {
                "items": request.items,
                "plan": request.plan,
                "sessionId": request.session_id,
                "projectPath": request.project_path,
                "projectRoot": request.project_root,
                "goalDeliveryId": request.goal_delivery_id,
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentProgress", AGENT_GATEWAY.list_agent_progress(limit=30, session_id=request.session_id or "", project_root=request.project_root or ""))
    return payload


@app.post("/api/app/agent/progress")
async def app_create_agent_progress(request: AgentProgressItemRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.create_agent_progress(
            {
                "title": request.title,
                "step": request.step,
                "content": request.content,
                "summary": request.summary,
                "description": request.description,
                "status": request.status,
                "order": request.order,
                "owner": request.owner,
                "sessionId": request.session_id,
                "projectPath": request.project_path,
                "projectRoot": request.project_root,
                "goalDeliveryId": request.goal_delivery_id,
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentProgress", AGENT_GATEWAY.list_agent_progress(limit=30, session_id=request.session_id or "", project_root=request.project_root or ""))
    return payload


@app.post("/api/app/agent/progress/{progress_id}")
async def app_update_agent_progress(progress_id: str, request: AgentProgressItemRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.update_agent_progress(
            progress_id,
            {
                "title": request.title,
                "step": request.step,
                "content": request.content,
                "summary": request.summary,
                "description": request.description,
                "status": request.status,
                "order": request.order,
                "owner": request.owner,
                "sessionId": request.session_id,
                "projectPath": request.project_path,
                "projectRoot": request.project_root,
            },
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentProgress", AGENT_GATEWAY.list_agent_progress(limit=30, session_id=request.session_id or "", project_root=request.project_root or ""))
    return payload


@app.delete("/api/app/agent/progress/{progress_id}")
async def app_delete_agent_progress(progress_id: str, sessionId: str = "", projectRoot: str = "") -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.delete_agent_progress(progress_id, {"sessionId": sessionId, "projectRoot": projectRoot})
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentProgress", AGENT_GATEWAY.list_agent_progress(limit=30, session_id=sessionId or "", project_root=projectRoot or ""))
    return payload


@app.get("/api/app/agent/questions")
def app_agent_questions(limit: int = 50, sessionId: str = "", projectRoot: str = "", includeAnswered: bool = False) -> dict[str, Any]:
    return AGENT_QUESTIONS.list(limit=limit, session_id=sessionId, project_root=projectRoot, include_answered=includeAnswered)


@app.post("/api/app/agent/questions")
async def app_create_agent_question(request: AgentQuestionCreateRequest) -> dict[str, Any]:
    try:
        payload = AGENT_QUESTIONS.create(
            {
                "header": request.header,
                "question": request.question,
                "prompt": request.prompt,
                "options": request.options,
                "choices": request.choices,
                "owner": request.owner,
                "sessionId": request.session_id,
                "projectPath": request.project_path,
                "projectRoot": request.project_root,
                "goalDeliveryId": request.goal_delivery_id,
            }
        )
    except AgentQuestionServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentQuestions", AGENT_QUESTIONS.list(limit=30, session_id=request.session_id or "", project_root=request.project_root or ""))
    return payload


@app.post("/api/app/agent/questions/{question_id}/answer")
async def app_answer_agent_question(question_id: str, request: AgentQuestionAnswerRequest) -> dict[str, Any]:
    try:
        payload = AGENT_QUESTIONS.answer(
            question_id,
            {
                "answer": request.answer,
                "value": request.value,
                "optionId": request.option_id,
                "selectedOptionId": request.selected_option_id,
                "sessionId": request.session_id,
                "projectRoot": request.project_root,
            },
        )
    except AgentQuestionServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentQuestions", AGENT_QUESTIONS.list(limit=30, session_id=request.session_id or "", project_root=request.project_root or ""))
    if payload.get("goalDelivery") is not None:
        await broadcast_background_goal_state({})
    return payload


CHAT_TRANSCRIPTS_MAX_BYTES = 16 * 1024 * 1024
CHAT_TRANSCRIPTS_MAX_CHATS = 100
CHAT_TRANSCRIPTS_LOCK = RLock()
CHAT_REQUESTED_PROJECT_PATH_LIMIT = 100
CHAT_REQUESTED_PROJECT_PATHS: dict[str, str] = {}


# STOPGAP: keep one app-lifetime Memory Review graph while the remaining 1.5
# domains move to typed composition.  The final 1.5 seam-retirement gate must
# inject this graph into lifecycle/controllers and remove this root lookup and
# Gateway callback binding before the version bump.
MEMORY_REVIEW: MemoryReviewComposition = build_memory_review_composition(
    MemoryReviewCompositionPorts(
        accepted_memory_store=AGENT_GATEWAY.agent_memory_store,
        review_root=lambda: AGENT_GATEWAY.audit_dir / "memory-review",
        shared_state_lock=AGENT_GATEWAY._lock,
        audit_append_lock=AGENT_GATEWAY._audit_append_lock,
        list_memory=lambda limit, project_root: AGENT_GATEWAY.list_agent_memory(
            limit=limit,
            project_root=project_root,
        ),
        acquire_background_project_read=AGENT_GATEWAY.try_acquire_background_project_read,
        release_background_project_read=AGENT_GATEWAY.release_background_project_read,
        bind_background_activity=lambda callback: setattr(
            AGENT_GATEWAY,
            "background_activity_started_fn",
            callback,
        ),
        lane_budget=RUNTIME_LANE_BUDGET,
        preflight=BACKGROUND_GOAL_PREFLIGHT,
        build_runtime=lambda lane_budget, preflight, on_state: MemoryReviewRuntimeCoordinator(
            lane_budget=lane_budget,
            preflight=preflight,
            on_state=on_state,
        ),
        adapter=MemoryReviewDashboardAdapter(
            project_snapshot=lambda: cached_project_snapshot_payload(refresh_async=False),
            selected_project_path=lambda: (
                str(DASHBOARD_STATE.selected_project_path or "")
                if DASHBOARD_STATE is not None
                else ""
            ),
            indexed_project_paths=lambda: load_chat_project_index_paths(),
            requested_project_paths=lambda: list(CHAT_REQUESTED_PROJECT_PATHS.values()),
            resolve_project_root=lambda candidate: resolve_chat_project_root(candidate),
            chat_lock=CHAT_TRANSCRIPTS_LOCK,
            chat_transcripts_path=lambda: chat_transcripts_path(),
            project_chat_transcripts_path=lambda project_root: project_chat_transcripts_path(project_root),
            chat_store_target=lambda *args, **kwargs: chat_store_target(*args, **kwargs),
            load_chat_transcript_file=lambda *args, **kwargs: load_chat_transcript_file(*args, **kwargs),
            list_tasks=lambda: _SUB_AGENT_COLLABORATION.list_tasks(
                include_events=False,
                limit=500,
            ),
            audit_log_path=lambda: AGENT_GATEWAY.audit_log_path,
            load_provider_settings=lambda: load_dashboard_settings(ConnectionRequest()),
            normalize_provider=normalize_provider_name,
            provider_display_name=provider_display_name,
            provider_requires_api_key=provider_requires_api_key,
        ),
        broadcast=EVENT_BUS.broadcast,
        emit_warning=lambda failure_class: emit_log(
            "warn",
            "agent",
            "Memory Review background run had a bounded failure.",
            {"failureClass": str(failure_class or "runtime")},
        ),
        chat_lock=CHAT_TRANSCRIPTS_LOCK,
        provider_call=lambda settings, payload, token_cap: invoke_memory_review_provider(
            settings,
            payload,
            token_cap=token_cap,
        ),
        sub_agent_source_commit_lock=_SUB_AGENT_COLLABORATION.source_commit_lock,
    )
)
app.include_router(build_memory_review_router(MEMORY_REVIEW.host))


@app.get("/api/app/agent/memory")
def app_agent_memory(limit: int = 50, projectRoot: str = "", scope: str = "") -> dict[str, Any]:
    return AGENT_GATEWAY.list_agent_memory(limit=limit, project_root=projectRoot, scope=scope)


@app.post("/api/app/agent/memory")
async def app_create_agent_memory(request: AgentMemoryCreateRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.create_agent_memory(
            {
                "text": request.text,
                "content": request.content,
                "scope": request.scope,
                "kind": request.kind,
                "source": request.source,
                "projectPath": request.project_path,
                "projectRoot": request.project_root,
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentMemory", AGENT_GATEWAY.list_agent_memory(limit=30, project_root=request.project_root or ""))
    return payload


@app.delete("/api/app/agent/memory/{memory_id}")
async def app_delete_agent_memory(memory_id: str, request: AgentMemoryDeleteRequest | None = None) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.delete_agent_memory(memory_id, {"reason": request.reason if request else ""})
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await asyncio.to_thread(MEMORY_REVIEW.service.reconcile_external_memory_deletions, [memory_id])
    await EVENT_BUS.broadcast("agentMemory", AGENT_GATEWAY.list_agent_memory(limit=30))
    await MEMORY_REVIEW.notify_review_changed()
    return payload


@app.post("/api/app/agent/memory/clear")
async def app_clear_agent_memory(request: AgentMemoryClearRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.clear_agent_memory({"scope": request.scope, "reason": request.reason, "projectRoot": request.project_root})
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await asyncio.to_thread(MEMORY_REVIEW.service.reconcile_external_memory_deletions)
    await EVENT_BUS.broadcast("agentMemory", AGENT_GATEWAY.list_agent_memory(limit=30, project_root=request.project_root or ""))
    await MEMORY_REVIEW.notify_review_changed()
    return payload


@app.get("/api/app/agent/approvals")
def app_agent_approvals(projectRoot: str = "", globalOnly: bool = False) -> dict[str, Any]:
    approvals = AGENT_GATEWAY.list_approvals(project_root=projectRoot, global_only=globalOnly)
    return {"ok": True, "approvals": approvals, "count": len(approvals)}


@app.get("/api/app/checkpoints")
def app_list_checkpoints(projectRoot: str = "", limit: int = 50) -> dict[str, Any]:
    return AGENT_GATEWAY.list_checkpoints({"projectRoot": projectRoot, "limit": limit})


@app.post("/api/app/checkpoints/{checkpoint_id}/preview")
def app_preview_restore_checkpoint(checkpoint_id: str) -> dict[str, Any]:
    return AGENT_GATEWAY.preview_restore_checkpoint({"checkpointId": checkpoint_id})


@app.post("/api/app/checkpoints/{checkpoint_id}/restore")
async def app_request_restore_checkpoint(checkpoint_id: str) -> dict[str, Any]:
    preview = AGENT_GATEWAY.preview_restore_checkpoint({"checkpointId": checkpoint_id})
    if not preview.get("ok"):
        raise HTTPException(status_code=400, detail=preview.get("error") or "Checkpoint is not restorable.")
    checkpoint = ensure_dict(preview.get("checkpoint"))
    arguments = {"checkpointId": checkpoint_id, "confirmRestore": True}
    if checkpoint.get("projectRoot"):
        arguments["projectRoot"] = str(checkpoint.get("projectRoot"))
    try:
        payload = AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": "vrcforge_restore_checkpoint",
                "arguments": arguments,
                "reason": "Restore Unity project files from a VRCForge checkpoint.",
                "preview": preview,
                "agent_name": "desktop-agent",
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


def require_primitive_basis_live_runtime() -> ModelPartCompositionLiveRuntime:
    if PRIMITIVE_BASIS_LIVE_RUNTIME is None:
        raise HTTPException(status_code=404, detail="Packaged primitive live verification is not active.")
    return PRIMITIVE_BASIS_LIVE_RUNTIME


@app.post("/api/app/primitive-basis/live/model-part/start")
async def app_start_primitive_basis_model_part_live(
    request: PrimitiveBasisLiveStartRequest,
) -> dict[str, Any]:
    runtime = require_primitive_basis_live_runtime()
    try:
        return await asyncio.to_thread(runtime.start, request.project_path)
    except PrimitiveBasisLiveRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/app/primitive-basis/live/model-part/status")
def app_primitive_basis_model_part_live_status() -> dict[str, Any]:
    return require_primitive_basis_live_runtime().status()


@app.post("/api/app/primitive-basis/live/model-part/readback")
async def app_readback_primitive_basis_model_part_live() -> dict[str, Any]:
    runtime = require_primitive_basis_live_runtime()
    try:
        payload = await asyncio.to_thread(runtime.readback_and_request_restore)
    except PrimitiveBasisLiveRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/app/primitive-basis/live/model-part/prepare-cleanup")
async def app_prepare_primitive_basis_model_part_cleanup() -> dict[str, Any]:
    runtime = require_primitive_basis_live_runtime()
    try:
        return await asyncio.to_thread(runtime.prepare_cleanup)
    except PrimitiveBasisLiveRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/app/primitive-basis/live/model-part/finalize")
async def app_finalize_primitive_basis_model_part_live() -> dict[str, Any]:
    runtime = require_primitive_basis_live_runtime()
    try:
        return await asyncio.to_thread(runtime.finalize_after_cleanup)
    except PrimitiveBasisLiveRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/app/recoveries")
def app_list_interrupted_apply_recoveries(projectRoot: str = "", limit: int = 50, includeResolved: bool = False) -> dict[str, Any]:
    return AGENT_GATEWAY.list_interrupted_apply_recoveries(
        {"projectRoot": projectRoot, "limit": limit, "includeResolved": includeResolved}
    )


@app.post("/api/app/recoveries/{recovery_id}/preview")
def app_preview_interrupted_apply_recovery(recovery_id: str) -> dict[str, Any]:
    payload = AGENT_GATEWAY.preview_interrupted_apply_recovery({"recoveryId": recovery_id})
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Interrupted apply recovery was not found.")
    return payload


@app.post("/api/app/recoveries/{recovery_id}/restore")
async def app_request_restore_interrupted_apply_recovery(recovery_id: str) -> dict[str, Any]:
    preview = AGENT_GATEWAY.preview_interrupted_apply_recovery({"recoveryId": recovery_id})
    if not preview.get("ok"):
        raise HTTPException(status_code=404, detail=preview.get("error") or "Interrupted apply recovery was not found.")
    checkpoint = ensure_dict(ensure_dict(preview.get("checkpointPreview")).get("checkpoint"))
    checkpoint_id = str(checkpoint.get("id") or "")
    if not checkpoint_id:
        raise HTTPException(status_code=400, detail="Interrupted apply recovery has no restorable checkpoint.")
    arguments = {"checkpointId": checkpoint_id, "confirmRestore": True}
    if checkpoint.get("projectRoot"):
        arguments["projectRoot"] = str(checkpoint.get("projectRoot"))
    try:
        payload = AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": "vrcforge_restore_checkpoint",
                "arguments": arguments,
                "reason": "Restore Unity project files after an interrupted or failed approved write.",
                "preview": preview,
                "agent_name": "desktop-agent",
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    denied_goal = payload.get("goalDelivery")
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    if denied_goal is not None:
        await broadcast_background_goal_state({})
    return payload


@app.post("/api/app/recoveries/{recovery_id}/resolve")
async def app_request_resolve_interrupted_apply_recovery(
    recovery_id: str,
    request: InterruptedApplyRecoveryResolveRequest,
) -> dict[str, Any]:
    arguments = request.model_dump(by_alias=True, exclude_none=True)
    arguments["recoveryId"] = recovery_id
    if arguments.get("confirmResolved") is not True:
        raise HTTPException(status_code=400, detail="confirmResolved=true is required.")
    try:
        payload = AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": "vrcforge_resolve_interrupted_apply_recovery",
                "arguments": arguments,
                "reason": "Mark an interrupted approved write as manually resolved.",
                "preview": AGENT_GATEWAY.preview_interrupted_apply_recovery({"recoveryId": recovery_id}),
                "agent_name": "desktop-agent",
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/app/recoveries/{recovery_id}/incident-bundle")
def app_export_interrupted_apply_incident_bundle(recovery_id: str) -> dict[str, Any]:
    payload = AGENT_GATEWAY.export_interrupted_apply_incident_bundle({"recoveryId": recovery_id})
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Interrupted apply recovery was not found.")
    return payload


@app.get("/api/app/adjustment-checkpoints")
def app_list_adjustment_checkpoints(
    kind: str = "",
    projectRoot: str = "",
    avatarPath: str = "",
    limit: int = 50,
    includeDeleted: bool = False,
) -> dict[str, Any]:
    return AGENT_GATEWAY.list_adjustment_checkpoints(
        {
            "kind": kind,
            "projectRoot": projectRoot,
            "avatarPath": avatarPath,
            "limit": limit,
            "includeDeleted": includeDeleted,
        }
    )


@app.post("/api/app/adjustment-checkpoints")
def app_create_adjustment_checkpoint(request: AdjustmentCheckpointCreateRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.create_adjustment_checkpoint(request.model_dump(by_alias=True, exclude_none=True))
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if not payload.get("ok"):
        raise HTTPException(status_code=400, detail=payload.get("error") or "Adjustment checkpoint could not be created.")
    return payload


@app.get("/api/app/adjustment-checkpoints/selection")
def app_get_selected_adjustment_checkpoints(kind: str = "", compareGroup: str = "") -> dict[str, Any]:
    return AGENT_GATEWAY.get_selected_adjustment_checkpoints({"kind": kind, "compareGroup": compareGroup})


@app.get("/api/app/adjustment-checkpoints/{entry_id}")
def app_get_adjustment_checkpoint(entry_id: str) -> dict[str, Any]:
    payload = AGENT_GATEWAY.get_adjustment_checkpoint(entry_id)
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Adjustment checkpoint was not found.")
    return payload


@app.put("/api/app/adjustment-checkpoints/{entry_id}")
def app_update_adjustment_checkpoint(entry_id: str, request: AdjustmentCheckpointUpdateRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.update_adjustment_checkpoint(
            entry_id,
            request.model_dump(by_alias=True, exclude_none=True),
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Adjustment checkpoint was not found.")
    return payload


@app.delete("/api/app/adjustment-checkpoints/{entry_id}")
def app_delete_adjustment_checkpoint(entry_id: str, hardDelete: bool = False) -> dict[str, Any]:
    payload = AGENT_GATEWAY.delete_adjustment_checkpoint(entry_id, {"hardDelete": hardDelete})
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Adjustment checkpoint was not found.")
    return payload


@app.post("/api/app/adjustment-checkpoints/{entry_id}/select")
def app_select_adjustment_checkpoint(entry_id: str, request: AdjustmentCheckpointSelectRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.select_adjustment_checkpoint(
            entry_id,
            request.model_dump(by_alias=True, exclude_none=True),
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Adjustment checkpoint was not found.")
    return payload


@app.post("/api/app/adjustment-checkpoints/{entry_id}/overwrite")
def app_overwrite_adjustment_checkpoint(entry_id: str, request: AdjustmentCheckpointOverwriteRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.overwrite_adjustment_checkpoint(
            entry_id,
            request.model_dump(by_alias=True, exclude_none=True),
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if not payload.get("ok"):
        raise HTTPException(status_code=400, detail=payload.get("error") or "Adjustment checkpoint could not be overwritten.")
    return payload


@app.post("/api/app/adjustment-checkpoints/{entry_id}/apply")
async def app_apply_adjustment_checkpoint(entry_id: str) -> dict[str, Any]:
    preview = AGENT_GATEWAY.preview_restore_adjustment_checkpoint(entry_id)
    if not preview.get("ok"):
        raise HTTPException(status_code=400, detail=preview.get("error") or "Adjustment checkpoint is not restorable.")
    checkpoint = ensure_dict(preview.get("checkpoint"))
    checkpoint_id = str(checkpoint.get("id") or "")
    arguments = {"checkpointId": checkpoint_id, "confirmRestore": True}
    if checkpoint.get("projectRoot"):
        arguments["projectRoot"] = str(checkpoint.get("projectRoot"))
    try:
        payload = AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": "vrcforge_restore_checkpoint",
                "arguments": arguments,
                "reason": "Apply a selected high-frequency face/shader adjustment checkpoint for A/B comparison.",
                "preview": preview,
                "agent_name": "desktop-agent",
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/app/adjustment-checkpoints/{entry_id}/preview")
def app_preview_adjustment_checkpoint_restore(entry_id: str) -> dict[str, Any]:
    payload = AGENT_GATEWAY.preview_restore_adjustment_checkpoint(entry_id)
    if not payload.get("ok"):
        raise HTTPException(status_code=400, detail=payload.get("error") or "Adjustment checkpoint is not restorable.")
    return payload


@app.post("/api/app/adjustment-checkpoints/{entry_id}/restore")
async def app_request_restore_adjustment_checkpoint(entry_id: str) -> dict[str, Any]:
    preview = AGENT_GATEWAY.preview_restore_adjustment_checkpoint(entry_id)
    if not preview.get("ok"):
        raise HTTPException(status_code=400, detail=preview.get("error") or "Adjustment checkpoint is not restorable.")
    checkpoint = ensure_dict(preview.get("checkpoint"))
    checkpoint_id = str(checkpoint.get("id") or "")
    arguments = {"checkpointId": checkpoint_id, "confirmRestore": True}
    if checkpoint.get("projectRoot"):
        arguments["projectRoot"] = str(checkpoint.get("projectRoot"))
    try:
        payload = AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": "vrcforge_restore_checkpoint",
                "arguments": arguments,
                "reason": "Restore a high-frequency face/shader adjustment checkpoint.",
                "preview": preview,
                "agent_name": "desktop-agent",
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/app/agent/approvals/{approval_id}/approve")
async def app_agent_approve_and_execute(
    approval_id: str,
    request: AgentApprovalScopeRequest | None = None,
) -> dict[str, Any]:
    expected_project_root = (request.expected_project_root if request else "") or ""
    global_only = bool(request.global_only if request else True)

    def approve() -> dict[str, Any]:
        if request and request.allow_future_category:
            return AGENT_GATEWAY.approve_with_project_category_rule(
                approval_id,
                expected_project_root=expected_project_root,
                global_only=global_only,
            )
        return AGENT_GATEWAY.approve(
            approval_id,
            expected_project_root=expected_project_root,
            global_only=global_only,
        )

    def execute_approved(approved: dict[str, Any]) -> dict[str, Any]:
        approval = approved.get("approval") if isinstance(approved, dict) else None
        if not isinstance(approval, dict) or not approved.get("ok"):
            return {}
        if approval.get("targetTool") == "vrcforge_shell_execute":
            return AGENT_GATEWAY.shell.execute_approved({"approval_id": approval_id})
        return AGENT_GATEWAY.apply_approved({"approval_id": approval_id})

    linked = await asyncio.to_thread(AGENT_GOALS.agent_goal_delivery_for_approval, approval_id)
    linked_delivery = ensure_dict((linked or {}).get("delivery"))
    linked_delivery_id = str(linked_delivery.get("deliveryId") or "")

    if linked_delivery_id:
        try:
            approved, execution = await BACKGROUND_GOAL_COORDINATOR.execute_approved_action(
                delivery_id=linked_delivery_id,
                approval_id=approval_id,
                approve_operation=approve,
                execute_operation=execute_approved,
            )
        except BackgroundGoalDeliveryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except (AgentGatewayError, AgentGoalServiceError) as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        payload = {
            "ok": bool(approved.get("ok")),
            "approval": approved.get("approval"),
            "execution": execution,
        }
        payload["goalDelivery"] = ensure_dict(execution).get("goalDelivery") or linked
    else:
        def approve_and_execute() -> dict[str, Any]:
            approved = approve()
            execution = execute_approved(approved)
            return {
                "ok": bool(approved.get("ok")),
                "approval": approved.get("approval"),
                "execution": execution or None,
            }

        try:
            payload = await asyncio.to_thread(approve_and_execute)
        except AgentGatewayError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    if linked_delivery_id:
        await broadcast_background_goal_state({})
    return payload


@app.post("/api/app/agent/approvals/{approval_id}/reject")
async def app_agent_reject(
    approval_id: str,
    request: AgentApprovalScopeRequest | None = None,
) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.reject(
            approval_id,
            expected_project_root=((request.expected_project_root if request else "") or ""),
            global_only=bool(request.global_only if request else True),
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    denied_goal = payload.get("goalDelivery")
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    if denied_goal is not None:
        await broadcast_background_goal_state({})
    return payload


@app.post("/api/app/agent/approvals/{approval_id}/revision")
async def app_agent_request_approval_revision(approval_id: str, request: AgentApprovalRevisionRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.request_approval_revision(
            approval_id,
            reason=request.reason,
            note=request.note,
            expected_project_root=request.expected_project_root or "",
            global_only=bool(request.global_only),
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    denied_goal = payload.get("goalDelivery")
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    if denied_goal is not None:
        await broadcast_background_goal_state({})
    return payload


@app.get("/api/app/agent-notes")
def read_agent_notes() -> dict[str, Any]:
    path = AGENT_GATEWAY.user_constraints_path
    content = ""
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"无法读取 AGENTS.md: {exc}") from exc
    return {"ok": True, "path": str(path), "exists": path.exists(), "content": content}


@app.post("/api/app/agent-notes")
async def write_agent_notes(request: AgentNotesRequest) -> dict[str, Any]:
    path = AGENT_GATEWAY.user_constraints_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(request.content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法写入 AGENTS.md: {exc}") from exc
    await EVENT_BUS.broadcast("agentNotesUpdated", {"path": str(path), "bytes": len(request.content.encode("utf-8"))})
    return {"ok": True, "path": str(path), "bytes": len(request.content.encode("utf-8"))}


AGENT_GATEWAY.bind_project_chat_checkpoint_lock(CHAT_TRANSCRIPTS_LOCK)


def chat_transcripts_path() -> Path:
    return AGENT_GATEWAY.user_constraints_path.parent / "chat-transcripts.json"


def chat_project_index_path() -> Path:
    return AGENT_GATEWAY.user_constraints_path.parent / "chat-projects.json"


def resolve_chat_project_root(project_path: str) -> Path | None:
    raw = str(project_path or "").strip()
    if not raw:
        return None
    try:
        root = Path(raw).expanduser()
    except (OSError, ValueError):
        return None
    if not root.is_absolute():
        return None
    try:
        anchor = Path(root.anchor)
        if not root.anchor or path_has_link_like_segment(root, anchor):
            return None
        if not root.exists() or not root.is_dir():
            return None
        return root.resolve()
    except OSError:
        return None


def project_chat_transcripts_path(project_path: str) -> Path | None:
    root = resolve_chat_project_root(project_path)
    if root is None:
        return None
    target = root / ".vrcforge" / "chat-transcripts.json"
    if path_has_link_like_segment(target, root):
        return None
    return target


def normalize_chat_project_key(project_path: str) -> str:
    root = resolve_chat_project_root(project_path)
    if root is None:
        return ""
    return str(root).replace("/", "\\").lower()


def remember_chat_project_path(project_path: str) -> None:
    """Keep a bounded set of project chat sources actually requested this run."""

    root = resolve_chat_project_root(project_path)
    if root is None:
        return
    key = normalize_chat_project_key(str(root))
    if not key:
        return
    CHAT_REQUESTED_PROJECT_PATHS.pop(key, None)
    CHAT_REQUESTED_PROJECT_PATHS[key] = str(root)
    while len(CHAT_REQUESTED_PROJECT_PATHS) > CHAT_REQUESTED_PROJECT_PATH_LIMIT:
        oldest = next(iter(CHAT_REQUESTED_PROJECT_PATHS))
        CHAT_REQUESTED_PROJECT_PATHS.pop(oldest, None)


def load_chat_project_index_paths() -> list[str]:
    path = chat_project_index_path()
    if path_has_link_like_segment(path, path.parent):
        return []
    if not path.exists():
        return []
    try:
        if path.stat().st_size > CHAT_TRANSCRIPTS_MAX_BYTES:
            return []
        payload = load_strict_json(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    project_paths = payload.get("projectPaths")
    if not isinstance(project_paths, list):
        return []
    return [str(item) for item in project_paths if isinstance(item, str) and item.strip()]


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    try:
        with temp_path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    atomic_write_bytes(path, content.encode("utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def snapshot_chat_storage_files(paths: list[Path]) -> tuple[dict[Path, bytes | None], dict[Path, bool]]:
    """Capture exact bytes before a multi-file chat transaction starts."""

    snapshots: dict[Path, bytes | None] = {}
    parent_existence: dict[Path, bool] = {}
    for path in dict.fromkeys(paths):
        parent_existence[path.parent] = path.parent.exists()
        try:
            snapshots[path] = path.read_bytes()
        except FileNotFoundError:
            snapshots[path] = None
    return snapshots, parent_existence


def restore_chat_storage_files(
    snapshots: dict[Path, bytes | None],
    parent_existence: dict[Path, bool],
) -> bool:
    """Best-effort exact rollback; return false if any restoration step fails."""

    restored = True
    for path, original in snapshots.items():
        if original is None:
            continue
        try:
            atomic_write_bytes(path, original)
        except OSError:
            restored = False
    for path, original in snapshots.items():
        if original is not None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            restored = False
    for parent, existed in parent_existence.items():
        if existed or parent.name != ".vrcforge":
            continue
        try:
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            restored = False
    return restored


def chat_store_target(path: Path, *, scope: str, project_path: str = "") -> SessionStoreTarget:
    if scope == "project":
        project_root = resolve_chat_project_root(project_path) or path.parent.parent
        project_key = normalize_chat_project_key(project_path) or str(project_root).lower()
        suffix = hashlib.sha256(project_key.encode("utf-8", errors="replace")).hexdigest()[:16]
        return SessionStoreTarget(
            store_id=f"chat.project.{suffix}",
            path=path,
            scope="project_owned",
            format="json",
            required_list_field="chats",
            required_list_item_kind="chat",
            document_version_field="version",
            known_document_versions=(1,),
            guard_root=project_root,
            max_bytes=CHAT_TRANSCRIPTS_MAX_BYTES,
            max_list_items=CHAT_TRANSCRIPTS_MAX_CHATS,
        )
    return SessionStoreTarget(
        store_id="chat.app",
        path=path,
        scope="app_owned",
        format="json",
        required_list_field="chats",
        required_list_item_kind="chat",
        document_version_field="version",
        known_document_versions=(1,),
        guard_root=path.parent,
        max_bytes=CHAT_TRANSCRIPTS_MAX_BYTES,
        max_list_items=CHAT_TRANSCRIPTS_MAX_CHATS,
    )


def chat_project_index_target() -> SessionStoreTarget:
    return SessionStoreTarget(
        store_id="chat.project-index",
        path=chat_project_index_path(),
        scope="app_owned",
        format="json",
        required_list_field="projectPaths",
        required_list_item_kind="nonempty_string",
        document_version_field="version",
        known_document_versions=(1,),
        guard_root=chat_project_index_path().parent,
        max_bytes=CHAT_TRANSCRIPTS_MAX_BYTES,
        max_list_items=CHAT_REQUESTED_PROJECT_PATH_LIMIT,
    )


def chat_recovery_marker(scan: dict[str, Any], *, scope: str) -> dict[str, Any]:
    return {
        "storeId": str(scan.get("storeId") or ""),
        "scope": scope,
        "status": str(scan.get("status") or "error"),
        "reason": str(scan.get("reason") or "read_error"),
        "requiresApproval": bool(scan.get("requiresApproval")),
        "invalidCount": int(scan.get("invalidCount") or 0),
        "quarantinedCount": int(scan.get("quarantinedCount") or 0),
    }


def chat_recovered_marker(result: dict[str, Any], *, scope: str) -> dict[str, Any]:
    invalid_count = int(result.get("invalidCount") or 0)
    return {
        "storeId": str(result.get("storeId") or ""),
        "scope": scope,
        "status": "recovered",
        "reason": str(result.get("reason") or "recovered"),
        "requiresApproval": False,
        "invalidCount": invalid_count,
        "quarantinedCount": invalid_count,
    }


def load_chat_transcript_file(
    target: SessionStoreTarget,
    *,
    scope: str,
    self_heal_app_owned: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    """Load one chat source without allowing it to poison healthy sources."""

    scan = scan_session_store(target)
    recovery_notice: dict[str, Any] | None = None
    if self_heal_app_owned and target.scope == "app_owned" and scan.get("status") == "needs_repair":
        repaired = repair_session_store(target, scan)
        if repaired.get("status") in {"repaired", "quarantined"}:
            recovery_notice = chat_recovered_marker(repaired, scope=scope)
            scan = scan_session_store(target)
    source = {
        "storeId": scan.get("storeId"),
        "scope": scope,
        "exists": scan.get("exists"),
        "digest": scan.get("digest"),
        "status": scan.get("status"),
    }
    if scan.get("status") == "missing":
        return [], source, recovery_notice
    if scan.get("status") != "ok":
        return [], source, chat_recovery_marker(scan, scope=scope)
    try:
        payload = load_strict_json(target.path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        marker = {**scan, "status": "needs_repair", "reason": "snapshot_changed"}
        source["status"] = "needs_repair"
        return [], source, chat_recovery_marker(marker, scope=scope)
    if not isinstance(payload, dict) or not isinstance(payload.get("chats"), list):
        marker = {**scan, "status": "unsupported", "reason": "invalid_record_shape"}
        source["status"] = "unsupported"
        return [], source, chat_recovery_marker(marker, scope=scope)
    chats = [
        sanitized
        for item in payload["chats"]
        if isinstance(item, dict)
        for sanitized in [sanitize_chat_transcript(item)]
        if not is_empty_chat_transcript(sanitized)
    ]
    return chats, source, recovery_notice


def inspect_chat_project_index(*, self_heal_app_owned: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None]:
    target = chat_project_index_target()
    scan = scan_session_store(target)
    recovery_notice: dict[str, Any] | None = None
    if self_heal_app_owned and scan.get("status") == "needs_repair":
        repaired = repair_session_store(target, scan)
        if repaired.get("status") in {"repaired", "quarantined"}:
            recovery_notice = chat_recovered_marker(repaired, scope="app-index")
            scan = scan_session_store(target)
    source = {
        "storeId": scan.get("storeId"),
        "scope": "app-index",
        "exists": scan.get("exists"),
        "digest": scan.get("digest"),
        "status": scan.get("status"),
    }
    if scan.get("status") == "missing":
        return source, recovery_notice
    if scan.get("status") != "ok":
        return source, chat_recovery_marker(scan, scope="app-index")
    try:
        payload = load_strict_json(target.path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        marker = {**scan, "status": "needs_repair", "reason": "snapshot_changed"}
        source["status"] = "needs_repair"
        return source, chat_recovery_marker(marker, scope="app-index")
    if not isinstance(payload, dict) or not isinstance(payload.get("projectPaths"), list):
        marker = {**scan, "status": "unsupported", "reason": "invalid_record_shape"}
        source["status"] = "unsupported"
        return source, chat_recovery_marker(marker, scope="app-index")
    return source, recovery_notice


def chat_record_fingerprint(chat: dict[str, Any]) -> str:
    encoded = json.dumps(chat, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@app.get("/api/app/chats")
def read_chat_transcripts(request: Request) -> dict[str, Any]:
    with CHAT_TRANSCRIPTS_LOCK:
        app_path = chat_transcripts_path()
        app_target = chat_store_target(app_path, scope="app")
        chats, app_source, app_recovery = load_chat_transcript_file(
            app_target,
            scope="app",
            self_heal_app_owned=True,
        )
        sources: list[dict[str, Any]] = [{**app_source, "path": str(app_path)}]
        recoveries: list[dict[str, Any]] = [app_recovery] if app_recovery else []
        index_source, index_recovery = inspect_chat_project_index(self_heal_app_owned=True)
        sources.append({**index_source, "path": str(chat_project_index_path())})
        if index_recovery:
            recoveries.append(index_recovery)
        seen_chat_fingerprints = {
            str(item.get("id") or ""): chat_record_fingerprint(item)
            for item in chats
            if str(item.get("id") or "")
        }
        conflicting_chat_ids: set[str] = set()
        requested_project_paths = request.query_params.getlist("projectPath")
        for requested_project_path in requested_project_paths:
            remember_chat_project_path(requested_project_path)
        index_recovery_blocking = bool(index_recovery and index_recovery.get("status") != "recovered")
        indexed_project_paths = [] if index_recovery_blocking else load_chat_project_index_paths()
        indexed_project_values = {str(value).strip().casefold() for value in indexed_project_paths if str(value).strip()}
        project_sources = list(dict.fromkeys([*indexed_project_paths, *requested_project_paths]))
        for project_path in project_sources:
            path = project_chat_transcripts_path(project_path)
            if path is None:
                project_root = resolve_chat_project_root(project_path)
                if project_root is not None:
                    unsafe_path = project_root / ".vrcforge" / "chat-transcripts.json"
                    unsafe_target = chat_store_target(unsafe_path, scope="project", project_path=str(project_root))
                    unsafe_scan = scan_session_store(unsafe_target)
                    unsafe_source = {
                        "storeId": unsafe_scan.get("storeId"),
                        "scope": "project",
                        "exists": unsafe_scan.get("exists"),
                        "digest": unsafe_scan.get("digest"),
                        "status": unsafe_scan.get("status"),
                        "projectPath": project_path,
                        "path": str(unsafe_path),
                        "count": 0,
                    }
                    sources.append(unsafe_source)
                    recoveries.append(chat_recovery_marker(unsafe_scan, scope="project"))
                elif str(project_path or "").strip():
                    unavailable_store_id = f"chat.unavailable.{hashlib.sha256(str(project_path).encode('utf-8', errors='replace')).hexdigest()[:16]}"
                    unavailable_source = {
                        "storeId": unavailable_store_id,
                        "scope": "project",
                        "exists": False,
                        "digest": "",
                        "status": "unsupported",
                        "reason": "project_unavailable",
                        "projectPath": project_path,
                        "unavailable": True,
                        "indexed": str(project_path).strip().casefold() in indexed_project_values,
                        "count": 0,
                    }
                    sources.append(unavailable_source)
                    if unavailable_source["indexed"]:
                        recoveries.append(
                            {
                                "storeId": unavailable_store_id,
                                "scope": "project",
                                "status": "unsupported",
                                "reason": "project_unavailable",
                                "requiresApproval": False,
                                "invalidCount": 0,
                                "quarantinedCount": 0,
                            }
                        )
                continue
            target = chat_store_target(path, scope="project", project_path=project_path)
            project_chats, project_source, project_recovery = load_chat_transcript_file(target, scope="project")
            added = 0
            source_project_path = str(resolve_chat_project_root(project_path) or project_path)
            for chat in project_chats:
                chat_id = str(chat.get("id") or "")
                # A project-owned file is authoritative for its own scope. An
                # embedded path is untrusted data and must never move a chat to
                # another project on the next whole-store save.
                chat = {**chat, "projectPath": source_project_path}
                fingerprint = chat_record_fingerprint(chat)
                previous_fingerprint = seen_chat_fingerprints.get(chat_id) if chat_id else None
                if previous_fingerprint is not None:
                    if previous_fingerprint != fingerprint and chat_id not in conflicting_chat_ids:
                        conflicting_chat_ids.add(chat_id)
                        recoveries.append(
                            {
                                "storeId": f"chat.merge.{hashlib.sha256(chat_id.encode('utf-8')).hexdigest()[:16]}",
                                "scope": "merge",
                                "status": "unsupported",
                                "reason": "duplicate_chat_id_conflict",
                                "requiresApproval": False,
                                "invalidCount": 1,
                                "quarantinedCount": 0,
                            }
                        )
                    continue
                chats.append(chat)
                if chat_id:
                    seen_chat_fingerprints[chat_id] = fingerprint
                added += 1
            sources.append(
                {
                    **project_source,
                    "projectPath": project_path,
                    "path": str(path),
                    "count": added,
                }
            )
            if project_recovery:
                recoveries.append(project_recovery)
        return {
            "ok": True,
            "path": str(app_path),
            "exists": app_path.exists(),
            "chats": chats,
            "count": len(chats),
            "sources": sources,
            "recoveries": recoveries,
            "writeBlocked": any(item.get("status") != "recovered" for item in recoveries),
        }


@app.post("/api/app/chats")
async def write_chat_transcripts(request: ChatTranscriptsRequest) -> dict[str, Any]:
    storage_result, chats, automatic_groups = await asyncio.to_thread(write_chat_transcripts_storage, request)
    # Vault 引用同步：该请求总是携带全部会话，作为活引用快照驱动 LRU 清理。
    # 清理失败绝不阻断会话保存。
    vault_retention: dict[str, Any] = {}
    try:
        vault_retention = await asyncio.to_thread(
            chat_attachment_vault_store().retain,
            collect_vault_attachment_refs(chats),
        )
    except Exception:
        vault_retention = {}
    automatic_capture: dict[str, int] = {}
    try:
        automatic_capture = await asyncio.to_thread(capture_automatic_memory_after_chat_storage, automatic_groups)
        if automatic_capture.get("acceptedCount") or automatic_capture.get("conflictCount"):
            await MEMORY_REVIEW.notify_review_changed()
    except Exception:
        automatic_capture = {}
    return {**storage_result, "vaultRetention": vault_retention, "automaticCapture": automatic_capture}


def capture_automatic_memory_after_chat_storage(
    groups: list[tuple[MemoryScope, str, list[dict[str, Any]]]],
) -> dict[str, int]:
    """The chat write has committed; failure here is deliberately non-blocking."""
    totals = {"eligibleCount": 0, "acceptedCount": 0, "conflictCount": 0}
    for scope, project_root, grouped_chats in groups:
        result = MEMORY_REVIEW.service.capture_automatic_chat_sources(grouped_chats, scope=scope, project_root=project_root)
        for key in totals:
            totals[key] += int(result.get(key) or 0)
    return totals


def _chat_source_revision_map(request: ChatTranscriptsRequest) -> dict[str, dict[str, Any]]:
    revisions: dict[str, dict[str, Any]] = {}
    for item in request.source_revisions:
        if not isinstance(item, dict):
            continue
        store_id = str(item.get("storeId") or "").strip()
        if store_id:
            revisions[store_id] = item
    return revisions


def _assert_chat_source_writable(
    target: SessionStoreTarget,
    *,
    scope: str,
    revisions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if target.store_id == "chat.project-index":
        source, recovery = inspect_chat_project_index()
    else:
        _ignored, source, recovery = load_chat_transcript_file(target, scope=scope)
    if recovery:
        raise HTTPException(
            status_code=409,
            detail={"code": "chat_store_recovery_required", "recovery": recovery},
        )
    expected = revisions.get(target.store_id)
    if expected is None:
        if bool(source.get("exists")):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "chat_store_snapshot_changed",
                    "storeId": target.store_id,
                },
            )
    else:
        expected_exists = bool(expected.get("exists"))
        expected_digest = str(expected.get("digest") or "")
        current_exists = bool(source.get("exists"))
        current_digest = str(source.get("digest") or "")
        if expected_exists != current_exists or not hmac.compare_digest(expected_digest, current_digest):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "chat_store_snapshot_changed",
                    "storeId": target.store_id,
                },
            )
    return source


def _assert_no_current_chat_source_id_conflicts(
    app_path: Path,
    project_sources: list[tuple[Path, str]],
) -> None:
    """Fail before any write when durable sources disagree on one chat id."""

    seen: dict[str, str] = {}

    def admit(chat: dict[str, Any]) -> None:
        chat_id = str(chat.get("id") or "")
        if not chat_id:
            return
        fingerprint = chat_record_fingerprint(chat)
        previous = seen.get(chat_id)
        if previous is not None and previous != fingerprint:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "chat_store_duplicate_id_conflict",
                    "storeId": f"chat.merge.{hashlib.sha256(chat_id.encode('utf-8')).hexdigest()[:16]}",
                },
            )
        seen[chat_id] = fingerprint

    app_chats, _source, app_recovery = load_chat_transcript_file(
        chat_store_target(app_path, scope="app"),
        scope="app",
    )
    if app_recovery:
        raise HTTPException(status_code=409, detail={"code": "chat_store_recovery_required", "recovery": app_recovery})
    for chat in app_chats:
        admit(chat)

    unique_projects: dict[str, tuple[Path, str]] = {}
    for path, project_path in project_sources:
        key = normalize_chat_project_key(project_path) or str(path).casefold()
        unique_projects[key] = (path, project_path)
    for path, project_path in unique_projects.values():
        target = chat_store_target(path, scope="project", project_path=project_path)
        project_chats, _source, project_recovery = load_chat_transcript_file(target, scope="project")
        if project_recovery:
            raise HTTPException(status_code=409, detail={"code": "chat_store_recovery_required", "recovery": project_recovery})
        source_project_path = str(resolve_chat_project_root(project_path) or project_path)
        for chat in project_chats:
            admit({**chat, "projectPath": source_project_path})


def write_chat_transcripts_storage(
    request: ChatTranscriptsRequest,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[tuple[MemoryScope, str, list[dict[str, Any]]]]]:
    if len(request.chats) > CHAT_TRANSCRIPTS_MAX_CHATS:
        raise HTTPException(
            status_code=413,
            detail=f"Chat storage accepts at most {CHAT_TRANSCRIPTS_MAX_CHATS} chats; no data was written.",
        )
    invalid_chat_count = sum(1 for chat in request.chats if not is_valid_chat_record(chat))
    chat_ids = [str(chat.get("id") or "") for chat in request.chats]
    if invalid_chat_count or len(set(chat_ids)) != len(chat_ids):
        raise HTTPException(
            status_code=422,
            detail="Chat storage rejected malformed or duplicate chat records; no data was written.",
        )
    chats = [
        sanitized
        for chat in request.chats
        for sanitized in [sanitize_chat_transcript(chat)]
        if not is_empty_chat_transcript(sanitized)
    ]
    app_chats: list[dict[str, Any]] = []
    project_groups: dict[str, dict[str, Any]] = {}
    for chat in chats:
        project_path = str(chat.get("projectPath") or "").strip()
        project_key = normalize_chat_project_key(project_path)
        project_file = project_chat_transcripts_path(project_path) if project_key else None
        if project_path and not project_key:
            raise HTTPException(status_code=422, detail="Chat project path is unavailable or invalid; no data was written.")
        if project_key and project_file is None:
            raise HTTPException(status_code=409, detail="Chat project storage path is unsafe; no data was written.")
        if project_key and project_file is not None:
            group = project_groups.setdefault(project_key, {"path": project_file, "projectPath": str(resolve_chat_project_root(project_path) or project_path), "chats": []})
            group["chats"].append(chat)
        else:
            app_chats.append(chat)
    try:
        serialized = json.dumps({"version": 1, "chats": app_chats}, ensure_ascii=False, allow_nan=False)
        total_bytes = len(serialized.encode("utf-8"))
        project_serialized: list[tuple[Path, str, int]] = []
        for group in project_groups.values():
            payload = json.dumps(
                {"version": 1, "scope": "project", "chats": group["chats"]},
                ensure_ascii=False,
                allow_nan=False,
            )
            total_bytes += len(payload.encode("utf-8"))
            project_serialized.append((group["path"], payload, len(group["chats"])))
    except (TypeError, ValueError, RecursionError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Chat storage rejected a non-JSON or excessively nested value; no data was written.",
        ) from exc
    if total_bytes > CHAT_TRANSCRIPTS_MAX_BYTES:
        raise HTTPException(status_code=413, detail="会话记录超过 16MB 上限，请删除旧会话后重试。")
    app_path = chat_transcripts_path()
    with CHAT_TRANSCRIPTS_LOCK:
        revisions = _chat_source_revision_map(request)
        stale_project_sources: dict[str, tuple[str, Path]] = {}
        current_project_keys = set(project_groups.keys())
        candidate_project_paths = list(load_chat_project_index_paths())
        for revision in revisions.values():
            if (
                revision.get("scope") == "project"
                and revision.get("status") == "ok"
                and revision.get("exists") is True
                and isinstance(revision.get("projectPath"), str)
            ):
                candidate_project_paths.append(str(revision["projectPath"]))
        for project_path in candidate_project_paths:
            project_key = normalize_chat_project_key(project_path)
            project_file = project_chat_transcripts_path(project_path)
            if not project_key or project_key in current_project_keys or project_file is None:
                continue
            target = chat_store_target(project_file, scope="project", project_path=project_path)
            revision = revisions.get(target.store_id)
            if revision is not None and str(revision.get("projectPath") or ""):
                resolved_revision_root = resolve_chat_project_root(str(revision.get("projectPath") or ""))
                if resolved_revision_root is None or normalize_chat_project_key(str(resolved_revision_root)) != project_key:
                    continue
            stale_project_sources[project_key] = (project_path, project_file)
        stale_project_files = list(stale_project_sources.values())

        _assert_chat_source_writable(
            chat_store_target(app_path, scope="app"),
            scope="app",
            revisions=revisions,
        )
        _assert_chat_source_writable(
            chat_project_index_target(),
            scope="app-index",
            revisions=revisions,
        )
        for group in project_groups.values():
            _assert_chat_source_writable(
                chat_store_target(group["path"], scope="project", project_path=group["projectPath"]),
                scope="project",
                revisions=revisions,
            )
        for project_path, project_file in stale_project_files:
            _assert_chat_source_writable(
                chat_store_target(project_file, scope="project", project_path=project_path),
                scope="project",
                revisions=revisions,
            )

        _assert_no_current_chat_source_id_conflicts(
            app_path,
            [
                *[(group["path"], group["projectPath"]) for group in project_groups.values()],
                *[(project_file, project_path) for project_path, project_file in stale_project_files],
            ],
        )

        index_path = chat_project_index_path()
        mutation_paths = [
            app_path,
            *[path for path, _payload, _count in project_serialized],
            *[path for _project_path, path in stale_project_files],
            index_path,
        ]
        storage_snapshot: dict[Path, bytes | None] = {}
        parent_existence: dict[Path, bool] = {}
        try:
            storage_snapshot, parent_existence = snapshot_chat_storage_files(mutation_paths)
            atomic_write_text(app_path, serialized)
            for path, payload, _count in project_serialized:
                atomic_write_text(path, payload)
            for _project_path, path in stale_project_files:
                if path.exists():
                    path.unlink()
            atomic_write_json(
                index_path,
                {"version": 1, "projectPaths": [str(group["projectPath"]) for group in project_groups.values()]},
            )
        except OSError as exc:
            rollback_ok = restore_chat_storage_files(storage_snapshot, parent_existence)
            detail = (
                "Unable to write chat storage; all source files were restored."
                if rollback_ok
                else "Unable to write chat storage and exact rollback could not be verified; run Doctor before retrying."
            )
            raise HTTPException(status_code=500, detail=detail) from exc
        for _project_path, path in stale_project_files:
            parent = path.parent
            try:
                if parent.name == ".vrcforge" and parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass

        source_revisions: list[dict[str, Any]] = []
        _app_chats, app_source, _app_recovery = load_chat_transcript_file(
            chat_store_target(app_path, scope="app"),
            scope="app",
        )
        source_revisions.append(app_source)
        index_source, _index_recovery = inspect_chat_project_index()
        source_revisions.append(index_source)
        for group in project_groups.values():
            _group_chats, source, _recovery = load_chat_transcript_file(
                chat_store_target(group["path"], scope="project", project_path=group["projectPath"]),
                scope="project",
            )
            source_revisions.append(source)

    project_paths = [{"path": str(path), "count": count} for path, _payload, count in project_serialized]
    automatic_groups = [(MemoryScope("user", "user"), "", app_chats)]
    automatic_groups.extend(
        (
            MemoryScope("project", project_scope_key(str(group["projectPath"]))),
            str(group["projectPath"]),
            list(group["chats"]),
        )
        for group in project_groups.values()
    )
    return (
        {
            "ok": True,
            "path": str(app_path),
            "count": len(chats),
            "appCount": len(app_chats),
            "projectPaths": project_paths,
            "sourceRevisions": source_revisions,
        },
        chats,
        automatic_groups,
    )


def is_empty_chat_transcript(chat: dict[str, Any]) -> bool:
    items = chat.get("items")
    title = str(chat.get("title") or "").strip()
    session_id = str(chat.get("sessionId") or chat.get("session_id") or "").strip()
    return isinstance(items, list) and not items and not title and not session_id


def sanitize_chat_transcript(chat: dict[str, Any]) -> dict[str, Any]:
    items = chat.get("items")
    if not isinstance(items, list):
        return chat
    durable_items = [
        item
        for item in items
        if not isinstance(item, dict) or str(item.get("type") or "") != "streaming"
    ]
    return chat if len(durable_items) == len(items) else {**chat, "items": durable_items}


# ---- chat attachment vault (1.3.2) ---------------------------------------
# 二进制体不进 prompt/transcript：字节落 chat_attachment_vault 域模块管理的
# 本地文件库，durable message 只带 metadata + payloadHash。这里只做薄接线。

_CHAT_ATTACHMENT_VAULT: ChatAttachmentVault | None = None
_CHAT_ATTACHMENT_VAULT_LOCK = Lock()
_CHAT_ATTACHMENT_UPLOADS: dict[str, dict[str, Any]] = {}
_CHAT_ATTACHMENT_UPLOAD_LOCK = Lock()
CHAT_ATTACHMENT_UPLOAD_CHUNK_MAX_BYTES = 8 * 1024 * 1024
CHAT_ATTACHMENT_UPLOAD_STALE_SECONDS = 60 * 60
CHAT_ATTACHMENT_UPLOAD_MAX_SESSIONS = 16
CHAT_ATTACHMENT_UPLOAD_MAX_STAGED_BYTES = 2 * ARCHIVE_MAX_BYTES
CHAT_ATTACHMENT_UPLOAD_MAX_SESSIONS_PER_CHAT = 4


def chat_attachment_vault_store() -> ChatAttachmentVault:
    global _CHAT_ATTACHMENT_VAULT
    root = AGENT_GATEWAY.user_constraints_path.parent / "chat-attachments"
    if _CHAT_ATTACHMENT_VAULT is None or _CHAT_ATTACHMENT_VAULT.root != root:
        with _CHAT_ATTACHMENT_VAULT_LOCK:
            if _CHAT_ATTACHMENT_VAULT is None or _CHAT_ATTACHMENT_VAULT.root != root:
                _CHAT_ATTACHMENT_VAULT = ChatAttachmentVault(root)
                _cleanup_stale_chat_attachment_upload_files(root, time.time())
    return _CHAT_ATTACHMENT_VAULT


def _cleanup_stale_chat_attachment_upload_files(root: Path, now: float) -> None:
    uploads_dir = root / "uploads"
    if not uploads_dir.is_dir():
        return
    for path in uploads_dir.glob("*.partial"):
        try:
            if now - path.stat().st_mtime > CHAT_ATTACHMENT_UPLOAD_STALE_SECONDS:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _cleanup_stale_chat_attachment_uploads(now: float) -> None:
    stale_ids = [
        upload_id
        for upload_id, entry in _CHAT_ATTACHMENT_UPLOADS.items()
        if now - float(entry.get("updatedAt") or entry.get("createdAt") or 0.0) > CHAT_ATTACHMENT_UPLOAD_STALE_SECONDS
    ]
    for upload_id in stale_ids:
        entry = _CHAT_ATTACHMENT_UPLOADS.pop(upload_id, None)
        if entry:
            try:
                Path(str(entry["path"])).unlink(missing_ok=True)
            except OSError:
                pass
    _cleanup_stale_chat_attachment_upload_files(chat_attachment_vault_store().root, now)


def _chat_attachment_upload_path(upload_id: str) -> Path:
    uploads_dir = chat_attachment_vault_store().root / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    return uploads_dir / f"{upload_id}.partial"


def _take_chat_attachment_upload(upload_id: str) -> dict[str, Any]:
    key = str(upload_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
        raise HTTPException(status_code=400, detail="Invalid chat attachment upload id.")
    with _CHAT_ATTACHMENT_UPLOAD_LOCK:
        entry = _CHAT_ATTACHMENT_UPLOADS.pop(key, None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Chat attachment upload session was not found.")
    return entry


def collect_vault_attachment_refs(chats: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Collect every payloadHash a durable transcript still references, per chat.

    保守收集：user items 的 attachments、compactedAttachmentRefs、
    attachmentPayloads 键全算活引用——多收无害（retain 只按索引匹配），
    漏收会误删仍被引用的附件体。
    """

    refs: dict[str, set[str]] = {}
    for chat in chats:
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            continue
        hashes: set[str] = set()
        items = chat.get("items") if isinstance(chat.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            attachments = item.get("attachments") if isinstance(item.get("attachments"), list) else []
            for attachment in attachments:
                if isinstance(attachment, dict):
                    value = str(attachment.get("payloadHash") or "").strip().lower()
                    if is_vault_payload_hash(value):
                        hashes.add(value)
                    vault_value = str(attachment.get("vaultPayloadHash") or "").strip().lower()
                    if is_vault_payload_hash(vault_value):
                        hashes.add(vault_value)
        compacted = chat.get("compactedAttachmentRefs") if isinstance(chat.get("compactedAttachmentRefs"), list) else []
        for reference in compacted:
            if isinstance(reference, dict):
                value = str(reference.get("payloadHash") or "").strip().lower()
                if is_vault_payload_hash(value):
                    hashes.add(value)
                vault_value = str(reference.get("vaultPayloadHash") or "").strip().lower()
                if is_vault_payload_hash(vault_value):
                    hashes.add(vault_value)
        payloads = chat.get("attachmentPayloads") if isinstance(chat.get("attachmentPayloads"), dict) else {}
        for key in payloads:
            value = str(key or "").strip().lower()
            if is_vault_payload_hash(value):
                hashes.add(value)
        refs[chat_id] = hashes
    return refs


@app.post("/api/app/chat-attachments/uploads")
def app_begin_chat_attachment_upload(request: ChatAttachmentUploadBeginRequest) -> dict[str, Any]:
    now = time.time()
    upload_id = secrets.token_urlsafe(24)
    path = _chat_attachment_upload_path(upload_id)
    with _CHAT_ATTACHMENT_UPLOAD_LOCK:
        _cleanup_stale_chat_attachment_uploads(now)
        if len(_CHAT_ATTACHMENT_UPLOADS) >= CHAT_ATTACHMENT_UPLOAD_MAX_SESSIONS:
            raise HTTPException(status_code=429, detail="Too many active chat attachment uploads.")
        staged_bytes = sum(int(entry.get("size") or 0) for entry in _CHAT_ATTACHMENT_UPLOADS.values())
        if staged_bytes + request.size > CHAT_ATTACHMENT_UPLOAD_MAX_STAGED_BYTES:
            raise HTTPException(status_code=429, detail="Active chat attachment uploads exceed the staging quota.")
        active_for_chat = sum(
            1 for entry in _CHAT_ATTACHMENT_UPLOADS.values() if str(entry.get("chatId") or "") == request.chat_id
        )
        if active_for_chat >= CHAT_ATTACHMENT_UPLOAD_MAX_SESSIONS_PER_CHAT:
            raise HTTPException(status_code=429, detail="This chat has too many active attachment uploads.")
        try:
            path.touch(exist_ok=False)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not stage chat attachment upload: {exc}") from exc
        _CHAT_ATTACHMENT_UPLOADS[upload_id] = {
            "path": str(path),
            "name": request.name,
            "chatId": request.chat_id,
            "declaredType": request.declared_type,
            "size": request.size,
            "received": 0,
            "createdAt": now,
            "updatedAt": now,
        }
    return {
        "ok": True,
        "uploadId": upload_id,
        "chunkSize": CHAT_ATTACHMENT_UPLOAD_CHUNK_MAX_BYTES,
        "size": request.size,
    }


@app.post("/api/app/chat-attachments/uploads/{upload_id}/chunks")
async def app_append_chat_attachment_upload(upload_id: str, request: Request, offset: int = 0) -> dict[str, Any]:
    chunk = await request.body()
    if not chunk:
        raise HTTPException(status_code=400, detail="Chat attachment upload chunk is empty.")
    if len(chunk) > CHAT_ATTACHMENT_UPLOAD_CHUNK_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Chat attachment upload chunk exceeds the transport cap.")
    return await asyncio.to_thread(_append_chat_attachment_upload_chunk, upload_id, offset, chunk)


def _append_chat_attachment_upload_chunk(upload_id: str, offset: int, chunk: bytes) -> dict[str, Any]:
    """Append one bounded chunk without blocking the FastAPI event loop on disk I/O."""

    with _CHAT_ATTACHMENT_UPLOAD_LOCK:
        entry = _CHAT_ATTACHMENT_UPLOADS.get(upload_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Chat attachment upload session was not found.")
        received = int(entry["received"])
        total_size = int(entry["size"])
        if offset != received:
            raise HTTPException(status_code=409, detail=f"Upload offset mismatch: expected {received}, received {offset}.")
        if received + len(chunk) > total_size:
            raise HTTPException(status_code=413, detail="Chat attachment upload exceeds its declared size.")
        try:
            with Path(str(entry["path"])).open("ab") as handle:
                handle.write(chunk)
                handle.flush()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not append chat attachment upload: {exc}") from exc
        entry["received"] = received + len(chunk)
        entry["updatedAt"] = time.time()
        return {"ok": True, "uploadId": upload_id, "received": entry["received"], "size": total_size}


@app.post("/api/app/chat-attachments/uploads/finish")
def app_finish_chat_attachment_upload(request: ChatAttachmentUploadFinishRequest) -> Any:
    entry = _take_chat_attachment_upload(request.upload_id)
    path = Path(str(entry["path"]))
    try:
        if int(entry["received"]) != int(entry["size"]):
            raise HTTPException(
                status_code=409,
                detail=f"Chat attachment upload is incomplete ({entry['received']}/{entry['size']} bytes).",
            )
        attachment = chat_attachment_vault_store().ingest_file(
            source_path=path,
            name=str(entry["name"]),
            declared_type=str(entry["declaredType"]),
            chat_id=str(entry["chatId"]),
        )
    except ChatAttachmentVaultError as exc:
        path.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "reason": exc.reason, "error": str(exc)}, status_code=exc.status_code)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"ok": True, "attachment": attachment}


@app.post("/api/app/chat-attachments/uploads/abort")
def app_abort_chat_attachment_upload(request: ChatAttachmentUploadFinishRequest) -> dict[str, Any]:
    try:
        entry = _take_chat_attachment_upload(request.upload_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return {"ok": True, "aborted": False}
        raise
    Path(str(entry["path"])).unlink(missing_ok=True)
    return {"ok": True, "aborted": True}


@app.get("/api/app/chat-attachments/{payload_hash}")
def app_read_chat_attachment(payload_hash: str) -> dict[str, Any]:
    try:
        resolved = chat_attachment_vault_store().resolve(payload_hash)
    except ChatAttachmentVaultError as exc:
        raise HTTPException(status_code=exc.status_code, detail=f"{exc} (reason: {exc.reason})") from exc
    if resolved is None:
        raise HTTPException(status_code=404, detail="Chat attachment was not found in the local vault.")
    return {"ok": True, "attachment": resolved}


@app.post("/api/app/chat-attachments/import")
async def app_request_chat_attachment_import(request: ChatAttachmentImportRequest) -> dict[str, Any]:
    vault = chat_attachment_vault_store()
    try:
        resolved = await asyncio.to_thread(vault.resolve, request.payload_hash)
    except ChatAttachmentVaultError as exc:
        raise HTTPException(status_code=exc.status_code, detail=f"{exc} (reason: {exc.reason})") from exc
    if resolved is None:
        raise HTTPException(status_code=404, detail="Chat attachment was not found in the local vault.")
    params = request.model_dump(by_alias=True)
    try:
        if resolved["category"] == "archive":
            # 归档物化复用既有 outfit-import 写通道（vault 文件名保留扩展名，
            # 计划器/导入器按磁盘路径工作，无需感知 vault）。
            plan_params = {**params, "packagePath": resolved["path"]}
            guard = await asyncio.to_thread(
                guard_vault_archive,
                Path(str(resolved["path"])),
                str(resolved["kind"]),
            )
            preview = await asyncio.to_thread(plan_outfit_import_sync, plan_params)
            plan_payload = preview.get("plan") if isinstance(preview.get("plan"), dict) else {}
            if not preview.get("ok") or not plan_payload.get("readyToApply"):
                if resolved["kind"] != "zip":
                    raise HTTPException(status_code=400, detail=preview.get("error") or "Outfit import plan is not ready to apply.")
                preview = {
                    "ok": True,
                    "schema": CHAT_ATTACHMENT_INSPECTION_SCHEMA,
                    "archiveGuard": guard,
                    "chatAttachment": {key: resolved[key] for key in ("payloadHash", "name", "size", "kind", "category")},
                    "plan": {
                        "kind": "managed_zip_extract",
                        "readyToApply": True,
                        "targetFolder": request.target_folder or "Assets/VRCForge/Imports",
                    },
                }
            payload = AGENT_GATEWAY.create_apply_request(
                {
                    "target_tool": "vrcforge_import_chat_archive",
                    "arguments": params,
                    "reason": f"Import chat attachment {resolved['name']} through the supervised outfit-import lane.",
                    "preview": {**preview, "chatAttachment": {key: resolved[key] for key in ("payloadHash", "name", "size", "kind", "category")}},
                    "agent_name": "desktop-agent",
                }
            )
        else:
            preview = {
                "ok": True,
                "schema": CHAT_ATTACHMENT_INSPECTION_SCHEMA,
                "chatAttachment": {key: resolved[key] for key in ("payloadHash", "name", "size", "kind", "category")},
                "image": await asyncio.to_thread(
                    inspect_image_bytes,
                    Path(resolved["path"]),
                    resolved["kind"],
                    resolved["size"],
                ),
                "plan": {
                    "kind": "chat_image_copy",
                    "readyToApply": True,
                    "targetFolder": request.target_folder or "Assets/VRCForge/Imports",
                },
            }
            payload = AGENT_GATEWAY.create_apply_request(
                {
                    "target_tool": "vrcforge_import_chat_image",
                    "arguments": {
                        "payloadHash": resolved["payloadHash"],
                        "projectPath": request.project_path,
                        "targetFolder": request.target_folder or "Assets/VRCForge/Imports",
                    },
                    "reason": f"Copy chat image {resolved['name']} into the Unity project imports folder.",
                    "preview": preview,
                    "agent_name": "desktop-agent",
                }
            )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


def inspect_chat_attachment_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    payload_hash = str(params.get("payloadHash") or params.get("payload_hash") or "").strip().lower()
    if not payload_hash:
        raise AgentGatewayError("payloadHash is required.", status_code=400)
    try:
        resolved = chat_attachment_vault_store().resolve(payload_hash)
    except ChatAttachmentVaultError as exc:
        raise AgentGatewayError(f"{exc} (reason: {exc.reason})", status_code=exc.status_code) from exc
    if resolved is None:
        raise AgentGatewayError("Chat attachment was not found in the local vault.", status_code=404)
    path = Path(str(resolved["path"]))
    result: dict[str, Any] = {
        "ok": True,
        "schema": CHAT_ATTACHMENT_INSPECTION_SCHEMA,
        "summary": f"Vault attachment {resolved['name']} is {resolved['kind']} ({resolved['size']} bytes).",
        "attachment": {key: resolved[key] for key in ("payloadHash", "name", "size", "type", "kind", "category", "extension")},
    }
    try:
        if resolved["category"] == "image":
            result["image"] = inspect_image_bytes(path, str(resolved["kind"]), int(resolved["size"]))
            dimensions = result["image"]
            if dimensions.get("width") and dimensions.get("height"):
                result["summary"] += f" Dimensions: {dimensions['width']}x{dimensions['height']}."
            return result
        result["archiveGuard"] = guard_vault_archive(path, str(resolved["kind"]))
        result["entryCount"] = int(result["archiveGuard"].get("entryCount") or 0)
        result["totalUncompressedBytes"] = int(result["archiveGuard"].get("totalUncompressedBytes") or 0)
        entry_path = str(params.get("entryPath") or params.get("entry_path") or "").strip()
        if entry_path:
            result["entry"] = extract_archive_entry_text(path, str(resolved["kind"]), entry_path)
            result["summary"] = f"Read bounded text from archive entry {entry_path}."
            result["summaryText"] = str(result["entry"].get("text") or "")
        else:
            max_entries = max(1, min(int(params.get("maxEntries") or params.get("max_entries") or 2000), 10000))
            result["listing"] = inspect_outfit_package(str(path), max_entries=max_entries)
            listing_summary = result["listing"].get("summary") if isinstance(result["listing"], dict) else {}
            if isinstance(listing_summary, dict):
                for key, value in listing_summary.items():
                    if key.lower().endswith("count") and isinstance(value, int):
                        result[key] = value
            result["summary"] = f"Guarded archive listing contains {result['entryCount']} entries."
            result["summaryText"] = _chat_attachment_listing_summary(result["listing"])
    except ChatAttachmentVaultError as exc:
        raise AgentGatewayError(f"{exc} (reason: {exc.reason})", status_code=exc.status_code) from exc
    return result


def _chat_attachment_listing_summary(listing: Any) -> str:
    if not isinstance(listing, dict):
        return ""
    names: list[str] = []
    for field in ("unityPackages", "prefabCandidates", "textures", "materials", "models"):
        values = listing.get(field)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                name = str(item.get("path") or item.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
            if len(names) >= 24:
                break
        if len(names) >= 24:
            break
    return "Detected import candidates:\n" + "\n".join(f"- {name}" for name in names) if names else "No supported import candidates detected."


def prepare_import_chat_image_request(
    arguments: dict[str, Any], preview: Any,
) -> tuple[dict[str, Any], Any]:
    """Bind one vault image and one currently absent Assets target before approval."""
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
        raise RuntimeError("Caller may not provide the reserved prepared Unity execution key.")
    params = dict(arguments or {})
    payload_hash = str(params.get("payloadHash") or params.get("payload_hash") or "").strip().lower()
    if not payload_hash:
        raise AgentGatewayError("payloadHash is required.", status_code=400)
    try:
        resolved = chat_attachment_vault_store().resolve(payload_hash)
    except ChatAttachmentVaultError as exc:
        raise AgentGatewayError(f"{exc} (reason: {exc.reason})", status_code=exc.status_code) from exc
    if resolved is None:
        raise AgentGatewayError("Chat attachment was not found in the local vault.", status_code=404)
    if resolved["category"] != "image":
        raise AgentGatewayError("vrcforge_import_chat_image only accepts image attachments; archives go through the outfit-import lane.", status_code=400)
    project_root = _resolve_unity_project_root_for_import(params, {})
    target_folder = str(params.get("targetFolder") or params.get("target_folder") or "Assets/VRCForge/Imports")
    target_root = _resolve_import_target_folder(project_root, target_folder)
    source_identity, source_sha256 = capture_regular_file(Path(str(resolved["path"])), label="Chat attachment")
    if source_sha256 != payload_hash or int(source_identity["size"]) != int(resolved["size"]):
        raise AgentGatewayError("Chat attachment changed before approval.", status_code=409)
    stem = sanitize_artifact_name(Path(str(resolved["name"])).stem) or "chat-image"
    extension = str(resolved["extension"])
    filename = f"{stem}{extension}"
    candidate = target_root / filename
    if candidate.exists() or candidate.is_symlink():
        filename = f"{stem}_{payload_hash[:8]}{extension}"
    target_facts = prepare_project_asset_target(project_root, target_folder, filename)
    prepared = {
        "payloadHash": payload_hash,
        "projectPath": str(project_root),
        "targetFolder": target_folder,
    }
    refresh_arguments = {
        "projectPath": str(project_root),
        "resolvePackages": False,
        "packageResolveTimeoutSeconds": 120,
    }
    return install_prepared_calls(
        prepared,
        [("vrc_refresh_asset_database", refresh_arguments)],
        {
            "attachment": {key: resolved[key] for key in ("payloadHash", "name", "size", "kind", "extension")},
            "sourceIdentity": source_identity,
            "sourceSha256": source_sha256,
            "target": target_facts,
        },
    ), preview


def import_chat_image_sync(params: dict[str, Any]) -> dict[str, Any]:
    """Copy only the approval-sealed vault image into its exact Assets target."""
    evidence = prepared_evidence(params)
    if not isinstance(evidence, dict):
        raise RuntimeError("Prepared chat image evidence is invalid.")
    attachment = evidence.get("attachment")
    target_facts = evidence.get("target")
    source_identity = evidence.get("sourceIdentity")
    source_sha256 = evidence.get("sourceSha256")
    if not isinstance(attachment, dict) or not isinstance(target_facts, dict) or not isinstance(source_identity, dict) or not isinstance(source_sha256, str):
        raise RuntimeError("Prepared chat image evidence is incomplete.")
    calls = build_prepared_execution_plan(params)
    project_path = str(params.get("projectPath") or "")
    expected_call = (
        "vrc_refresh_asset_database",
        {"projectPath": project_path, "resolvePackages": False, "packageResolveTimeoutSeconds": 120},
    )
    if calls != [expected_call]:
        raise RuntimeError("Prepared chat image Core call is invalid.")
    target: Path | None = None
    ownership: dict[str, Any] = {
        "schema": "vrcforge.owned-import-output.v1",
        "targetIdentity": None,
        "targetSha256": None,
        "createdDirectories": [],
    }
    try:
        target, copied_hash, ownership = copy_approved_file_create_new(
            source_identity=source_identity,
            source_sha256=source_sha256,
            project_identity=ensure_dict(target_facts.get("project") or {}),
            assets_identity=ensure_dict(target_facts.get("assets") or {}),
            parent_identities=[item for item in target_facts.get("parentIdentities") or [] if isinstance(item, dict)],
            absent_parent_relative_paths=[str(item) for item in target_facts.get("absentParentRelativePaths") or []],
            target_relative_path=str(target_facts.get("targetRelativePath") or ""),
        )
        refresh = refresh_asset_database_sync(expected_call[1])
        if refresh.get("ok") is not True:
            raise RuntimeError(refresh.get("error") or "Unity AssetDatabase refresh failed after chat image copy.")
        asset_path = str(target_facts["targetRelativePath"])
        return {
            "ok": True,
            "kind": "chat_image_copy",
            "assetPath": asset_path,
            "copiedFiles": [asset_path],
            "copiedFileCount": 1,
            "attachment": attachment,
            "copiedSha256": copied_hash,
            "assetDatabaseRefresh": refresh,
        }
    except Exception as exc:
        cleanup_error = cleanup_owned_import(target, ownership)
        if cleanup_error:
            raise RuntimeError(f"Chat image import failed; recovery cleanup also failed: {cleanup_error}") from exc
        raise


CHAT_ZIP_SAFE_EXTRACT_SUFFIXES = {
    ".prefab", ".mat", ".png", ".jpg", ".jpeg", ".tga", ".psd", ".exr",
    ".fbx", ".blend", ".obj", ".asset", ".controller", ".anim",
    ".txt", ".md", ".json",
}


def prepare_import_chat_archive_request(arguments: dict[str, Any], preview: Any) -> tuple[dict[str, Any], Any]:
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
        raise RuntimeError("Caller may not provide the reserved prepared Unity execution key.")
    payload_hash = str(arguments.get("payloadHash") or arguments.get("payload_hash") or "").strip().lower()
    if not is_vault_payload_hash(payload_hash):
        raise RuntimeError("payloadHash is invalid.")
    try:
        resolved = chat_attachment_vault_store().resolve(payload_hash)
    except ChatAttachmentVaultError as exc:
        raise RuntimeError(f"{exc} (reason: {exc.reason})") from exc
    if resolved is None:
        raise RuntimeError("Chat archive was not found in the local vault.")
    if resolved.get("category") != "archive":
        raise RuntimeError("vrcforge_import_chat_archive only accepts archive attachments.")
    source = Path(str(resolved.get("path") or ""))
    kind = str(resolved.get("kind") or "")
    try:
        archive_guard = guard_vault_archive(source, kind)
    except ChatAttachmentVaultError as exc:
        raise RuntimeError(f"{exc} (reason: {exc.reason})") from exc

    if kind == "unitypackage":
        nested_arguments = {**arguments, "packagePath": str(source)}
        nested_prepared, nested_preview = prepare_outfit_import_package_request(nested_arguments, preview)
        calls = build_prepared_execution_plan(nested_prepared)
        nested_evidence = prepared_evidence(nested_prepared)
        queue = ensure_dict(nested_evidence).get("queue")
        target_hashes = [
            str(ensure_dict(ensure_dict(item).get("identity")).get("sha256") or "")
            for item in (queue if isinstance(queue, list) else [])
            if ensure_dict(item).get("role") == "target"
        ]
        if target_hashes != [payload_hash]:
            raise RuntimeError("Vault UnityPackage identity did not match payloadHash.")
        evidence = {
            "branch": "unitypackage",
            "payloadHash": payload_hash,
            "archiveGuard": archive_guard,
            "outfitEvidence": nested_evidence,
        }
        base_arguments = {key: value for key, value in nested_prepared.items() if key != PREPARED_UNITY_EXECUTION_ARGUMENT_KEY}
        prepared = install_prepared_calls(base_arguments, calls, evidence)
        return prepared, {"ok": True, "branch": "unitypackage", "payloadHash": payload_hash, "outfit": nested_preview}

    if kind != "zip":
        raise RuntimeError("Prepared chat archive branch is unsupported.")
    project_root = _resolve_unity_project_root_for_import(arguments, {})
    target_folder = str(arguments.get("targetFolder") or arguments.get("target_folder") or "Assets/VRCForge/Imports")
    stem = sanitize_artifact_name(Path(str(resolved.get("name") or "")).stem) or "chat-archive"
    target_root_name = f"{stem}_{payload_hash[:8]}"
    zip_facts = prepare_zip_extract(
        source=source,
        project_root=project_root,
        target_folder=target_folder,
        target_root_name=target_root_name,
        allowed_suffixes=CHAT_ZIP_SAFE_EXTRACT_SUFFIXES,
    )
    if zip_facts.get("sourceSha256") != payload_hash:
        raise RuntimeError("Vault ZIP identity did not match payloadHash.")
    project_path = str(ensure_dict(zip_facts.get("project")).get("path") or "")
    refresh_arguments = {"projectPath": project_path, "resolvePackages": False, "packageResolveTimeoutSeconds": 120}
    evidence = {
        "branch": "zip",
        "payloadHash": payload_hash,
        "archiveGuard": archive_guard,
        "zipFacts": zip_facts,
    }
    prepared = install_prepared_calls(arguments, [("vrc_refresh_asset_database", refresh_arguments)], evidence)
    return prepared, {"ok": True, "branch": "zip", "payloadHash": payload_hash, "targetRoot": zip_facts["targetRootRelativePath"], "fileCount": len(zip_facts["manifest"])}


def import_chat_archive_approved_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    committed = False
    try:
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict):
            raise RuntimeError("Prepared chat archive evidence is invalid.")
        branch = str(evidence.get("branch") or "")
        payload_hash = str(evidence.get("payloadHash") or "")
        if not is_vault_payload_hash(payload_hash):
            raise RuntimeError("Prepared chat archive payloadHash is invalid.")
        if branch == "unitypackage":
            nested_evidence = evidence.get("outfitEvidence")
            if not isinstance(nested_evidence, dict):
                raise RuntimeError("Prepared chat UnityPackage evidence is invalid.")
            calls = build_prepared_execution_plan(arguments)
            nested_base = {key: value for key, value in arguments.items() if key != PREPARED_UNITY_EXECUTION_ARGUMENT_KEY}
            nested_arguments = install_prepared_calls(nested_base, calls, nested_evidence)
            result = import_outfit_package_approved_sync(nested_arguments)
            return {**result, "payloadHash": payload_hash, "archiveGuard": evidence.get("archiveGuard")}
        if branch != "zip":
            raise RuntimeError("Prepared chat archive branch is invalid.")
        zip_facts = evidence.get("zipFacts")
        if not isinstance(zip_facts, dict) or zip_facts.get("sourceSha256") != payload_hash:
            raise RuntimeError("Prepared chat ZIP facts are invalid.")
        extraction = execute_zip_extract(zip_facts)
        committed = True
        tool_name, tool_arguments = prepared_call(arguments)
        expected_refresh = {
            "projectPath": str(ensure_dict(zip_facts.get("project")).get("path") or ""),
            "resolvePackages": False,
            "packageResolveTimeoutSeconds": 120,
        }
        if tool_name != "vrc_refresh_asset_database":
            raise RuntimeError("Prepared chat archive refresh call is invalid.")
        _require_prepared_import_evidence(expected_refresh, tool_arguments, "chat archive refresh arguments")
        settings = load_dashboard_settings(build_agent_connection_request(arguments))
        refresh = ensure_dict_payload(
            extract_tool_result_payload(invoke_unity_mcp(settings, tool_name, tool_arguments)),
            "prepared chat archive refresh",
        )
        if refresh.get("ok") is not True:
            return {
                "ok": False,
                "committed": True,
                "commitState": "committed",
                "checkpointRecoveryRequired": True,
                "payloadHash": payload_hash,
                "extraction": extraction,
                "assetDatabaseRefresh": refresh,
                "error": refresh.get("error") or "Asset refresh failed after archive extraction.",
            }
        return {
            "ok": True,
            "kind": "managed_zip_extract",
            "payloadHash": payload_hash,
            "targetFolder": zip_facts["targetRootRelativePath"],
            "copiedFiles": extraction["files"][:256],
            "copiedFileCount": extraction["fileCount"],
            "archiveGuard": evidence.get("archiveGuard"),
            "assetDatabaseRefresh": refresh,
        }
    except (RuntimeError, UnityMcpError, ValueError, OSError, zipfile.BadZipFile) as exc:
        emit_log("error", "archive", "Prepared chat archive import failed.", {"error": str(exc)})
        if committed or "recovery cleanup also failed" in str(exc):
            return {
                "ok": False,
                "committed": True,
                "commitState": "unknown" if "recovery cleanup also failed" in str(exc) else "committed",
                "checkpointRecoveryRequired": True,
                "error": str(exc),
            }
        raise to_http_exception(exc) from exc


PROJECT_PREFS_MAX_PATHS = 64


def project_prefs_path() -> Path:
    return AGENT_GATEWAY.user_constraints_path.parent / "custom-projects.json"


def load_project_prefs() -> dict[str, list[str]]:
    path = project_prefs_path()
    custom_paths: list[str] = []
    hidden_paths: list[str] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                custom_paths = [item for item in payload.get("customPaths") or [] if isinstance(item, str) and item.strip()]
                hidden_paths = [item for item in payload.get("hiddenPaths") or [] if isinstance(item, str) and item.strip()]
        except (OSError, ValueError):
            # 配置损坏时退回空配置，不阻断主流程；下次保存会覆盖修复。
            pass
    return {"customPaths": custom_paths, "hiddenPaths": hidden_paths}


@app.get("/api/app/projects/prefs")
def read_project_prefs() -> dict[str, Any]:
    prefs = load_project_prefs()
    return {"ok": True, "path": str(project_prefs_path()), **prefs}


@app.post("/api/app/projects/prefs")
async def write_project_prefs(request: ProjectPrefsRequest) -> dict[str, Any]:
    custom_paths: list[str] = []
    seen: set[str] = set()
    for raw in request.custom_paths[:PROJECT_PREFS_MAX_PATHS]:
        normalized = normalize_path_string(raw)
        if not normalized or normalized.casefold() in seen:
            continue
        candidate = Path(normalized)
        if not candidate.is_dir() or not is_unity_project_path(candidate):
            continue
        seen.add(normalized.casefold())
        custom_paths.append(normalized)
    hidden_paths: list[str] = []
    hidden_seen: set[str] = set()
    for raw in request.hidden_paths[:PROJECT_PREFS_MAX_PATHS]:
        normalized = normalize_path_string(raw)
        if not normalized or normalized.casefold() in hidden_seen:
            continue
        hidden_seen.add(normalized.casefold())
        hidden_paths.append(normalized)
    path = project_prefs_path()
    try:
        atomic_write_json(path, {"version": 1, "customPaths": custom_paths, "hiddenPaths": hidden_paths})
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法写入项目配置: {exc}") from exc
    await EVENT_BUS.broadcast("projects", project_snapshot_payload(use_cache=True, refresh_async=False))
    return {"ok": True, "path": str(path), "customPaths": custom_paths, "hiddenPaths": hidden_paths}


@app.get("/api/app/skills")
def app_agent_skills() -> dict[str, Any]:
    return AGENT_GATEWAY.build_skill_registry()


@app.get("/api/app/skills/check")
def app_agent_skills_check() -> dict[str, Any]:
    return AGENT_GATEWAY.check_skill_registry()


@app.post("/api/app/skills")
def app_create_agent_skill(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return AGENT_GATEWAY.create_user_skill(payload)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.put("/api/app/skills/{skill_id}")
def app_update_agent_skill(skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return AGENT_GATEWAY.update_user_skill(skill_id, payload)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.delete("/api/app/skills/{skill_id}")
def app_delete_agent_skill(skill_id: str) -> dict[str, Any]:
    try:
        return AGENT_GATEWAY.delete_user_skill(skill_id)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def request_model_to_dict(request: Any) -> dict[str, Any]:
    if isinstance(request, BaseModel):
        return request.model_dump(by_alias=True)
    if isinstance(request, dict):
        return dict(request)
    return {}


def is_unity_write_preview_request(request: Any, *, allow_mock_execute: bool) -> bool:
    if bool(getattr(request, "dry_run", False)):
        return True
    if allow_mock_execute and bool(getattr(request, "mock_execute", False)):
        return True
    return False


def build_supervised_unity_write_arguments(request: Any, extra_arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    arguments = request_model_to_dict(request)
    if not any(arguments.get(key) for key in ("project_root", "projectRoot", "project_path", "projectPath")):
        selected_project = str(getattr(DASHBOARD_STATE, "selected_project_path", "") or "").strip()
        if selected_project:
            arguments["project_path"] = selected_project
    if extra_arguments:
        arguments.update(extra_arguments)
    return arguments


def prepare_avatar_scoped_tuning_write_request(
    arguments: dict[str, Any],
    caller_preview: Any,
) -> tuple[dict[str, Any], Any]:
    """Freeze the avatar fallback before approval for deterministic tuning writes."""
    prepared = copy.deepcopy(arguments or {})
    snake_avatar = prepared.get("avatar_path")
    camel_avatar = prepared.get("avatarPath")
    if snake_avatar and camel_avatar and str(snake_avatar).strip() != str(camel_avatar).strip():
        raise AgentGatewayError("Conflicting avatar_path and avatarPath values.", status_code=400)
    avatar_path = str(
        camel_avatar
        or snake_avatar
        or getattr(DASHBOARD_RUNTIME, "current_avatar_path", "")
        or ""
    ).strip()
    if not avatar_path:
        raise AgentGatewayError(
            "The exact avatar path must be known before approval.",
            status_code=409,
        )
    prepared.pop("avatar_path", None)
    prepared["avatarPath"] = avatar_path
    return prepared, caller_preview


def request_supervised_unity_write(
    target_tool: str,
    request: Any,
    *,
    reason: str,
    preview_callback: Callable[[], dict[str, Any]] | None = None,
    allow_mock_execute: bool = False,
    extra_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue a live Unity write; only the approved gateway handler may execute it.

    Preview-only requests deliberately retain their existing no-Unity-write path.
    The gateway creates the pre-write checkpoint immediately before the approved
    handler, so a request cannot create a checkpoint and then bypass approval.
    """
    if preview_callback is not None and is_unity_write_preview_request(
        request,
        allow_mock_execute=allow_mock_execute,
    ):
        return preview_callback()

    arguments = build_supervised_unity_write_arguments(request, extra_arguments)
    try:
        created = AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": target_tool,
                "arguments": arguments,
                "reason": reason,
                "agent_name": "dashboard",
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    approval = ensure_dict(created.get("approval"))
    if created.get("autoApproved"):
        execution_result = created.get("result")
        if isinstance(execution_result, dict):
            return {
                **execution_result,
                "status": str(created.get("status") or "executed"),
                "autoApproved": True,
                "approval": approval,
                "approvalId": str(created.get("approvalId") or approval.get("id") or ""),
                "execution": created,
            }
        return {
            **created,
            "approval": approval,
            "approvalId": str(created.get("approvalId") or approval.get("id") or ""),
            "execution": created,
        }
    return {
        "ok": bool(created.get("ok", True)),
        "status": "pending_approval",
        "approval": approval,
        "approvalId": str(approval.get("id") or ""),
        "message": "Unity write is waiting for approval. A checkpoint will be created immediately before execution.",
    }


def request_supervised_vision_capture(
    target_tool: str,
    request: Any,
    *,
    reason: str,
) -> dict[str, Any]:
    """Create a capture approval without exposing its approved execution handler."""

    return AGENT_GATEWAY.create_apply_request(
        {
            "target_tool": target_tool,
            "arguments": request.model_dump(),
            "reason": reason,
        }
    )


def skill_package_store_dir() -> Path:
    return AGENT_GATEWAY.user_constraints_path.parent / "skill-packages"


def skill_package_service() -> SkillPackageService:
    return SkillPackageService(skill_package_store_dir(), vrcforge_version=app.version)


def skill_package_error_response(exc: Exception) -> HTTPException:
    status = 400 if isinstance(exc, SkillPackageError) else 500
    return HTTPException(status_code=status, detail=str(exc))


def list_skill_packages_sync(_params: dict[str, Any] | None = None) -> dict[str, Any]:
    service = skill_package_service()
    registry = service.load_registry()
    return {
        "ok": True,
        "store": str(service.skill_store),
        "registry": registry,
        "governance": registry.get("governance", {}),
        "audit": registry.get("audit", []),
        "installed": service.list_installed(),
    }


def preflight_skill_package_sync(params: dict[str, Any]) -> dict[str, Any]:
    service = skill_package_service()
    preview = service.preflight_import(
        str(params.get("packagePath") or params.get("package_path") or ""),
        allow_downgrade=bool(params.get("allowDowngrade") or params.get("allow_downgrade") or False),
        dev_mode=bool(params.get("devMode") or params.get("dev_mode") or False),
    )
    return {"ok": True, "preview": preview.as_dict()}


def _skill_projection_path_is_link_like(path: Path) -> bool:
    return _SKILL_PACKAGE_PROJECTION._impl_skill_projection_path_is_link_like(path)


def _resolve_skill_projection_source(installed_root: Path, relative: str, *, label: str) -> tuple[Path, PurePosixPath]:
    return _SKILL_PACKAGE_PROJECTION._impl_resolve_skill_projection_source(installed_root, relative, label=label)


def _copy_projected_skill_file(source: Path, target_dir: Path, relative: PurePosixPath) -> Path:
    return _SKILL_PACKAGE_PROJECTION._impl_copy_projected_skill_file(source, target_dir, relative)


def _write_projected_skill_state(target_dir: Path, enabled: bool) -> Path:
    return _SKILL_PACKAGE_PROJECTION._impl_write_projected_skill_state(target_dir, enabled)


def _capture_projected_skill_state(manifest: dict[str, Any]) -> tuple[Path, bytes | None]:
    return _SKILL_PACKAGE_PROJECTION._impl_capture_projected_skill_state(manifest)


def _restore_projected_skill_state(snapshot: tuple[Path, bytes | None]) -> None:
    return _SKILL_PACKAGE_PROJECTION._impl_restore_projected_skill_state(snapshot)


def _set_projected_skills_enabled(manifests: list[dict[str, Any]], enabled: bool) -> list[dict[str, Any]]:
    return _SKILL_PACKAGE_PROJECTION._impl_set_projected_skills_enabled(manifests, enabled)


def _project_installed_skill(installed_path: Path, manifest: dict[str, Any], *, enabled: bool = True) -> dict[str, Any] | None:
    return _SKILL_PACKAGE_PROJECTION._impl_project_installed_skill(installed_path, manifest, enabled=enabled)


def _projected_skill_name(manifest: dict[str, Any]) -> str:
    return _SKILL_PACKAGE_PROJECTION._impl_projected_skill_name(manifest)


def _set_projected_skill_enabled(manifest: dict[str, Any], enabled: bool) -> dict[str, Any]:
    return _SKILL_PACKAGE_PROJECTION._impl_set_projected_skill_enabled(manifest, enabled)


def _delete_projected_skill_transaction(manifest: dict[str, Any]) -> Any:
    return _SKILL_PACKAGE_PROJECTION._impl_delete_projected_skill_transaction(manifest)


def import_skill_package_sync(params: dict[str, Any]) -> dict[str, Any]:
    return _SKILL_PACKAGE_CONTROLLER._impl_import_skill_package_sync(params)


def set_skill_package_enabled_sync(params: dict[str, Any]) -> dict[str, Any]:
    return _SKILL_PACKAGE_CONTROLLER._impl_set_skill_package_enabled_sync(params)


def uninstall_skill_package_sync(params: dict[str, Any]) -> dict[str, Any]:
    return _SKILL_PACKAGE_CONTROLLER._impl_uninstall_skill_package_sync(params)


def _disable_projected_skills_for_packages(service: SkillPackageService, skill_ids: list[str]) -> list[dict[str, Any]]:
    return _SKILL_PACKAGE_GOVERNANCE._impl_disable_projected_skills_for_packages(service, skill_ids)


def set_skill_package_safe_mode_sync(params: dict[str, Any]) -> dict[str, Any]:
    return _SKILL_PACKAGE_GOVERNANCE._impl_set_skill_package_safe_mode_sync(params)


def trust_skill_package_signer_sync(params: dict[str, Any]) -> dict[str, Any]:
    return _SKILL_PACKAGE_GOVERNANCE._impl_trust_skill_package_signer_sync(params)


def revoke_skill_package_signer_sync(params: dict[str, Any]) -> dict[str, Any]:
    return _SKILL_PACKAGE_GOVERNANCE._impl_revoke_skill_package_signer_sync(params)


def block_skill_package_sync(params: dict[str, Any]) -> dict[str, Any]:
    return _SKILL_PACKAGE_GOVERNANCE._impl_block_skill_package_sync(params)


def _exportable_user_skill(skill_name: str) -> tuple[dict[str, Any], Path]:
    skill = AGENT_GATEWAY._find_user_skill(skill_name)  # noqa: SLF001 - package export is a host-level integration.
    if not skill:
        raise AgentGatewayError(f"User skill was not found: {skill_name}", status_code=404)
    storage_path = Path(str(skill.get("storagePath") or ""))
    if not storage_path.is_file():
        raise AgentGatewayError(f"User skill file was not found: {skill_name}", status_code=404)
    return skill, storage_path


def _read_user_skill_export_file(
    service: SkillPackageService,
    root: Path,
    relative: str,
    *,
    label: str,
) -> tuple[Path, PurePosixPath, bytes]:
    resolved, relative_path = _resolve_skill_projection_source(root, relative, label=label)
    normalized = relative_path.as_posix()
    exclusion = service._source_file_exclusion_reason(resolved, normalized)  # noqa: SLF001 - selected export files use package policy.
    if exclusion:
        raise SkillPackageError(f"{label} is not exportable ({exclusion}): {normalized}.")
    metadata = resolved.stat(follow_symlinks=False)
    if metadata.st_size > service.max_file_size:
        raise SkillPackageError(f"{label} exceeds the package file-size limit: {normalized}.")
    with resolved.open("rb") as stream:
        data = stream.read(service.max_file_size + 1)
    if len(data) > service.max_file_size:
        raise SkillPackageError(f"{label} exceeds the package file-size limit: {normalized}.")
    if service._contains_sensitive_content(data):  # noqa: SLF001 - selected export files use package policy.
        raise SkillPackageError(f"{label} contains secret or binary material: {normalized}.")
    return resolved, relative_path, data


def _copy_manifest_user_skill_export_source(
    skill_file: Path,
    source: Path,
    service: SkillPackageService,
) -> bool:
    root = skill_file.parent
    manifest_path = root / "manifest.json"
    if not os.path.lexists(manifest_path):
        return False

    _manifest_resolved, _manifest_relative, manifest_bytes = _read_user_skill_export_file(
        service,
        root,
        "manifest.json",
        label="user skill manifest",
    )
    manifest_value = _load_json_bytes(manifest_bytes, "manifest.json")
    manifest = service.validate_manifest(manifest_value, package_root=root)
    entrypoints = manifest["entrypoints"]
    skill_relative = entrypoints.get("skill")
    if not skill_relative:
        raise SkillPackageError("User skill manifest must declare the loaded SKILL.md as its skill entrypoint.")

    loaded_relative = skill_file.relative_to(root).as_posix()
    loaded_resolved, _loaded_path = _resolve_skill_projection_source(
        root,
        loaded_relative,
        label="loaded user SKILL.md",
    )
    manifest_skill_resolved, _manifest_skill_path = _resolve_skill_projection_source(
        root,
        skill_relative,
        label="skill entrypoint",
    )
    if not os.path.samefile(loaded_resolved, manifest_skill_resolved):
        raise SkillPackageError("User skill manifest skill entrypoint does not resolve to the loaded SKILL.md.")

    selected: dict[str, bytes] = {"manifest.json": manifest_bytes}
    entrypoint_paths: dict[str, str] = {}
    collision_keys = {"manifest.json"}
    for name, relative in entrypoints.items():
        resolved, relative_path, data = _read_user_skill_export_file(
            service,
            root,
            relative,
            label=f"user skill entrypoint {name}",
        )
        normalized = relative_path.as_posix()
        collision_key = normalized.casefold()
        if collision_key in collision_keys:
            raise SkillPackageError(f"User skill manifest contains a colliding entrypoint path: {normalized}.")
        collision_keys.add(collision_key)
        entrypoint_paths[name] = normalized
        selected[normalized] = data
        if name == "skill" and not os.path.samefile(resolved, loaded_resolved):
            raise SkillPackageError("User skill manifest skill entrypoint does not resolve to the loaded SKILL.md.")

    non_skill_entrypoints = {
        relative for name, relative in entrypoint_paths.items() if name != "skill"
    }
    service._validate_payload_limits(selected)  # noqa: SLF001 - selected export payload uses package limits.
    for relative, data in selected.items():
        destination = source.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    try:
        staged_skill = parse_skill_markdown(source.joinpath(*PurePosixPath(skill_relative).parts))
    except Exception as exc:  # noqa: BLE001 - the staged bytes must remain a loadable user skill.
        raise SkillPackageError("The selected SKILL.md cannot be parsed for export.") from exc
    raw_support_files = staged_skill.get("supportFiles")
    if isinstance(raw_support_files, list):
        declared_support = raw_support_files
    elif raw_support_files:
        declared_support = [raw_support_files]
    else:
        declared_support = []
    support_collision_keys: set[str] = set()
    for raw_relative in declared_support:
        relative = str(raw_relative or "").strip()
        _resolved, relative_path = _resolve_skill_projection_source(
            root,
            relative,
            label="SKILL.md support file",
        )
        normalized = relative_path.as_posix()
        collision_key = normalized.casefold()
        if collision_key in support_collision_keys:
            raise SkillPackageError(f"SKILL.md contains a colliding support-file path: {normalized}.")
        support_collision_keys.add(collision_key)
        if normalized not in non_skill_entrypoints:
            raise SkillPackageError(
                f"Runtime support files must also be declared as non-skill manifest entrypoints: {normalized}"
            )

    return True


def export_skill_package_sync(params: dict[str, Any]) -> dict[str, Any]:
    skill_name = str(params.get("skillName") or params.get("skill_name") or "").strip()
    output_text = str(params.get("outputPath") or params.get("output_path") or "").strip()
    if not skill_name or not output_text:
        raise AgentGatewayError("skillName and outputPath are required.", status_code=400)
    output_path = Path(output_text)
    skill, skill_file = _exportable_user_skill(skill_name)
    service = skill_package_service()
    with tempfile.TemporaryDirectory(prefix="vrcforge-skill-export-") as temp_dir:
        source = Path(temp_dir)
        if not _copy_manifest_user_skill_export_source(skill_file, source, service):
            shutil.copy2(skill_file, source / "SKILL.md")
            package_id = f"community.{str(skill.get('name') or skill_name).lower()}"
            package_id = re.sub(r"[^a-z0-9_.-]+", "-", package_id).strip("-._")
            manifest = {
                "id": package_id,
                "name": str(skill.get("title") or skill.get("name") or skill_name)[:160],
                "skill_name": str(skill.get("name") or skill_name),
                "version": "1.0.0",
                "author": "VRCForge User",
                "description": str(skill.get("description") or "Exported VRCForge skill.")[:4000],
                "min_vrcforge_version": DEFAULT_MIN_VRCFORGE_VERSION,
                "permissions": ["read_project"],
                "entrypoints": {"skill": "SKILL.md"},
            }
            (source / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if params.get("release"):
            private_key = params.get("privateKeyPem") or params.get("private_key_pem") or params.get("privateKeyPath") or params.get("private_key_path")
            exported = service.export_release(source, output_path, private_key)
        else:
            exported = service.export_dev(source, output_path)
    return {"ok": True, "exported": exported.as_dict()}


def _path_to_skill_kwargs(params: dict[str, Any]) -> dict[str, Any]:
    return _PATH_TO_SKILL_CONTROLLER._impl_path_to_skill_kwargs(params)


def _path_to_skill_file_list(source_files: dict[str, str]) -> list[dict[str, Any]]:
    return _PATH_TO_SKILL_CONTROLLER._impl_path_to_skill_file_list(source_files)


def _path_to_skill_vsk_filename(manifest: dict[str, Any]) -> str:
    return _PATH_TO_SKILL_CONTROLLER._impl_path_to_skill_vsk_filename(manifest)


def capture_path_to_skill_sync(params: dict[str, Any], *, allow_write: bool = False) -> dict[str, Any]:
    return _PATH_TO_SKILL_CONTROLLER._impl_capture_path_to_skill_sync(params, allow_write=allow_write)


def scan_project_index_sync(params: dict[str, Any]) -> dict[str, Any]:
    project_path = str(params.get("projectPath") or params.get("project_path") or "").strip()
    max_files = int(params.get("maxFiles") or params.get("max_files") or 100000)
    if not project_path:
        raise AgentGatewayError("projectPath is required.", status_code=400)
    return scan_project_memory(project_path, PROJECT_MEMORY_INDEX_DIR, max_files=max_files)


def inspect_outfit_package_sync(params: dict[str, Any]) -> dict[str, Any]:
    package_path = str(params.get("packagePath") or params.get("package_path") or "").strip()
    max_entries = int(params.get("maxEntries") or params.get("max_entries") or 5000)
    if not package_path:
        raise AgentGatewayError("packagePath is required.", status_code=400)
    return inspect_outfit_package(package_path, max_entries=max_entries)


def plan_outfit_import_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    package_path = str(params.get("packagePath") or params.get("package_path") or "").strip()
    if not package_path:
        raise AgentGatewayError("packagePath is required.", status_code=400)
    project_path = str(params.get("projectPath") or params.get("project_path") or DASHBOARD_STATE.selected_project_path or "").strip()
    return build_outfit_import_plan(
        package_path=package_path,
        project_path=project_path or None,
        target_folder=str(params.get("targetFolder") or params.get("target_folder") or "").strip() or None,
        selected_unitypackage=str(params.get("selectedUnityPackage") or params.get("selected_unitypackage") or "").strip() or None,
        selected_prefab=str(params.get("selectedPrefab") or params.get("selected_prefab") or "").strip() or None,
        base_avatar_name=str(params.get("baseAvatarName") or params.get("base_avatar_name") or "").strip() or None,
        max_entries=int(params.get("maxEntries") or params.get("max_entries") or 5000),
    )


def connector_bundle_sync(params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    bridge = resolve_stdio_bridge(ROOT_DIR)
    stdio_command = str(params.get("stdioCommand") or params.get("stdio_command") or bridge.command)
    stdio_script = params.get("stdioScript") or params.get("stdio_script") or (bridge.args[0] if bridge.args else "")
    stdio_cwd = params.get("stdioCwd") or params.get("stdio_cwd") or bridge.cwd
    smoke_script = params.get("smokeScript") or params.get("smoke_script") or (ROOT_DIR / "scripts" / "smoke_external_agent_bridge.py")
    options = ExternalAgentConnectorOptions(
        server_name=str(params.get("serverName") or params.get("server_name") or "vrcforge"),
        mcp_url=str(params.get("mcpUrl") or params.get("mcp_url") or "http://127.0.0.1:8757/mcp"),
        token_env_var=str(params.get("tokenEnvVar") or params.get("token_env_var") or "VRCFORGE_AGENT_TOKEN"),
        skills_projection_dir=str(
            params.get("skillsProjectionDir")
            or params.get("skills_projection_dir")
            or AGENT_GATEWAY.user_skills_dir
        ),
        stdio_command=stdio_command,
        stdio_script=str(stdio_script),
        stdio_cwd=str(stdio_cwd),
        smoke_script=str(smoke_script),
    )
    return {"ok": True, **build_connector_bundle(options)}


def summarize_external_agent_audit(limit: int = 25) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for entry in AGENT_GATEWAY.recent_audit_logs(limit=limit * 3):
        event = str(entry.get("event") or "")
        if not any(marker in event for marker in ("approval", "checkpoint", "agent")):
            continue
        approval = entry.get("approval") if isinstance(entry.get("approval"), dict) else {}
        calls.append(
            {
                "event": event,
                "createdAt": entry.get("createdAt") or approval.get("createdAt") or entry.get("timestamp") or "",
                "agentName": approval.get("agentName") or entry.get("agentName") or "",
                "targetTool": approval.get("targetTool") or entry.get("targetTool") or "",
                "status": approval.get("status") or entry.get("status") or "",
                "riskLevel": approval.get("riskLevel") or entry.get("riskLevel") or "",
            }
        )
        if len(calls) >= limit:
            break
    return calls


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _selected_project_path_or(project_path: str | None = None) -> str:
    value = str(project_path or "").strip()
    if value:
        return value
    return DASHBOARD_STATE.selected_project_path if DASHBOARD_STATE else ""


def external_agent_status_sync(project_path: str | None = None, generic_config_path: str | None = None) -> dict[str, Any]:
    config = AGENT_GATEWAY.ensure_config()
    health = safe_agent_health()
    manifest = safe_agent_manifest()
    selected_project_path = _selected_project_path_or(project_path)
    return {
        **connector_bundle_sync({}),
        "clients": connector_client_statuses(
            root_dir=ROOT_DIR,
            project_path=selected_project_path,
            generic_config_path_value=generic_config_path,
        ),
        "gateway": {
            "enabled": bool(config.enabled),
            "requiresToken": bool(config.require_token),
            "allowWriteRequests": bool(config.allow_write_requests),
            "tokenConfigured": bool(config.token),
            "approvalTokenConfigured": bool(config.approval_token),
            "configPath": str(AGENT_GATEWAY.config_path),
            "mcpUrl": health.get("mcpUrl"),
            "restUrl": health.get("restUrl"),
            "pendingApprovalCount": health.get("pendingApprovalCount"),
            "checkpointArchiveMaxSizeMb": int(config.checkpoint_archive_max_size_mb),
            "checkpointArchiveUsage": AGENT_GATEWAY.checkpoint_archive_usage(config),
        },
        "advertisedTools": [
            {"name": tool.get("name"), "category": tool.get("category"), "write": bool(tool.get("write"))}
            for tool in _list_or_empty(manifest.get("tools"))
            if isinstance(tool, dict)
        ],
        "writeTargets": [
            {"name": target.get("name"), "riskLevel": target.get("riskLevel"), "advanced": bool(target.get("advanced"))}
            for target in _list_or_empty(manifest.get("writeTargets"))
            if isinstance(target, dict)
        ],
        "lastCalls": summarize_external_agent_audit(),
    }


def update_external_agent_gateway_sync(params: dict[str, Any]) -> dict[str, Any]:
    config = AGENT_GATEWAY.ensure_config()
    if params.get("enabled") is not None:
        config.enabled = bool(params.get("enabled"))
    if params.get("allowWriteRequests") is not None or params.get("allow_write_requests") is not None:
        config.allow_write_requests = bool(params.get("allowWriteRequests", params.get("allow_write_requests")))
    checkpoint_limit = params.get("checkpointArchiveMaxSizeMb", params.get("checkpoint_archive_max_size_mb"))
    prune_summary: dict[str, Any] | None = None
    if checkpoint_limit is not None:
        config.checkpoint_archive_max_size_mb = normalize_checkpoint_archive_max_size_mb(checkpoint_limit)
    if params.get("revokeToken") is True or params.get("revoke_token") is True:
        config.token = secrets.token_urlsafe(32)
        config.approval_token = secrets.token_urlsafe(32)
        rotated_at = datetime.now(timezone.utc).isoformat()
        config.token_created_at = config.token_created_at or rotated_at
        config.token_rotated_at = rotated_at
    AGENT_GATEWAY.save_config(config)
    if checkpoint_limit is not None:
        prune_summary = AGENT_GATEWAY.prune_checkpoint_archives(config.checkpoint_archive_max_size_mb)

    delete_ids = params.get("deleteCheckpointArchiveIds")
    if delete_ids is None:
        delete_ids = params.get("delete_checkpoint_archive_ids")
    delete_summary: dict[str, Any] | None = None
    if delete_ids:
        try:
            delete_summary = AGENT_GATEWAY.delete_checkpoint_archives(delete_ids)
        except Exception as exc:  # noqa: BLE001
            delete_summary = {"ok": False, "error": str(exc)}

    relocate_dir = params.get("checkpointArchiveDirectory")
    if relocate_dir is None:
        relocate_dir = params.get("checkpoint_archive_directory")
    relocate_summary: dict[str, Any] | None = None
    if isinstance(relocate_dir, str) and relocate_dir.strip():
        try:
            relocate_summary = AGENT_GATEWAY.relocate_checkpoint_archives(relocate_dir)
        except Exception as exc:  # noqa: BLE001
            relocate_summary = {"ok": False, "error": str(exc)}

    status = external_agent_status_sync()
    if prune_summary:
        status["gateway"]["checkpointArchivePrune"] = prune_summary
    if delete_summary is not None:
        status["gateway"]["checkpointArchiveDelete"] = delete_summary
    if relocate_summary is not None:
        status["gateway"]["checkpointArchiveRelocate"] = relocate_summary
    return status


def install_external_agent_connector_sync(params: dict[str, Any]) -> dict[str, Any]:
    client = str(params.get("client") or "").strip()
    project_path = _selected_project_path_or(params.get("projectPath") or params.get("project_path"))
    config_path = str(params.get("configPath") or params.get("config_path") or "").strip() or None
    try:
        action = install_connector(client, root_dir=ROOT_DIR, project_path=project_path, config_path=config_path)
    except ConnectorInstallError as exc:
        action = exc.as_result(client=client or "unknown", action="install")
    except Exception as exc:  # noqa: BLE001 - connector UX should return diagnostics instead of crashing Settings.
        action = {
            "ok": False,
            "client": client or "unknown",
            "action": "install",
            "stage": "unexpected",
            "error": str(exc),
            "suggestion": "Export a support bundle and retry after restarting VRCForge.",
        }
    if config_path:
        action.setdefault("configPath", config_path)
    emit_log(
        "success" if action.get("ok") else "warn",
        "connectors",
        f"External agent connector install {'succeeded' if action.get('ok') else 'failed'}.",
        {
            "client": action.get("client"),
            "stage": action.get("stage", ""),
            "configPath": action.get("configPath", ""),
            "error": action.get("error", ""),
            "suggestion": action.get("suggestion", ""),
            "handshake": action.get("handshake", {}),
        },
    )
    return {**external_agent_status_sync(project_path, config_path), "lastConnectorAction": action}


def uninstall_external_agent_connector_sync(params: dict[str, Any]) -> dict[str, Any]:
    client = str(params.get("client") or "").strip()
    project_path = _selected_project_path_or(params.get("projectPath") or params.get("project_path"))
    config_path = str(params.get("configPath") or params.get("config_path") or "").strip() or None
    try:
        action = uninstall_connector(client, root_dir=ROOT_DIR, project_path=project_path, config_path=config_path)
    except ConnectorInstallError as exc:
        action = exc.as_result(client=client or "unknown", action="uninstall")
    except Exception as exc:  # noqa: BLE001
        action = {
            "ok": False,
            "client": client or "unknown",
            "action": "uninstall",
            "stage": "unexpected",
            "error": str(exc),
            "suggestion": "Export a support bundle and retry after restarting VRCForge.",
        }
    if config_path:
        action.setdefault("configPath", config_path)
    emit_log(
        "success" if action.get("ok") else "warn",
        "connectors",
        f"External agent connector uninstall {'succeeded' if action.get('ok') else 'failed'}.",
        {
            "client": action.get("client"),
            "stage": action.get("stage", ""),
            "configPath": action.get("configPath", ""),
            "error": action.get("error", ""),
            "suggestion": action.get("suggestion", ""),
        },
    )
    return {**external_agent_status_sync(project_path, config_path), "lastConnectorAction": action}


@app.get("/api/app/external-agent/connectors")
def app_external_agent_connectors(
    projectPath: str | None = None,
    project_path: str | None = None,
    configPath: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    return external_agent_status_sync(projectPath or project_path, configPath or config_path)


@app.post("/api/app/external-agent/connectors")
def app_external_agent_connectors_custom(request: ExternalAgentConnectorRequest) -> dict[str, Any]:
    try:
        payload = connector_bundle_sync(request.model_dump(by_alias=True))
        return {**external_agent_status_sync(), **payload}
    except ConnectorInstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/app/external-agent/gateway")
def app_update_external_agent_gateway(request: ExternalAgentGatewayUpdateRequest) -> dict[str, Any]:
    return update_external_agent_gateway_sync(request.model_dump(by_alias=True))


@app.post("/api/app/external-agent/connectors/install")
def app_install_external_agent_connector(request: ExternalAgentConnectorActionRequest) -> dict[str, Any]:
    return install_external_agent_connector_sync(request.model_dump(by_alias=True))


@app.post("/api/app/external-agent/connectors/uninstall")
def app_uninstall_external_agent_connector(request: ExternalAgentConnectorActionRequest) -> dict[str, Any]:
    return uninstall_external_agent_connector_sync(request.model_dump(by_alias=True))


@app.get("/api/app/skill-packages")
def app_list_skill_packages() -> dict[str, Any]:
    try:
        return list_skill_packages_sync({})
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.post("/api/app/skill-packages/preflight")
def app_preflight_skill_package(request: SkillPackagePathRequest) -> dict[str, Any]:
    try:
        return preflight_skill_package_sync(request.model_dump(by_alias=True))
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.post("/api/app/skill-packages/import")
def app_import_skill_package(request: SkillPackagePathRequest) -> dict[str, Any]:
    try:
        return import_skill_package_sync(request.model_dump(by_alias=True))
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.post("/api/app/skill-packages/safe-mode")
def app_set_skill_package_safe_mode(request: SkillPackageSafeModeRequest) -> dict[str, Any]:
    try:
        return set_skill_package_safe_mode_sync(request.model_dump(by_alias=True))
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.post("/api/app/skill-packages/trust-signer")
def app_trust_skill_package_signer(request: SkillPackageSignerRequest) -> dict[str, Any]:
    try:
        return trust_skill_package_signer_sync(request.model_dump(by_alias=True))
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.post("/api/app/skill-packages/revoke-signer")
def app_revoke_skill_package_signer(request: SkillPackageSignerRequest) -> dict[str, Any]:
    try:
        return revoke_skill_package_signer_sync(request.model_dump(by_alias=True))
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.post("/api/app/skill-packages/block-package")
def app_block_skill_package(request: SkillPackageBlockRequest) -> dict[str, Any]:
    try:
        return block_skill_package_sync(request.model_dump(by_alias=True))
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.post("/api/app/skill-packages/export")
def app_export_skill_package(request: SkillPackageExportRequest) -> dict[str, Any]:
    try:
        return export_skill_package_sync(request.model_dump(by_alias=True))
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.post("/api/app/path-to-skill/preview")
def app_preview_path_to_skill(request: PathToSkillCaptureRequest) -> dict[str, Any]:
    try:
        return capture_path_to_skill_sync(request.model_dump(by_alias=True), allow_write=False)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except PathToSkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.post("/api/app/path-to-skill/write")
def app_write_path_to_skill(request: PathToSkillCaptureRequest) -> dict[str, Any]:
    try:
        return capture_path_to_skill_sync(request.model_dump(by_alias=True), allow_write=True)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except PathToSkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.put("/api/app/skill-packages/{skill_package_id}")
def app_set_skill_package_enabled(skill_package_id: str, request: SkillPackageStateRequest) -> dict[str, Any]:
    try:
        return set_skill_package_enabled_sync({"skillPackageId": skill_package_id, **request.model_dump(by_alias=True)})
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.delete("/api/app/skill-packages/{skill_package_id}")
def app_uninstall_skill_package(skill_package_id: str, request: SkillPackageUninstallRequest | None = None) -> dict[str, Any]:
    try:
        payload = request.model_dump(by_alias=True) if request is not None else {}
        return uninstall_skill_package_sync({"skillPackageId": skill_package_id, **payload})
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


@app.post("/api/app/validation/report")
def app_validation_report(request: ValidationReportRequest) -> dict[str, Any]:
    return build_validation_report_sync(request.model_dump(by_alias=True))


@app.post("/api/app/build-test/readiness")
def app_build_test_readiness(request: BuildTestReadinessRequest) -> dict[str, Any]:
    return build_test_readiness_sync(request.model_dump(by_alias=True))


@app.post("/api/app/avatars")
def app_list_avatars(request: DashboardStateRequest) -> dict[str, Any]:
    params = request.model_dump(by_alias=True)
    return AVATAR_TUNING_WORKFLOWS.read_avatars(build_agent_dashboard_request(params))


@app.post("/api/app/optimization/plan")
def app_optimization_plan(request: OptimizationPlanRequest) -> dict[str, Any]:
    return OPTIMIZATION_WORKFLOWS.build_plan(request.model_dump(by_alias=True))


@app.post("/api/app/optimization/tool")
def app_optimization_tool(request: OptimizationToolRequest) -> dict[str, Any]:
    params = request.model_dump(by_alias=True)
    tool_name = str(params.pop("tool", "") or "")
    return OPTIMIZATION_WORKFLOWS.build_tool(tool_name, params)


@app.post("/api/app/optimization/apply-request")
async def app_optimization_apply_request(request: OptimizationApplyRequest) -> dict[str, Any]:
    params = request.model_dump(by_alias=True)
    try:
        payload = OPTIMIZATION_WORKFLOWS.request_apply(
            params,
            agent_name="desktop-agent",
        )
    except (AgentGatewayError, ValueError) as exc:
        status_code = exc.status_code if isinstance(exc, AgentGatewayError) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    if not payload.get("ok"):
        raise HTTPException(status_code=400, detail=payload)
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/app/optimization/validation-delta")
def app_optimization_validation_delta(request: OptimizationValidationDeltaRequest) -> dict[str, Any]:
    return OPTIMIZATION_WORKFLOWS.build_validation_delta(
        request.model_dump(by_alias=True)
    )


@app.get("/api/app/optimization/proofs")
def app_optimization_proof_index(limit: int = 10) -> dict[str, Any]:
    return OPTIMIZATION_WORKFLOWS.list_proofs(limit=limit)


@app.get("/api/app/optimization/proofs/{run_id}")
def app_optimization_proof_detail(run_id: str) -> dict[str, Any]:
    try:
        return OPTIMIZATION_WORKFLOWS.read_proof(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Optimizer proof was not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/app/optimization/proofs/{run_id}/screenshots/{stage}")
def app_optimization_proof_screenshot(run_id: str, stage: str) -> FileResponse:
    try:
        path = OPTIMIZATION_WORKFLOWS.proof_screenshot_path(run_id, stage)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Optimizer proof screenshot was not found.") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Optimizer proof screenshot is outside the artifacts directory.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, media_type=mimetypes.guess_type(str(path))[0] or "application/octet-stream")


@app.post("/api/app/project-index/scan")
def app_project_index_scan(request: ProjectIndexScanRequest) -> dict[str, Any]:
    return scan_project_index_sync(request.model_dump(by_alias=True))


@app.post("/api/app/outfit-packages/inspect")
def app_outfit_package_inspect(request: OutfitPackageInspectRequest) -> dict[str, Any]:
    try:
        return WARDROBE_OUTFIT_WORKFLOWS.inspect_outfit_package(
            request.model_dump(by_alias=True)
        )
    except WardrobeOutfitWorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/app/outfit-imports/plan")
def app_outfit_import_plan(request: OutfitImportPlanRequest) -> dict[str, Any]:
    try:
        return WARDROBE_OUTFIT_WORKFLOWS.plan_outfit_import(
            request.model_dump(by_alias=True)
        )
    except WardrobeOutfitWorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/app/outfit-imports/request")
async def app_request_outfit_import(request: OutfitImportPlanRequest) -> dict[str, Any]:
    params = request.model_dump(by_alias=True)
    try:
        payload = WARDROBE_OUTFIT_WORKFLOWS.request_outfit_import(
            params,
            agent_name="desktop-agent",
        )
    except (AgentGatewayError, WardrobeOutfitWorkflowError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/app/package-install/diagnose")
def app_package_install_diagnostics(request: PackageInstallDiagnosticsRequest) -> dict[str, Any]:
    return PACKAGE_INSTALL_WORKFLOWS.diagnose_install(request.model_dump(by_alias=True))


@app.post("/api/app/package-install/plan")
def app_package_install_plan(request: PackageInstallPlanRequest) -> dict[str, Any]:
    return PACKAGE_INSTALL_WORKFLOWS.plan_install(request.model_dump(by_alias=True))


@app.post("/api/app/package-install/request")
async def app_package_install_request(request: PackageInstallPlanRequest) -> dict[str, Any]:
    payload = PACKAGE_INSTALL_WORKFLOWS.request_install(
        request.model_dump(by_alias=True),
        agent_name="desktop-agent",
    )
    if not payload.get("ok"):
        raise HTTPException(status_code=400, detail=payload)
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.get("/api/app/sub-agents")
def app_list_sub_agents(includeEvents: bool = False, limit: int = 50) -> dict[str, Any]:
    return _SUB_AGENT_COLLABORATION.list_tasks(include_events=includeEvents, limit=limit)


@app.post("/api/app/sub-agents")
async def app_create_sub_agent(request: SubAgentCreateRequest) -> dict[str, Any]:
    if not request.parent_chat_id.strip():
        raise HTTPException(status_code=400, detail="Sub-agent tasks require a durable parentChatId.")
    try:
        payload = _SUB_AGENT_COLLABORATION.create_task(
            role=request.role,
            task=request.task,
            display_name=request.display_name,
            parent_chat_id=request.parent_chat_id,
            parent_session_id=request.parent_session_id,
            project_path=request.project_path,
            params=request.params,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("subAgentTasks", _SUB_AGENT_COLLABORATION.list_tasks())
    return payload


@app.get("/api/app/sub-agents/{task_id}")
def app_get_sub_agent(task_id: str) -> dict[str, Any]:
    payload = _SUB_AGENT_COLLABORATION.get_task(task_id, include_events=True)
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Sub-agent task was not found.")
    return payload


@app.post("/api/app/sub-agents/{task_id}/cancel")
async def app_cancel_sub_agent(task_id: str) -> dict[str, Any]:
    payload = _SUB_AGENT_COLLABORATION.cancel_task(task_id)
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Sub-agent task was not found.")
    await EVENT_BUS.broadcast("subAgentTasks", _SUB_AGENT_COLLABORATION.list_tasks())
    return payload


@app.post("/api/app/sub-agents/{task_id}/retry")
async def app_retry_sub_agent(task_id: str) -> dict[str, Any]:
    payload = _SUB_AGENT_COLLABORATION.retry_task(task_id)
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Sub-agent task was not found.")
    await EVENT_BUS.broadcast("subAgentTasks", _SUB_AGENT_COLLABORATION.list_tasks())
    return payload


@app.post("/api/app/sub-agents/{task_id}/merge")
async def app_merge_sub_agent(task_id: str, request: SubAgentMergeRequest) -> dict[str, Any]:
    payload = _SUB_AGENT_COLLABORATION.merge_task(
        task_id,
        decision=request.decision,
        chat_id=request.chat_id,
        expected_revision=request.expected_revision,
    )
    if not payload.get("ok"):
        error_text = str(payload.get("error") or "Sub-agent task was not found.")
        status_code = 404 if "not found" in error_text else 409
        raise HTTPException(status_code=status_code, detail=error_text)
    await EVENT_BUS.broadcast("subAgentTasks", _SUB_AGENT_COLLABORATION.list_tasks())
    return payload


@app.post("/api/app/sub-agents/{task_id}/handoff-ack")
async def app_acknowledge_sub_agent_handoff(task_id: str, request: SubAgentHandoffAckRequest) -> dict[str, Any]:
    payload = _SUB_AGENT_COLLABORATION.acknowledge_handoff(
        task_id,
        expected_revision=request.expected_revision,
    )
    if not payload.get("ok"):
        error_text = str(payload.get("error") or "Sub-agent task was not found.")
        status_code = 404 if "not found" in error_text else 409
        raise HTTPException(status_code=status_code, detail=error_text)
    await EVENT_BUS.broadcast("subAgentTasks", _SUB_AGENT_COLLABORATION.list_tasks())
    return payload


@app.get("/api/agent/external-agent/connectors")
def read_agent_external_connectors(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return connector_bundle_sync({})


@app.get("/api/agent/skill-packages")
def read_agent_skill_packages(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    try:
        return list_skill_packages_sync({})
    except Exception as exc:  # noqa: BLE001
        raise skill_package_error_response(exc) from exc


def build_agentic_app_health() -> dict[str, Any]:
    try:
        payload = copy.deepcopy(build_full_health_payload())
    except Exception as exc:  # noqa: BLE001 - first-run desktop must still open as a normal agent.
        message = str(exc)
        return {
            "ok": False,
            "version": app.version,
            "portableMode": PORTABLE_MODE,
            "projectRoot": str(ROOT_DIR),
            "settingsPath": str(RUNTIME_SETTINGS_PATH),
            "configPath": str(CONFIG_PATH),
            "paths": {
                "programDir": str(ROOT_DIR),
                "userDataDir": str(USER_DATA_DIR),
                "configDir": str(CONFIG_DIR),
                "logsDir": str(LOG_DIR),
                "artifactsDir": str(ARTIFACTS_DIR),
                "dashboardDir": str(DASHBOARD_DIR),
            },
            "components": {
                "backend": health_component(
                    "ok",
                    "Backend process is responding.",
                    {"version": app.version, "programDir": str(ROOT_DIR), "portableMode": PORTABLE_MODE},
                ),
                "startupDegraded": health_component(
                    "warning",
                    "Startup diagnostics failed; VRCForge is running in normal agent mode.",
                    message,
                ),
            },
            "defaults": {},
            "state": serialize_dashboard_state(),
            "projects": {
                "selectedProjectPath": DASHBOARD_STATE.selected_project_path,
                "unityEditorPath": DASHBOARD_STATE.unity_editor_path,
                "projects": [],
                "warning": message,
            },
            "logRetentionHours": int(LOG_RETENTION.total_seconds() // 3600),
            "unityStatus": CURRENT_UNITY_STATUS,
        }
    payload.pop("apiConfig", None)
    return payload


def build_bootstrap_app_health(*, refresh_projects: bool = False) -> dict[str, Any]:
    api_config = PROVIDER_CONFIGURATION.serialize_app_api_config()
    agent_health = safe_agent_health()
    unity_status = CURRENT_UNITY_STATUS
    projects = bootstrap_project_snapshot_payload() if refresh_projects else project_snapshot_payload(use_cache=True, refresh_async=False)
    dashboard_index = DASHBOARD_DIR / "index.html"
    components: dict[str, dict[str, Any]] = {
        "backend": health_component(
            "ok",
            "Backend process is responding.",
            {"version": app.version, "programDir": str(ROOT_DIR), "portableMode": PORTABLE_MODE},
        ),
        "dashboardFiles": health_component(
            "ok" if dashboard_index.exists() else "error",
            "Dashboard files are present." if dashboard_index.exists() else "Dashboard index.html is missing.",
            {"index": str(dashboard_index), "dashboardUrl": "http://127.0.0.1:8757/"},
        ),
        "configReadWrite": health_component(
            "ok" if CONFIG_DIR.exists() and RUNTIME_SETTINGS_PATH.exists() else "warning",
            "Config path is available." if CONFIG_DIR.exists() else "Config directory is not initialized yet.",
            {"directory": str(CONFIG_DIR), "settingsPath": str(RUNTIME_SETTINGS_PATH)},
        ),
        "logsWrite": health_component("unknown", "Log write diagnostics are refreshing.", {"directory": str(LOG_DIR)}),
        "artifactsWrite": health_component("unknown", "Artifact write diagnostics are refreshing.", {"directory": str(ARTIFACTS_DIR)}),
        "selectedUnityProject": health_component(
            "unknown" if DASHBOARD_STATE.selected_project_path else "warning",
            "Selected Unity project diagnostics are refreshing." if DASHBOARD_STATE.selected_project_path else "No Unity project selected.",
            {"path": DASHBOARD_STATE.selected_project_path},
        ),
        "unityPluginInstalled": health_component("unknown", "Unity plugin status is refreshing.", ""),
        "mcpPackageConfigured": health_component("unknown", "VRCForge MCP Core status is refreshing.", ""),
        "providerConfigPresent": health_component(
            "ok" if not api_config.get("apiKeyRequired") or bool(api_config.get("apiKeyPresent")) else "warning",
            "Provider configuration is present." if not api_config.get("apiKeyRequired") or bool(api_config.get("apiKeyPresent")) else f"{api_config.get('providerLabel') or api_config.get('provider') or 'Provider'} API key is not configured.",
            {"provider": api_config.get("provider"), "model": api_config.get("model")},
        ),
        "agentGateway": health_component(
            "ok" if agent_health.get("enabled") else "warning",
            "Agent Gateway is enabled." if agent_health.get("enabled") else "Agent Gateway is disabled until enabled in the Launcher.",
            {
                "mcpUrl": agent_health.get("mcpUrl"),
                "restUrl": agent_health.get("restUrl"),
                "pendingApprovalCount": agent_health.get("pendingApprovalCount"),
                "allowRoslynAdvanced": agent_health.get("allowRoslynAdvanced"),
            },
        ),
    }
    if isinstance(unity_status, dict):
        connected = bool(unity_status.get("connected"))
        missing_tools = unity_status.get("missingRequiredVrcForgeTools") or []
        vrcforge_tools_registered = bool(unity_status.get("vrcForgeToolsRegistered"))
        components["unityMcpBridgeReachable"] = health_component(
            "ok" if connected else "warning",
            "Unity MCP bridge is reachable." if connected else "Unity MCP bridge is not reachable.",
            unity_status if connected else unity_status.get("error") or unity_status,
        )
        components["unityMcpInstance"] = health_component(
            "ok" if unity_status.get("unityInstanceRegistered") else "warning",
            "Unity instance is registered with MCP." if unity_status.get("unityInstanceRegistered") else "MCP server is reachable, but no Unity instance is registered.",
            {
                "activeInstance": unity_status.get("activeInstance"),
                "activeInstanceCount": unity_status.get("activeInstanceCount"),
                "selectedInstanceMatched": unity_status.get("selectedInstanceMatched"),
            },
        )
        components["vrcForgeUnityTools"] = health_component(
            "ok" if vrcforge_tools_registered and not missing_tools else "warning",
            "VRCForge Unity tools are registered."
            if vrcforge_tools_registered and not missing_tools
            else "Unity MCP is connected, but VRCForge Unity tools are missing or incomplete.",
            {
                "totalTools": (unity_status.get("tools") or {}).get("totalTools"),
                "vrcForgeToolsCount": (unity_status.get("tools") or {}).get("vrcForgeToolsCount"),
                "missingRequiredVrcForgeTools": missing_tools,
            },
        )
    else:
        components["unityMcpBridgeReachable"] = health_component("unknown", "Unity MCP bridge status is refreshing.", "")
        components["unityMcpInstance"] = health_component("unknown", "Unity instance status is refreshing.", "")
        components["vrcForgeUnityTools"] = health_component("unknown", "VRCForge Unity tool status is refreshing.", "")

    return {
        "ok": not any(component["status"] == "error" for component in components.values()),
        "schema": "vrcforge.bootstrap_health.v1",
        "deferredDiagnostics": True,
        "version": app.version,
        "portableMode": PORTABLE_MODE,
        "projectRoot": str(ROOT_DIR),
        "settingsPath": str(RUNTIME_SETTINGS_PATH),
        "configPath": str(CONFIG_PATH),
        "paths": {
            "programDir": str(ROOT_DIR),
            "userDataDir": str(USER_DATA_DIR),
            "configDir": str(CONFIG_DIR),
            "logsDir": str(LOG_DIR),
            "artifactsDir": str(ARTIFACTS_DIR),
            "dashboardDir": str(DASHBOARD_DIR),
        },
        "components": components,
        "defaults": {
            "provider": api_config.get("provider"),
            "model": api_config.get("model"),
            "baseUrl": api_config.get("base_url"),
            "sourceMode": "unity_live_export",
            "exportJson": str(DEFAULT_MVP_EXPORT_PATH),
            "planJson": "",
            "mockExecute": False,
            "minConfidence": 0.65,
            "unityHost": DASHBOARD_STATE.unity_host,
            "unityPort": DASHBOARD_STATE.unity_port,
            "unityInstance": DASHBOARD_STATE.unity_instance,
        },
        "state": serialize_dashboard_state(),
        "projects": projects,
        "logRetentionHours": int(LOG_RETENTION.total_seconds() // 3600),
        "unityStatus": unity_status,
    }


def safe_agent_manifest() -> dict[str, Any]:
    try:
        return AGENT_GATEWAY.build_manifest()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "schema": "vrcforge.agent-gateway.v1",
            "tools": [],
            "toolCount": 0,
            "skills": [],
            "writeTargets": [],
            "error": str(exc),
        }


def safe_agent_health() -> dict[str, Any]:
    try:
        return AGENT_GATEWAY.build_health()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "enabled": False,
            "pendingApprovalCount": 0,
            "allowRoslynAdvanced": False,
            "error": str(exc),
        }


def safe_permission_state() -> dict[str, Any]:
    try:
        return AGENT_GATEWAY.permission_state()
    except Exception as exc:  # noqa: BLE001
        return {
            "executionMode": "approval",
            "allowRoslynAdvanced": False,
            "roslynFullAuto": False,
            "roslynRiskAcknowledged": False,
            "error": str(exc),
        }


def safe_approval_list(project_root: str = "") -> list[dict[str, Any]]:
    try:
        return AGENT_GATEWAY.list_approvals(include_expired=False, project_root=project_root)
    except Exception:  # noqa: BLE001
        return []


def load_diagnostics_config() -> dict[str, Any]:
    state = diagnostics_state()
    return {
        "schema": state["schema"],
        "logLevel": state["logLevel"],
        "debugLogging": state["debugLogging"],
    }


def save_diagnostics_config(payload: dict[str, Any]) -> dict[str, Any]:
    log_level = payload.get("logLevel", payload.get("log_level"))
    debug_logging = payload.get("debugLogging", payload.get("debug_logging"))
    with ADVANCED_SETTINGS_TRANSITION_LOCK:
        if str(log_level or "").strip().lower() == "trace" and not developer_options_enabled_for_diagnostics():
            raise ValueError("Trace logging requires Developer Options.")
        DIAGNOSTIC_LOGGER.update_config(log_level=log_level, debug_logging=debug_logging)
        state = diagnostics_state()
    return {
        "schema": state["schema"],
        "logLevel": state["logLevel"],
        "debugLogging": state["debugLogging"],
    }


def diagnostics_state() -> dict[str, Any]:
    state = DIAGNOSTIC_LOGGER.status()
    posture = safety_posture_state()
    developer_enabled = bool(posture["developerOptions"]["enabled"])
    return {
        **state,
        "logLevels": available_log_levels(developer_enabled),
        "traceRequiresDeveloperOptions": TRACE_REQUIRES_DEVELOPER_OPTIONS,
        "safetyPosture": posture,
    }


def developer_options_enabled_for_diagnostics() -> bool:
    try:
        state = AGENT_GATEWAY.advanced_settings_state()
    except Exception:  # noqa: BLE001 - trace availability fails closed.
        return False
    return bool(state.get("developerOptionsEnabled"))


def safety_posture_state() -> dict[str, Any]:
    try:
        permission = AGENT_GATEWAY.permission_state()
    except Exception:  # noqa: BLE001 - posture uses safe defaults, never raw exception text.
        permission = {}
    try:
        advanced = AGENT_GATEWAY.advanced_settings_state()
    except Exception:  # noqa: BLE001 - posture uses safe defaults, never raw exception text.
        advanced = {}
    return build_safety_posture(permission, advanced, DIAGNOSTIC_LOGGER.log_level)


async def emit_safety_posture_snapshot(phase: Literal["startup", "normal_shutdown"]) -> None:
    posture = await asyncio.to_thread(safety_posture_state)
    message = (
        "Safety posture captured at startup."
        if phase == "startup"
        else "Safety posture captured for normal shutdown."
    )
    await emit_log_async(
        "info",
        "safety",
        message,
        {"phase": phase, "posture": posture},
        essential=True,
    )


def reconcile_diagnostic_trace_policy() -> bool:
    with ADVANCED_SETTINGS_TRANSITION_LOCK:
        if DIAGNOSTIC_LOGGER.log_level != "trace" or developer_options_enabled_for_diagnostics():
            return False
        DIAGNOSTIC_LOGGER.update_config(log_level="debug")
        return True


def debug_logging_enabled() -> bool:
    return DIAGNOSTIC_LOGGER.log_level in {"debug", "trace"}


def summarize_debug_payload(value: Any) -> Any:
    value = DIAGNOSTIC_PRIVACY.redact(value, context=current_diagnostic_identity_context())
    if isinstance(value, dict):
        return {str(key): summarize_debug_payload(item) for key, item in list(value.items())[:40]}
    if isinstance(value, list):
        return {"type": "list", "count": len(value), "items": [summarize_debug_payload(item) for item in value[:5]]}
    if isinstance(value, str):
        if _looks_like_local_path(value):
            return _redact_local_path(value)
        return value[:500] + ("..." if len(value) > 500 else "")
    return value


def raw_request_path_and_query(request: Request) -> str:
    raw_path = request.scope.get("raw_path")
    if isinstance(raw_path, bytes):
        try:
            path = raw_path.decode("ascii")
        except UnicodeDecodeError:
            return ""
    else:
        path = request.url.path
    query = request.scope.get("query_string", b"")
    if isinstance(query, bytes):
        try:
            query_text = query.decode("ascii")
        except UnicodeDecodeError:
            return ""
    else:
        query_text = str(query or "")
    return f"{path}?{query_text}" if query_text else path


def request_transport_component(request: Request) -> Literal["ipc", "http"]:
    # Cooperative diagnostic attribution only. The persistent local session
    # token makes this replayable by another same-user process; request
    # authentication remains a separate boundary.
    marker = str(request.headers.get("x-vrcforge-transport") or "").strip()
    proof = str(request.headers.get("x-vrcforge-transport-proof") or "").strip()
    if marker != "tauri-ipc-bridge" or not APP_SESSION_TOKEN or re.fullmatch(r"[0-9a-fA-F]{64}", proof) is None:
        return "http"
    raw_target = raw_request_path_and_query(request)
    if not raw_target:
        return "http"
    message = f"vrcforge.tauri-ipc-bridge.v1\n{request.method.upper()}\n{raw_target}".encode("utf-8")
    expected = hmac.new(APP_SESSION_TOKEN.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return "ipc" if hmac.compare_digest(proof.lower(), expected) else "http"


def current_owned_uvicorn_server() -> uvicorn.Server | None:
    with UVICORN_SERVER_LOCK:
        return CURRENT_UVICORN_SERVER


def register_owned_uvicorn_server(server: uvicorn.Server) -> None:
    global CURRENT_UVICORN_SERVER
    with UVICORN_SERVER_LOCK:
        if CURRENT_UVICORN_SERVER is not None:
            raise RuntimeError("A VRCForge uvicorn server is already registered.")
        CURRENT_UVICORN_SERVER = server


def clear_owned_uvicorn_server(server: uvicorn.Server) -> None:
    global CURRENT_UVICORN_SERVER
    with UVICORN_SERVER_LOCK:
        if CURRENT_UVICORN_SERVER is server:
            CURRENT_UVICORN_SERVER = None


def signal_owned_uvicorn_server_exit(server: uvicorn.Server) -> bool:
    with UVICORN_SERVER_LOCK:
        if CURRENT_UVICORN_SERVER is not server:
            return False
        server.should_exit = True
        return True


def run_owned_uvicorn_server(
    host: str,
    port: int,
    *,
    sockets: list[socket.socket] | None = None,
) -> None:
    config = uvicorn.Config(app=app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    register_owned_uvicorn_server(server)
    try:
        if sockets is None:
            server.run()
        else:
            server.run(sockets=sockets)
    finally:
        clear_owned_uvicorn_server(server)


def record_debug_interaction(entry: dict[str, Any], *, component: str = "http") -> None:
    normalized_component = "ipc" if component == "ipc" else "http"
    method = str(entry.get("method") or "").strip().upper()
    try:
        status = int(entry.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    has_error = bool(str(entry.get("error") or "").strip())
    if status >= 500 or (has_error and not 400 <= status < 500):
        level = "error"
    elif 400 <= status < 500:
        level = "warn"
    elif method in {"GET", "HEAD", "OPTIONS"} and 0 < status < 400:
        level = "trace"
    else:
        level = "debug"
    payload = {
        **entry,
        "component": normalized_component,
        "kind": "tauri_ipc_bridge" if normalized_component == "ipc" else "direct_http",
    }
    message = "Tauri IPC bridge interaction." if normalized_component == "ipc" else "Direct HTTP interaction."
    emit_log(level, normalized_component, message, payload)


def read_jsonl_tail(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):]
    except OSError:
        return []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.append(payload if isinstance(payload, dict) else {"value": payload})
    return entries


def read_text_tail(path: Path, limit: int = 200) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(limit, 500)):]
    except OSError:
        return []


def redact_support_payload(value: Any, include_full_paths: bool = False) -> Any:
    # includeFullPaths is accepted for wire compatibility only. Support bundles
    # always remain safe to share and never reverse pre-persistence redaction.
    return DIAGNOSTIC_PRIVACY.redact(value, context=current_diagnostic_identity_context())


def write_support_bundle_member(bundle: zipfile.ZipFile, name: str, payload: Any, include_full_paths: bool = False) -> None:
    try:
        redacted = redact_support_payload(payload, include_full_paths=include_full_paths)
    except Exception:  # noqa: BLE001 - omit private content when redaction storage is unavailable.
        redacted = {"omitted": True, "reason": "redaction_unavailable"}
    bundle.writestr(name, json.dumps(redacted, ensure_ascii=False, indent=2, sort_keys=True))


def write_support_bundle_text_member(bundle: zipfile.ZipFile, name: str, lines: list[str]) -> None:
    safe_lines: list[str] = []
    for line in lines:
        try:
            parsed = parse_diagnostic_log_line(line)
            if parsed is None:
                continue
            redacted = DIAGNOSTIC_PRIVACY.redact(parsed, context=current_diagnostic_identity_context())
            if not isinstance(redacted, dict):
                continue
            safe_lines.append(format_diagnostic_log_line(redacted))
        except Exception:  # noqa: BLE001 - never fall back to an unredacted diagnostic line.
            continue
    bundle.writestr(name, ("\n".join(safe_lines) + "\n") if safe_lines else "")


def build_support_bundle(request: SupportBundleRequest) -> dict[str, Any]:
    SUPPORT_BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc)
    bundle_path = SUPPORT_BUNDLE_DIR / f"vrcforge-support-{generated_at.strftime('%Y%m%d-%H%M%S')}.zip"
    log_limit = max(1, min(int(request.log_limit), 500))
    metadata = {
        "schema": "vrcforge.support-bundle.v1",
        "generatedAt": generated_at.isoformat(),
        "version": app.version,
        "portableMode": PORTABLE_MODE,
        "logLevel": DIAGNOSTIC_LOGGER.log_level,
        "debugLogging": debug_logging_enabled(),
        "includeFullPathsRequested": bool(request.include_full_paths),
        "includeFullPaths": False,
        "privacy": {
            "redactsSecrets": True,
            "includesScreenshots": False,
            "includesPaidAssetContents": False,
            "includesFullPaths": False,
            "redactsBeforeDisk": True,
            "includesIdentityMapping": False,
        },
    }
    try:
        bootstrap = build_agentic_app_bootstrap_payload(refresh_projects=False)
    except Exception as exc:  # noqa: BLE001
        bootstrap = {"ok": False, "error": str(exc)}
    try:
        doctor = read_agentic_app_doctor()
    except Exception as exc:  # noqa: BLE001
        doctor = {"ok": False, "error": str(exc)}
    try:
        checkpoints = AGENT_GATEWAY.list_checkpoints({"limit": 50})
    except Exception as exc:  # noqa: BLE001
        checkpoints = {"ok": False, "error": str(exc)}
    diagnostics = diagnostics_state()
    diagnostics.pop("identities", None)
    safety_posture = diagnostics.get("safetyPosture") or safety_posture_state()
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        write_support_bundle_member(bundle, "metadata.json", metadata, request.include_full_paths)
        write_support_bundle_member(bundle, "bootstrap.json", bootstrap, request.include_full_paths)
        write_support_bundle_member(bundle, "doctor.json", doctor, request.include_full_paths)
        write_support_bundle_member(bundle, "diagnostics.json", diagnostics, request.include_full_paths)
        write_support_bundle_member(bundle, "safety-posture.json", safety_posture, request.include_full_paths)
        write_support_bundle_text_member(bundle, "diagnostic-log.txt", DIAGNOSTIC_LOGGER.tail_lines(log_limit))
        write_support_bundle_member(bundle, "agent-audit.json", AGENT_GATEWAY.recent_audit_logs(limit=log_limit), request.include_full_paths)
        write_support_bundle_member(bundle, "sub-agent-events.json", _SUB_AGENT_COLLABORATION.recent_events(limit=log_limit), request.include_full_paths)
        write_support_bundle_member(bundle, "sub-agent-tasks.json", _SUB_AGENT_COLLABORATION.list_tasks(include_events=False, limit=log_limit), request.include_full_paths)
        write_support_bundle_member(bundle, "checkpoints.json", checkpoints, request.include_full_paths)
    emit_log(
        "success",
        "diagnostics",
        "Support bundle exported.",
        {"bundlePath": str(bundle_path), "debugLogging": debug_logging_enabled(), "logLimit": log_limit},
    )
    return {
        "ok": True,
        "schema": "vrcforge.support-bundle.v1",
        "bundlePath": str(bundle_path),
        "bundleUrl": to_artifact_url(str(bundle_path)),
        "bytes": bundle_path.stat().st_size,
        "debugLogging": debug_logging_enabled(),
        "redacted": True,
    }


def _status_from_counts(error_count: int = 0, warning_count: int = 0, unknown_count: int = 0) -> str:
    if error_count > 0:
        return "error"
    if warning_count > 0:
        return "warning"
    if unknown_count > 0:
        return "unknown"
    return "ok"


def _doctor_section_for_id(check_id: str) -> str:
    if check_id.startswith("desktop.") or check_id.startswith("backend.") or check_id.startswith("doctor.") or check_id.startswith("app."):
        return "Runtime"
    if check_id.startswith("unity."):
        return "Unity environment"
    if check_id.startswith("package."):
        return "SDK / dependencies"
    if check_id.startswith("provider."):
        return "Providers"
    if check_id.startswith("agent.") or check_id.startswith("external."):
        return "External agents"
    if check_id.startswith("skills."):
        return "Skills"
    if check_id.startswith("checkpoint."):
        return "Rollback"
    if check_id.startswith("session."):
        return "Session storage"
    if check_id.startswith("security."):
        return "Security"
    return "Doctor"


def _doctor_section_id(check_id: str) -> str:
    section = _doctor_section_for_id(check_id)
    return {
        "Runtime": "runtime",
        "Unity environment": "unity",
        "SDK / dependencies": "packages",
        "Providers": "providers",
        "External agents": "external",
        "Skills": "skills",
        "Rollback": "rollback",
        "Session storage": "sessions",
        "Security": "security",
    }.get(section, "doctor")


def _doctor_fix_command_for_id(check_id: str) -> str:
    commands = {
        "unity.project_root": "Open Project Picker and select the Unity project root used by the bridge.",
        "unity.plugin": "Run Unity plugin install/repair for the selected project.",
        "unity.mcp.package": "Repair the VRCForge Unity plugin; MCP Core is bundled and needs no separate package.",
        "unity.mcp.bridge": "Use Repair bridge to start the local MCP server and reconnect Unity, then Retry.",
        "unity.mcp.instance": "Use Repair bridge to wait for or relaunch the selected Unity project, then Retry.",
        "unity.tools": "Wait for Unity compile, then repair/reinstall the VRCForge plugin if tools remain missing.",
        "package.vrchat_sdk": "Install the VRChat Avatar SDK through VCC, ALCOM, or vrc-get.",
        "package.modular_avatar": "Install Modular Avatar if this avatar or outfit workflow requires it.",
        "package.vrcfury": "Install VRCFury only if this avatar uses VRCFury components.",
        "package.manager": "Install vrc-get or use VCC/ALCOM UI for package changes.",
        "provider.configured": "Open Settings > Providers and choose BYOK, Ollama, or manual/read-only mode.",
        "provider.test": "Open Settings > Providers and run an explicit provider test.",
        "provider.local_ollama": "Start Ollama and run the provider test when using local/offline mode.",
        "agent.gateway": "Open Settings > Agent Connectors before enabling or revoking external access.",
        "skills.registry": "Open Skill Manager, inspect broken skills, and disable or remove unsafe packages.",
        "checkpoint.backend": "Open logs and repair checkpoint storage before approving writes.",
        "external.security_contract": "Keep external agents on write-request tools; approve writes only inside VRCForge.",
    }
    return commands.get(check_id, "")


def _looks_like_local_path(value: str) -> bool:
    stripped = value.strip()
    if not stripped or stripped.startswith(("http://", "https://")):
        return False
    if stripped.startswith(("/api/", "/mcp", "/artifacts/")):
        return False
    return bool(re.match(r"^[A-Za-z]:[\\/]", stripped) or stripped.startswith("\\\\") or stripped.startswith("/"))


def _redact_local_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        name = Path(text).name
    except (OSError, ValueError):
        name = ""
    return f".../{name}" if name else "<redacted path>"


def _redact_doctor_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        if parsed.scheme.lower() in {"http", "https", "ws", "wss"} and parsed.hostname:
            host = parsed.hostname
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port is not None else ""
            return f"{parsed.scheme.lower()}://{host}{port}"
    except (TypeError, ValueError):
        pass
    if "://" in text or "@" in text:
        return "<redacted url>"
    return text.split("?", 1)[0].split("#", 1)[0]


def _redact_doctor_detail(value: Any, key_hint: str = "") -> Any:
    key = key_hint.lower()
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for item_key, item_value in value.items():
            lower_key = str(item_key).lower()
            if "url" in lower_key:
                redacted[str(item_key)] = _redact_doctor_url(item_value)
            elif any(marker in lower_key for marker in ("path", "directory", "folder", "root")):
                redacted[str(item_key)] = _redact_local_path(item_value)
            else:
                redacted[str(item_key)] = _redact_doctor_detail(item_value, lower_key)
        return redacted
    if isinstance(value, list):
        return [_redact_doctor_detail(item, key_hint) for item in value]
    if isinstance(value, str):
        if "url" in key or re.match(r"^(?:https?|wss?)://", value.strip(), flags=re.IGNORECASE):
            return _redact_doctor_url(value)
        if _looks_like_local_path(value) or any(marker in key for marker in ("path", "directory", "folder", "root")):
            return _redact_local_path(value)
    return value


def _doctor_check(
    check_id: str,
    title: str,
    status: str,
    message: str,
    why_it_matters: str,
    how_to_fix: str,
    detail: Any = None,
    actions: list[str] | None = None,
    fixable: bool = False,
    fix_modes: list[str] | None = None,
) -> dict[str, Any]:
    if status not in {"ok", "warning", "error", "unknown"}:
        status = "unknown"
    safe_title = sanitize_doctor_text(title)
    safe_message = sanitize_doctor_text(message)
    safe_why = sanitize_doctor_text(why_it_matters)
    safe_how = sanitize_doctor_text(how_to_fix)
    return {
        "id": check_id,
        "section": _doctor_section_for_id(check_id),
        "sectionId": _doctor_section_id(check_id),
        "title": safe_title,
        "status": status,
        "message": safe_message,
        "whatFailed": "" if status == "ok" else safe_message,
        "whyItMatters": safe_why,
        "howToFix": safe_how,
        "fixCommand": _doctor_fix_command_for_id(check_id),
        "fixable": fixable,
        "fixModes": fix_modes or (["safe"] if fixable else []),
        "actions": actions or ["retry", "open_logs", "copy_diagnostic_summary"],
        "detail": sanitize_doctor_value(_redact_doctor_detail(detail)),
    }


def _doctor_check_from_component(
    check_id: str,
    title: str,
    component: dict[str, Any] | None,
    why_it_matters: str,
    how_to_fix: str,
    missing_status: str = "unknown",
    actions: list[str] | None = None,
    fixable: bool = False,
) -> dict[str, Any]:
    component = component if isinstance(component, dict) else {}
    status = str(component.get("status") or missing_status)
    message = str(component.get("message") or "Check did not report a result.")
    return _doctor_check(
        check_id,
        title,
        status,
        message,
        why_it_matters,
        how_to_fix,
        component.get("detail"),
        actions=actions,
        fixable=fixable,
    )


def _doctor_summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "okCount": sum(1 for check in checks if check.get("status") == "ok"),
        "warningCount": sum(1 for check in checks if check.get("status") == "warning"),
        "errorCount": sum(1 for check in checks if check.get("status") == "error"),
        "unknownCount": sum(1 for check in checks if check.get("status") == "unknown"),
    }


def _doctor_sections(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        section = str(check.get("section") or "Doctor")
        sections.setdefault(section, []).append(check)
    order = ["Runtime", "Unity environment", "SDK / dependencies", "Providers", "External agents", "Skills", "Rollback", "Session storage", "Security", "Doctor"]
    names = [name for name in order if name in sections] + sorted(name for name in sections if name not in order)
    return [
        {
            "id": _doctor_section_id(str(sections[name][0].get("id") or "")),
            "name": name,
            "summary": _doctor_summary(sections[name]),
            "checkIds": [str(check.get("id") or "") for check in sections[name]],
        }
        for name in names
    ]


def _selected_project_path_from_health(health: dict[str, Any]) -> str:
    projects = health.get("projects") if isinstance(health.get("projects"), dict) else {}
    state = health.get("state") if isinstance(health.get("state"), dict) else {}
    return str(
        projects.get("selectedProjectPath")
        or state.get("selectedProjectPath")
        or DASHBOARD_STATE.selected_project_path
        or ""
    ).strip()


def _package_entry_version(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("version") or entry.get("hash") or entry.get("url") or "").strip()
    return str(entry or "").strip()


def detect_unity_project_package(project_path: Path | None, package_ids: list[str]) -> dict[str, Any]:
    info: dict[str, Any] = {"installed": False, "packageId": "", "version": "", "source": "", "checkedPackageIds": package_ids}
    if project_path is None:
        info["warning"] = "No Unity project selected; package detection skipped."
        return info
    packages_dir = project_path / "Packages"
    for package_id in package_ids:
        embedded = packages_dir / package_id / "package.json"
        if embedded.exists():
            try:
                data = json.loads(embedded.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                data = {}
            info.update({"installed": True, "packageId": package_id, "version": str(data.get("version") or ""), "source": "embedded"})
            return info
    manifest_specs = (
        ("manifest.json", "upm", ("dependencies",)),
        ("packages-lock.json", "lock", ("dependencies",)),
        ("vpm-manifest.json", "vpm", ("locked", "dependencies")),
    )
    for manifest_name, source, section_names in manifest_specs:
        manifest_path = packages_dir / manifest_name
        payload = load_manifest_payload(manifest_path)
        if not payload:
            continue
        for section_name in section_names:
            section = payload.get(section_name)
            if not isinstance(section, dict):
                continue
            for package_id in package_ids:
                if package_id not in section:
                    continue
                info.update(
                    {
                        "installed": True,
                        "packageId": package_id,
                        "version": _package_entry_version(section.get(package_id)),
                        "source": source,
                    }
                )
                return info
    return info


def _package_doctor_check(
    check_id: str,
    title: str,
    project_path: Path | None,
    package_ids: list[str],
    why_it_matters: str,
    how_to_fix: str,
    optional: bool = False,
) -> dict[str, Any]:
    info = detect_unity_project_package(project_path, package_ids)
    if project_path is None:
        status = "unknown"
        message = "No Unity environment root is selected; dependency version detection was skipped."
    elif info.get("installed"):
        status = "ok"
        version = str(info.get("version") or "").strip()
        suffix = f" {version}" if version else ""
        message = f"{title} is detected{suffix}."
    else:
        status = "warning" if optional else "error"
        message = f"{title} was not detected."
    return _doctor_check(check_id, title, status, message, why_it_matters, how_to_fix, info)


_APP_DOCTOR_SERVICE: DoctorService | None = None
_APP_DOCTOR_SERVICE_LOCK = Lock()


def _doctor_component_detect(component_key: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def detect(context: dict[str, Any]) -> dict[str, Any]:
        health = context.get("health") if isinstance(context.get("health"), dict) else {}
        components = health.get("components") if isinstance(health.get("components"), dict) else {}
        component = components.get(component_key) if isinstance(components.get(component_key), dict) else {}
        status = str(component.get("status") or "unknown")
        if status not in {"ok", "warning", "error", "unknown"}:
            status = "unknown"
        return {
            "status": status,
            "message": str(component.get("message") or "The component did not report a result."),
            "detail": component.get("detail") if isinstance(component.get("detail"), dict) else {},
        }

    return detect


def _detect_checkpoint_doctor(_context: dict[str, Any]) -> dict[str, Any]:
    inspection = AGENT_GATEWAY.inspect_checkpoint_storage()
    status = str(inspection.get("status") or "unknown")
    message = {
        "ok": "Checkpoint storage and its JSONL projection are healthy.",
        "warning": "Checkpoint storage needs a safe repair before new writes are trusted.",
        "error": "Checkpoint storage is unsafe or unreadable; writes must remain blocked.",
    }.get(status, "Checkpoint storage could not be fully inspected.")
    return {"status": status, "message": message, "detail": inspection}


def _repair_checkpoint_doctor(_context: dict[str, Any], _mode: str, phases: PhaseLog) -> dict[str, Any]:
    before = AGENT_GATEWAY.inspect_checkpoint_storage()
    phases.add("inspect", "ok" if before.get("fixable") else "warning", "Checkpoint storage was inspected under its writer lock.")
    result = AGENT_GATEWAY.repair_checkpoint_storage(expected_snapshot=str(before.get("snapshot") or ""))
    status = str(result.get("status") or "failed")
    phases.add(
        "repair",
        "ok" if status in {"healthy", "repaired"} else "warning" if status in {"busy", "needs_user_action"} else "error",
        "Checkpoint storage repair finished without deleting recovery evidence.",
        {"status": status, "changed": bool(result.get("changed")), "quarantined": bool(result.get("quarantineId"))},
    )
    return {
        "status": status if status in {"healthy", "repaired", "busy", "needs_user_action"} else "failed",
        "changed": bool(result.get("changed")),
    }


def _skill_triage_state(skill: dict[str, Any]) -> tuple[str, str]:
    validation = skill.get("validation") if isinstance(skill.get("validation"), dict) else {}
    status = str(validation.get("status") or "ok")
    reasons = [str(item).lower() for item in validation.get("reasons") or [] if isinstance(item, str)]
    combined = " ".join(reasons)
    if any(marker in combined for marker in ("missing env", "missing binaries", "unsupported os", "unavailable", "dependencies")):
        return "missing_requirements", "requirements_missing"
    if any(marker in combined for marker in ("allowed tools", "disallowed", "entrypoint tool", "allowlist")):
        return "blocked_allowlist", "tool_policy_blocked"
    if status == "error" or skill.get("loadError"):
        return "broken", "manifest_or_integrity_error"
    if not skill.get("enabled", True):
        return "missing_requirements", "disabled"
    if not skill.get("available", True) or status == "warning":
        return "missing_requirements", "requirements_unavailable"
    return "eligible", "eligible"


def _skill_signature_failed(entry: dict[str, Any]) -> bool:
    candidates = [
        entry.get("signatureStatus"),
        entry.get("signature_status"),
        ensure_dict(entry.get("signature")).get("status"),
        ensure_dict(entry.get("governance")).get("signatureStatus"),
    ]
    return any(str(value or "").strip().lower() in {"invalid", "failed", "tampered", "mismatch"} for value in candidates)


def _skill_doctor_snapshot() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    registry = AGENT_GATEWAY.build_skill_registry()
    skills = registry.get("skills") if isinstance(registry.get("skills"), list) else []
    rows: list[dict[str, Any]] = []
    repair_candidates: list[dict[str, Any]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        state, reason = _skill_triage_state(skill)
        skill_id = str(skill.get("name") or "unknown")
        rows.append(
            {
                "id": skill_id,
                "name": str(skill.get("title") or skill_id)[:120],
                "state": state,
                "reason": reason,
            }
        )
        if state == "broken" and str(skill.get("source") or "") == "user":
            storage_path = Path(str(skill.get("storagePath") or ""))
            try:
                manifest_digest = hashlib.sha256(storage_path.read_bytes()).hexdigest()
            except OSError:
                manifest_digest = ""
            repair_candidates.append(
                {
                    "kind": "user_manifest",
                    "id": skill_id,
                    "storagePath": skill.get("storagePath"),
                    "manifestDigest": manifest_digest,
                }
            )

    try:
        service = skill_package_service()
        installed = service.list_installed()
    except Exception:
        installed = []
        rows.append(
            {
                "id": "skill-package-registry",
                "name": "Skill package registry",
                "state": "broken",
                "reason": "registry_unreadable",
            }
        )
    for entry in installed:
        if not isinstance(entry, dict) or not _skill_signature_failed(entry):
            continue
        package_id = str(entry.get("skill_id") or entry.get("skillId") or entry.get("id") or "").strip()
        if package_id:
            repair_candidates.append({"kind": "package_signature", "id": package_id, "enabled": bool(entry.get("enabled", True))})
            rows.append({"id": package_id, "name": package_id, "state": "broken", "reason": "signature_failure"})
    rows.sort(key=lambda item: (item["state"] != "broken", item["name"].casefold(), item["id"]))
    return registry, rows, repair_candidates


def _detect_skills_doctor(_context: dict[str, Any]) -> dict[str, Any]:
    registry, rows, _candidates = _skill_doctor_snapshot()
    broken = sum(1 for row in rows if row["state"] == "broken")
    attention = sum(1 for row in rows if row["state"] != "eligible")
    status = "error" if broken else "warning" if attention else "ok"
    return {
        "status": status,
        "message": "Skill registry is healthy." if status == "ok" else "Skill registry contains unavailable or broken entries.",
        "detail": {
            "schema": registry.get("schema"),
            "count": len(rows),
            "brokenCount": broken,
            "attentionCount": attention,
            "rows": rows,
        },
    }


def _path_is_reparse_or_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(reparse_flag and attributes & reparse_flag)
    except OSError:
        return True


def _quarantine_broken_user_skill(candidate: dict[str, Any]) -> bool:
    storage_value = str(candidate.get("storagePath") or "").strip()
    if not storage_value:
        return False
    source = Path(storage_value)
    root = AGENT_GATEWAY.user_skills_dir.resolve()
    try:
        resolved_source = source.resolve(strict=True)
        resolved_source.relative_to(root)
    except (OSError, ValueError):
        return False
    skill_dir = resolved_source.parent
    if skill_dir.parent != root or _path_is_reparse_or_link(skill_dir) or _path_is_reparse_or_link(resolved_source):
        return False
    expected_digest = str(candidate.get("manifestDigest") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        return False
    raw = resolved_source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        return False
    quarantine_root = AGENT_GATEWAY.user_constraints_path.parent / "quarantine" / "skills"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    destination = quarantine_root / f"{skill_dir.name}.{hashlib.sha256(raw).hexdigest()[:16]}.quarantine"
    if destination.exists():
        return not skill_dir.exists()
    os.replace(skill_dir, destination)
    return True


def _repair_skills_doctor(_context: dict[str, Any], _mode: str, phases: PhaseLog) -> dict[str, Any]:
    changed = False
    failures = 0
    with SKILL_PACKAGE_WRITE_LOCK, AGENT_GATEWAY.user_skill_lock:
        _registry, rows, candidates = _skill_doctor_snapshot()
        candidate_ids = {str(candidate.get("id") or "") for candidate in candidates}
        unresolved_broken = any(
            row.get("state") == "broken" and str(row.get("id") or "") not in candidate_ids
            for row in rows
        )
        for candidate in candidates:
            try:
                if candidate.get("kind") == "user_manifest":
                    candidate_changed = _quarantine_broken_user_skill(candidate)
                else:
                    result = skill_package_service().set_enabled(str(candidate.get("id") or ""), False)
                    candidate_changed = bool(result.changed)
                changed = candidate_changed or changed
            except Exception:
                failures += 1
    phases.add(
        "quarantine_or_disable",
        "error" if failures else "ok",
        "Broken user manifests were quarantined and signature failures were disabled without deletion.",
        {"candidateCount": len(candidates), "changed": changed, "failureCount": failures},
    )
    if failures or unresolved_broken:
        return {"status": "needs_user_action", "changed": changed}
    if changed:
        return {"status": "repaired", "changed": True}
    return {"status": "needs_user_action" if candidates else "healthy", "changed": False}


def _session_store_targets(context: dict[str, Any]) -> list[SessionStoreTarget]:
    subagent_targets = _SUB_AGENT_COLLABORATION.maintenance_targets()
    targets = [
        SessionStoreTarget(
            "session.chat.app",
            chat_transcripts_path(),
            "app_owned",
            "json",
            required_list_field="chats",
            required_list_item_kind="chat",
            document_version_field="version",
            known_document_versions=(1,),
            guard_root=chat_transcripts_path().parent,
            max_bytes=CHAT_TRANSCRIPTS_MAX_BYTES,
            max_list_items=CHAT_TRANSCRIPTS_MAX_CHATS,
        ),
        SessionStoreTarget(
            "session.chat.index",
            chat_project_index_path(),
            "app_owned",
            "json",
            required_list_field="projectPaths",
            required_list_item_kind="nonempty_string",
            document_version_field="version",
            known_document_versions=(1,),
            guard_root=chat_project_index_path().parent,
            max_bytes=CHAT_TRANSCRIPTS_MAX_BYTES,
            max_list_items=CHAT_REQUESTED_PROJECT_PATH_LIMIT,
        ),
        SessionStoreTarget(
            "session.runtime-runs",
            AGENT_GATEWAY.runtime_run_log_path,
            "app_owned",
            "jsonl",
            ("vrcforge.runtime_run.v1",),
            schema_required=True,
            required_string_fields=("id", "createdAt", "event"),
            guard_root=AGENT_GATEWAY.runtime_run_log_path.parent,
        ),
        SessionStoreTarget(
            "session.agent-goals",
            AGENT_GOALS.log_path,
            "app_owned",
            "jsonl",
            ("vrcforge.agent_goal.v1", "vrcforge.agent_goal.v2"),
            schema_required=True,
            required_string_fields=("event",),
            guard_root=AGENT_GOALS.log_path.parent,
        ),
        SessionStoreTarget(
            "session.agent-progress",
            AGENT_GATEWAY.agent_progress_log_path,
            "app_owned",
            "jsonl",
            ("vrcforge.agent_progress.v1",),
            schema_required=True,
            required_string_fields=("id", "createdAt", "event"),
            guard_root=AGENT_GATEWAY.agent_progress_log_path.parent,
        ),
        SessionStoreTarget(
            "session.agent-questions",
            AGENT_QUESTIONS.log_path,
            "app_owned",
            "jsonl",
            ("vrcforge.agent_question.v1",),
            schema_required=True,
            required_string_fields=("id", "createdAt", "event"),
            guard_root=AGENT_QUESTIONS.log_path.parent,
        ),
        SessionStoreTarget(
            "session.subagent-events",
            subagent_targets.event_log_path,
            "app_owned",
            "jsonl",
            (subagent_targets.log_schema,),
            schema_required=True,
            required_string_fields=("timestamp", "taskId", "event"),
            required_object_fields=("task",),
            guard_root=subagent_targets.artifact_dir,
        ),
    ]
    with CHAT_TRANSCRIPTS_LOCK:
        remembered_project_paths = list(CHAT_REQUESTED_PROJECT_PATHS.values())
    project_values = {
        str(item).strip()
        for item in [*load_chat_project_index_paths(), *remembered_project_paths]
        if str(item).strip()
    }
    selected = str(context.get("selected_project_path") or "").strip()
    if selected:
        project_values.add(selected)
    for project_path in sorted(project_values):
        project_root = resolve_chat_project_root(project_path)
        if project_root is None:
            continue
        path = project_root / ".vrcforge" / "chat-transcripts.json"
        suffix = hashlib.sha256(normalize_chat_project_key(project_path).encode("utf-8", errors="replace")).hexdigest()[:16]
        targets.append(
            SessionStoreTarget(
                f"session.chat.project.{suffix}",
                path,
                "project_owned",
                "json",
                required_list_field="chats",
                required_list_item_kind="chat",
                document_version_field="version",
                known_document_versions=(1,),
                guard_root=project_root,
                max_bytes=CHAT_TRANSCRIPTS_MAX_BYTES,
                max_list_items=CHAT_TRANSCRIPTS_MAX_CHATS,
            )
        )
    for result_path in sorted(AGENT_GOALS.result_dir.glob("*.json")):
        suffix = hashlib.sha256(result_path.name.encode()).hexdigest()[:16]
        targets.append(
            SessionStoreTarget(
                f"session.goal-result.{suffix}",
                result_path,
                "app_owned",
                "json",
                (GOAL_DELIVERY_RESULT_SCHEMA,),
                schema_required=True,
                required_string_fields=("deliveryId", "goalId", "clientTurnId", "completedAt"),
                required_object_fields=("response",),
                guard_root=AGENT_GOALS.result_dir,
            )
        )
    subagent_results = subagent_targets.result_dir
    for result_path in sorted(subagent_results.glob("*.json")):
        suffix = hashlib.sha256(result_path.name.encode()).hexdigest()[:16]
        targets.append(
            SessionStoreTarget(
                f"session.subagent-result.{suffix}",
                result_path,
                "app_owned",
                "json",
                (subagent_targets.result_schema,),
                schema_required=True,
                required_string_fields=("taskId",),
                required_object_fields=("result",),
                guard_root=subagent_results,
            )
        )
    return targets


def _unavailable_session_project_stores(context: dict[str, Any]) -> list[dict[str, Any]]:
    values = {str(item).strip() for item in load_chat_project_index_paths() if str(item).strip()}
    selected = str(context.get("selected_project_path") or "").strip()
    if selected:
        values.add(selected)
    unavailable: list[dict[str, Any]] = []
    for value in sorted(values):
        if resolve_chat_project_root(value) is not None:
            continue
        suffix = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
        unavailable.append(
            {
                "schema": SESSION_STORE_INTEGRITY_SCHEMA,
                "storeId": f"session.chat.unavailable.{suffix}",
                "basename": "chat-transcripts.json",
                "scope": "project_owned",
                "format": "json",
                "status": "unsupported",
                "reason": "project_unavailable",
                "exists": False,
                "digest": "",
                "recordCount": 0,
                "invalidCount": 0,
                "unknownSchemaCount": 0,
                "semanticIssueCount": 1,
                "requiresApproval": False,
            }
        )
    return unavailable


def _detect_session_storage_doctor(context: dict[str, Any]) -> dict[str, Any]:
    report = scan_session_stores(_session_store_targets(context))
    stores = [
        *(report.get("stores") if isinstance(report.get("stores"), list) else []),
        *_unavailable_session_project_stores(context),
    ]
    app_damage = [item for item in stores if item.get("scope") == "app_owned" and item.get("status") == "needs_repair"]
    project_damage = [item for item in stores if item.get("scope") == "project_owned" and item.get("status") == "needs_repair"]
    unsupported = [item for item in stores if item.get("status") in {"error", "unsupported"}]
    status = "error" if app_damage else "warning" if project_damage or unsupported else "ok"
    return {
        "status": status,
        "message": "Session stores are healthy." if status == "ok" else "One or more session stores need explicit recovery.",
        "detail": {
            "storeCount": len(stores),
            "appRepairCount": len(app_damage),
            "projectApprovalCount": len(project_damage),
            "unsupportedCount": len(unsupported),
            "stores": stores,
        },
    }


def _repair_session_storage_doctor(context: dict[str, Any], _mode: str, phases: PhaseLog) -> dict[str, Any]:
    changed = False
    approval_queued = 0
    failures = 0
    recovered_store_count = 0
    detected_invalid_count = 0
    invalid_quarantined_count = 0
    approval_queued_invalid_count = 0
    unresolved_count = len(_unavailable_session_project_stores(context))
    for target in _session_store_targets(context):
        scan = scan_session_store(target)
        if scan.get("status") in {"error", "unsupported"}:
            unresolved_count += 1
            continue
        if scan.get("status") != "needs_repair":
            continue
        detected_invalid_count += int(scan.get("invalidCount") or 0)
        if target.scope == "project_owned":
            project_root = target.path.parent.parent
            try:
                pending = next(
                    (
                        approval
                        for approval in AGENT_GATEWAY.list_approvals(include_expired=False)
                        if approval.get("status") == "pending"
                        and approval.get("targetTool") == "vrcforge_repair_project_chat_store"
                        and ensure_dict(approval.get("arguments")).get("storeId") == target.store_id
                        and ensure_dict(approval.get("arguments")).get("expectedDigest") == str(scan.get("digest") or "")
                    ),
                    None,
                )
                request = {"ok": True, "status": "pending", "approval": pending} if pending else AGENT_GATEWAY.create_apply_request(
                    {
                        "target_tool": "vrcforge_repair_project_chat_store",
                        "arguments": {
                            "projectRoot": str(project_root),
                            "projectPath": str(project_root),
                            "expectedDigest": str(scan.get("digest") or ""),
                            "storeId": target.store_id,
                        },
                        "reason": "Repair a damaged project chat transcript store through the supervised write lane.",
                        "preview": {
                            "storeId": target.store_id,
                            "scope": "project_owned",
                            "reason": str(scan.get("reason") or "needs_repair"),
                        },
                        "agent_name": "doctor",
                        "requires_explicit_approval": True,
                        "never_auto_approve": True,
                        "explicit_approval_reason": "Project-owned chat recovery always requires explicit approval.",
                    },
                    internal_wrapper=True,
                )
            except Exception:
                failures += 1
                continue
            if request.get("ok") and request.get("status") == "pending":
                approval_queued += 1
                approval_queued_invalid_count += int(scan.get("invalidCount") or 0)
            else:
                failures += 1
            continue
        if target.store_id.startswith("session.subagent"):
            with _SUB_AGENT_COLLABORATION.maintenance_lock():
                result = repair_session_store(target, scan)
        else:
            lock = CHAT_TRANSCRIPTS_LOCK if target.store_id.startswith("session.chat") else AGENT_GATEWAY._lock
            with lock:
                result = repair_session_store(target, scan)
        changed = bool(result.get("changed")) or changed
        if result.get("status") in {"repaired", "quarantined", "already_repaired"}:
            recovered_store_count += 1
        if result.get("status") in {"repaired", "quarantined"}:
            invalid_quarantined_count += int(result.get("invalidCount") or 0)
        if result.get("status") in {"failed", "conflict"}:
            failures += 1
        elif result.get("status") in {"repaired", "quarantined", "already_repaired"}:
            # A mixed JSONL can contain both syntax damage and future or
            # semantically unfamiliar records. Repair only the damaged bytes,
            # then report the preserved remainder honestly instead of claiming
            # the store healthy merely because the write succeeded.
            post_repair = scan_session_store(target)
            if post_repair.get("status") in {"error", "unsupported", "needs_repair"}:
                unresolved_count += 1
    if failures:
        phase_status = "error"
        phase_message = "One or more session-store repairs failed; unchanged evidence was preserved."
    elif unresolved_count:
        phase_status = "warning"
        phase_message = "Unsupported or unreadable session evidence was preserved for manual recovery."
    elif approval_queued:
        phase_status = "warning"
        phase_message = "Project-owned session recovery was queued for explicit approval; no project evidence was changed yet."
    else:
        phase_status = "ok"
        phase_message = "Repairable app-owned session evidence was isolated with exact backups."
    phases.add(
        "repair_session_stores",
        phase_status,
        phase_message,
        {
            "changed": changed,
            "approvalQueuedCount": approval_queued,
            "recoveredStoreCount": recovered_store_count,
            "detectedInvalidCount": detected_invalid_count,
            "invalidQuarantinedCount": invalid_quarantined_count,
            "approvalQueuedInvalidCount": approval_queued_invalid_count,
            "unresolvedCount": unresolved_count,
            "failureCount": failures,
        },
    )
    if failures:
        return {"status": "needs_user_action", "changed": changed}
    if unresolved_count:
        return {"status": "needs_user_action", "changed": changed}
    if approval_queued:
        return {"status": "queued_for_approval", "changed": changed}
    return {"status": "repaired" if changed else "healthy", "changed": changed}


def repair_project_chat_store_sync(params: dict[str, Any]) -> dict[str, Any]:
    """Execute one approved, digest-bound project chat recovery."""

    params = params or {}
    project_value = str(
        params.get("projectPath") or params.get("project_path") or params.get("projectRoot") or params.get("project_root") or ""
    ).strip()
    project_root = resolve_chat_project_root(project_value)
    if project_root is None:
        return {"ok": False, "status": "conflict", "reason": "invalid_project_root", "changed": False}
    path = project_chat_transcripts_path(str(project_root))
    if path is None:
        return {"ok": False, "status": "conflict", "reason": "invalid_project_root", "changed": False}
    project_key = normalize_chat_project_key(str(project_root))
    suffix = hashlib.sha256(project_key.encode("utf-8", errors="replace")).hexdigest()[:16]
    target = SessionStoreTarget(
        f"session.chat.project.{suffix}",
        path,
        "project_owned",
        "json",
        required_list_field="chats",
        required_list_item_kind="chat",
        document_version_field="version",
        known_document_versions=(1,),
        guard_root=project_root,
        max_bytes=CHAT_TRANSCRIPTS_MAX_BYTES,
        max_list_items=CHAT_TRANSCRIPTS_MAX_CHATS,
    )
    if str(params.get("storeId") or "").strip() != target.store_id:
        return {"ok": False, "status": "conflict", "reason": "store_binding_changed", "changed": False}
    expected_digest = str(params.get("expectedDigest") or params.get("expected_digest") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        return {"ok": False, "status": "conflict", "reason": "invalid_snapshot", "changed": False}
    with CHAT_TRANSCRIPTS_LOCK:
        current = scan_session_store(target)
        if current.get("status") != "needs_repair" or current.get("digest") != expected_digest:
            prior_scan = {
                "schema": SESSION_STORE_INTEGRITY_SCHEMA,
                "storeId": target.store_id,
                "basename": target.path.name,
                "scope": target.scope,
                "format": target.format,
                "exists": True,
                "digest": expected_digest,
            }
            prior_result = repair_session_store(target, prior_scan, project_write_authorized=True)
            if prior_result.get("status") == "already_repaired":
                return {**prior_result, "ok": True}
            return {"ok": False, "status": "conflict", "reason": "snapshot_changed", "changed": False}
        result = repair_session_store(target, current, project_write_authorized=True)
    return {
        **result,
        "ok": result.get("status") in {"repaired", "quarantined", "already_repaired", "no_change"},
    }


def _repair_unity_bridge_doctor(context: dict[str, Any], mode: str, phases: PhaseLog) -> dict[str, Any]:
    project_path = str(context.get("selected_project_path") or "").strip()
    if project_path and vrcforge_mcp_core_installed(Path(project_path)):
        status = build_unity_status_snapshot(project_root=Path(project_path))
        if status.get("connected"):
            phases.add("core_ready", "ok", "The project-scoped VRCForge MCP Core is reachable.")
            return {"status": "healthy", "changed": False}
        phases.add(
            "open_unity",
            "warning",
            "Open the selected Unity project and wait for VRCForge MCP Core Ready, then retry.",
        )
        return {"status": "needs_user_action", "changed": False}
    request = UnityMcpRepairRequest(
        projectPath=str(context.get("selected_project_path") or ""),
        allowUnityRelaunch=mode == "force",
        waitSeconds=120 if mode == "force" else 90,
        closeTimeoutSeconds=60,
    )
    result = repair_unity_mcp_bridge_sync(request)
    for item in result.get("phases") or []:
        if isinstance(item, dict):
            phases.add(str(item.get("id") or "bridge"), str(item.get("status") or "warning"), str(item.get("message") or "Bridge repair phase completed."))
    if result.get("ok"):
        return {"status": "repaired" if result.get("status") != "healthy" else "healthy", "changed": result.get("status") != "healthy"}
    return {"status": "needs_user_action" if result.get("status") == "needs_user_action" else "failed", "changed": False}


def _detect_runtime_settings_doctor(_context: dict[str, Any]) -> dict[str, Any]:
    # Refresh the path-bound diagnostic from disk on every Doctor run. The
    # loader is read-only and falls back in memory, so external corruption is
    # visible without making Doctor itself unavailable.
    load_runtime_settings_safely(RUNTIME_SETTINGS_PATH, loader=load_settings)
    diagnostic = runtime_settings_diagnostic(RUNTIME_SETTINGS_PATH)
    return {
        "status": diagnostic.get("status", "unknown"),
        "message": diagnostic.get("message", "Runtime settings status is unavailable."),
        "detail": {"code": diagnostic.get("code"), "fallbackActive": diagnostic.get("fallbackActive")},
    }


def _detect_security_developer_options(context: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(ensure_dict(context.get("security_meta")).get("developerOptionsEnabled"))
    return {
        "status": "warning" if enabled else "ok",
        "message": "Developer Options are enabled." if enabled else "Developer Options are disabled.",
        "detail": {"enabled": enabled, "readOnly": True},
    }


def _detect_security_token_age(context: dict[str, Any]) -> dict[str, Any]:
    security_meta = ensure_dict(context.get("security_meta"))
    reference_at = str(security_meta.get("tokenRotatedAt") or security_meta.get("tokenCreatedAt") or "").strip()
    if not reference_at:
        return {"status": "unknown", "message": "Gateway token age is unknown for this legacy configuration.", "detail": {"ageKnown": False, "readOnly": True}}
    try:
        reference = datetime.fromisoformat(reference_at.replace("Z", "+00:00"))
        age_days = max(0, (datetime.now(timezone.utc) - reference.astimezone(timezone.utc)).days)
    except (ValueError, TypeError):
        return {"status": "unknown", "message": "Gateway token age metadata is invalid.", "detail": {"ageKnown": False, "readOnly": True}}
    return {
        "status": "warning" if age_days > 90 else "ok",
        "message": "Gateway token rotation is overdue." if age_days > 90 else "Gateway token age is within policy.",
        "detail": {"ageKnown": True, "ageDays": age_days, "readOnly": True},
    }


def _detect_security_trusted_signers(context: dict[str, Any]) -> dict[str, Any]:
    meta = ensure_dict(context.get("security_meta"))
    registry_readable = meta.get("skillRegistryReadable") is True
    signer_count = int(meta.get("trustedSignerCount") or 0)
    installed_count = int(meta.get("installedSkillPackageCount") or 0)
    if not registry_readable:
        return {
            "status": "error",
            "message": "Skill signer trust posture could not be established because the package registry is unreadable.",
            "detail": {"registryReadable": False, "readOnly": True},
        }
    warn = installed_count > 0 and signer_count == 0
    return {
        "status": "warning" if warn else "ok",
        "message": "Installed Skill packages have no trusted signer policy." if warn else "Skill signer trust posture is bounded.",
        "detail": {"trustedSignerCount": signer_count, "installedPackageCount": installed_count, "readOnly": True},
    }


def build_doctor_service_context() -> dict[str, Any]:
    health = build_agentic_app_health()
    selected_project = _selected_project_path_from_health(health)
    gateway_config = AGENT_GATEWAY.ensure_config()
    server_config = getattr(CURRENT_UVICORN_SERVER, "config", None)
    host = str(getattr(server_config, "host", "127.0.0.1") or "127.0.0.1")
    port = int(getattr(server_config, "port", 8757) or 8757)
    skill_registry_readable = True
    try:
        package_service = skill_package_service()
        package_registry = package_service.load_registry()
        installed_packages = package_service.list_installed()
        governance = package_registry.get("governance") if isinstance(package_registry.get("governance"), dict) else {}
        trusted = governance.get("trusted_signers") or governance.get("trustedSigners") or []
        trusted_count = len(trusted) if isinstance(trusted, (list, dict)) else 0
    except Exception:
        installed_packages = []
        trusted_count = 0
        skill_registry_readable = False
    execution_mode = str(gateway_config.execution_mode or "approval")
    broad_writes = bool(
        gateway_config.enabled
        and gateway_config.allow_write_requests
        and execution_mode in {"auto", "roslyn_full_auto"}
    )
    full_permission = bool(gateway_config.allow_write_requests and execution_mode == "roslyn_full_auto")
    packaged = bool(getattr(sys, "frozen", False))
    backend_path = Path(sys.executable) if packaged else ROOT_DIR / "backend" / "vrcforge_backend.exe"
    return {
        "health": health,
        "selected_project_path": selected_project,
        "app_config": {"path": CONFIG_PATH},
        "doctor_port": {
            "host": host,
            "port": port,
            "gatewayUrl": AGENT_GATEWAY.public_base_url,
            "currentPid": os.getpid(),
            "ownerPid": os.getpid() if BACKEND_OWNER_LEASE.owned else None,
            "ownerLeaseOwned": BACKEND_OWNER_LEASE.owned,
        },
        "desktop_install": {
            "packaged": packaged,
            "manifestPath": ROOT_DIR / "payload-integrity.json",
            "desktopVersion": os.environ.get("VRCFORGE_DESKTOP_VERSION", app.version),
            "desktopPath": ROOT_DIR / "VRCForge.exe",
            "backendPath": backend_path,
            "versionPath": ROOT_DIR / "VERSION",
            "stateDir": USER_DATA_DIR,
        },
        "security": {
            "external_writes": {
                "broadPermissions": broad_writes,
                "approvalRequired": not broad_writes,
                "checkpointRequired": True,
                "fullPermission": full_permission,
            },
            "bind_auth": {"publicBind": host not in {"127.0.0.1", "::1", "localhost"}, "tokenRequired": gateway_config.require_token, "tokenStrong": len(gateway_config.token) >= 32},
            "mcp_exposure": {"broadExposure": host not in {"127.0.0.1", "::1", "localhost"}, "writeToolsSupervised": True},
            "process_exec": {
                "unsafeExec": full_permission or (broad_writes and gateway_config.developer_options_enabled),
                "approvalRequired": not broad_writes,
                "policyBounded": not full_permission,
                "fullPermission": full_permission,
            },
        },
        "security_meta": {
            "developerOptionsEnabled": gateway_config.developer_options_enabled,
            "tokenCreatedAt": getattr(gateway_config, "token_created_at", ""),
            "tokenRotatedAt": getattr(gateway_config, "token_rotated_at", ""),
            "trustedSignerCount": trusted_count,
            "installedSkillPackageCount": len(installed_packages),
            "skillRegistryReadable": skill_registry_readable,
        },
    }


def app_doctor_service() -> DoctorService:
    global _APP_DOCTOR_SERVICE
    with _APP_DOCTOR_SERVICE_LOCK:
        if _APP_DOCTOR_SERVICE is not None:
            return _APP_DOCTOR_SERVICE
        service = DoctorService(build_doctor_service_context)
        service.register_rule(DoctorRule("app.runtime_settings", "Runtime", "Runtime settings", _detect_runtime_settings_doctor))
        service.register_rule(DoctorRule("checkpoint.backend", "Rollback", "Checkpoint backend", _detect_checkpoint_doctor, _repair_checkpoint_doctor))
        service.register_rule(DoctorRule("skills.registry", "Skills", "Skill registry", _detect_skills_doctor, _repair_skills_doctor))
        service.register_rule(DoctorRule("session.storage", "Session storage", "Session store integrity", _detect_session_storage_doctor, _repair_session_storage_doctor))
        service.register_rule(DoctorRule("unity.mcp.package", "Unity environment", "VRCForge MCP Core", _doctor_component_detect("mcpPackageConfigured")))
        service.register_rule(DoctorRule("unity.mcp.bridge", "Unity environment", "Unity MCP bridge", _doctor_component_detect("unityMcpBridgeReachable"), _repair_unity_bridge_doctor))
        service.register_rule(DoctorRule("unity.mcp.instance", "Unity environment", "Unity instance registration", _doctor_component_detect("unityMcpInstance"), _repair_unity_bridge_doctor))
        service.register_rule(DoctorRule("security.developer_options", "Security", "Developer Options posture", _detect_security_developer_options))
        service.register_rule(DoctorRule("security.gateway_token_age", "Security", "Gateway token age", _detect_security_token_age))
        service.register_rule(DoctorRule("security.trusted_signers", "Security", "Trusted Skill signers", _detect_security_trusted_signers))
        _APP_DOCTOR_SERVICE = service
        return service


DOCTOR_RULE_COPY: dict[str, tuple[str, str]] = {
    "app.config": ("Provider and vision settings must remain recoverable without silently discarding unknown or legacy values.", "Run Safe fix to create a verified backup and canonicalize a valid document."),
    "app.runtime_settings": ("The runtime settings file controls Unity bridge defaults and legacy provider fallback.", "Repair the file manually when fallback mode is active; Doctor never replaces malformed settings with an empty document."),
    "doctor.port": ("Loopback listener ownership prevents accidental public exposure and ambiguous runtime connections.", "Close the foreign process or change the configured port; Doctor never kills a process."),
    "desktop.install_integrity": ("Installed desktop and backend bytes must match the package that was built and shipped.", "Reinstall from a verified VRCForge package if version or file hashes do not match."),
    "checkpoint.backend": ("Every write depends on readable checkpoint evidence for rollback.", "Run Safe fix to recreate missing storage and quarantine malformed JSONL rows."),
    "skills.registry": ("Only eligible Skills should be exposed to the runtime and external agents.", "Run Safe fix to quarantine broken user manifests and disable signature failures without deleting evidence."),
    "session.storage": ("Chat, goal, run, and sub-agent continuity depends on durable stores that do not silently lose corrupt records.", "Run Safe fix for app-owned data; Unity-project data remains approval-gated."),
    "unity.mcp.package": ("The project-scoped MCP Core is bundled with the VRCForge Unity plugin.", "Repair the VRCForge plugin install; no separate MCP package is required."),
}


def _merge_registered_doctor_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {str(check.get("id") or ""): check for check in checks}
    for detected in app_doctor_service().detect_all():
        check_id = str(detected.get("id") or "")
        status = str(detected.get("status") or "unknown")
        if status == "skipped":
            status = "ok"
        why, how = DOCTOR_RULE_COPY.get(
            check_id,
            ("This posture contributes to VRCForge runtime safety and recoverability.", "Review the diagnostic detail and keep this check within its supervised policy."),
        )
        fixable = bool(detected.get("fixable"))
        actions = (
            ["repair_unity_bridge", "fix", "retry", "open_logs", "copy_diagnostic_summary"]
            if check_id in {"unity.mcp.bridge", "unity.mcp.instance"}
            else ["fix", "retry", "open_logs", "copy_diagnostic_summary"]
            if fixable
            else ["retry", "open_logs", "copy_diagnostic_summary"]
        )
        merged[check_id] = _doctor_check(
            check_id,
            str(detected.get("title") or check_id),
            status,
            str(detected.get("message") or "Check completed."),
            why,
            how,
            detected.get("detail"),
            actions=actions,
            fixable=fixable,
            fix_modes=["safe", "force"] if check_id in {"app.config", "unity.mcp.bridge", "unity.mcp.instance"} else (["safe"] if fixable else []),
        )
    ordered_ids = [str(check.get("id") or "") for check in checks]
    extras = [check_id for check_id in merged if check_id not in ordered_ids]
    return [merged[check_id] for check_id in ordered_ids if check_id in merged] + [merged[check_id] for check_id in extras]


def build_app_doctor_report() -> dict[str, Any]:
    return _DOCTOR_READINESS_REPORT.build_app_doctor_report()


def know_yourself_sync(params: dict[str, Any] | None = None) -> dict[str, Any]:
    return _KNOW_YOURSELF_READINESS.know_yourself_sync(params)


@app.get("/api/app/doctor")
def read_agentic_app_doctor() -> dict[str, Any]:
    try:
        return build_app_doctor_report()
    except Exception as exc:  # noqa: BLE001 - doctor must not break first-run desktop startup.
        checks = [
            _doctor_check(
                "desktop.runtime",
                "Desktop runtime connection",
                "ok",
                "Desktop can reach the local VRCForge runtime.",
                "The desktop UI needs the loopback runtime for chat, tools, approvals, checkpoints, and diagnostics.",
                "Restart VRCForge or use Retry if this check ever disappears.",
                {"endpoint": "http://127.0.0.1:8757"},
            ),
            _doctor_check(
                "doctor.degraded",
                "Doctor report",
                "warning",
                f"Doctor diagnostics failed: {exc}",
                "Doctor should explain optional subsystem failures without blocking normal chat.",
                "Open logs, copy the diagnostic summary, and continue in manual/read-only mode.",
                {"error": str(exc)},
            ),
        ]
        return {
            "ok": False,
            "schema": "vrcforge.doctor.v1",
            "scope": "vrcforge.environment.v1",
            "projectContentInspected": False,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "version": app.version,
            "summary": _doctor_summary(checks),
            "sections": _doctor_sections(checks),
            "selectedUnityEnvironment": {
                "configured": bool(DASHBOARD_STATE.selected_project_path),
                "label": _redact_local_path(DASHBOARD_STATE.selected_project_path),
            },
            "checks": checks,
        }


@app.post("/api/app/doctor/unity-mcp/repair")
async def repair_agentic_app_unity_mcp(request: UnityMcpRepairRequest) -> dict[str, Any]:
    await emit_log_async(
        "info",
        "doctor",
        "Unity MCP bridge repair requested.",
        {
            "projectPath": request.project_path or DASHBOARD_STATE.selected_project_path,
            "allowUnityRelaunch": request.allow_unity_relaunch,
        },
    )
    result = await asyncio.to_thread(repair_unity_mcp_bridge_sync, request)
    await emit_log_async(
        "success" if result.get("ok") else "warn",
        "doctor",
        "Unity MCP bridge repair finished.",
        {
            "status": result.get("status"),
            "ok": result.get("ok"),
            "phaseCount": len(result.get("phases") or []),
        },
    )
    return result


@app.post("/api/app/doctor/fix/{check_id}")
async def fix_agentic_app_doctor_check(check_id: str, request: DoctorFixRequest) -> dict[str, Any]:
    await emit_log_async(
        "info",
        "doctor",
        "Doctor repair requested.",
        {"checkId": check_id, "mode": request.mode},
    )
    try:
        def run_fix() -> dict[str, Any]:
            project_override = request.project_path.strip()
            fix_args: tuple[Any, ...] = (
                (check_id, request.mode, {"selected_project_path": project_override})
                if project_override and check_id in {"session.storage", "unity.mcp.package", "unity.mcp.bridge", "unity.mcp.instance"}
                else (check_id, request.mode)
            )
            if check_id == "app.config":
                with CONFIG_DOCUMENT_LOCK:
                    result = app_doctor_service().fix(*fix_args)
                    if result.get("status") == "repaired":
                        PROVIDER_CONFIGURATION.reload_from_disk()
                    return result
            return app_doctor_service().fix(*fix_args)

        result = await asyncio.to_thread(run_fix)
    except DoctorServiceError as exc:
        await emit_log_async(
            "warn" if exc.status_code < 500 else "error",
            "doctor",
            "Doctor repair was not applied.",
            {"checkId": check_id, "mode": request.mode, "statusCode": exc.status_code},
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    result_status = str(result.get("status") or "failed")
    audit_level = (
        "success"
        if result_status in {"healthy", "repaired"}
        else "error"
        if result_status in {"failed", "error"}
        else "warn"
    )
    await emit_log_async(
        audit_level,
        "doctor",
        "Doctor repair finished." if audit_level == "success" else "Doctor repair requires follow-up.",
        {
            "checkId": check_id,
            "mode": request.mode,
            "status": result.get("status"),
            "changed": bool(result.get("changed")),
        },
    )
    return result


@app.get("/api/app/diagnostics")
def read_app_diagnostics() -> dict[str, Any]:
    return diagnostics_state()


@app.post("/api/app/diagnostics")
async def update_app_diagnostics(request: DiagnosticsConfigRequest) -> dict[str, Any]:
    try:
        payload = await asyncio.to_thread(update_app_diagnostics_guarded, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unsupported diagnostic log level.") from exc
    await emit_log_async(
        "success",
        "diagnostics",
        "Diagnostics settings updated.",
        {"debugLogging": payload["debugLogging"], "logLevel": payload["logLevel"]},
    )
    return payload


def update_app_diagnostics_guarded(request: DiagnosticsConfigRequest) -> dict[str, Any]:
    with ADVANCED_SETTINGS_TRANSITION_LOCK:
        if request.log_level == "trace" and not developer_options_enabled_for_diagnostics():
            raise HTTPException(status_code=409, detail="Trace logging requires Developer Options.")
        DIAGNOSTIC_LOGGER.update_config(
            log_level=request.log_level,
            debug_logging=request.debug_logging,
        )
        return diagnostics_state()


@app.post("/api/app/support-bundle")
def create_app_support_bundle(request: SupportBundleRequest) -> dict[str, Any]:
    return build_support_bundle(request)


@app.get("/api/app/tools/registry")
def read_app_tool_registry() -> dict[str, Any]:
    return AGENT_GATEWAY.build_tool_registry()


@app.get("/api/agent/manifest")
def read_agent_manifest(request: Request, exposure_layer: Literal["planning", "execution"] = "planning") -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.build_manifest(exposure_layer)


@app.get("/api/agent/tools/registry")
def read_agent_tool_registry(request: Request, exposure_layer: Literal["planning", "execution"] = "planning") -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.build_tool_registry(exposure_layer=exposure_layer)


@app.get("/api/agent/health")
def read_agent_health(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.build_health()


@app.get("/api/agent/skills")
def read_agent_skills(request: Request, exposure_layer: Literal["planning", "execution"] = "planning") -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.build_skill_registry(exposure_layer=exposure_layer)


@app.get("/api/agent/skills/check")
def read_agent_skills_check(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.check_skill_registry()


@app.post("/api/agent/session")
def create_agent_session(request: Request, session_request: AgentSessionRequest) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return {
        "ok": True,
        "agentName": session_request.agent_name,
        "manifest": AGENT_GATEWAY.build_manifest("planning"),
    }


@app.post("/api/agent/runtime/message")
def agent_runtime_message(request: Request, runtime_request: AgentRuntimeMessageRequest) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=False)
    try:
        return AGENT_GATEWAY.runtime_message(
            {
                "session_id": runtime_request.session_id,
                "message": runtime_request.message,
                "shell_command": runtime_request.shell_command,
                "skill_tool": runtime_request.skill_tool,
                "skill_params": runtime_request.skill_params,
                "cwd": runtime_request.cwd,
                "workspace_root": runtime_request.workspace_root,
                "projectPath": runtime_request.project_path,
                "projectRoot": runtime_request.project_root,
                "provider": runtime_request.provider,
                "providerLabel": runtime_request.provider_label,
                "model": runtime_request.model,
            },
            agent_name=runtime_request.agent_name,
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/agent/runtime/session/{session_id}")
def agent_runtime_session(session_id: str, request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=False)
    try:
        return AGENT_GATEWAY.get_runtime_session(session_id)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/agent/tool/{tool_name}")
async def call_agent_tool(tool_name: str, request: Request, tool_request: AgentToolRequest) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=False)
    try:
        if tool_name == "vrcforge_agent_desktop_action":
            payload = await asyncio.to_thread(
                AGENT_GATEWAY.call_tool,
                tool_name,
                tool_request.params,
                tool_request.agent_name,
            )
        else:
            payload = AGENT_GATEWAY.call_tool(tool_name, tool_request.params, agent_name=tool_request.agent_name)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    params = ensure_dict(tool_request.params or {})
    session_id = str(params.get("sessionId") or params.get("session_id") or "")
    project_root = str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or "")
    if tool_name.startswith("vrcforge_progress_"):
        await EVENT_BUS.broadcast("agentProgress", AGENT_GATEWAY.list_agent_progress(limit=30, session_id=session_id, project_root=project_root))
    elif tool_name == "vrcforge_ask_user":
        await EVENT_BUS.broadcast("agentQuestions", AGENT_QUESTIONS.list(limit=30, session_id=session_id, project_root=project_root))
    elif tool_name == "vrcforge_agent_desktop_action":
        await EVENT_BUS.broadcast("agentDesktopActions", AGENT_GATEWAY.list_desktop_actions(limit=30, session_id=session_id, project_root=project_root))
    elif tool_name == "vrcforge_apply_approved":
        await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
        if isinstance(payload, dict) and payload.get("goalDelivery") is not None:
            await broadcast_background_goal_state({})
    return payload


@app.get("/api/agent/approvals")
def read_agent_approvals(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=False)
    approvals = AGENT_GATEWAY.list_approvals()
    return {"ok": True, "approvals": approvals, "count": len(approvals)}


@app.post("/api/agent/approvals/{approval_id}/approve")
async def approve_agent_approval(approval_id: str, request: Request) -> dict[str, Any]:
    authenticate_agent_approval_request(request)
    try:
        payload = AGENT_GATEWAY.approve(approval_id)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/agent/approvals/{approval_id}/reject")
async def reject_agent_approval(approval_id: str, request: Request) -> dict[str, Any]:
    authenticate_agent_approval_request(request)
    try:
        payload = AGENT_GATEWAY.reject(approval_id)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    if payload.get("goalDelivery") is not None:
        await broadcast_background_goal_state({})
    return payload


@app.get("/api/agent/logs")
def read_agent_logs(request: Request, limit: int = 100) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=False)
    logs = AGENT_GATEWAY.recent_audit_logs(limit=limit)
    return {"ok": True, "logs": logs, "count": len(logs)}


@app.websocket("/ws")
async def dashboard_socket(websocket: WebSocket) -> None:
    client_host = websocket.client.host if websocket.client else ""
    origin = websocket.headers.get("origin", "").strip()
    supplied = extract_websocket_auth_token(websocket.headers)
    try:
        validate_app_request_auth(client_host=client_host, origin=origin, supplied_token=supplied)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail))
        return

    await EVENT_BUS.connect(websocket)
    try:
        await EVENT_BUS.send_to_client(websocket, "hello", await asyncio.to_thread(build_dashboard_socket_payload))
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        await EVENT_BUS.disconnect(websocket)
    except Exception as exc:  # noqa: BLE001
        await EVENT_BUS.disconnect(websocket)
        emit_log("warn", "socket", "WebSocket client closed unexpectedly.", {"error": str(exc)})


@app.get("/api/projects")
def read_projects() -> dict[str, Any]:
    return project_snapshot_payload(use_cache=True, refresh_async=False)


@app.post("/api/projects/refresh")
async def refresh_projects() -> dict[str, Any]:
    payload = await asyncio.to_thread(refresh_project_snapshot_cache_sync)
    await EVENT_BUS.broadcast("projects", payload)
    await emit_log_async("info", "project", "Project list refreshed.", {"count": len(payload["projects"])})
    return payload


@app.post("/api/state")
async def update_state(request: DashboardStateRequest) -> dict[str, Any]:
    live_connection = globals().get("PRIMITIVE_BASIS_LIVE_CONNECTION")
    if (
        live_connection is not None
        and hasattr(live_connection, "state_update_allowed")
        and not live_connection.state_update_allowed(request)
    ):
        raise HTTPException(
            status_code=409,
            detail="The fixed live run has frozen the Unity project and transport.",
        )
    if request.project_path is not None:
        try:
            selected_project_path = await asyncio.to_thread(
                persist_selected_project_path,
                request.project_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Unable to persist the selected Unity project: {exc}") from exc
        DASHBOARD_STATE.selected_project_path = selected_project_path
        if request.unity_instance is None or not request.unity_instance.strip():
            DASHBOARD_STATE.unity_instance = (
                Path(DASHBOARD_STATE.selected_project_path).name
                if DASHBOARD_STATE.selected_project_path
                else ""
            )

    DASHBOARD_STATE.settings_path = resolve_local_path(request.settings_path)

    if request.unity_host is not None:
        DASHBOARD_STATE.unity_host = request.unity_host.strip() or DASHBOARD_STATE.unity_host
    if request.unity_port is not None:
        DASHBOARD_STATE.unity_port = int(request.unity_port)
    if request.unity_instance is not None:
        DASHBOARD_STATE.unity_instance = request.unity_instance.strip()

    payload = serialize_dashboard_state()
    await EVENT_BUS.broadcast("state", payload)
    await emit_log_async(
        "info",
        "dashboard",
        "Dashboard state updated.",
        {
            "projectPath": DASHBOARD_STATE.selected_project_path,
            "unityInstance": DASHBOARD_STATE.unity_instance,
        },
    )
    return payload


@app.get("/api/config")
def read_api_config() -> dict[str, Any]:
    return {
        "configPath": str(CONFIG_PATH),
        "apiConfig": PROVIDER_CONFIGURATION.serialize_api_config(include_secret=True),
        "visionConfig": PROVIDER_CONFIGURATION.serialize_vision_config(include_secret=True),
        "effective": PROVIDER_CONFIGURATION.build_effective_model_summary(),
    }


@app.post("/api/config")
async def update_api_config(request: ApiConfigRequest) -> dict[str, Any]:
    try:
        config = PROVIDER_CONFIGURATION.resolve_api_request(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    PROVIDER_CONFIGURATION.save_api_config(config)
    payload = {
        "configPath": str(CONFIG_PATH),
        "apiConfig": PROVIDER_CONFIGURATION.serialize_api_config(include_secret=True),
        "visionConfig": PROVIDER_CONFIGURATION.serialize_vision_config(include_secret=True),
        "effective": PROVIDER_CONFIGURATION.build_effective_model_summary(),
    }
    await EVENT_BUS.broadcast("config", payload)
    await emit_log_async(
        "success",
        "config",
        "Dashboard API config saved and applied.",
        {
            "provider": config.provider,
            "model": config.model,
            "baseUrl": config.base_url or "(official endpoint)",
        },
    )
    return payload


@app.post("/api/config/vision")
async def update_vision_config(request: VisionConfigRequest) -> dict[str, Any]:
    try:
        config = PROVIDER_CONFIGURATION.resolve_vision_request(request)
    except ProviderCredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    PROVIDER_CONFIGURATION.save_vision_config(config)
    payload = {
        "configPath": str(CONFIG_PATH),
        "apiConfig": PROVIDER_CONFIGURATION.serialize_api_config(include_secret=True),
        "visionConfig": PROVIDER_CONFIGURATION.serialize_vision_config(include_secret=True),
        "effective": PROVIDER_CONFIGURATION.build_effective_model_summary(),
    }
    await EVENT_BUS.broadcast("config", payload)
    await emit_log_async(
        "success",
        "config",
        "Vision profile saved." if config.provider else "Vision profile cleared.",
        {
            "provider": config.provider or "(none)",
            "model": config.model or "(none)",
            "enabled": config.enabled,
        },
    )
    return payload


@app.post("/api/models")
async def read_api_models(request: ApiModelListRequest) -> dict[str, Any]:
    try:
        config = PROVIDER_CONFIGURATION.resolve_api_request(request)
    except (ProviderCredentialError, ProviderApiTypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider_label = provider_display_name(config.provider)

    try:
        models = await asyncio.to_thread(PROVIDER_MODEL_CATALOG.fetch_provider_models, config)
    except Exception as exc:  # noqa: BLE001
        await emit_log_async(
            "error",
            "config",
            "Provider model list request failed.",
            {
                "provider": config.provider,
                "baseUrl": config.base_url or "(official endpoint)",
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = {
        "provider": config.provider,
        "providerLabel": provider_label,
        "baseUrl": config.base_url,
        "models": [PROVIDER_MODEL_CATALOG.enrich_provider_model_item(config, item) for item in models],
        "modelCount": len(models),
        "selectedModel": config.model,
        **PROVIDER_MODEL_CATALOG.provider_config_descriptor(config),
    }
    await emit_log_async(
        "success",
        "config",
        "Provider model list loaded.",
        {"provider": config.provider, "modelCount": len(models)},
    )
    return payload


@app.post("/api/app/provider/test")
async def test_api_provider(request: ProviderTestRequest) -> dict[str, Any]:
    return await asyncio.to_thread(PROVIDER_TESTS.run, request)


@app.post("/api/app/provider/reasoning-variants")
def read_reasoning_variants(request: ReasoningVariantsRequest) -> dict[str, Any]:
    """Return the backend-owned provider/model reasoning capability descriptor."""

    return reasoning_variants_descriptor(request.provider, request.model, request.api_type)


@app.post("/api/app/provider/mcp-selection")
async def request_mcp_selection_acceptance(request: McpSelectionAcceptanceRequest) -> dict[str, Any]:
    """Issue one process-owned, selection-only receipt through the authenticated App API."""

    visible_tools = tools_for_exposure_layer(request.visible_tools, request.exposure_layer)
    if not visible_tools:
        raise HTTPException(status_code=422, detail="No tools are available in the requested exposure layer.")
    return await asyncio.to_thread(
        mcp_trigger_selection_planner,
        request.message,
        visible_tools,
        request.exposure_layer,
    )


@app.post("/api/app/provider/mcp-selection/verify")
def verify_mcp_selection_acceptance(request: McpSelectionAcceptanceVerifyRequest) -> dict[str, Any]:
    """Consume a valid receipt; the process-owned authority lives only for this backend lifetime."""

    visible_tools = tools_for_exposure_layer(request.visible_tools, request.exposure_layer)
    accepted = verify_mcp_trigger_selection_receipt(
        request.message,
        visible_tools,
        request.result,
        request.exposure_layer,
    )
    return {
        "ok": accepted,
        "schema": "vrcforge.mcp_selection_verification.v1",
        "accepted": accepted,
    }


def _resolve_install_source_assets() -> Path:
    candidates = [
        ROOT_DIR / "Assets" / "VRCForge",
        ROOT_DIR / "unity_plugin" / "Assets" / "VRCForge",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    raise RuntimeError("Source Assets/VRCForge folder was not found in the source tree or packaged payload.")


def _new_install_backup_path(backup_root: Path, prefix: str) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = backup_root / f"{prefix}_{timestamp}"
    suffix = 1
    while candidate.exists() or candidate.with_suffix(candidate.suffix + ".meta").exists():
        candidate = backup_root / f"{prefix}_{timestamp}_{suffix}"
        suffix += 1
    return candidate


def _remove_path_with_meta(path: Path) -> None:
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    meta = Path(str(path) + ".meta")
    if meta.exists():
        if meta.is_dir():
            shutil.rmtree(meta)
        else:
            meta.unlink()


def _move_path_with_meta(source: Path, destination: Path) -> None:
    shutil.move(str(source), str(destination))
    meta = Path(str(source) + ".meta")
    if meta.exists():
        shutil.move(str(meta), str(Path(str(destination) + ".meta")))


def _copy_tree_clean_with_meta(source: Path, destination: Path) -> None:
    _remove_path_with_meta(destination)
    shutil.copytree(source, destination)
    source_meta = Path(str(source) + ".meta")
    destination_meta = Path(str(destination) + ".meta")
    if source_meta.exists() and not destination_meta.exists():
        if source_meta.is_dir():
            shutil.copytree(source_meta, destination_meta)
        else:
            shutil.copy2(source_meta, destination_meta)


def _restore_install_backup(backup_path: Path | None, target_path: Path) -> None:
    if backup_path is None or not backup_path.exists():
        return
    _remove_path_with_meta(target_path)
    _move_path_with_meta(backup_path, target_path)


def install_vrcforge_into_unity_project(project_root: Path) -> dict[str, Any]:
    resolved_project = project_root.expanduser().resolve()
    target_assets_root = resolved_project / "Assets"
    target_packages_root = resolved_project / "Packages"
    target_manifest = target_packages_root / "manifest.json"
    target_project_version = resolved_project / "ProjectSettings" / "ProjectVersion.txt"
    target_vrcforge = target_assets_root / "VRCForge"
    legacy_target = target_assets_root / "VRCAutoRig"
    state_root = resolved_project / ".vrcforge"
    backup_root = state_root / "backups"
    source_assets = _resolve_install_source_assets()

    for required, label in (
        (target_assets_root, "Assets"),
        (target_manifest, "Packages/manifest.json"),
        (target_project_version, "ProjectSettings/ProjectVersion.txt"),
    ):
        if not required.exists():
            raise RuntimeError(f"Target Unity project is missing {label}: {required}")

    try:
        manifest = json.loads(target_manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Target Packages/manifest.json is invalid: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("manifest root is not an object")

    backups: dict[str, str] = {}
    installed_vrcforge = False
    legacy_backup: Path | None = None
    vrcforge_backup: Path | None = None
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        if legacy_target.exists():
            legacy_backup = _new_install_backup_path(backup_root, "VRCAutoRig")
            _move_path_with_meta(legacy_target, legacy_backup)
            backups["legacy"] = str(legacy_backup)

        if target_vrcforge.exists():
            vrcforge_backup = _new_install_backup_path(backup_root, "VRCForge")
            _move_path_with_meta(target_vrcforge, vrcforge_backup)
            backups["vrcforge"] = str(vrcforge_backup)

        try:
            _copy_tree_clean_with_meta(source_assets, target_vrcforge)
            installed_vrcforge = True
        except Exception:
            _restore_install_backup(vrcforge_backup, target_vrcforge)
            raise

    except Exception:
        if legacy_backup is not None:
            _restore_install_backup(legacy_backup, legacy_target)
        if vrcforge_backup is not None:
            _restore_install_backup(vrcforge_backup, target_vrcforge)
        elif installed_vrcforge:
            _remove_path_with_meta(target_vrcforge)
        raise

    summary_parts = [
        f"Installed Assets/VRCForge into: {resolved_project}",
        f"Project backups are under: {backup_root}",
        "VRCForge MCP Core is bundled; no separate Unity MCP package is required.",
    ]
    return {
        "summary": "\n".join(summary_parts),
        "projectPath": str(resolved_project),
        "sourceAssets": str(source_assets),
        "sourceMcpPackage": "",
        "backupRoot": str(backup_root),
        "backups": backups,
        "installedMcp": False,
        "configuredMcp": True,
        "mcpCoreBundled": True,
    }


@app.post("/api/projects/install")
async def install_project(request: ProjectInstallRequest) -> dict[str, Any]:
    live_connection = globals().get("PRIMITIVE_BASIS_LIVE_CONNECTION")
    if live_connection is not None and live_connection.is_frozen():
        raise HTTPException(status_code=409, detail="Project installation is blocked during the fixed live run.")
    project_path = resolve_target_project(request.project_path)
    await emit_log_async("info", "project", "Installing VRCForge into Unity project.", {"projectPath": project_path})
    try:
        install_result = await asyncio.to_thread(install_vrcforge_into_unity_project, Path(project_path))
        if request.launch_unity and DASHBOARD_STATE.unity_editor_path:
            launch_unity_subprocess([DASHBOARD_STATE.unity_editor_path, "-projectPath", project_path], Path(DASHBOARD_STATE.unity_editor_path), Path(project_path))
            install_result["launchedUnity"] = True
    except Exception as exc:  # noqa: BLE001
        await emit_log_async("error", "project", "Project installation failed.", {"projectPath": project_path, "error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    payload = {
        "ok": True,
        "projectPath": project_path,
        "output": install_result["summary"],
        "details": install_result,
    }
    await EVENT_BUS.broadcast("projects", project_snapshot_payload(use_cache=True, refresh_async=False))
    await emit_log_async("success", "project", "VRCForge installed into Unity project.", {"projectPath": project_path})
    return payload


@app.post("/api/projects/open")
async def open_project(request: ProjectActionRequest) -> dict[str, Any]:
    live_connection = globals().get("PRIMITIVE_BASIS_LIVE_CONNECTION")
    if live_connection is not None and live_connection.is_frozen():
        raise HTTPException(status_code=409, detail="Opening another project is blocked during the fixed live run.")
    project_path = resolve_target_project(request.project_path)
    editor_path = DASHBOARD_STATE.unity_editor_path
    if not editor_path or not Path(editor_path).exists():
        raise HTTPException(
            status_code=400,
            detail="Unity editor path is empty or does not exist. Update dashboard settings before opening a project.",
        )

    try:
        DASHBOARD_STATE.selected_project_path = await asyncio.to_thread(
            persist_selected_project_path,
            project_path,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to persist the selected Unity project: {exc}") from exc
    DASHBOARD_STATE.unity_instance = Path(project_path).name
    launch_unity_subprocess([editor_path, "-projectPath", project_path], Path(editor_path), Path(project_path))
    payload = serialize_dashboard_state()
    await EVENT_BUS.broadcast("state", payload)
    await emit_log_async("info", "project", "Opened Unity project.", {"projectPath": project_path, "unityEditorPath": editor_path})
    return {"ok": True, "projectPath": project_path, "unityEditorPath": editor_path}


@app.post("/api/unity/status")
async def read_unity_status(request: ConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(build_unity_status_snapshot, load_dashboard_settings(request))


@app.post("/api/unity/instances")
async def read_unity_instances(request: ConnectionRequest) -> dict[str, Any]:
    status = await asyncio.to_thread(build_unity_status_snapshot, load_dashboard_settings(request))
    return {
        "ok": bool(status.get("connected")),
        "protocolVersion": "2026-07-28",
        "transport": "vrcforge-mcp-core",
        "instances": status.get("instances") or [],
        "activeInstance": status.get("activeInstance"),
        "error": str(status.get("error") or ""),
    }


@app.post("/api/unity/tools")
async def read_unity_tools(request: ConnectionRequest) -> dict[str, Any]:
    status = await asyncio.to_thread(build_unity_status_snapshot, load_dashboard_settings(request))
    tools = status.get("tools") if isinstance(status.get("tools"), dict) else {}
    return {
        **tools,
        "protocolVersion": "2026-07-28",
        "transport": "vrcforge-mcp-core",
        "projectPath": status.get("projectPath") or "",
    }


@app.post("/api/scene/avatars")
async def read_scene_avatars(request: AvatarSceneScanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(AVATAR_TUNING_WORKFLOWS.scan_scene_avatars, request)


@app.post("/api/avatars")
async def read_avatars(request: DashboardRequest) -> dict[str, Any]:
    return await asyncio.to_thread(AVATAR_TUNING_WORKFLOWS.read_avatars, request)


@app.post("/api/avatar/blendshapes")
async def read_avatar_blendshapes(request: AvatarBlendshapeListRequest) -> dict[str, Any]:
    return await asyncio.to_thread(AVATAR_TUNING_WORKFLOWS.read_avatar_blendshapes, request)


@app.post("/api/pipeline/plan")
async def build_pipeline_plan(request: DashboardRequest) -> dict[str, Any]:
    return await asyncio.to_thread(AVATAR_TUNING_WORKFLOWS.plan_face_tuning, request)


@app.post("/api/pipeline/run")
async def run_pipeline(request: DashboardRequest) -> dict[str, Any]:
    return await asyncio.to_thread(AVATAR_TUNING_WORKFLOWS.request_face_tuning, request)


@app.post("/api/blendshapes/apply")
async def apply_manual_blendshapes(request: ManualBlendshapeApplyRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        AVATAR_TUNING_WORKFLOWS.request_manual_blendshape_apply,
        request,
    )


@app.post("/api/blendshapes/undo")
async def undo_manual_blendshapes(request: UndoBlendshapeRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        AVATAR_TUNING_WORKFLOWS.request_manual_blendshape_undo,
        request,
    )


@app.get("/api/tuning/history")
def read_tuning_history(avatar_path: str | None = None) -> dict[str, Any]:
    return AVATAR_TUNING_WORKFLOWS.list_tuning_history(avatar_path)


@app.post("/api/tuning/history/{history_id}/reapply")
async def reapply_tuning_history(history_id: str, request: DashboardRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        AVATAR_TUNING_WORKFLOWS.request_reapply_tuning_history,
        history_id,
        request,
    )


@app.get("/api/tuning/presets")
def read_tuning_presets(avatar_path: str | None = None) -> dict[str, Any]:
    return AVATAR_TUNING_WORKFLOWS.list_tuning_presets(avatar_path)


async def _run_avatar_tuning_local_store_write(
    handler: Callable[..., dict[str, Any]],
    *args: Any,
    map_all_runtime_errors: bool = False,
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(handler, *args)
    except AvatarTuningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        if map_all_runtime_errors:
            raise to_http_exception(exc) from exc
        raise


@app.post("/api/tuning/presets")
async def create_tuning_preset(request: TuningPresetCreateRequest) -> dict[str, Any]:
    return await _run_avatar_tuning_local_store_write(
        AVATAR_TUNING_WORKFLOWS.create_tuning_preset,
        request,
        map_all_runtime_errors=True,
    )


@app.post("/api/tuning/presets/{preset_id}/apply")
async def apply_tuning_preset(preset_id: str, request: DashboardRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        AVATAR_TUNING_WORKFLOWS.request_apply_tuning_preset,
        preset_id,
        request,
    )


@app.post("/api/tuning/presets/{preset_id}/rename")
async def rename_tuning_preset(preset_id: str, request: TuningPresetRenameRequest) -> dict[str, Any]:
    return await _run_avatar_tuning_local_store_write(
        AVATAR_TUNING_WORKFLOWS.rename_tuning_preset,
        preset_id,
        request,
        map_all_runtime_errors=True,
    )


@app.post("/api/tuning/presets/{preset_id}/duplicate")
async def duplicate_tuning_preset(preset_id: str, request: TuningPresetDuplicateRequest) -> dict[str, Any]:
    return await _run_avatar_tuning_local_store_write(
        AVATAR_TUNING_WORKFLOWS.duplicate_tuning_preset,
        preset_id,
        request,
        map_all_runtime_errors=True,
    )


@app.post("/api/tuning/presets/{preset_id}/delete")
async def delete_tuning_preset(preset_id: str) -> dict[str, Any]:
    return await _run_avatar_tuning_local_store_write(
        AVATAR_TUNING_WORKFLOWS.delete_tuning_preset,
        preset_id,
    )


@app.get("/api/tuning/locks")
def read_tuning_locks(avatar_path: str | None = None) -> dict[str, Any]:
    return AVATAR_TUNING_WORKFLOWS.read_tuning_locks(avatar_path)


@app.post("/api/tuning/locks")
async def update_tuning_locks(request: TuningLocksUpdateRequest) -> dict[str, Any]:
    return await _run_avatar_tuning_local_store_write(
        AVATAR_TUNING_WORKFLOWS.update_tuning_locks,
        request,
    )


@app.post("/api/tuning/locks/ai-select")
async def ai_select_tuning_locks(request: TuningLocksAiSelectRequest) -> dict[str, Any]:
    return await asyncio.to_thread(AVATAR_TUNING_WORKFLOWS.ai_select_tuning_locks, request)


@app.post("/api/clothes/scan")
async def scan_clothes(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(WARDROBE_OUTFIT_WORKFLOWS.scan_clothes, request)
    except RuntimeError as exc:
        raise to_http_exception(exc) from exc


@app.post("/api/clothes/toggle")
async def toggle_clothing(request: ClothingToggleRequest) -> dict[str, Any]:
    return await asyncio.to_thread(WARDROBE_OUTFIT_WORKFLOWS.request_toggle_clothing, request)


@app.post("/api/clothes/generate-fx")
async def generate_clothing_fx(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(WARDROBE_OUTFIT_WORKFLOWS.generate_clothing_fx, request)
    except RuntimeError as exc:
        raise to_http_exception(exc) from exc


@app.post("/api/clothes/apply-fx")
async def apply_clothing_fx(request: ClothingApplyFxRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            WARDROBE_OUTFIT_WORKFLOWS.request_apply_clothing_fx,
            request,
        )
    except RuntimeError as exc:
        raise to_http_exception(exc) from exc


@app.post("/api/parameters/scan")
async def scan_avatar_parameters(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(scan_avatar_parameters_sync, request)


@app.post("/api/parameters/optimize")
async def optimize_avatar_parameters(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(optimize_avatar_parameters_sync, request)


@app.post("/api/parameters/apply-optimization")
async def apply_parameter_optimization(request: ParameterApplyOptimizationRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        request_supervised_unity_write,
        "vrcforge_apply_parameter_optimization",
        request,
        reason="Apply the selected parameter optimization to the Unity avatar.",
        preview_callback=lambda: apply_parameter_optimization_sync(request),
    )


@app.post("/api/parameters/rollback")
async def rollback_parameter_optimization(request: ParameterRollbackRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        request_supervised_unity_write,
        "vrcforge_rollback_parameters",
        request,
        reason="Restore the selected Unity avatar parameter snapshot.",
    )


@app.post("/api/shader/materials/scan")
async def scan_shader_materials(request: ShaderMaterialScanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.scan_shader_materials, request)


@app.post("/api/shader/plan")
async def generate_shader_material_plan(request: ShaderMaterialPlanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.generate_shader_material_plan, request)


@app.post("/api/shader/apply")
async def apply_shader_material_plan(request: ShaderMaterialApplyRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.request_shader_material_apply,
        request,
    )


@app.post("/api/shader/restore")
async def restore_shader_material_plan(request: ShaderMaterialRestoreRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.request_shader_material_restore,
        request,
    )


@app.get("/api/shader/history")
def read_shader_tuning_history(avatar_path: str | None = None) -> dict[str, Any]:
    return SHADER_VISION_PROTECTION.read_shader_tuning_history(avatar_path)


@app.post("/api/shader/history/{history_id}/reapply")
async def reapply_shader_tuning_history(history_id: str, request: ShaderMaterialPlanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.request_shader_history_reapply,
        history_id,
        request,
    )


@app.get("/api/shader/presets")
def read_shader_tuning_presets(avatar_path: str | None = None) -> dict[str, Any]:
    return SHADER_VISION_PROTECTION.read_shader_tuning_presets(avatar_path)


@app.post("/api/shader/presets")
async def create_shader_tuning_preset(request: ShaderTuningPresetCreateRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.create_shader_tuning_preset, request)


@app.post("/api/shader/presets/{preset_id}/apply")
async def apply_shader_tuning_preset(preset_id: str, request: ShaderMaterialPlanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.request_shader_preset_apply,
        preset_id,
        request,
    )


@app.post("/api/shader/presets/{preset_id}/rename")
async def rename_shader_tuning_preset(preset_id: str, request: ShaderTuningPresetRenameRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.rename_shader_tuning_preset,
        preset_id,
        request,
    )


@app.post("/api/shader/presets/{preset_id}/duplicate")
async def duplicate_shader_tuning_preset(preset_id: str, request: ShaderTuningPresetDuplicateRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.duplicate_shader_tuning_preset,
        preset_id,
        request,
    )


@app.post("/api/shader/presets/{preset_id}/delete")
async def delete_shader_tuning_preset(preset_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.delete_shader_tuning_preset,
        preset_id,
    )


@app.get("/api/shader/locks")
def read_shader_tuning_locks(avatar_path: str | None = None) -> dict[str, Any]:
    return SHADER_VISION_PROTECTION.read_shader_tuning_locks(avatar_path)


@app.post("/api/shader/locks")
async def update_shader_tuning_locks(request: ShaderTuningLocksUpdateRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.update_shader_tuning_locks, request)


@app.post("/api/shader/vision-review")
async def review_shader_material_vision(request: ShaderVisionReviewRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.review_shader_material_vision, request)


@app.post("/api/avatar-encryption/research-report")
async def avatar_encryption_research_report(request: AvatarEncryptionResearchRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.build_protection_research_report,
        request,
    )


@app.post("/api/avatar-encryption/scan")
async def avatar_encryption_scan(request: AvatarEncryptionScanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.scan_protection_candidates, request)


@app.post("/api/avatar-encryption/plan")
async def avatar_encryption_plan(request: AvatarEncryptionPlanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.plan_protection, request)


@app.post("/api/avatar-encryption/preview")
async def avatar_encryption_preview(request: AvatarEncryptionPreviewRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.preview_protection, request)


@app.post("/api/avatar-encryption/apply-request")
async def avatar_encryption_apply_request(request: AvatarEncryptionApplyRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.request_protection_apply,
        request.model_dump(by_alias=True),
        request.target_shader_family,
        agent_name="desktop-agent",
    )


@app.post("/api/avatar-encryption/remove-request")
async def avatar_encryption_remove_request(request: AvatarEncryptionRemoveRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.request_protection_remove,
        request.model_dump(by_alias=True),
        agent_name="desktop-agent",
    )


@app.post("/api/vision/capture")
async def capture_avatar_screenshot(request: VisionCaptureRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.request_avatar_screenshot, request)


@app.post("/api/vision/capture-status")
async def read_vision_capture_status(request: VisionCaptureStatusRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.read_vision_capture_status, request)


@app.post("/api/vision/capture-multi")
async def capture_avatar_multi_screenshot(request: VisionCaptureMultiRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.request_avatar_multi_screenshot,
        request,
    )


@app.post("/api/vision/audit")
async def audit_avatar_screenshot(request: VisionAuditRequest) -> dict[str, Any]:
    return await asyncio.to_thread(SHADER_VISION_PROTECTION.audit_avatar_screenshot, request)


@app.post("/api/vision/audit-multi")
async def audit_avatar_multi_screenshot(request: VisionAuditMultiRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        SHADER_VISION_PROTECTION.audit_avatar_multi_screenshot,
        request,
    )


def _read_avatars_tuning_adapter(request: DashboardRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        export_payload, export_source, using_mock_execute = load_dashboard_export_payload(settings, request)
        avatars = serialize_avatar_list(export_payload)
        emit_log("info", "avatar", "Blendshape avatar export loaded.", {"count": len(avatars), "source": export_source})
        return {
            "ok": True,
            "executed": not using_mock_execute,
            "exportSource": export_source,
            "executionMode": "mock" if using_mock_execute else "live-unity",
            "summary": export_payload.get("summary", {}),
            "avatars": avatars,
            "avatarCount": len(avatars),
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "avatar", "Failed to load avatar export.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def _scan_scene_avatars_tuning_adapter(request: AvatarSceneScanRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        export_payload = export_blendshapes(settings)
        avatars = serialize_avatar_list(export_payload)
        vrchat_avatars = [avatar for avatar in avatars if avatar.get("isVrChatAvatar")]
        avatars = vrchat_avatars or avatars
        DASHBOARD_RUNTIME.scene_avatars = avatars
        emit_log(
            "info",
            "avatar",
            "Scene avatar scan completed from blendshape export.",
            {"count": len(avatars), "summary": export_payload.get("summary", {})},
        )
        return {
            "ok": True,
            "avatars": avatars,
            "avatarCount": len(avatars),
            "summary": export_payload.get("summary", {}),
            "exportSource": "unity-mcp export",
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "avatar", "Failed to scan scene avatars.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def _read_avatar_blendshapes_tuning_adapter(request: AvatarBlendshapeListRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        export_payload, export_source, using_mock_execute = load_dashboard_export_payload(settings, request)
        selected_avatar = resolve_avatar_selection(export_payload, request.avatar)
        remember_loaded_avatar(selected_avatar.avatar_name, selected_avatar.avatar_path)
        blendshapes = serialize_blendshape_details(export_payload, selected_avatar)
        emit_log(
            "info",
            "blendshape",
            "Avatar blendshape list loaded.",
            {"avatarPath": selected_avatar.avatar_path, "count": len(blendshapes)},
        )
        return {
            "ok": True,
            "exportSource": export_source,
            "executionMode": "mock" if using_mock_execute else "live-unity",
            "generatedAtUtc": export_payload.get("generatedAtUtc"),
            "summary": export_payload.get("summary", {}),
            "avatars": export_payload.get("avatars", []),
            "selectedAvatar": serialize_selected_avatar(selected_avatar),
            "blendshapes": blendshapes,
            "filterScope": "face",
            "filterNote": "Only face-related blendshapes are shown for the face editor.",
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "blendshape", "Failed to load blendshape list.", {"error": str(exc)})
        raise to_http_exception(exc) from exc




def _preview_manual_blendshapes_adapter(request: ManualBlendshapeApplyRequest) -> dict[str, Any]:
    try:
        if not request.adjustments:
            raise RuntimeError("No blendshape adjustments were provided.")

        settings = load_dashboard_settings(request)
        export_payload, export_source, using_mock_execute = load_dashboard_export_payload(settings, request)
        selected_avatar = resolve_avatar_selection(export_payload, request.avatar)
        remember_loaded_avatar(selected_avatar.avatar_name, selected_avatar.avatar_path)

        validated_adjustments = []
        skipped_adjustments: list[dict[str, Any]] = []
        undo_items: list[dict[str, Any]] = []
        allowed_targets = build_allowed_blendshape_index(export_payload, selected_avatar.avatar_path)
        locked_targets = build_locked_blendshape_set(
            AVATAR_TUNING_STORES.load_locked_blendshapes(selected_avatar.avatar_path)
        )
        for item in request.adjustments:
            key = (item.renderer_path, item.blendshape_name)
            if key not in allowed_targets:
                skipped_adjustments.append(
                    {
                        "rendererPath": item.renderer_path,
                        "blendshapeName": item.blendshape_name,
                        "reason": "missing_blendshape",
                    }
                )
                continue
            if is_blendshape_locked(item.renderer_path, item.blendshape_name, locked_targets):
                skipped_adjustments.append(
                    {
                        "rendererPath": item.renderer_path,
                        "blendshapeName": item.blendshape_name,
                        "reason": "locked",
                    }
                )
                continue

            current_weight = allowed_targets[key]["currentWeight"]
            previous_weight = current_weight if item.previous_weight is None else item.previous_weight
            validated_adjustments.append(
                {
                    "rendererPath": item.renderer_path,
                    "blendshapeName": item.blendshape_name,
                    "targetWeight": clamp_blendshape_weight(item.target_weight),
                }
            )
            undo_items.append(
                {
                    "rendererPath": item.renderer_path,
                    "blendshapeName": item.blendshape_name,
                    "targetWeight": previous_weight,
                }
            )

        if not validated_adjustments:
            emit_log(
                "warning",
                "blendshape",
                "No manual blendshape adjustments were applied after lock/missing-target filtering.",
                {"avatarPath": selected_avatar.avatar_path, "skippedCount": len(skipped_adjustments)},
            )
            return {
                "ok": True,
                "selectedAvatar": serialize_selected_avatar(selected_avatar),
                "executionMode": "mock" if using_mock_execute else "live-unity",
                "result": None,
                "appliedAdjustments": [],
                "skippedAdjustments": skipped_adjustments,
                "undoDepth": AVATAR_TUNING_UNDO.depth(selected_avatar.avatar_path),
            }

        if using_mock_execute:
            apply_payload = render_manual_blendshape_payload_json(selected_avatar.avatar_path, validated_adjustments)
            result = mock_execute_payload(apply_payload, selected_avatar, export_source)
        else:
            result = apply_blendshapes_direct(settings, selected_avatar.avatar_path, validated_adjustments)

        undo_depth = AVATAR_TUNING_UNDO.push(selected_avatar.avatar_path, undo_items)
        emit_log(
            "success",
            "blendshape",
            "Manual blendshape adjustments applied.",
            {"avatarPath": selected_avatar.avatar_path, "count": len(validated_adjustments)},
        )
        return {
            "ok": True,
            "selectedAvatar": serialize_selected_avatar(selected_avatar),
            "executionMode": "mock" if using_mock_execute else "live-unity",
            "result": serialize_result(result),
            "appliedAdjustments": validated_adjustments,
            "skippedAdjustments": skipped_adjustments,
            "undoDepth": undo_depth,
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "blendshape", "Failed to apply manual blendshape adjustments.", {"error": str(exc)})
        raise to_http_exception(exc) from exc








def load_shader_tuning_history_store() -> dict[str, Any]:
    return load_tuning_store(
        SHADER_TUNING_HISTORY_PATH,
        {
            "type": "shader_tuning_history",
            "version": "0.2",
            "records": [],
        },
    )


def load_shader_tuning_preset_store() -> dict[str, Any]:
    return load_tuning_store(
        SHADER_TUNING_PRESETS_PATH,
        {
            "type": "shader_tuning_presets",
            "version": "0.2",
            "presets": [],
        },
    )


def load_shader_tuning_locks_store() -> dict[str, Any]:
    return load_tuning_store(
        SHADER_TUNING_LOCKS_PATH,
        {
            "type": "shader_tuning_locks",
            "version": "0.2",
            "avatars": {},
        },
    )


def load_tuning_store(path: Path, default_payload: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(default_payload))

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Tuning store is not valid JSON: {path}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"Tuning store must be a JSON object: {path}")

    merged = json.loads(json.dumps(default_payload))
    merged.update(payload)
    return merged


def save_tuning_store(path: Path, payload: dict[str, Any]) -> None:
    with TUNING_STORE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(path)


def tuning_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_tuning_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"


def normalize_locked_blendshape_item(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None

    renderer_path = str(item.get("rendererPath") or item.get("renderer_path") or "").strip()
    blendshape_name = str(item.get("blendshapeName") or item.get("blendshape_name") or item.get("blendshape") or "").strip()
    if not blendshape_name:
        return None

    return {
        "rendererPath": renderer_path,
        "blendshapeName": blendshape_name,
    }


def normalize_locked_blendshape_list(items: list[dict[str, Any]] | list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items or []:
        normalized_item = normalize_locked_blendshape_item(item)
        if normalized_item is None:
            continue
        key = (normalized_item["rendererPath"], normalized_item["blendshapeName"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_item)
    return normalized




def _ai_select_tuning_locks_adapter(request: TuningLocksAiSelectRequest) -> dict[str, Any]:
    instruction = request.selection_instruction.strip()
    if not instruction:
        raise to_http_exception(RuntimeError("selection_instruction is required for AI lock selection."))

    settings = load_dashboard_settings(request)
    if provider_requires_api_key(settings.llm_provider) and not settings.llm_api_key:
        raise to_http_exception(RuntimeError(f"{provider_display_name(settings.llm_provider)} API key is empty."))

    candidates = normalize_ai_lock_candidates(request.candidate_blendshapes)
    if not candidates:
        raise to_http_exception(RuntimeError("No candidate Blendshapes were provided for AI lock selection."))

    current_locked = normalize_locked_blendshape_list(request.current_locked_blendshapes)
    prompt = build_ai_lock_selection_prompt(
        action=request.action,
        instruction=instruction,
        candidates=candidates,
        current_locked=current_locked,
    )
    try:
        raw_response = request_llm_plan(settings, prompt)
        raw_json = extract_json_block(raw_response)
        payload = json.loads(raw_json) if raw_json else {}
    except Exception as exc:  # noqa: BLE001
        raise to_http_exception(RuntimeError(f"AI lock selection failed: {exc}")) from exc

    selected = validate_ai_lock_selection(payload, candidates)
    if request.action == "unlock":
        locked_keys = {
            (item["rendererPath"], item["blendshapeName"])
            for item in current_locked
        }
        selected = [
            item
            for item in selected
            if (item["rendererPath"], item["blendshapeName"]) in locked_keys
        ]
    emit_log(
        "info",
        "blendshape",
        "AI lock selection completed.",
        {"action": request.action, "instruction": instruction, "selectedCount": len(selected)},
    )
    return {
        "ok": True,
        "action": request.action,
        "instruction": instruction,
        "selectedBlendshapes": selected,
        "warnings": payload.get("warnings") if isinstance(payload.get("warnings"), list) else [],
        "rawSummary": str(payload.get("summary") or ""),
    }


def normalize_ai_lock_candidates(items: list[dict[str, Any]] | list[Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        renderer_path = str(item.get("rendererPath") or item.get("renderer_path") or "").strip()
        blendshape_name = str(item.get("blendshapeName") or item.get("blendshape_name") or item.get("blendshape") or "").strip()
        if not blendshape_name:
            continue
        key = (renderer_path, blendshape_name)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "rendererPath": renderer_path,
                "blendshapeName": blendshape_name,
                "currentWeight": item.get("currentWeight", item.get("current_weight", 0)),
                "rendererName": item.get("rendererName", item.get("renderer_name", "")),
            }
        )
    return candidates[:400]


def build_ai_lock_selection_prompt(
    action: str,
    instruction: str,
    candidates: list[dict[str, Any]],
    current_locked: list[dict[str, str]],
) -> str:
    schema = {
        "summary": "Selected eye and mouth-corner blendshapes.",
        "selected": [
            {
                "rendererPath": "Avatar/Body",
                "blendshapeName": "eye_smile_L",
                "reason": "Matches the requested eye area.",
            }
        ],
        "warnings": [],
    }
    return (
        "You are helping a VRChat avatar editor choose which face Blendshapes should be locked or unlocked.\n"
        "Return JSON only. Do not output Markdown.\n"
        "Only select exact rendererPath and blendshapeName pairs from the candidate list.\n"
        "If the action is unlock, only select candidates that are already listed in Current locked Blendshapes.\n"
        "Prefer conservative, semantically relevant selections. If the user asks for eyes, choose eye/eyelid/pupil-related names; "
        "if mouth or smile, choose mouth/lip/corner/smile-related names; if brows, choose brow/eyebrow-related names.\n"
        "Do not select unrelated body, clothing, hair, or accessory blendshapes.\n"
        f"Requested action: {action}.\n"
        f"User selection instruction: {instruction}\n"
        f"Current locked Blendshapes: {json.dumps(current_locked, ensure_ascii=False)}\n"
        f"Output JSON shape example: {json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Candidate Blendshapes:\n{json.dumps(candidates, ensure_ascii=False, indent=2)}"
    )


def validate_ai_lock_selection(payload: Any, candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    allowed = {
        (str(item.get("rendererPath") or ""), str(item.get("blendshapeName") or ""))
        for item in candidates
    }
    selected = payload.get("selected") or payload.get("selectedBlendshapes") or payload.get("blendshapes") or []
    if not isinstance(selected, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in selected:
        candidate = normalize_locked_blendshape_item(item)
        if candidate is None:
            continue
        key = (candidate["rendererPath"], candidate["blendshapeName"])
        if key not in allowed or key in seen:
            continue
        seen.add(key)
        normalized.append(candidate)
    return normalized


def load_shader_tuning_locks(avatar_path: str | None) -> dict[str, Any]:
    if not avatar_path:
        return {"lockedMaterials": [], "lockedProperties": []}
    store = load_shader_tuning_locks_store()
    avatars = store.get("avatars") if isinstance(store.get("avatars"), dict) else {}
    payload = avatars.get(avatar_path) if isinstance(avatars.get(avatar_path), dict) else {}
    return {
        "lockedMaterials": normalize_string_list(payload.get("lockedMaterials") or payload.get("locked_materials") or []),
        "lockedProperties": normalize_string_list(payload.get("lockedProperties") or payload.get("locked_properties") or []),
    }


def update_shader_tuning_locks_sync(request: ShaderTuningLocksUpdateRequest) -> dict[str, Any]:
    avatar_path = (request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path or "").strip()
    if not avatar_path:
        raise to_http_exception(RuntimeError("avatar_path is required before updating shader locks."))

    locked_materials = normalize_string_list(request.locked_materials)
    locked_properties = normalize_string_list(request.locked_properties)
    store = load_shader_tuning_locks_store()
    avatars = store.get("avatars") if isinstance(store.get("avatars"), dict) else {}
    avatars[avatar_path] = {
        "lockedMaterials": locked_materials,
        "lockedProperties": locked_properties,
    }
    store["avatars"] = avatars
    save_tuning_store(SHADER_TUNING_LOCKS_PATH, store)
    emit_log(
        "info",
        "shader",
        "Shader material lock list updated.",
        {"avatarPath": avatar_path, "materials": len(locked_materials), "properties": len(locked_properties)},
    )
    return {
        "ok": True,
        "avatarPath": avatar_path,
        "lockedMaterials": locked_materials,
        "lockedProperties": locked_properties,
    }


def normalize_string_list(items: list[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def build_locked_blendshape_set(locked_blendshapes: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        (
            str(item.get("rendererPath") or item.get("renderer_path") or ""),
            str(item.get("blendshapeName") or item.get("blendshape_name") or item.get("blendshape") or ""),
        )
        for item in normalize_locked_blendshape_list(locked_blendshapes)
    }


def is_blendshape_locked(renderer_path: str, blendshape_name: str, locked_targets: set[tuple[str, str]]) -> bool:
    return (renderer_path, blendshape_name) in locked_targets or ("", blendshape_name) in locked_targets


def filter_plan_locked_blendshapes(plan: Any, locked_blendshapes: list[dict[str, Any]]) -> Any:
    locked_targets = build_locked_blendshape_set(locked_blendshapes)
    if not locked_targets:
        return plan

    kept = []
    dropped = []
    for adjustment in plan.adjustments:
        if is_blendshape_locked(adjustment.renderer_path, adjustment.blendshape_name, locked_targets):
            dropped.append(adjustment)
            continue
        kept.append(adjustment)

    if not dropped:
        return plan

    warnings = list(getattr(plan, "warnings", []) or [])
    warnings.append(
        "Skipped locked Blendshape adjustments: "
        + ", ".join(f"{item.renderer_path}::{item.blendshape_name}" for item in dropped[:8])
    )
    return plan.__class__(summary=plan.summary, warnings=warnings, adjustments=kept)


def filter_planning_payload_locked_blendshapes(payload: dict[str, Any], locked_blendshapes: list[dict[str, Any]]) -> dict[str, Any]:
    locked_targets = build_locked_blendshape_set(locked_blendshapes)
    if not locked_targets:
        return payload

    filtered_payload = json.loads(json.dumps(payload))
    renderer_count = 0
    blendshape_count = 0
    filtered_avatars: list[dict[str, Any]] = []

    for avatar in filtered_payload.get("avatars") or []:
        filtered_renderers: list[dict[str, Any]] = []
        for renderer in avatar.get("renderers") or []:
            renderer_path = str(renderer.get("rendererPath") or renderer.get("path") or "")
            kept_blendshapes = []
            for blendshape in renderer.get("blendshapes") or []:
                blendshape_name = str(blendshape.get("name") or blendshape.get("blendshapeName") or "")
                if is_blendshape_locked(renderer_path, blendshape_name, locked_targets):
                    continue
                kept_blendshapes.append(blendshape)
            if not kept_blendshapes:
                continue
            renderer["blendshapes"] = kept_blendshapes
            renderer["blendshapeCount"] = len(kept_blendshapes)
            filtered_renderers.append(renderer)
            blendshape_count += len(kept_blendshapes)

        if filtered_renderers:
            avatar["renderers"] = filtered_renderers
            filtered_avatars.append(avatar)
            renderer_count += len(filtered_renderers)

    filtered_payload["avatars"] = filtered_avatars
    summary = dict(filtered_payload.get("summary") or {})
    summary["avatarCount"] = len(filtered_avatars)
    summary["rendererCount"] = renderer_count
    summary["blendshapeCount"] = blendshape_count
    filtered_payload["summary"] = summary
    filtered_payload["lockedBlendshapeFilter"] = {
        "scope": "unlocked_blendshapes_only",
        "lockedCount": len(locked_targets),
        "note": "Locked Blendshapes are hidden from planning and also blocked during apply.",
    }
    return filtered_payload


def build_tuning_history_record(
    *,
    request: DashboardRequest,
    settings: Settings,
    selected_avatar: SelectedAvatar,
    plan: Any,
    change_preview: list[dict[str, Any]],
    reference_context: dict[str, Any] | None,
    locked_blendshapes: list[dict[str, Any]],
    applied: bool,
    visual_proof: dict[str, Any] | None,
    artifacts: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "id": make_tuning_id("hist"),
        "created_at": tuning_timestamp(),
        "avatar_name": selected_avatar.avatar_name,
        "avatar_path": selected_avatar.avatar_path,
        "user_prompt": request.instruction or "",
        "provider": provider_display_name(settings.llm_provider),
        "provider_id": settings.llm_provider,
        "model": settings.llm_model,
        "reference_image_count": int((reference_context or {}).get("count") or 0),
        "applied": bool(applied),
        "changes": tuning_changes_from_preview(change_preview),
        "locked_blendshapes": normalize_locked_blendshape_list(locked_blendshapes),
        "notes": "",
        "label": "",
        "thumbnail_paths": extract_tuning_thumbnail_paths(visual_proof),
        "artifacts": artifacts or {},
        "summary": getattr(plan, "summary", "") or "",
        "warnings": list(getattr(plan, "warnings", []) or []),
    }


def tuning_changes_from_preview(change_preview: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for item in change_preview or []:
        before = clamp_blendshape_weight(item.get("previousWeight", 0.0))
        after = clamp_blendshape_weight(item.get("targetWeight", before))
        changes.append(
            {
                "avatar_path": str(item.get("avatarPath") or ""),
                "renderer_path": str(item.get("rendererPath") or ""),
                "blendshape": str(item.get("blendshapeName") or ""),
                "before": before,
                "after": after,
                "delta": after - before,
                "reason": str(item.get("reason") or ""),
                "confidence": clamp01(item.get("confidence", 0.0)),
            }
        )
    return changes


def extract_tuning_thumbnail_paths(visual_proof: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(visual_proof, dict):
        return {}
    thumbnails: dict[str, str] = {}
    for key in ("before", "after"):
        image_path = (visual_proof.get(key) or {}).get("imagePath") if isinstance(visual_proof.get(key), dict) else None
        if image_path:
            thumbnails[key] = str(image_path)
    return thumbnails




def trim_presets_for_avatar(presets: list[dict[str, Any]], max_presets: int) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(max_presets or 10), 100))
    grouped: dict[str, list[dict[str, Any]]] = {}
    ordered_keys: list[str] = []
    for preset in presets:
        avatar_key = str(preset.get("avatar_path") or preset.get("avatar_name") or "__global__")
        if avatar_key not in grouped:
            grouped[avatar_key] = []
            ordered_keys.append(avatar_key)
        grouped[avatar_key].append(preset)

    trimmed: list[dict[str, Any]] = []
    for avatar_key in ordered_keys:
        avatar_presets = grouped[avatar_key]
        if len(avatar_presets) > safe_limit:
            avatar_presets = avatar_presets[-safe_limit:]
        trimmed.extend(avatar_presets)
    return trimmed




def _preview_saved_tuning_history_adapter(history_id: str, request: DashboardRequest) -> dict[str, Any]:
    try:
        record = AVATAR_TUNING_STORES.find_history(history_id)
        payload = apply_saved_tuning_payload(record, request, source_type="history")
        AVATAR_TUNING_STORES.mark_history_applied(history_id)
        payload["historyRecord"] = AVATAR_TUNING_STORES.find_history(history_id)
        return payload
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "preset", "Failed to reapply tuning history.", {"historyId": history_id, "error": str(exc)})
        raise to_http_exception(exc) from exc


def _preview_saved_tuning_preset_adapter(preset_id: str, request: DashboardRequest) -> dict[str, Any]:
    try:
        preset = AVATAR_TUNING_STORES.find_preset(preset_id)
        payload = apply_saved_tuning_payload(preset, request, source_type="preset")
        AVATAR_TUNING_STORES.mark_preset_applied(preset_id)
        payload["preset"] = AVATAR_TUNING_STORES.find_preset(preset_id)
        return payload
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "preset", "Failed to apply tuning preset.", {"presetId": preset_id, "error": str(exc)})
        raise to_http_exception(exc) from exc




def apply_saved_tuning_payload(saved_payload: dict[str, Any], request: DashboardRequest, source_type: str) -> dict[str, Any]:
    settings = load_dashboard_settings(request)
    export_payload, export_source, using_mock_execute = load_dashboard_export_payload(settings, request)
    avatar_hint = request.avatar or saved_payload.get("avatar_path") or saved_payload.get("avatar_name")
    selected_avatar = resolve_avatar_selection(export_payload, avatar_hint)
    remember_loaded_avatar(selected_avatar.avatar_name, selected_avatar.avatar_path)

    allowed_targets = build_allowed_blendshape_index(export_payload, selected_avatar.avatar_path)
    locked_blendshapes = AVATAR_TUNING_STORES.load_locked_blendshapes(
        selected_avatar.avatar_path
    )
    locked_targets = build_locked_blendshape_set(locked_blendshapes)
    direct_adjustments: list[dict[str, Any]] = []
    undo_items: list[dict[str, Any]] = []
    change_preview: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for change in saved_payload.get("changes") or []:
        renderer_path = str(change.get("renderer_path") or change.get("rendererPath") or "")
        blendshape_name = str(change.get("blendshape") or change.get("blendshapeName") or change.get("blendshape_name") or "")
        key = (renderer_path, blendshape_name)
        if key not in allowed_targets:
            skipped.append({"rendererPath": renderer_path, "blendshapeName": blendshape_name, "reason": "missing_blendshape"})
            continue
        if is_blendshape_locked(renderer_path, blendshape_name, locked_targets):
            skipped.append({"rendererPath": renderer_path, "blendshapeName": blendshape_name, "reason": "locked"})
            continue

        current_weight = clamp_blendshape_weight(allowed_targets[key].get("currentWeight", 0.0))
        target_weight = clamp_blendshape_weight(change.get("after", change.get("targetWeight", current_weight)))
        direct_adjustments.append(
            {
                "rendererPath": renderer_path,
                "blendshapeName": blendshape_name,
                "targetWeight": target_weight,
            }
        )
        undo_items.append(
            {
                "rendererPath": renderer_path,
                "blendshapeName": blendshape_name,
                "targetWeight": current_weight,
            }
        )
        change_preview.append(
            {
                "avatarPath": selected_avatar.avatar_path,
                "rendererPath": renderer_path,
                "blendshapeName": blendshape_name,
                "previousWeight": current_weight,
                "targetWeight": target_weight,
                "delta": target_weight - current_weight,
                "reason": str(change.get("reason") or f"Reapply saved {source_type} after value."),
                "confidence": clamp01(change.get("confidence", 1.0)),
            }
        )

    result: McpResult | None = None
    if direct_adjustments:
        if using_mock_execute:
            apply_payload = render_manual_blendshape_payload_json(selected_avatar.avatar_path, direct_adjustments)
            result = mock_execute_payload(apply_payload, selected_avatar, export_source)
        else:
            result = apply_blendshapes_direct(settings, selected_avatar.avatar_path, direct_adjustments)
        AVATAR_TUNING_UNDO.push(selected_avatar.avatar_path, undo_items)

    emit_log(
        "success" if direct_adjustments else "warning",
        "preset",
        f"Applied saved tuning {source_type}." if direct_adjustments else f"No saved tuning {source_type} changes were applied.",
        {"avatarPath": selected_avatar.avatar_path, "appliedCount": len(direct_adjustments), "skippedCount": len(skipped)},
    )
    return {
        "ok": True,
        "sourceType": source_type,
        "selectedAvatar": serialize_selected_avatar(selected_avatar),
        "executionMode": "mock" if using_mock_execute else "live-unity",
        "result": serialize_result(result),
        "appliedAdjustments": direct_adjustments,
        "skippedAdjustments": skipped,
        "changePreview": change_preview,
        "lockedBlendshapes": locked_blendshapes,
        "undoDepth": AVATAR_TUNING_UNDO.depth(selected_avatar.avatar_path),
    }




def toggle_clothing_sync(request: ClothingToggleRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        result = toggle_scene_object_direct(settings, request.object_path, request.active)
        payload = ensure_dict_payload(result, "clothing toggle")
        emit_log(
            "success",
            "fx",
            "Clothing object toggled.",
            {"objectPath": request.object_path, "active": request.active},
        )
        return {"ok": True, "result": payload}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "fx", "Failed to toggle clothing object.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def scan_avatar_parameters_sync(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        payload = scan_avatar_parameters_direct(settings, avatar_path)
        emit_log("info", "parameter", "Avatar parameter scan completed.", {"avatarPath": avatar_path})
        return {"ok": True, "avatarPath": avatar_path, "stats": payload}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "parameter", "Failed to scan avatar parameters.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def optimize_avatar_parameters_sync(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        stats = scan_avatar_parameters_direct(settings, avatar_path)
        suggestions = stats.get("suggestions") or []
        payload = {
            "suggestionCount": len(suggestions),
            "suggestions": suggestions,
            "note": stats.get("note") or "Suggestions are heuristic only. Review animator conditions and menu bindings before changing parameter types.",
        }
        emit_log("success", "parameter", "Avatar parameter optimization suggestions generated.", {"avatarPath": avatar_path})
        return {"ok": True, "avatarPath": avatar_path, "optimization": payload}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "parameter", "Failed to build parameter optimization suggestions.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def scan_shader_materials_sync(request: ShaderMaterialScanRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        inventory = scan_shader_materials_direct(settings, avatar_path)
        materials = ensure_list_payload(inventory.get("materials") or [], "shader material inventory")
        overrides = dict(request.category_overrides or {})
        if overrides:
            ambiguous_material_ids = find_ambiguous_shader_material_ids(inventory)
            for material in materials:
                if not isinstance(material, dict):
                    continue
                material_id = str(material.get("material_id") or "")
                override = overrides.get(material_id)
                if (
                    material_id not in ambiguous_material_ids
                    and override in {"skin", "eyes", "hair", "clothes", "accessory", "unknown"}
                ):
                    material["category"] = override

        emit_log(
            "info",
            "shader",
            "Shader material inventory scanned.",
            {"avatarPath": avatar_path, "materialCount": len(materials), "jsonPath": inventory.get("jsonPath")},
        )
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "inventory": inventory,
            "materials": materials,
            "summary": inventory.get("summary") or {},
            "jsonPath": inventory.get("jsonPath") or inventory.get("absoluteOutputPath"),
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "shader", "Failed to scan shader materials.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def build_avatar_encryption_research_report_sync(request: AvatarEncryptionResearchRequest | None = None) -> dict[str, Any]:
    include_refs = True if request is None else bool(request.include_external_references)
    references = []
    if include_refs:
        references = [
            {
                "id": "liltoon",
                "label": "lilToon",
                "role": "primary open shader base for the first fork/prototype",
                "reusePolicy": "inspect and pin before any fork; keep restore patch minimal",
            },
            {
                "id": "avacrypt-v2-liltoon",
                "label": "AvaCrypt V2 lilToon fork",
                "role": "research reference for optional Anti-Rip compatibility",
                "reusePolicy": "research only until license, trust, and code review are complete",
            },
            {
                "id": "poiyomi-toon",
                "label": "Poiyomi Toon Shader",
                "role": "second first-class shader family after lilToon proves the abstraction",
                "reusePolicy": "public Toon line first; private/Pro modules out of scope until cleared",
            },
        ]

    return {
        "ok": True,
        "schema": AVATAR_ENCRYPTION_SCHEMA,
        "addonVersion": AVATAR_ENCRYPTION_ADDON_VERSION,
        "phase": "M0/M4",
        "track": "avatar-encryption-addon",
        "readOnly": True,
        "writeStatus": "private_addon_connector_required",
        "writeBoundary": "Apply/remove are exposed only as dedicated approval requests that hand off to a configured private addon connector.",
        "firstClassShaderFamilies": ["lilToon", "Poiyomi"],
        "compatibilityPolicy": {
            "genericSemantic": "scan-only until validated support exists",
            "standardOrUnknown": "compatibility report only; never auto-convert",
            "otherShaderFamilies": "collect family/material evidence and keep apply blocked by default",
        },
        "securityPrinciples": [
            "Opt-in only; never run as part of normal optimization/import flows.",
            "Creator-owned local assets only.",
            "Generated encrypted copies preserve originals and must use checkpointed apply/remove requests.",
            "Describe protection claims as Avatar Encryption / Anti-Rip hardening with proof gates.",
            "Do not write secrets, paid asset payloads, or private addon outputs into .vsk packages.",
        ],
        "keyModel": [
            {
                "id": "internal",
                "status": "managed_by_vrcforge",
                "security": "mvp",
                "notes": "Implementation details are intentionally omitted from public reports.",
            },
        ],
        "layers": [
            {"id": "lite", "status": "available", "default": False},
            {"id": "standard", "status": "available", "default": True},
            {"id": "paranoid", "status": "proof_gated", "default": False},
        ],
        "milestones": [
            "M0 research packet",
            "M1 local disposable lilToon prototype",
            "M2 VRCForge scan/plan/preview skill",
            "M3 lilToon apply/remove request with checkpointed generated copies",
            "M4 Poiyomi apply/remove request through the shared restore abstraction",
            "M5 governed .vsk addon packaging",
        ],
        "externalReferences": references,
    }


def avatar_encryption_addon_status_sync() -> dict[str, Any]:
    base_url = os.environ.get(AVATAR_ENCRYPTION_ADDON_URL_ENV, "").strip().rstrip("/")
    token_present = bool(os.environ.get(AVATAR_ENCRYPTION_ADDON_TOKEN_ENV, "").strip())
    return {
        "ok": True,
        "schema": AVATAR_ENCRYPTION_SCHEMA,
        "addonVersion": AVATAR_ENCRYPTION_ADDON_VERSION,
        "connector": {
            "configured": bool(base_url),
            "baseUrlConfigured": bool(base_url),
            "tokenPresent": token_present,
            "applyTool": AVATAR_ENCRYPTION_ADDON_APPLY_TOOL,
            "removeTool": AVATAR_ENCRYPTION_ADDON_REMOVE_TOOL,
            "contract": "private-addon-rest-v1",
            "publicRepoImplementation": False,
            "status": "configured" if base_url else "private_addon_not_configured",
            "blocker": "" if base_url else "Set VRCFORGE_AVATAR_ENCRYPTION_ADDON_URL to a trusted private addon endpoint before apply/remove.",
        },
    }


def call_avatar_encryption_addon(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    status = avatar_encryption_addon_status_sync()
    connector = ensure_dict(status.get("connector"))
    base_url = os.environ.get(AVATAR_ENCRYPTION_ADDON_URL_ENV, "").strip().rstrip("/")
    if not base_url:
        return {
            "ok": False,
            "status": "blocked",
            "schema": AVATAR_ENCRYPTION_SCHEMA,
            "error": str(connector.get("blocker") or "Private Avatar Encryption addon is not configured."),
            "connector": connector,
        }
    url = f"{base_url}/api/v1/avatar-encryption/{endpoint.lstrip('/')}"
    body = json.dumps({"schema": AVATAR_ENCRYPTION_SCHEMA, "params": params}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.environ.get(AVATAR_ENCRYPTION_ADDON_TOKEN_ENV, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - configured trusted local/private addon endpoint.
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "status": "failed",
            "schema": AVATAR_ENCRYPTION_SCHEMA,
            "error": f"Private Avatar Encryption addon call failed: {exc}",
            "connector": connector,
        }
    result = ensure_dict_payload(payload, "avatar encryption private addon response")
    result.setdefault("schema", AVATAR_ENCRYPTION_SCHEMA)
    result.setdefault("connector", connector)
    return result


def scan_avatar_encryption_sync(request: AvatarEncryptionScanRequest) -> dict[str, Any]:
    try:
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        if request.inventory is not None:
            inventory = copy.deepcopy(request.inventory)
        else:
            settings = load_dashboard_settings(request)
            inventory = scan_shader_materials_direct(settings, avatar_path)
        scan = build_avatar_encryption_scan_payload(
            inventory=inventory,
            avatar_path=avatar_path,
            include_compatibility=bool(request.include_compatibility),
        )
        emit_log(
            "info",
            "avatar-encryption",
            "Avatar encryption compatibility scanned.",
            {
                "avatarPath": avatar_path,
                "candidateCount": scan["summary"]["candidateCount"],
                "compatibilityOnlyCount": scan["summary"]["compatibilityOnlyCount"],
            },
        )
        return scan
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "avatar-encryption", "Failed to scan avatar encryption compatibility.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def plan_avatar_encryption_sync(request: AvatarEncryptionPlanRequest) -> dict[str, Any]:
    profile = avatar_encryption_request_profile(request)
    scan = scan_avatar_encryption_sync(
        AvatarEncryptionScanRequest(
            settings_path=request.settings_path,
            unity_host=request.unity_host,
            unity_port=request.unity_port,
            unity_instance=request.unity_instance,
            avatar_path=request.avatar_path,
            inventory=request.inventory,
            include_compatibility=request.include_compatibility,
        )
    )
    target_families = normalize_avatar_encryption_target_families(request.target_shader_families)
    unsupported_target_families = [family for family in target_families if family not in AVATAR_ENCRYPTION_PRIMARY_SHADER_FAMILIES]
    selected_candidates = [
        item for item in scan["targets"]
        if item.get("status") == "candidate" and item.get("shaderFamilyId") in target_families
    ]
    requested_target_filter_active = has_avatar_encryption_target_filter(request)
    if requested_target_filter_active:
        selected_candidates = filter_avatar_encryption_requested_targets(selected_candidates, request)
    platform = normalize_avatar_encryption_platform(request.target_platform or request.platform)
    addon_status = avatar_encryption_addon_status_sync()
    warnings: list[str] = []
    blocking_ids: list[str] = []

    if not request.confirm_creator_owned_assets:
        warnings.append("Creator-owned asset confirmation is required before any future apply/remove request.")
    if not selected_candidates:
        blocking_ids.append("shader_family.no_liltoon_or_poiyomi_candidate")
        if requested_target_filter_active:
            blocking_ids.append("targets.requested_targets_not_found")
    if unsupported_target_families:
        blocking_ids.append("shader_family.requested_restore_adapter_missing")
    if platform["status"] != "supported":
        blocking_ids.append("platform.windows_only")
    if not addon_status["connector"]["configured"]:
        blocking_ids.append("addon.private_module_not_configured")
    if profile.get("applyStatus") != "available":
        blocking_ids.append("profile.paranoid_blendshape_proof_required")

    plan_status = "blocked" if blocking_ids else "request_ready"
    plan = {
        "schema": AVATAR_ENCRYPTION_SCHEMA,
        "addonVersion": AVATAR_ENCRYPTION_ADDON_VERSION,
        "phase": "M3/M4-apply-request",
        "status": plan_status,
        "readOnly": True,
        "writeStatus": "blocked" if blocking_ids else "approval_request_available",
        "writeBlockReason": (
            "; ".join(blocking_ids)
            if blocking_ids
            else "Direct apply remains blocked. Use the dedicated request tools; approved execution is handed to the configured private addon after checkpoint."
        ),
        "profile": profile,
        "recommendedProfile": AVATAR_ENCRYPTION_RECOMMENDED_PROFILE,
        "profileCards": avatar_encryption_profile_cards(),
        "benchmarkTable": avatar_encryption_benchmark_table(),
        "benchmarkAssumptions": {
            "kind": "estimated_static_profile_budget",
            "baselineFps": 90,
            "avatarScales": list(AVATAR_ENCRYPTION_BENCHMARK_TRIANGLES),
            "note": "Deterministic planning estimate for profile comparison; replace with captured GPU data when project-specific benchmark evidence exists.",
        },
        "avatarPath": scan.get("avatarPath") or request.avatar_path or "",
        "targetShaderFamilies": list(target_families),
        "unsupportedTargetFamilies": unsupported_target_families,
        "priorityOrder": ["liltoon", "poiyomi", "compatibility-only"],
        "selectedCandidateCount": len(selected_candidates),
        "selectedCandidates": selected_candidates,
        "compatibilityTargets": scan["compatibilityTargets"],
        "externalAddon": addon_status["connector"],
        "platform": platform,
        "layers": [{"id": "profile_managed", "label": str(profile.get("label") or "Profile"), "status": "managed_by_private_addon"}],
        "hardGate": {
            "status": "blocked" if blocking_ids else "request_ready",
            "blockingIds": blocking_ids,
            "warnings": warnings,
        },
        "ownershipGate": {
            "status": "confirmed" if request.confirm_creator_owned_assets else "required_before_write",
            "confirmed": bool(request.confirm_creator_owned_assets),
            "requiredBeforeWrite": True,
        },
        "proofRequirements": avatar_encryption_proof_requirements(),
        "dynamicTimePolicy": {"mode": "private_addon_managed", "details": "Implementation details are intentionally omitted from public reports."},
        "futureRequestTools": {
            "liltoonApplyRequest": "vrcforge_avatar_encryption_liltoon_apply_request",
            "poiyomiApplyRequest": "vrcforge_avatar_encryption_poiyomi_apply_request",
            "removeRequest": "vrcforge_avatar_encryption_remove_request",
            "status": "registered_request_only_private_addon_connector",
        },
        "futureCapabilities": [
            {"id": "liltoon.apply_request", "tool": "vrcforge_avatar_encryption_liltoon_apply_request", "registered": True},
            {"id": "poiyomi.apply_request", "tool": "vrcforge_avatar_encryption_poiyomi_apply_request", "registered": True},
            {"id": "remove.restore_originals", "tool": "vrcforge_avatar_encryption_remove_request", "registered": True},
        ],
        "nextSteps": [
            "Run apply requests on creator-owned disposable copies before using them on production avatars.",
            "Capture before/apply/remove/rollback visual evidence for each real project proof.",
            "Use remove request first for normal cleanup; use checkpoint rollback when remove cannot resolve an original asset.",
            "Keep Generic/Standard/unknown shader families as compatibility-only until a restore adapter exists.",
            "Configure the private Avatar Encryption addon before any apply/remove request can execute.",
        ],
    }
    return {"ok": True, "schema": AVATAR_ENCRYPTION_SCHEMA, "scan": scan, "plan": plan}


def preview_avatar_encryption_sync(request: AvatarEncryptionPreviewRequest) -> dict[str, Any]:
    if request.plan and request.inventory is None and not request.avatar_path:
        plan_payload = copy.deepcopy(request.plan)
        plan = ensure_dict(plan_payload)
        hard_gate = ensure_dict(plan.get("hardGate"))
        blocking_ids = [
            str(item)
            for item in ensure_list_payload(hard_gate.get("blockingIds") or [], "avatar encryption hard gate blockers")
        ]
        if "plan.untrusted_external_plan" not in blocking_ids:
            blocking_ids.append("plan.untrusted_external_plan")
        hard_gate["status"] = "blocked"
        hard_gate["blockingIds"] = blocking_ids
        plan["status"] = "blocked"
        plan["writeStatus"] = "blocked"
        plan["writeBlockReason"] = "; ".join(blocking_ids)
        plan["selectedCandidates"] = []
        plan["hardGate"] = hard_gate
    else:
        plan_payload = plan_avatar_encryption_sync(request).get("plan")
        plan = ensure_dict(plan_payload)
    candidates = ensure_list_payload(plan.get("selectedCandidates") or [], "avatar encryption selected candidates")
    eligible_candidates, blocked_candidates = filter_avatar_encryption_preview_candidates(candidates)
    write_targets = [
        {
            "materialId": item.get("materialId") or "",
            "rendererId": item.get("rendererId") or "",
            "rendererPath": item.get("rendererPath") or "",
            "slotIndex": item.get("slotIndex", 0),
            "materialName": item.get("materialName") or "",
            "shaderFamily": item.get("shaderFamily") or "",
            "shaderFamilyId": normalize_avatar_encryption_shader_family(item.get("shaderFamilyId") or item.get("shaderFamily")),
            "adapterId": normalize_avatar_encryption_shader_family(item.get("shaderFamilyId") or item.get("shaderFamily")),
            "targetResolutionStatus": "resolved" if item.get("rendererPath") and item.get("materialId") else "needs_resolution",
            "wouldCreate": [
                "private addon output under the configured VRCForge output folder",
                "approval audit entry",
                "private addon rollback manifest",
            ],
            "wouldModifyOriginalAsset": False,
        }
        for item in eligible_candidates
        if isinstance(item, dict)
    ]
    return {
        "ok": True,
        "schema": AVATAR_ENCRYPTION_SCHEMA,
        "addonVersion": AVATAR_ENCRYPTION_ADDON_VERSION,
        "previewOnly": True,
        "writeAllowed": False,
        "wouldWrite": False,
        "applyRequestReady": bool(write_targets) and ensure_dict(plan.get("hardGate")).get("status") == "request_ready",
        "blockedApply": {
            "status": "approval_required" if write_targets and ensure_dict(plan.get("hardGate")).get("status") == "request_ready" else "blocked",
            "reason": (
                "Direct apply is unavailable. Use lilToon/Poiyomi apply-request; approved execution creates a pre-write checkpoint."
                if write_targets and ensure_dict(plan.get("hardGate")).get("status") == "request_ready"
                else "Avatar-encryption apply request is blocked by the hard gate or has no eligible lilToon/Poiyomi targets."
            ),
        },
        "plan": plan,
        "writeTargetsPreview": write_targets,
        "blockedTargetsPreview": blocked_candidates,
        "rollbackPolicyPreview": {
            "futureScope": ["Assets", "Packages", "ProjectSettings"],
            "requiresCheckpoint": True,
            "removeMustRestoreOriginalMeshesAndMaterials": True,
            "normalCleanupTool": "vrcforge_avatar_encryption_remove_request",
            "checkpointRollbackTool": "vrcforge_restore_checkpoint",
            "supportBundlesMustRedactSecrets": True,
        },
    }


def request_avatar_encryption_apply_sync(
    params: dict[str, Any],
    target_family: str | None = None,
    agent_name: str = "external-agent",
) -> dict[str, Any]:
    params = dict(params or {})
    family = normalize_avatar_encryption_shader_family(target_family or params.get("targetShaderFamily") or params.get("target_shader_family") or "")
    if family not in AVATAR_ENCRYPTION_PRIMARY_SHADER_FAMILIES:
        families = normalize_avatar_encryption_target_families(params.get("targetShaderFamilies") or params.get("target_shader_families"))
        family = families[0] if families and families[0] in AVATAR_ENCRYPTION_PRIMARY_SHADER_FAMILIES else ""
    if family not in AVATAR_ENCRYPTION_PRIMARY_SHADER_FAMILIES:
        return {"ok": False, "status": "blocked", "error": "targetShaderFamily must be lilToon or Poiyomi for avatar-encryption apply requests."}

    params["targetShaderFamilies"] = [family]
    request = AvatarEncryptionApplyRequest(**params)
    preview = preview_avatar_encryption_sync(request)
    plan = ensure_dict(preview.get("plan"))
    hard_gate = ensure_dict(plan.get("hardGate"))
    write_targets = ensure_list_payload(preview.get("writeTargetsPreview") or [], "avatar encryption write target preview")
    blocked_reasons: list[str] = []
    if hard_gate.get("status") != "request_ready":
        blocked_reasons.extend(str(item) for item in ensure_list_payload(hard_gate.get("blockingIds") or [], "avatar encryption hard gate blockers"))
    if not request.confirm_creator_owned_assets:
        blocked_reasons.append("ownership.confirm_creator_owned_assets_required")
    if not write_targets:
        blocked_reasons.append("targets.no_liltoon_or_poiyomi_targets")

    if blocked_reasons:
        return {
            "ok": False,
            "status": "blocked",
            "error": "; ".join(blocked_reasons),
            "preview": preview,
        }

    avatar_path = str(plan.get("avatarPath") or request.avatar_path or "").strip()
    project_path = normalize_path_string(request.project_path or "")
    try:
        output_folder = normalize_avatar_encryption_output_folder(request.output_folder, project_path)
    except AgentGatewayError as exc:
        return {"ok": False, "status": "blocked", "error": str(exc), "preview": preview}
    profile = ensure_dict(plan.get("profile"))
    apply_arguments = {
        "avatarPath": avatar_path,
        "projectPath": project_path,
        "profile": str(profile.get("id") or AVATAR_ENCRYPTION_RECOMMENDED_PROFILE),
        "protectionProfile": str(profile.get("id") or AVATAR_ENCRYPTION_RECOMMENDED_PROFILE),
        "targetShaderFamily": family,
        "targets": write_targets,
        "outputFolder": output_folder,
        "platform": str(ensure_dict(plan.get("platform")).get("id") or request.target_platform or request.platform or "pc"),
        "targetPlatform": str(ensure_dict(plan.get("platform")).get("id") or request.target_platform or request.platform or "pc"),
        "connectorContract": "private-addon-rest-v1",
        "preview": bool(request.preview_unity_write),
        "confirmCreatorOwnedAssets": True,
        "saveAssets": bool(request.save_assets),
    }
    request_preview = {
        **preview,
        "readyToRequest": True,
        "targetTool": AVATAR_ENCRYPTION_ADDON_APPLY_TOOL,
        "targetShaderFamily": family,
        "applyArguments": {
            **apply_arguments,
            "targets": write_targets,
        },
        "directApplyVisible": False,
        "requiresExplicitApproval": True,
        "checkpointRequired": True,
        "rollback": {
            "removeRequestTool": "vrcforge_avatar_encryption_remove_request",
            "checkpointRestoreTool": "vrcforge_restore_checkpoint",
            "manifestRequired": True,
        },
        "limitations": [
            "MVP blocks targets that still need additional validation.",
            "The public repository provides only the connector and supervised request path.",
            "Lite/Standard are available request profiles; Paranoid is blocked until additional proof is complete.",
            "A configured private Avatar Encryption addon is required for execution.",
        ],
    }
    return AGENT_GATEWAY.create_apply_request(
        {
            "target_tool": AVATAR_ENCRYPTION_ADDON_APPLY_TOOL,
            "arguments": apply_arguments,
            "reason": f"Request supervised Avatar Encryption apply for {avatar_encryption_shader_family_label(family)}.",
            "preview": request_preview,
            "agent_name": agent_name,
            "requires_explicit_approval": True,
            "explicit_approval_reason": "Avatar Encryption apply changes selected avatar assets; explicit approval is required even when global auto mode is enabled.",
        },
        internal_wrapper=True,
    )


def request_avatar_encryption_remove_sync(params: dict[str, Any], agent_name: str = "external-agent") -> dict[str, Any]:
    params = dict(params or {})
    request = AvatarEncryptionRemoveRequest(**params)
    if not request.confirm_remove:
        return {
            "ok": False,
            "status": "blocked",
            "error": "confirmRemove=true is required before creating an avatar-encryption remove request.",
        }
    if not str(request.manifest_path or "").strip() and not str(request.avatar_path or "").strip():
        return {
            "ok": False,
            "status": "blocked",
            "error": "manifestPath or avatarPath is required before creating an avatar-encryption remove request.",
        }
    addon_status = avatar_encryption_addon_status_sync()
    if not addon_status["connector"]["configured"]:
        return {
            "ok": False,
            "status": "blocked",
            "error": "addon.private_module_not_configured",
            "connector": addon_status["connector"],
        }
    project_path = normalize_path_string(request.project_path or "")
    try:
        output_folder = normalize_avatar_encryption_output_folder(request.output_folder, project_path)
        manifest_path = normalize_avatar_encryption_manifest_path(request.manifest_path, project_path)
    except AgentGatewayError as exc:
        return {"ok": False, "status": "blocked", "error": str(exc), "connector": addon_status["connector"]}
    arguments = {
        "avatarPath": request.avatar_path or "",
        "projectPath": project_path,
        "manifestPath": manifest_path,
        "outputFolder": output_folder,
        "deleteGeneratedAssets": bool(request.delete_generated_assets),
        "preview": bool(request.preview_unity_write),
        "confirmRemove": True,
        "saveAssets": bool(request.save_assets),
    }
    preview = {
        "ok": True,
        "schema": AVATAR_ENCRYPTION_SCHEMA,
        "addonVersion": AVATAR_ENCRYPTION_ADDON_VERSION,
        "previewOnly": True,
        "readyToRequest": True,
        "targetTool": AVATAR_ENCRYPTION_ADDON_REMOVE_TOOL,
        "directApplyVisible": False,
        "requiresExplicitApproval": True,
        "checkpointRequired": True,
        "avatarPath": request.avatar_path or "",
        "manifestPath": manifest_path,
        "outputFolder": output_folder,
        "deleteGeneratedAssets": bool(request.delete_generated_assets),
        "rollback": {
            "checkpointRestoreTool": "vrcforge_restore_checkpoint",
            "checkpointStillAvailable": True,
        },
    }
    return AGENT_GATEWAY.create_apply_request(
        {
            "target_tool": AVATAR_ENCRYPTION_ADDON_REMOVE_TOOL,
            "arguments": arguments,
            "reason": "Request supervised Avatar Encryption remove/restore.",
            "preview": preview,
            "agent_name": agent_name,
            "requires_explicit_approval": True,
            "explicit_approval_reason": "Avatar Encryption remove restores renderer mesh/material references and may delete generated assets; explicit approval is required.",
        },
        internal_wrapper=True,
    )


def apply_avatar_encryption_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = dict(params or {})
    return call_avatar_encryption_addon("apply", params)


def remove_avatar_encryption_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = dict(params or {})
    return call_avatar_encryption_addon("remove", params)


def build_avatar_encryption_scan_payload(
    inventory: dict[str, Any],
    avatar_path: str | None,
    include_compatibility: bool,
) -> dict[str, Any]:
    materials = ensure_list_payload(inventory.get("materials") or [], "shader material inventory")
    targets = [build_avatar_encryption_target(material) for material in materials if isinstance(material, dict)]
    if not include_compatibility:
        targets = [target for target in targets if target["supportLevel"] == "first_class"]
    targets.sort(key=lambda item: (int(item.get("priority") or 99), str(item.get("materialName") or "")))
    compatibility_targets = [target for target in targets if target["supportLevel"] != "first_class"]
    first_class_targets = [target for target in targets if target["supportLevel"] == "first_class"]
    summary = {
        "materialCount": len(materials),
        "targetCount": len(targets),
        "candidateCount": sum(1 for target in targets if target["status"] == "candidate"),
        "lilToonCandidateCount": sum(1 for target in targets if target["shaderFamilyId"] == "liltoon" and target["status"] == "candidate"),
        "poiyomiCandidateCount": sum(1 for target in targets if target["shaderFamilyId"] == "poiyomi" and target["status"] == "candidate"),
        "compatibilityOnlyCount": len(compatibility_targets),
        "blockedCount": sum(1 for target in targets if target["status"] == "blocked"),
    }
    return {
        "ok": True,
        "schema": AVATAR_ENCRYPTION_SCHEMA,
        "addonVersion": AVATAR_ENCRYPTION_ADDON_VERSION,
        "phase": "M2-preview",
        "readOnly": True,
        "avatarPath": avatar_path or infer_avatar_path_from_inventory(inventory),
        "inventorySummary": inventory.get("summary") or {},
        "summary": summary,
        "firstClassTargets": first_class_targets,
        "compatibilityTargets": compatibility_targets,
        "targets": targets,
        "policy": {
            "primaryFamilies": ["lilToon", "Poiyomi"],
            "otherFamilies": "compatibility-only blocked preview",
            "applyAvailable": True,
            "applyMode": "dedicated_request_tool_only",
        },
    }


def build_avatar_encryption_target(material: dict[str, Any]) -> dict[str, Any]:
    family_id = normalize_avatar_encryption_shader_family(material.get("shader_family") or material.get("shader_name"))
    first_class = family_id in AVATAR_ENCRYPTION_PRIMARY_SHADER_FAMILIES
    blockers: list[str] = []
    warnings: list[str] = []
    if not first_class:
        blockers.append("shader_family.restore_adapter_missing")
    if family_id == "generic":
        warnings.append("Generic semantic shader support can report compatibility but has no restore fork.")
    if not str(material.get("renderer_path") or ""):
        warnings.append("Renderer path is missing; future apply would require a concrete renderer/material slot.")
    return {
        "materialId": str(material.get("material_id") or ""),
        "materialName": str(material.get("material_name") or ""),
        "rendererPath": str(material.get("renderer_path") or material.get("item_path") or ""),
        "rendererId": str(material.get("renderer_id") or ""),
        "slotIndex": material.get("slot_index", 0),
        "meshName": str(material.get("mesh_name") or ""),
        "shaderName": str(material.get("shader_name") or ""),
        "shaderFamily": avatar_encryption_shader_family_label(family_id),
        "shaderFamilyId": family_id,
        "priority": 1 if family_id == "liltoon" else 2 if family_id == "poiyomi" else 50,
        "supportLevel": "first_class" if first_class else "compatibility_only",
        "status": "candidate" if first_class else "blocked",
        "recommendedAdapter": f"{family_id}_restore_adapter" if first_class else "",
        "adapterDecision": shader_adapter_definition(family_id),
        "blockers": blockers,
        "warnings": warnings,
    }


def normalize_avatar_encryption_shader_family(value: Any) -> str:
    family = normalize_shader_family_id(value)
    return "generic" if family == "generic-semantic" else family


def avatar_encryption_shader_family_label(family_id: str) -> str:
    return "Generic" if family_id == "generic" else shader_family_label(family_id)


def normalize_avatar_encryption_target_families(values: list[str] | None) -> tuple[str, ...]:
    families = []
    requested_values = values if values else list(AVATAR_ENCRYPTION_PRIMARY_SHADER_FAMILIES)
    for value in requested_values:
        family = normalize_avatar_encryption_shader_family(value)
        if family not in families:
            families.append(family)
    return tuple(families or AVATAR_ENCRYPTION_PRIMARY_SHADER_FAMILIES)


def normalize_avatar_encryption_profile(value: Any) -> str:
    profile = str(value or AVATAR_ENCRYPTION_RECOMMENDED_PROFILE).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "low": "lite",
        "light": "lite",
        "balanced": "standard",
        "default": "standard",
        "normal": "standard",
        "high": "paranoid",
        "max": "paranoid",
        "maximum": "paranoid",
    }
    profile = aliases.get(profile, profile)
    return profile if profile in AVATAR_ENCRYPTION_PROFILES else AVATAR_ENCRYPTION_RECOMMENDED_PROFILE


def avatar_encryption_profile_definition(profile_id: str) -> dict[str, Any]:
    profile = copy.deepcopy(AVATAR_ENCRYPTION_PROFILES.get(profile_id) or AVATAR_ENCRYPTION_PROFILES[AVATAR_ENCRYPTION_RECOMMENDED_PROFILE])
    return profile


def avatar_encryption_request_profile(request: AvatarEncryptionPlanRequest) -> dict[str, Any]:
    profile_id = normalize_avatar_encryption_profile(request.protection_profile or request.profile)
    return avatar_encryption_profile_definition(profile_id)


def avatar_encryption_profile_cards() -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for profile_id in ("lite", "standard", "paranoid"):
        profile = avatar_encryption_profile_definition(profile_id)
        cards.append({
            "id": profile["id"],
            "icon": profile["icon"],
            "title": profile["uiTitle"],
            "label": profile["label"],
            "description": profile["uiDescription"],
            "recommended": bool(profile.get("recommended")),
            "cost": profile["gpuCost"],
            "deviceFit": profile["deviceFit"],
            "protection": profile["plainProtection"],
            "applyStatus": profile["applyStatus"],
        })
    return cards


def avatar_encryption_benchmark_table() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    baseline_fps = 90.0
    baseline_frame_ms = 1000.0 / baseline_fps
    for triangles in AVATAR_ENCRYPTION_BENCHMARK_TRIANGLES:
        scale = (triangles / 50_000.0) ** 0.85
        for profile_id in ("lite", "standard", "paranoid"):
            profile = AVATAR_ENCRYPTION_PROFILES[profile_id]
            impact_percent = round(float(profile["costWeight"]) * scale, 1)
            estimated_fps = round(baseline_fps * (1.0 - impact_percent / 100.0), 1)
            frame_ms = 1000.0 / max(estimated_fps, 1.0)
            rows.append({
                "profile": profile_id,
                "label": profile["label"],
                "triangles": triangles,
                "avatarScale": f"{triangles // 1000}k triangles",
                "baselineFps": int(baseline_fps),
                "estimatedFps": estimated_fps,
                "estimatedFpsLoss": round(baseline_fps - estimated_fps, 1),
                "estimatedFrameTimeAddedMs": round(frame_ms - baseline_frame_ms, 2),
                "estimatedImpactPercent": impact_percent,
                "gpuCost": profile["gpuCost"],
            })
    return rows


def filter_avatar_encryption_preview_candidates(candidates: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        family_id = normalize_avatar_encryption_shader_family(candidate.get("shaderFamilyId") or candidate.get("shaderFamily"))
        is_candidate = candidate.get("status") == "candidate"
        if is_candidate and family_id in AVATAR_ENCRYPTION_PRIMARY_SHADER_FAMILIES:
            eligible.append(candidate)
            continue
        blocked.append(
            {
                "materialId": candidate.get("materialId") or "",
                "rendererId": candidate.get("rendererId") or "",
                "rendererPath": candidate.get("rendererPath") or "",
                "slotIndex": candidate.get("slotIndex", 0),
                "materialName": candidate.get("materialName") or "",
                "shaderFamily": candidate.get("shaderFamily") or avatar_encryption_shader_family_label(family_id),
                "shaderFamilyId": family_id,
                "status": "blocked",
                "reason": "Only lilToon and Poiyomi first-class candidates may appear in writeTargetsPreview.",
            }
        )
    return eligible, blocked


def has_avatar_encryption_target_filter(request: AvatarEncryptionPlanRequest) -> bool:
    return bool(request.material_ids or request.renderer_paths or request.targets)


def filter_avatar_encryption_requested_targets(
    candidates: list[dict[str, Any]],
    request: AvatarEncryptionPlanRequest,
) -> list[dict[str, Any]]:
    material_ids = {str(value or "").strip() for value in request.material_ids}
    renderer_paths = {str(value or "").strip() for value in request.renderer_paths}
    renderer_ids: set[str] = set()
    for target in request.targets:
        if not isinstance(target, dict):
            continue
        material_id = str(target.get("materialId") or target.get("material_id") or "").strip()
        renderer_path = str(target.get("rendererPath") or target.get("renderer_path") or "").strip()
        renderer_id = str(target.get("rendererId") or target.get("renderer_id") or "").strip()
        if material_id:
            material_ids.add(material_id)
        if renderer_path:
            renderer_paths.add(renderer_path)
        if renderer_id:
            renderer_ids.add(renderer_id)

    material_ids.discard("")
    renderer_paths.discard("")
    renderer_ids.discard("")
    if not (material_ids or renderer_paths or renderer_ids):
        return candidates

    selected: list[dict[str, Any]] = []
    for candidate in candidates:
        material_id = str(candidate.get("materialId") or "").strip()
        renderer_path = str(candidate.get("rendererPath") or "").strip()
        renderer_id = str(candidate.get("rendererId") or "").strip()
        if material_id in material_ids or renderer_path in renderer_paths or renderer_id in renderer_ids:
            selected.append(candidate)
    return selected


def normalize_avatar_encryption_platform(value: Any) -> dict[str, Any]:
    platform = str(value or "pc").strip().lower()
    if platform in {"pc", "windows"}:
        return {"id": "pc", "label": "PC", "status": "supported"}
    if platform in {"quest", "android"}:
        return {
            "id": "quest_android",
            "label": "Quest/Android",
            "status": "blocked",
            "reason": "Avatar Encryption is Windows PC-only; Quest/Android requests are blocked for this feature.",
        }
    return {"id": platform or "unknown", "label": platform or "unknown", "status": "blocked", "reason": "Unknown platform."}


def avatar_encryption_proof_requirements() -> list[str]:
    return [
        "Disposable avatar proof is required before using this on a production avatar.",
        "Source assets are preserved; private addon output is checkpointed.",
        "Unity compile errors remain zero.",
        "Build/Test readiness or explainable blocker is recorded.",
        "Visual proof includes before, applied, removed, and rollback screenshots.",
        "Remove operation restores the original avatar state.",
        "Support bundles redact secrets and private project details.",
    ]


AVATAR_ENCRYPTION_OUTPUT_ROOT = "Assets/VRCForgeGenerated/AvatarEncryption"


def _safe_unity_asset_relative_path(value: str, *, label: str) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("/")
    path = PurePosixPath(text)
    parts = path.parts
    if (
        not parts
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or parts[0] != "Assets"
    ):
        raise AgentGatewayError(f"{label} must be a safe Unity asset-relative path under Assets/.", status_code=400)
    return path.as_posix()


def _normalize_avatar_encryption_managed_asset_path(
    value: str,
    *,
    project_path: str,
    label: str,
    default_value: str = "",
) -> str:
    raw = str(value or default_value or "").strip()
    if not raw:
        raise AgentGatewayError(f"{label} is required.", status_code=400)

    candidate = Path(raw)
    if candidate.is_absolute():
        project_text = str(project_path or "").strip()
        if not project_text:
            raise AgentGatewayError(f"{label} absolute paths require projectPath.", status_code=400)
        project_root = Path(project_text)
        if not project_root.is_absolute():
            raise AgentGatewayError(f"{label} absolute paths require an absolute projectPath.", status_code=400)
        try:
            relative = candidate.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError as exc:
            raise AgentGatewayError(f"{label} must stay inside projectPath.", status_code=400) from exc
    else:
        relative = raw

    normalized = _safe_unity_asset_relative_path(relative, label=label)
    allowed_root = PurePosixPath(AVATAR_ENCRYPTION_OUTPUT_ROOT).as_posix()
    if normalized != allowed_root and not normalized.startswith(allowed_root + "/"):
        raise AgentGatewayError(f"{label} must stay under {AVATAR_ENCRYPTION_OUTPUT_ROOT}.", status_code=400)
    return normalized


def normalize_avatar_encryption_output_folder(value: str, project_path: str) -> str:
    return _normalize_avatar_encryption_managed_asset_path(
        value,
        project_path=project_path,
        label="outputFolder",
        default_value=AVATAR_ENCRYPTION_OUTPUT_ROOT,
    )


def normalize_avatar_encryption_manifest_path(value: str | None, project_path: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    manifest_path = _normalize_avatar_encryption_managed_asset_path(
        raw,
        project_path=project_path,
        label="manifestPath",
    )
    if PurePosixPath(manifest_path).suffix.lower() != ".json":
        raise AgentGatewayError("manifestPath must point to a JSON manifest.", status_code=400)
    return manifest_path


def infer_avatar_path_from_inventory(inventory: dict[str, Any]) -> str:
    for material in ensure_list_payload(inventory.get("materials") or [], "shader material inventory"):
        if isinstance(material, dict) and material.get("avatar_path"):
            return str(material.get("avatar_path") or "")
    return ""


def generate_shader_material_plan_sync(request: ShaderMaterialPlanRequest) -> dict[str, Any]:
    try:
        if not (request.instruction or "").strip():
            raise RuntimeError("Shader tuning instruction is empty.")

        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or request.avatar or DASHBOARD_RUNTIME.current_avatar_path
        inventory = copy.deepcopy(request.inventory) if request.inventory else scan_shader_materials_direct(settings, avatar_path)
        inventory = apply_shader_category_overrides(inventory, request.category_overrides)
        reference_context = build_reference_image_context(request)
        reference_image_paths = [image["imagePath"] for image in (reference_context or {}).get("images", [])]
        reference_image_labels = [image["label"] for image in (reference_context or {}).get("images", [])]

        plan = create_material_tuning_plan(
            settings=settings,
            material_inventory=inventory,
            instruction=request.instruction or "",
            reference_image_paths=reference_image_paths,
            reference_image_labels=reference_image_labels,
        )
        locks = load_shader_tuning_locks(avatar_path)
        locked_materials = set(locks.get("lockedMaterials") or []) | set(request.locked_materials or [])
        locked_properties = set(locks.get("lockedProperties") or []) | set(request.locked_properties or [])
        validation = validate_shader_material_tuning_plan(
            plan=plan,
            inventory=inventory,
            locked_materials=locked_materials,
            locked_properties=locked_properties,
        )
        history_record = save_shader_tuning_history_record(
            request=request,
            settings=settings,
            avatar_path=avatar_path,
            inventory=inventory,
            plan=validation["plan"],
            reference_context=reference_context,
            locked_materials=sorted(locked_materials),
            locked_properties=sorted(locked_properties),
            applied=False,
        )
        emit_log(
            "success",
            "shader",
            "Shader material tuning plan generated.",
            {
                "avatarPath": avatar_path,
                "validChangeCount": len(validation["validatedChanges"]),
                "skippedChangeCount": len(validation["skippedChanges"]),
            },
        )
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "inventory": inventory,
            "plan": validation["plan"],
            "changePreview": validation["validatedChanges"],
            "validatedChanges": validation["validatedChanges"],
            "skippedChanges": validation["skippedChanges"],
            "warnings": validation["warnings"],
            "referenceImage": reference_context,
            "historyRecord": history_record,
            "lockedMaterials": sorted(locked_materials),
            "lockedProperties": sorted(locked_properties),
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "shader", "Failed to generate shader tuning plan.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def _prepare_shader_tuning_apply_state(request: ShaderMaterialApplyRequest) -> dict[str, Any]:
    """Read current shader facts and derive the one Core call an approval may bind.

    Deliberately ignores request.inventory: that object is display/planning input
    and cannot authorize a later material write.
    """
    settings = load_dashboard_settings(request)
    avatar_path = request.avatar_path or request.avatar or DASHBOARD_RUNTIME.current_avatar_path
    if not avatar_path:
        raise RuntimeError("The exact avatar path must be known before approval.")
    if not request.changes:
        raise RuntimeError("No shader material changes were provided.")
    inventory = apply_shader_category_overrides(
        scan_shader_materials_direct(settings, avatar_path), request.category_overrides
    )
    stored_locks = load_shader_tuning_locks(avatar_path)
    locked_materials = set(stored_locks.get("lockedMaterials") or []) | set(request.locked_materials or [])
    locked_properties = set(stored_locks.get("lockedProperties") or []) | set(request.locked_properties or [])
    validation = validate_shader_material_tuning_plan(
        plan={"type": "material_tuning_plan", "version": "0.2", "changes": request.changes, "warnings": []},
        inventory=inventory,
        locked_materials=locked_materials,
        locked_properties=locked_properties,
    )
    changes = validation["validatedChanges"]
    if not changes:
        raise RuntimeError("No valid shader material changes remained after validation.")
    core_arguments = {"avatarPath": avatar_path, "changes": changes, "saveAssets": True}
    return {
        "settings": settings,
        "avatarPath": avatar_path,
        "coreArguments": core_arguments,
        "validatedChanges": changes,
        "skippedChanges": validation["skippedChanges"],
        "warnings": validation["warnings"],
        "effectiveLocks": {
            "lockedMaterials": sorted(locked_materials),
            "lockedProperties": sorted(locked_properties),
        },
    }


def prepare_shader_material_apply_request(
    arguments: dict[str, Any], preview: Any,
) -> tuple[dict[str, Any], Any]:
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
        raise RuntimeError("Caller may not provide the reserved prepared Unity execution key.")
    request = ShaderMaterialApplyRequest(**arguments)
    state = _prepare_shader_tuning_apply_state(request)
    evidence = {
        "avatarPath": state["avatarPath"],
        "coreArgumentsSha256": shader_evidence_sha256(state["coreArguments"]),
        "effectiveLocks": state["effectiveLocks"],
        "historyId": request.history_id or "",
    }
    prepared = install_prepared_calls(
        arguments,
        [("vrc_apply_material_tuning", state["coreArguments"])],
        evidence,
    )
    return prepared, {
        "ok": True,
        "targetTool": "vrcforge_apply_shader_tuning",
        "avatarPath": state["avatarPath"],
        "changeCount": len(state["validatedChanges"]),
        "skippedChanges": state["skippedChanges"],
        "warnings": state["warnings"],
    }


def apply_shader_material_plan_approved_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute only the approval-sealed shader call after a fresh fact scan."""
    try:
        request = ShaderMaterialApplyRequest(**arguments)
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict):
            raise RuntimeError("Prepared shader evidence is invalid.")
        state = _prepare_shader_tuning_apply_state(request)
        if evidence.get("avatarPath") != state["avatarPath"]:
            raise RuntimeError("Prepared shader avatar selection drifted after approval.")
        require_exact_shader_evidence(evidence.get("effectiveLocks"), state["effectiveLocks"], "locks")
        if evidence.get("coreArgumentsSha256") != shader_evidence_sha256(state["coreArguments"]):
            raise RuntimeError("Prepared shader validated Core arguments drifted after approval.")
        if evidence.get("historyId") != (request.history_id or ""):
            raise RuntimeError("Prepared shader history target drifted after approval.")
        tool_name, tool_arguments = prepared_call(arguments)
        if tool_name != "vrc_apply_material_tuning":
            raise RuntimeError("Prepared shader Core call is invalid.")
        require_exact_shader_evidence(tool_arguments, state["coreArguments"], "Core arguments")
        result = apply_shader_material_tuning_direct(state["settings"], state["avatarPath"], tool_arguments["changes"])
        if result.get("ok") is False:
            raise RuntimeError(result.get("error") or "Shader material apply was rejected by Unity Core.")
        applied = require_shader_apply_readback(result, tool_arguments["changes"])
        skipped = [*state["skippedChanges"], *list(result.get("skipped") or [])]
        backup_changes = build_shader_restore_changes(applied)
        if backup_changes:
            with SHADER_UNDO_LOCK:
                DASHBOARD_RUNTIME.shader_undo_stack.setdefault(state["avatarPath"], []).append(backup_changes)
                undo_depth = len(DASHBOARD_RUNTIME.shader_undo_stack.get(state["avatarPath"], []))
        else:
            with SHADER_UNDO_LOCK:
                undo_depth = len(DASHBOARD_RUNTIME.shader_undo_stack.get(state["avatarPath"], []))
        committed_warning = ""
        if request.history_id:
            try:
                mark_shader_tuning_history_applied(request.history_id)
            except Exception as exc:  # Core already committed; do not make retry appear safe.
                committed_warning = f"Unity changes committed, but history metadata was not updated: {exc}"
        response = {
            "ok": True,
            "avatarPath": state["avatarPath"],
            "result": result,
            "appliedChanges": applied,
            "skippedChanges": skipped,
            "warnings": state["warnings"],
            "undoDepth": undo_depth,
        }
        if committed_warning:
            response.update({"committed": True, "committedWithWarning": True, "warning": committed_warning})
        return response
    except (RuntimeError, UnityMcpError, ValueError) as exc:
        emit_log("error", "shader", "Failed to apply shader material tuning.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def prepare_shader_material_restore_request(
    arguments: dict[str, Any], preview: Any,
) -> tuple[dict[str, Any], Any]:
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
        raise RuntimeError("Caller may not provide the reserved prepared Unity execution key.")
    request = ShaderMaterialRestoreRequest(**arguments)
    avatar_path = (request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path or "").strip()
    if not avatar_path:
        raise RuntimeError("avatar_path is required for shader restore.")
    with SHADER_UNDO_LOCK:
        stack = DASHBOARD_RUNTIME.shader_undo_stack.get(avatar_path) or []
        if not stack:
            raise RuntimeError("No shader material restore point is available.")
        restore_changes = copy.deepcopy(stack[-1])
        evidence = {
            "avatarPath": avatar_path,
            "undoDepth": len(stack),
            "undoSha256": shader_evidence_sha256(restore_changes),
        }
    prepared = install_prepared_calls(
        arguments,
        [("vrc_apply_material_tuning", {"avatarPath": avatar_path, "changes": restore_changes, "saveAssets": True})],
        evidence,
    )
    return prepared, {"ok": True, "targetTool": "vrcforge_restore_shader_tuning", "avatarPath": avatar_path, "restoreCount": len(restore_changes)}


def restore_shader_material_plan_approved_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        request = ShaderMaterialRestoreRequest(**arguments)
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict):
            raise RuntimeError("Prepared shader restore evidence is invalid.")
        avatar_path = (request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path or "").strip()
        if avatar_path != evidence.get("avatarPath"):
            raise RuntimeError("Prepared shader restore avatar drifted after approval.")
        with SHADER_UNDO_LOCK:
            stack = DASHBOARD_RUNTIME.shader_undo_stack.get(avatar_path) or []
            if not stack:
                raise RuntimeError("No shader material restore point is available.")
            restore_changes = copy.deepcopy(stack[-1])
            if evidence.get("undoDepth") != len(stack):
                raise RuntimeError("Prepared shader restore stack depth drifted after approval.")
            if evidence.get("undoSha256") != shader_evidence_sha256(restore_changes):
                raise RuntimeError("Prepared shader restore stack drifted after approval.")
            tool_name, tool_arguments = prepared_call(arguments)
            expected = {"avatarPath": avatar_path, "changes": restore_changes, "saveAssets": True}
            if tool_name != "vrc_apply_material_tuning":
                raise RuntimeError("Prepared shader restore Core call is invalid.")
            require_exact_shader_evidence(tool_arguments, expected, "restore Core arguments")
            settings = load_dashboard_settings(request)
            result = apply_shader_material_tuning_direct(settings, avatar_path, restore_changes)
            if result.get("ok") is False:
                raise RuntimeError(result.get("error") or "Shader material restore was rejected by Unity Core.")
            applied = require_shader_apply_readback(result, restore_changes)
            skipped = list(result.get("skipped") or [])
            # A failed Core call leaves the undo point intact.  Only success consumes it.
            stack.pop()
            undo_depth = len(stack)
        return {"ok": True, "avatarPath": avatar_path, "result": result, "restoredChanges": applied, "skippedChanges": skipped, "undoDepth": undo_depth}
    except (RuntimeError, UnityMcpError, ValueError) as exc:
        emit_log("error", "shader", "Failed to restore shader material tuning.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def build_shader_restore_changes(applied_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    restore: list[dict[str, Any]] = []
    for change in applied_changes:
        if not isinstance(change, dict):
            continue
        material_id = str(change.get("material_id") or "")
        semantic = str(change.get("semantic_property") or "")
        if not material_id or not semantic or "before" not in change:
            continue
        restore.append(
            {
                "material_id": material_id,
                "material_name": change.get("material_name") or "",
                "semantic_property": semantic,
                "after": change.get("before"),
                "reason": "Restore previous material value.",
            }
        )
    return restore


def normalize_shader_applied_changes(result: dict[str, Any], requested_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_applied = result.get("applied") or result.get("appliedChanges") or []
    if isinstance(raw_applied, list):
        return [item for item in raw_applied if isinstance(item, dict)]
    return []


def require_shader_apply_readback(
    result: dict[str, Any], requested_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Require one exact Core result row for every approved shader change."""
    applied = normalize_shader_applied_changes(result, requested_changes)
    if len(applied) != len(requested_changes):
        raise RuntimeError("Unity Core reported a partial shader material write; checkpoint recovery is required.")
    for index, (expected, actual) in enumerate(zip(requested_changes, applied, strict=True)):
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            raise RuntimeError(f"Unity Core returned an invalid shader readback row at index {index}.")
        expected_identity = {
            "material_id": expected.get("material_id", expected.get("materialId")),
            "semantic_property": expected.get("semantic_property", expected.get("semanticProperty")),
            "after": expected.get("after", expected.get("target", expected.get("value"))),
        }
        actual_identity = {
            "material_id": actual.get("material_id", actual.get("materialId")),
            "semantic_property": actual.get("semantic_property", actual.get("semanticProperty")),
            "after": actual.get("after", actual.get("target", actual.get("value"))),
        }
        require_exact_shader_evidence(expected_identity, actual_identity, f"Core readback row {index}")
        if "before" in expected:
            require_exact_shader_evidence(expected.get("before"), actual.get("before"), f"Core before-value row {index}")
    return applied


def save_shader_tuning_history_record(
    request: ShaderMaterialPlanRequest,
    settings: Settings,
    avatar_path: str | None,
    inventory: dict[str, Any],
    plan: dict[str, Any],
    reference_context: dict[str, Any] | None,
    locked_materials: list[str],
    locked_properties: list[str],
    applied: bool,
) -> dict[str, Any]:
    materials = inventory.get("materials") or []
    avatar_name = ""
    if materials and isinstance(materials[0], dict):
        avatar_name = str(materials[0].get("avatar_name") or "")
    record = {
        "id": make_tuning_id("shader_hist"),
        "created_at": tuning_timestamp(),
        "avatar_name": avatar_name,
        "avatar_path": avatar_path or "",
        "user_instruction": request.instruction or "",
        "provider": provider_display_name(settings.llm_provider),
        "model": settings.llm_model,
        "reference_image_count": len((reference_context or {}).get("images", []) or []),
        "changes": list(plan.get("changes") or []),
        "warnings": list(plan.get("warnings") or []),
        "visual_analysis": plan.get("visual_analysis") or {},
        "applied": applied,
        "locked_materials": locked_materials,
        "locked_properties": locked_properties,
    }
    store = load_shader_tuning_history_store()
    records = list(store.get("records") or [])
    records.append(record)
    store["records"] = records[-100:]
    save_tuning_store(SHADER_TUNING_HISTORY_PATH, store)
    return record


def mark_shader_tuning_history_applied(history_id: str) -> None:
    store = load_shader_tuning_history_store()
    records = list(store.get("records") or [])
    for record in records:
        if record.get("id") == history_id:
            record["applied"] = True
            record["last_applied_at"] = tuning_timestamp()
            break
    store["records"] = records
    save_tuning_store(SHADER_TUNING_HISTORY_PATH, store)


def _find_saved_shader_record(store: dict[str, Any], collection: str, record_id: str, label: str) -> dict[str, Any]:
    record = next(
        (item for item in store.get(collection) or [] if isinstance(item, dict) and item.get("id") == record_id),
        None,
    )
    if not isinstance(record, dict):
        raise RuntimeError(f"Saved shader {label} was not found: {record_id}")
    return copy.deepcopy(record)


def _saved_shader_source_from_arguments(arguments: dict[str, Any], source_type: str) -> tuple[str, dict[str, Any]]:
    if source_type == "history":
        source_id = str(arguments.get("historyId", arguments.get("history_id", "")) or "").strip()
        if not source_id:
            raise RuntimeError("historyId is required for saved shader history reapply.")
        return source_id, _find_saved_shader_record(load_shader_tuning_history_store(), "records", source_id, "history record")
    source_id = str(arguments.get("presetId", arguments.get("preset_id", "")) or "").strip()
    if not source_id:
        raise RuntimeError("presetId is required for saved shader preset apply.")
    return source_id, _find_saved_shader_record(load_shader_tuning_preset_store(), "presets", source_id, "preset")


def _prepare_saved_shader_tuning_state(
    request: ShaderMaterialPlanRequest,
    saved_payload: dict[str, Any],
    source_type: str,
) -> dict[str, Any]:
    """Revalidate a saved replay against current Unity facts without replanning."""
    settings = load_dashboard_settings(request)
    avatar_path = (
        request.avatar_path
        or saved_payload.get("avatar_path")
        or request.avatar
        or DASHBOARD_RUNTIME.current_avatar_path
        or ""
    ).strip()
    if not avatar_path:
        raise RuntimeError("The exact avatar path must be known before approval.")
    replay_changes = [
        {
            **change,
            "after": change.get("after"),
            "reason": change.get("reason") or f"Reapply saved shader {source_type}.",
        }
        for change in saved_payload.get("changes") or []
        if isinstance(change, dict) and "after" in change
    ]
    if not replay_changes:
        raise RuntimeError("Saved shader record contains no replayable changes.")
    inventory = apply_shader_category_overrides(
        scan_shader_materials_direct(settings, avatar_path), request.category_overrides
    )
    stored_locks = load_shader_tuning_locks(avatar_path)
    locked_materials = set(stored_locks.get("lockedMaterials") or []) | set(request.locked_materials or [])
    locked_properties = set(stored_locks.get("lockedProperties") or []) | set(request.locked_properties or [])
    validation = validate_shader_material_tuning_plan(
        plan={"type": "material_tuning_plan", "version": "0.2", "changes": replay_changes, "warnings": []},
        inventory=inventory,
        locked_materials=locked_materials,
        locked_properties=locked_properties,
    )
    validated_changes = validation["validatedChanges"]
    if not validated_changes:
        raise RuntimeError("No valid shader material changes remain after saved-record validation.")
    core_arguments = {"avatarPath": avatar_path, "changes": validated_changes, "saveAssets": True}
    return {
        "settings": settings,
        "avatarPath": avatar_path,
        "coreArguments": core_arguments,
        "validatedChanges": validated_changes,
        "skippedChanges": validation["skippedChanges"],
        "warnings": validation["warnings"],
        "effectiveLocks": {
            "lockedMaterials": sorted(locked_materials),
            "lockedProperties": sorted(locked_properties),
        },
        "restoreSnapshot": build_shader_restore_changes(validated_changes),
    }


def _prepare_saved_shader_tuning_request(
    arguments: dict[str, Any], preview: Any, source_type: str,
) -> tuple[dict[str, Any], Any]:
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
        raise RuntimeError("Caller may not provide the reserved prepared Unity execution key.")
    request = ShaderMaterialPlanRequest(**arguments)
    source_id, saved_payload = _saved_shader_source_from_arguments(arguments, source_type)
    state = _prepare_saved_shader_tuning_state(request, saved_payload, source_type)
    linked_history_id = str(saved_payload.get("source_history_id") or "") if source_type == "preset" else source_id
    linked_history_digest = ""
    if linked_history_id:
        linked_history = _find_saved_shader_record(load_shader_tuning_history_store(), "records", linked_history_id, "history record")
        linked_history_digest = shader_evidence_sha256(linked_history)
    evidence = {
        "sourceType": source_type,
        "sourceId": source_id,
        "sourceSha256": shader_evidence_sha256(saved_payload),
        "linkedHistoryId": linked_history_id,
        "linkedHistorySha256": linked_history_digest,
        "avatarPath": state["avatarPath"],
        "effectiveLocks": state["effectiveLocks"],
        "coreArgumentsSha256": shader_evidence_sha256(state["coreArguments"]),
        "restoreSnapshot": state["restoreSnapshot"],
    }
    prepared = install_prepared_calls(
        arguments,
        [("vrc_apply_material_tuning", state["coreArguments"])],
        evidence,
    )
    return prepared, {
        "ok": True,
        "targetTool": "vrcforge_reapply_shader_tuning_history" if source_type == "history" else "vrcforge_apply_shader_tuning_preset",
        "avatarPath": state["avatarPath"],
        "changeCount": len(state["validatedChanges"]),
        "skippedChanges": state["skippedChanges"],
        "warnings": state["warnings"],
    }


def prepare_reapply_shader_tuning_history_request(arguments: dict[str, Any], preview: Any) -> tuple[dict[str, Any], Any]:
    return _prepare_saved_shader_tuning_request(arguments, preview, "history")


def prepare_apply_shader_tuning_preset_request(arguments: dict[str, Any], preview: Any) -> tuple[dict[str, Any], Any]:
    return _prepare_saved_shader_tuning_request(arguments, preview, "preset")


def _mark_saved_shader_metadata(source_type: str, source_id: str, linked_history_id: str) -> str:
    """Perform metadata only after Core commit; failures are a non-retry warning."""
    try:
        if linked_history_id:
            mark_shader_tuning_history_applied(linked_history_id)
        if source_type == "preset":
            mark_shader_tuning_preset_applied(source_id)
        return ""
    except Exception as exc:  # The Core write has already committed.
        return f"Unity changes committed, but saved shader metadata was not updated: {exc}"


def _apply_saved_shader_tuning_approved_sync(arguments: dict[str, Any], source_type: str) -> dict[str, Any]:
    try:
        request = ShaderMaterialPlanRequest(**arguments)
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict) or evidence.get("sourceType") != source_type:
            raise RuntimeError("Prepared saved shader evidence is invalid.")
        source_id, saved_payload = _saved_shader_source_from_arguments(arguments, source_type)
        if source_id != evidence.get("sourceId") or shader_evidence_sha256(saved_payload) != evidence.get("sourceSha256"):
            raise RuntimeError("Prepared saved shader source record drifted after approval.")
        linked_history_id = str(evidence.get("linkedHistoryId") or "")
        if linked_history_id:
            live_history = _find_saved_shader_record(load_shader_tuning_history_store(), "records", linked_history_id, "history record")
            if shader_evidence_sha256(live_history) != evidence.get("linkedHistorySha256"):
                raise RuntimeError("Prepared saved shader history record drifted after approval.")
        state = _prepare_saved_shader_tuning_state(request, saved_payload, source_type)
        if state["avatarPath"] != evidence.get("avatarPath"):
            raise RuntimeError("Prepared saved shader avatar selection drifted after approval.")
        require_exact_shader_evidence(evidence.get("effectiveLocks"), state["effectiveLocks"], "saved replay locks")
        if evidence.get("coreArgumentsSha256") != shader_evidence_sha256(state["coreArguments"]):
            raise RuntimeError("Prepared saved shader validated Core arguments drifted after approval.")
        if evidence.get("restoreSnapshot") != state["restoreSnapshot"]:
            raise RuntimeError("Prepared saved shader restore snapshot drifted after approval.")
        tool_name, tool_arguments = prepared_call(arguments)
        if tool_name != "vrc_apply_material_tuning":
            raise RuntimeError("Prepared saved shader Core call is invalid.")
        require_exact_shader_evidence(tool_arguments, state["coreArguments"], "saved replay Core arguments")
        result = apply_shader_material_tuning_direct(state["settings"], state["avatarPath"], tool_arguments["changes"])
        if result.get("ok") is False:
            raise RuntimeError(result.get("error") or "Saved shader replay was rejected by Unity Core.")
        applied = require_shader_apply_readback(result, tool_arguments["changes"])
        restore_changes = build_shader_restore_changes(applied)
        if restore_changes:
            with SHADER_UNDO_LOCK:
                DASHBOARD_RUNTIME.shader_undo_stack.setdefault(state["avatarPath"], []).append(restore_changes)
                undo_depth = len(DASHBOARD_RUNTIME.shader_undo_stack.get(state["avatarPath"], []))
        else:
            with SHADER_UNDO_LOCK:
                undo_depth = len(DASHBOARD_RUNTIME.shader_undo_stack.get(state["avatarPath"], []))
        metadata_warning = _mark_saved_shader_metadata(source_type, source_id, linked_history_id)
        response = {
            "ok": True,
            "sourceType": source_type,
            "avatarPath": state["avatarPath"],
            "result": result,
            "appliedChanges": applied,
            "skippedChanges": [*state["skippedChanges"], *list(result.get("skipped") or [])],
            "warnings": state["warnings"],
            "undoDepth": undo_depth,
        }
        if metadata_warning:
            response.update({"committed": True, "committedWithWarning": True, "warning": metadata_warning})
        return response
    except (RuntimeError, UnityMcpError, ValueError) as exc:
        emit_log("error", "shader", "Failed to apply saved shader tuning.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def reapply_shader_tuning_history_approved_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    return _apply_saved_shader_tuning_approved_sync(arguments, "history")


def apply_shader_tuning_preset_approved_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    return _apply_saved_shader_tuning_approved_sync(arguments, "preset")


def create_shader_tuning_preset_sync(request: ShaderTuningPresetCreateRequest) -> dict[str, Any]:
    history_store = load_shader_tuning_history_store()
    history = next((item for item in history_store.get("records") or [] if item.get("id") == request.history_id), None)
    if not history:
        raise to_http_exception(RuntimeError(f"Shader tuning history record was not found: {request.history_id}"))
    preset = {
        "id": make_tuning_id("shader_preset"),
        "name": request.name.strip(),
        "created_at": tuning_timestamp(),
        "avatar_name": history.get("avatar_name") or "",
        "avatar_path": history.get("avatar_path") or "",
        "source_history_id": history.get("id"),
        "user_instruction": history.get("user_instruction") or "",
        "provider": history.get("provider") or "",
        "model": history.get("model") or "",
        "tags": request.tags,
        "description": request.description,
        "apply_mode": "after_values",
        "changes": list(history.get("changes") or []),
        "warnings": list(history.get("warnings") or []),
    }
    store = load_shader_tuning_preset_store()
    presets = list(store.get("presets") or [])
    presets.append(preset)
    presets = trim_presets_for_avatar(presets, request.max_presets)
    store["presets"] = presets
    save_tuning_store(SHADER_TUNING_PRESETS_PATH, store)
    return {"ok": True, "preset": preset, "presets": presets}


def rename_shader_tuning_preset_sync(preset_id: str, request: ShaderTuningPresetRenameRequest) -> dict[str, Any]:
    store = load_shader_tuning_preset_store()
    presets = list(store.get("presets") or [])
    preset = None
    for item in presets:
        if item.get("id") == preset_id:
            item["name"] = request.name.strip()
            item["updated_at"] = tuning_timestamp()
            preset = item
            break
    if not preset:
        raise to_http_exception(RuntimeError(f"Shader tuning preset was not found: {preset_id}"))
    store["presets"] = presets
    save_tuning_store(SHADER_TUNING_PRESETS_PATH, store)
    return {"ok": True, "preset": preset, "presets": presets}


def duplicate_shader_tuning_preset_sync(preset_id: str, request: ShaderTuningPresetDuplicateRequest) -> dict[str, Any]:
    store = load_shader_tuning_preset_store()
    presets = list(store.get("presets") or [])
    source = next((item for item in presets if item.get("id") == preset_id), None)
    if not source:
        raise to_http_exception(RuntimeError(f"Shader tuning preset was not found: {preset_id}"))
    duplicate = copy.deepcopy(source)
    duplicate["id"] = make_tuning_id("shader_preset")
    duplicate["name"] = (request.name or f"{source.get('name') or 'shader_preset'}_copy").strip()
    duplicate["created_at"] = tuning_timestamp()
    duplicate.pop("last_applied_at", None)
    duplicate["apply_count"] = 0
    presets.append(duplicate)
    presets = trim_presets_for_avatar(presets, request.max_presets)
    store["presets"] = presets
    save_tuning_store(SHADER_TUNING_PRESETS_PATH, store)
    return {"ok": True, "preset": duplicate, "presets": presets}


def delete_shader_tuning_preset_sync(preset_id: str) -> dict[str, Any]:
    store = load_shader_tuning_preset_store()
    presets = [item for item in store.get("presets") or [] if item.get("id") != preset_id]
    store["presets"] = presets
    save_tuning_store(SHADER_TUNING_PRESETS_PATH, store)
    return {"ok": True, "presets": presets}


def mark_shader_tuning_preset_applied(preset_id: str) -> None:
    store = load_shader_tuning_preset_store()
    presets = list(store.get("presets") or [])
    for preset in presets:
        if preset.get("id") == preset_id:
            preset["last_applied_at"] = tuning_timestamp()
            preset["apply_count"] = int(preset.get("apply_count") or 0) + 1
            break
    store["presets"] = presets
    save_tuning_store(SHADER_TUNING_PRESETS_PATH, store)


def review_shader_material_vision_sync(request: ShaderVisionReviewRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        goal = (request.goal or request.instruction or "").strip()
        if not goal:
            raise RuntimeError("Shader vision review goal is empty.")

        before_paths = [str(resolve_reference_image_path_value(path)) for path in request.before_image_paths if path]
        after_paths = [str(resolve_reference_image_path_value(path)) for path in request.after_image_paths if path]
        if not before_paths and not after_paths:
            raise RuntimeError("Shader vision review needs at least one before or after screenshot.")

        review = create_shader_visual_review(
            settings=settings,
            goal=goal,
            before_image_paths=before_paths,
            after_image_paths=after_paths,
        )
        save_vision_audit_artifact(
            "shader_visual_review.json",
            {
                "goal": goal,
                "beforeImagePaths": before_paths,
                "afterImagePaths": after_paths,
                "review": review,
            },
        )
        emit_log(
            "success",
            "shader",
            "Shader vision review completed.",
            {"beforeCount": len(before_paths), "afterCount": len(after_paths), "improved": review.get("improved")},
        )
        return {
            "ok": True,
            "goal": goal,
            "beforeImagePaths": before_paths,
            "afterImagePaths": after_paths,
            "review": review,
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "shader", "Failed to run shader vision review.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def apply_shader_category_overrides(inventory: dict[str, Any], overrides: dict[str, str] | None) -> dict[str, Any]:
    valid_categories = {"skin", "eyes", "hair", "clothes", "accessory", "unknown"}
    ambiguous_material_ids = find_ambiguous_shader_material_ids(inventory)
    for material in inventory.get("materials") or []:
        if not isinstance(material, dict):
            continue
        material_id = str(material.get("material_id") or "")
        override = (overrides or {}).get(material_id)
        if material_id not in ambiguous_material_ids and override in valid_categories:
            material["category"] = override
    return inventory


def find_ambiguous_shader_material_ids(inventory: dict[str, Any]) -> set[str]:
    counts: dict[str, int] = {}
    ambiguous: set[str] = set()
    for material in inventory.get("materials") or []:
        if not isinstance(material, dict):
            continue
        material_id = str(material.get("material_id") or "").strip()
        if not material_id:
            continue
        counts[material_id] = counts.get(material_id, 0) + 1
        if material.get("material_id_ambiguous") is True:
            ambiguous.add(material_id)
    ambiguous.update(material_id for material_id, count in counts.items() if count > 1)
    return ambiguous


def build_shader_material_index(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    ambiguous_material_ids = find_ambiguous_shader_material_ids(inventory)
    for material in inventory.get("materials") or []:
        if not isinstance(material, dict):
            continue
        material_id = str(material.get("material_id") or "")
        if material_id and material_id not in ambiguous_material_ids:
            index[material_id] = material
    return index


def validate_shader_material_tuning_plan(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    locked_materials: set[str] | None = None,
    locked_properties: set[str] | None = None,
) -> dict[str, Any]:
    material_index = build_shader_material_index(inventory)
    ambiguous_material_ids = find_ambiguous_shader_material_ids(inventory)
    locked_materials = locked_materials or set()
    locked_properties = locked_properties or set()
    warnings = list(plan.get("warnings") or [])
    validated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for raw_change in plan.get("changes") or []:
        if not isinstance(raw_change, dict):
            skipped.append({"validation_status": "skipped", "warning": "Change is not a JSON object.", "change": raw_change})
            continue

        change = dict(raw_change)
        material_id = str(change.get("material_id") or "")
        semantic = str(change.get("semantic_property") or "").strip()
        skip_reason = ""

        if any(key in change for key in ("shader_property", "property_name", "real_property")):
            skip_reason = "Real shader property names are not accepted; use semantic_property only."
        elif material_id in ambiguous_material_ids:
            skip_reason = f"Ambiguous material_id: {material_id}"
        elif not material_id or material_id not in material_index:
            skip_reason = f"Unknown material_id: {material_id}"
        elif material_id in locked_materials:
            skip_reason = f"Material is locked: {material_id}"
        elif semantic in locked_properties or f"{material_id}::{semantic}" in locked_properties:
            skip_reason = f"Semantic property is locked: {semantic}"
        elif semantic not in MATERIAL_SEMANTIC_PROPERTIES:
            skip_reason = f"Unsupported semantic_property: {semantic}"

        material = material_index.get(material_id) or {}
        if not skip_reason:
            shader_family = str(material.get("shader_family") or "")
            if shader_family not in {"lilToon", "Poiyomi", "Generic"}:
                skip_reason = f"Unsupported shader family: {shader_family or 'Unknown'}"

        supported_properties = material.get("supported_properties") or {}
        if not skip_reason and semantic not in supported_properties:
            skip_reason = f"Material does not expose semantic_property: {semantic}"

        normalized_after: Any = None
        if not skip_reason:
            normalized_after, normalized_warning = normalize_shader_material_value(semantic, change.get("after"))
            if normalized_warning:
                skip_reason = normalized_warning

        if skip_reason:
            change["validation_status"] = "skipped"
            change["warning"] = skip_reason
            skipped.append(change)
            warnings.append(skip_reason)
            continue

        current_value = supported_properties.get(semantic, {}).get("value")
        change["material_name"] = change.get("material_name") or material.get("material_name") or ""
        change["shader_family"] = material.get("shader_family") or change.get("shader_family") or ""
        change["category"] = material.get("category") or change.get("category") or "unknown"
        change["before"] = current_value if current_value is not None else change.get("before")
        change["after"] = normalized_after
        change["validation_status"] = "valid"
        validated.append(change)

    normalized_plan = dict(plan)
    normalized_plan["warnings"] = dedupe_strings(warnings)
    normalized_plan["changes"] = validated
    normalized_plan["skipped_changes"] = skipped
    return {
        "plan": normalized_plan,
        "validatedChanges": validated,
        "skippedChanges": skipped,
        "warnings": normalized_plan["warnings"],
    }


def normalize_shader_material_value(semantic: str, value: Any) -> tuple[Any, str]:
    if semantic in MATERIAL_COLOR_PROPERTIES:
        text = str(value or "").strip()
        if not text:
            return None, f"Missing color value for {semantic}"
        if not text.startswith("#"):
            text = "#" + text
        digits = text[1:]
        if len(digits) not in {6, 8} or any(ch not in "0123456789abcdefABCDEF" for ch in digits):
            return None, f"Invalid color value for {semantic}: {value}"
        if len(digits) == 6:
            text = "#" + digits.upper() + "FF"
        else:
            text = "#" + digits.upper()
        return text, ""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"Invalid numeric value for {semantic}: {value}"
    if not math.isfinite(number):
        return None, f"Invalid numeric value for {semantic}: {value}"

    min_value, max_value = MATERIAL_NUMERIC_RANGES.get(semantic, (0.0, 1.0))
    return min(max(number, min_value), max_value), ""


def dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


_VISION_CAPTURE_RESERVED_ARGUMENTS = {
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    "outputPath", "output_path", "imagePath", "image_path", "captureScope", "capture_scope",
    "setRotation", "set_rotation", "restoreView", "restore_view", "pitch", "yaw", "roll",
    "statusOnly", "status_only", "preview",
}


def _reject_capture_reserved_arguments(arguments: dict[str, Any]) -> None:
    reserved = sorted(
        key
        for key in arguments
        if key in _VISION_CAPTURE_RESERVED_ARGUMENTS or key.startswith("_vrcforge_")
    )
    if reserved:
        raise RuntimeError("Screenshot capture arguments include reserved Core fields: " + ", ".join(reserved))


def _normalize_capture_angles(raw_angles: Any) -> list[str]:
    if not isinstance(raw_angles, list) or not raw_angles:
        raise RuntimeError("Screenshot capture angles must be a non-empty list.")
    normalized: list[str] = []
    for raw_angle in raw_angles:
        if not isinstance(raw_angle, str):
            raise RuntimeError("Screenshot capture angles must be strings.")
        angle = raw_angle.strip().lower()
        if angle not in _ANGLE_CAMERA_ROTATIONS:
            raise RuntimeError(f"Unsupported screenshot capture angle: {angle or '<empty>'}.")
        if angle not in normalized:
            normalized.append(angle)
    if len(normalized) > 4:
        raise RuntimeError("Screenshot capture supports at most four unique angles.")
    return normalized


def _vision_capture_output_path(file_name: str) -> Path:
    dashboard_root = DASHBOARD_ARTIFACTS_DIR.resolve()
    latest_dir = (dashboard_root / "latest").resolve()
    if latest_dir.parent != dashboard_root:
        raise RuntimeError("Dashboard latest artifact directory is outside the dashboard artifact root.")
    output_path = (latest_dir / file_name).resolve()
    if output_path.parent != latest_dir:
        raise RuntimeError("Screenshot output path is outside the dashboard artifact directory.")
    return output_path


def _scene_view_capture_call(
    request: VisionCaptureRequest | VisionCaptureMultiRequest,
    output_path: Path,
    *,
    angle: str | None = None,
) -> dict[str, Any]:
    pitch, yaw, roll = _ANGLE_CAMERA_ROTATIONS.get(angle or "", (0.0, 0.0, 0.0))
    return {
        "outputPath": str(output_path),
        "width": request.width,
        "height": request.height,
        "pitch": pitch,
        "yaw": yaw,
        "roll": roll,
        "setRotation": bool(angle),
        "restoreView": True,
        "avatarPath": request.avatar_path or "",
        "captureScope": "face" if angle else "avatar",
        "requirePlayMode": request.require_play_mode,
    }


def _prepared_capture_base(request: VisionCaptureRequest | VisionCaptureMultiRequest) -> dict[str, Any]:
    return {
        "settings_path": request.settings_path,
        "unity_host": request.unity_host,
        "unity_port": request.unity_port,
        "unity_instance": request.unity_instance,
        "project_path": request.project_path,
        "avatar_path": request.avatar_path,
        "width": request.width,
        "height": request.height,
        "require_play_mode": request.require_play_mode,
    }


def prepare_capture_screenshot_request(arguments: dict[str, Any], preview: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    _reject_capture_reserved_arguments(arguments)
    request = VisionCaptureRequest(**arguments)
    output_path = _vision_capture_output_path("vision_capture.png")
    call = _scene_view_capture_call(request, output_path)
    prepared = install_prepared_calls(
        _prepared_capture_base(request),
        [("vrc_capture_scene_view", call)],
        {"outputPaths": [str(output_path)], "captureKind": "single"},
    )
    return prepared, {"ok": True, "captureKind": "single", "outputPaths": [str(output_path)], "calls": [call]}


def prepare_capture_multi_screenshot_request(arguments: dict[str, Any], preview: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    _reject_capture_reserved_arguments(arguments)
    raw_angles = arguments.get("angles", list(_ANGLE_CAMERA_ROTATIONS))
    angles = _normalize_capture_angles(raw_angles)
    request = VisionCaptureMultiRequest(**{**arguments, "angles": angles})
    calls: list[tuple[str, dict[str, Any]]] = []
    output_paths: list[str] = []
    for angle in angles:
        output_path = _vision_capture_output_path(f"vision_{angle}.png")
        calls.append(("vrc_capture_scene_view", _scene_view_capture_call(request, output_path, angle=angle)))
        output_paths.append(str(output_path))
    prepared_base = _prepared_capture_base(request)
    prepared_base["angles"] = angles
    prepared = install_prepared_calls(
        prepared_base,
        calls,
        {"outputPaths": output_paths, "angles": angles, "captureKind": "multi"},
    )
    return prepared, {"ok": True, "captureKind": "multi", "angles": angles, "outputPaths": output_paths, "calls": [call for _, call in calls]}


def _execute_prepared_scene_view_capture(
    arguments: dict[str, Any],
    request: VisionCaptureRequest | VisionCaptureMultiRequest,
    expected_calls: list[tuple[str, dict[str, Any]]],
    angles: list[str],
) -> dict[str, Any]:
    settings = load_dashboard_settings(request)
    captures: list[dict[str, Any]] = []
    for index, (expected_tool, expected_arguments) in enumerate(expected_calls):
        tool_name, tool_arguments = prepared_call(arguments, index)
        if tool_name != expected_tool or tool_arguments != expected_arguments:
            raise RuntimeError("Prepared screenshot Core call drifted after approval.")
        result = invoke_unity_mcp(settings, tool_name, tool_arguments)
        payload = ensure_dict_payload(extract_tool_result_payload(result), "vision capture")
        image_path = str(payload.get("imagePath") or expected_arguments["outputPath"])
        approved_path = Path(expected_arguments["outputPath"]).resolve()
        returned_path = Path(image_path).resolve()
        if os.path.normcase(str(returned_path)) != os.path.normcase(str(approved_path)):
            raise RuntimeError("Unity returned a screenshot path outside the approved capture plan.")
        if not approved_path.is_file() or approved_path.stat().st_size <= 0:
            raise RuntimeError("Unity did not create the approved screenshot artifact.")
        image_path = str(approved_path)
        capture = {"imagePath": image_path, "imageUrl": to_artifact_url(image_path), "capture": payload}
        if angles:
            angle = angles[index]
            pitch, yaw, roll = _ANGLE_CAMERA_ROTATIONS[angle]
            capture.update({"angle": angle, "rotation": {"pitch": pitch, "yaw": yaw, "roll": roll}})
        captures.append(capture)
    if not captures:
        raise RuntimeError("Prepared screenshot plan contains no Core calls.")
    DASHBOARD_RUNTIME.latest_screenshot_path = captures[0]["imagePath"]
    DASHBOARD_RUNTIME.latest_screenshot_url = captures[0]["imageUrl"]
    return {"ok": True, "captures": captures}


def capture_avatar_screenshot_approved_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    request = VisionCaptureRequest(**arguments)
    expected = _scene_view_capture_call(request, _vision_capture_output_path("vision_capture.png"))
    payload = _execute_prepared_scene_view_capture(arguments, request, [("vrc_capture_scene_view", expected)], [])
    capture = payload["captures"][0]
    emit_log("success", "vision", "Screenshot captured through approval.", {"imagePath": capture["imagePath"]})
    return {"ok": True, **capture}


def capture_avatar_multi_screenshot_approved_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    request = VisionCaptureMultiRequest(**arguments)
    angles = _normalize_capture_angles(request.angles)
    expected_calls = [
        ("vrc_capture_scene_view", _scene_view_capture_call(request, _vision_capture_output_path(f"vision_{angle}.png"), angle=angle))
        for angle in angles
    ]
    payload = _execute_prepared_scene_view_capture(arguments, request, expected_calls, angles)
    emit_log("success", "vision", "Multi-angle screenshots captured through approval.", {"angles": angles, "count": len(payload["captures"])})
    return payload


def read_vision_capture_status_sync(request: VisionCaptureStatusRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        payload = capture_scene_view_status_direct(settings=settings, require_play_mode=request.require_play_mode)
        return {"ok": True, **payload}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "vision", "Failed to read capture status.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def audit_avatar_screenshot_sync(request: VisionAuditRequest) -> dict[str, Any]:
    try:
        image_path = request.image_path or DASHBOARD_RUNTIME.latest_screenshot_path
        if not image_path:
            raise RuntimeError("No screenshot is available yet. Capture a screenshot before running image analysis.")

        image_file = resolve_local_path(image_path)
        if not image_file.exists():
            raise RuntimeError(f"Screenshot file does not exist: {image_file}")

        api_config = PROVIDER_CONFIGURATION.serialize_api_config(include_secret=True)
        if api_config.get("provider") != "gemini":
            raise RuntimeError("Image analysis currently requires the dashboard provider to be set to Google AI Studio.")

        result = run_gemini_vision_audit(api_config, image_file)
        save_vision_audit_artifact("vision_audit.json", {"imagePath": str(image_file), "audit": result})
        emit_log("success", "vision", "Image analysis completed.", {"status": result.get("status")})
        return {
            "ok": True,
            "imagePath": str(image_file),
            "imageUrl": to_artifact_url(str(image_file)),
            "audit": result,
        }
    except RuntimeError as exc:
        emit_log("error", "vision", "Failed to run image analysis.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def apply_clothing_fx_approved_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized_arguments = dict(arguments)
    if "dry_run" not in normalized_arguments and "dryRun" in normalized_arguments:
        normalized_arguments["dry_run"] = normalized_arguments["dryRun"]
    request = ClothingApplyFxRequest(**normalized_arguments)
    if request.dry_run:
        try:
            return CLOTHING_FX_READ.preview_apply_clothing_fx(request)
        except RuntimeError as exc:
            raise to_http_exception(exc) from exc
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        items = request.items
        if not items:
            raise RuntimeError("No clothing items provided. Run /api/clothes/scan or /api/clothes/generate-fx first.")

        apply_payload = build_clothes_fx_apply_preview(avatar_path, items)

        payload = apply_clothing_fx_direct(settings, avatar_path, items)
        emit_log("success", "fx", "Clothing FX assets authored in Unity.", {"avatarPath": avatar_path, "itemCount": len(items)})
        return {"ok": True, "avatarPath": avatar_path, "dryRun": False, "applyPayload": apply_payload, "result": payload, "itemCount": len(items)}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "fx", "Failed to apply clothing FX.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def apply_parameter_optimization_sync(request: ParameterApplyOptimizationRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        suggestions = request.suggestions
        if not suggestions:
            raise RuntimeError("No optimization suggestions provided. Run /api/parameters/optimize first.")

        apply_payload = build_parameter_apply_optimization_preview(avatar_path, suggestions)
        diff = [
            {"name": s.get("name", ""), "from": s.get("currentType", "Int"), "to": s.get("suggestedType", "Bool")}
            for s in suggestions
        ]

        if request.dry_run:
            emit_log("info", "parameter", "Parameter optimization payload generated (dry-run).", {"avatarPath": avatar_path, "count": len(suggestions)})
            return {"ok": True, "avatarPath": avatar_path, "dryRun": True, "applyPayload": apply_payload, "diff": diff, "appliedCount": len(suggestions)}

        snapshot_payload = scan_avatar_parameters_direct(settings, avatar_path)
        snapshot_info = save_parameter_snapshot_payload(snapshot_payload, avatar_path)
        emit_log(
            "info",
            "parameter",
            "Parameter snapshot saved before optimization.",
            {"avatarPath": avatar_path, "snapshotPath": snapshot_info["snapshotPath"]},
        )

        payload = apply_parameter_optimization_direct(settings, avatar_path, suggestions)
        emit_log("success", "parameter", "Parameter optimization applied in Unity.", {"avatarPath": avatar_path, "count": len(suggestions)})
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "dryRun": False,
            "applyPayload": apply_payload,
            "diff": diff,
            "appliedCount": len(suggestions),
            "snapshotPath": snapshot_info["snapshotPath"],
            "snapshotUrl": snapshot_info["snapshotUrl"],
            "result": payload,
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "parameter", "Failed to apply parameter optimization.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def prepare_rollback_parameter_optimization_request(
    arguments: dict[str, Any], preview: Any,
) -> tuple[dict[str, Any], Any]:
    """Freeze one bounded parameter rollback before user approval."""
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
        raise ValueError("Caller may not provide the reserved prepared Unity execution key.")
    request = ParameterRollbackRequest(**arguments)
    snapshot_path = resolve_parameter_snapshot_path(request.snapshot_path)
    raw_snapshot = snapshot_path.read_bytes()
    try:
        snapshot_payload = json.loads(raw_snapshot.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Parameter snapshot is not valid JSON: {snapshot_path}") from exc
    if not isinstance(snapshot_payload, dict):
        raise RuntimeError(f"Parameter snapshot is not a JSON object: {snapshot_path}")
    raw_parameters = snapshot_payload.get("parameterNames") or snapshot_payload.get("parameters") or []
    if not isinstance(raw_parameters, list):
        raise RuntimeError(f"Parameter snapshot parameter names are invalid: {snapshot_path}")
    parameter_names: list[dict[str, Any]] = []
    for index, item in enumerate(raw_parameters):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"Parameter snapshot item {index} is not an object: {snapshot_path}"
            )
        name = item.get("name")
        value_type = item.get("valueType")
        default_value = item.get("defaultValue", 0.0)
        saved = item.get("saved", True)
        network_synced = item.get("networkSynced", True)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 256:
            raise RuntimeError(
                f"Parameter snapshot item {index} has an invalid name: {snapshot_path}"
            )
        if value_type not in {"Bool", "Int", "Float"}:
            raise RuntimeError(
                f"Parameter snapshot item {index} has an invalid valueType: {snapshot_path}"
            )
        if (
            isinstance(default_value, bool)
            or not isinstance(default_value, (int, float))
            or not math.isfinite(float(default_value))
        ):
            raise RuntimeError(
                f"Parameter snapshot item {index} has an invalid defaultValue: {snapshot_path}"
            )
        if not isinstance(saved, bool) or not isinstance(network_synced, bool):
            raise RuntimeError(
                f"Parameter snapshot item {index} has invalid flags: {snapshot_path}"
            )
        parameter_names.append(
            {
                "name": name.strip(),
                "valueType": value_type,
                "defaultValue": float(default_value),
                "saved": saved,
                "networkSynced": network_synced,
            }
        )
    avatar_path = request.avatar_path or snapshot_payload.get("avatarPath") or DASHBOARD_RUNTIME.current_avatar_path
    call = (
        "vrc_rollback_avatar_parameters",
        {"avatarPath": str(avatar_path or ""), "parameterNames": parameter_names},
    )
    prepared = dict(arguments)
    prepared["snapshot_path"] = str(snapshot_path)
    return install_prepared_calls(
        prepared,
        [call],
        {
            "snapshotPath": str(snapshot_path),
            "snapshotSha256": hashlib.sha256(raw_snapshot).hexdigest(),
            "snapshotParameterCount": snapshot_payload.get("parameterCount", 0),
        },
    ), preview


def rollback_parameter_optimization_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        request = ParameterRollbackRequest(**arguments)
        settings = load_dashboard_settings(request)
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict):
            raise RuntimeError("Prepared parameter rollback evidence is invalid.")
        expected_path = evidence.get("snapshotPath")
        expected_sha256 = evidence.get("snapshotSha256")
        if not isinstance(expected_path, str) or not isinstance(expected_sha256, str):
            raise RuntimeError("Prepared parameter rollback evidence is invalid.")
        snapshot_path = resolve_parameter_snapshot_path(expected_path)
        if str(snapshot_path) != expected_path:
            raise RuntimeError("Prepared parameter rollback snapshot path drifted.")
        raw_snapshot = snapshot_path.read_bytes()
        if hashlib.sha256(raw_snapshot).hexdigest() != expected_sha256:
            raise RuntimeError("Prepared parameter rollback snapshot drifted.")
        tool_name, tool_arguments = prepared_call(arguments)
        if tool_name != "vrc_rollback_avatar_parameters":
            raise RuntimeError("Prepared parameter rollback Core call is invalid.")
        apply_payload = build_tool_payload_preview(tool_name, tool_arguments)
        payload = ensure_dict_payload(
            extract_tool_result_payload(invoke_unity_mcp(settings, tool_name, tool_arguments)),
            "parameter rollback",
        )
        avatar_path = str(tool_arguments["avatarPath"])
        restored_count = payload.get("restoredCount", evidence.get("snapshotParameterCount", 0))
        emit_log(
            "success",
            "parameter",
            "Parameter snapshot restored in Unity.",
            {"avatarPath": avatar_path, "snapshotPath": str(snapshot_path), "restoredCount": restored_count},
        )
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "snapshotPath": str(snapshot_path),
            "snapshotUrl": to_artifact_url(str(snapshot_path)),
            "applyPayload": apply_payload,
            "restoredCount": restored_count,
            "result": payload,
        }
    except (RuntimeError, UnityMcpError, ValueError) as exc:
        emit_log("error", "parameter", "Failed to rollback parameter optimization.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


_ANGLE_CAMERA_ROTATIONS: dict[str, tuple[float, float, float]] = {
    "front":      (15.0,   0.0,  0.0),
    "side_left":  (10.0,  90.0,  0.0),
    "side_right": (10.0, -90.0,  0.0),
    "back":       (10.0, 180.0,  0.0),
}


def audit_avatar_multi_screenshot_sync(request: VisionAuditMultiRequest) -> dict[str, Any]:
    try:
        image_paths = request.image_paths
        if not image_paths:
            raise RuntimeError("No image paths provided for multi-image audit.")

        api_config = PROVIDER_CONFIGURATION.serialize_api_config(include_secret=True)
        if api_config.get("provider") != "gemini":
            raise RuntimeError("Image analysis currently requires the dashboard provider to be set to Google AI Studio.")

        results: list[dict[str, Any]] = []
        for path_str in image_paths:
            image_file = resolve_local_path(path_str)
            if not image_file.exists():
                results.append({"imagePath": path_str, "error": f"File not found: {image_file}"})
                continue
            audit = run_gemini_vision_audit(api_config, image_file)
            results.append({"imagePath": str(image_file), "imageUrl": to_artifact_url(str(image_file)), "audit": audit})

        overall_status = "clipping" if any(r.get("audit", {}).get("status") == "clipping" for r in results) else "pass"
        save_vision_audit_artifact("vision_audit_multi.json", {"overallStatus": overall_status, "results": results})
        emit_log("success", "vision", "Multi-image analysis completed.", {"imageCount": len(results), "overallStatus": overall_status})
        return {"ok": True, "overallStatus": overall_status, "results": results}
    except RuntimeError as exc:
        emit_log("error", "vision", "Failed to run multi-image analysis.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def _resolve_avatar_tuning_live_context(
    arguments: dict[str, Any],
    avatar_hint: str | None,
) -> AvatarTuningLiveContext:
    request = build_agent_dashboard_request(arguments)
    settings = load_dashboard_settings(request)
    export_payload, _export_source, using_mock_execute = load_dashboard_export_payload(
        settings,
        request,
    )
    selected_avatar = resolve_avatar_selection(
        export_payload,
        avatar_hint or request.avatar,
    )
    return AvatarTuningLiveContext(
        settings=settings,
        avatar_name=selected_avatar.avatar_name,
        avatar_path=selected_avatar.avatar_path,
        allowed_targets=build_allowed_blendshape_index(
            export_payload,
            selected_avatar.avatar_path,
        ),
        locked_blendshapes=AVATAR_TUNING_STORES.load_locked_blendshapes(
            selected_avatar.avatar_path
        ),
        using_mock_execute=using_mock_execute,
        selected_avatar=selected_avatar,
    )


def _prepare_face_tuning_state(arguments: dict[str, Any]) -> PreparedFaceTuningState:
    """Generate and validate a face plan before, never during, approval execution."""
    request = build_agent_dashboard_request(arguments)
    settings = load_dashboard_settings(request)
    export_payload, export_source, using_mock_execute = load_dashboard_export_payload(settings, request)
    selected_avatar = resolve_avatar_selection(export_payload, request.avatar)
    remember_loaded_avatar(selected_avatar.avatar_name, selected_avatar.avatar_path)
    locked_blendshapes = AVATAR_TUNING_STORES.load_locked_blendshapes(
        selected_avatar.avatar_path
    )
    planning_payload = filter_planning_payload_to_face_blendshapes(build_planning_payload(export_payload, selected_avatar))
    planning_payload = filter_planning_payload_locked_blendshapes(planning_payload, locked_blendshapes)
    if int((planning_payload.get("summary") or {}).get("blendshapeCount", 0) or 0) == 0:
        raise RuntimeError("No unlocked face-related Blendshapes are available for the selected avatar.")
    reference_context: dict[str, Any] | None = None
    if request.plan_json:
        plan = read_plan_json(resolve_local_path(request.plan_json))
    else:
        if not request.instruction:
            raise RuntimeError("instruction is required unless a local plan_json path is provided.")
        reference_context = build_reference_image_context(request)
        plan = create_blendshape_plan(
            settings,
            planning_payload,
            request.instruction,
            reference_image_paths=reference_context.get("imagePaths") if reference_context else None,
            reference_image_labels=reference_context.get("imageLabels") if reference_context else None,
        )
    min_confidence = request.min_confidence if request.min_confidence is not None else settings.min_confidence
    plan = validate_plan(plan, planning_payload, selected_avatar, min_confidence, request.allow_low_confidence)
    plan = filter_plan_locked_blendshapes(plan, locked_blendshapes)
    direct_adjustments = build_direct_blendshape_adjustments_from_plan(plan)
    if not direct_adjustments:
        raise RuntimeError("The prepared face-tuning plan contains no writable Blendshape adjustments.")
    change_preview = build_plan_change_preview(plan, export_payload, selected_avatar)
    return PreparedFaceTuningState(
        context=AvatarTuningLiveContext(
            settings=settings,
            avatar_name=selected_avatar.avatar_name,
            avatar_path=selected_avatar.avatar_path,
            allowed_targets=build_allowed_blendshape_index(
                export_payload,
                selected_avatar.avatar_path,
            ),
            locked_blendshapes=locked_blendshapes,
            using_mock_execute=using_mock_execute,
            selected_avatar=selected_avatar,
        ),
        plan=plan.model_dump(),
        direct_adjustments=direct_adjustments,
        change_preview=change_preview,
        undo_items=build_undo_items_from_change_preview(change_preview),
        reference_context=reference_context,
        preview=render_preview(selected_avatar, plan, export_source, using_mock_execute),
        apply_payload=render_apply_payload_json(selected_avatar, plan),
        export_source=export_source,
    )


def _avatar_tuning_face_adjustments(
    plan_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan = BlendshapePlan(**plan_payload)
    return plan.model_dump(), build_direct_blendshape_adjustments_from_plan(plan)


def _render_avatar_tuning_face_summary(
    _arguments: dict[str, Any],
    context: AvatarTuningLiveContext,
    _evidence: dict[str, Any],
    result: Any,
    normalized_plan: dict[str, Any],
) -> Any:
    return render_summary(
        context.selected_avatar,
        BlendshapePlan(**normalized_plan),
        result,
        False,
    )


def _save_avatar_tuning_face_artifacts(
    arguments: dict[str, Any],
    _context: AvatarTuningLiveContext,
    evidence: dict[str, Any],
    result: Any,
    normalized_plan: dict[str, Any],
    summary: Any,
) -> Any:
    request = build_agent_dashboard_request(arguments)
    if not request.save_artifacts:
        return None
    return save_dashboard_artifacts(
        BlendshapePlan(**normalized_plan),
        str(evidence.get("applyPayload") or ""),
        evidence.get("preview") or {},
        result,
        summary,
    )


def _save_avatar_tuning_face_history(
    arguments: dict[str, Any],
    context: AvatarTuningLiveContext,
    evidence: dict[str, Any],
    _result: Any,
    normalized_plan: dict[str, Any],
    _summary: Any,
    artifacts: Any,
) -> Any:
    request = build_agent_dashboard_request(arguments)
    return AVATAR_TUNING_STORES.save_history_record(
        build_tuning_history_record(
            request=request,
            settings=context.settings,
            selected_avatar=context.selected_avatar,
            plan=BlendshapePlan(**normalized_plan),
            change_preview=list(evidence.get("changePreview") or []),
            reference_context=evidence.get("referenceContext"),
            locked_blendshapes=list(evidence.get("lockedBlendshapes") or []),
            applied=True,
            visual_proof={
                "status": "unavailable",
                "reason": (
                    "Capture is deferred to a separately approved screenshot write."
                ),
            },
            artifacts=artifacts,
        )
    )




def _run_face_tuning_adapter(request: DashboardRequest, execute: bool) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        export_payload, export_source, using_mock_execute = load_dashboard_export_payload(settings, request)
        selected_avatar = resolve_avatar_selection(export_payload, request.avatar)
        remember_loaded_avatar(selected_avatar.avatar_name, selected_avatar.avatar_path)
        locked_blendshapes = AVATAR_TUNING_STORES.load_locked_blendshapes(
            selected_avatar.avatar_path
        )
        planning_payload = filter_planning_payload_to_face_blendshapes(
            build_planning_payload(export_payload, selected_avatar)
        )
        planning_payload = filter_planning_payload_locked_blendshapes(planning_payload, locked_blendshapes)
        face_blendshape_count = int((planning_payload.get("summary") or {}).get("blendshapeCount", 0) or 0)
        if face_blendshape_count == 0:
            if locked_blendshapes:
                raise RuntimeError("All available face-related Blendshapes are currently locked. Unlock at least one Blendshape before rerolling.")
            raise RuntimeError(
                "No face-related blendshapes were found for the selected avatar. "
                "The natural-language face editor only exposes eye, brow, mouth, jaw/face, nose, tongue, teeth, ear, and VRC viseme blendshapes."
            )

        emit_log(
            "info",
            "pipeline",
            "Pipeline started.",
            {
                "avatarPath": selected_avatar.avatar_path,
                "mode": "execute" if execute else "plan",
                "executionMode": "mock" if using_mock_execute else "live-unity",
                "source": export_source,
                "faceBlendshapeCount": face_blendshape_count,
                "lockedBlendshapeCount": len(locked_blendshapes),
            },
        )

        reference_context: dict[str, Any] | None = None
        if request.plan_json:
            plan = read_plan_json(resolve_local_path(request.plan_json))
            emit_log("info", "pipeline", "Loaded local plan JSON.", {"planJson": request.plan_json})
        else:
            if not request.instruction:
                raise RuntimeError("instruction is required unless a local plan_json path is provided.")
            reference_context = build_reference_image_context(request)
            plan = create_blendshape_plan(
                settings,
                planning_payload,
                request.instruction,
                reference_image_paths=reference_context.get("imagePaths") if reference_context else None,
                reference_image_labels=reference_context.get("imageLabels") if reference_context else None,
            )
            emit_log(
                "info",
                "pipeline",
                "LLM plan generated.",
                {
                    "instruction": request.instruction,
                    "provider": settings.llm_provider,
                    "model": settings.llm_model,
                    "referenceImageCount": reference_context.get("count") if reference_context else 0,
                },
            )

        min_confidence = request.min_confidence if request.min_confidence is not None else settings.min_confidence
        plan = validate_plan(
            plan=plan,
            export_payload=planning_payload,
            selected_avatar=selected_avatar,
            min_confidence=min_confidence,
            allow_low_confidence=request.allow_low_confidence,
        )
        plan = filter_plan_locked_blendshapes(plan, locked_blendshapes)

        for adjustment in plan.adjustments:
            emit_log(
                "info",
                "blendshape",
                f"{adjustment.blendshape_name} -> {adjustment.target_weight:.2f}",
                {
                    "avatarPath": adjustment.avatar_path,
                    "rendererPath": adjustment.renderer_path,
                    "confidence": adjustment.confidence,
                },
            )

        preview = render_preview(selected_avatar, plan, export_source, using_mock_execute)
        apply_payload_json = render_apply_payload_json(selected_avatar, plan)
        change_preview = build_plan_change_preview(plan, export_payload, selected_avatar)
        visual_proof: dict[str, Any] | None = None
        verified_changes: list[dict[str, Any]] = []

        result: McpResult | None = None
        summary: str | None = None
        if execute:
            emit_log("info", "pipeline", "Executing blendshape plan.", {"executionMode": "mock" if using_mock_execute else "live-unity"})
            if not plan.adjustments:
                emit_log("info", "pipeline", "Plan contains no blendshape adjustments; execution skipped.", {})
            elif using_mock_execute:
                result = mock_execute_payload(apply_payload_json, selected_avatar, export_source)
            else:
                visual_proof = {
                    "status": "unavailable",
                    "reason": "Capture is deferred to a separately approved screenshot write.",
                }
                direct_adjustments = build_direct_blendshape_adjustments_from_plan(plan)
                undo_items = build_undo_items_from_change_preview(change_preview)
                result = apply_blendshapes_direct(settings, selected_avatar.avatar_path, direct_adjustments)
                AVATAR_TUNING_UNDO.push(selected_avatar.avatar_path, undo_items)
                time.sleep(0.15)
                verified_changes = verify_live_blendshape_changes(
                    settings=settings,
                    selected_avatar=selected_avatar,
                    change_preview=change_preview,
                )
            summary = render_summary(selected_avatar, plan, result, using_mock_execute)
            emit_log(
                "success",
                "pipeline",
                "Pipeline execution finished.",
                {
                    "avatarPath": selected_avatar.avatar_path,
                    "adjustmentCount": len(plan.adjustments),
                    "executionMode": "mock" if using_mock_execute else "live-unity",
                },
            )
        else:
            emit_log("success", "pipeline", "Plan generated successfully.", {"adjustmentCount": len(plan.adjustments)})

        artifacts = None
        if request.save_artifacts:
            artifacts = save_dashboard_artifacts(plan, apply_payload_json, preview, result, summary)
            emit_log("info", "artifact", "Dashboard artifacts saved.", {"runDirectory": artifacts["runDirectory"]})

        history_record = AVATAR_TUNING_STORES.save_history_record(
            build_tuning_history_record(
                request=request,
                settings=settings,
                selected_avatar=selected_avatar,
                plan=plan,
                change_preview=change_preview,
                reference_context=reference_context,
                locked_blendshapes=locked_blendshapes,
                applied=execute,
                visual_proof=visual_proof,
                artifacts=artifacts,
            )
        )

        return {
            "exportSource": export_source,
            "executionMode": "mock" if using_mock_execute else "live-unity",
            "selectedAvatar": serialize_selected_avatar(selected_avatar),
            "availableAvatars": serialize_avatar_list(export_payload),
            "plan": plan.model_dump(),
            "changePreview": change_preview,
            "verifiedChanges": verified_changes,
            "visualProof": visual_proof,
            "referenceImage": reference_context,
            "preview": preview,
            "applyPayload": apply_payload_json,
            "result": serialize_result(result),
            "summary": summary,
            "artifacts": artifacts,
            "historyRecord": history_record,
            "lockedBlendshapes": locked_blendshapes,
            "undoDepth": len(DASHBOARD_RUNTIME.manual_undo_stack.get(selected_avatar.avatar_path, [])),
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "pipeline", "Pipeline failed.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def load_dashboard_settings(request: DashboardRequest | ConnectionRequest) -> Settings:
    settings_path = resolve_local_path(request.settings_path)
    settings = load_runtime_settings_safely(
        settings_path,
        getattr(request, "model", None),
        llm_override=PROVIDER_CONFIGURATION.serialize_api_config(include_secret=True),
        loader=load_settings,
    )

    settings.unity_mcp_host = request.unity_host or DASHBOARD_STATE.unity_host or settings.unity_mcp_host
    settings.unity_mcp_port = int(request.unity_port if request.unity_port is not None else DASHBOARD_STATE.unity_port or settings.unity_mcp_port)

    if request.unity_instance is not None:
        settings.unity_mcp_instance = request.unity_instance.strip()
    elif DASHBOARD_STATE.unity_instance:
        settings.unity_mcp_instance = DASHBOARD_STATE.unity_instance

    settings.unity_project_path = str(
        getattr(request, "project_path", None)
        or DASHBOARD_STATE.selected_project_path
        or settings.unity_project_path
        or ""
    ).strip()

    return settings


STREAMING_DIALOGUE_FIELDS = ("reply", "summary")


def extract_streaming_json_string_field(raw_json_fragment: str, field_name: str) -> str | None:
    marker = f'"{field_name}"'
    search_from = 0
    colon_index = -1
    while True:
        marker_index = raw_json_fragment.find(marker, search_from)
        if marker_index < 0:
            return None
        cursor = marker_index + len(marker)
        while cursor < len(raw_json_fragment) and raw_json_fragment[cursor].isspace():
            cursor += 1
        if cursor < len(raw_json_fragment) and raw_json_fragment[cursor] == ":":
            colon_index = cursor
            break
        search_from = marker_index + len(marker)
    quote_index = colon_index + 1
    while quote_index < len(raw_json_fragment) and raw_json_fragment[quote_index].isspace():
        quote_index += 1
    if quote_index >= len(raw_json_fragment) or raw_json_fragment[quote_index] != '"':
        return None

    output: list[str] = []
    escaped = False
    for char in raw_json_fragment[quote_index + 1 :]:
        if escaped:
            output.append({"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "/": "/"}.get(char, char))
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            break
        output.append(char)
    return "".join(output)


def extract_streaming_dialogue_text(raw_json_fragment: str) -> tuple[str, str]:
    for field_name in STREAMING_DIALOGUE_FIELDS:
        text = extract_streaming_json_string_field(raw_json_fragment, field_name)
        if text:
            return field_name, text
    return "", ""


def extract_streaming_reply_text(raw_json_fragment: str) -> str:
    _field_name, text = extract_streaming_dialogue_text(raw_json_fragment)
    return text


def _agent_gateway_llm_plan(prompt: str) -> dict[str, Any]:
    """LLM planner hook for the agent gateway (multi-provider dispatch).

    Raises when no API key is configured so the gateway falls back to the
    deterministic local planner.
    """
    settings = load_dashboard_settings(ConnectionRequest())
    if provider_requires_api_key(settings.llm_provider) and not settings.llm_api_key:
        raise RuntimeError("LLM API key is not configured; planner falls back to deterministic-local.")
    label_parts = [provider_display_name(settings.llm_provider), str(settings.llm_model or "").strip()]
    AGENT_GATEWAY.llm_planner_label = " · ".join(part for part in label_parts if part)
    AGENT_GATEWAY.llm_reasoning_trace = {}
    stream_state = {"raw": "", "field": "", "text": ""}

    def stream_callback(delta: str) -> None:
        stream_state["raw"] += delta
        field_name, text = extract_streaming_dialogue_text(stream_state["raw"])
        if not text:
            return
        if field_name != stream_state["field"]:
            if stream_state["field"] and stream_state["text"] and not text.startswith(stream_state["text"]):
                stream_state["field"] = field_name
                stream_state["text"] = text
                return
            stream_state["field"] = field_name
        if text == stream_state["text"]:
            return
        text_delta = text[len(stream_state["text"]) :]
        stream_state["text"] = text
        context = AGENT_GATEWAY.runtime_stream_context()
        client_turn_id = str(context.get("clientTurnId") or "").strip()
        if not client_turn_id:
            return
        EVENT_BUS.broadcast_from_sync(
            "agentRuntimeDelta",
            {
                "sessionId": context.get("sessionId") or "",
                "turnId": context.get("turnId") or "",
                "clientTurnId": client_turn_id,
                "textDelta": text_delta[:1000],
            },
        )

    response = request_llm_plan_with_metadata(settings, prompt, stream_callback=stream_callback)
    context = AGENT_GATEWAY.runtime_stream_context()
    if context.get("clientTurnId"):
        EVENT_BUS.broadcast_from_sync(
            "agentRuntimeDelta",
            {
                "sessionId": context.get("sessionId") or "",
                "turnId": context.get("turnId") or "",
                "clientTurnId": context.get("clientTurnId") or "",
                "done": True,
            },
        )
    reasoning = dict(response.reasoning or {})
    if int(reasoning.get("itemCount") or 0) > 0:
        AGENT_GATEWAY.llm_reasoning_trace = reasoning
    return {"text": response.text, "usage": dict(response.usage or {}), "reasoning": reasoning}


AGENT_GATEWAY.llm_plan_fn = _agent_gateway_llm_plan


def mcp_trigger_selection_config_binding(config: ProviderApiConfig) -> tuple[str, str, str, str]:
    """Freeze the nonsecret provider identity used by one acceptance receipt."""

    _requested_api_type, resolved_api_type = normalize_provider_api_type(
        config.provider, config.model, config.api_type
    )
    config_digest = hashlib.sha256(
        json.dumps(
            {
                "provider": config.provider,
                "model": config.model,
                "baseUrl": config.base_url,
                "apiType": config.api_type or "auto",
                "resolvedApiType": resolved_api_type,
                "thinkingLevel": config.thinking_level,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return config.provider, config.model, config_digest, resolved_api_type


def mcp_trigger_selection_planner(
    message: str,
    visible_tools: list[dict[str, Any]],
    exposure_layer: str = "planning",
) -> dict[str, Any]:
    """Call the configured provider once without entering the runtime execution loop."""

    config = PROVIDER_CONFIGURATION.current_api_config()
    if provider_requires_api_key(config.provider) and not config.api_key:
        raise RuntimeError("LLM API key is not configured for selection acceptance.")
    provider, model, config_digest, resolved_api_type = mcp_trigger_selection_config_binding(config)
    result = plan_mcp_tool_selection(
        message,
        visible_tools,
        provider=provider,
        model=model,
        request_text=lambda prompt: PROVIDER_TESTS.probe_text(config, prompt, structured=True),
    )
    result["providerEvidence"] = MCP_TRIGGER_SELECTION_RECEIPTS.issue(
        message,
        visible_tools,
        result,
        provider=provider,
        model=model,
        config_digest=config_digest,
        resolved_api_type=resolved_api_type,
        exposure_layer=exposure_layer,
    )
    return result


def verify_mcp_trigger_selection_receipt(
    message: str,
    visible_tools: list[dict[str, Any]],
    result: dict[str, Any],
    exposure_layer: str = "planning",
) -> bool:
    config = PROVIDER_CONFIGURATION.current_api_config()
    provider, model, config_digest, resolved_api_type = mcp_trigger_selection_config_binding(config)
    return MCP_TRIGGER_SELECTION_RECEIPTS.verify_and_consume(
        message,
        visible_tools,
        result,
        provider=provider,
        model=model,
        config_digest=config_digest,
        resolved_api_type=resolved_api_type,
        exposure_layer=exposure_layer,
    )


def _agent_gateway_context_compact(
    history: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Host compactor for safe runtime continuation boundaries."""

    settings = load_dashboard_settings(ConnectionRequest())
    summarizer: Callable[[str], Any] | None = None
    if not provider_requires_api_key(settings.llm_provider) or str(settings.llm_api_key or "").strip():
        summarizer = lambda prompt: request_llm_plan(settings, prompt)
    return compact_context(
        history,
        summarizer=summarizer,
        trigger="auto",
        phase="mid_turn",
        language=str(metadata.get("language") or ""),
        provider=settings.llm_provider,
        model=settings.llm_model,
        target_tokens=metadata.get("targetTokens"),
        real_context_limit=metadata.get("realContextLimit"),
    )


AGENT_GATEWAY.runtime_context_compact_fn = _agent_gateway_context_compact
AGENT_GATEWAY.vision_analyze_fn = PROVIDER_VISION.analyze


def load_dashboard_export_payload(
    settings: Settings,
    request: DashboardRequest,
) -> tuple[dict[str, Any], str, bool]:
    source_mode = request.source_mode
    export_json_path: Path | None = None
    skip_export = False
    mvp_mode = False

    if source_mode == "mvp_sample":
        mvp_mode = True
    elif source_mode == "configured_export":
        skip_export = True
    elif source_mode == "custom_export":
        if not request.export_json:
            raise RuntimeError("source_mode=custom_export requires an export_json path.")
        export_json_path = resolve_local_path(request.export_json)
    elif source_mode != "unity_live_export":
        raise RuntimeError(f"Unsupported source mode: {source_mode}")

    return load_export_payload(
        settings=settings,
        export_json_path=export_json_path,
        skip_export=skip_export,
        mvp_mode=mvp_mode,
        mock_execute=request.mock_execute,
    )


def extract_tool_result_payload(result: McpResult) -> Any:
    candidate: Any = result.payload
    if isinstance(candidate, dict):
        visited = set()
        while isinstance(candidate, dict):
            marker = id(candidate)
            if marker in visited:
                break
            visited.add(marker)

            if "structuredContent" in candidate and isinstance(candidate["structuredContent"], dict):
                candidate = candidate["structuredContent"]
                continue
            if "data" in candidate and isinstance(candidate["data"], dict):
                candidate = candidate["data"]
                continue
            if "result" in candidate:
                candidate = candidate["result"]
                continue
            if "payload" in candidate:
                candidate = candidate["payload"]
                continue
            if "value" in candidate:
                candidate = candidate["value"]
                continue
            break

        if isinstance(candidate, str):
            parsed = try_parse_json(candidate)
            return parsed if parsed is not None else candidate
        return candidate

    if isinstance(candidate, str):
        parsed = try_parse_json(candidate)
        return parsed if parsed is not None else candidate

    return candidate


def ensure_list_payload(payload: Any, scope: str) -> list[Any]:
    if not isinstance(payload, list):
        raise RuntimeError(f"Expected {scope} to return a JSON array, got: {type(payload).__name__}")
    return payload


def ensure_dict_payload(payload: Any, scope: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected {scope} to return a JSON object, got: {type(payload).__name__}")
    return payload


def build_dashboard_artifact_path(prefix: str, avatar_path: str | None, suffix: str) -> Path:
    latest_dir = DASHBOARD_ARTIFACTS_DIR / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    safe_avatar = sanitize_artifact_name(str(avatar_path or "avatar"))
    return latest_dir / f"{prefix}_{safe_avatar}.{suffix.lstrip('.')}"


def write_dashboard_json_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload.setdefault("jsonPath", str(path))


def serialize_avatar_list(export_payload: dict[str, Any]) -> list[dict[str, Any]]:
    avatars: list[dict[str, Any]] = []
    for avatar in export_payload.get("avatars") or []:
        renderers = avatar.get("renderers") or []
        blendshape_count = sum(len(renderer.get("blendshapes") or []) for renderer in renderers)
        avatars.append(
            {
                "avatarName": avatar.get("avatarName", "<unknown>"),
                "avatarPath": avatar.get("avatarPath", "<unknown path>"),
                "sceneName": avatar.get("sceneName", "<unknown scene>"),
                "rendererCount": len(renderers),
                "blendshapeCount": blendshape_count,
                "isVrChatAvatar": avatar.get("isVrChatAvatar", False),
            }
        )

    return avatars


def serialize_blendshape_details(export_payload: dict[str, Any], selected_avatar: SelectedAvatar) -> list[dict[str, Any]]:
    avatar_payload = next(
        avatar for avatar in export_payload.get("avatars") or [] if avatar.get("avatarPath") == selected_avatar.avatar_path
    )
    details: list[dict[str, Any]] = []
    for renderer in avatar_payload.get("renderers") or []:
        renderer_path = renderer.get("rendererPath", "")
        renderer_name = renderer.get("rendererName", "")
        mesh_name = renderer.get("meshName", "")
        for blendshape in renderer.get("blendshapes") or []:
            if not is_face_related_blendshape(renderer, blendshape):
                continue
            details.append(
                {
                    "avatarPath": selected_avatar.avatar_path,
                    "avatarName": selected_avatar.avatar_name,
                    "rendererName": renderer_name,
                    "rendererPath": renderer_path,
                    "meshName": mesh_name,
                    "blendshapeName": blendshape.get("name", ""),
                    "currentWeight": float(blendshape.get("currentWeight", 0.0) or 0.0),
                    "normalizedWeight": float(blendshape.get("normalizedWeight", 0.0) or 0.0),
                    "index": int(blendshape.get("index", 0) or 0),
                }
            )
    return details


def build_allowed_blendshape_index(
    export_payload: dict[str, Any],
    avatar_path: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    allowed: dict[tuple[str, str], dict[str, Any]] = {}
    for avatar in export_payload.get("avatars") or []:
        if avatar.get("avatarPath") != avatar_path:
            continue
        for renderer in avatar.get("renderers") or []:
            renderer_path = renderer.get("rendererPath", "")
            for blendshape in renderer.get("blendshapes") or []:
                allowed[(renderer_path, blendshape.get("name", ""))] = {
                    "currentWeight": float(blendshape.get("currentWeight", 0.0) or 0.0),
                }
    return allowed


def serialize_selected_avatar(selected_avatar: SelectedAvatar) -> dict[str, Any]:
    return {
        "avatarName": selected_avatar.avatar_name,
        "avatarPath": selected_avatar.avatar_path,
        "sceneName": selected_avatar.scene_name,
        "rendererCount": selected_avatar.renderer_count,
        "blendshapeCount": selected_avatar.blendshape_count,
    }


def serialize_result(result: McpResult | None) -> dict[str, Any] | None:
    if result is None:
        return None

    return {
        "exitCode": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "payload": result.payload,
    }


def render_manual_blendshape_payload_json(avatar_path: str, adjustments: list[dict[str, Any]]) -> str:
    payload = {
        "tool": "vrc_apply_blendshapes",
        "params": {
            "avatarPath": avatar_path,
            "adjustments": adjustments,
            "saveAssets": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def apply_blendshapes_direct(
    settings: Settings,
    avatar_path: str,
    adjustments: list[dict[str, Any]],
) -> McpResult:
    return invoke_unity_mcp(
        settings,
        "vrc_apply_blendshapes",
        {
            "avatarPath": avatar_path,
            "adjustments": adjustments,
            "saveAssets": True,
        },
    )


def scan_avatar_controls_direct(settings: Settings, avatar_path: str | None) -> dict[str, Any]:
    output_path = build_dashboard_artifact_path("avatar_controls", avatar_path, "json")
    output_path.unlink(missing_ok=True)
    result = invoke_unity_mcp(
        settings,
        "vrc_scan_avatar_controls",
        {
            "avatarPath": avatar_path or "",
            "outputPath": "",
        },
    )
    if output_path.exists():
        payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        payload.setdefault("jsonPath", str(output_path))
        return ensure_dict_payload(payload, "avatar menu/parameter scan")

    payload = extract_tool_result_payload(result)
    payload = ensure_dict_payload(payload, "avatar menu/parameter scan")
    write_dashboard_json_artifact(output_path, payload)
    return payload


def toggle_scene_object_direct(settings: Settings, object_path: str, active: bool) -> Any:
    return extract_tool_result_payload(
        invoke_unity_mcp(
            settings,
            "vrc_toggle_scene_object",
            {
                "objectPath": object_path,
                "active": active,
                "saveAssets": True,
            },
        )
    )


def scan_avatar_parameters_direct(settings: Settings, avatar_path: str | None) -> dict[str, Any]:
    output_path = build_dashboard_artifact_path("avatar_parameters", avatar_path, "json")
    output_path.unlink(missing_ok=True)
    result = invoke_unity_mcp(
        settings,
        "vrc_scan_avatar_parameters",
        {
            "avatarPath": avatar_path or "",
            "outputPath": "",
        },
    )
    if output_path.exists():
        payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        payload.setdefault("jsonPath", str(output_path))
        return ensure_dict_payload(payload, "parameter scan")

    payload = extract_tool_result_payload(result)
    payload = ensure_dict_payload(payload, "parameter scan")
    write_dashboard_json_artifact(output_path, payload)
    return payload


def capture_scene_view_status_direct(settings: Settings, require_play_mode: bool = False) -> dict[str, Any]:
    result = invoke_unity_mcp(
        settings,
        "vrc_capture_scene_view",
        {
            "statusOnly": True,
            "requirePlayMode": require_play_mode,
        },
    )
    payload = extract_tool_result_payload(result)
    return ensure_dict_payload(payload, "vision capture status")


def build_clothing_fx_blueprint_from_controls(settings: Settings, avatar_path: str | None) -> dict[str, Any]:
    payload = scan_avatar_controls_direct(settings, avatar_path)
    controls = ensure_list_payload(payload.get("items") or [], "avatar menu/parameter scan")

    items: list[dict[str, Any]] = []
    for control in controls:
        if not isinstance(control, dict):
            continue
        display_name = str(control.get("displayName") or control.get("name") or control.get("parameterName") or "").strip()
        if not display_name:
            continue
        parameter_name = str(control.get("parameterName") or f"Cloth_{sanitize_fx_identifier(display_name)}").strip()
        object_path = str(control.get("objectPath") or "").strip()
        items.append(
            {
                "displayName": display_name,
                "parameterName": parameter_name,
                "animationClipName": f"FX_{sanitize_fx_identifier(display_name)}_Toggle",
                "sampleObjectPath": object_path,
                "source": control.get("source") or "",
                "bindingCount": 1 if object_path else 0,
                "note": "" if object_path else "Loaded from menu/parameter; no scene object binding was detected.",
            }
        )

    return {
        "items": items,
        "itemCount": len(items),
        "note": "Blueprint is built from avatar menu/parameter data. Items without scene object paths are existing controls and may not need new FX assets.",
    }


def sanitize_fx_identifier(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isalnum())
    return cleaned or "Clothing"


def apply_clothing_fx_direct(settings: Settings, avatar_path: str | None, items: list[dict[str, Any]]) -> dict[str, Any]:
    payload = extract_tool_result_payload(
        invoke_unity_mcp(
            settings,
            "vrc_apply_clothing_fx",
            {
                "avatarPath": avatar_path or "",
                "items": items,
            },
        )
    )
    return ensure_dict_payload(payload, "clothing fx apply")


def apply_parameter_optimization_direct(
    settings: Settings,
    avatar_path: str | None,
    suggestions: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = extract_tool_result_payload(
        invoke_unity_mcp(
            settings,
            "vrc_apply_parameter_optimization",
            {
                "avatarPath": avatar_path or "",
                "suggestions": suggestions,
            },
        )
    )
    return ensure_dict_payload(payload, "parameter optimization apply")


def scan_shader_materials_direct(settings: Settings, avatar_path: str | None) -> dict[str, Any]:
    output_path = build_dashboard_artifact_path("shader_material_inventory", avatar_path, "json")
    output_path.unlink(missing_ok=True)
    original_timeout = int(settings.unity_mcp_timeout_seconds or 30)
    settings.unity_mcp_timeout_seconds = max(original_timeout, 120)
    try:
        result = invoke_unity_mcp(
            settings,
            "vrc_scan_avatar_materials",
            {
                "avatarPath": avatar_path or "",
                "outputPath": "",
                "refreshAssets": False,
            },
        )
    finally:
        settings.unity_mcp_timeout_seconds = original_timeout
    if output_path.exists():
        payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        payload.setdefault("jsonPath", str(output_path))
        return ensure_dict_payload(payload, "shader material scan")

    payload = extract_tool_result_payload(result)
    payload = ensure_dict_payload(payload, "shader material scan")
    write_dashboard_json_artifact(output_path, payload)
    return payload


def apply_shader_material_tuning_direct(
    settings: Settings,
    avatar_path: str | None,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = extract_tool_result_payload(
        invoke_unity_mcp(
            settings,
            "vrc_apply_material_tuning",
            {
                "avatarPath": avatar_path or "",
                "changes": changes,
                "saveAssets": True,
            },
        )
    )
    return ensure_dict_payload(payload, "shader material apply")


def rollback_parameters_direct(
    settings: Settings,
    avatar_path: str | None,
    snapshot_payload: dict[str, Any],
) -> dict[str, Any]:
    payload = extract_tool_result_payload(
        invoke_unity_mcp(
            settings,
            "vrc_rollback_avatar_parameters",
            {
                "avatarPath": avatar_path or "",
                "parameterNames": snapshot_payload.get("parameterNames") or snapshot_payload.get("parameters") or [],
            },
        )
    )
    return ensure_dict_payload(payload, "parameter rollback")


def build_direct_blendshape_adjustments_from_plan(plan: Any) -> list[dict[str, Any]]:
    return [
        {
            "rendererPath": adjustment.renderer_path,
            "blendshapeName": adjustment.blendshape_name,
            "targetWeight": adjustment.target_weight,
        }
        for adjustment in plan.adjustments
    ]


def build_plan_change_preview(
    plan: Any,
    export_payload: dict[str, Any],
    selected_avatar: SelectedAvatar,
) -> list[dict[str, Any]]:
    allowed_targets = build_allowed_blendshape_index(export_payload, selected_avatar.avatar_path)
    changes: list[dict[str, Any]] = []
    for adjustment in plan.adjustments:
        current_weight = allowed_targets.get(
            (adjustment.renderer_path, adjustment.blendshape_name),
            {},
        ).get("currentWeight", 0.0)
        target_weight = float(adjustment.target_weight)
        previous_weight = float(current_weight)
        changes.append(
            {
                "avatarPath": adjustment.avatar_path,
                "rendererPath": adjustment.renderer_path,
                "blendshapeName": adjustment.blendshape_name,
                "previousWeight": previous_weight,
                "targetWeight": target_weight,
                "delta": target_weight - previous_weight,
                "reason": adjustment.reason,
                "confidence": adjustment.confidence,
            }
        )
    return changes


def verify_live_blendshape_changes(
    settings: Settings,
    selected_avatar: SelectedAvatar,
    change_preview: list[dict[str, Any]],
    tolerance: float = 0.25,
) -> list[dict[str, Any]]:
    if not change_preview:
        return []

    try:
        export_payload = export_blendshapes(settings)
        live_index = build_allowed_blendshape_index(export_payload, selected_avatar.avatar_path)
    except Exception as exc:
        emit_log("warning", "pipeline", "Failed to re-read blendshape export for verification.", {"error": str(exc)})
        return [
            {
                **item,
                "verified": False,
                "verificationStatus": "unreadable",
                "verificationError": str(exc),
            }
            for item in change_preview
        ]

    verified: list[dict[str, Any]] = []
    for item in change_preview:
        renderer_path = str(item.get("rendererPath") or "")
        blendshape_name = str(item.get("blendshapeName") or "")
        live_entry = live_index.get((renderer_path, blendshape_name))
        target_weight = float(item.get("targetWeight", 0.0) or 0.0)
        actual_weight = None
        if live_entry is not None:
            actual_weight = float(live_entry.get("currentWeight", 0.0) or 0.0)

        if actual_weight is None:
            status = "missing"
            verified_item = False
            difference = None
        else:
            difference = abs(actual_weight - target_weight)
            verified_item = difference <= tolerance
            status = "verified" if verified_item else "mismatch"

        verified.append(
            {
                **item,
                "actualWeight": actual_weight,
                "difference": difference,
                "verified": verified_item,
                "verificationStatus": status,
                "verificationTolerance": tolerance,
            }
        )

    emit_log(
        "info",
        "pipeline",
        "Blendshape live values re-read after execution.",
        {"verified": sum(1 for item in verified if item.get("verified")), "count": len(verified)},
    )
    return verified


def build_undo_items_from_change_preview(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "rendererPath": str(item.get("rendererPath") or ""),
            "blendshapeName": str(item.get("blendshapeName") or ""),
            "targetWeight": float(item.get("previousWeight", 0.0) or 0.0),
        }
        for item in changes
    ]




def sanitize_artifact_name(value: str, fallback: str = "avatar") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (value or "").strip())
    cleaned = cleaned.strip("._")
    return (cleaned or fallback)[:80]


def save_parameter_snapshot_payload(snapshot_payload: dict[str, Any], avatar_path: str | None) -> dict[str, str]:
    payload = dict(snapshot_payload)
    payload.setdefault("avatarPath", avatar_path or "")
    payload.setdefault("capturedBy", "dashboard")
    if "parameters" not in payload and isinstance(payload.get("parameterNames"), list):
        payload["parameters"] = payload["parameterNames"]

    PARAMETER_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    safe_avatar = sanitize_artifact_name(str(payload.get("avatarPath") or avatar_path or "avatar"))
    snapshot_path = PARAMETER_SNAPSHOT_DIR / f"{timestamp}_{safe_avatar}.json"
    latest_path = PARAMETER_SNAPSHOT_DIR / "latest.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    snapshot_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")

    DASHBOARD_RUNTIME.latest_parameter_snapshot_path = str(snapshot_path)
    return {
        "snapshotPath": str(snapshot_path),
        "snapshotUrl": to_artifact_url(str(snapshot_path)),
        "latestSnapshotPath": str(latest_path),
        "latestSnapshotUrl": to_artifact_url(str(latest_path)),
    }


def resolve_parameter_snapshot_path(snapshot_path: str | None) -> Path:
    if snapshot_path:
        candidate = Path(snapshot_path)
        if not candidate.is_absolute():
            candidate = (ROOT_DIR / candidate).resolve()
        else:
            candidate = candidate.resolve()
    elif DASHBOARD_RUNTIME.latest_parameter_snapshot_path:
        candidate = Path(DASHBOARD_RUNTIME.latest_parameter_snapshot_path).resolve()
    else:
        candidates = [
            path for path in PARAMETER_SNAPSHOT_DIR.glob("*.json")
            if path.name.lower() != "latest.json"
        ]
        if not candidates:
            raise RuntimeError("No parameter snapshot is available for rollback.")
        candidate = max(candidates, key=lambda path: path.stat().st_mtime).resolve()

    snapshot_root = PARAMETER_SNAPSHOT_DIR.resolve()
    try:
        candidate.relative_to(snapshot_root)
    except ValueError as exc:
        raise RuntimeError("Parameter snapshot path must be under artifacts/dashboard/parameter_snapshots.") from exc

    if not candidate.exists() or not candidate.is_file():
        raise RuntimeError(f"Parameter snapshot does not exist: {candidate}")

    return candidate


def remember_loaded_avatar(avatar_name: str, avatar_path: str) -> None:
    DASHBOARD_RUNTIME.current_avatar_name = avatar_name
    DASHBOARD_RUNTIME.current_avatar_path = avatar_path


def save_dashboard_artifacts(
    plan: Any,
    apply_payload_json: str,
    preview: str,
    result: McpResult | None,
    summary: str | None,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = DASHBOARD_ARTIFACTS_DIR / "runs" / timestamp
    latest_dir = DASHBOARD_ARTIFACTS_DIR / "latest"

    run_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)

    run_plan_path = run_dir / "plan.json"
    run_apply_payload_path = run_dir / "apply_payload.json"
    run_preview_path = run_dir / "preview.txt"
    run_summary_path = run_dir / "summary.txt"
    run_result_path = run_dir / "result.json"

    latest_plan_path = latest_dir / "plan.json"
    latest_apply_payload_path = latest_dir / "apply_payload.json"
    latest_preview_path = latest_dir / "preview.txt"
    latest_summary_path = latest_dir / "summary.txt"
    latest_result_path = latest_dir / "result.json"

    save_plan(run_plan_path, plan)
    save_plan(latest_plan_path, plan)
    save_text(run_apply_payload_path, apply_payload_json)
    save_text(latest_apply_payload_path, apply_payload_json)
    save_text(run_preview_path, preview)
    save_text(latest_preview_path, preview)

    if summary:
        save_text(run_summary_path, summary)
        save_text(latest_summary_path, summary)

    if result:
        save_result(run_result_path, result)
        save_result(latest_result_path, result)

    return {
        "runDirectory": str(run_dir),
        "latestDirectory": str(latest_dir),
        "files": {
            "plan": str(run_plan_path),
            "applyPayload": str(run_apply_payload_path),
            "preview": str(run_preview_path),
            "summary": str(run_summary_path) if summary else None,
            "result": str(run_result_path) if result else None,
        },
    }


def build_tool_payload_preview(tool: str, params: dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "params": params}, ensure_ascii=False, indent=2)


def build_clothes_fx_apply_preview(avatar_path: str | None, items: list[dict[str, Any]]) -> str:
    normalized_items = [
        {
            "displayName": item.get("displayName") or item.get("name") or "",
            "parameterName": item.get("parameterName") or f"Cloth_{(item.get('displayName') or item.get('name') or '').replace(' ', '')}",
            "sampleObjectPath": item.get("sampleObjectPath") or item.get("objectPath") or "",
            "animationClipName": item.get("animationClipName") or f"FX_{(item.get('displayName') or item.get('name') or '').replace(' ', '')}_Toggle",
        }
        for item in items
    ]
    return build_tool_payload_preview(
        "vrc_apply_clothing_fx",
        {"avatarPath": avatar_path or "", "items": normalized_items},
    )


def build_parameter_apply_optimization_preview(avatar_path: str | None, suggestions: list[dict[str, Any]]) -> str:
    return build_tool_payload_preview(
        "vrc_apply_parameter_optimization",
        {"avatarPath": avatar_path or "", "suggestions": suggestions},
    )


def build_parameter_rollback_preview(avatar_path: str | None, snapshot_payload: dict[str, Any]) -> str:
    parameter_names = snapshot_payload.get("parameters") or snapshot_payload.get("parameterNames") or []
    return build_tool_payload_preview(
        "vrc_rollback_avatar_parameters",
        {"avatarPath": avatar_path or "", "parameterNames": parameter_names},
    )


ARTIFACT_URL_TTL_SECONDS = 24 * 60 * 60
ARTIFACT_URL_MAX_FUTURE_SECONDS = 7 * 24 * 60 * 60
ARTIFACT_URL_CACHE_BUCKET_SECONDS = 60 * 60


def normalize_artifact_relative_path(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip().lstrip("/")
    path = PurePosixPath(text)
    parts = path.parts
    if not parts or path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Artifact path is not a safe relative path.")
    return path.as_posix()


def artifact_url_expiry(now: float | None = None) -> int:
    current = int(time.time() if now is None else now)
    bucket = max(1, ARTIFACT_URL_CACHE_BUCKET_SECONDS)
    return (current // bucket) * bucket + ARTIFACT_URL_TTL_SECONDS


def artifact_signature(relative_path: str, expires: int) -> str:
    message = f"{normalize_artifact_relative_path(relative_path)}\n{int(expires)}".encode("utf-8")
    return hmac.new(APP_SESSION_TOKEN.encode("utf-8"), message, hashlib.sha256).hexdigest()


def runtime_artifact_signature(relative_path: str, expires: int) -> str:
    relative = normalize_artifact_relative_path(relative_path)
    message = f"runtime-artifacts/{relative}\n{int(expires)}".encode("utf-8")
    return hmac.new(APP_SESSION_TOKEN.encode("utf-8"), message, hashlib.sha256).hexdigest()


def normalize_app_session_challenge_nonce(value: str) -> str:
    text = str(value or "").strip()
    if not 8 <= len(text) <= 128:
        raise HTTPException(status_code=400, detail="Session challenge nonce is invalid.")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", text):
        raise HTTPException(status_code=400, detail="Session challenge nonce is invalid.")
    return text


def app_session_challenge_signature(nonce: str) -> str:
    if not APP_SESSION_TOKEN:
        raise HTTPException(status_code=503, detail="App session token is unavailable.")
    message = f"vrcforge.app-session.v1\n{nonce}".encode("utf-8")
    return hmac.new(APP_SESSION_TOKEN.encode("utf-8"), message, hashlib.sha256).hexdigest()


def signed_artifact_url(relative_path: str, cache_version: str = "") -> str:
    relative = normalize_artifact_relative_path(relative_path)
    expires = artifact_url_expiry()
    signature = artifact_signature(relative, expires)
    version = f"&artifact_v={cache_version}" if cache_version else ""
    return f"/artifacts/{relative}?artifact_expires={expires}&artifact_sig={signature}{version}"


def signed_runtime_artifact_url(relative_path: str, cache_version: str = "") -> str:
    relative = normalize_artifact_relative_path(relative_path)
    expires = artifact_url_expiry()
    signature = runtime_artifact_signature(relative, expires)
    version = f"&artifact_v={cache_version}" if cache_version else ""
    return f"/runtime-artifacts/{relative}?artifact_expires={expires}&artifact_sig={signature}{version}"


def strip_url_query_fragment(value: str) -> str:
    return str(value or "").split("?", 1)[0].split("#", 1)[0]


def to_artifact_url(path_value: str) -> str:
    try:
        path = resolve_local_path(path_value)
        relative = path.relative_to(DASHBOARD_ARTIFACTS_DIR).as_posix()
        try:
            cache_version = str(path.stat().st_mtime_ns)
        except OSError:
            cache_version = ""
        return signed_artifact_url(relative, cache_version=cache_version)
    except Exception:
        return ""


def to_runtime_artifact_url(path_value: str) -> str:
    try:
        path = resolve_local_path(path_value)
        relative = path.relative_to(ARTIFACTS_DIR).as_posix()
        try:
            cache_version = str(path.stat().st_mtime_ns)
        except OSError:
            cache_version = ""
        return signed_runtime_artifact_url(relative, cache_version=cache_version)
    except Exception:
        return ""


def save_vision_audit_artifact(file_name: str, payload: dict[str, Any]) -> Path:
    latest_dir = DASHBOARD_ARTIFACTS_DIR / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    audit_path = latest_dir / file_name
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit_path


REFERENCE_GROUP_LABELS = {
    "source": "原图 / 当前脸",
    "target": "目标参考图",
}


def build_reference_image_context(request: DashboardRequest) -> dict[str, Any] | None:
    source_images = resolve_reference_image_entries(
        role="source",
        path_values=request.source_reference_image_paths,
        data_urls=request.source_reference_image_data_urls,
    )
    target_paths = list(request.target_reference_image_paths)
    target_data_urls = list(request.target_reference_image_data_urls)
    if request.reference_image_path:
        target_paths.append(request.reference_image_path)
    if request.reference_image_data_url:
        target_data_urls.append(request.reference_image_data_url)
    target_images = resolve_reference_image_entries(
        role="target",
        path_values=target_paths,
        data_urls=target_data_urls,
    )
    images = [*source_images, *target_images]
    if not images:
        return None

    groups = []
    if source_images:
        groups.append({"role": "source", "label": REFERENCE_GROUP_LABELS["source"], "images": source_images})
    if target_images:
        groups.append({"role": "target", "label": REFERENCE_GROUP_LABELS["target"], "images": target_images})

    context = {
        "imagePath": images[0]["imagePath"],
        "imageUrl": images[0]["imageUrl"],
        "mimeType": images[0]["mimeType"],
        "imagePaths": [image["imagePath"] for image in images],
        "imageLabels": [image["label"] for image in images],
        "images": images,
        "groups": groups,
        "count": len(images),
        "mode": "text_images_same_request",
    }
    save_vision_audit_artifact("reference_face_context.json", context)
    emit_log("info", "pipeline", "Reference images attached to blendshape planning request.", {"count": len(images)})
    return context


def resolve_reference_image_entries(
    role: str,
    path_values: list[str] | None = None,
    data_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for data_url in data_urls or []:
        image_path = save_reference_image_data_url(data_url, role=role, index=len(entries) + 1)
        entries.append(build_reference_image_entry(image_path, role, len(entries) + 1))
    for path_value in path_values or []:
        image_path = resolve_reference_image_path_value(path_value)
        if image_path is None:
            continue
        entries.append(build_reference_image_entry(image_path, role, len(entries) + 1))
    return entries


def build_reference_image_entry(image_path: Path, role: str, index: int) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    role_label = REFERENCE_GROUP_LABELS.get(role, role)
    return {
        "role": role,
        "label": f"{role_label} {index}",
        "imagePath": str(image_path),
        "imageUrl": to_artifact_url(str(image_path)),
        "mimeType": mime_type,
    }


def resolve_reference_image_path(request: DashboardRequest) -> Path | None:
    data_url = (request.reference_image_data_url or "").strip()
    if data_url:
        return save_reference_image_data_url(data_url)

    return resolve_reference_image_path_value(request.reference_image_path)


def resolve_reference_image_path_value(path_value: str | None) -> Path | None:
    path_value = (path_value or "").strip()
    if not path_value:
        return None

    if path_value.startswith("/artifacts/"):
        clean_path = strip_url_query_fragment(path_value)
        image_path = resolve_under(DASHBOARD_ARTIFACTS_DIR, clean_path[len("/artifacts/"):])
    else:
        image_path = resolve_local_path(path_value)

    if not image_path.exists() or not image_path.is_file():
        raise RuntimeError(f"Reference image file does not exist: {image_path}")
    validate_reference_image_file(image_path)
    return image_path


def resolve_under(root: Path, value: str) -> Path:
    root_path = root.resolve()
    candidate = (root_path / value).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise RuntimeError(f"Path escapes allowed root: {value}") from exc
    return candidate


def validate_reference_image_file(image_path: Path) -> None:
    suffix = image_path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise RuntimeError(f"Reference file is not a supported image type: {image_path}")
    with image_path.open("rb") as handle:
        header = handle.read(16)
    known_magic = (
        header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"\xff\xd8\xff")
        or header.startswith(b"GIF87a")
        or header.startswith(b"GIF89a")
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )
    if not known_magic:
        raise RuntimeError(f"Reference file content is not a supported image: {image_path}")


def save_reference_image_data_url(data_url: str, role: str = "target", index: int = 1) -> Path:
    if "," not in data_url or not data_url.lower().startswith("data:"):
        raise RuntimeError("Uploaded reference image must be a browser data URL.")

    header, encoded = data_url.split(",", 1)
    mime_type = header[5:].split(";", 1)[0].strip().lower() or "image/png"
    if not mime_type.startswith("image/"):
        raise RuntimeError(f"Uploaded reference file is not an image: {mime_type}")

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RuntimeError("Uploaded reference image could not be decoded.") from exc

    max_bytes = 8 * 1024 * 1024
    if len(image_bytes) > max_bytes:
        raise RuntimeError("Uploaded reference image is larger than 8 MB.")

    suffix = mimetypes.guess_extension(mime_type) or ".png"
    if suffix == ".jpe":
        suffix = ".jpg"

    latest_dir = DASHBOARD_ARTIFACTS_DIR / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    safe_role = "".join(char for char in role.lower() if char.isalnum() or char in {"_", "-"}) or "target"
    output_path = (latest_dir / f"reference_{safe_role}_{index:02d}{suffix}").resolve()
    output_path.write_bytes(image_bytes)
    return output_path


def clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return min(1.0, max(0.0, number))


def clamp_blendshape_weight(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return min(100.0, max(0.0, number))


def normalize_vision_box(raw_box: Any) -> dict[str, float] | None:
    if not raw_box:
        return None

    x = y = width = height = None
    x2 = y2 = None

    if isinstance(raw_box, dict):
        lowered = {str(key).lower().replace("-", "_"): value for key, value in raw_box.items()}
        if {"x", "y", "width", "height"}.issubset(lowered):
            x = lowered.get("x")
            y = lowered.get("y")
            width = lowered.get("width")
            height = lowered.get("height")
        elif {"x_min", "y_min", "x_max", "y_max"}.issubset(lowered):
            x = lowered.get("x_min")
            y = lowered.get("y_min")
            x2 = lowered.get("x_max")
            y2 = lowered.get("y_max")
        elif {"xmin", "ymin", "xmax", "ymax"}.issubset(lowered):
            x = lowered.get("xmin")
            y = lowered.get("ymin")
            x2 = lowered.get("xmax")
            y2 = lowered.get("ymax")
        elif {"left", "top", "right", "bottom"}.issubset(lowered):
            x = lowered.get("left")
            y = lowered.get("top")
            x2 = lowered.get("right")
            y2 = lowered.get("bottom")
    elif isinstance(raw_box, (list, tuple)) and len(raw_box) >= 4:
        x, y, width, height = raw_box[:4]

    if x is None or y is None:
        return None
    if x2 is None and y2 is None and (width is None or height is None):
        return None
    if (x2 is None) != (y2 is None):
        return None

    values = [value for value in [x, y, width, height, x2, y2] if value is not None]
    try:
        numeric_values = [abs(float(value)) for value in values]
    except (TypeError, ValueError):
        return None

    scale = 1.0
    if numeric_values:
        max_value = max(numeric_values)
        if max_value > 100:
            scale = 1000.0
        elif max_value > 1:
            scale = 100.0

    def scaled(value: Any) -> float:
        return clamp01(float(value) / scale)

    if x2 is not None and y2 is not None:
        left = scaled(x)
        top = scaled(y)
        right = scaled(x2)
        bottom = scaled(y2)
        x = min(left, right)
        y = min(top, bottom)
        width = abs(right - left)
        height = abs(bottom - top)
    else:
        x = scaled(x)
        y = scaled(y)
        width = clamp01(float(width) / scale)
        height = clamp01(float(height) / scale)

    if width <= 0 or height <= 0:
        return None

    return {
        "x": clamp01(x),
        "y": clamp01(y),
        "width": min(width, 1.0 - clamp01(x)),
        "height": min(height, 1.0 - clamp01(y)),
    }


def normalize_vision_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    status = str(normalized.get("status") or "").strip().lower()
    issues_raw = normalized.get("issues") or []
    issues = [str(item.get("summary") or item.get("label") or item) if isinstance(item, dict) else str(item) for item in issues_raw]
    annotations_raw = normalized.get("annotations") or normalized.get("regions") or normalized.get("boxes") or []

    annotations: list[dict[str, Any]] = []
    if isinstance(annotations_raw, list):
        for item in annotations_raw:
            if not isinstance(item, dict):
                continue
            box = normalize_vision_box(item.get("box") or item.get("bbox") or item.get("boundingBox") or item.get("bounding_box"))
            if not box:
                continue
            annotations.append(
                {
                    "label": str(item.get("label") or item.get("title") or "风险区域"),
                    "reason": str(item.get("reason") or item.get("summary") or ""),
                    "severity": str(item.get("severity") or item.get("risk") or "medium"),
                    "box": box,
                }
            )

    if status not in {"pass", "clipping"}:
        status = "clipping" if annotations or issues else "pass"

    normalized["status"] = status
    normalized["summary"] = str(normalized.get("summary") or ("检测到穿模风险" if status == "clipping" else "未发现明显穿模"))
    normalized["issues"] = issues
    normalized["annotations"] = annotations
    return normalized


def run_gemini_vision_audit(api_config: dict[str, Any], image_path: Path) -> dict[str, Any]:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("The google-genai package is not installed.") from exc

    api_key = str(api_config.get("api_key") or "").strip()
    model = str(api_config.get("model") or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    if not api_key:
        raise RuntimeError("Google AI Studio API key is empty. Save a Google AI Studio provider config before running image analysis.")

    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    client = genai.Client(api_key=api_key)
    image_bytes = image_path.read_bytes()
    prompt = (
        "你是 VRChat Avatar 视觉质检助手。检查这张 Avatar 截图是否存在明显穿模、衣物穿插、头发穿插或严重视觉问题。"
        "如果发现问题，请给出可定位区域，坐标使用相对图片宽高的 0 到 1 小数。"
        "只输出 JSON，不要 Markdown。格式为："
        '{"status":"pass|clipping","summary":"一句话结论","issues":["问题1","问题2"],'
        '"annotations":[{"label":"区域名","reason":"原因","severity":"low|medium|high",'
        '"box":{"x":0.1,"y":0.2,"width":0.3,"height":0.2}}]}'
    )
    response = client.models.generate_content(
        model=model,
        contents=[
            prompt,
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    payload = try_parse_json(getattr(response, "text", "") or "")
    if not isinstance(payload, dict):
        raise RuntimeError("Image analysis did not return valid JSON.")
    return normalize_vision_audit_payload(payload)


def build_event_message(event_type: str, payload: Any) -> dict[str, Any]:
    return {
        "type": event_type,
        "payload": payload,
        "timestamp": utc_now_iso(),
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_diagnostic_identity_context() -> dict[str, Any]:
    return {
        "projectPath": str(getattr(DASHBOARD_STATE, "selected_project_path", "") or ""),
        "avatarPath": str(getattr(DASHBOARD_RUNTIME, "current_avatar_path", "") or ""),
        "avatarName": str(getattr(DASHBOARD_RUNTIME, "current_avatar_name", "") or ""),
    }


def emit_log(
    level: str,
    scope: str,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    essential: bool = False,
) -> None:
    entry = DIAGNOSTIC_LOGGER.emit(
        level,
        scope,
        message,
        data,
        context=current_diagnostic_identity_context(),
        essential=essential,
    )
    if entry is not None:
        EVENT_BUS.broadcast_from_sync("log", entry)


async def emit_log_async(
    level: str,
    scope: str,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    essential: bool = False,
) -> None:
    entry = DIAGNOSTIC_LOGGER.emit(
        level,
        scope,
        message,
        data,
        context=current_diagnostic_identity_context(),
        essential=essential,
    )
    if entry is not None:
        await EVENT_BUS.broadcast("log", entry)


def build_log_entry(level: str, scope: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "timestamp": utc_now_iso(),
        "level": level,
        "scope": scope,
        "message": message,
        "data": data or {},
    }


def record_log_entry(entry: dict[str, Any]) -> None:
    DIAGNOSTIC_LOGGER.emit(
        entry.get("level"),
        entry.get("scope"),
        entry.get("message"),
        entry.get("data"),
        context=current_diagnostic_identity_context(),
    )


def recent_log_snapshot() -> list[dict[str, Any]]:
    return DIAGNOSTIC_LOGGER.recent_snapshot()


def prune_recent_logs() -> None:
    DIAGNOSTIC_LOGGER.cleanup()


def append_local_log(entry: dict[str, Any]) -> None:
    record_log_entry(entry)


def prune_local_log_file() -> None:
    DIAGNOSTIC_LOGGER.cleanup()


def prune_jsonl_log_file(path: Path) -> None:
    # Legacy JSONL diagnostics are never rewritten: startup cleanup removes
    # the known raw files after the unified pre-redaction logger is active.
    if path.parent == LOG_DIR and path.name in {"dashboard.log", "interactions.jsonl"}:
        path.unlink(missing_ok=True)


def prune_stale_dashboard_log_files() -> None:
    DIAGNOSTIC_LOGGER.cleanup()


def should_keep_log_line(line: str, cutoff: datetime) -> bool:
    payload = parse_diagnostic_log_line(line)
    if payload is None:
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            return False
        payload = candidate if isinstance(candidate, dict) else {}
    timestamp = parse_log_timestamp(payload.get("timestamp"))
    return timestamp is not None and timestamp >= cutoff


def parse_log_timestamp(value: Any) -> datetime | None:
    return parse_diagnostic_log_timestamp(value)


async def status_monitor_loop() -> None:
    global CURRENT_UNITY_STATUS
    global LAST_STATUS_CONNECTED
    global LAST_STATUS_FINGERPRINT

    while True:
        snapshot = await asyncio.to_thread(build_unity_status_snapshot)
        fingerprint = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        connected = bool(snapshot.get("connected"))

        if fingerprint != LAST_STATUS_FINGERPRINT:
            CURRENT_UNITY_STATUS = snapshot
            LAST_STATUS_FINGERPRINT = fingerprint
            await EVENT_BUS.broadcast("unity_status", snapshot)

        if LAST_STATUS_CONNECTED is None or connected != LAST_STATUS_CONNECTED:
            LAST_STATUS_CONNECTED = connected
            await emit_log_async(
                "success" if connected else "warn",
                "unity",
                "Unity MCP connected." if connected else "Unity MCP disconnected.",
                {
                    "host": snapshot.get("host"),
                    "port": snapshot.get("port"),
                    "instance": snapshot.get("instance"),
                },
            )

        await asyncio.sleep(DASHBOARD_STATE.status_push_interval_seconds)


def memory_review_background_blocker() -> str:
    if not BACKEND_OWNER_LEASE.owned:
        return "not_backend_owner"
    if any(
        str(item.get("status") or "") == "pending"
        for item in AGENT_GATEWAY.list_approvals(include_expired=False)
        if isinstance(item, dict)
    ):
        return "pending_approval"
    if AGENT_GATEWAY.has_in_flight_project_write():
        return "active_project_write"
    if AGENT_GATEWAY._active_apply_recoveries():
        return "active_project_recovery"
    active_desktop = AGENT_GATEWAY.list_active_desktop_actions(limit=1)
    if int(active_desktop.get("count") or len(active_desktop.get("actions") or [])) > 0:
        return "active_desktop_action"
    if int(RUNTIME_LANE_BUDGET.snapshot().get("interactive") or 0) > 0:
        return "interactive_lane_active"
    runs = AGENT_GATEWAY.list_runtime_runs(limit=100).get("runs") or []
    if any(
        str(item.get("status") or "").strip().casefold() in {"running", "applying"}
        for item in runs
        if isinstance(item, dict)
    ):
        return "interactive_run_active"
    return ""


async def background_goal_monitor_loop() -> None:
    while True:
        try:
            payload = await asyncio.to_thread(AGENT_GOALS.reconcile_agent_goal_watchdogs)
            if payload.get("deliveries") or payload.get("reminders"):
                await broadcast_background_goal_state({})
        except Exception as exc:  # noqa: BLE001 - monitoring must not interrupt the core runtime.
            emit_log("warn", "agent", "Background goal monitor check had a warning.", {"error": str(exc)})
        try:
            await MEMORY_REVIEW.host.schedule_due_background(memory_review_background_blocker)
        except Exception:  # noqa: BLE001 - Memory Review cannot interrupt Goal monitoring.
            emit_log("warn", "agent", "Memory Review monitor check had a bounded warning.", {"failureClass": "monitor"})
        await asyncio.sleep(30)


def build_unity_status_snapshot(
    settings: Settings | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    return _UNITY_STATUS.build_unity_status_snapshot(settings, project_root)


def build_vrcforge_mcp_core_unavailable_status(
    project_root: Path | None,
    error: str,
) -> dict[str, Any]:
    return _UNITY_STATUS.build_vrcforge_mcp_core_unavailable_status(project_root, error)


def build_vrcforge_mcp_core_status(project_root: Path, settings: Settings) -> dict[str, Any]:
    return _UNITY_STATUS.build_vrcforge_mcp_core_status(project_root, settings)


def _repair_phase(phase_id: str, status: str, message: str, detail: Any = None) -> dict[str, Any]:
    if status not in {"ok", "warning", "error", "skipped"}:
        status = "warning"
    return {
        "id": phase_id,
        "status": status,
        "message": message,
        "detail": _redact_doctor_detail(detail),
    }


def _unity_repair_status_summary(
    status: dict[str, Any],
    project_root: Path | None = None,
) -> dict[str, Any]:
    tools = status.get("tools") if isinstance(status.get("tools"), dict) else {}
    mcp_health = status.get("mcpHealth") if isinstance(status.get("mcpHealth"), dict) else {}
    _ = project_root
    selected_instance_matched = bool(status.get("selectedInstanceMatched"))
    return {
        "connected": bool(status.get("connected")),
        "mcpServerReachable": bool(status.get("mcpServerReachable")),
        "mcpServerVersion": str(mcp_health.get("version") or mcp_health.get("serverVersion") or ""),
        "unityMcpPackageVersion": str(status.get("unityMcpPackageVersion") or ""),
        "unityInstanceRegistered": bool(status.get("unityInstanceRegistered")),
        "selectedInstanceMatched": selected_instance_matched,
        "activeInstanceCount": int(status.get("activeInstanceCount") or 0),
        "vrcForgeToolsRegistered": bool(status.get("vrcForgeToolsRegistered")),
        "totalTools": int(tools.get("totalTools") or 0),
        "vrcForgeToolsCount": int(tools.get("vrcForgeToolsCount") or 0),
        "missingRequiredVrcForgeTools": status.get("missingRequiredVrcForgeTools") or [],
        "toolsError": str(tools.get("error") or ""),
        "error": str(status.get("error") or ""),
    }


def read_unity_mcp_package_version(project_root: Path) -> str:
    return "vrcforge-core-2026-07-28" if vrcforge_mcp_core_installed(project_root) else ""


def _repair_process_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _process_cmdline_text(
    process: Any,
    *,
    require_discovery_evidence: bool = False,
) -> str:
    try:
        cmdline = process.info.get("cmdline") if hasattr(process, "info") else process.cmdline()
    except Exception as exc:  # noqa: BLE001 - process metadata can disappear while enumerating.
        if require_discovery_evidence:
            raise UnityProcessDiscoveryUnavailable(
                "A process command line could not be read."
            ) from exc
        return ""
    if isinstance(cmdline, (list, tuple)):
        value = " ".join(str(part) for part in cmdline if part is not None)
    else:
        value = str(cmdline or "")
    if require_discovery_evidence and not value.strip():
        raise UnityProcessDiscoveryUnavailable(
            "A running Unity process has no readable command line."
        )
    return value


def _process_exe_text(process: Any) -> str:
    try:
        value = process.info.get("exe") if hasattr(process, "info") else process.exe()
    except Exception:  # noqa: BLE001
        return ""
    return normalize_path_string(str(value or ""))


def _process_name_text(
    process: Any,
    *,
    require_discovery_evidence: bool = False,
) -> str:
    try:
        value = process.info.get("name") if hasattr(process, "info") else process.name()
    except Exception as exc:  # noqa: BLE001
        if require_discovery_evidence:
            raise UnityProcessDiscoveryUnavailable(
                "A process name could not be read."
            ) from exc
        return ""
    name = str(value or "")
    if require_discovery_evidence and not name.strip():
        raise UnityProcessDiscoveryUnavailable(
            "Process discovery returned an unreadable process name."
        )
    return name


class UnityProcessDiscoveryUnavailable(RuntimeError):
    """Raised when running-process evidence cannot be collected reliably."""


def _iter_processes(*, require_discovery_evidence: bool = False) -> list[Any]:
    if psutil is None:
        if require_discovery_evidence:
            raise UnityProcessDiscoveryUnavailable("Process discovery is unavailable.")
        return []
    try:
        return list(psutil.process_iter(["pid", "name", "exe", "cmdline"]))
    except Exception as exc:  # noqa: BLE001
        if require_discovery_evidence:
            raise UnityProcessDiscoveryUnavailable(
                "Process discovery did not produce usable evidence."
            ) from exc
        return []


def list_running_unity_processes(
    *,
    require_discovery_evidence: bool = False,
) -> list[dict[str, Any]]:
    if os.name != "nt":
        if require_discovery_evidence:
            raise UnityProcessDiscoveryUnavailable(
                "Unity process discovery is unavailable on this platform."
            )
        return []
    processes: list[dict[str, Any]] = []
    for process in _iter_processes(
        require_discovery_evidence=require_discovery_evidence
    ):
        if _process_name_text(
            process,
            require_discovery_evidence=require_discovery_evidence,
        ).casefold() != "unity.exe":
            continue
        try:
            process_id = int(process.info.get("pid") if hasattr(process, "info") else process.pid)
        except Exception as exc:  # noqa: BLE001
            if require_discovery_evidence:
                raise UnityProcessDiscoveryUnavailable(
                    "A running Unity process has no readable process id."
                ) from exc
            continue
        command_line = _process_cmdline_text(
            process,
            require_discovery_evidence=require_discovery_evidence,
        )
        processes.append(
            {
                "processId": process_id,
                "executablePath": _process_exe_text(process),
                "commandLine": command_line,
            }
        )
    return processes


def extract_unity_project_path_from_command_line(command_line: str) -> str:
    value = str(command_line or "")
    if not value:
        return ""
    match = re.search(r"(?i)(?:^|\s)-projectPath(?:\s+|=)(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))", value)
    if not match:
        return ""
    return normalize_path_string(str(next((group for group in match.groups() if group), "")).strip())


def discover_running_unity_projects() -> list[dict[str, str]]:
    projects: list[dict[str, str]] = []
    seen: set[str] = set()
    for process in list_running_unity_processes():
        path = extract_unity_project_path_from_command_line(str(process.get("commandLine") or ""))
        if not path:
            continue
        project_root = Path(path)
        if not is_unity_project_path(project_root):
            continue
        key = normalize_path_string(str(project_root)).casefold()
        if key in seen:
            continue
        seen.add(key)
        projects.append(
            {
                "name": project_root.name,
                "path": normalize_path_string(str(project_root)),
                "editorVersion": parse_editor_version(project_root / "ProjectSettings" / "ProjectVersion.txt"),
            }
        )
    return projects


def _project_path_token(path: Path) -> str:
    return normalize_path_string(str(path)).replace("\\", "/").casefold().strip()


def unity_process_matches_project(process: dict[str, Any], project_root: Path) -> bool:
    return unity_process_exactly_matches_project(process, project_root)


def unity_process_exactly_matches_project(process: dict[str, Any], project_root: Path) -> bool:
    observed_path = extract_unity_project_path_from_command_line(
        str(process.get("commandLine") or "")
    )
    return bool(
        observed_path
        and _project_path_token(Path(observed_path)) == _project_path_token(project_root)
    )


def unity_instance_matches_project(instance: dict[str, Any], project_root: Path) -> bool:
    instance_path = normalize_path_string(str(instance.get("projectPath") or "")).casefold()
    project_path = normalize_path_string(str(project_root)).casefold()
    return bool(instance_path and instance_path == project_path)


def _existing_command_path_candidates(command_names: tuple[str, ...], extra_candidates: list[Path] | None = None) -> Path | None:
    candidates = list(extra_candidates or [])
    for command_name in command_names:
        resolved = shutil.which(command_name)
        if resolved:
            candidates.append(Path(resolved))
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate.resolve()
        except OSError:
            continue
    return None

def request_windows_process_close(process_id: int) -> bool:
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return False
    found_window = False
    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _enum_window(hwnd: int, _lparam: int) -> bool:
        nonlocal found_window
        window_pid = ctypes.c_ulong()
        try:
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if int(window_pid.value) == int(process_id) and user32.IsWindowVisible(hwnd):
                found_window = True
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception:  # noqa: BLE001
            return True
        return True

    try:
        user32.EnumWindows(enum_proc_type(_enum_window), 0)
    except Exception:  # noqa: BLE001
        return False
    return found_window


def wait_for_process_exit(process_id: int, timeout_seconds: int) -> bool:
    if psutil is None:
        return False
    try:
        process = psutil.Process(process_id)
        process.wait(timeout=max(1, int(timeout_seconds)))
        return True
    except psutil.NoSuchProcess:
        return True
    except Exception:  # noqa: BLE001
        return False

def wait_for_unity_project_registration(settings: Settings, project_root: Path, wait_seconds: int) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + max(1, wait_seconds)
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = build_unity_instances_diagnostics(settings, project_root)
        instances = latest.get("instances") if isinstance(latest.get("instances"), list) else []
        matched = next(
            (
                instance
                for instance in instances
                if unity_instance_matches_project(instance, project_root)
                and stable_unity_cli_selector(instance)
            ),
            None,
        )
        if matched:
            cli_selector = stable_unity_cli_selector(matched)
            if cli_selector:
                DASHBOARD_STATE.unity_instance = cli_selector
                settings.unity_mcp_instance = cli_selector
            return True, latest
        time.sleep(2.0)
    return False, latest


def unity_repair_tools_ready(summary: dict[str, Any]) -> bool:
    return bool(
        summary.get("unityInstanceRegistered")
        and summary.get("selectedInstanceMatched")
        and summary.get("vrcForgeToolsRegistered")
        and int(summary.get("totalTools") or 0) > 0
        and not summary.get("missingRequiredVrcForgeTools")
    )


def unity_repair_tools_message(summary: dict[str, Any]) -> str:
    tools_error = str(summary.get("toolsError") or summary.get("error") or "")
    if "No Unity instances connected" in tools_error:
        return "MCP server is reachable, but Unity's execution connection is not active."
    if not summary.get("unityInstanceRegistered"):
        return "Unity has not registered with the MCP server yet."
    if not summary.get("selectedInstanceMatched"):
        return "Unity registered, but its active instance is not bound to the selected project."
    if int(summary.get("totalTools") or 0) <= 0:
        return "Unity registered, but the MCP tool list is still empty."
    if not summary.get("vrcForgeToolsRegistered"):
        return "Unity registered, but VRCForge Unity tools are not registered yet."
    missing = summary.get("missingRequiredVrcForgeTools") or []
    if missing:
        return f"Unity registered, but {len(missing)} required VRCForge tool(s) are missing."
    return "Unity MCP tools are ready."


def recent_unity_mcp_execution_error(window_seconds: int = 300) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(30, window_seconds))
    patterns = (
        "disconnected while awaiting command_result",
        "No Unity instances connected",
        "Unity plugin session",
        "Unity MCP disconnected",
    )
    entries = DIAGNOSTIC_LOGGER.tail_entries(250)
    entries.extend(recent_log_snapshot())
    for entry in reversed(entries):
        timestamp = parse_log_timestamp(entry.get("timestamp"))
        if timestamp is not None and timestamp < cutoff:
            continue
        message = str(entry.get("message") or "")
        data_text = json.dumps(entry.get("data") or {}, ensure_ascii=False)
        haystack = f"{message}\n{data_text}"
        if any(pattern in haystack for pattern in patterns):
            return {
                "timestamp": entry.get("timestamp"),
                "level": entry.get("level"),
                "scope": entry.get("scope"),
                "message": message,
                "detail": entry.get("data") or {},
            }
    return {}


def build_unity_repair_quick_summary(settings: Settings, project_root: Path) -> dict[str, Any]:
    health = fetch_mcp_server_health(settings)
    instances = build_unity_instances_diagnostics(settings, project_root)
    active_instance = instances.get("activeInstance") if isinstance(instances.get("activeInstance"), dict) else {}
    matched = bool(
        active_instance
        and stable_unity_cli_selector(active_instance)
        and unity_instance_matches_project(active_instance, project_root)
    )
    active_count = int(instances.get("activeCount") or 0)
    recent_error = recent_unity_mcp_execution_error()
    return {
        "connected": bool(health.get("ok") and active_count),
        "mcpServerReachable": bool(health.get("ok")),
        "mcpServerVersion": str(health.get("version") or ""),
        "unityMcpPackageVersion": read_unity_mcp_package_version(project_root),
        "unityInstanceRegistered": bool(active_count),
        "selectedInstanceMatched": matched,
        "activeInstanceCount": active_count,
        "vrcForgeToolsRegistered": False,
        "totalTools": 0,
        "vrcForgeToolsCount": 0,
        "missingRequiredVrcForgeTools": list(REQUIRED_VRCFORGE_UNITY_TOOLS),
        "toolsError": str(recent_error.get("message") or recent_error.get("detail") or ""),
        "error": str(health.get("error") or instances.get("error") or ""),
    }


def verify_unity_mcp_execution_connection(settings: Settings) -> tuple[bool, dict[str, Any]]:
    _ = settings
    recent_error = recent_unity_mcp_execution_error()
    if recent_error:
        return False, {
            "mode": "recent-log-scan",
            "error": "Recent Unity MCP execution disconnect detected.",
            "recentError": recent_error,
        }
    return True, {
        "mode": "recent-log-scan",
        "message": "No recent Unity MCP execution disconnect was recorded.",
    }


def unity_repair_execution_ready(
    settings: Settings,
    summary: dict[str, Any],
    phases: list[dict[str, Any]],
    phase_id: str,
) -> bool:
    if not unity_repair_tools_ready(summary):
        return False
    probe_ok, probe_detail = verify_unity_mcp_execution_connection(settings)
    phases.append(
        _repair_phase(
            phase_id,
            "ok" if probe_ok else "warning",
            "Unity MCP tool execution probe succeeded."
            if probe_ok
            else "Unity MCP tool list is available, but executing a VRCForge read-only tool failed.",
            probe_detail,
        )
    )
    return probe_ok


def unity_repair_stable_tool_poll_settings(settings: Settings, wait_seconds: int) -> Settings:
    poll_settings = copy.copy(settings)
    current_timeout = int(getattr(poll_settings, "unity_mcp_timeout_seconds", 0) or 0)
    stable_timeout = min(max(int(wait_seconds or 0), 8), 10)
    poll_settings.unity_mcp_timeout_seconds = min(max(current_timeout, stable_timeout), 10)
    return poll_settings


def wait_for_unity_tools_ready(
    settings: Settings,
    project_root: Path,
    wait_seconds: int,
    *,
    poll_interval_seconds: float = 2.0,
) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + max(1, wait_seconds)
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = build_unity_status_snapshot(settings, project_root)
        latest = _unity_repair_status_summary(status, project_root)
        if unity_repair_tools_ready(latest):
            return True, latest
        if poll_interval_seconds > 0:
            time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))
    if not latest:
        latest = _unity_repair_status_summary(
            build_unity_status_snapshot(settings, project_root),
            project_root,
        )
    return False, latest


def resolve_unity_editor_path_for_repair(project_root: Path, requested_path: str = "") -> tuple[Path | None, str]:
    candidates: list[tuple[str, Path]] = []
    if requested_path.strip():
        candidates.append(("request", Path(requested_path.strip()).expanduser()))
    if DASHBOARD_STATE.unity_editor_path.strip():
        candidates.append(("settings", Path(DASHBOARD_STATE.unity_editor_path.strip()).expanduser()))

    running_processes = list_running_unity_processes()
    for process in running_processes:
        executable = str(process.get("executablePath") or "").strip()
        if executable and unity_process_exactly_matches_project(process, project_root):
            candidates.append(("running-unity-project", Path(executable)))

    editor_version = parse_editor_version(project_root / "ProjectSettings" / "ProjectVersion.txt")
    if editor_version and editor_version != "Unknown":
        for base_value in [
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ]:
            if not base_value:
                continue
            base = Path(base_value)
            candidates.extend(
                [
                    ("unity-hub", base / "Unity" / "Hub" / "Editor" / editor_version / "Editor" / "Unity.exe"),
                    ("unity-hub", base / "Programs" / "Unity" / "Hub" / "Editor" / editor_version / "Editor" / "Unity.exe"),
                ]
            )

    seen: set[str] = set()
    for source, candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            return resolved, source
    return None, "not-found"


def close_unity_project_gracefully(project_root: Path, timeout_seconds: int) -> tuple[bool, str, dict[str, Any]]:
    try:
        processes = list_running_unity_processes(require_discovery_evidence=True)
    except UnityProcessDiscoveryUnavailable:
        return False, "Unity process evidence is unavailable, so VRCForge did not close any editor.", {
            "processCount": 0,
            "evidenceAvailable": False,
        }
    matching = [process for process in processes if unity_process_exactly_matches_project(process, project_root)]
    if not processes:
        return True, "Unity is not currently running; launch can proceed.", {"processCount": 0}
    if not matching:
        return False, "No running Unity process clearly matched the selected project, so VRCForge did not close any editor.", {"processCount": len(processes)}

    results: list[dict[str, Any]] = []
    for process in matching:
        process_id = int(process["processId"])
        close_requested = request_windows_process_close(process_id)
        exited = wait_for_process_exit(process_id, timeout_seconds)
        result = {"pid": process_id, "ok": exited, "closeRequested": close_requested, "exited": exited}
        results.append(result)
        if not exited:
            return False, "Unity did not exit after a normal close request. Save or close Unity manually, then Retry.", {"processes": results}

    return True, "Unity closed cleanly.", {"processes": results}


def launch_unity_project(editor_path: Path, project_root: Path) -> tuple[bool, str]:
    try:
        launch_unity_subprocess(
            [
                str(editor_path),
                "-projectPath",
                str(project_root),
                "-executeMethod",
                "VRCForge.Editor.McpBridgeBootstrap.StartBridgeNow",
            ],
            editor_path,
            project_root,
        )
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, ""


def launch_unity_subprocess(command: list[str], editor_path: Path, project_root: Path) -> subprocess.Popen[str]:
    internal_dir = pyinstaller_internal_dir()
    if internal_dir:
        set_windows_dll_directory(None)
    try:
        return subprocess.Popen(
            command,
            cwd=unity_launch_working_directory(editor_path, project_root),
            env=unity_launch_environment(),
        )
    finally:
        if internal_dir:
            set_windows_dll_directory(str(internal_dir))


def unity_launch_working_directory(editor_path: Path, project_root: Path) -> str:
    if editor_path.parent.is_dir():
        return str(editor_path.parent)
    if project_root.is_dir():
        return str(project_root)
    return str(Path.home())


def pyinstaller_internal_dir() -> Path | None:
    candidates = []
    if getattr(sys, "_MEIPASS", ""):
        candidates.append(Path(str(getattr(sys, "_MEIPASS"))))
    candidates.append(ROOT_DIR / "backend" / "_internal")
    candidates.append(Path(sys.executable).resolve().parent / "_internal")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def set_windows_dll_directory(path: str | None) -> None:
    if os.name != "nt":
        return
    ctypes.windll.kernel32.SetDllDirectoryW(path)


def unity_launch_environment() -> dict[str, str]:
    env = os.environ.copy()
    path_entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
    blocked_dirs = [
        ROOT_DIR / "backend" / "_internal",
        Path(sys.executable).resolve().parent / "_internal",
    ]
    filtered_entries: list[str] = []
    for entry in path_entries:
        entry_path = Path(entry).resolve(strict=False)
        if any(path_is_under(entry_path, blocked) for blocked in blocked_dirs):
            continue
        filtered_entries.append(entry)
    env["PATH"] = os.pathsep.join(filtered_entries)
    for key in list(env):
        if key.startswith("_PYI_"):
            env.pop(key, None)
    return env


def path_is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def resolve_unity_mcp_repair_project(project_path: str) -> Path:
    candidate_text = project_path.strip() or DASHBOARD_STATE.selected_project_path.strip()
    if not candidate_text:
        raise RuntimeError("No Unity project is selected. Select a Unity project first, then run Repair bridge.")
    candidate = Path(normalize_path_string(candidate_text))
    if not is_unity_project_path(candidate):
        raise RuntimeError("Selected path is not a Unity project root. Select the project root containing Assets, Packages, and ProjectSettings.")
    return candidate


def repair_unity_mcp_bridge_sync(request: UnityMcpRepairRequest) -> dict[str, Any]:
    generated_at = utc_now_iso()
    if not UNITY_MCP_REPAIR_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "schema": "vrcforge.unity_mcp_repair.v1",
            "status": "busy",
            "generatedAt": generated_at,
            "projectPath": request.project_path,
            "phases": [
                _repair_phase(
                    "repair_lock",
                    "warning",
                    "Another Unity MCP repair is already running. Wait for it to finish, then retry.",
                )
            ],
            "before": {},
            "after": {},
        }
    try:
        return _repair_unity_mcp_bridge_sync_unlocked(request, generated_at=generated_at)
    finally:
        UNITY_MCP_REPAIR_LOCK.release()


def _repair_unity_mcp_bridge_sync_unlocked(request: UnityMcpRepairRequest, *, generated_at: str) -> dict[str, Any]:
    phases: list[dict[str, Any]] = []
    try:
        project_root = resolve_unity_mcp_repair_project(request.project_path)
        settings = load_dashboard_settings(ConnectionRequest(settings_path=str(DASHBOARD_STATE.settings_path)))
        if not vrcforge_mcp_core_installed(project_root):
            before_status = build_vrcforge_mcp_core_unavailable_status(
                project_root,
                "The selected project does not contain the VRCForge MCP2 unitypackage.",
            )
            before = _unity_repair_status_summary(before_status, project_root)
            phases.append(
                _repair_phase(
                    "import_vrcforge_package",
                    "warning",
                    "Import the VRCForge unitypackage into the selected project; no external Unity MCP connector is used.",
                )
            )
            return {
                "ok": False,
                "schema": "vrcforge.unity_mcp_repair.v1",
                "status": "needs_user_action",
                "generatedAt": generated_at,
                "projectPath": str(project_root),
                "phases": phases,
                "before": before,
                "after": before,
            }

        status = build_unity_status_snapshot(settings, project_root)
        summary = _unity_repair_status_summary(status, project_root)
        if unity_repair_tools_ready(summary):
            phases.append(_repair_phase("core_ready", "ok", "The project-scoped VRCForge MCP Core is reachable."))
            return {
                "ok": True,
                "schema": "vrcforge.unity_mcp_repair.v1",
                "status": "healthy",
                "generatedAt": generated_at,
                "projectPath": str(project_root),
                "phases": phases,
                "before": summary,
                "after": summary,
            }

        phases.append(
            _repair_phase(
                "open_unity",
                "warning",
                "Open the selected project in Unity and wait for VRCForge MCP Core Ready, then retry. "
                "Repair does not start an external server or use a fallback connector.",
                {"error": str(status.get("error") or "")},
            )
        )
        return {
            "ok": False,
            "schema": "vrcforge.unity_mcp_repair.v1",
            "status": "needs_user_action",
            "generatedAt": generated_at,
            "projectPath": str(project_root),
            "phases": phases,
            "before": summary,
            "after": summary,
        }
    except Exception as exc:  # noqa: BLE001 - Doctor repair should report actionable failure instead of crashing the UI.
        phases.append(_repair_phase("repair", "error", str(exc)))
        return {
            "ok": False,
            "schema": "vrcforge.unity_mcp_repair.v1",
            "status": "failed",
            "generatedAt": generated_at,
            "projectPath": request.project_path,
            "phases": phases,
            "before": {},
            "after": {},
        }


def health_component(status: str, message: str, detail: Any = "") -> dict[str, Any]:
    if status not in {"ok", "warning", "error", "unknown"}:
        status = "unknown"
    return {
        "status": status,
        "message": message,
        "detail": detail,
    }


def probe_directory_write(directory: Path) -> tuple[bool, str]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe_path = directory / ".vrcforge_write_probe"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def load_manifest_payload(manifest_path: Path) -> dict[str, Any] | None:
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def build_health_components(settings: Settings) -> dict[str, dict[str, Any]]:
    selected_project = Path(DASHBOARD_STATE.selected_project_path) if DASHBOARD_STATE.selected_project_path else None
    manifest_path = selected_project / "Packages" / "manifest.json" if selected_project else None
    manifest_payload = load_manifest_payload(manifest_path) if manifest_path else None
    dependencies = manifest_payload.get("dependencies") if isinstance(manifest_payload, dict) else {}
    dependencies = dependencies if isinstance(dependencies, dict) else {}

    config_writable, config_error = probe_directory_write(CONFIG_DIR)
    logs_writable, logs_error = probe_directory_write(LOG_DIR)
    artifacts_writable, artifacts_error = probe_directory_write(ARTIFACTS_DIR)

    dashboard_index = DASHBOARD_DIR / "index.html"
    dashboard_url = "http://127.0.0.1:8757/"
    components: dict[str, dict[str, Any]] = {
        "backend": health_component(
            "ok",
            "Backend process is responding.",
            {"version": app.version, "programDir": str(ROOT_DIR), "portableMode": PORTABLE_MODE},
        ),
        "dashboardFiles": health_component(
            "ok" if dashboard_index.exists() else "error",
            "Dashboard files are present." if dashboard_index.exists() else "Dashboard index.html is missing.",
            {"index": str(dashboard_index), "dashboardUrl": dashboard_url},
        ),
        "configReadWrite": health_component(
            "ok" if config_writable and RUNTIME_SETTINGS_PATH.exists() else "warning" if config_writable else "error",
            "Config directory is writable." if config_writable else "Config directory is not writable.",
            {"directory": str(CONFIG_DIR), "settingsPath": str(RUNTIME_SETTINGS_PATH), "error": config_error},
        ),
        "logsWrite": health_component(
            "ok" if logs_writable else "error",
            "Logs directory is writable." if logs_writable else "Logs directory is not writable.",
            {"directory": str(LOG_DIR), "error": logs_error},
        ),
        "artifactsWrite": health_component(
            "ok" if artifacts_writable else "error",
            "Artifacts directory is writable." if artifacts_writable else "Artifacts directory is not writable.",
            {"directory": str(ARTIFACTS_DIR), "error": artifacts_error},
        ),
    }

    if selected_project is None:
        components["selectedUnityProject"] = health_component("warning", "No Unity project selected.", "")
        components["unityPluginInstalled"] = health_component("unknown", "Unity plugin status is unknown until a project is selected.", "")
        components["mcpPackageConfigured"] = health_component("unknown", "VRCForge MCP Core status is unknown until a project is selected.", "")
    else:
        required_paths = {
            "Assets": selected_project / "Assets",
            "Packages/manifest.json": selected_project / "Packages" / "manifest.json",
            "ProjectSettings/ProjectVersion.txt": selected_project / "ProjectSettings" / "ProjectVersion.txt",
        }
        missing = [label for label, path in required_paths.items() if not path.exists()]
        components["selectedUnityProject"] = health_component(
            "ok" if not missing else "error",
            "Selected Unity project root is valid." if not missing else "Selected Unity project is missing required files.",
            {"path": str(selected_project), "missing": missing},
        )

        plugin_path = selected_project / "Assets" / "VRCForge" / "Editor"
        components["unityPluginInstalled"] = health_component(
            "ok" if plugin_path.exists() else "error",
            "VRCForge Unity plugin is installed." if plugin_path.exists() else "VRCForge Unity plugin is missing.",
            str(plugin_path),
        )

        mcp_core_installed = vrcforge_mcp_core_installed(selected_project)
        components["mcpPackageConfigured"] = health_component(
            "ok" if mcp_core_installed else "error",
            "VRCForge MCP Core is bundled with the plugin."
            if mcp_core_installed
            else "VRCForge MCP Core is missing from the plugin install.",
            {"corePath": str(selected_project / "Assets" / "VRCForge" / "Core" / "MCP")},
        )

    unity_status = CURRENT_UNITY_STATUS or build_unity_status_snapshot(settings)
    if unity_status.get("connected"):
        components["unityMcpBridgeReachable"] = health_component("ok", "Unity MCP bridge is reachable.", unity_status)
    else:
        components["unityMcpBridgeReachable"] = health_component(
            "warning",
            "Unity MCP bridge is not reachable.",
            unity_status.get("error") or unity_status,
        )
    components["unityMcpInstance"] = health_component(
        "ok" if unity_status.get("unityInstanceRegistered") else "warning",
        "Unity instance is registered with MCP." if unity_status.get("unityInstanceRegistered") else "MCP server is reachable, but no Unity instance is registered.",
        {
            "activeInstance": unity_status.get("activeInstance"),
            "activeInstanceCount": unity_status.get("activeInstanceCount"),
            "selectedInstanceMatched": unity_status.get("selectedInstanceMatched"),
        },
    )
    missing_tools = unity_status.get("missingRequiredVrcForgeTools") or []
    vrcforge_tools_registered = bool(unity_status.get("vrcForgeToolsRegistered"))
    components["vrcForgeUnityTools"] = health_component(
        "ok" if vrcforge_tools_registered and not missing_tools else "warning",
        "VRCForge Unity tools are registered."
        if vrcforge_tools_registered and not missing_tools
        else "Unity MCP is connected, but VRCForge Unity tools are missing or incomplete.",
        {
            "totalTools": (unity_status.get("tools") or {}).get("totalTools"),
            "vrcForgeToolsCount": (unity_status.get("tools") or {}).get("vrcForgeToolsCount"),
            "missingRequiredVrcForgeTools": missing_tools,
        },
    )

    components["providerConfigPresent"] = health_component(
        "ok" if not provider_requires_api_key(settings.llm_provider) or bool(settings.llm_api_key) else "warning",
        "Provider configuration is present."
        if not provider_requires_api_key(settings.llm_provider) or bool(settings.llm_api_key)
        else f"{provider_display_name(settings.llm_provider)} API key is not configured.",
        {"provider": settings.llm_provider, "model": settings.llm_model},
    )
    agent_health = AGENT_GATEWAY.build_health()
    components["agentGateway"] = health_component(
        "ok" if agent_health["enabled"] else "warning",
        "Agent Gateway is enabled." if agent_health["enabled"] else "Agent Gateway is disabled until enabled in the Launcher.",
        {
            "mcpUrl": agent_health["mcpUrl"],
            "restUrl": agent_health["restUrl"],
            "pendingApprovalCount": agent_health["pendingApprovalCount"],
            "allowRoslynAdvanced": agent_health["allowRoslynAdvanced"],
        },
    )

    return components


def build_bootstrap_payload() -> dict[str, Any]:
    return build_dashboard_socket_payload(include_secret=True)


def build_dashboard_socket_payload(include_secret: bool = False) -> dict[str, Any]:
    if CURRENT_UNITY_STATUS is None:
        status = build_unity_status_snapshot()
    else:
        status = CURRENT_UNITY_STATUS
    health = build_full_health_payload()
    api_config = PROVIDER_CONFIGURATION.serialize_api_config(include_secret=include_secret)
    if not include_secret:
        health_api_config = health.get("apiConfig")
        if isinstance(health_api_config, dict):
            health_api_config.pop("api_key", None)
        api_config.pop("api_key", None)

    return {
        "health": health,
        "state": serialize_dashboard_state(),
        "config": {
            "configPath": str(CONFIG_PATH),
            "apiConfig": api_config,
            "effective": PROVIDER_CONFIGURATION.build_effective_model_summary(),
        },
        "projects": project_snapshot_payload(use_cache=True, refresh_async=False),
        "unityStatus": status,
    }


def serialize_dashboard_state() -> dict[str, Any]:
    return {
        "settingsPath": str(DASHBOARD_STATE.settings_path),
        "configPath": str(CONFIG_PATH),
        "selectedProjectPath": DASHBOARD_STATE.selected_project_path,
        "unityHost": DASHBOARD_STATE.unity_host,
        "unityPort": DASHBOARD_STATE.unity_port,
        "unityInstance": DASHBOARD_STATE.unity_instance,
        "unityEditorPath": DASHBOARD_STATE.unity_editor_path,
        "statusPushIntervalSeconds": DASHBOARD_STATE.status_push_interval_seconds,
        "currentAvatarName": DASHBOARD_RUNTIME.current_avatar_name,
        "currentAvatarPath": DASHBOARD_RUNTIME.current_avatar_path,
        "latestScreenshotUrl": DASHBOARD_RUNTIME.latest_screenshot_url,
    }


def build_project_snapshot_payload() -> dict[str, Any]:
    projects = discover_projects(DASHBOARD_STATE.project_roots, include_external=True)
    return {
        "selectedProjectPath": DASHBOARD_STATE.selected_project_path,
        "unityEditorPath": DASHBOARD_STATE.unity_editor_path,
        "projects": projects,
    }


def project_snapshot_list(value: Any) -> list[Any]:
    return _PROJECT_SNAPSHOT_SELECTION.project_snapshot_list(value)


def project_snapshot_cache_document(payload: dict[str, Any], *, updated_at: str, duration_ms: int) -> dict[str, Any]:
    return _PROJECT_SNAPSHOT_SELECTION.project_snapshot_cache_document(payload, updated_at=updated_at, duration_ms=duration_ms)


def load_project_snapshot_cache() -> dict[str, Any] | None:
    return _PROJECT_SNAPSHOT_SELECTION.load_project_snapshot_cache()


def project_snapshot_identity(project: dict[str, Any]) -> str:
    return _PROJECT_SNAPSHOT_SELECTION.project_snapshot_identity(project)


def project_snapshot_label(project: dict[str, Any]) -> dict[str, str]:
    return _PROJECT_SNAPSHOT_SELECTION.project_snapshot_label(project)


def project_snapshot_changes(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    return _PROJECT_SNAPSHOT_SELECTION.project_snapshot_changes(previous, current)


def annotate_project_snapshot(payload: dict[str, Any], *, status: str, cached: bool, error: str = "") -> dict[str, Any]:
    return _PROJECT_SNAPSHOT_SELECTION.annotate_project_snapshot(payload, status=status, cached=cached, error=error)


def empty_project_snapshot_payload(*, status: str = "pending") -> dict[str, Any]:
    return _PROJECT_SNAPSHOT_SELECTION.empty_project_snapshot_payload(status=status)


def _store_project_snapshot_cache(payload: dict[str, Any], *, started_at: str, duration_ms: int) -> None:
    return _PROJECT_SNAPSHOT_SELECTION._store_project_snapshot_cache(payload, started_at=started_at, duration_ms=duration_ms)


def refresh_project_snapshot_cache_sync() -> dict[str, Any]:
    return _PROJECT_SNAPSHOT_SELECTION.refresh_project_snapshot_cache_sync()


def schedule_project_snapshot_refresh(*, force: bool = False) -> bool:
    return _PROJECT_SNAPSHOT_SELECTION.schedule_project_snapshot_refresh(force=force)


def bootstrap_project_snapshot_payload() -> dict[str, Any]:
    return _PROJECT_SNAPSHOT_SELECTION.bootstrap_project_snapshot_payload()


def cached_project_snapshot_payload(*, refresh_async: bool = True, force_refresh: bool = False) -> dict[str, Any]:
    return _PROJECT_SNAPSHOT_SELECTION.cached_project_snapshot_payload(refresh_async=refresh_async, force_refresh=force_refresh)


def project_snapshot_payload(*, use_cache: bool = False, refresh_async: bool = True) -> dict[str, Any]:
    return _PROJECT_SNAPSHOT_SELECTION.project_snapshot_payload(use_cache=use_cache, refresh_async=refresh_async)


def discover_projects(project_roots: list[Path], include_external: bool = False) -> list[dict[str, Any]]:
    projects_by_key: dict[str, dict[str, Any]] = {}
    name_index: dict[str, str] = {}

    def project_key(path: str, name: str) -> str:
        normalized_path = normalize_path_string(path)
        if normalized_path:
            return normalized_path.casefold()
        return f"name:{name.casefold()}"

    def upsert_project(
        *,
        name: str,
        path: str = "",
        editor_version: str = "Unknown",
        source: str,
        active_instance: dict[str, Any] | None = None,
    ) -> None:
        normalized_path = normalize_path_string(path)
        display_name = name or (Path(normalized_path).name if normalized_path else "Active Unity Instance")
        key = project_key(normalized_path, display_name)
        existing_key = name_index.get(display_name.casefold())
        if not normalized_path and existing_key:
            key = existing_key
        project = projects_by_key.get(key)
        if project is None:
            project_path = Path(normalized_path) if normalized_path else None
            version_file = project_path / "ProjectSettings" / "ProjectVersion.txt" if project_path else None
            manifest_path = project_path / "Packages" / "manifest.json" if project_path else None
            plugin_path = project_path / "Assets" / "VRCForge" / "Editor" if project_path else None
            project = {
                "name": display_name,
                "path": normalized_path,
                "editorVersion": parse_editor_version(version_file) if version_file and version_file.exists() else editor_version,
                "hasVrcForge": bool(plugin_path and plugin_path.exists()),
                "hasUnityMcpPackage": bool(manifest_path and has_unity_mcp_dependency(manifest_path)),
                "selected": normalized_path == normalize_path_string(DASHBOARD_STATE.selected_project_path),
                "sources": [],
                "source": source,
                "activeMcp": False,
                "sessionId": "",
                "cliInstanceId": "",
                "unityVersion": "",
                "selectable": bool(normalized_path),
            }
            projects_by_key[key] = project
            name_index[display_name.casefold()] = key

        if source not in project["sources"]:
            project["sources"].append(source)
        project["source"] = project["sources"][0]
        if editor_version and project.get("editorVersion") in {"", "Unknown"}:
            project["editorVersion"] = editor_version
        if active_instance:
            project["activeMcp"] = True
            project["sessionId"] = active_instance.get("sessionId") or ""
            project["cliInstanceId"] = "project-scoped"
            project["unityVersion"] = active_instance.get("unityVersion") or project.get("editorVersion") or ""
            project["editorVersion"] = project["unityVersion"] or project["editorVersion"]

    for root in project_roots:
        if not root.exists():
            continue

        for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if not child.is_dir():
                continue

            version_file = child / "ProjectSettings" / "ProjectVersion.txt"
            if not version_file.exists():
                continue

            upsert_project(name=child.name, path=str(child), editor_version=parse_editor_version(version_file), source="configured-root")

    if include_external:
        for project_path in discover_vcc_projects():
            upsert_project(name=Path(project_path).name, path=project_path, source="vcc")

        for project_path in discover_alcom_projects():
            upsert_project(name=Path(project_path).name, path=project_path, source="alcom")

        for project in discover_unity_hub_projects():
            upsert_project(
                name=project.get("name") or Path(project.get("path") or "").name,
                path=project.get("path") or "",
                editor_version=project.get("editorVersion") or "Unknown",
                source="unity-hub",
            )

        for project in discover_running_unity_projects():
            upsert_project(
                name=project.get("name") or Path(project.get("path") or "").name,
                path=project.get("path") or "",
                editor_version=project.get("editorVersion") or "Unknown",
                source="running-unity",
            )

        if DASHBOARD_STATE.selected_project_path:
            selected = Path(DASHBOARD_STATE.selected_project_path)
            upsert_project(name=selected.name, path=str(selected), source="manual")

        for custom_path in load_project_prefs()["customPaths"]:
            candidate = Path(custom_path)
            if candidate.is_dir():
                upsert_project(name=candidate.name, path=str(candidate), source="custom")

        status = CURRENT_UNITY_STATUS
        if status is None:
            try:
                settings = load_dashboard_settings(ConnectionRequest(settings_path=str(DASHBOARD_STATE.settings_path)))
                status = build_unity_status_snapshot(settings)
            except Exception:  # noqa: BLE001
                status = None
        for instance in (status or {}).get("instances") or []:
            upsert_project(
                name=instance.get("project") or instance.get("projectName") or instance.get("sessionId") or "Active Unity Instance",
                path=instance.get("projectPath") or "",
                editor_version=instance.get("unityVersion") or "Unknown",
                source="active-mcp",
                active_instance=instance,
            )

    return sorted(
        projects_by_key.values(),
        key=lambda item: (not item.get("activeMcp"), str(item.get("name") or "").casefold()),
    )


def discover_vcc_projects() -> list[str]:
    return _PROJECT_CATALOG_DISCOVERY._impl_discover_vcc_projects()


def discover_alcom_projects() -> list[str]:
    return _PROJECT_CATALOG_DISCOVERY._impl_discover_alcom_projects()


def discover_projects_from_settings_files(candidates: list[Path]) -> list[str]:
    return _PROJECT_CATALOG_DISCOVERY._impl_discover_projects_from_settings_files(candidates)


def extract_project_paths_from_json(payload: Any) -> list[str]:
    return _PROJECT_CATALOG_DISCOVERY._impl_extract_project_paths_from_json(payload)


def extract_windows_paths_from_text(value: str) -> list[str]:
    return _PROJECT_CATALOG_DISCOVERY._impl_extract_windows_paths_from_text(value)


def discover_unity_hub_projects() -> list[dict[str, str]]:
    return _PROJECT_CATALOG_DISCOVERY._impl_discover_unity_hub_projects()


def discover_unity_hub_project_roots() -> list[Path]:
    return _PROJECT_CATALOG_DISCOVERY._impl_discover_unity_hub_project_roots()


def is_unity_project_path(path: Path) -> bool:
    return (path / "Assets").exists() and (path / "Packages").exists() and (path / "ProjectSettings" / "ProjectVersion.txt").exists()


def parse_editor_version(version_file: Path) -> str:
    try:
        for line in version_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("m_EditorVersion:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass

    return "Unknown"


def has_unity_mcp_dependency(manifest_path: Path) -> bool:
    # Core ships inside Assets/VRCForge; Packages/manifest.json is never a
    # valid substitute for the project-scoped VRCForge descriptor.
    return vrcforge_mcp_core_installed(manifest_path.parent.parent)


def vrcforge_mcp_core_installed(project_root: Path) -> bool:
    core_root = project_root / "Assets" / "VRCForge" / "Core" / "MCP"
    return all(
        (core_root / name).is_file()
        for name in (
            "VRCForgeCommandAttribute.cs",
            "VRCForgeInputAttribute.cs",
            "VRCForgeToolRegistry.cs",
            "VRCForgeToolResult.cs",
        )
    ) and (project_root / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpCoreServer.cs").is_file()


def resolve_target_project(project_path: str | None) -> str:
    candidate = project_path or DASHBOARD_STATE.selected_project_path
    if not candidate:
        raise HTTPException(status_code=400, detail="No Unity project is selected.")

    resolved = resolve_local_path(candidate)
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"Unity project path does not exist: {resolved}")

    return normalize_path_string(str(resolved))


def resolve_local_path(value: str | Path) -> Path:
    path = value if isinstance(value, Path) else Path(value)
    return path if path.is_absolute() else (ROOT_DIR / path).resolve()


def normalize_path_string(value: str) -> str:
    if not str(value or "").strip():
        return ""
    return str(Path(value)).replace("\\", "/")


def canonical_selected_project_path(value: Any) -> str:
    return _PROJECT_SNAPSHOT_SELECTION.canonical_selected_project_path(value)


def load_persisted_selected_project_path() -> str:
    return _PROJECT_SNAPSHOT_SELECTION.load_persisted_selected_project_path()


def persist_selected_project_path(value: Any) -> str:
    return _PROJECT_SNAPSHOT_SELECTION.persist_selected_project_path(value)


def _review_saved_project_category_approval(approval: dict[str, Any]) -> str:
    config = PROVIDER_CONFIGURATION.current_api_config()
    return review_saved_project_category_approval(
        approval,
        model=config.model,
        request_text=lambda prompt: PROVIDER_TESTS.probe_text(config, prompt, structured=True),
    )


def resolve_vertex_project_location(value: str) -> tuple[str, str]:
    settings = load_runtime_settings_safely(
        RUNTIME_SETTINGS_PATH,
        llm_override={
            "provider": "vertexai",
            "api_key": "",
            "base_url": value,
            "model": get_provider_defaults("vertexai")["model"],
        },
        loader=load_settings,
    )
    from vrchat_blendshape_agent import resolve_vertex_ai_project_location

    return resolve_vertex_ai_project_location(settings.llm_base_url)


def provider_auth_label(provider: str) -> str:
    provider = normalize_provider_name(provider)
    if provider == "anthropic":
        return "x-api-key"
    if provider == "gemini":
        return "API key"
    if provider == "ollama":
        return "optional"
    if provider == "vertexai":
        return "Google ADC"
    return "Authorization: Bearer"


def load_initial_dashboard_state() -> DashboardState:
    settings_path = RUNTIME_SETTINGS_PATH
    settings = load_runtime_settings_safely(
        settings_path,
        llm_override=PROVIDER_CONFIGURATION.serialize_api_config(include_secret=True),
        loader=load_settings,
    )
    raw = read_runtime_settings_document_safely(settings_path)
    raw_dashboard_settings = raw.get("dashboard")
    dashboard_settings = raw_dashboard_settings if isinstance(raw_dashboard_settings, dict) else {}

    raw_project_roots = dashboard_settings.get("project_roots", [])
    project_roots: list[Path] = []
    if isinstance(raw_project_roots, list):
        for value in raw_project_roots:
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                project_roots.append(Path(value))
            except (OSError, ValueError):
                continue
    raw_unity_editor_path = dashboard_settings.get("unity_editor_path", "")
    unity_editor_path = str(raw_unity_editor_path).strip() if isinstance(raw_unity_editor_path, (str, Path)) else ""
    try:
        status_push_interval_seconds = float(dashboard_settings.get("status_push_interval_seconds", 2.5))
    except (TypeError, ValueError):
        status_push_interval_seconds = 2.5
    if not math.isfinite(status_push_interval_seconds) or not 0.25 <= status_push_interval_seconds <= 300:
        status_push_interval_seconds = 2.5

    selected_project_path = load_persisted_selected_project_path()
    unity_instance = Path(selected_project_path).name if selected_project_path else settings.unity_mcp_instance

    return DashboardState(
        settings_path=settings_path,
        project_roots=project_roots,
        unity_editor_path=unity_editor_path,
        status_push_interval_seconds=status_push_interval_seconds,
        selected_project_path=selected_project_path,
        unity_host=settings.unity_mcp_host,
        unity_port=settings.unity_mcp_port,
        unity_instance=unity_instance,
    )


def authenticate_agent_request(request: Request, allow_disabled: bool = False):
    try:
        return AGENT_GATEWAY.authenticate(
            headers=dict(request.headers),
            query_params=dict(request.query_params),
            client_host=request.client.host if request.client else "",
            allow_disabled=allow_disabled,
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def authenticate_agent_approval_request(request: Request):
    try:
        return AGENT_GATEWAY.authenticate_approval(
            headers=dict(request.headers),
            query_params=dict(request.query_params),
            client_host=request.client.host if request.client else "",
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


APP_AUTH_PREFIXES = ("/api",)
APP_AUTH_EXEMPT_PATHS = {"/api/health", "/api/app/session", "/api/app/session-challenge"}
APP_AUTH_EXEMPT_PREFIXES = ("/api/agent",)
APP_LOOPBACK_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def app_route_requires_auth(request: Request) -> bool:
    path = request.url.path
    if not any(path == prefix or path.startswith(prefix + "/") for prefix in APP_AUTH_PREFIXES):
        return False
    if path in APP_AUTH_EXEMPT_PATHS:
        return False
    if any(path == prefix or path.startswith(prefix + "/") for prefix in APP_AUTH_EXEMPT_PREFIXES):
        return False
    return True


def artifact_route_requires_auth(request: Request) -> bool:
    path = request.url.path
    return path == "/artifacts" or path.startswith("/artifacts/") or path == "/runtime-artifacts" or path.startswith("/runtime-artifacts/")


def is_cors_preflight_request(request: Request) -> bool:
    return (
        request.method.upper() == "OPTIONS"
        and bool(request.headers.get("origin"))
        and bool(request.headers.get("access-control-request-method"))
    )


def authenticate_app_request(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    origin = request.headers.get("origin", "").strip()
    supplied = extract_bearer_token(request)
    validate_app_request_auth(client_host=client_host, origin=origin, supplied_token=supplied)


def attach_dashboard_session_cookie(response: FileResponse) -> None:
    if not APP_SESSION_TOKEN:
        return
    response.set_cookie(
        APP_DASHBOARD_SESSION_COOKIE,
        APP_SESSION_TOKEN,
        httponly=True,
        samesite="strict",
        max_age=3600,
        path="/",
    )


def validate_app_session_handshake_request(request: Request, *, dev_only: bool) -> None:
    if dev_only and PORTABLE_MODE:
        raise HTTPException(status_code=404, detail="Development session handshake is unavailable in packaged mode.")
    client_host = request.client.host if request.client else ""
    origin = request.headers.get("origin", "").strip()
    if client_host not in APP_LOOPBACK_CLIENT_HOSTS:
        raise HTTPException(status_code=403, detail="App session handshake only accepts loopback clients.")
    if origin not in APP_ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="App session handshake origin is not allowed.")
    if not APP_SESSION_TOKEN:
        raise HTTPException(status_code=503, detail="App session token is unavailable.")


def authenticate_artifact_request(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    origin = request.headers.get("origin", "").strip()
    if client_host not in APP_LOOPBACK_CLIENT_HOSTS:
        raise HTTPException(status_code=403, detail="Artifact access only accepts loopback clients.")
    if origin and origin not in APP_ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="Artifact origin is not allowed.")
    if not APP_AUTH_REQUIRED:
        return
    runtime_artifact = request.url.path.startswith("/runtime-artifacts/")
    if runtime_artifact:
        relative = request.url.path[len("/runtime-artifacts/") :]
    else:
        relative = request.url.path[len("/artifacts/") :] if request.url.path.startswith("/artifacts/") else ""
    try:
        relative = normalize_artifact_relative_path(relative)
        expires = int(str(request.query_params.get("artifact_expires") or "0"))
        supplied = str(request.query_params.get("artifact_sig") or "")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Artifact URL is missing or invalid.") from exc
    now = int(time.time())
    if expires < now or expires > now + ARTIFACT_URL_MAX_FUTURE_SECONDS:
        raise HTTPException(status_code=401, detail="Artifact URL has expired.")
    expected = runtime_artifact_signature(relative, expires) if runtime_artifact else artifact_signature(relative, expires)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Artifact URL signature is invalid.")


def validate_app_request_auth(client_host: str, origin: str, supplied_token: str) -> None:
    if client_host not in APP_LOOPBACK_CLIENT_HOSTS:
        raise HTTPException(status_code=403, detail="App API only accepts loopback clients.")
    if origin and origin not in APP_ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="App API origin is not allowed.")
    if not APP_AUTH_REQUIRED:
        return
    if not supplied_token or not hmac.compare_digest(supplied_token, APP_SESSION_TOKEN):
        raise HTTPException(status_code=401, detail="App session token is missing or invalid.")


def extract_bearer_token(request: Request) -> str:
    return extract_bearer_token_from_headers(request.headers)


def extract_bearer_token_from_headers(headers: Any) -> str:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def extract_websocket_auth_token(headers: Any) -> str:
    bearer = extract_bearer_token_from_headers(headers)
    if bearer:
        return bearer
    raw_cookie = str(headers.get("cookie") or "")
    if not raw_cookie:
        return ""
    try:
        cookie = SimpleCookie()
        cookie.load(raw_cookie)
    except Exception:  # noqa: BLE001
        return ""
    morsel = cookie.get(APP_DASHBOARD_SESSION_COOKIE)
    return morsel.value if morsel is not None else ""


def build_agent_connection_request(params: dict[str, Any]) -> ConnectionRequest:
    return ConnectionRequest(**params)


def build_agent_dashboard_request(params: dict[str, Any]) -> DashboardRequest:
    data = dict(params)
    data.setdefault("settings_path", runtime_settings_path())
    data.setdefault("source_mode", "unity_live_export")
    data.setdefault("mock_execute", False)
    data.setdefault("save_artifacts", True)
    return DashboardRequest(**data)


def build_agent_shader_request(params: dict[str, Any]) -> ShaderMaterialPlanRequest:
    data = dict(params)
    data.setdefault("settings_path", runtime_settings_path())
    data.setdefault("source_mode", "unity_live_export")
    data.setdefault("mock_execute", False)
    return ShaderMaterialPlanRequest(**data)


def _preview_agent_blendshape_adapter(params: dict[str, Any]) -> dict[str, Any]:
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or params.get("avatar") or "").strip()
    adjustments = params.get("adjustments") or []
    if not avatar_path:
        raise RuntimeError("avatar_path is required for blendshape apply preview.")
    if not isinstance(adjustments, list):
        raise RuntimeError("adjustments must be a list.")
    payload = render_manual_blendshape_payload_json(avatar_path, adjustments)
    return {
        "ok": True,
        "targetTool": "vrcforge_apply_blendshapes",
        "avatarPath": avatar_path,
        "adjustmentCount": len(adjustments),
        "applyPayload": payload,
    }


def preview_agent_shader_apply(params: dict[str, Any]) -> dict[str, Any]:
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or params.get("avatar") or "").strip()
    changes = params.get("changes") or []
    if not isinstance(changes, list):
        raise RuntimeError("changes must be a list.")
    return {
        "ok": True,
        "targetTool": "vrcforge_apply_shader_tuning",
        "avatarPath": avatar_path,
        "changeCount": len(changes),
        "applyPayload": {
            "tool": "vrc_apply_material_tuning",
            "params": {
                "avatarPath": avatar_path,
                "changes": changes,
                "saveAssets": True,
            },
        },
    }


def request_agent_restore_last_backup(params: dict[str, Any]) -> dict[str, Any]:
    kind = str(params.get("kind") or params.get("restoreKind") or "shader").strip().lower()
    arguments = dict(params)
    if kind in {"shader", "material", "materials"}:
        target_tool = "vrcforge_restore_shader_tuning"
    elif kind in {"blendshape", "blendshapes", "face"}:
        target_tool = "vrcforge_undo_blendshapes"
    else:
        raise RuntimeError("kind must be shader or blendshape.")
    arguments.pop("kind", None)
    arguments.pop("restoreKind", None)
    return AGENT_GATEWAY.create_apply_request(
        {
            "target_tool": target_tool,
            "arguments": arguments,
            "reason": f"Restore last {kind} backup requested by external agent.",
            "preview": {"kind": kind, "targetTool": target_tool},
        }
    )


def read_agent_compile_errors(params: dict[str, Any]) -> dict[str, Any]:
    settings = load_dashboard_settings(build_agent_connection_request(params))
    arguments: dict[str, Any] = {}
    if params.get("maxErrors") is not None:
        arguments["maxErrors"] = int(params["maxErrors"])
    if params.get("includeConsoleFallback") is not None:
        arguments["includeConsoleFallback"] = bool(params["includeConsoleFallback"])
    result = invoke_unity_mcp(settings, "vrc_get_compile_errors", arguments)
    return {"ok": True, "result": serialize_result(result)}


def prepare_unity_checkpoint_sync(project_root: Path) -> dict[str, Any]:
    live_connection = globals().get("PRIMITIVE_BASIS_LIVE_CONNECTION")
    if isinstance(live_connection, PrimitiveBasisLiveUnityConnection):
        return live_connection.prepare_checkpoint(project_root)
    settings = load_dashboard_settings(build_agent_connection_request({}))
    settings.unity_mcp_timeout_seconds = max(int(settings.unity_mcp_timeout_seconds or 30), 180)
    result = invoke_unity_mcp(
        settings,
        "vrc_prepare_checkpoint",
        {"projectPath": str(project_root)},
        execution_context={"lane": "app_safety_control"},
        preserve_tool_error=True,
    )
    return normalize_unity_checkpoint_result(result, project_root)


CHECKPOINT_RELOAD_CALL_TIMEOUT_SECONDS = 20
CHECKPOINT_RELOAD_READY_TIMEOUT_SECONDS = 70
CHECKPOINT_RELOAD_READY_POLL_SECONDS = 0.5
CHECKPOINT_RELOAD_CONNECTION_CLOSED_ERROR = "Unity MCP Core connection closed unexpectedly."


def prepare_unity_checkpoint_restore_sync(project_root: Path) -> dict[str, Any]:
    live_connection = globals().get("PRIMITIVE_BASIS_LIVE_CONNECTION")
    if isinstance(live_connection, PrimitiveBasisLiveUnityConnection):
        return live_connection.prepare_restore_checkpoint(project_root)
    settings = load_dashboard_settings(build_agent_connection_request({}))
    settings.unity_mcp_timeout_seconds = min(
        max(int(settings.unity_mcp_timeout_seconds or 30), 1),
        CHECKPOINT_RELOAD_CALL_TIMEOUT_SECONDS,
    )
    settings.unity_mcp_retries = 1
    result = invoke_unity_mcp(
        settings,
        "vrc_reload_after_checkpoint_restore",
        {
            "projectPath": str(project_root),
            "phase": "prepare_restore",
        },
        execution_context={"lane": "app_safety_control"},
        preserve_tool_error=True,
    )
    return normalize_unity_checkpoint_result(result, project_root)


def _checkpoint_reload_connection_closed(error: BaseException) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if (
            isinstance(current, UnityMcpCoreError)
            and str(current) == CHECKPOINT_RELOAD_CONNECTION_CLOSED_ERROR
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _wait_for_reloaded_unity_core(
    project_root: Path,
    previous_connection: Any,
    *,
    timeout_seconds: float = CHECKPOINT_RELOAD_READY_TIMEOUT_SECONDS,
    poll_seconds: float = CHECKPOINT_RELOAD_READY_POLL_SECONDS,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        try:
            current = load_unity_mcp_core_connection(project_root)
            if (
                current.process_id == previous_connection.process_id
                and current.project_hash == previous_connection.project_hash
                and current.instance_id != previous_connection.instance_id
            ):
                remaining = max(1.0, deadline - time.monotonic())
                tools = UnityMcpCoreClient(
                    project_root,
                    timeout_seconds=min(3.0, remaining),
                ).list_tools(exposure_layer="execution")
                tool_names = {
                    str(item.get("name") or "")
                    for item in tools
                    if isinstance(item, dict)
                }
                if (
                    len(tools) != len(REQUIRED_VRCFORGE_UNITY_TOOLS)
                    or tool_names != set(REQUIRED_VRCFORGE_UNITY_TOOLS)
                ):
                    raise UnityMcpCoreError("Unity MCP Core tool contract is not ready.")
                return {
                    "ok": True,
                    "projectPath": str(project_root),
                    "coreReady": True,
                    "domainReloadObserved": True,
                }
        except (OSError, UnityMcpCoreError):
            pass
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(max(0.0, poll_seconds), remaining))
    return {
        "ok": False,
        "error": "Unity MCP Core did not become ready after the checkpoint reload connection closed.",
    }


def reload_unity_checkpoint_sync(
    project_root: Path,
    restore_prepare: dict[str, Any] | None = None,
) -> dict[str, Any]:
    live_connection = globals().get("PRIMITIVE_BASIS_LIVE_CONNECTION")
    if isinstance(live_connection, PrimitiveBasisLiveUnityConnection):
        return live_connection.reload_checkpoint(project_root, restore_prepare)
    try:
        previous_connection = load_unity_mcp_core_connection(project_root)
    except UnityMcpCoreError:
        previous_connection = None
    settings = load_dashboard_settings(build_agent_connection_request({}))
    settings.unity_mcp_timeout_seconds = min(
        max(int(settings.unity_mcp_timeout_seconds or 30), 1),
        CHECKPOINT_RELOAD_CALL_TIMEOUT_SECONDS,
    )
    settings.unity_mcp_retries = 1
    try:
        prepared_scenes_raw = ensure_dict(restore_prepare).get("scenes")
        prepared_scenes = normalize_string_list(
            prepared_scenes_raw if isinstance(prepared_scenes_raw, list) else []
        )
        active_scene_path = str(ensure_dict(restore_prepare).get("activeScenePath") or "").strip()
        result = invoke_unity_mcp(
            settings,
            "vrc_reload_after_checkpoint_restore",
            {
                "projectPath": str(project_root),
                "phase": "reload",
                "scenePaths": prepared_scenes,
                "activeScenePath": active_scene_path,
            },
            execution_context={"lane": "app_safety_control"},
            preserve_tool_error=True,
        )
    except UnityMcpError as exc:
        if previous_connection is None or not _checkpoint_reload_connection_closed(exc):
            return {
                "ok": False,
                "error": "Unity did not confirm the checkpoint reload command.",
            }
        return _wait_for_reloaded_unity_core(project_root, previous_connection)
    return normalize_unity_checkpoint_result(result, project_root)


def normalize_unity_checkpoint_result(
    result: McpResult,
    project_root: Path,
) -> dict[str, Any]:
    envelope = result.payload if isinstance(result.payload, dict) else {}
    structured = envelope.get("structuredContent")
    structured = structured if isinstance(structured, dict) else {}
    data = structured.get("data")
    data = dict(data) if isinstance(data, dict) else {}
    rejected = bool(
        result.exit_code != 0
        or envelope.get("isError") is True
        or structured.get("success") is False
    )
    if rejected:
        code = str(structured.get("code") or "unity_checkpoint_prepare_failed")
        message = str(
            data.get("message")
            or structured.get("error")
            or "Unity rejected checkpoint preparation."
        )
        return {
            **data,
            "ok": False,
            "blocking": bool(data.get("blocking", True)),
            "code": code,
            "error": message,
            "projectPath": str(project_root),
            "result": serialize_result(result),
        }
    return {
        **data,
        "ok": True,
        "projectPath": str(project_root),
        "result": serialize_result(result),
    }


def unity_mcp_write_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = dict(params or {})
    tool_name = str(params.get("tool_name") or params.get("toolName") or "").strip()
    if not tool_name:
        return {"ok": False, "error": "toolName is required."}
    if tool_name in {"vrc_prepare_checkpoint", "vrc_reload_after_checkpoint_restore"}:
        return {"ok": False, "error": f"Internal checkpoint tool cannot be invoked through the generic write wrapper: {tool_name}"}
    if tool_name in {"vrc_execute_roslyn", "execute_code"}:
        return {"ok": False, "error": f"Dynamic code execution is not supported by VRCForge static Unity tools: {tool_name}"}
    if tool_name not in VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST:
        return {"ok": False, "error": f"Unity MCP write tool is not in the VRCForge static write allowlist: {tool_name}"}
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else params.get("params")
    if not isinstance(arguments, dict):
        arguments = {}
    strict_result = authoritative_unity_write_has_strict_result(params)
    settings = load_dashboard_settings(build_agent_connection_request(params))
    result = invoke_unity_mcp(
        settings,
        tool_name,
        arguments,
    )
    if strict_result and result.exit_code != 0:
        return {
            "ok": False,
            "toolName": tool_name,
            "error": "The authoritative Unity write transport failed.",
        }
    if result.exit_code == 0:
        try:
            validated_payload = validate_authoritative_unity_write_result(
                params,
                extract_tool_result_payload(result),
            )
        except AuthoritativeUnityWriteError as exc:
            return {"ok": False, "toolName": tool_name, "error": str(exc)}
        if strict_result:
            return {
                "ok": True,
                "toolName": tool_name,
                "result": {
                    "exitCode": 0,
                    "stdout": "",
                    "stderr": "",
                    "payload": {"data": validated_payload},
                },
            }
    return {"ok": result.exit_code == 0, "toolName": tool_name, "result": serialize_result(result)}


def export_vrm_sync(params: dict[str, Any]) -> dict[str, Any]:
    export_arguments = dict(params or {})
    payload = {"toolName": "vrc_export_vrm", "arguments": export_arguments}
    return unity_mcp_write_sync(payload)


def build_unity_mcp_write_execution_plan(
    params: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    request = dict(params or {})
    request.pop("_vrcforge_approved_execution", None)
    tool_name = str(request.get("tool_name") or request.get("toolName") or "").strip()
    if not tool_name or tool_name not in VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST:
        raise ValueError("Unity MCP write tool is not in the static write allowlist.")
    arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else request.get("params")
    return [(tool_name, dict(arguments) if isinstance(arguments, dict) else {})]


def build_export_vrm_execution_plan(
    params: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    arguments = dict(params or {})
    arguments.pop("_vrcforge_approved_execution", None)
    return [("vrc_export_vrm", arguments)]


def prepare_unity_mcp_write_request(
    params: dict[str, Any],
    caller_preview: Any,
) -> tuple[dict[str, Any], Any]:
    try:
        return prepare_authoritative_unity_write(
            params or {},
            caller_preview,
            lambda tool_name, preview_arguments: _invoke_authoritative_unity_preview(
                params or {},
                tool_name,
                preview_arguments,
            ),
        )
    except AuthoritativeUnityWriteError as exc:
        raise AgentGatewayError(str(exc), status_code=exc.status_code) from exc


def unity_mcp_manual_approval_reason(arguments: dict[str, Any], _preview: Any) -> str:
    nested_tool = str(arguments.get("toolName") or arguments.get("tool_name") or "").strip()
    if nested_tool in {PARAMETER_BIT_PACKING_TOOL, ATOMIC_REFERENCE_RENAME_TOOL}:
        return "This operation requires explicit user approval in every permission mode."
    return ""


def prepare_authoritative_unity_checkpoint_sync(
    project_root: Path,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    nested_tool = str(arguments.get("toolName") or arguments.get("tool_name") or "").strip()
    if nested_tool not in {
        PARAMETER_BIT_PACKING_TOOL,
        ATOMIC_REFERENCE_RENAME_TOOL,
        CONSTRAINT_SOURCE_TOOL,
        DUPLICATE_SCENE_OBJECT_TOOL,
        SAVE_SCENE_OBJECT_AS_PREFAB_TOOL,
    }:
        return prepare_unity_checkpoint_sync(project_root)
    approved_write_arguments = copy.deepcopy(arguments)
    approved_write_arguments.pop("_vrcforge_user_constraints", None)
    try:
        refreshed_arguments, _refreshed_preview = prepare_unity_mcp_write_request(
            approved_write_arguments,
            None,
        )
    except AgentGatewayError:
        return {
            "ok": False,
            "error": "The approved Unity state could not be revalidated before checkpointing.",
        }
    if refreshed_arguments != approved_write_arguments:
        return {
            "ok": False,
            "error": "The approved Unity state changed before checkpointing.",
        }
    if Path(str(refreshed_arguments.get("projectPath") or "")).resolve() != project_root.resolve():
        return {
            "ok": False,
            "error": "The approved Unity project changed before checkpointing.",
        }
    return {
        "ok": True,
        "projectPath": str(project_root),
        "toolName": nested_tool,
        "mode": "read_only_authoritative_revalidation",
        "canonicalRevalidated": True,
    }


def _invoke_authoritative_unity_preview(
    params: dict[str, Any],
    tool_name: str,
    preview_arguments: dict[str, Any],
) -> Any:
    settings = load_dashboard_settings(build_agent_connection_request(params))
    result = invoke_unity_mcp(
        settings,
        tool_name,
        preview_arguments,
        execution_context={"lane": "app_preview"},
    )
    if result.exit_code != 0:
        raise RuntimeError("Authoritative Unity preview failed.")
    return extract_tool_result_payload(result)


def preview_material_shader_assignment_sync(params: dict[str, Any]) -> dict[str, Any]:
    _arguments, preview = prepare_unity_mcp_write_request(
        build_material_shader_wrapper_arguments(params or {}),
        None,
    )
    return {"ok": True, "preview": preview}


def preview_scene_object_copy_sync(params: dict[str, Any], tool_name: str) -> dict[str, Any]:
    _arguments, preview = prepare_unity_mcp_write_request(
        build_scene_object_copy_wrapper_arguments(params or {}, tool_name),
        None,
    )
    return {"ok": True, "preview": preview}


def preview_texture_import_settings_sync(params: dict[str, Any]) -> dict[str, Any]:
    _arguments, preview = prepare_unity_mcp_write_request(
        build_texture_import_settings_wrapper_arguments(params or {}),
        None,
    )
    return {"ok": True, "preview": preview}


def preview_constraint_sources_sync(params: dict[str, Any]) -> dict[str, Any]:
    _arguments, preview = prepare_unity_mcp_write_request(
        build_constraint_source_wrapper_arguments(params or {}),
        None,
    )
    return {"ok": True, "preview": preview}


def preview_component_feature_sync(params: dict[str, Any]) -> dict[str, Any]:
    _arguments, preview = prepare_unity_mcp_write_request(
        build_component_feature_wrapper_arguments(params or {}),
        None,
    )
    return {"ok": True, "preview": preview}


def preview_parameter_bit_packing_sync(params: dict[str, Any]) -> dict[str, Any]:
    _arguments, preview = prepare_unity_mcp_write_request(
        build_parameter_bit_packing_wrapper_arguments(params or {}),
        None,
    )
    return {"ok": True, "preview": preview}


def preview_atomic_reference_rename_sync(params: dict[str, Any]) -> dict[str, Any]:
    _arguments, preview = prepare_unity_mcp_write_request(
        build_atomic_reference_rename_wrapper_arguments(params or {}),
        None,
    )
    return {"ok": True, "preview": preview}


ADDON_FRAMEWORKS: dict[str, dict[str, Any]] = {
    "modular_avatar": {
        "label": "Modular Avatar",
        "packageIds": ["nadena.dev.modular-avatar"],
        "componentPrefixes": ["ModularAvatar"],
        "hint": (
            "Modular Avatar merges armatures, animators, menus, and parameters non-destructively "
            "at avatar build time. Treat its components as the source of truth and avoid editing "
            "merged FX output directly."
        ),
    },
    "vrcfury": {
        "label": "VRCFury",
        "packageIds": ["com.vrcfury.vrcfury"],
        "componentPrefixes": ["VRCFury"],
        "hint": (
            "VRCFury features are stored as build-time components on the avatar and are applied "
            "non-destructively on upload or play. Plan edits against the components, not against "
            "generated controllers."
        ),
    },
}


def scan_addon_framework_sync(framework: str, params: dict[str, Any]) -> dict[str, Any]:
    spec = ADDON_FRAMEWORKS[framework]
    prefixes = [str(prefix).lower() for prefix in spec["componentPrefixes"]]
    project_value = str(params.get("project_path") or params.get("projectPath") or DASHBOARD_STATE.selected_project_path or "").strip()
    project_path = Path(project_value) if project_value else None
    package_info = PACKAGE_DETECTION.detect(project_path, list(spec["packageIds"]))
    avatar_path = str(
        params.get("source_avatar_path")
        or params.get("sourceAvatarPath")
        or params.get("avatar_path")
        or params.get("avatarPath")
        or ""
    ).strip()

    unity_state: dict[str, Any] = {"scanned": False}
    matches: list[dict[str, Any]] = []
    if params.get("skip_unity") is not True and params.get("skipUnity") is not True:
        try:
            settings = load_dashboard_settings(build_agent_connection_request(params))
            payload = extract_tool_result_payload(
                invoke_unity_mcp(
                    settings,
                    "vrc_scan_avatar_items",
                    {"avatarPath": avatar_path, "outputPath": "", "maxItems": 2000, "refreshAssets": False},
                )
            )
            items = payload.get("items") if isinstance(payload, dict) else None
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                component_types = [str(value) for value in (item.get("component_types") or [])]
                hits = sorted({ctype for ctype in component_types if any(ctype.lower().startswith(prefix) for prefix in prefixes)})
                if hits:
                    matches.append(
                        {
                            "avatarPath": item.get("avatar_path") or "",
                            "objectPath": item.get("object_path") or "",
                            "components": hits,
                            "activeInHierarchy": item.get("active_in_hierarchy"),
                        }
                    )
            unity_state = {"scanned": True, "itemCount": len(items or []), "matchCount": len(matches)}
        except (RuntimeError, UnityMcpError) as exc:
            unity_state = {"scanned": False, "error": str(exc)[:240]}

    if package_info.get("installed"):
        package_text = "installed" + (f" {package_info['version']}" if package_info.get("version") else "")
    else:
        package_text = "not detected"
    if unity_state.get("scanned"):
        component_text = f"{len(matches)} component carrier(s) found on scanned avatars"
    else:
        component_text = "Unity component scan unavailable"
    summary = f"{spec['label']}: package {package_text}; {component_text}."
    emit_log(
        "info",
        "addon",
        f"{spec['label']} scan finished.",
        {"framework": framework, "installed": package_info.get("installed"), "matchCount": len(matches), "scanned": unity_state.get("scanned")},
    )
    return {
        "ok": True,
        "framework": framework,
        "label": spec["label"],
        "projectPath": project_value,
        "package": package_info,
        "components": matches,
        "componentCount": len(matches),
        "unity": unity_state,
        "summary": summary,
        "hint": spec["hint"],
    }



def run_unity_artifact_scan_sync(
    params: dict[str, Any],
    tool_name: str,
    prefix: str,
    unity_params: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    settings = load_dashboard_settings(build_agent_connection_request(params))
    avatar_path = str(
        params.get("source_avatar_path")
        or params.get("sourceAvatarPath")
        or params.get("avatar_path")
        or params.get("avatarPath")
        or ""
    ).strip()
    output_path = build_dashboard_artifact_path(prefix, avatar_path, "json")
    # Never let a previous scan for the same avatar path masquerade as the
    # current Unity response when the tool fails to refresh its output file.
    output_path.unlink(missing_ok=True)
    merged: dict[str, Any] = {"avatarPath": avatar_path, "outputPath": ""}
    merged.update(unity_params)
    result = invoke_unity_mcp(settings, tool_name, merged)
    if output_path.exists():
        payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        payload.setdefault("jsonPath", str(output_path))
    else:
        payload = extract_tool_result_payload(result)
    payload = ensure_dict_payload(payload, label)
    if not output_path.exists():
        write_dashboard_json_artifact(output_path, payload)
    payload.setdefault("ok", True)
    return payload


def scan_fx_animator_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    return run_unity_artifact_scan_sync(
        params,
        "vrc_scan_fx_animator",
        "fx_animator",
        {
            "controllerPath": str(params.get("controller_path") or params.get("controllerPath") or "").strip(),
            "refreshAssets": False,
        },
        "FX animator scan",
    )


def scan_animation_bindings_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    clip_paths = params.get("clip_paths") or params.get("clipPaths") or []
    return run_unity_artifact_scan_sync(
        params,
        "vrc_scan_animation_bindings",
        "animation_bindings",
        {
            "controllerPath": str(params.get("controller_path") or params.get("controllerPath") or "").strip(),
            "clipPaths": [str(item) for item in clip_paths if str(item).strip()],
            "includeAllProjectClips": bool(params.get("include_all_project_clips") or params.get("includeAllProjectClips") or False),
            "maxClips": int(params.get("max_clips") or params.get("maxClips") or 300),
            "refreshAssets": False,
        },
        "animation binding scan",
    )


def _coerce_gateway_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def build_create_wardrobe_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "parameterName": str(
            params.get("parameter_name")
            or params.get("parameterName")
            or params.get("wardrobe_parameter")
            or params.get("wardrobeParameter")
            or "Clothes"
        ).strip(),
        "preview": preview,
    }
    menu_name = str(params.get("menu_name") or params.get("menuName") or params.get("sub_menu_name") or params.get("subMenuName") or "").strip()
    if menu_name:
        request["menuName"] = menu_name
    default_control_name = str(params.get("default_control_name") or params.get("defaultControlName") or "").strip()
    if default_control_name:
        request["defaultControlName"] = default_control_name
    layer_name = str(params.get("layer_name") or params.get("layerName") or "").strip()
    if layer_name:
        request["layerName"] = layer_name
    asset_dir = str(params.get("asset_dir") or params.get("assetDir") or params.get("clip_output_dir") or params.get("clipOutputDir") or "").strip()
    if asset_dir:
        request["assetDir"] = asset_dir
    if params.get("write_defaults") is not None or params.get("writeDefaults") is not None:
        request["writeDefaults"] = _coerce_gateway_bool(params.get("write_defaults", params.get("writeDefaults")), True)
    if params.get("saved") is not None:
        request["saved"] = _coerce_gateway_bool(params.get("saved"), True)
    if params.get("network_synced") is not None or params.get("networkSynced") is not None:
        request["networkSynced"] = _coerce_gateway_bool(params.get("network_synced", params.get("networkSynced")), True)
    return request


def build_ensure_expression_parameter_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "parameterName": str(params.get("parameter_name") or params.get("parameterName") or "").strip(),
        "valueType": str(params.get("value_type") or params.get("valueType") or "Int").strip() or "Int",
        "defaultValue": float(params.get("default_value", params.get("defaultValue", 0)) or 0),
        "saved": _coerce_gateway_bool(params.get("saved"), True),
        "networkSynced": _coerce_gateway_bool(params.get("network_synced", params.get("networkSynced")), True),
        "preview": preview,
    }
    asset_dir = str(params.get("asset_dir") or params.get("assetDir") or "").strip()
    if asset_dir:
        request["assetDir"] = asset_dir
    return request


def ensure_expression_parameter_sync(params: dict[str, Any], preview: bool = False) -> dict[str, Any]:
    params = params or {}
    request = build_ensure_expression_parameter_request(params, preview)
    if not request["parameterName"]:
        return {"ok": False, "error": "parameterName is required."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(
                settings,
                "vrc_ensure_expression_parameter",
                request,
                execution_context={"lane": "app_preview"} if preview else None,
            )
        ),
        "ensure expression parameter",
    )
    payload.setdefault("ok", True)
    return payload


def build_ensure_expression_menu_control_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "menuPath": str(params.get("menu_path") or params.get("menuPath") or "").strip(),
        "controlName": str(params.get("control_name") or params.get("controlName") or "").strip(),
        "controlType": str(params.get("control_type") or params.get("controlType") or "Toggle").strip() or "Toggle",
        "parameterName": str(params.get("parameter_name") or params.get("parameterName") or "").strip(),
        "controlValue": float(params.get("control_value", params.get("controlValue", 0)) or 0),
        "preview": preview,
    }
    asset_dir = str(params.get("asset_dir") or params.get("assetDir") or "").strip()
    if asset_dir:
        request["assetDir"] = asset_dir
    return request


def ensure_expression_menu_control_sync(params: dict[str, Any], preview: bool = False) -> dict[str, Any]:
    params = params or {}
    request = build_ensure_expression_menu_control_request(params, preview)
    if not request["controlName"]:
        return {"ok": False, "error": "controlName is required."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(
                settings,
                "vrc_ensure_expression_menu_control",
                request,
                execution_context={"lane": "app_preview"} if preview else None,
            )
        ),
        "ensure expression menu control",
    )
    payload.setdefault("ok", True)
    return payload


def build_ensure_animator_state_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "layerName": str(params.get("layer_name") or params.get("layerName") or "").strip(),
        "stateName": str(params.get("state_name") or params.get("stateName") or "").strip(),
        "parameterName": str(params.get("parameter_name") or params.get("parameterName") or "").strip(),
        "parameterType": str(params.get("parameter_type") or params.get("parameterType") or "Int").strip() or "Int",
        "conditionMode": str(params.get("condition_mode") or params.get("conditionMode") or "Equals").strip() or "Equals",
        "threshold": float(params.get("threshold", 0) or 0),
        "writeDefaults": _coerce_gateway_bool(params.get("write_defaults", params.get("writeDefaults")), True),
        "preview": preview,
    }
    asset_dir = str(params.get("asset_dir") or params.get("assetDir") or "").strip()
    if asset_dir:
        request["assetDir"] = asset_dir
    return request


def ensure_animator_state_sync(params: dict[str, Any], preview: bool = False) -> dict[str, Any]:
    params = params or {}
    request = build_ensure_animator_state_request(params, preview)
    if not request["layerName"]:
        return {"ok": False, "error": "layerName is required."}
    if not request["stateName"]:
        return {"ok": False, "error": "stateName is required."}
    if not request["parameterName"]:
        return {"ok": False, "error": "parameterName is required."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(
                settings,
                "vrc_ensure_animator_state",
                request,
                execution_context={"lane": "app_preview"} if preview else None,
            )
        ),
        "ensure animator state",
    )
    payload.setdefault("ok", True)
    return payload


def _copy_if_present(source: dict[str, Any], target: dict[str, Any], *keys: str, out: str | None = None) -> None:
    for key in keys:
        if key in source:
            target[out or key] = source[key]
            return


def _avatar_primitive_request(params: dict[str, Any], preview: bool | None = None) -> dict[str, Any]:
    params = params or {}
    request: dict[str, Any] = {}
    for key in (
        "action",
        "avatarPath",
        "clipPath",
        "bindingPath",
        "objectPath",
        "componentType",
        "propertyName",
        "constantFloat",
        "keys",
        "parameterName",
        "newName",
        "orderNames",
        "valueType",
        "defaultValue",
        "saved",
        "networkSynced",
        "menuPath",
        "controlName",
        "controlIndex",
        "controlType",
        "controlFloat",
        "value",
        "iconAssetPath",
        "subMenuAssetPath",
        "createSubMenu",
        "subParameters",
        "assetDir",
        "controllerPath",
        "fxControllerPath",
        "layerName",
        "stateName",
        "destinationStateName",
        "transitionIndex",
        "hasExitTime",
        "exitTime",
        "duration",
        "canTransitionToSelf",
        "conditions",
        "parameterType",
        "conditionMode",
        "threshold",
        "writeDefaults",
        "motionClipPath",
        "speed",
        "viewPosition",
        "lipSync",
        "visemeSkinnedMeshPath",
        "visemeBlendShapes",
        "expressionParametersPath",
        "expressionsMenuPath",
        "baseAnimationLayers",
        "specialAnimationLayers",
        "eyeLookEnabled",
    ):
        _copy_if_present(params, request, key)
    aliases = {
        "avatarPath": ("avatar_path",),
        "clipPath": ("clip_path",),
        "bindingPath": ("binding_path",),
        "componentType": ("component_type",),
        "propertyName": ("property_name",),
        "constantFloat": ("constant_float",),
        "parameterName": ("parameter_name",),
        "newName": ("new_name",),
        "orderNames": ("order_names",),
        "valueType": ("value_type",),
        "defaultValue": ("default_value",),
        "networkSynced": ("network_synced",),
        "menuPath": ("menu_path",),
        "controlName": ("control_name",),
        "controlIndex": ("control_index",),
        "controlType": ("control_type",),
        "controlFloat": ("control_float", "control_value"),
        "iconAssetPath": ("icon_asset_path",),
        "subMenuAssetPath": ("sub_menu_asset_path",),
        "createSubMenu": ("create_sub_menu",),
        "subParameters": ("sub_parameters",),
        "assetDir": ("asset_dir",),
        "controllerPath": ("controller_path",),
        "fxControllerPath": ("fx_controller_path",),
        "layerName": ("layer_name",),
        "stateName": ("state_name",),
        "destinationStateName": ("destination_state_name",),
        "transitionIndex": ("transition_index",),
        "hasExitTime": ("has_exit_time",),
        "exitTime": ("exit_time",),
        "canTransitionToSelf": ("can_transition_to_self",),
        "parameterType": ("parameter_type",),
        "conditionMode": ("condition_mode",),
        "writeDefaults": ("write_defaults",),
        "motionClipPath": ("motion_clip_path",),
        "viewPosition": ("view_position",),
        "visemeSkinnedMeshPath": ("viseme_skinned_mesh_path",),
        "visemeBlendShapes": ("viseme_blend_shapes",),
        "expressionParametersPath": ("expression_parameters_path",),
        "expressionsMenuPath": ("expressions_menu_path",),
        "baseAnimationLayers": ("base_animation_layers",),
        "specialAnimationLayers": ("special_animation_layers",),
        "eyeLookEnabled": ("eye_look_enabled",),
    }
    for canonical, alias_keys in aliases.items():
        if canonical not in request:
            _copy_if_present(params, request, *alias_keys, out=canonical)
    if preview is not None:
        request["preview"] = preview
    elif "preview" in params:
        request["preview"] = bool(params.get("preview"))
    return request


def read_avatar_descriptor_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = _avatar_primitive_request(params)
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_read_avatar_descriptor", request)),
        "read avatar descriptor",
    )
    payload.setdefault("ok", True)
    return payload


def write_avatar_descriptor_sync(params: dict[str, Any], preview: bool = False) -> dict[str, Any]:
    params = params or {}
    request = _avatar_primitive_request(params, preview=preview)
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(
                settings,
                "vrc_write_avatar_descriptor",
                request,
                execution_context={"lane": "app_preview"} if preview else None,
            )
        ),
        "write avatar descriptor",
    )
    payload.setdefault("ok", True)
    return payload


def write_animation_curve_sync(params: dict[str, Any], preview: bool = False) -> dict[str, Any]:
    params = params or {}
    request = _avatar_primitive_request(params, preview=preview)
    if not request.get("clipPath"):
        return {"ok": False, "error": "clipPath is required."}
    if not request.get("propertyName"):
        return {"ok": False, "error": "propertyName is required."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(
                settings,
                "vrc_write_animation_curve",
                request,
                execution_context={"lane": "app_preview"} if preview else None,
            )
        ),
        "write animation curve",
    )
    payload.setdefault("ok", True)
    return payload


def manage_expression_parameters_sync(params: dict[str, Any], preview: bool = False) -> dict[str, Any]:
    params = params or {}
    request = _avatar_primitive_request(params, preview=preview)
    if not request.get("action"):
        return {"ok": False, "error": "action is required."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(
                settings,
                "vrc_manage_expression_parameters",
                request,
                execution_context={"lane": "app_preview"} if preview else None,
            )
        ),
        "manage expression parameters",
    )
    payload.setdefault("ok", True)
    return payload


def manage_expression_menu_sync(params: dict[str, Any], preview: bool = False) -> dict[str, Any]:
    params = params or {}
    request = _avatar_primitive_request(params, preview=preview)
    if not request.get("action"):
        return {"ok": False, "error": "action is required."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(
                settings,
                "vrc_manage_expression_menu",
                request,
                execution_context={"lane": "app_preview"} if preview else None,
            )
        ),
        "manage expression menu",
    )
    payload.setdefault("ok", True)
    return payload


def manage_fx_animator_sync(params: dict[str, Any], preview: bool = False) -> dict[str, Any]:
    params = params or {}
    request = _avatar_primitive_request(params, preview=preview)
    if not request.get("action"):
        return {"ok": False, "error": "action is required."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(
                settings,
                "vrc_manage_fx_animator",
                request,
                execution_context={"lane": "app_preview"} if preview else None,
            )
        ),
        "manage FX animator",
    )
    payload.setdefault("ok", True)
    return payload


def _validate_create_wardrobe_request(request: dict[str, Any]) -> dict[str, Any] | None:
    if not request["parameterName"]:
        return {"ok": False, "error": "parameterName is required for wardrobe creation."}
    return None


def _create_wardrobe_primitive_args(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    common = {
        "avatarPath": request["avatarPath"],
        "assetDir": request.get("assetDir", "Assets/VRCForge/Generated/Wardrobe"),
    }
    parameter_name = request["parameterName"]
    menu_name = str(request.get("menuName") or request.get("subMenuName") or "Wardrobe").strip() or "Wardrobe"
    default_control_name = str(request.get("defaultControlName") or "Default").strip() or "Default"
    layer_name = str(request.get("layerName") or parameter_name).strip() or parameter_name
    return (
        {
            **common,
            "parameterName": parameter_name,
            "valueType": "Int",
            "defaultValue": 0,
            "saved": bool(request.get("saved", True)),
            "networkSynced": bool(request.get("networkSynced", True)),
        },
        {
            **common,
            "layerName": layer_name,
            "stateName": default_control_name,
            "parameterName": parameter_name,
            "parameterType": "Int",
            "conditionMode": "Equals",
            "threshold": 0,
            "writeDefaults": bool(request.get("writeDefaults", True)),
        },
        {
            **common,
            "menuPath": menu_name,
            "controlName": default_control_name,
            "controlType": "Toggle",
            "parameterName": parameter_name,
            "controlValue": 0,
        },
    )


def preview_create_wardrobe_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_create_wardrobe_request(params, True)
    invalid = _validate_create_wardrobe_request(request)
    if invalid is not None:
        return invalid
    parameter_args, animator_args, menu_args = _create_wardrobe_primitive_args(request)
    steps = [
        {"tool": "vrc_ensure_expression_parameter", "result": ensure_expression_parameter_sync(parameter_args, preview=True)},
        {"tool": "vrc_ensure_animator_state", "result": ensure_animator_state_sync(animator_args, preview=True)},
        {"tool": "vrc_ensure_expression_menu_control", "result": ensure_expression_menu_control_sync(menu_args, preview=True)},
    ]
    ok = all(bool(step["result"].get("ok")) for step in steps)
    return {
        "ok": ok,
        "preview": True,
        "action": "create_wardrobe",
        "parameterName": request["parameterName"],
        "steps": steps,
        "error": next((step["result"].get("error") for step in steps if not step["result"].get("ok")), None),
    }


def create_wardrobe_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_create_wardrobe_request(params, False)
    invalid = _validate_create_wardrobe_request(request)
    if invalid is not None:
        return invalid
    parameter_args, animator_args, menu_args = _create_wardrobe_primitive_args(request)
    steps = [
        {"tool": "vrc_ensure_expression_parameter", "result": ensure_expression_parameter_sync(parameter_args, preview=False)},
    ]
    if not steps[-1]["result"].get("ok"):
        return {"ok": False, "action": "create_wardrobe", "parameterName": request["parameterName"], "steps": steps, "error": steps[-1]["result"].get("error")}
    steps.append({"tool": "vrc_ensure_animator_state", "result": ensure_animator_state_sync(animator_args, preview=False)})
    if not steps[-1]["result"].get("ok"):
        return {"ok": False, "action": "create_wardrobe", "parameterName": request["parameterName"], "steps": steps, "error": steps[-1]["result"].get("error")}
    steps.append({"tool": "vrc_ensure_expression_menu_control", "result": ensure_expression_menu_control_sync(menu_args, preview=False)})
    if not steps[-1]["result"].get("ok"):
        return {"ok": False, "action": "create_wardrobe", "parameterName": request["parameterName"], "steps": steps, "error": steps[-1]["result"].get("error")}
    emit_log("info", "wardrobe", "Wardrobe skeleton created.", {"parameterName": request["parameterName"]})
    return {
        "ok": True,
        "preview": False,
        "action": "create_wardrobe",
        "parameterName": request["parameterName"],
        "steps": steps,
    }


def scan_avatar_parameters_gateway_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
    payload = scan_avatar_parameters_direct(settings, avatar_path)
    payload.setdefault("ok", True)
    return payload


def create_safe_backup_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_safe_backup_core_request(params)
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_create_safe_backup", request)),
        "safe backup",
    )
    payload.setdefault("ok", True)
    emit_log("info", "backup", "Safe backup snapshot created.", {"backupPath": payload.get("backup_path")})
    return payload


def build_safe_backup_core_request(params: dict[str, Any] | None) -> dict[str, Any]:
    """Canonical approved Core payload; backups stay in the project-owned default root."""
    request_params = dict(params or {})
    requested_root = str(
        request_params.get("backup_root") or request_params.get("backupRoot") or ""
    ).strip()
    if requested_root:
        raise ValueError("Custom backupRoot is not supported by the approved safe backup lane.")
    raw_asset_paths = request_params.get("asset_paths") or request_params.get("assetPaths") or []
    if not isinstance(raw_asset_paths, list):
        raise ValueError("assetPaths must be an array.")
    return {
        "avatarPath": str(request_params.get("avatar_path") or request_params.get("avatarPath") or "").strip(),
        "assetPaths": [str(item) for item in raw_asset_paths if str(item).strip()],
        "includeOpenScenes": bool(
            request_params.get("include_open_scenes", request_params.get("includeOpenScenes", True))
        ),
        "refreshAssets": False,
    }


def build_safe_backup_execution_plan(params: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [("vrc_create_safe_backup", build_safe_backup_core_request(params))]


def build_safe_backup_restore_request(params: dict[str, Any], confirm: bool) -> dict[str, Any]:
    asset_paths = params.get("asset_paths") or params.get("assetPaths") or []
    request: dict[str, Any] = {
        "backupPath": str(params.get("backup_path") or params.get("backupPath") or "").strip(),
        "backupId": str(params.get("backup_id") or params.get("backupId") or "").strip(),
        "assetPaths": [str(item) for item in asset_paths if str(item).strip()],
        "confirmRestore": confirm,
        "allowProjectMismatch": bool(params.get("allow_project_mismatch") or params.get("allowProjectMismatch") or False),
        "allowOverwriteChanged": bool(params.get("allow_overwrite_changed") or params.get("allowOverwriteChanged") or False),
        "refreshAssets": confirm,
    }
    backup_root = str(params.get("backup_root") or params.get("backupRoot") or "").strip()
    if backup_root:
        request["backupRoot"] = backup_root
    return request


def preview_safe_backup_restore_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(
                settings,
                "vrc_restore_safe_backup",
                build_safe_backup_restore_request(params, False),
                execution_context={"lane": "app_preview"},
            )
        ),
        "safe backup restore preview",
    )
    payload.setdefault("ok", True)
    return payload


def restore_safe_backup_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(settings, "vrc_restore_safe_backup", build_safe_backup_restore_request(params, True))
        ),
        "safe backup restore",
    )
    payload.setdefault("ok", True)
    emit_log("info", "backup", "Safe backup restore executed.", {"backupId": params.get("backupId") or params.get("backup_id")})
    return payload


def toggle_scene_object_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    object_path = str(params.get("object_path") or params.get("objectPath") or "").strip()
    if not object_path:
        return {"ok": False, "error": "objectPath is required."}
    if "active" not in params:
        return {"ok": False, "error": "active (true/false) is required."}
    active = bool(params.get("active"))
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = toggle_scene_object_direct(settings, object_path, active)
    emit_log("info", "wardrobe", "Scene object toggled.", {"objectPath": object_path, "active": active})
    return {"ok": True, "objectPath": object_path, "active": active, "result": payload}


def _coerce_path_list(params: dict[str, Any], *keys: str) -> list[str]:
    result: list[str] = []
    for key in keys:
        raw = params.get(key)
        if raw is None:
            continue
        items = raw if isinstance(raw, (list, tuple)) else [raw]
        for item in items:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
    return result


def build_inspect_modular_avatar_component_request(params: dict[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] = {
        "gameObjectPath": str(
            params.get("game_object_path")
            or params.get("gameObjectPath")
            or params.get("target_path")
            or params.get("targetPath")
            or ""
        ).strip(),
        "componentType": str(params.get("component_type") or params.get("componentType") or "").strip(),
    }
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
    if avatar_path:
        request["avatarPath"] = avatar_path
    return request


def inspect_modular_avatar_component_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_inspect_modular_avatar_component_request(params)
    invalid = validate_owned_add_modular_avatar_component_request(request)
    if invalid is not None:
        return invalid
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_inspect_modular_avatar_component", request)),
        "inspect modular avatar component",
    )
    payload.setdefault("ok", True)
    return payload


def inspect_primitive_basis_fixture_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    expected_run_id_digest = str(
        params.get("expected_run_id_digest")
        or params.get("expectedRunIdDigest")
        or ""
    ).strip()
    if re.fullmatch(r"[0-9a-f]{64}", expected_run_id_digest) is None:
        return {"ok": False, "error": "expectedRunIdDigest must be a lowercase SHA-256 digest."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(
            invoke_unity_mcp(
                settings,
                "vrc_inspect_primitive_basis_fixture",
                {"expectedRunIdDigest": expected_run_id_digest},
            )
        ),
        "inspect primitive basis fixture",
    )
    payload.setdefault("ok", True)
    return payload


def create_primitive_basis_restore_request_sync(checkpoint_id: str) -> dict[str, Any]:
    preview = AGENT_GATEWAY.preview_restore_checkpoint({"checkpointId": checkpoint_id})
    if preview.get("ok") is not True:
        raise PrimitiveBasisLiveRuntimeError("The fixed checkpoint is not restorable.")
    checkpoint = ensure_dict(preview.get("checkpoint"))
    arguments: dict[str, Any] = {
        "checkpointId": checkpoint_id,
        "confirmRestore": True,
    }
    if checkpoint.get("projectRoot"):
        arguments["projectRoot"] = str(checkpoint["projectRoot"])
    return AGENT_GATEWAY.create_apply_request(
        {
            "target_tool": "vrcforge_restore_checkpoint",
            "arguments": arguments,
            "reason": "Restore the fixed primitive-basis fixture checkpoint.",
            "preview": preview,
            "agent_name": "primitive-basis-live-runner",
            "never_auto_approve": True,
            "requires_explicit_approval": True,
        },
        include_arguments_digest=True,
    )


def scan_avatar_performance_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    return run_unity_artifact_scan_sync(
        params,
        "vrc_scan_avatar_performance",
        "avatar_performance",
        {"isMobile": bool(params.get("is_mobile") or params.get("isMobile") or False)},
        "avatar performance scan",
    )


def scan_thry_avatar_performance_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    return run_unity_artifact_scan_sync(
        params,
        "vrc_scan_thry_avatar_performance",
        "thry_avatar_performance",
        {},
        "Thry avatar performance scan",
    )


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
VRCHAT_SDK_PACKAGE_IDS = ["com.vrchat.avatars", "com.vrchat.base"]
GENERATED_ASSET_RESIDUE_DIRS = (
    Path("Assets") / "VRCForge" / "Generated",
    Path("Assets") / "VRCForge" / "Imported",
    Path("Assets") / "VRCForge" / "RollbackSmoke",
    Path("Assets") / "VRCForge" / "Temp",
)


def _validation_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        finding["detail"] = _redact_doctor_detail(detail)
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


def _validation_source_summary(payload: Any) -> dict[str, Any]:
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
    return _redact_doctor_detail(summary)


def _run_validation_source(name: str, runner: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        payload = runner()
        if not isinstance(payload, dict):
            payload = {"ok": True, "value": payload}
        payload.setdefault("ok", True)
        return {"ok": bool(payload.get("ok")), "payload": payload}
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "source": name}


def _validation_add_source_failure(
    findings: list[dict[str, Any]],
    section: str,
    source: str,
    result: dict[str, Any],
    severity: str = "Warning",
) -> None:
    if result.get("ok"):
        return
    _validation_add_finding(
        findings,
        section,
        severity,
        f"{section} scan failed",
        str(result.get("error") or "Scanner returned ok=false."),
        source,
        result,
    )


def _compile_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "Unity compile", "compile", result, severity="Error")
    if not result.get("ok"):
        return
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    raw_result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    stdout = str(raw_result.get("stdout") or "")
    error_count = _validation_max_number(raw_result, "errorCount")
    has_errors = "hasErrors: True" in stdout or error_count > 0
    if has_errors:
        _validation_add_finding(
            findings,
            "Unity compile",
            "Error",
            "Unity compile errors detected",
            f"Unity reports {int(error_count)} compile error(s).",
            "compile",
            _validation_source_summary(raw_result),
        )
    else:
        _validation_add_finding(findings, "Unity compile", "Info", "Unity compile clean", "No Unity compile errors were reported.", "compile")


def _parameters_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "Expression parameters", "parameters", result)
    if not result.get("ok"):
        return
    payload = result.get("payload") or {}
    error_count = _validation_max_number(payload, "errorCount")
    warning_count = _validation_max_number(payload, "warningCount")
    suggestions = _validation_list_count(payload, "suggestions", "optimizationSuggestions")
    used_bits = _validation_max_number(payload, "usedBits", "syncedBits", "memoryCost", "parameterCost")
    if error_count:
        _validation_add_finding(findings, "Expression parameters", "Error", "Parameter errors detected", f"{int(error_count)} parameter error(s) were reported.", "parameters")
    if warning_count or used_bits > 256:
        message = f"{int(warning_count)} warning(s) were reported."
        if used_bits > 256:
            message = f"Parameter usage appears over budget ({used_bits:g} > 256)."
        _validation_add_finding(findings, "Expression parameters", "Warning", "Parameter budget or consistency warning", message, "parameters")
    if suggestions:
        _validation_add_finding(findings, "Expression parameters", "Suggestion", "Parameter optimization suggestions available", f"{suggestions} optimization suggestion(s) were reported.", "parameters")
    if not (error_count or warning_count or suggestions or used_bits > 256):
        _validation_add_finding(findings, "Expression parameters", "Info", "Parameter scan completed", "No parameter errors were reported by the scanner.", "parameters")


def _menu_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "Expression menu", "menu", result)
    if not result.get("ok"):
        return
    payload = result.get("payload") or {}
    missing = _validation_list_count(payload, "missingReferences", "missingParameterControls", "brokenControls")
    warnings = _validation_list_count(payload, "warnings")
    if missing:
        _validation_add_finding(findings, "Expression menu", "Warning", "Expression menu missing references", f"{missing} missing or broken menu reference(s) were reported.", "menu")
    elif warnings:
        _validation_add_finding(findings, "Expression menu", "Warning", "Expression menu warnings", f"{warnings} warning(s) were reported.", "menu")
    else:
        _validation_add_finding(findings, "Expression menu", "Info", "Expression menu scan completed", "No menu reference warnings were reported.", "menu")


def _fx_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "FX animator", "fx", result)
    if not result.get("ok"):
        return
    payload = result.get("payload") or {}
    mismatches = _validation_list_count(payload, "parameterTypeMismatches", "typeMismatches", "mismatches")
    warnings = _validation_list_count(payload, "warnings")
    if mismatches:
        _validation_add_finding(findings, "FX animator", "Warning", "FX parameter/type mismatch", f"{mismatches} FX parameter/type mismatch(es) were reported.", "fx")
    elif warnings:
        _validation_add_finding(findings, "FX animator", "Warning", "FX animator warnings", f"{warnings} warning(s) were reported.", "fx")
    else:
        _validation_add_finding(findings, "FX animator", "Info", "FX animator scan completed", "No FX parameter/type warnings were reported.", "fx")


def _binding_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "Animation bindings", "animation_bindings", result)
    if not result.get("ok"):
        return
    payload = result.get("payload") or {}
    broken = _validation_list_count(payload, "brokenBindings", "missingBindings", "missingObjectBindings", "unsupportedBindings")
    warnings = _validation_list_count(payload, "warnings")
    if broken:
        _validation_add_finding(findings, "Animation bindings", "Warning", "Broken animation bindings", f"{broken} broken or unsupported animation binding(s) were reported.", "animation_bindings")
    elif warnings:
        _validation_add_finding(findings, "Animation bindings", "Warning", "Animation binding warnings", f"{warnings} warning(s) were reported.", "animation_bindings")
    else:
        _validation_add_finding(findings, "Animation bindings", "Info", "Animation binding scan completed", "No broken binding warnings were reported.", "animation_bindings")


def _material_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "Materials / shaders", "materials", result)
    if not result.get("ok"):
        return
    payload = result.get("payload") or {}
    # Post-import magenta / broken-shader gate. A material whose shader reference is
    # missing or compiled to Unity's internal error shader renders magenta/pink in
    # the editor, which almost always means the outfit prefab was imported before its
    # shader/material support package. This is a blocking Error (not a soft warning)
    # so a visibly broken outfit cannot pass validation quietly.
    inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else payload
    magenta = detect_magenta_materials(inventory)
    if magenta:
        post_import = build_post_import_outfit_validation(inventory)
        affected = post_import.get("affectedRenderers") or []
        _validation_add_finding(
            findings,
            "Materials / shaders",
            "Error",
            "Magenta / missing-shader materials after import",
            (
                f"{len(magenta)} material(s) imported with a missing or error shader "
                "(they render magenta/pink in Unity). Import the shader and the outfit's "
                "material/texture support package before the clothing prefab, then re-import "
                "the prefab and re-run validation."
            ),
            "materials",
            {
                "magentaCount": post_import.get("magentaCount"),
                "magentaMaterials": post_import.get("magentaMaterials"),
                "affectedRenderers": affected,
                "remediation": post_import.get("remediation"),
                "postImportSchema": post_import.get("schema"),
            },
        )
        return
    unsupported = _validation_max_number(payload, "unsupportedShaderCount", "unsupportedMaterialCount")
    missing = _validation_list_count(payload, "missingMaterials", "missingShaders")
    if unsupported or missing:
        _validation_add_finding(
            findings,
            "Materials / shaders",
            "Warning",
            "Material/shader compatibility warnings",
            f"{int(unsupported)} unsupported shader/material item(s), {missing} missing reference(s).",
            "materials",
            _validation_source_summary(payload),
        )
    else:
        _validation_add_finding(findings, "Materials / shaders", "Info", "Material scan completed", "No material/shader compatibility warnings were reported.", "materials")


def _wardrobe_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "Wardrobe", "wardrobe", result)
    if not result.get("ok"):
        return
    payload = result.get("payload") or {}
    inconsistencies = _validation_list_count(payload, "inconsistencies", "errors", "warnings")
    candidate_count = _validation_max_number(payload, "wardrobeCandidateCount")
    if inconsistencies:
        _validation_add_finding(findings, "Wardrobe", "Warning", "Wardrobe consistency warnings", f"{inconsistencies} wardrobe consistency warning(s) were reported.", "wardrobe")
    elif candidate_count:
        _validation_add_finding(findings, "Wardrobe", "Suggestion", "Wardrobe candidates need confirmation", f"{int(candidate_count)} loose or candidate wardrobe group(s) require user selection before writes.", "wardrobe")
    else:
        _validation_add_finding(findings, "Wardrobe", "Info", "Wardrobe scan completed", "No wardrobe consistency warnings were reported.", "wardrobe")


def _performance_validation(findings: list[dict[str, Any]], result: dict[str, Any], section: str, source: str) -> None:
    _validation_add_source_failure(findings, section, source, result)
    if not result.get("ok"):
        return
    payload = result.get("payload") or {}
    rank = str(payload.get("rank") or payload.get("performanceRank") or payload.get("overallRank") or "")
    if not rank and isinstance(payload.get("summary"), dict):
        rank = str(payload["summary"].get("rank") or payload["summary"].get("performanceRank") or payload["summary"].get("overallRank") or "")
    lowered = rank.lower()
    if any(value in lowered for value in ("poor", "very poor", "verypoor")):
        _validation_add_finding(findings, section, "Warning", f"{section} performance warning", f"Performance rank is {rank or 'not ideal'}.", source, _validation_source_summary(payload))
    else:
        _validation_add_finding(findings, section, "Info", f"{section} performance headline", f"Performance scan completed{f' with rank {rank}' if rank else ''}.", source, _validation_source_summary(payload))


def _validation_resolve_project_path(project_value: str) -> Path | None:
    if not project_value:
        return None
    try:
        project_path = Path(project_value)
    except (OSError, ValueError):
        return None
    return project_path if project_path.is_dir() else None


def validation_dependency_status_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    project_value = str(params.get("projectPath") or params.get("project_path") or DASHBOARD_STATE.selected_project_path or "").strip()
    project_path = _validation_resolve_project_path(project_value)
    return {
        "ok": True,
        "projectConfigured": bool(project_value),
        "projectReadable": project_path is not None,
        "packages": {
            "vrchat_sdk": PACKAGE_DETECTION.detect(project_path, VRCHAT_SDK_PACKAGE_IDS),
            "modular_avatar": PACKAGE_DETECTION.detect(project_path, list(ADDON_FRAMEWORKS["modular_avatar"]["packageIds"])),
            "vrcfury": PACKAGE_DETECTION.detect(project_path, list(ADDON_FRAMEWORKS["vrcfury"]["packageIds"])),
        },
    }


def validation_environment_status_sync(_params: dict[str, Any]) -> dict[str, Any]:
    health = build_agentic_app_health()
    components = health.get("components") if isinstance(health.get("components"), dict) else {}
    selected = {
        key: components.get(key)
        for key in (
            "unityPluginInstalled",
            "mcpPackageConfigured",
            "unityMcpBridgeReachable",
            "unityMcpInstance",
            "vrcForgeUnityTools",
        )
    }
    return {
        "ok": bool(health.get("ok", True)),
        "version": health.get("version") or app.version,
        "components": selected,
        "unityStatus": health.get("unityStatus"),
    }


def scan_generated_asset_residue_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    project_value = str(params.get("projectPath") or params.get("project_path") or DASHBOARD_STATE.selected_project_path or "").strip()
    project_path = _validation_resolve_project_path(project_value)
    roots: list[dict[str, Any]] = []
    total_files = 0
    total_dirs = 0
    if project_path is None:
        return {
            "ok": True,
            "projectConfigured": bool(project_value),
            "projectReadable": False,
            "residueCount": 0,
            "roots": roots,
            "warning": "Project path is not configured or is not readable; generated asset residue scan skipped.",
        }
    for relative_root in GENERATED_ASSET_RESIDUE_DIRS:
        root = project_path / relative_root
        if not root.exists():
            continue
        file_count = 0
        dir_count = 0
        samples: list[str] = []
        try:
            for child in root.rglob("*"):
                if child.is_dir():
                    dir_count += 1
                    continue
                if not child.is_file():
                    continue
                file_count += 1
                if len(samples) < 10:
                    try:
                        samples.append(child.relative_to(project_path).as_posix())
                    except ValueError:
                        samples.append(child.name)
        except OSError as exc:
            roots.append({"root": relative_root.as_posix(), "readable": False, "error": str(exc)})
            continue
        total_files += file_count
        total_dirs += dir_count
        roots.append(
            {
                "root": relative_root.as_posix(),
                "readable": True,
                "fileCount": file_count,
                "dirCount": dir_count,
                "samplePaths": samples,
            }
        )
    return {
        "ok": True,
        "projectConfigured": True,
        "projectReadable": True,
        "residueCount": total_files + total_dirs,
        "fileCount": total_files,
        "dirCount": total_dirs,
        "roots": roots,
    }


def _dependency_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "VRChat SDK", "dependencies", result)
    if not result.get("ok"):
        return
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    packages = payload.get("packages") if isinstance(payload.get("packages"), dict) else {}
    vrchat_sdk = packages.get("vrchat_sdk") if isinstance(packages.get("vrchat_sdk"), dict) else {}
    modular_avatar = packages.get("modular_avatar") if isinstance(packages.get("modular_avatar"), dict) else {}
    vrcfury = packages.get("vrcfury") if isinstance(packages.get("vrcfury"), dict) else {}
    if not payload.get("projectConfigured"):
        _validation_add_finding(findings, "VRChat SDK", "Warning", "No Unity project selected", "VRChat SDK package detection needs a selected Unity project.", "dependencies")
    elif not payload.get("projectReadable"):
        _validation_add_finding(findings, "VRChat SDK", "Warning", "Unity project path is not readable", "VRChat SDK package detection could not read the configured Unity project.", "dependencies")
    elif vrchat_sdk.get("installed"):
        _validation_add_finding(findings, "VRChat SDK", "Info", "VRChat SDK detected", "VRChat SDK package metadata is present.", "dependencies", vrchat_sdk)
    else:
        _validation_add_finding(findings, "VRChat SDK", "Error", "VRChat SDK not detected", "Avatar validation and Build & Test require the VRChat Avatar SDK package.", "dependencies")

    for section, label, package_info, source in (
        ("Modular Avatar conflicts", "Modular Avatar", modular_avatar, "modular_avatar"),
        ("VRCFury conflicts", "VRCFury", vrcfury, "vrcfury"),
    ):
        if package_info.get("installed"):
            _validation_add_finding(findings, section, "Info", f"{label} package detected", f"{label} metadata is present; conflict scanners can use this context.", source, package_info)
        else:
            _validation_add_finding(findings, section, "Info", f"{label} package not detected", f"{label} is optional unless this avatar uses it.", source)


def _environment_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "VRCForge Unity plugin", "environment", result, severity="Error")
    if not result.get("ok"):
        return
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    components = payload.get("components") if isinstance(payload.get("components"), dict) else {}

    def component_status(key: str) -> tuple[str, dict[str, Any]]:
        component = components.get(key) if isinstance(components.get(key), dict) else {}
        return str(component.get("status") or "unknown").lower(), component

    plugin_status, plugin = component_status("unityPluginInstalled")
    if plugin_status == "ok":
        _validation_add_finding(findings, "VRCForge Unity plugin", "Info", "VRCForge Unity plugin installed", "The Unity-side VRCForge tool surface is present.", "environment", plugin)
    elif plugin_status in {"warning", "unknown"}:
        _validation_add_finding(findings, "VRCForge Unity plugin", "Warning", "VRCForge Unity plugin needs attention", "Install or repair the VRCForge Unity plugin before live scans or Build & Test.", "environment", plugin)
    else:
        _validation_add_finding(findings, "VRCForge Unity plugin", "Error", "VRCForge Unity plugin unavailable", "VRCForge cannot rely on live Unity tools until the plugin is repaired.", "environment", plugin)

    for key, title in (("mcpPackageConfigured", "VRCForge MCP Core"), ("unityMcpBridgeReachable", "Unity MCP bridge"), ("unityMcpInstance", "Unity MCP instance"), ("vrcForgeUnityTools", "VRCForge Unity tools")):
        status, component = component_status(key)
        if status == "ok":
            _validation_add_finding(findings, "MCP bridge", "Info", f"{title} available", f"{title} is available for read-only scans and supervised requests.", "environment", component)
        elif status in {"warning", "unknown"}:
            _validation_add_finding(findings, "MCP bridge", "Warning", f"{title} needs attention", f"{title} is not confirmed; Unity-facing validation may be incomplete.", "environment", component)
        else:
            _validation_add_finding(findings, "MCP bridge", "Error", f"{title} unavailable", f"{title} is required for live Unity validation.", "environment", component)


def _package_manager_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "Package manager", "package_manager", result)
    if not result.get("ok"):
        return
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    managers = payload.get("managers") if isinstance(payload.get("managers"), list) else []
    if payload.get("preferredCli"):
        _validation_add_finding(findings, "Package manager", "Info", "VPM CLI available", "A supported VPM CLI is available for supervised package repair plans.", "package_manager", payload.get("preferredCli"))
    elif managers:
        _validation_add_finding(findings, "Package manager", "Warning", "Package manager detected but not CLI-ready", "A package manager was detected, but VRCForge could not find a preferred CLI for automated repair plans.", "package_manager", {"managerCount": len(managers)})
    else:
        _validation_add_finding(findings, "Package manager", "Warning", "No VPM CLI detected", "Install vrc-get or use VCC/ALCOM UI for package install and repair workflows.", "package_manager")


def _hierarchy_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "Hierarchy paths", "avatar_items", result)
    if not result.get("ok"):
        return
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    item_count = _validation_max_number(payload, "itemCount", "count")
    _validation_add_finding(
        findings,
        "Hierarchy paths",
        "Info",
        "Avatar hierarchy scan completed",
        f"Hierarchy scan completed{f' with {int(item_count)} item(s)' if item_count else ''}.",
        "avatar_items",
        _validation_source_summary(payload),
    )


def _generated_residue_validation(findings: list[dict[str, Any]], result: dict[str, Any]) -> None:
    _validation_add_source_failure(findings, "Generated asset residue", "generated_residue", result)
    if not result.get("ok"):
        return
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    residue_count = int(payload.get("residueCount") or 0)
    if not payload.get("projectReadable"):
        _validation_add_finding(findings, "Generated asset residue", "Info", "Generated asset residue scan skipped", "A readable Unity project is required to scan generated residue directories.", "generated_residue")
    elif residue_count:
        _validation_add_finding(findings, "Generated asset residue", "Suggestion", "Generated asset residue found", f"{residue_count} generated file or folder item(s) were found in VRCForge-owned generated locations.", "generated_residue", payload)
    else:
        _validation_add_finding(findings, "Generated asset residue", "Info", "No generated asset residue found", "No VRCForge generated residue was found in known generated locations.", "generated_residue")


def _coverage_gap_validation(findings: list[dict[str, Any]]) -> None:
    for section in ("PhysBones", "Contacts", "Particles"):
        _validation_add_finding(
            findings,
            section,
            "Info",
            f"{section} scanner pending",
            f"{section} is reserved in vrcforge.validation.v1; this build reports section coverage but does not run a dedicated scanner yet.",
            "coverage",
        )


def build_validation_report_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
    project_path = str(params.get("project_path") or params.get("projectPath") or DASHBOARD_STATE.selected_project_path or "").strip()
    include_quest = bool(params.get("include_quest", params.get("includeQuest", True)))
    include_sources = bool(params.get("include_sources", params.get("includeSources", False)))
    include_readiness = bool(params.get("include_readiness", params.get("includeReadiness", True)))
    gate_build = bool(params.get("gate_build", params.get("gateBuild", True)))
    max_errors = int(params.get("max_errors") or params.get("maxErrors") or 50)
    base_params = {
        "avatarPath": avatar_path,
        "projectPath": project_path,
        "maxErrors": max(1, min(max_errors, 200)),
        "includeConsoleFallback": True,
    }

    sources: dict[str, dict[str, Any]] = {
        "compile": _run_validation_source("compile", lambda: read_agent_compile_errors(base_params)),
        "parameters": _run_validation_source("parameters", lambda: scan_avatar_parameters_gateway_sync(base_params)),
        "menu": _run_validation_source(
            "menu",
            lambda: WARDROBE_ARTIFACT_READ.scan_avatar_controls(base_params),
        ),
        "fx": _run_validation_source("fx", lambda: scan_fx_animator_sync(base_params)),
        "animation_bindings": _run_validation_source("animation_bindings", lambda: scan_animation_bindings_sync(base_params)),
        "materials": _run_validation_source(
            "materials",
            lambda: SHADER_VISION_PROTECTION.scan_shader_materials(
                ShaderMaterialScanRequest(**base_params)
            ),
        ),
        "wardrobe": _run_validation_source(
            "wardrobe",
            lambda: WARDROBE_ARTIFACT_READ.scan_wardrobe(base_params),
        ),
        "performance_pc": _run_validation_source("performance_pc", lambda: scan_avatar_performance_sync({**base_params, "isMobile": False})),
    }
    if include_readiness:
        sources.update(
            {
                "dependencies": _run_validation_source("dependencies", lambda: validation_dependency_status_sync(base_params)),
                "environment": _run_validation_source("environment", lambda: validation_environment_status_sync(base_params)),
                "package_manager": _run_validation_source("package_manager", lambda: PACKAGE_INSTALL_WORKFLOWS.package_manager_status(base_params)),
                "avatar_items": _run_validation_source(
                    "avatar_items",
                    lambda: WARDROBE_ARTIFACT_READ.scan_avatar_items(base_params),
                ),
                "generated_residue": _run_validation_source("generated_residue", lambda: scan_generated_asset_residue_sync(base_params)),
            }
        )
    if include_quest:
        sources["performance_quest"] = _run_validation_source(
            "performance_quest",
            lambda: scan_avatar_performance_sync({**base_params, "isMobile": True}),
        )

    findings: list[dict[str, Any]] = []
    _compile_validation(findings, sources["compile"])
    if avatar_path:
        _validation_add_finding(findings, "Selected avatar", "Info", "Avatar path selected", "Validation ran against the selected avatar path.", "selected_avatar", {"avatarPath": avatar_path})
    else:
        _validation_add_finding(findings, "Selected avatar", "Warning", "No avatar path selected", "Validation could not confirm a selected avatar path. Some scanners may fall back to the current Unity selection or all avatars.", "selected_avatar")
    if include_readiness:
        _dependency_validation(findings, sources["dependencies"])
        _environment_validation(findings, sources["environment"])
        _package_manager_validation(findings, sources["package_manager"])
        _hierarchy_validation(findings, sources["avatar_items"])
        _generated_residue_validation(findings, sources["generated_residue"])
        _coverage_gap_validation(findings)
    _parameters_validation(findings, sources["parameters"])
    _menu_validation(findings, sources["menu"])
    _fx_validation(findings, sources["fx"])
    _binding_validation(findings, sources["animation_bindings"])
    _material_validation(findings, sources["materials"])
    _wardrobe_validation(findings, sources["wardrobe"])
    _performance_validation(findings, sources["performance_pc"], "Performance PC", "performance_pc")
    if include_quest:
        _performance_validation(findings, sources["performance_quest"], "Performance Quest", "performance_quest")

    counts = _validation_severity_counts(findings)
    gate = _validation_gate(findings, enabled=gate_build)
    source_summaries = {
        name: (
            {"ok": bool(result.get("ok")), "error": result.get("error")}
            if not result.get("ok")
            else {"ok": True, "summary": _validation_source_summary(result.get("payload"))}
        )
        for name, result in sources.items()
    }
    if include_sources:
        for name, result in sources.items():
            if result.get("ok") and isinstance(result.get("payload"), dict):
                source_summaries[name]["payload"] = _redact_doctor_detail(result["payload"])

    return {
        "ok": counts["Error"] == 0,
        "schema": "vrcforge.validation.v1",
        "readOnly": True,
        "autoFix": False,
        "generatedAt": _validation_now(),
        "avatarPath": avatar_path,
        "projectPathConfigured": bool(project_path),
        "summary": {
            "findingCount": len(findings),
            "severityCounts": counts,
            "gateStatus": gate["status"],
            "sourceCount": len(sources),
            "failedSourceCount": sum(1 for result in sources.values() if not result.get("ok")),
        },
        "sections": _validation_section_summaries(findings),
        "findings": findings,
        "sources": source_summaries,
        "gate": gate,
        "severitySystem": {
            "Error": "Blocks Build & Test when the validation gate is enabled.",
            "Warning": "Likely issue that should be reviewed before Build & Test.",
            "Suggestion": "Optional optimization or cleanup.",
            "Info": "Context only.",
            "Ignored": "User-dismissed item.",
        },
        "rules": {
            "validationIsReadOnly": True,
            "validationNeverFixes": True,
            "fixesRequirePlanPreviewApprovalCheckpointApplyValidateRestore": True,
        },
    }


def _readiness_section_status(validation: dict[str, Any], section_name: str) -> dict[str, Any]:
    for section in validation.get("sections") or []:
        if isinstance(section, dict) and section.get("name") == section_name:
            return section
    return {
        "name": section_name,
        "id": VALIDATION_SECTION_IDS.get(section_name) or re.sub(r"[^a-z0-9]+", "_", section_name.lower()).strip("_"),
        "status": "not_run",
        "counts": _validation_severity_counts([]),
        "findingIds": [],
    }


def _build_test_fix_suggestions(validation: dict[str, Any], package_diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    gate = validation.get("gate") if isinstance(validation.get("gate"), dict) else {}
    if gate.get("status") == "blocked":
        suggestions.append(
            {
                "id": "resolve_validation_errors_request",
                "title": "Create supervised fix plan for blocking validation errors",
                "category": "validation",
                "automatic": False,
                "requiresPreviewApprovalCheckpointValidationRollback": True,
                "findingIds": gate.get("blockingFindingIds") or [],
            }
        )
    for item in package_diagnostics.get("suggestedFixPlans") or []:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized.setdefault("automatic", False)
        normalized["requiresPreviewApprovalCheckpointValidationRollback"] = True
        suggestions.append(normalized)
    return suggestions


def build_test_readiness_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
    project_path = str(params.get("project_path") or params.get("projectPath") or DASHBOARD_STATE.selected_project_path or "").strip()
    include_quest = bool(params.get("include_quest", params.get("includeQuest", True)))
    max_errors = int(params.get("max_errors") or params.get("maxErrors") or 50)
    validation = build_validation_report_sync(
        {
            "avatarPath": avatar_path,
            "projectPath": project_path,
            "includeQuest": include_quest,
            "includeSources": False,
            "includeReadiness": True,
            "gateBuild": True,
            "maxErrors": max_errors,
        }
    )
    try:
        package_diagnostics = PACKAGE_INSTALL_WORKFLOWS.diagnose_install(
            {
                "projectPath": project_path,
                "maxCompileErrors": max_errors,
            }
        )
    except Exception as exc:  # noqa: BLE001 - readiness must stay diagnostic-only.
        package_diagnostics = {
            "ok": False,
            "schema": "vrcforge.package_install_diagnostics.v1",
            "error": str(exc),
            "symptoms": [],
            "suggestedFixPlans": [],
        }

    counts = validation.get("summary", {}).get("severityCounts", {}) if isinstance(validation.get("summary"), dict) else {}
    gate = validation.get("gate") if isinstance(validation.get("gate"), dict) else {}
    if gate.get("status") == "blocked":
        status = "blocked"
    elif counts.get("Warning", 0) or counts.get("Suggestion", 0):
        status = "review"
    else:
        status = "ready"

    checks = [
        {
            "id": "unity_compile",
            "label": "Unity compile",
            "section": _readiness_section_status(validation, "Unity compile"),
        },
        {
            "id": "vrchat_sdk",
            "label": "VRChat SDK",
            "section": _readiness_section_status(validation, "VRChat SDK"),
        },
        {
            "id": "selected_avatar",
            "label": "Selected avatar",
            "section": _readiness_section_status(validation, "Selected avatar"),
        },
        {
            "id": "mcp_bridge",
            "label": "MCP bridge",
            "section": _readiness_section_status(validation, "MCP bridge"),
        },
        {
            "id": "package_manager",
            "label": "Package manager",
            "section": _readiness_section_status(validation, "Package manager"),
        },
    ]
    return {
        "ok": status != "blocked",
        "schema": "vrcforge.build_test_readiness.v1",
        "readOnly": True,
        "autoBuild": False,
        "autoPublish": False,
        "generatedAt": _validation_now(),
        "status": status,
        "avatarPath": avatar_path,
        "projectPathConfigured": bool(project_path),
        "gate": gate,
        "checks": checks,
        "validationSummary": validation.get("summary"),
        "validationSections": validation.get("sections"),
        "packageDiagnostics": redact_support_payload(package_diagnostics),
        "suggestedFixPlans": _build_test_fix_suggestions(validation, package_diagnostics),
        "rules": {
            "readOnly": True,
            "noAutomaticPublish": True,
            "noHiddenAccountUploadAutomation": True,
            "noUnattendedVrchatSdkPublish": True,
            "fixesRequirePreviewApprovalCheckpointApplyValidateRestore": True,
        },
    }




VALIDATION_DELTA_SEVERITIES = ("Error", "Warning", "Suggestion", "Info", "Ignored")


















def _lac_component_properties(profile: str) -> dict[str, Any]:
    profile_id = normalize_optimizer_profile_id(profile)
    if profile_id in {"pc_conservative", "high_quality"}:
        return {
            "Preset": "HighQuality",
            "Strategy": "Combined",
            "FastWeight": 0.1,
            "HighAccuracyWeight": 0.5,
            "PerceptualWeight": 0.4,
            "HighComplexityThreshold": 0.3,
            "LowComplexityThreshold": 0.1,
            "MinDivisor": 1,
            "MaxDivisor": 2,
            "MaxResolution": 2048,
            "MinResolution": 256,
            "ForcePowerOfTwo": True,
            "MinSourceSize": 1024,
            "SkipIfSmallerThan": 512,
            "TargetPlatform": "Auto",
            "UseHighQualityFormatForHighComplexity": True,
            "ProcessMainTextures": True,
            "ProcessNormalMaps": True,
            "ProcessEmissionMaps": True,
            "ProcessOtherTextures": True,
            "SkipUnknownUncompressedTextures": True,
        }
    return {
        "Preset": "Balanced",
        "Strategy": "Combined",
        "FastWeight": 0.3,
        "HighAccuracyWeight": 0.5,
        "PerceptualWeight": 0.2,
        "HighComplexityThreshold": 0.7,
        "LowComplexityThreshold": 0.2,
        "MinDivisor": 1,
        "MaxDivisor": 8,
        "MaxResolution": 2048,
        "MinResolution": 64,
        "ForcePowerOfTwo": True,
        "MinSourceSize": 256,
        "SkipIfSmallerThan": 128,
        "TargetPlatform": "Auto",
        "UseHighQualityFormatForHighComplexity": True,
        "ProcessMainTextures": True,
        "ProcessNormalMaps": True,
        "ProcessEmissionMaps": True,
        "ProcessOtherTextures": True,
        "SkipUnknownUncompressedTextures": True,
    }


def _optimizer_component_properties(optimizer_id: str, profile: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = ensure_dict(options or {})
    if optimizer_id == "lac":
        return _lac_component_properties(profile)
    if optimizer_id == "ma2bt_pro":
        return {
            "compactMode": True,
            "convertMultiState": True,
            "mergeIdenticalBlendTreesAndAnimations": True,
            "scanAllLayers": False,
            "maResponsivePrefixes": ["MA Responsive: ", "RC MA Responsive: "],
        }
    if optimizer_id == "textrans_tool":
        material_paths = confirmed_ttt_material_paths({}, options)
        properties: dict[str, Any] = {}
        if material_paths:
            properties["AtlasTargetMaterials"] = material_paths
        reference = str(options.get("allMaterialMergeReference") or options.get("mergeReference") or "").strip()
        if reference:
            properties["AllMaterialMergeReference"] = reference.replace("\\", "/")
        return properties
    if optimizer_id == "meshia":
        ratio, _ratio_error = meshia_relative_vertex_count(
            normalize_optimizer_profile_id(profile),
            options,
        )
        return {
            "target": {
                "Kind": "RelativeVertexCount",
                "Value": ratio,
            }
        }
    return {}


def _component_already_present(project_path: str, avatar_path: str, component_type: str) -> tuple[bool, dict[str, Any]]:
    try:
        payload = get_gameobject_sync({"projectPath": project_path, "gameObjectPath": avatar_path})
    except Exception as exc:  # noqa: BLE001 - best-effort idempotence check before the write.
        return False, {"ok": False, "error": str(exc)}
    components = payload.get("components") if isinstance(payload, dict) else None
    if not isinstance(components, list):
        return False, payload if isinstance(payload, dict) else {}
    component_short = component_type.rsplit(".", 1)[-1]
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            continue
        values = {
            str(component.get("type") or ""),
            str(component.get("fullName") or ""),
            str(component.get("componentType") or ""),
            str(component.get("name") or ""),
        }
        if component_type in values or component_short in values or any(value.endswith(f".{component_short}") for value in values):
            return True, {"ok": True, "componentIndex": index, "component": component, "gameObject": payload}
    return False, payload


def _optimizer_component_snapshot(
    project_path: str,
    target_path: str,
    component_type: str,
) -> tuple[bool, int, dict[str, Any], list[dict[str, Any]]]:
    """Read the exact component layout used to bind an optimizer approval."""
    payload = get_gameobject_sync({"projectPath": project_path, "gameObjectPath": target_path})
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "Optimizer target inspection failed.")
    components = payload.get("components")
    if not isinstance(components, list) or not all(isinstance(item, dict) for item in components):
        raise RuntimeError("Optimizer target component inventory is invalid.")
    copied_components = [copy.deepcopy(item) for item in components]
    component_short = component_type.rsplit(".", 1)[-1]
    for index, component in enumerate(copied_components):
        values = {
            str(component.get("type") or ""),
            str(component.get("fullName") or ""),
            str(component.get("componentType") or ""),
            str(component.get("name") or ""),
        }
        if component_type in values or component_short in values or any(
            value.endswith(f".{component_short}") for value in values
        ):
            return True, index, component, copied_components
    return False, len(copied_components), {}, copied_components


def _optimizer_component_calls(
    *,
    target_path: str,
    component_type: str,
    component_index: int,
    add_component: bool,
    properties: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    if add_component:
        calls.append(("vrc_add_component", {
            "gameObjectPath": target_path,
            "componentType": component_type,
            "preview": False,
        }))
    for property_path, value in properties.items():
        calls.append(("vrc_set_property", {
            "gameObjectPath": target_path,
            "componentType": component_type,
            "componentIndex": component_index,
            "propertyPath": property_path,
            "preview": False,
            "value": value,
        }))
    if not calls:
        raise RuntimeError("Optimizer configuration has no bounded Unity writes.")
    return calls


def prepare_configure_optimizer_component_request(
    arguments: dict[str, Any], preview: Any,
) -> tuple[dict[str, Any], Any]:
    """Freeze component identity, add/reuse decision, and every Core write before approval."""
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
        raise RuntimeError("Caller may not provide the reserved prepared Unity execution key.")
    params = dict(arguments or {})
    optimizer_id = str(params.get("optimizerId") or params.get("optimizer_id") or "").strip()
    mode = str(params.get("mode") or "").strip()
    avatar_path = str(params.get("avatarPath") or params.get("avatar_path") or "").strip()
    target_path = str(params.get("targetPath") or params.get("target_path") or "").strip() or avatar_path
    component_type = str(params.get("componentType") or params.get("component_type") or "").strip()
    if not optimizer_id or not mode or not avatar_path or not target_path or not component_type:
        raise RuntimeError("optimizerId, mode, avatarPath, targetPath, and componentType are required.")
    project_path = resolve_project_path(
        params,
        DASHBOARD_STATE.selected_project_path if DASHBOARD_STATE else "",
    )
    profile = normalize_optimizer_profile_id(
        params.get("profile") or "pc_conservative"
    )
    options = ensure_dict(params.get("options") or {})
    properties = _optimizer_component_properties(optimizer_id, profile, options)
    if not properties:
        raise RuntimeError("Optimizer configuration has no validated component properties.")
    present, component_index, component, components = _optimizer_component_snapshot(
        project_path, target_path, component_type,
    )
    prepared = {
        "optimizerId": optimizer_id,
        "mode": mode,
        "avatarPath": avatar_path,
        "targetPath": target_path,
        "componentType": component_type,
        "projectPath": project_path,
        "profile": profile,
        "options": options,
    }
    calls = _optimizer_component_calls(
        target_path=target_path,
        component_type=component_type,
        component_index=component_index,
        add_component=not present,
        properties=properties,
    )
    evidence = {
        "targetPath": target_path,
        "componentType": component_type,
        "componentPresent": present,
        "componentIndex": component_index,
        "component": component,
        "components": components,
        "properties": properties,
    }
    return install_prepared_calls(prepared, calls, evidence), preview


def configure_optimizer_component_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    evidence = prepared_evidence(params)
    if not isinstance(evidence, dict):
        raise RuntimeError("Prepared optimizer evidence is invalid.")
    target_path = str(evidence.get("targetPath") or "")
    component_type = str(evidence.get("componentType") or "")
    present = evidence.get("componentPresent")
    component_index = evidence.get("componentIndex")
    if not target_path or not component_type or not isinstance(present, bool) or isinstance(component_index, bool) or not isinstance(component_index, int):
        raise RuntimeError("Prepared optimizer evidence is incomplete.")
    project_path = resolve_project_path(
        params,
        DASHBOARD_STATE.selected_project_path if DASHBOARD_STATE else "",
    )
    current_present, current_index, current_component, current_components = _optimizer_component_snapshot(
        project_path, target_path, component_type,
    )
    if current_present != present or current_components != evidence.get("components"):
        raise RuntimeError("Optimizer component layout drifted after approval.")
    if present and (current_index != component_index or current_component != evidence.get("component")):
        raise RuntimeError("Optimizer component identity drifted after approval.")
    if not present and component_index != len(current_components):
        raise RuntimeError("Optimizer component insertion index drifted after approval.")
    expected_calls = _optimizer_component_calls(
        target_path=target_path,
        component_type=component_type,
        component_index=component_index,
        add_component=not present,
        properties=ensure_dict(evidence.get("properties") or {}),
    )
    calls = build_prepared_execution_plan(params)
    if calls != expected_calls:
        raise RuntimeError("Prepared optimizer Core calls drifted.")
    steps: list[dict[str, Any]] = []
    written_values: dict[str, Any] = {}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    for index, (tool_name, tool_arguments) in enumerate(calls):
        result = ensure_dict_payload(
            extract_tool_result_payload(invoke_unity_mcp(settings, tool_name, tool_arguments)),
            "optimizer component write",
        )
        if result.get("ok") is False:
            raise RuntimeError(result.get("error") or f"Optimizer Core write failed: {tool_name}")
        if tool_name == "vrc_set_property":
            property_path = str(tool_arguments.get("propertyPath") or "")
            if "newValue" not in result:
                raise RuntimeError(f"Optimizer Core write omitted readback value: {property_path}")
            written_values[property_path] = copy.deepcopy(result["newValue"])
        steps.append({"id": f"core_{index}", "tool": tool_name, "status": "done", "result": redact_support_payload(result)})
        if tool_name == "vrc_add_component":
            added_present, added_index, _added_component, _added_components = _optimizer_component_snapshot(
                project_path, target_path, component_type,
            )
            if not added_present or added_index != component_index:
                raise RuntimeError("Optimizer component add result did not match the approved insertion index.")
    readback: list[dict[str, Any]] = []
    for property_path, expected_value in ensure_dict(evidence.get("properties") or {}).items():
        read = read_component_property_sync({
            "projectPath": project_path,
            "gameObjectPath": target_path,
            "componentType": component_type,
            "componentIndex": component_index,
            "propertyPath": property_path,
        })
        if not read.get("ok"):
            raise RuntimeError(read.get("error") or f"Optimizer property readback failed: {property_path}")
        if "propertyValue" not in read:
            raise RuntimeError(f"Optimizer property readback omitted value: {property_path}")
        if property_path not in written_values or shader_evidence_sha256(read["propertyValue"]) != shader_evidence_sha256(written_values[property_path]):
            raise RuntimeError(f"Optimizer property readback drifted after write: {property_path}")
        simple_expected = isinstance(expected_value, (str, bool, int, float))
        if isinstance(expected_value, list) and isinstance(read["propertyValue"], list):
            simple_expected = all(isinstance(item, (str, bool, int, float, type(None))) for item in expected_value + read["propertyValue"])
        if simple_expected and shader_evidence_sha256(read["propertyValue"]) != shader_evidence_sha256(expected_value):
            raise RuntimeError(f"Optimizer property readback did not match the approved value: {property_path}")
        readback.append({"propertyPath": property_path, "expected": expected_value, "result": read})
    emit_log("info", "optimization", "Optimizer component configured.", {"optimizerId": params.get("optimizerId"), "mode": params.get("mode")})
    return {
        "ok": True,
        "schema": "vrcforge.optimization.configure_component.v1",
        "optimizerId": params.get("optimizerId"),
        "mode": params.get("mode"),
        "profile": params.get("profile"),
        "avatarPath": params.get("avatarPath"),
        "targetPath": target_path,
        "componentType": component_type,
        "componentIndex": component_index,
        "steps": steps,
        "readback": readback,
        "validationRequired": False,
        "readbackVerified": True,
        "rollbackProofRequired": True,
    }


def build_component_target(params: dict[str, Any]) -> tuple[str, str]:
    return (
        str(
            params.get("game_object_path")
            or params.get("gameObjectPath")
            or params.get("object_path")
            or params.get("objectPath")
            or ""
        ).strip(),
        str(params.get("component_type") or params.get("componentType") or "").strip(),
    )


def read_component_property_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    go_path, comp_type = build_component_target(params)
    if not go_path:
        return {"ok": False, "error": "gameObjectPath is required."}
    if not comp_type:
        return {"ok": False, "error": "componentType is required."}
    prop = str(params.get("property_path") or params.get("propertyPath") or "").strip()
    if not prop:
        return {"ok": False, "error": "propertyPath is required."}
    request = {
        "gameObjectPath": go_path,
        "componentType": comp_type,
        "propertyPath": prop,
        "componentIndex": int(params.get("component_index", params.get("componentIndex", 0)) or 0),
    }
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_get_property", request)),
        "get property",
    )
    payload.setdefault("ok", True)
    return payload


def add_component_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    go_path, comp_type = build_component_target(params)
    if not go_path:
        return {"ok": False, "error": "gameObjectPath is required."}
    if not comp_type:
        return {"ok": False, "error": "componentType is required."}
    preview = bool(params.get("preview", False))
    request = {"gameObjectPath": go_path, "componentType": comp_type, "preview": preview}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_add_component", request)),
        "add component",
    )
    payload.setdefault("ok", True)
    if not preview:
        emit_log("info", "component", "Component added.", {"gameObjectPath": go_path, "componentType": comp_type})
    return payload


def remove_component_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    go_path, comp_type = build_component_target(params)
    if not go_path:
        return {"ok": False, "error": "gameObjectPath is required."}
    if not comp_type:
        return {"ok": False, "error": "componentType is required."}
    preview = bool(params.get("preview", False))
    request = {
        "gameObjectPath": go_path,
        "componentType": comp_type,
        "componentIndex": int(params.get("component_index", params.get("componentIndex", 0)) or 0),
        "preview": preview,
    }
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_remove_component", request)),
        "remove component",
    )
    payload.setdefault("ok", True)
    if not preview:
        emit_log("info", "component", "Component removed.", {"gameObjectPath": go_path, "componentType": comp_type})
    return payload


def set_component_property_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    go_path, comp_type = build_component_target(params)
    if not go_path:
        return {"ok": False, "error": "gameObjectPath is required."}
    if not comp_type:
        return {"ok": False, "error": "componentType is required."}
    prop = str(params.get("property_path") or params.get("propertyPath") or "").strip()
    if not prop:
        return {"ok": False, "error": "propertyPath is required."}
    if "value" not in params:
        return {"ok": False, "error": "value is required."}
    preview = bool(params.get("preview", False))
    request = {
        "gameObjectPath": go_path,
        "componentType": comp_type,
        "propertyPath": prop,
        "componentIndex": int(params.get("component_index", params.get("componentIndex", 0)) or 0),
        "preview": preview,
        "value": params.get("value"),
    }
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_set_property", request)),
        "set property",
    )
    payload.setdefault("ok", True)
    if not preview:
        emit_log("info", "component", "Component property set.", {"gameObjectPath": go_path, "componentType": comp_type, "propertyPath": prop})
    return payload



def build_gameobject_target(params: dict[str, Any]) -> str:
    return str(
        params.get("game_object_path")
        or params.get("gameObjectPath")
        or params.get("object_path")
        or params.get("objectPath")
        or ""
    ).strip()


def get_gameobject_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    go_path = build_gameobject_target(params)
    if not go_path:
        return {"ok": False, "error": "gameObjectPath is required."}
    request = {"gameObjectPath": go_path}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_get_gameobject", request)),
        "get gameobject",
    )
    payload.setdefault("ok", True)
    return payload


def create_gameobject_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    name = str(params.get("name") or "").strip()
    parent_path = str(params.get("parent_path") or params.get("parentPath") or "").strip()
    preview = bool(params.get("preview", False))
    request = {"name": name, "parentPath": parent_path, "preview": preview}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_create_gameobject", request)),
        "create gameobject",
    )
    payload.setdefault("ok", True)
    if not preview:
        emit_log("info", "gameobject", "GameObject created.", {"name": name or "GameObject", "parentPath": parent_path})
    return payload


def rename_gameobject_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    go_path = build_gameobject_target(params)
    if not go_path:
        return {"ok": False, "error": "gameObjectPath is required."}
    new_name = str(params.get("new_name") or params.get("newName") or "").strip()
    if not new_name:
        return {"ok": False, "error": "newName is required."}
    preview = bool(params.get("preview", False))
    request = {"gameObjectPath": go_path, "newName": new_name, "preview": preview}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_rename_gameobject", request)),
        "rename gameobject",
    )
    payload.setdefault("ok", True)
    if not preview:
        emit_log("info", "gameobject", "GameObject renamed.", {"gameObjectPath": go_path, "newName": new_name})
    return payload


def reparent_gameobject_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    go_path = build_gameobject_target(params)
    if not go_path:
        return {"ok": False, "error": "gameObjectPath is required."}
    new_parent_path = str(params.get("new_parent_path") or params.get("newParentPath") or "").strip()
    world_position_stays = bool(params.get("world_position_stays", params.get("worldPositionStays", True)))
    preview = bool(params.get("preview", False))
    request = {
        "gameObjectPath": go_path,
        "newParentPath": new_parent_path,
        "worldPositionStays": world_position_stays,
        "preview": preview,
    }
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_reparent_gameobject", request)),
        "reparent gameobject",
    )
    payload.setdefault("ok", True)
    if not preview:
        emit_log("info", "gameobject", "GameObject reparented.", {"gameObjectPath": go_path, "newParentPath": new_parent_path})
    return payload


def delete_gameobject_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    go_path = build_gameobject_target(params)
    if not go_path:
        return {"ok": False, "error": "gameObjectPath is required."}
    preview = bool(params.get("preview", False))
    request = {"gameObjectPath": go_path, "preview": preview}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_delete_gameobject", request)),
        "delete gameobject",
    )
    payload.setdefault("ok", True)
    if not preview:
        emit_log("info", "gameobject", "GameObject deleted.", {"gameObjectPath": go_path})
    return payload


def set_gameobject_active_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    go_path = build_gameobject_target(params)
    if not go_path:
        return {"ok": False, "error": "gameObjectPath is required."}
    if "active" not in params and "isActive" not in params:
        return {"ok": False, "error": "active is required."}
    active = bool(params.get("active", params.get("isActive")))
    preview = bool(params.get("preview", False))
    request = {"gameObjectPath": go_path, "active": active, "preview": preview}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_set_gameobject_active", request)),
        "set gameobject active",
    )
    payload.setdefault("ok", True)
    if not preview:
        emit_log("info", "gameobject", "GameObject active state set.", {"gameObjectPath": go_path, "active": active})
    return payload


def build_asset_path_target(params: dict[str, Any]) -> str:
    return str(
        params.get("asset_path")
        or params.get("assetPath")
        or ""
    ).strip()


def find_assets_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = {
        "query": str(params.get("query") or "").strip(),
        "typeName": str(params.get("type_name") or params.get("typeName") or "").strip(),
        "folder": str(params.get("folder") or "").strip(),
        "limit": int(params.get("limit", 50) or 50),
    }
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_find_assets", request)),
        "find assets",
    )
    payload.setdefault("ok", True)
    return payload


def get_asset_info_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    asset_path = build_asset_path_target(params)
    guid = str(params.get("guid") or "").strip()
    if not asset_path and not guid:
        return {"ok": False, "error": "assetPath or guid is required."}
    request = {"assetPath": asset_path, "guid": guid}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_get_asset_info", request)),
        "get asset info",
    )
    payload.setdefault("ok", True)
    return payload


def instantiate_prefab_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    asset_path = build_asset_path_target(params)
    guid = str(params.get("guid") or "").strip()
    if not asset_path and not guid:
        return {"ok": False, "error": "assetPath or guid is required."}
    parent_path = str(params.get("parent_path") or params.get("parentPath") or "").strip()
    name = str(params.get("name") or "").strip()
    world_position_stays = bool(params.get("world_position_stays", params.get("worldPositionStays", True)))
    preview = bool(params.get("preview", False))
    request = {
        "assetPath": asset_path,
        "guid": guid,
        "parentPath": parent_path,
        "name": name,
        "worldPositionStays": world_position_stays,
        "preview": preview,
    }
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_instantiate_prefab", request)),
        "instantiate prefab",
    )
    payload.setdefault("ok", True)
    if not preview:
        emit_log("info", "prefab", "Prefab instantiated.", {"assetPath": asset_path or guid, "parentPath": parent_path})
    return payload


def unpack_prefab_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    go_path = build_gameobject_target(params)
    if not go_path:
        return {"ok": False, "error": "gameObjectPath is required."}
    mode = str(params.get("mode") or "outermost").strip()
    preview = bool(params.get("preview", False))
    request = {"gameObjectPath": go_path, "mode": mode, "preview": preview}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_unpack_prefab", request)),
        "unpack prefab",
    )
    payload.setdefault("ok", True)
    if not preview:
        emit_log("info", "prefab", "Prefab instance unpacked.", {"gameObjectPath": go_path, "mode": mode})
    return payload


OUTFIT_IMPORT_ALLOWED_SUFFIXES = {
    ".prefab",
    ".mat",
    ".png",
    ".jpg",
    ".jpeg",
    ".tga",
    ".psd",
    ".exr",
    ".fbx",
    ".blend",
    ".obj",
    ".asset",
    ".controller",
    ".anim",
}
OUTFIT_IMPORT_MAX_NESTED_UNITYPACKAGE_BYTES = 512 * 1024 * 1024
OUTFIT_IMPORT_MAX_NESTED_UNITYPACKAGE_RATIO = 100.0
OUTFIT_IMPORT_JOB_TIMEOUT_SECONDS = 180.0
OUTFIT_IMPORT_JOB_POLL_SECONDS = 0.5


def _prepared_import_path_identity(path: Path, label: str) -> dict[str, Any]:
    try:
        identity, digest = capture_regular_file(path.expanduser(), label=f"Prepared outfit {label}")
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return {**identity, "sha256": digest}


def _prepared_import_project_identity(project_root: Path) -> dict[str, Any]:
    candidate = Path(os.path.abspath(project_root.expanduser()))
    if not _is_unity_project_root(candidate):
        raise RuntimeError("Prepared outfit import project/Assets identity is invalid.")
    try:
        project = capture_directory(candidate, label="Prepared outfit Unity project")
        assets = capture_directory(candidate / "Assets", label="Prepared outfit Unity Assets")
        packages = capture_directory(candidate / "Packages", label="Prepared outfit Unity Packages")
        project_settings = capture_directory(
            candidate / "ProjectSettings",
            label="Prepared outfit Unity ProjectSettings",
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return {
        "projectPath": project["path"],
        "project": project,
        "assets": assets,
        "packages": packages,
        "projectSettings": project_settings,
    }


def _require_prepared_import_evidence(expected: Any, actual: Any, label: str) -> None:
    if shader_evidence_sha256(expected) != shader_evidence_sha256(actual):
        raise RuntimeError(f"Prepared outfit import {label} drifted after approval.")


def _prepared_outfit_expected_asset_paths(plan_payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for raw_path in plan_payload.get("expectedAssetPaths") or []:
        asset_path = str(raw_path or "").replace("\\", "/").strip()
        parts = PurePosixPath(asset_path).parts
        if (
            len(parts) < 2
            or parts[0] != "Assets"
            or any(part in {"", ".", ".."} for part in parts)
            or "//" in asset_path
        ):
            raise RuntimeError("Prepared outfit expected asset path is invalid.")
        folded = asset_path.casefold()
        if folded in seen:
            raise RuntimeError("Prepared outfit expected asset paths contain a duplicate.")
        seen.add(folded)
        paths.append(asset_path)
    return paths


def _verify_prepared_import_project_identity(project_identity: dict[str, Any]) -> Path:
    try:
        project_root = verify_directory(project_identity["project"], label="Prepared outfit Unity project")
        verify_directory(project_identity["assets"], label="Prepared outfit Unity Assets")
        verify_directory(project_identity["packages"], label="Prepared outfit Unity Packages")
        verify_directory(project_identity["projectSettings"], label="Prepared outfit Unity ProjectSettings")
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Prepared outfit import project identity drifted after approval.") from exc
    if str(project_root) != str(project_identity.get("projectPath") or ""):
        raise RuntimeError("Prepared outfit import project path drifted after approval.")
    return project_root


def _require_prepared_import_asset_receipts(
    payload: dict[str, Any],
    expected_asset_paths: list[str],
) -> list[dict[str, str]]:
    raw_receipts = payload.get("expectedAssets")
    if not isinstance(raw_receipts, list) or len(raw_receipts) != len(expected_asset_paths):
        raise RuntimeError("Unity Core expected-asset receipt count did not match approval.")
    receipts: list[dict[str, str]] = []
    for expected_path, raw_receipt in zip(expected_asset_paths, raw_receipts, strict=True):
        if not isinstance(raw_receipt, dict):
            raise RuntimeError("Unity Core expected-asset receipt is invalid.")
        asset_path = str(raw_receipt.get("assetPath") or "").replace("\\", "/")
        guid = str(raw_receipt.get("guid") or "").strip().lower()
        asset_type = str(raw_receipt.get("assetType") or "").strip()
        if (
            asset_path != expected_path
            or len(guid) != 32
            or any(character not in "0123456789abcdef" for character in guid)
            or not asset_type
        ):
            raise RuntimeError("Unity Core expected-asset readback did not match approval.")
        receipts.append({"assetPath": asset_path, "guid": guid, "assetType": asset_type})
    return receipts


def _require_prepared_import_job_receipt(
    payload: dict[str, Any],
    project_identity: dict[str, Any],
    identity: dict[str, Any],
    expected_asset_paths: list[str],
) -> str:
    job_id = str(payload.get("jobId") or "").strip().lower()
    if len(job_id) != 32 or any(character not in "0123456789abcdef" for character in job_id):
        raise RuntimeError("Unity Core import job receipt is invalid.")
    if (
        str(payload.get("projectPath") or "") != project_identity["projectPath"]
        or str(payload.get("unityPackagePath") or "").replace("\\", "/")
        != str(identity["path"]).replace("\\", "/")
        or str(payload.get("expectedSha256") or "").lower() != str(identity["sha256"]).lower()
        or int(payload.get("expectedSize", -1)) != int(identity["size"])
    ):
        raise RuntimeError("Unity Core import receipt project/path did not match the prepared call.")
    raw_paths = payload.get("expectedAssetPaths")
    if not isinstance(raw_paths, list) or [str(path).replace("\\", "/") for path in raw_paths] != expected_asset_paths:
        raise RuntimeError("Unity Core import receipt asset paths did not match approval.")
    return job_id


def _wait_for_unitypackage_import_job(
    settings: Any,
    initial_payload: dict[str, Any],
) -> dict[str, Any]:
    job_id = str(initial_payload.get("jobId") or "").strip().lower()
    if len(job_id) != 32 or any(character not in "0123456789abcdef" for character in job_id):
        raise RuntimeError("Unity Core import job id is invalid.")
    poll_settings = copy.copy(settings)
    try:
        poll_settings.unity_mcp_timeout_seconds = min(
            int(getattr(settings, "unity_mcp_timeout_seconds", 30) or 30), 8
        )
    except Exception:  # noqa: BLE001 - tests may use a minimal settings object.
        pass
    deadline = time.monotonic() + OUTFIT_IMPORT_JOB_TIMEOUT_SECONDS
    payload = initial_payload
    while payload.get("pending") is True and time.monotonic() < deadline:
        time.sleep(min(OUTFIT_IMPORT_JOB_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        if time.monotonic() >= deadline:
            break
        payload = ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(
                    poll_settings,
                    "vrc_import_unitypackage",
                    {"jobId": job_id},
                    execution_context={"lane": "app_unitypackage_import_poll"},
                )
            ),
            "prepared unitypackage import job",
        )
        if str(payload.get("jobId") or "").strip().lower() != job_id:
            raise RuntimeError("Unity Core import job identity drifted while polling.")
    if payload.get("pending") is True:
        return {
            "ok": False,
            "pending": False,
            "status": "timeout",
            "jobId": job_id,
            "mutationStarted": True,
            "committed": True,
            "commitState": "unknown",
            "checkpointRecoveryRequired": True,
            "error": "UnityPackage import did not reach a terminal state before timeout.",
        }
    return payload


def _prepared_outfit_unitypackage_queue(plan_payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = ensure_dict_payload(plan_payload.get("source"), "outfit import source")
    raw_queue = source.get("importQueue")
    if not isinstance(raw_queue, list) or not raw_queue:
        dependency = plan_payload.get("dependencyPreflight") if isinstance(plan_payload.get("dependencyPreflight"), dict) else {}
        package_order = dependency.get("packageOrder") if isinstance(dependency.get("packageOrder"), dict) else {}
        raw_queue = package_order.get("importQueue") if isinstance(package_order.get("importQueue"), list) else []
    if not raw_queue:
        raw_queue = [{"path": source.get("actualPackagePath") or source.get("path"), "role": "target", "order": 1}]
    queue: list[dict[str, Any]] = []
    materializations: list[dict[str, Any]] = []
    temp_parent = _outfit_import_temp_dir()
    for index, raw in enumerate(raw_queue, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError("Prepared outfit import queue item is invalid.")
        source_type = str(raw.get("sourceType") or "").strip().lower()
        materialization_index: int | None = None
        folder_root_identity: dict[str, Any] | None = None
        if source_type == "zip":
            source = ensure_dict_payload(plan_payload.get("source"), "outfit import source")
            container_value = str(raw.get("containerPath") or source.get("path") or "").strip()
            container_path = Path(os.path.abspath(Path(container_value).expanduser()))
            entry_path = normalize_archive_name(str(raw.get("path") or ""))
            target_name = f"prepared-{secrets.token_hex(16)}-{index:04d}.unitypackage"
            try:
                materialization = prepare_zip_member_materialization(
                    source=container_path,
                    temp_parent=temp_parent,
                    selected_members=[{"path": entry_path, "targetName": target_name}],
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            selected = ensure_dict_payload(materialization["selected"][0], "prepared nested UnityPackage")
            materialization_index = len(materializations)
            materializations.append(materialization)
            identity = {
                "path": str(temp_parent / target_name),
                "sha256": selected["sha256"],
                "size": selected["size"],
            }
        else:
            raw_path = str(raw.get("actualPackagePath") or "").strip()
            if not raw_path and source_type == "folder":
                source = ensure_dict_payload(plan_payload.get("source"), "outfit import source")
                source_root = Path(os.path.abspath(Path(str(source.get("path") or "")).expanduser()))
                try:
                    folder_root_identity = capture_directory(source_root, label="Prepared outfit source folder")
                except ValueError as exc:
                    raise RuntimeError(str(exc)) from exc
                relative = PurePosixPath(normalize_archive_name(str(raw.get("path") or "")))
                if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                    raise RuntimeError("Prepared outfit folder queue path is unsafe.")
                path = source_root.joinpath(*relative.parts)
            else:
                raw_path = raw_path or str(raw.get("path") or "").strip()
                path = Path(os.path.abspath(Path(raw_path).expanduser()))
            if path.suffix.lower() != ".unitypackage":
                raise RuntimeError(f"Prepared outfit import queue item is not a UnityPackage: {path}")
            identity = _prepared_import_path_identity(path, "UnityPackage")
        queue.append({
            "order": int(raw.get("order") or index),
            "role": str(raw.get("role") or "target"),
            "identity": identity,
            "materializationIndex": materialization_index,
            "folderRootIdentity": folder_root_identity,
        })
    queue.sort(key=lambda item: item["order"])
    if len({item["order"] for item in queue}) != len(queue):
        raise RuntimeError("Prepared outfit import queue has duplicate order values.")
    return queue, materializations


def prepare_outfit_import_package_request(arguments: dict[str, Any], preview: Any) -> tuple[dict[str, Any], Any]:
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
        raise RuntimeError("Caller may not provide the reserved prepared Unity execution key.")
    plan = plan_outfit_import_sync(arguments)
    plan_payload = ensure_dict_payload(plan.get("plan"), "outfit import plan")
    if not plan_payload.get("readyToApply"):
        raise RuntimeError("Outfit import plan is not ready to apply.")
    kind = str(plan_payload.get("kind") or "")
    project_root = _resolve_unity_project_root_for_import(arguments, plan_payload)
    project_identity = _prepared_import_project_identity(project_root)
    if kind == "loose_prefab_copy":
        source = ensure_dict_payload(plan_payload.get("source"), "outfit import source")
        try:
            loose_plan = prepare_loose_outfit_import(
                source_root=Path(str(source.get("path") or "")),
                project_root=project_root,
                target_folder=str(plan_payload.get("targetFolder") or "Assets/VRCForge/ImportedOutfits"),
                allowed_suffixes=frozenset(OUTFIT_IMPORT_ALLOWED_SUFFIXES),
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        calls = [("vrc_refresh_asset_database", {"projectPath": project_identity["projectPath"], "resolvePackages": False, "packageResolveTimeoutSeconds": 120})]
        evidence = {
            "kind": kind,
            "planSha256": shader_evidence_sha256(plan_payload),
            "plan": plan_payload,
            "projectIdentity": project_identity,
            "loosePlan": loose_plan,
        }
        return install_prepared_calls(arguments, calls, evidence), {"ok": True, "plan": plan_payload, "preparedFileCount": len(loose_plan["files"])}
    if kind not in {"unitypackage_import", "unitypackage_import_sequence"}:
        raise RuntimeError(f"Unsupported prepared outfit import branch: {kind or 'unknown'}")
    queue, materializations = _prepared_outfit_unitypackage_queue(plan_payload)
    expected_asset_paths = _prepared_outfit_expected_asset_paths(plan_payload)
    target_indexes = [index for index, item in enumerate(queue) if item["role"] == "target"]
    if len(target_indexes) != 1:
        raise RuntimeError("Prepared outfit import queue must contain exactly one target package.")
    calls = [
        (
            "vrc_import_unitypackage",
            {
                "projectPath": project_identity["projectPath"],
                "unityPackagePath": item["identity"]["path"],
                "expectedSha256": item["identity"]["sha256"],
                "expectedSize": item["identity"]["size"],
                "expectedAssetPaths": expected_asset_paths if index == target_indexes[0] else [],
                "interactive": False,
            },
        )
        for index, item in enumerate(queue)
    ]
    calls.append(("vrc_refresh_asset_database", {"projectPath": project_identity["projectPath"], "resolvePackages": False, "packageResolveTimeoutSeconds": 120}))
    evidence = {
        "kind": kind,
        "planSha256": shader_evidence_sha256(plan_payload),
        "plan": plan_payload,
        "projectIdentity": project_identity,
        "queue": queue,
        "materializations": materializations,
        "expectedAssetPaths": expected_asset_paths,
        "targetIndex": target_indexes[0],
    }
    return install_prepared_calls(arguments, calls, evidence), {"ok": True, "plan": plan_payload, "preparedQueueCount": len(queue)}


def import_outfit_package_approved_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    imports: list[dict[str, Any]] = []
    core_call_started = False
    import_readback_pending = False
    materialization_receipts: list[dict[str, Any]] = []

    def cleanup_materializations() -> str:
        errors: list[str] = []
        for receipt in reversed(materialization_receipts):
            error = cleanup_owned_zip_materialization(receipt)
            if error:
                errors.append(error)
        materialization_receipts.clear()
        return "; ".join(errors)

    try:
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict):
            raise RuntimeError("Prepared outfit import evidence is invalid.")
        kind = str(evidence.get("kind") or "")
        if kind not in {"unitypackage_import", "unitypackage_import_sequence", "loose_prefab_copy"}:
            raise RuntimeError("Prepared outfit import branch is invalid.")
        plan_payload = evidence.get("plan")
        if not isinstance(plan_payload, dict) or shader_evidence_sha256(plan_payload) != evidence.get("planSha256"):
            raise RuntimeError("Prepared outfit import plan evidence is invalid.")
        project_identity = evidence.get("projectIdentity")
        if kind == "loose_prefab_copy":
            if not isinstance(project_identity, dict):
                raise RuntimeError("Prepared loose outfit project identity is invalid.")
            _verify_prepared_import_project_identity(project_identity)
            loose_plan = evidence.get("loosePlan")
            if not isinstance(loose_plan, dict):
                raise RuntimeError("Prepared loose outfit plan is missing.")
            try:
                copied = execute_loose_outfit_import(loose_plan)
            except RuntimeError as exc:
                return {"ok": False, "committed": True, "commitState": "unknown", "checkpointRecoveryRequired": True, "kind": kind, "error": str(exc)}
            _verify_prepared_import_project_identity(project_identity)
            refresh_tool, refresh_arguments = prepared_call(arguments, 0)
            expected_refresh = {"projectPath": project_identity["projectPath"], "resolvePackages": False, "packageResolveTimeoutSeconds": 120}
            if refresh_tool != "vrc_refresh_asset_database":
                raise RuntimeError("Prepared loose outfit refresh call is invalid.")
            _require_prepared_import_evidence(expected_refresh, refresh_arguments, "refresh arguments")
            core_call_started = True
            settings = load_dashboard_settings(build_agent_connection_request(arguments))
            settings.unity_mcp_timeout_seconds = max(int(settings.unity_mcp_timeout_seconds or 30), 150)
            refresh = ensure_dict_payload(extract_tool_result_payload(invoke_unity_mcp(settings, refresh_tool, refresh_arguments)), "prepared loose outfit refresh")
            if refresh.get("ok") is not True:
                return {"ok": False, "committed": True, "commitState": "partial", "checkpointRecoveryRequired": True, "kind": kind, "copiedFiles": copied.get("copiedFiles") or [], "assetDatabaseRefresh": refresh, "error": refresh.get("error") or "Asset refresh failed after loose outfit import."}
            core_call_started = False
            prefab_assets = [str(path) for path in copied.get("copiedFiles") or [] if str(path).lower().endswith(".prefab")]
            return {"ok": True, "kind": kind, **copied, "importedPrefabCandidates": prefab_assets, "assetDatabaseRefresh": refresh, "nextTool": "vrcforge_add_outfit"}
        queue = evidence.get("queue")
        if not isinstance(project_identity, dict) or not isinstance(queue, list) or not queue:
            raise RuntimeError("Prepared outfit import evidence is incomplete.")
        _verify_prepared_import_project_identity(project_identity)
        materializations = evidence.get("materializations") or []
        if not isinstance(materializations, list):
            raise RuntimeError("Prepared outfit materialization evidence is invalid.")
        for facts in materializations:
            if not isinstance(facts, dict):
                raise RuntimeError("Prepared outfit materialization item is invalid.")
            materialization_receipts.append(execute_zip_member_materialization(facts))
        settings = load_dashboard_settings(build_agent_connection_request(arguments))
        settings.unity_mcp_timeout_seconds = max(int(settings.unity_mcp_timeout_seconds or 30), 300)
        for index, item in enumerate(queue):
            _verify_prepared_import_project_identity(project_identity)
            identity = item.get("identity") if isinstance(item, dict) else None
            if not isinstance(identity, dict):
                raise RuntimeError("Prepared outfit import queue identity is invalid.")
            materialization_index = item.get("materializationIndex") if isinstance(item, dict) else None
            try:
                if materialization_index is not None:
                    receipt = materialization_receipts[int(materialization_index)]
                    owned = receipt.get("ownedFiles") if isinstance(receipt, dict) else None
                    if not isinstance(owned, list) or len(owned) != 1:
                        raise RuntimeError("Prepared nested UnityPackage receipt is invalid.")
                    receipt_identity = ensure_dict_payload(owned[0], "prepared nested UnityPackage receipt")
                    if (
                        str(receipt_identity.get("path") or "") != str(identity.get("path") or "")
                        or int(receipt_identity.get("size", -1)) != int(identity.get("size", -2))
                        or str(receipt_identity.get("sha256") or "") != str(identity.get("sha256") or "")
                    ):
                        raise RuntimeError("Prepared nested UnityPackage receipt drifted from approval.")
                    verify_regular_file(
                        {key: value for key, value in receipt_identity.items() if key != "sha256"},
                        str(identity.get("sha256") or ""),
                        label="Prepared nested UnityPackage",
                    )
                else:
                    folder_identity = item.get("folderRootIdentity") if isinstance(item, dict) else None
                    if folder_identity is not None:
                        verify_directory(folder_identity, label="Prepared outfit source folder")
                    verify_regular_file(
                        {key: value for key, value in identity.items() if key != "sha256"},
                        str(identity.get("sha256") or ""),
                        label="Prepared outfit UnityPackage",
                    )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            tool_name, tool_arguments = prepared_call(arguments, index)
            expected_asset_paths = evidence.get("expectedAssetPaths") if index == evidence.get("targetIndex") else []
            if not isinstance(expected_asset_paths, list):
                raise RuntimeError("Prepared outfit expected asset evidence is invalid.")
            expected = {"projectPath": project_identity["projectPath"], "unityPackagePath": identity["path"], "expectedSha256": identity["sha256"], "expectedSize": identity["size"], "expectedAssetPaths": expected_asset_paths, "interactive": False}
            if tool_name != "vrc_import_unitypackage":
                raise RuntimeError("Prepared outfit import Core call is invalid.")
            _require_prepared_import_evidence(expected, tool_arguments, "Core arguments")
            core_call_started = True
            payload = ensure_dict_payload(extract_tool_result_payload(invoke_unity_mcp(settings, tool_name, tool_arguments)), "prepared unitypackage import")
            import_readback_pending = payload.get("mutationStarted") is True or payload.get("pending") is True
            if payload.get("ok") is not True:
                cleanup_error = cleanup_materializations()
                return {"ok": False, "committed": True, "commitState": "unknown", "checkpointRecoveryRequired": True, "kind": kind, "unityImports": imports, "temporaryCleanupError": cleanup_error or None, "error": payload.get("error") or "UnityPackage import failed after Core invocation."}
            job_id = _require_prepared_import_job_receipt(
                payload, project_identity, identity, expected_asset_paths
            )
            if payload.get("pending") is True:
                payload = _wait_for_unitypackage_import_job(settings, payload)
            if payload.get("ok") is not True or str(payload.get("status") or "") != "completed":
                cleanup_error = cleanup_materializations()
                return {
                    "ok": False,
                    "committed": True,
                    "commitState": "unknown",
                    "checkpointRecoveryRequired": True,
                    "kind": kind,
                    "unityImports": imports,
                    "temporaryCleanupError": cleanup_error or None,
                    "error": payload.get("error") or payload.get("reason") or "UnityPackage import did not complete.",
                }
            if str(payload.get("jobId") or "").strip().lower() != job_id:
                raise RuntimeError("Unity Core import terminal job identity drifted.")
            _require_prepared_import_job_receipt(
                payload, project_identity, identity, expected_asset_paths
            )
            asset_receipts = _require_prepared_import_asset_receipts(payload, expected_asset_paths)
            imports.append({"ok": True, "order": item["order"], "role": item["role"], "path": identity["path"], "expectedAssets": asset_receipts, "unityImport": payload})
            import_readback_pending = False
            core_call_started = False
        _verify_prepared_import_project_identity(project_identity)
        refresh_tool, refresh_arguments = prepared_call(arguments, len(queue))
        expected_refresh = {"projectPath": project_identity["projectPath"], "resolvePackages": False, "packageResolveTimeoutSeconds": 120}
        if refresh_tool != "vrc_refresh_asset_database":
            raise RuntimeError("Prepared outfit import refresh call is invalid.")
        _require_prepared_import_evidence(expected_refresh, refresh_arguments, "refresh arguments")
        core_call_started = True
        refresh = ensure_dict_payload(extract_tool_result_payload(invoke_unity_mcp(settings, refresh_tool, refresh_arguments)), "prepared outfit import refresh")
        if refresh.get("ok") is not True:
            cleanup_error = cleanup_materializations()
            return {"ok": False, "committed": True, "checkpointRecoveryRequired": True, "kind": kind, "unityImports": imports, "assetDatabaseRefresh": refresh, "temporaryCleanupError": cleanup_error or None, "error": refresh.get("error") or "Asset refresh failed after UnityPackage import."}
        core_call_started = False
        _verify_prepared_import_project_identity(project_identity)
        cleanup_error = cleanup_materializations()
        if cleanup_error:
            return {"ok": False, "committed": True, "commitState": "complete", "checkpointRecoveryRequired": False, "temporaryCleanupRequired": True, "kind": kind, "unityImports": imports, "assetDatabaseRefresh": refresh, "error": cleanup_error}
        return {"ok": True, "kind": kind, "unityImports": imports, "assetDatabaseRefresh": refresh, "importedPrefabCandidates": [path for path in evidence.get("expectedAssetPaths") or [] if str(path).lower().endswith(".prefab")], "nextTool": "vrcforge_add_outfit"}
    except (RuntimeError, UnityMcpError, ValueError) as exc:
        cleanup_error = cleanup_materializations()
        emit_log("error", "outfit", "Prepared outfit import failed.", {"error": str(exc)})
        if core_call_started or imports:
            return {
                "ok": False,
                "committed": True,
                "commitState": "unknown" if core_call_started or import_readback_pending else "partial",
                "checkpointRecoveryRequired": True,
                "kind": str(locals().get("kind") or ""),
                "unityImports": imports,
                "temporaryCleanupError": cleanup_error or None,
                "error": str(exc),
            }
        if cleanup_error:
            raise to_http_exception(RuntimeError(f"{exc}; temporary cleanup failed: {cleanup_error}")) from exc
        raise to_http_exception(exc) from exc


def import_outfit_package_sync(params: dict[str, Any]) -> dict[str, Any]:
    del params
    raise RuntimeError(
        "Direct outfit import is disabled. Create and approve a vrcforge_import_outfit_package request so exact source, target, checkpoint, and Core calls are sealed."
    )


def _outfit_import_temp_dir() -> Path:
    path = DASHBOARD_ARTIFACTS_DIR / "outfit-imports" / "temp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_outfit_import_queue(plan_payload: dict[str, Any], temp_root: Path) -> list[dict[str, Any]]:
    source = ensure_dict_payload(plan_payload.get("source"), "outfit import source")
    raw_queue = source.get("importQueue")
    if not isinstance(raw_queue, list) or not raw_queue:
        dependency = plan_payload.get("dependencyPreflight") if isinstance(plan_payload.get("dependencyPreflight"), dict) else {}
        package_order = dependency.get("packageOrder") if isinstance(dependency.get("packageOrder"), dict) else {}
        raw_queue = package_order.get("importQueue") if isinstance(package_order.get("importQueue"), list) else []
    resolved: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_queue, start=1):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        item.setdefault("order", index)
        item["resolvedPackagePath"] = str(_resolve_import_queue_package(item, source, temp_root))
        resolved.append(item)
    return sorted(resolved, key=lambda item: int(item.get("order") or 0))


def _resolve_import_queue_package(item: dict[str, Any], plan_source: dict[str, Any], temp_root: Path) -> Path:
    source_type = str(item.get("sourceType") or "").strip()
    actual = str(item.get("actualPackagePath") or "").strip()
    if actual:
        package_path = Path(actual).expanduser().resolve()
        if not package_path.is_file() or package_path.suffix.lower() != ".unitypackage":
            raise AgentGatewayError(f"Import queue item is not a UnityPackage: {package_path}", status_code=400)
        return package_path

    if source_type == "zip":
        container_path = Path(str(item.get("containerPath") or plan_source.get("path") or "")).expanduser().resolve()
        entry_path = str(item.get("path") or "").replace("\\", "/").strip("/")
        return _extract_unitypackage_from_zip(container_path, entry_path, temp_root)

    if source_type == "folder":
        source_root = Path(str(plan_source.get("path") or "")).expanduser().resolve()
        entry_path = str(item.get("path") or "").replace("\\", "/").strip("/")
        package_path = (source_root / entry_path).resolve()
        try:
            package_path.relative_to(source_root)
        except ValueError as exc:
            raise AgentGatewayError("Import queue item escapes the selected folder.", status_code=400) from exc
        if not package_path.is_file() or package_path.suffix.lower() != ".unitypackage":
            raise AgentGatewayError(f"Import queue item is not a UnityPackage: {entry_path}", status_code=400)
        return package_path

    direct_path = str(plan_source.get("actualPackagePath") or plan_source.get("path") or "").strip()
    package_path = Path(direct_path).expanduser().resolve()
    if not package_path.is_file() or package_path.suffix.lower() != ".unitypackage":
        raise AgentGatewayError(f"Import queue item is not a UnityPackage: {direct_path}", status_code=400)
    return package_path


def _extract_unitypackage_from_zip(container_path: Path, entry_path: str, temp_root: Path) -> Path:
    if not container_path.is_file() or container_path.suffix.lower() != ".zip":
        raise AgentGatewayError(f"ZIP container does not exist: {container_path}", status_code=400)
    normalized_entry = normalize_archive_name(entry_path)
    if not normalized_entry.lower().endswith(".unitypackage") or not is_safe_archive_path(normalized_entry):
        raise AgentGatewayError("ZIP import queue entry is not a safe UnityPackage path.", status_code=400)
    with zipfile.ZipFile(container_path) as archive:
        names = {normalize_archive_name(name): name for name in archive.namelist()}
        raw_name = names.get(normalized_entry)
        if raw_name is None:
            raise AgentGatewayError(f"UnityPackage entry was not found in ZIP: {normalized_entry}", status_code=400)
        info = archive.getinfo(raw_name)
        if info.file_size > OUTFIT_IMPORT_MAX_NESTED_UNITYPACKAGE_BYTES:
            raise AgentGatewayError("Nested UnityPackage exceeds the import size limit.", status_code=400)
        compression_ratio = float(info.file_size) / float(max(1, info.compress_size))
        if compression_ratio > OUTFIT_IMPORT_MAX_NESTED_UNITYPACKAGE_RATIO:
            raise AgentGatewayError("Nested UnityPackage compression ratio exceeds the import limit.", status_code=400)
        safe_name = sanitize_artifact_name(Path(normalized_entry).stem) or "package"
        target = (temp_root / f"{safe_name}_{int(time.time() * 1000)}.unitypackage").resolve()
        try:
            written = 0
            with archive.open(info) as source, target.open("wb") as destination:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > OUTFIT_IMPORT_MAX_NESTED_UNITYPACKAGE_BYTES:
                        raise AgentGatewayError("Nested UnityPackage exceeds the import size limit.", status_code=400)
                    destination.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return target


def import_unitypackage_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    package_path = str(params.get("unityPackagePath") or params.get("unity_package_path") or "").strip()
    if not package_path:
        return {"ok": False, "error": "unityPackagePath is required."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    settings.unity_mcp_timeout_seconds = max(int(settings.unity_mcp_timeout_seconds or 30), 300)
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_import_unitypackage", {
            "projectPath": str(params.get("projectPath") or params.get("project_path") or ""),
            "unityPackagePath": package_path,
            "interactive": False,
        })),
        "import unitypackage",
    )
    payload.setdefault("ok", True)
    return payload


def refresh_asset_database_sync(params: dict[str, Any]) -> dict[str, Any]:
    package_resolve_timeout = max(5, min(int((params or {}).get("packageResolveTimeoutSeconds") or 120), 300))
    settings = load_dashboard_settings(build_agent_connection_request(params or {}))
    settings.unity_mcp_timeout_seconds = max(int(settings.unity_mcp_timeout_seconds or 30), 120, package_resolve_timeout + 30)
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_refresh_asset_database", {
            "projectPath": str((params or {}).get("projectPath") or (params or {}).get("project_path") or ""),
            "resolvePackages": bool((params or {}).get("resolvePackages") or (params or {}).get("resolve_packages")),
            "packageResolveTimeoutSeconds": package_resolve_timeout,
        })),
        "refresh asset database",
    )
    payload.setdefault("ok", True)
    return payload


def _resolve_unity_project_root_for_import(params: dict[str, Any], plan_payload: dict[str, Any]) -> Path:
    value = str(params.get("projectPath") or params.get("project_path") or plan_payload.get("projectPath") or DASHBOARD_STATE.selected_project_path or "").strip()
    if not value:
        raise AgentGatewayError("projectPath is required for outfit import.", status_code=400)
    project_root = Path(value).expanduser().resolve()
    if not _is_unity_project_root(project_root):
        raise AgentGatewayError("projectPath must point to a Unity project root.", status_code=400)
    return project_root


def _is_unity_project_root(path: Path) -> bool:
    return (path / "Assets").is_dir() and (path / "Packages").is_dir() and (path / "ProjectSettings").is_dir()


def _copy_loose_outfit_assets(source_root: Path, project_root: Path, target_folder: str) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve()
    if not source_root.is_dir():
        raise AgentGatewayError("Loose outfit import requires a folder source.", status_code=400)
    target_asset_root = _resolve_import_target_folder(project_root, target_folder)
    copied: list[str] = []
    prefab_assets: list[str] = []
    for source in sorted((item for item in source_root.rglob("*") if item.is_file()), key=lambda item: str(item).lower()):
        if source.is_symlink():
            continue
        if source.suffix.lower() == ".meta":
            continue
        if source.suffix.lower() not in OUTFIT_IMPORT_ALLOWED_SUFFIXES:
            continue
        relative = source.relative_to(source_root)
        target = (target_asset_root / relative).resolve()
        _ensure_path_inside_project(project_root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        asset_path = target.relative_to(project_root).as_posix()
        copied.append(asset_path)
        if target.suffix.lower() == ".prefab":
            prefab_assets.append(asset_path)
        meta_source = source.with_name(source.name + ".meta")
        if meta_source.is_file():
            meta_target = target.with_name(target.name + ".meta")
            shutil.copy2(meta_source, meta_target)
            copied.append(meta_target.relative_to(project_root).as_posix())
    if not copied:
        raise AgentGatewayError("No importable loose outfit files were found.", status_code=400)
    return {"copiedFileCount": len(copied), "copiedFiles": copied, "prefabAssets": prefab_assets}


def _resolve_import_target_folder(project_root: Path, target_folder: str) -> Path:
    normalized = str(target_folder or "Assets/VRCForge/ImportedOutfits").replace("\\", "/").strip().strip("/")
    relative = PurePosixPath(normalized)
    parts = relative.parts
    if (
        len(parts) < 2
        or relative.is_absolute()
        or parts[0] != "Assets"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise AgentGatewayError("targetFolder must be under Assets/.", status_code=400)
    target = (project_root / relative.as_posix()).resolve()
    _ensure_path_inside_project(project_root, target)
    assets_root = (project_root / "Assets").resolve()
    try:
        target.relative_to(assets_root)
    except ValueError as exc:
        raise AgentGatewayError("targetFolder must stay under Assets/.", status_code=400) from exc
    return target


def _ensure_path_inside_project(project_root: Path, target: Path) -> None:
    try:
        target.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise AgentGatewayError("Resolved import target is outside the Unity project.", status_code=400) from exc


def _expected_prefab_assets(plan_payload: dict[str, Any]) -> list[str]:
    return [str(path) for path in (plan_payload.get("expectedAssetPaths") or []) if str(path).lower().endswith(".prefab")]


def _workflow_project_params(params: dict[str, Any]) -> dict[str, Any]:
    project_value = str(params.get("project_path") or params.get("projectPath") or "").strip()
    return {"projectPath": project_value} if project_value else {}


def _workflow_bool(params: dict[str, Any], keys: tuple[str, ...], default: bool) -> bool:
    for key in keys:
        if key not in params or params.get(key) is None:
            continue
        raw = params.get(key)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw != 0
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _workflow_parameter_name(params: dict[str, Any]) -> tuple[str, bool]:
    for key in ("parameter_name", "parameterName", "wardrobe_parameter", "wardrobeParameter"):
        value = str(params.get(key) or "").strip()
        if value:
            return value, True
    return "Clothes", False


def _wardrobe_parameter_names(scan_payload: dict[str, Any]) -> list[str]:
    wardrobes = scan_payload.get("wardrobes") if isinstance(scan_payload.get("wardrobes"), list) else []
    names: list[str] = []
    for wardrobe in wardrobes:
        if not isinstance(wardrobe, dict):
            continue
        name = str(wardrobe.get("parameterName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _wardrobe_candidate_parameter_names(scan_payload: dict[str, Any]) -> list[str]:
    candidates = scan_payload.get("wardrobeCandidates") if isinstance(scan_payload.get("wardrobeCandidates"), list) else []
    names: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        name = str(candidate.get("parameterName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _workflow_wardrobe_create_args(params: dict[str, Any], avatar_path: str, parameter_name: str) -> dict[str, Any]:
    result = {
        **_workflow_project_params(params),
        "avatarPath": avatar_path,
        "parameterName": parameter_name,
    }
    for src_key, dst_key in (
        ("menu_name", "menuName"),
        ("menuName", "menuName"),
        ("sub_menu_name", "subMenuName"),
        ("subMenuName", "subMenuName"),
        ("default_control_name", "defaultControlName"),
        ("defaultControlName", "defaultControlName"),
        ("layer_name", "layerName"),
        ("layerName", "layerName"),
        ("asset_dir", "assetDir"),
        ("assetDir", "assetDir"),
        ("clip_output_dir", "clipOutputDir"),
        ("clipOutputDir", "clipOutputDir"),
    ):
        value = str(params.get(src_key) or "").strip()
        if value:
            result[dst_key] = value
    for src_key, dst_key in (
        ("write_defaults", "writeDefaults"),
        ("writeDefaults", "writeDefaults"),
        ("saved", "saved"),
        ("network_synced", "networkSynced"),
        ("networkSynced", "networkSynced"),
    ):
        if src_key in params and params.get(src_key) is not None:
            result[dst_key] = params.get(src_key)
    return result


def _resolve_workflow_asset(params: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    asset_path = build_asset_path_target(params)
    guid = str(params.get("guid") or "").strip()
    if asset_path or guid:
        return {"assetPath": asset_path, "guid": guid, "source": "explicit"}, None
    query = str(params.get("query") or params.get("asset_query") or params.get("assetQuery") or "").strip()
    if not query:
        return None, {"ok": False, "error": "assetPath, guid, or assetQuery/query is required."}
    search = find_assets_sync({
        **_workflow_project_params(params),
        "query": query,
        "typeName": str(params.get("type_name") or params.get("typeName") or "Prefab").strip() or "Prefab",
        "folder": str(params.get("folder") or "").strip(),
        "limit": 1,
    })
    if not search.get("ok"):
        return None, search
    assets = search.get("assets") if isinstance(search.get("assets"), list) else []
    if not assets:
        return None, {"ok": False, "error": f"No prefab asset matched query '{query}'."}
    first = ensure_dict_payload(assets[0], "workflow asset")
    return {
        "assetPath": str(first.get("assetPath") or ""),
        "guid": str(first.get("guid") or ""),
        "name": str(first.get("name") or ""),
        "source": "query",
        "query": query,
    }, None


ADD_OUTFIT_CONTINUATION_NONCE_KEY = "__vrcforgeAddOutfitContinuationNonce"


def _canonical_add_outfit_asset(payload: dict[str, Any]) -> dict[str, Any]:
    asset_path = str(payload.get("assetPath") or "").replace("\\", "/").strip()
    guid = str(payload.get("guid") or "").strip().lower()
    dependency_hash = str(payload.get("dependencyHash") or "").strip().lower()
    if (
        payload.get("ok") is not True
        or payload.get("isPrefab") is not True
        or not asset_path.startswith("Assets/")
        or ".." in PurePosixPath(asset_path).parts
        or len(guid) != 32
        or any(character not in "0123456789abcdef" for character in guid)
        or len(dependency_hash) != 32
        or any(character not in "0123456789abcdef" for character in dependency_hash)
    ):
        raise RuntimeError("Add Outfit prefab asset identity is incomplete or invalid.")
    return {
        "assetPath": asset_path,
        "guid": guid,
        "dependencyHash": dependency_hash,
        "name": str(payload.get("name") or payload.get("prefabRootName") or "").strip(),
        "assetType": str(payload.get("assetType") or "").strip(),
        "prefabAssetType": str(payload.get("prefabAssetType") or "").strip(),
    }


def _canonical_add_outfit_gameobject(payload: dict[str, Any], label: str) -> dict[str, Any]:
    path = str(payload.get("gameObjectPath") or "").replace("\\", "/").strip().strip("/")
    global_id = str(payload.get("globalObjectId") or "").strip()
    scene_path = str(payload.get("scenePath") or "").replace("\\", "/").strip()
    count = int(payload.get("hierarchyPathCount", 0) or 0)
    if payload.get("ok") is not True or not path or not global_id or not scene_path or count != 1:
        raise RuntimeError(f"Add Outfit {label} identity is incomplete or ambiguous.")
    raw_children = payload.get("children")
    if not isinstance(raw_children, list):
        raise RuntimeError(f"Add Outfit {label} child readback is invalid.")
    children = sorted(
        str(item.get("gameObjectPath") or "").replace("\\", "/").strip().strip("/")
        for item in raw_children
        if isinstance(item, dict)
    )
    return {"gameObjectPath": path, "globalObjectId": global_id, "scenePath": scene_path, "children": children}


def _selected_add_outfit_wardrobe(scan: dict[str, Any], parameter_name: str, explicit: bool) -> tuple[dict[str, Any], str, int]:
    if scan.get("ok") is not True:
        raise RuntimeError(scan.get("error") or "Wardrobe scan failed during Add Outfit preparation.")
    fingerprint = str(scan.get("fingerprint") or "").strip().lower()
    if len(fingerprint) != 64 or any(character not in "0123456789abcdef" for character in fingerprint):
        raise RuntimeError("Wardrobe scan fingerprint is invalid.")
    wardrobes = [item for item in (scan.get("wardrobes") or []) if isinstance(item, dict)]
    if not wardrobes:
        candidates = _wardrobe_candidate_parameter_names(scan)
        detail = f" Candidate groups: {', '.join(candidates)}." if candidates else ""
        raise RuntimeError("Add Outfit requires an existing verified wardrobe. Approve vrcforge_create_wardrobe first, then retry." + detail)
    selected: dict[str, Any] | None = None
    if explicit:
        selected = next((item for item in wardrobes if str(item.get("parameterName") or "") == parameter_name), None)
        if selected is None:
            raise RuntimeError(f"Verified wardrobe '{parameter_name}' was not found. Create or repair it in a separate approved action, then retry.")
    else:
        selected = wardrobes[0]
        parameter_name = str(selected.get("parameterName") or "").strip()
    outfits = selected.get("outfits")
    if not parameter_name or not isinstance(outfits, list):
        raise RuntimeError("Selected wardrobe readback is invalid.")
    values = [int(item.get("value")) for item in outfits if isinstance(item, dict) and item.get("value") is not None]
    assigned_value = (max(values) if values else 0) + 1
    return copy.deepcopy(selected), fingerprint, assigned_value


def _prepare_add_outfit_state(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    continuation_nonce = str(params.get(ADD_OUTFIT_CONTINUATION_NONCE_KEY) or "").strip().lower()
    if continuation_nonce and (len(continuation_nonce) != 64 or any(character not in "0123456789abcdef" for character in continuation_nonce)):
        raise RuntimeError("Prepared Add Outfit continuation nonce is invalid.")
    project_root = _resolve_unity_project_root_for_import(params, {})
    project_identity = _prepared_import_project_identity(project_root)
    project_params = {"projectPath": project_identity["projectPath"]}
    asset_ref, asset_error = _resolve_workflow_asset({**params, **project_params})
    if asset_error:
        raise RuntimeError(str(asset_error.get("error") or "Add Outfit prefab could not be resolved."))
    assert asset_ref is not None
    asset = _canonical_add_outfit_asset(get_asset_info_sync({**project_params, "assetPath": asset_ref.get("assetPath"), "guid": asset_ref.get("guid")}))
    avatar_requested = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
    parent_requested = str(params.get("parent_path") or params.get("parentPath") or avatar_requested).strip()
    if not avatar_requested or not parent_requested:
        raise RuntimeError("avatarPath and a resolvable parentPath are required for Add Outfit.")
    parent = _canonical_add_outfit_gameobject(get_gameobject_sync({**project_params, "gameObjectPath": parent_requested}), "parent")
    avatar = parent if parent["gameObjectPath"] == avatar_requested else _canonical_add_outfit_gameobject(
        get_gameobject_sync({**project_params, "gameObjectPath": avatar_requested}), "avatar"
    )
    outfit_name = str(params.get("outfit_name") or params.get("outfitName") or params.get("name") or asset.get("name") or "Outfit").strip()
    if not outfit_name or "/" in outfit_name or "\\" in outfit_name:
        raise RuntimeError("Add Outfit name must be a non-empty single hierarchy segment.")
    outfit_path = f"{parent['gameObjectPath'].rstrip('/')}/{outfit_name}"
    if outfit_path in parent["children"]:
        raise RuntimeError("Approval-bound Add Outfit target already exists.")

    manage_wardrobe = _workflow_bool(params, ("manage_wardrobe", "manageWardrobe"), True)
    setup_outfit = _workflow_bool(params, ("setup_outfit", "setupOutfit"), True)
    unpack_prefab = _workflow_bool(params, ("unpack_prefab", "unpackPrefab"), False)
    parameter_name, parameter_explicit = _workflow_parameter_name(params)
    wardrobe_scan: dict[str, Any] | None = None
    selected_wardrobe: dict[str, Any] | None = None
    wardrobe_fingerprint = ""
    assigned_value: int | None = None
    if manage_wardrobe:
        wardrobe_scan = WARDROBE_ARTIFACT_READ.scan_wardrobe(
            {**project_params, "avatarPath": avatar["gameObjectPath"]}
        )
        selected_wardrobe, wardrobe_fingerprint, assigned_value = _selected_add_outfit_wardrobe(
            wardrobe_scan, parameter_name, parameter_explicit
        )
        parameter_name = str(selected_wardrobe.get("parameterName") or "")

    continuation_tools: list[str] = []
    if unpack_prefab:
        continuation_tools.append("vrc_unpack_prefab")
    if setup_outfit:
        continuation_tools.append("vrc_setup_outfit")
    if manage_wardrobe:
        continuation_tools.append("vrc_add_wardrobe_outfit")

    instantiate_arguments = {
        **project_params,
        "assetPath": asset["assetPath"],
        "guid": asset["guid"],
        "parentPath": parent["gameObjectPath"],
        "name": outfit_name,
        "worldPositionStays": _workflow_bool(params, ("world_position_stays", "worldPositionStays"), True),
        "expectedPrefabGuid": asset["guid"],
        "expectedAssetDependencyHash": asset["dependencyHash"],
        "expectedScenePath": parent["scenePath"],
        "expectedParentGlobalObjectId": parent["globalObjectId"],
        "expectedResultPath": outfit_path,
        "preview": False,
    }
    if continuation_nonce and continuation_tools:
        instantiate_arguments.update({
            "approvedObjectReceiptNonce": continuation_nonce,
            "approvedContinuationTools": continuation_tools,
        })
    calls: list[tuple[str, dict[str, Any]]] = [("vrc_instantiate_prefab", instantiate_arguments)]
    if unpack_prefab:
        mode = str(params.get("unpack_mode") or params.get("unpackMode") or "outermost").strip().lower()
        if mode not in {"outermost", "completely"}:
            raise RuntimeError("Add Outfit unpack mode must be outermost or completely.")
        unpack_arguments = {
            **project_params,
            "gameObjectPath": outfit_path,
            "expectedPrefabGuid": asset["guid"],
            "expectedAssetDependencyHash": asset["dependencyHash"],
            "expectedScenePath": parent["scenePath"],
            "mode": mode,
            "preview": False,
        }
        if continuation_nonce:
            unpack_arguments["approvedObjectReceiptNonce"] = continuation_nonce
        calls.append(("vrc_unpack_prefab", unpack_arguments))
    if setup_outfit:
        setup_arguments = {
            **project_params,
            "avatarPath": avatar["gameObjectPath"],
            "outfitPath": outfit_path,
            "confirmSetup": True,
            "saveScene": _workflow_bool(params, ("save_scene", "saveScene"), True),
        }
        if continuation_nonce:
            setup_arguments["approvedObjectReceiptNonce"] = continuation_nonce
        calls.append(("vrc_setup_outfit", setup_arguments))
    if manage_wardrobe:
        assert assigned_value is not None
        wardrobe_source: dict[str, Any] = {
            **project_params,
            "avatarPath": avatar["gameObjectPath"],
            "parameterName": parameter_name,
            "outfitName": outfit_name,
            "objectPaths": [outfit_path],
            "value": assigned_value,
            "offObjectPaths": _coerce_path_list(params, "off_object_paths", "offObjectPaths"),
            "addMenuToggle": _workflow_bool(params, ("add_menu_toggle", "addMenuToggle"), True),
            "setObjectsDefaultOff": _workflow_bool(params, ("set_objects_default_off", "setObjectsDefaultOff"), True),
            "subMenuOverflow": _workflow_bool(params, ("sub_menu_overflow", "subMenuOverflow"), True),
            "subMenuName": str(params.get("sub_menu_name") or params.get("subMenuName") or "Wardrobe").strip() or "Wardrobe",
        }
        clip_output_dir = str(params.get("clip_output_dir") or params.get("clipOutputDir") or "").strip()
        if clip_output_dir:
            wardrobe_source["clipOutputDir"] = clip_output_dir
        if params.get("write_defaults") is not None or params.get("writeDefaults") is not None:
            wardrobe_source["writeDefaults"] = _workflow_bool(params, ("write_defaults", "writeDefaults"), True)
        wardrobe_arguments = build_owned_add_wardrobe_outfit_request(
            wardrobe_source,
            False,
        )
        wardrobe_arguments.update({
            **project_params,
            "expectedAssignedValue": assigned_value,
            "expectedWardrobeFingerprint": wardrobe_fingerprint,
        })
        if continuation_nonce:
            wardrobe_arguments["approvedObjectReceiptNonce"] = continuation_nonce
        calls.append(("vrc_add_wardrobe_outfit", wardrobe_arguments))

    read_facts = {
        "asset": asset,
        "avatar": avatar,
        "parent": parent,
        "outfitPath": outfit_path,
        "wardrobeFingerprint": wardrobe_fingerprint,
        "selectedWardrobe": selected_wardrobe,
        "assignedValue": assigned_value,
    }
    evidence = {
        "schema": "vrcforge.prepared-add-outfit.v1",
        "projectIdentity": project_identity,
        "readFacts": read_facts,
        "readFactsSha256": shader_evidence_sha256(read_facts),
        "callsSha256": shader_evidence_sha256([{"tool": tool, "arguments": arguments} for tool, arguments in calls]),
        "manageWardrobe": manage_wardrobe,
        "setupOutfit": setup_outfit,
        "unpackPrefab": unpack_prefab,
        "parameterName": parameter_name if manage_wardrobe else "",
        "outfitName": outfit_name,
    }
    preview = {
        "ok": True,
        "preview": True,
        "plan": {
            "action": "add_outfit_workflow",
            "projectPath": project_identity["projectPath"],
            "avatarPath": avatar["gameObjectPath"],
            "parentPath": parent["gameObjectPath"],
            "outfitPath": outfit_path,
            "outfitName": outfit_name,
            "asset": asset,
            "manageWardrobe": manage_wardrobe,
            "parameterName": parameter_name if manage_wardrobe else None,
            "assignedValue": assigned_value,
            "wardrobeFingerprint": wardrobe_fingerprint or None,
            "steps": [{"tool": tool, "write": True} for tool, _arguments in calls],
        },
    }
    return {"calls": calls, "evidence": evidence, "preview": preview}


def preview_add_outfit_workflow_sync(params: dict[str, Any]) -> dict[str, Any]:
    try:
        return _prepare_add_outfit_state(params or {})["preview"]
    except (RuntimeError, UnityMcpError, ValueError) as exc:
        return {"ok": False, "preview": True, "error": str(exc)}


def prepare_add_outfit_request(arguments: dict[str, Any], preview: Any) -> tuple[dict[str, Any], Any]:
    if PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in arguments:
        raise RuntimeError("Caller may not provide the reserved prepared Unity execution key.")
    if ADD_OUTFIT_CONTINUATION_NONCE_KEY in arguments:
        raise RuntimeError("Caller may not provide the reserved Add Outfit continuation nonce.")
    prepared_arguments = copy.deepcopy(arguments)
    prepared_arguments[ADD_OUTFIT_CONTINUATION_NONCE_KEY] = secrets.token_hex(32)
    state = _prepare_add_outfit_state(prepared_arguments)
    return install_prepared_calls(prepared_arguments, state["calls"], state["evidence"]), state["preview"]


def _require_add_outfit_receipt(expected: dict[str, Any], actual: dict[str, Any], label: str) -> None:
    for key, value in expected.items():
        if actual.get(key) != value:
            raise RuntimeError(f"Add Outfit {label} receipt did not match approved {key}.")


def add_outfit_workflow_approved_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    writes_started = False
    try:
        evidence = prepared_evidence(arguments)
        if not isinstance(evidence, dict) or evidence.get("schema") != "vrcforge.prepared-add-outfit.v1":
            raise RuntimeError("Prepared Add Outfit evidence is invalid.")
        read_facts = evidence.get("readFacts")
        if not isinstance(read_facts, dict) or shader_evidence_sha256(read_facts) != evidence.get("readFactsSha256"):
            raise RuntimeError("Prepared Add Outfit read facts are invalid.")
        project_identity = evidence.get("projectIdentity")
        if not isinstance(project_identity, dict):
            raise RuntimeError("Prepared Add Outfit project identity is invalid.")
        _verify_prepared_import_project_identity(project_identity)
        live = _prepare_add_outfit_state({key: value for key, value in arguments.items() if key != PREPARED_UNITY_EXECUTION_ARGUMENT_KEY})
        live_calls = live["calls"]
        if shader_evidence_sha256(live["evidence"]["readFacts"]) != evidence.get("readFactsSha256"):
            raise RuntimeError("Add Outfit read facts drifted after approval.")
        if shader_evidence_sha256([{"tool": tool, "arguments": call_args} for tool, call_args in live_calls]) != evidence.get("callsSha256"):
            raise RuntimeError("Add Outfit Core calls drifted after approval.")
        settings = load_dashboard_settings(build_agent_connection_request(arguments))
        settings.unity_mcp_timeout_seconds = max(int(settings.unity_mcp_timeout_seconds or 30), 300)
        instantiate_global_id = ""
        wardrobe_receipt_fingerprint = ""
        for index, (expected_tool, expected_arguments) in enumerate(live_calls):
            tool_name, tool_arguments = prepared_call(arguments, index)
            if tool_name != expected_tool:
                raise RuntimeError("Prepared Add Outfit Core call order is invalid.")
            _require_prepared_import_evidence(expected_arguments, tool_arguments, "Add Outfit Core arguments")
            writes_started = True
            payload = ensure_dict_payload(extract_tool_result_payload(invoke_unity_mcp(settings, tool_name, tool_arguments)), f"prepared Add Outfit {tool_name}")
            if payload.get("ok") is not True:
                raise RuntimeError(payload.get("error") or f"{tool_name} failed.")
            if tool_name == "vrc_instantiate_prefab":
                _require_add_outfit_receipt({
                    "assetPath": expected_arguments["assetPath"],
                    "gameObjectPath": expected_arguments["expectedResultPath"],
                    "prefabGuid": expected_arguments["expectedPrefabGuid"],
                    "dependencyHash": expected_arguments["expectedAssetDependencyHash"],
                    "scenePath": expected_arguments["expectedScenePath"],
                    "parentGlobalObjectId": expected_arguments["expectedParentGlobalObjectId"],
                    "continuationRegistered": bool(expected_arguments.get("approvedContinuationTools")),
                    "continuationCount": len(expected_arguments.get("approvedContinuationTools") or []),
                }, payload, "instantiate")
                instantiate_global_id = str(payload.get("globalObjectId") or "").strip()
                if not instantiate_global_id:
                    raise RuntimeError("Add Outfit instantiate receipt omitted GlobalObjectId.")
            elif tool_name == "vrc_unpack_prefab":
                _require_add_outfit_receipt({
                    "gameObjectPath": expected_arguments["gameObjectPath"],
                    "unpacked": True,
                    "continuationConsumed": bool(expected_arguments.get("approvedObjectReceiptNonce")),
                }, payload, "unpack")
                instantiate_global_id = str(payload.get("globalObjectId") or "").strip()
                if not instantiate_global_id:
                    raise RuntimeError("Add Outfit unpack receipt omitted GlobalObjectId.")
            elif tool_name == "vrc_setup_outfit":
                _require_add_outfit_receipt({
                    "outfitGlobalObjectId": instantiate_global_id,
                    "continuationConsumed": False,
                }, payload, "setup start")
                payload = SETUP_OUTFIT_APPROVED_WRITE.wait_for_existing_job(
                    settings,
                    {},
                    payload,
                )
                if payload.get("ok") is not True or str(payload.get("status") or "").lower() in {"error", "timeout"}:
                    raise RuntimeError(payload.get("error") or "Setup Outfit did not complete successfully.")
                _require_add_outfit_receipt({
                    "outfitGlobalObjectId": instantiate_global_id,
                    "continuationConsumed": bool(expected_arguments.get("approvedObjectReceiptNonce")),
                    "committed": True,
                    "commitState": "complete",
                    "checkpointRecoveryRequired": False,
                }, payload, "setup completion")
            elif tool_name == "vrc_add_wardrobe_outfit":
                _require_add_outfit_receipt({
                    "parameterName": expected_arguments["parameterName"],
                    "outfitName": expected_arguments["outfitName"],
                    "assignedValue": expected_arguments["expectedAssignedValue"],
                    "continuationConsumed": bool(expected_arguments.get("approvedObjectReceiptNonce")),
                }, payload, "wardrobe")
                wardrobe_receipt_fingerprint = str(payload.get("wardrobeFingerprint") or "").strip().lower()
                if (
                    len(wardrobe_receipt_fingerprint) != 64
                    or any(character not in "0123456789abcdef" for character in wardrobe_receipt_fingerprint)
                    or wardrobe_receipt_fingerprint == str(expected_arguments.get("expectedWardrobeFingerprint") or "").lower()
                ):
                    raise RuntimeError("Add Outfit wardrobe receipt fingerprint is not a valid post-write readback.")
            steps.append({"tool": tool_name, "ok": True, "receipt": payload})

        final_object = _canonical_add_outfit_gameobject(
            get_gameobject_sync({"projectPath": project_identity["projectPath"], "gameObjectPath": read_facts["outfitPath"]}),
            "final object",
        )
        if final_object["gameObjectPath"] != read_facts["outfitPath"] or final_object["scenePath"] != read_facts["parent"]["scenePath"]:
            raise RuntimeError("Add Outfit final object readback drifted from approval.")
        if instantiate_global_id and final_object["globalObjectId"] != instantiate_global_id:
            raise RuntimeError("Add Outfit final object GlobalObjectId changed after execution.")
        if evidence.get("manageWardrobe") is True:
            scan = WARDROBE_ARTIFACT_READ.scan_wardrobe(
                {
                    "projectPath": project_identity["projectPath"],
                    "avatarPath": read_facts["avatar"]["gameObjectPath"],
                }
            )
            if str(scan.get("fingerprint") or "").strip().lower() != wardrobe_receipt_fingerprint:
                raise RuntimeError("Add Outfit final wardrobe fingerprint did not match the Core write receipt.")
            selected = next((item for item in (scan.get("wardrobes") or []) if isinstance(item, dict) and str(item.get("parameterName") or "") == evidence.get("parameterName")), None)
            expected_value = read_facts.get("assignedValue")
            if not isinstance(selected, dict) or not any(isinstance(item, dict) and int(item.get("value", -1)) == int(expected_value) for item in (selected.get("outfits") or [])):
                raise RuntimeError("Add Outfit wardrobe readback did not contain the approved value.")
        emit_log("info", "wardrobe", "Prepared Add Outfit workflow executed.", {"outfitPath": read_facts["outfitPath"], "parameterName": evidence.get("parameterName")})
        return {"ok": True, "preview": False, "committed": True, "outfitPath": read_facts["outfitPath"], "steps": steps, "finalObject": final_object}
    except (RuntimeError, UnityMcpError, ValueError) as exc:
        emit_log("error", "wardrobe", "Prepared Add Outfit workflow failed.", {"error": str(exc)})
        if writes_started:
            return {"ok": False, "committed": True, "commitState": "unknown", "checkpointRecoveryRequired": True, "steps": steps, "error": str(exc)}
        raise to_http_exception(exc) from exc


def register_agent_gateway_tools() -> None:
    AGENT_GATEWAY.register_tool(
        "vrcforge_agent_observe",
        "Observe VRCForge agent runtime state.",
        "read/debug",
        lambda params: AGENT_GATEWAY.runtime_observe(
            str(params.get("session_id") or params.get("sessionId") or ""),
            project_root=str(params.get("projectRoot") or params.get("project_root") or params.get("projectPath") or ""),
        ),
    )
    AGENT_GATEWAY.register_tool("vrcforge_agent_message", "Run one VRCForge agent runtime turn.", "plan/preview", lambda params: AGENT_GATEWAY.runtime_message(params, agent_name=str(params.get("agent_name") or params.get("agentName") or "external-agent")))
    AGENT_GATEWAY.register_tool(
        "vrcforge_agent_desktop_action",
        "Run an action only inside a user-started Computer Use turn. Supported params.operation values are list_apps, launch_app, list_windows, get_window, window_state/get_window_state, inspect_window, screenshot, focus_window/activate_window, move_pointer, click, drag, scroll, type_text, key_press/press_key, focus_element, invoke_element, set_value, secondary_action/perform_secondary_action, wait, and sequence. Start with list_apps, carry the returned window handle plus app/process identity, then use window_state for a bounded screenshot and/or UI Automation text. Input actions require and automatically activate a target window; click and value/secondary actions can use a fresh elementIndex. Never target terminals, authentication/security UI, password managers, ChatGPT/Codex, or Windows-key shortcuts. Before deleting, sending/submitting, uploading, installing, changing permissions/settings, or making financial/medical actions, call vrcforge_ask_user at action time unless the exact action was explicitly pre-approved and policy permits that. Execution stays visible, cancellable, and scoped to the explicit turn.",
        "supervised-write",
        AGENT_GATEWAY.request_turn_authorized_desktop_action_and_wait,
        write=True,
        requires_user_activation=True,
    )
    AGENT_GATEWAY.register_tool("vrcforge_progress_list", "List the current agent progress items for a session or project.", "read/debug", lambda params: AGENT_GATEWAY.list_agent_progress(limit=int(ensure_dict(params or {}).get("limit") or 50), session_id=str(ensure_dict(params or {}).get("sessionId") or ensure_dict(params or {}).get("session_id") or ""), project_root=str(ensure_dict(params or {}).get("projectRoot") or ensure_dict(params or {}).get("project_root") or ensure_dict(params or {}).get("projectPath") or "")))
    AGENT_GATEWAY.register_tool("vrcforge_progress_replace", "Replace the visible agent progress list, similar to a TodoWrite plan update.", "plan/preview", lambda params: AGENT_GATEWAY.replace_agent_progress(params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_progress_create", "Create one visible agent progress item.", "plan/preview", lambda params: AGENT_GATEWAY.create_agent_progress(params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_progress_update", "Update one visible agent progress item title, summary, order, or status.", "plan/preview", lambda params: AGENT_GATEWAY.update_agent_progress(str(ensure_dict(params or {}).get("progressId") or ensure_dict(params or {}).get("id") or ""), params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_progress_delete", "Delete one visible agent progress item.", "plan/preview", lambda params: AGENT_GATEWAY.delete_agent_progress(str(ensure_dict(params or {}).get("progressId") or ensure_dict(params or {}).get("id") or ""), params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_ask_user", "Ask the user a short question with selectable options while the agent task continues.", "plan/preview", lambda params: AGENT_QUESTIONS.create(params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_classify_shell", "Classify a shell command before execution.", "read/debug", AGENT_GATEWAY.shell.classify)
    AGENT_GATEWAY.register_tool("vrcforge_execute_shell", "Execute low-risk shell commands or request approval for high-risk commands.", "supervised-write", lambda params: AGENT_GATEWAY.shell.execute(params, agent_name=str(params.get("agent_name") or params.get("agentName") or "external-agent")), write=True)
    AGENT_GATEWAY.register_tool("vrcforge_execute_approved_shell", "Execute a previously approved shell command payload.", "supervised-write", AGENT_GATEWAY.shell.execute_approved, write=True)
    AGENT_GATEWAY.register_tool("vrcforge_skill_manifest", "List VRCForge Agent Gateway skills.", "read/debug", lambda params: AGENT_GATEWAY.build_manifest(normalize_exposure_layer(ensure_dict(params).get("exposureLayer"))))
    AGENT_GATEWAY.register_tool("vrcforge_skill_check", "Validate VRCForge Agent Gateway skill packages.", "read/debug", lambda params: AGENT_GATEWAY.check_skill_registry(exposure_layer=normalize_exposure_layer(ensure_dict(params).get("exposureLayer"))))
    AGENT_GATEWAY.register_tool("vrcforge_tool_registry", "List standardized VRCForge tool metadata for Desktop, MCP, and CLI surfaces.", "read/debug", lambda params: AGENT_GATEWAY.build_tool_registry(exposure_layer=normalize_exposure_layer(ensure_dict(params).get("exposureLayer"))))
    AGENT_GATEWAY.register_tool("vrcforge_external_agent_connectors", "Generate loopback MCP connector templates for external coding agents without exposing plaintext tokens.", "read/debug", connector_bundle_sync)
    AGENT_GATEWAY.register_tool("vrcforge_list_skill_packages", "List installed community .vsk skill packages.", "read/debug", list_skill_packages_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preflight_skill_package", "Inspect and verify a local .vsk skill package before import.", "plan/preview", preflight_skill_package_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_project_index", "Scan and update the local project index, returning only structural file deltas and scanner-family hints.", "read/debug", scan_project_index_sync)
    AGENT_GATEWAY.register_tool("vrcforge_inspect_outfit_package", "Inspect a UnityPackage, Booth ZIP/folder, or loose prefab/texture folder without reading paid asset binary contents.", "read/debug", WARDROBE_OUTFIT_WORKFLOWS.inspect_outfit_package)
    AGENT_GATEWAY.register_tool(
        "vrcforge_inspect_chat_attachment",
        "Inspect a vault-stored chat attachment by payloadHash: bounded archive listing with bomb/zip-slip guards, single-entry text extract via entryPath, or image header metadata. Read-only; materialization goes through the supervised import lane.",
        "read/debug",
        inspect_chat_attachment_sync,
    )
    AGENT_GATEWAY.register_tool("vrcforge_plan_outfit_import", "Build a supervised import plan for a UnityPackage, Booth folder, or loose prefab/texture folder without writing Unity project files.", "plan/preview", WARDROBE_OUTFIT_WORKFLOWS.plan_outfit_import)
    AGENT_GATEWAY.register_tool("vrcforge_health", "Read VRCForge backend and component health.", "read/debug", lambda _params: build_full_health_payload())
    AGENT_GATEWAY.register_tool(
        "vrcforge_know_yourself",
        "Read the current work-start preparation, Unity/MCP readiness, capability map, gaps, and safe operating boundaries without changing the Unity project.",
        "read/debug",
        know_yourself_sync,
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_unity_status",
        "Read Unity MCP bridge status.",
        "read/debug",
        lambda params: build_unity_status_snapshot(load_dashboard_settings(build_agent_connection_request(params))),
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_unity_tools",
        "List Unity MCP tools visible to VRCForge.",
        "read/debug",
        lambda params: build_unity_status_snapshot(
            load_dashboard_settings(build_agent_connection_request(params))
        ).get("tools", {}),
    )
    AGENT_GATEWAY.register_tool("vrcforge_list_avatars", "List avatars from the current Unity project.", "read/debug", lambda params: AVATAR_TUNING_WORKFLOWS.read_avatars(build_agent_dashboard_request(params)))
    AGENT_GATEWAY.register_tool("vrcforge_scan_blendshapes", "Scan face-related Blendshapes for an avatar.", "read/debug", lambda params: AVATAR_TUNING_WORKFLOWS.read_avatar_blendshapes(AvatarBlendshapeListRequest(**build_agent_dashboard_request(params).model_dump())))
    AGENT_GATEWAY.register_tool("vrcforge_scan_materials", "Scan shader/material inventory for an avatar.", "read/debug", lambda params: SHADER_VISION_PROTECTION.scan_shader_materials(ShaderMaterialScanRequest(**params)))
    AGENT_GATEWAY.register_tool("vrcforge_scan_modular_avatar", "Detect the Modular Avatar package and scan avatars for Modular Avatar components.", "read/debug", lambda params: scan_addon_framework_sync("modular_avatar", params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_inspect_modular_avatar_component", "Read the exact presence, count, type, scene dirty state, and AvatarObjectReference paths for one Modular Avatar component without writing.", "read/debug", inspect_modular_avatar_component_sync)
    AGENT_GATEWAY.register_tool("vrcforge_inspect_primitive_basis_fixture", "Read the fixed primitive-basis fixture identity and active-scene binding without writing.", "read/debug", inspect_primitive_basis_fixture_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_vrcfury", "Detect the VRCFury package and scan avatars for VRCFury components.", "read/debug", lambda params: scan_addon_framework_sync("vrcfury", params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_scan_avatar_items", "Scan avatar hierarchy items including wardrobe-related objects and component types.", "read/debug", WARDROBE_OUTFIT_WORKFLOWS.scan_avatar_items)
    AGENT_GATEWAY.register_tool("vrcforge_scan_fx_animator", "Scan FX animator layers, states, and parameters for an avatar.", "read/debug", scan_fx_animator_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_animation_bindings", "Scan animation clip bindings for an avatar or animator controller.", "read/debug", scan_animation_bindings_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_avatar_controls", "Scan expression menu controls and linked parameters for an avatar.", "read/debug", WARDROBE_OUTFIT_WORKFLOWS.scan_avatar_controls)
    AGENT_GATEWAY.register_tool("vrcforge_scan_wardrobe", "Detect int-exclusive wardrobe(s) by reconciling an expression Int parameter, menu toggle values, FX Any-State Equals transitions, per-clip object on/off toggles, and Write Defaults.", "read/debug", WARDROBE_OUTFIT_WORKFLOWS.scan_wardrobe)
    AGENT_GATEWAY.register_tool("vrcforge_scan_parameters", "Scan expression parameter usage for an avatar.", "read/debug", scan_avatar_parameters_gateway_sync)
    AGENT_GATEWAY.register_tool("vrcforge_run_validation_report", "Run the read-only vrcforge.validation.v1 report across compile, SDK, avatar, hierarchy, parameters, menu, FX, bindings, materials, performance, plugin, MCP, package, and residue checks.", "read/debug", build_validation_report_sync)
    AGENT_GATEWAY.register_tool("vrcforge_build_test_readiness", "Run the read-only Build & Test readiness gate without building, publishing, or repairing automatically.", "read/debug", build_test_readiness_sync)
    AGENT_GATEWAY.register_tool("vrcforge_optimization_plan", "Build the read-only vrcforge.optimization.v1 model optimization dashboard plan and recommended step order without modifying the Unity project.", "plan/preview", OPTIMIZATION_WORKFLOWS.build_plan)
    AGENT_GATEWAY.register_tool("vrcforge_optimization_validation_delta", "Compare before/after/rollback vrcforge.validation.v1 reports for one optimizer step without writing project files.", "read/debug", OPTIMIZATION_WORKFLOWS.build_validation_delta)
    for definition in OPTIMIZATION_TOOL_DEFINITIONS:
        gateway_tool = definition["gatewayName"]
        external_tool = definition["externalName"]
        AGENT_GATEWAY.register_tool(
            gateway_tool,
            definition["description"],
            definition["category"],
            lambda params, _tool=external_tool: OPTIMIZATION_WORKFLOWS.build_tool(
                _tool,
                params or {},
            ),
        )
    for definition in STABLE_OPTIMIZATION_APPLY_REQUEST_DEFINITIONS:
        gateway_tool = str(definition["gatewayName"])
        external_tool = str(definition["externalName"])
        AGENT_GATEWAY.register_tool(
            gateway_tool,
            str(definition["description"]),
            "supervised-write",
            lambda params, _tool=external_tool: OPTIMIZATION_WORKFLOWS.request_apply(
                {**ensure_dict(params or {}), "tool": _tool},
                agent_name=str(ensure_dict(params or {}).get("agent_name") or ensure_dict(params or {}).get("agentName") or "external-agent"),
            ),
            write=True,
        )
    AGENT_GATEWAY.register_tool(
        "vrcforge_avatar_encryption_research_report",
        "Build the read-only Avatar Encryption / Anti-Rip addon research packet.",
        "read/debug",
        lambda params: SHADER_VISION_PROTECTION.build_protection_research_report(AvatarEncryptionResearchRequest(**(params or {}))),
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_avatar_encryption_scan",
        "Scan shader material inventory for lilToon/Poiyomi avatar-encryption candidates and compatibility-only blockers.",
        "read/debug",
        lambda params: SHADER_VISION_PROTECTION.scan_protection_candidates(AvatarEncryptionScanRequest(**(params or {}))),
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_avatar_encryption_plan",
        "Build a read-only Avatar Encryption / Anti-Rip addon plan without writing Unity assets.",
        "plan/preview",
        lambda params: SHADER_VISION_PROTECTION.plan_protection(AvatarEncryptionPlanRequest(**(params or {}))),
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_avatar_encryption_preview",
        "Preview future avatar-encryption mesh/material write targets without writing Unity assets.",
        "plan/preview",
        lambda params: SHADER_VISION_PROTECTION.preview_protection(AvatarEncryptionPreviewRequest(**(params or {}))),
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_avatar_encryption_addon_status",
        "Read the private Avatar Encryption addon connector status.",
        "read/debug",
        lambda params: SHADER_VISION_PROTECTION.read_protection_addon_status(),
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_avatar_encryption_liltoon_apply_request",
        "Request supervised lilToon Avatar Encryption apply through approval, checkpoint, generated copies, and rollback.",
        "supervised-write",
        lambda params: SHADER_VISION_PROTECTION.request_protection_apply(
            ensure_dict(params or {}),
            "liltoon",
            agent_name=str(ensure_dict(params or {}).get("agent_name") or ensure_dict(params or {}).get("agentName") or "external-agent"),
        ),
        write=True,
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_avatar_encryption_poiyomi_apply_request",
        "Request supervised Poiyomi Avatar Encryption apply through approval, checkpoint, generated copies, and rollback.",
        "supervised-write",
        lambda params: SHADER_VISION_PROTECTION.request_protection_apply(
            ensure_dict(params or {}),
            "poiyomi",
            agent_name=str(ensure_dict(params or {}).get("agent_name") or ensure_dict(params or {}).get("agentName") or "external-agent"),
        ),
        write=True,
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_avatar_encryption_remove_request",
        "Request supervised Avatar Encryption remove/restore through approval, checkpoint, and generated asset cleanup.",
        "supervised-write",
        lambda params: SHADER_VISION_PROTECTION.request_protection_remove(
            ensure_dict(params or {}),
            agent_name=str(ensure_dict(params or {}).get("agent_name") or ensure_dict(params or {}).get("agentName") or "external-agent"),
        ),
        write=True,
    )
    AGENT_GATEWAY.register_tool("vrcforge_preview_ensure_expression_parameter", "Preview creating or updating an avatar expression parameter without writing.", "plan/preview", lambda params: ensure_expression_parameter_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_preview_ensure_expression_menu_control", "Preview creating or updating an expression menu control without writing.", "plan/preview", lambda params: ensure_expression_menu_control_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_preview_ensure_animator_state", "Preview creating or updating an FX animator layer/state/transition without writing.", "plan/preview", lambda params: ensure_animator_state_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_read_avatar_descriptor", "Read VRCAvatarDescriptor viewpoint, lip sync, visemes, expression assets, playable layers, and eye-look summary.", "read/debug", read_avatar_descriptor_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preview_write_avatar_descriptor", "Preview changing selected VRCAvatarDescriptor fields without writing.", "plan/preview", lambda params: write_avatar_descriptor_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_preview_write_animation_curve", "Preview creating, replacing, or deleting one AnimationClip curve binding without writing.", "plan/preview", lambda params: write_animation_curve_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_preview_manage_expression_parameters", "Preview deleting, renaming, reordering, or updating existing expression parameters without writing.", "plan/preview", lambda params: manage_expression_parameters_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_preview_manage_expression_menu", "Preview expression menu control create/update/delete/reorder without writing.", "plan/preview", lambda params: manage_expression_menu_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_preview_manage_fx_animator", "Preview FX AnimatorController layer/state/transition create/update/delete without writing.", "plan/preview", lambda params: manage_fx_animator_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_preview_restore_backup", "Preview which files a safe backup restore would overwrite, without writing.", "plan/preview", preview_safe_backup_restore_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_avatar_performance", "Calculate VRChat SDK performance statistics and rank for an avatar.", "read/debug", scan_avatar_performance_sync)
    AGENT_GATEWAY.register_tool("vrcforge_package_manager_status", "Detect vrc-get/ALCOM/vpm CLIs and addon package install state.", "read/debug", PACKAGE_INSTALL_WORKFLOWS.package_manager_status)
    AGENT_GATEWAY.register_tool("vrcforge_package_install_plan", "Plan a VPM package install using ALCOM/VCC UI handoff, VCC vpm CLI, vrc-get CLI, or agent-managed download fallback without writing.", "plan/preview", PACKAGE_INSTALL_WORKFLOWS.plan_install)
    AGENT_GATEWAY.register_tool("vrcforge_package_install_request", "Request supervised VPM package installation through the selected package manager; creates an approval request only.", "supervised-write", lambda params: PACKAGE_INSTALL_WORKFLOWS.request_install(params or {}, agent_name=str((params or {}).get("agent_name") or (params or {}).get("agentName") or "external-agent")), write=True)
    AGENT_GATEWAY.register_tool("vrcforge_diagnose_package_install_errors", "Read package-manager output and Unity compile errors to explain plugin/package install failures without repairing automatically.", "read/debug", PACKAGE_INSTALL_WORKFLOWS.diagnose_install)
    AGENT_GATEWAY.register_tool("vrcforge_preview_setup_outfit", "Check Modular Avatar Setup Outfit readiness for an outfit object, without writing.", "plan/preview", WARDROBE_OUTFIT_WORKFLOWS.preview_setup_outfit)
    AGENT_GATEWAY.register_tool("vrcforge_preview_add_wardrobe_outfit", "Preview adding one outfit to an existing int-exclusive wardrobe (assigned int value, FX state, on/off objects, menu placement), without writing.", "plan/preview", WARDROBE_OUTFIT_WORKFLOWS.preview_add_wardrobe_outfit)
    AGENT_GATEWAY.register_tool("vrcforge_preview_add_outfit_part", "Preview adding an int-gated part toggle (e.g. a hat) to one outfit value of an int-exclusive wardrobe: Bool parameter, dedicated FX layer (int Equals N AND bool gating), on/off clips, and menu toggle, without writing.", "plan/preview", WARDROBE_OUTFIT_WORKFLOWS.preview_add_outfit_part)
    AGENT_GATEWAY.register_tool("vrcforge_preview_add_modular_avatar_component", "Preview adding a common Modular Avatar component (MergeArmature, BoneProxy, MenuInstaller, MergeAnimator, Parameters) to a scene object, resolving references and fields, without writing.", "plan/preview", WARDROBE_OUTFIT_WORKFLOWS.preview_add_modular_avatar_component)
    AGENT_GATEWAY.register_tool("vrcforge_preview_manage_wardrobe", "Preview destructive or structural wardrobe management actions (remove/rename/reorder outfits, set default value, delete wardrobe) without writing.", "plan/preview", WARDROBE_OUTFIT_WORKFLOWS.preview_manage_wardrobe)
    AGENT_GATEWAY.register_tool("vrcforge_preview_create_wardrobe", "Preview creating an empty int-exclusive wardrobe skeleton (Int parameter, FX layer/default state, and menu), without writing.", "plan/preview", WARDROBE_OUTFIT_WORKFLOWS.preview_create_wardrobe)
    AGENT_GATEWAY.register_tool("vrcforge_preview_add_outfit", "Preview the full add-outfit workflow: resolve prefab, instantiate under avatar, run Setup Outfit, scan/create wardrobe if needed, and add the outfit to it.", "plan/preview", WARDROBE_OUTFIT_WORKFLOWS.preview_add_outfit)
    AGENT_GATEWAY.register_tool("vrcforge_list_checkpoints", "List pre-write git checkpoints created by VRCForge.", "read/debug", lambda params: AGENT_GATEWAY.list_checkpoints(params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_preview_restore_checkpoint", "Preview restoring Assets/Packages/ProjectSettings from a VRCForge checkpoint.", "plan/preview", lambda params: AGENT_GATEWAY.preview_restore_checkpoint(params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_list_interrupted_apply_recoveries", "List interrupted or unfinished approved writes that must be restored or resolved before new writes.", "read/debug", lambda params: AGENT_GATEWAY.list_interrupted_apply_recoveries(params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_preview_interrupted_apply_recovery", "Preview the checkpoint restore path for an interrupted approved write.", "plan/preview", lambda params: AGENT_GATEWAY.preview_interrupted_apply_recovery(params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_export_interrupted_apply_incident_bundle", "Export a local incident bundle for an interrupted approved write.", "read/debug", lambda params: AGENT_GATEWAY.export_interrupted_apply_incident_bundle(params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_capture_status", "Read current Play Mode / Gesture Manager capture status.", "read/debug", lambda params: SHADER_VISION_PROTECTION.read_vision_capture_status(VisionCaptureStatusRequest(**params)))
    AGENT_GATEWAY.register_tool("vrcforge_vision_audit", "Run advisory Vision audit on a captured screenshot.", "read/debug", lambda params: SHADER_VISION_PROTECTION.audit_avatar_screenshot(VisionAuditRequest(**params)))
    AGENT_GATEWAY.register_tool("vrcforge_scan_thry_avatar_performance", "Call VRC Avatar Performance Tools / Thry read-only VRAM and mesh memory calculator for an avatar.", "read/debug", scan_thry_avatar_performance_sync)
    AGENT_GATEWAY.register_tool("vrcforge_read_recent_logs", "Read recent VRCForge dashboard logs.", "read/debug", lambda params: {"ok": True, "logs": recent_log_snapshot()[-int(params.get("limit", 80)):], "agentLogs": AGENT_GATEWAY.recent_audit_logs(limit=int(params.get("limit", 80)))})
    AGENT_GATEWAY.register_tool("vrcforge_get_compile_errors", "Read C# compile errors from the last Unity compilation pass.", "read/debug", read_agent_compile_errors)
    AGENT_GATEWAY.register_tool("vrcforge_get_property", "Read a single field/property value from a component on a scene GameObject.", "read/debug", read_component_property_sync)
    AGENT_GATEWAY.register_tool("vrcforge_get_gameobject", "Describe a scene GameObject: path, active state, tag/layer, parent, children, and components.", "read/debug", get_gameobject_sync)
    AGENT_GATEWAY.register_tool("vrcforge_find_assets", "Search the project for assets by query/type/folder.", "read/debug", find_assets_sync)
    AGENT_GATEWAY.register_tool("vrcforge_get_asset_info", "Describe a project asset: path, GUID, type, importer, and prefab details.", "read/debug", get_asset_info_sync)
    AGENT_GATEWAY.register_tool("vrcforge_plan_face_tuning", "Generate a face tuning plan without applying it.", "plan/preview", lambda params: AVATAR_TUNING_WORKFLOWS.plan_face_tuning(build_agent_dashboard_request(params)))
    AGENT_GATEWAY.register_tool("vrcforge_plan_shader_tuning", "Generate a shader/material tuning plan without applying it.", "plan/preview", lambda params: SHADER_VISION_PROTECTION.generate_shader_material_plan(build_agent_shader_request(params)))
    AGENT_GATEWAY.register_tool("vrcforge_preview_blendshape_apply", "Preview blendshape apply payload without writing to Unity.", "plan/preview", AVATAR_TUNING_WORKFLOWS.preview_agent_blendshape_apply)
    AGENT_GATEWAY.register_tool("vrcforge_preview_shader_apply", "Preview shader/material apply payload without writing to Unity.", "plan/preview", SHADER_VISION_PROTECTION.preview_shader_apply)
    AGENT_GATEWAY.register_tool(
        "vrcforge_preview_material_shader_assignment",
        "Preview one persistent material shader assignment and its shared impact without writing project files.",
        "plan/preview",
        SHADER_VISION_PROTECTION.preview_material_shader_assignment,
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_preview_scene_object_duplicate",
        "Preview one create-new scene object duplicate without writing project files.",
        "plan/preview",
        lambda params: preview_scene_object_copy_sync(params or {}, DUPLICATE_SCENE_OBJECT_TOOL),
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_preview_scene_object_prefab",
        "Preview saving one scene object as a create-new generated prefab without writing project files.",
        "plan/preview",
        lambda params: preview_scene_object_copy_sync(params or {}, SAVE_SCENE_OBJECT_AS_PREFAB_TOOL),
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_preview_texture_import_settings",
        "Preview one bounded texture importer settings change without writing project files.",
        "plan/preview",
        preview_texture_import_settings_sync,
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_preview_constraint_sources",
        "Preview one exact ordered constraint-source replacement without writing project files.",
        "plan/preview",
        preview_constraint_sources_sync,
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_preview_component_feature",
        "Preview one fixed-schema component feature creation without writing project files.",
        "plan/preview",
        preview_component_feature_sync,
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_preview_parameter_bit_packing",
        "Preview one source-preserving parameter bit-packing build on a temporary avatar clone without writing project files.",
        "plan/preview",
        preview_parameter_bit_packing_sync,
    )
    AGENT_GATEWAY.register_tool(
        "vrcforge_preview_atomic_reference_rename",
        "Preview one complete object or parameter reference migration without writing project files.",
        "plan/preview",
        preview_atomic_reference_rename_sync,
    )
    AGENT_GATEWAY.register_write_handler("vrcforge_import_skill_package", "Import a verified .vsk skill package into the user skill store.", "medium", import_skill_package_sync)
    AGENT_GATEWAY.register_write_handler("vrcforge_export_skill_package", "Export a user skill as a shareable .vsk package.", "medium", export_skill_package_sync)
    AGENT_GATEWAY.register_write_handler("vrcforge_set_skill_package_enabled", "Enable or disable an installed .vsk skill package and its projected user skill.", "medium", set_skill_package_enabled_sync)
    AGENT_GATEWAY.register_write_handler("vrcforge_uninstall_skill_package", "Uninstall an installed .vsk skill package and optionally remove its projected user skill.", "medium", uninstall_skill_package_sync)
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_repair_project_chat_store",
        "Repair a digest-bound project chat transcript store after explicit approval.",
        "medium",
        repair_project_chat_store_sync,
    )
    AGENT_GATEWAY.register_tool("vrcforge_request_apply", "Request user approval for a write operation.", "supervised-write", AGENT_GATEWAY.create_apply_request, write=True)
    AGENT_GATEWAY.register_tool("vrcforge_apply_approved", "Apply a previously approved write operation.", "supervised-write", AGENT_GATEWAY.apply_approved, write=True)
    AGENT_GATEWAY.register_tool("vrcforge_restore_last_backup", "Request approval to restore the last face or shader backup.", "supervised-write", request_agent_restore_last_backup, write=True)
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_create_safe_backup",
        "Create a project-owned safe backup snapshot through VRCForge approval and checkpoint controls.",
        "medium",
        create_safe_backup_sync,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_safe_backup_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_capture_screenshot",
        "Capture one fixed dashboard scene-view artifact through VRCForge approval and checkpoint controls.",
        "medium",
        capture_avatar_screenshot_approved_sync,
        request_preparer=prepare_capture_screenshot_request,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_capture_multi_screenshot",
        "Capture up to four fixed-angle dashboard scene-view artifacts through VRCForge approval and checkpoint controls.",
        "medium",
        capture_avatar_multi_screenshot_approved_sync,
        request_preparer=prepare_capture_multi_screenshot_request,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_apply_blendshapes",
        "Apply validated Blendshape adjustments through VRCForge.",
        "medium",
        AVATAR_TUNING_APPROVED_WRITES.execute_manual_apply,
        request_preparer=AVATAR_TUNING_APPROVED_WRITES.prepare_manual_apply,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_run_face_tuning",
        "Run and apply a generated face tuning plan through VRCForge.",
        "high",
        AVATAR_TUNING_APPROVED_WRITES.execute_face_tuning,
        request_preparer=AVATAR_TUNING_APPROVED_WRITES.prepare_face_tuning,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_apply_shader_tuning",
        "Apply validated shader/material tuning changes through VRCForge.",
        "high",
        apply_shader_material_plan_approved_sync,
        request_preparer=prepare_shader_material_apply_request,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_restore_shader_tuning",
        "Restore the last shader/material tuning undo point.",
        "medium",
        restore_shader_material_plan_approved_sync,
        request_preparer=prepare_shader_material_restore_request,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_reapply_tuning_history",
        "Reapply one saved face-tuning history record through VRCForge.",
        "high",
        AVATAR_TUNING_APPROVED_WRITES.execute_reapply_history,
        request_preparer=AVATAR_TUNING_APPROVED_WRITES.prepare_reapply_history,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_apply_tuning_preset",
        "Apply one saved face-tuning preset through VRCForge.",
        "high",
        AVATAR_TUNING_APPROVED_WRITES.execute_apply_preset,
        request_preparer=AVATAR_TUNING_APPROVED_WRITES.prepare_apply_preset,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_reapply_shader_tuning_history",
        "Reapply one saved shader-tuning history record through VRCForge.",
        "high",
        reapply_shader_tuning_history_approved_sync,
        request_preparer=prepare_reapply_shader_tuning_history_request,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_apply_shader_tuning_preset",
        "Apply one saved shader-tuning preset through VRCForge.",
        "high",
        apply_shader_tuning_preset_approved_sync,
        request_preparer=prepare_apply_shader_tuning_preset_request,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        AVATAR_ENCRYPTION_ADDON_APPLY_TOOL,
        "Hand off approved Avatar Encryption apply requests to a configured private addon connector.",
        "high",
        apply_avatar_encryption_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        AVATAR_ENCRYPTION_ADDON_REMOVE_TOOL,
        "Hand off approved Avatar Encryption remove requests to a configured private addon connector.",
        "high",
        remove_avatar_encryption_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_undo_blendshapes",
        "Undo the last Blendshape apply snapshot for an avatar.",
        "medium",
        AVATAR_TUNING_APPROVED_WRITES.execute_manual_undo,
        request_preparer=AVATAR_TUNING_APPROVED_WRITES.prepare_manual_undo,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_apply_clothing_fx",
        "Apply generated clothing FX assets through VRCForge.",
        "high",
        WARDROBE_OUTFIT_APPROVED_WRITES.apply_clothing_fx,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_apply_parameter_optimization",
        "Apply avatar parameter optimization through VRCForge.",
        "high",
        lambda params: apply_parameter_optimization_sync(ParameterApplyOptimizationRequest(**params)),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_rollback_parameters",
        "Rollback avatar parameter optimization through VRCForge.",
        "medium",
        rollback_parameter_optimization_sync,
        request_preparer=prepare_rollback_parameter_optimization_request,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_setup_outfit",
        "Run Modular Avatar Setup Outfit on an outfit object through VRCForge.",
        "high",
        WARDROBE_OUTFIT_APPROVED_WRITES.setup_outfit,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_add_wardrobe_outfit",
        "Add one outfit to an existing int-exclusive wardrobe (assign next int value, set new objects scene-default off, author an on/off clip, add an FX Any-State Equals state, and a menu toggle) through VRCForge.",
        "high",
        WARDROBE_OUTFIT_APPROVED_WRITES.add_wardrobe_outfit,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_manage_wardrobe",
        "Manage an existing int-exclusive wardrobe: remove/rename/reorder outfits, set default value, or delete wardrobe bindings through VRCForge.",
        "high",
        WARDROBE_OUTFIT_APPROVED_WRITES.manage_wardrobe,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_add_outfit_part",
        "Add an int-gated part toggle (e.g. a hat) to one outfit value of an existing int-exclusive wardrobe: create a Bool parameter, author a dedicated FX layer gated on (int Equals N AND bool), set the part scene-default off, and add a menu toggle through VRCForge.",
        "high",
        WARDROBE_OUTFIT_APPROVED_WRITES.add_outfit_part,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_add_modular_avatar_component",
        "Add a common Modular Avatar component (MergeArmature, BoneProxy, MenuInstaller, MergeAnimator, Parameters) to a scene object, resolving AvatarObjectReference/asset references and scalar fields, through VRCForge.",
        "high",
        WARDROBE_OUTFIT_APPROVED_WRITES.add_modular_avatar_component,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_create_wardrobe",
        "Create an empty int-exclusive wardrobe skeleton (expression Int parameter, FX layer/default state, and wardrobe menu) through VRCForge.",
        "high",
        WARDROBE_OUTFIT_APPROVED_WRITES.create_wardrobe,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_ensure_expression_parameter",
        "Create or update an avatar expression parameter through VRCForge.",
        "medium",
        lambda params: ensure_expression_parameter_sync(params, preview=False),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_ensure_expression_menu_control",
        "Create or update an avatar expression menu control through VRCForge.",
        "medium",
        lambda params: ensure_expression_menu_control_sync(params, preview=False),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_ensure_animator_state",
        "Create or update an FX animator layer/state/transition through VRCForge.",
        "high",
        lambda params: ensure_animator_state_sync(params, preview=False),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_write_avatar_descriptor",
        "Update selected VRCAvatarDescriptor fields through VRCForge.",
        "high",
        lambda params: write_avatar_descriptor_sync(params, preview=False),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_write_animation_curve",
        "Create, replace, or delete one AnimationClip curve binding through VRCForge.",
        "high",
        lambda params: write_animation_curve_sync(params, preview=False),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_manage_expression_parameters",
        "Delete, rename, reorder, or update existing expression parameters through VRCForge.",
        "high",
        lambda params: manage_expression_parameters_sync(params, preview=False),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_manage_expression_menu",
        "Create, update, delete, or reorder expression menu controls through VRCForge.",
        "high",
        lambda params: manage_expression_menu_sync(params, preview=False),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_manage_fx_animator",
        "Create, update, or delete FX AnimatorController layers, states, and Any-State transitions through VRCForge.",
        "high",
        lambda params: manage_fx_animator_sync(params, preview=False),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_add_outfit",
        "Run the approval-bound add-outfit workflow against one existing verified wardrobe: instantiate an exact prefab, optionally unpack it, run Modular Avatar Setup Outfit, and bind the exact approved wardrobe value.",
        "high",
        WARDROBE_OUTFIT_APPROVED_WRITES.add_outfit,
        request_preparer=WARDROBE_OUTFIT_APPROVED_WRITES.prepare_add_outfit,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_import_outfit_package",
        "Import a direct UnityPackage or copy loose outfit prefab/material/texture assets into the Unity project through VRCForge.",
        "high",
        WARDROBE_OUTFIT_APPROVED_WRITES.import_package,
        request_preparer=WARDROBE_OUTFIT_APPROVED_WRITES.prepare_import_package,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_import_chat_image",
        "Copy a vault-stored chat image attachment into the Unity project's Assets/VRCForge/Imports folder through VRCForge.",
        "high",
        import_chat_image_sync,
        request_preparer=prepare_import_chat_image_request,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_import_chat_archive",
        "Re-verify and import a vault-stored chat archive through the supervised outfit lane or conservative managed ZIP extraction.",
        "high",
        import_chat_archive_approved_sync,
        request_preparer=prepare_import_chat_archive_request,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_add_component",
        "Add a component of a given type to a scene GameObject through VRCForge.",
        "medium",
        add_component_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_remove_component",
        "Remove a component of a given type from a scene GameObject through VRCForge.",
        "high",
        remove_component_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_set_property",
        "Set a single field/property on a component of a scene GameObject through VRCForge.",
        "medium",
        set_component_property_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_create_gameobject",
        "Create a new empty GameObject in the scene through VRCForge.",
        "medium",
        create_gameobject_sync,
        approval_category="scene-object-create",
        allow_future_category=True,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_rename_gameobject",
        "Rename a scene GameObject through VRCForge.",
        "low",
        rename_gameobject_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_reparent_gameobject",
        "Move a scene GameObject under a new parent (or to the scene root) through VRCForge.",
        "medium",
        reparent_gameobject_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_delete_gameobject",
        "Delete a scene GameObject and its children through VRCForge.",
        "high",
        delete_gameobject_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_set_gameobject_active",
        "Set a scene GameObject's active-self state through VRCForge.",
        "low",
        set_gameobject_active_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_instantiate_prefab",
        "Instantiate a prefab asset into the active scene (optionally under a parent) through VRCForge.",
        "medium",
        instantiate_prefab_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_unpack_prefab",
        "Unpack a prefab instance in the scene so its contents become plain GameObjects through VRCForge.",
        "high",
        unpack_prefab_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_install_vpm_package",
        "Install a VPM package through the VRCForge package manager strategy: ALCOM/VCC UI handoff for humans, VCC vpm or vrc-get CLI for supervised non-interactive installs.",
        "medium",
        PACKAGE_INSTALL_APPROVED_WRITE.execute,
        request_preparer=PACKAGE_INSTALL_APPROVED_WRITE.prepare,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_configure_optimizer_component",
        "Configure one delegated optimizer component on an avatar after approval; no external agent direct apply is exposed.",
        "high",
        configure_optimizer_component_sync,
        request_preparer=prepare_configure_optimizer_component_request,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_prepared_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_restore_safe_backup",
        "Restore files from a safe backup snapshot through VRCForge.",
        "high",
        restore_safe_backup_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_restore_checkpoint",
        "Restore Unity project files from a pre-write VRCForge checkpoint.",
        "high",
        lambda params: AGENT_GATEWAY.restore_checkpoint(params or {}),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_resolve_interrupted_apply_recovery",
        "Mark an interrupted approved write as manually resolved after explicit confirmation.",
        "medium",
        lambda params: AGENT_GATEWAY.resolve_interrupted_apply_recovery(params or {}),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_unity_mcp_write",
        "Run an allowlisted VRCForge static Unity MCP write tool through the approval and rollback checkpoint boundary.",
        "high",
        unity_mcp_write_sync,
        request_preparer=prepare_unity_mcp_write_request,
        manual_approval_resolver=unity_mcp_manual_approval_reason,
        checkpoint_prepare_handler=prepare_authoritative_unity_checkpoint_sync,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_unity_mcp_write_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_export_vrm",
        "Export one selected humanoid scene avatar as validated VRM 1.0 through an installed compatible UniVRM package. Requires author metadata, confirmRights=true, approval, and a pre-write checkpoint.",
        "medium",
        export_vrm_sync,
        risk_level_resolver=lambda params: "high" if normalize_bool(params.get("overwrite")) else "medium",
        requires_approved_execution_context=True,
        approved_execution_plan_builder=build_export_vrm_execution_plan,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_toggle_scene_object",
        "Toggle a scene object's active state (for example wardrobe items) through VRCForge.",
        "medium",
        toggle_scene_object_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_shell_execute",
        "Execute an approved high-risk shell command.",
        "high",
        AGENT_GATEWAY.shell.execute_payload,
    )
    missing_core_targets = VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS.difference(
        AGENT_GATEWAY._write_handlers
    )
    if missing_core_targets:
        raise RuntimeError(
            "VRCForge Unity write registry is incomplete: "
            + ", ".join(sorted(missing_core_targets))
        )
    for target_name in VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS:
        handler = AGENT_GATEWAY._write_handlers[target_name]
        handler.requires_approved_execution_context = True
        handler.checkpoint_prepare_handler = prepare_authoritative_unity_checkpoint_sync
    for target_name in SCENE_EXECUTION_PLAN_TARGETS:
        handler = AGENT_GATEWAY._write_handlers[target_name]
        handler.approved_execution_plan_builder = (
            lambda arguments, exact_target=target_name: build_scene_execution_plan(
                exact_target,
                arguments,
            )
        )
    for target_name in TUNING_EXECUTION_PLAN_TARGETS:
        handler = AGENT_GATEWAY._write_handlers[target_name]
        handler.request_preparer = prepare_avatar_scoped_tuning_write_request
        handler.approved_execution_plan_builder = (
            lambda arguments, exact_target=target_name: build_tuning_execution_plan(
                exact_target,
                arguments,
            )
        )
    for target_name in WORKFLOW_EXECUTION_PLAN_TARGETS:
        handler = AGENT_GATEWAY._write_handlers[target_name]
        handler.approved_execution_plan_builder = (
            lambda arguments, exact_target=target_name: build_workflow_execution_plan(
                exact_target,
                arguments,
            )
        )


PROVIDER_CONFIGURATION.current_api_config()


if DASHBOARD_STATE is None:
    DASHBOARD_STATE = load_initial_dashboard_state()


WARDROBE_ARTIFACT_READ = WardrobeArtifactReadService(
    WardrobeArtifactReadPorts(
        scan_avatar_items=lambda params: run_unity_artifact_scan_sync(
            params,
            "vrc_scan_avatar_items",
            "avatar_items",
            {
                "maxItems": int(
                    params.get("max_items") or params.get("maxItems") or 2000
                ),
                "refreshAssets": False,
            },
            "avatar item scan",
        ),
        scan_avatar_controls=lambda params: scan_avatar_controls_direct(
            load_dashboard_settings(build_agent_connection_request(params)),
            str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        ),
        scan_wardrobe=lambda params: run_unity_artifact_scan_sync(
            params,
            "vrc_scan_wardrobe",
            "wardrobe",
            {},
            "wardrobe scan",
        ),
    )
)
SETUP_OUTFIT_PREVIEW = SetupOutfitPreviewService(
    SetupOutfitPreviewPorts(
        load_settings=lambda params: load_dashboard_settings(
            build_agent_connection_request(params)
        ),
        invoke_preview=lambda settings, request: ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(
                    settings,
                    "vrc_setup_outfit",
                    request,
                    execution_context={"lane": "app_preview"},
                )
            ),
            "setup outfit preview",
        ),
    )
)
SETUP_OUTFIT_APPROVED_WRITE = SetupOutfitApprovedWriteService(
    SetupOutfitApprovedWritePorts(
        load_settings=lambda params: load_dashboard_settings(
            build_agent_connection_request(params)
        ),
        start_approved=lambda settings, request: ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(settings, "vrc_setup_outfit", request)
            ),
            "setup outfit",
        ),
        poll_existing_job=lambda settings, job_id: ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(
                    settings,
                    "vrc_setup_outfit",
                    {"jobId": job_id},
                    execution_context={"lane": "app_setup_outfit_poll"},
                )
            ),
            "setup outfit job",
        ),
        retryable_poll_error=UnityMcpError,
        monotonic=time.monotonic,
        sleep=time.sleep,
        log=emit_log,
    )
)
ADD_WARDROBE_OUTFIT_PREVIEW = AddWardrobeOutfitPreviewService(
    AddWardrobeOutfitPreviewPorts(
        build_request=build_owned_add_wardrobe_outfit_request,
        load_settings=lambda params: load_dashboard_settings(
            build_agent_connection_request(params)
        ),
        invoke_preview=lambda settings, request: ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(
                    settings,
                    "vrc_add_wardrobe_outfit",
                    request,
                    execution_context={"lane": "app_preview"},
                )
            ),
            "add wardrobe outfit preview",
        ),
    )
)
ADD_WARDROBE_OUTFIT_APPROVED_WRITE = AddWardrobeOutfitApprovedWriteService(
    AddWardrobeOutfitApprovedWritePorts(
        build_request=build_owned_add_wardrobe_outfit_request,
        load_settings=lambda params: load_dashboard_settings(
            build_agent_connection_request(params)
        ),
        invoke_approved=lambda settings, request: ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(settings, "vrc_add_wardrobe_outfit", request)
            ),
            "add wardrobe outfit",
        ),
        log=emit_log,
    )
)
ADD_OUTFIT_PART_PREVIEW = AddOutfitPartPreviewService(
    AddOutfitPartPreviewPorts(
        build_request=build_owned_add_outfit_part_request,
        load_settings=lambda params: load_dashboard_settings(
            build_agent_connection_request(params)
        ),
        invoke_preview=lambda settings, request: ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(
                    settings,
                    "vrc_add_outfit_part",
                    request,
                    execution_context={"lane": "app_preview"},
                )
            ),
            "add outfit part preview",
        ),
    )
)
ADD_OUTFIT_PART_APPROVED_WRITE = AddOutfitPartApprovedWriteService(
    AddOutfitPartApprovedWritePorts(
        build_request=build_owned_add_outfit_part_request,
        load_settings=lambda params: load_dashboard_settings(
            build_agent_connection_request(params)
        ),
        invoke_approved=lambda settings, request: ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(settings, "vrc_add_outfit_part", request)
            ),
            "add outfit part",
        ),
        log=emit_log,
    )
)
ADD_MODULAR_AVATAR_COMPONENT_PREVIEW = AddModularAvatarComponentPreviewService(
    AddModularAvatarComponentPreviewPorts(
        build_request=build_owned_add_modular_avatar_component_request,
        load_settings=lambda params: load_dashboard_settings(
            build_agent_connection_request(params)
        ),
        invoke_preview=lambda settings, request: ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(
                    settings,
                    "vrc_add_modular_avatar_component",
                    request,
                    execution_context={"lane": "app_preview"},
                )
            ),
            "add modular avatar component preview",
        ),
    )
)
ADD_MODULAR_AVATAR_COMPONENT_APPROVED_WRITE = (
    AddModularAvatarComponentApprovedWriteService(
        AddModularAvatarComponentApprovedWritePorts(
            primitive_live_connection=lambda: PRIMITIVE_BASIS_LIVE_CONNECTION,
            primitive_live_guard_fields=lambda params: _primitive_live_guard_fields(
                params
            ),
            build_request=build_owned_add_modular_avatar_component_request,
            load_settings=lambda params: load_dashboard_settings(
                build_agent_connection_request(params)
            ),
            invoke_approved=lambda settings, request: ensure_dict_payload(
                extract_tool_result_payload(
                    invoke_unity_mcp(
                        settings,
                        "vrc_add_modular_avatar_component",
                        request,
                    )
                ),
                "add modular avatar component",
            ),
            log=emit_log,
        )
    )
)
MANAGE_WARDROBE_PREVIEW = ManageWardrobePreviewService(
    ManageWardrobePreviewPorts(
        build_request=build_owned_manage_wardrobe_request,
        load_settings=lambda params: load_dashboard_settings(
            build_agent_connection_request(params)
        ),
        invoke_preview=lambda settings, request: ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(
                    settings,
                    "vrc_manage_wardrobe",
                    request,
                    execution_context={"lane": "app_preview"},
                )
            ),
            "manage wardrobe preview",
        ),
    )
)
MANAGE_WARDROBE_APPROVED_WRITE = ManageWardrobeApprovedWriteService(
    ManageWardrobeApprovedWritePorts(
        build_request=build_owned_manage_wardrobe_request,
        load_settings=lambda params: load_dashboard_settings(
            build_agent_connection_request(params)
        ),
        invoke_approved=lambda settings, request: ensure_dict_payload(
            extract_tool_result_payload(
                invoke_unity_mcp(settings, "vrc_manage_wardrobe", request)
            ),
            "manage wardrobe",
        ),
        log=emit_log,
    )
)
CLOTHING_FX_READ = ClothingFxReadService(
    ClothingFxReadPorts(
        load_settings=lambda request: load_dashboard_settings(request),
        current_avatar_path=lambda: DASHBOARD_RUNTIME.current_avatar_path,
        scan_controls=scan_avatar_controls_direct,
        build_blueprint=build_clothing_fx_blueprint_from_controls,
        build_apply_preview=build_clothes_fx_apply_preview,
        ensure_list=ensure_list_payload,
        log=emit_log,
    )
)
WARDROBE_OUTFIT_WORKFLOWS = WardrobeOutfitWorkflowService(
    WardrobeOutfitWorkflowPorts(
        selected_project_path=lambda: (
            DASHBOARD_STATE.selected_project_path if DASHBOARD_STATE else ""
        ),
        inspect_package=inspect_outfit_package,
        build_import_plan=build_outfit_import_plan,
        create_apply_request=AGENT_GATEWAY.create_apply_request,
        request_supervised_write=request_supervised_unity_write,
        scan_avatar_items=WARDROBE_ARTIFACT_READ.scan_avatar_items,
        scan_avatar_controls=WARDROBE_ARTIFACT_READ.scan_avatar_controls,
        scan_wardrobe=WARDROBE_ARTIFACT_READ.scan_wardrobe,
        scan_clothes=CLOTHING_FX_READ.scan_clothes,
        generate_clothing_fx=CLOTHING_FX_READ.generate_clothing_fx,
        preview_apply_clothing_fx=CLOTHING_FX_READ.preview_apply_clothing_fx,
        preview_setup_outfit=SETUP_OUTFIT_PREVIEW.preview,
        preview_add_wardrobe_outfit=ADD_WARDROBE_OUTFIT_PREVIEW.preview,
        preview_add_outfit_part=ADD_OUTFIT_PART_PREVIEW.preview,
        preview_add_modular_avatar_component=ADD_MODULAR_AVATAR_COMPONENT_PREVIEW.preview,
        preview_manage_wardrobe=MANAGE_WARDROBE_PREVIEW.preview,
        preview_create_wardrobe=preview_create_wardrobe_sync,
        preview_add_outfit=preview_add_outfit_workflow_sync,
    )
)
WARDROBE_OUTFIT_APPROVED_WRITES = WardrobeOutfitApprovedWriteHandlers(
    apply_clothing_fx=apply_clothing_fx_approved_sync,
    setup_outfit=SETUP_OUTFIT_APPROVED_WRITE.execute,
    add_wardrobe_outfit=ADD_WARDROBE_OUTFIT_APPROVED_WRITE.execute,
    add_outfit_part=ADD_OUTFIT_PART_APPROVED_WRITE.execute,
    add_modular_avatar_component=ADD_MODULAR_AVATAR_COMPONENT_APPROVED_WRITE.execute,
    manage_wardrobe=MANAGE_WARDROBE_APPROVED_WRITE.execute,
    create_wardrobe=create_wardrobe_sync,
    prepare_add_outfit=prepare_add_outfit_request,
    add_outfit=add_outfit_workflow_approved_sync,
    prepare_import_package=prepare_outfit_import_package_request,
    import_package=import_outfit_package_approved_sync,
)

PACKAGE_MANAGER_DISCOVERY = PackageManagerDiscoveryService(
    PackageManagerDiscoveryPorts(
        get_environment_value=lambda name: os.environ.get(name, ""),
        find_executable=shutil.which,
        is_file=lambda path: path.is_file(),
    )
)
PACKAGE_DETECTION = PackageDetectionService(
    PackageDetectionPorts(
        path_exists=lambda path: path.exists(),
        read_utf8_sig_text=lambda path: path.read_text(encoding="utf-8-sig"),
    )
)
PACKAGE_INSTALL_WORKFLOWS = PackageInstallWorkflowService(
    PackageInstallWorkflowPorts(
        selected_project_path=lambda: (
            DASHBOARD_STATE.selected_project_path if DASHBOARD_STATE else ""
        ),
        locate_managers=PACKAGE_MANAGER_DISCOVERY.locate,
        detect_package=PACKAGE_DETECTION.detect,
        addon_frameworks=ADDON_FRAMEWORKS,
        optimizer_dependencies=OPTIMIZER_DEPENDENCIES,
        summarize_debug=summarize_debug_payload,
        read_compile_errors=read_agent_compile_errors,
        redact_support=redact_support_payload,
        create_apply_request=AGENT_GATEWAY.create_apply_request,
    )
)
VPM_PACKAGE_INSTALL_PREPARER = VpmPackageInstallPreparer(
    VpmPackageInstallPreparationPorts(
        resolve_project_path=lambda params: resolve_project_path(
            params,
            DASHBOARD_STATE.selected_project_path if DASHBOARD_STATE else "",
        ),
        locate_managers=PACKAGE_MANAGER_DISCOVERY.locate,
        select_strategy=PACKAGE_INSTALL_WORKFLOWS.select_strategy,
        detect_package=PACKAGE_DETECTION.detect,
        process_environment=lambda: os.environ,
        run_probe_process=run_bounded_process,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
)
VPM_PACKAGE_INSTALL_EXECUTOR = VpmPackageInstallExecutor(
    VpmPackageInstallExecutionPorts(
        detect_package=PACKAGE_DETECTION.detect,
        process_environment=lambda: os.environ,
        run_install_process=run_bounded_process,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
)
PACKAGE_INSTALL_APPROVED_WRITE = PackageInstallApprovedWriteHandler(
    prepare=VPM_PACKAGE_INSTALL_PREPARER.prepare,
    execute=VPM_PACKAGE_INSTALL_EXECUTOR.execute,
)

AVATAR_TUNING_STORES = AvatarTuningStoreService(
    AvatarTuningStorePorts(
        paths=lambda: AvatarTuningStorePaths(
            history=TUNING_HISTORY_PATH,
            presets=TUNING_PRESETS_PATH,
            locks=TUNING_LOCKS_PATH,
        ),
        lock=TUNING_STORE_LOCK,
        current_avatar_path=lambda: (
            DASHBOARD_RUNTIME.current_avatar_path if DASHBOARD_RUNTIME else ""
        ),
        now_utc=lambda: datetime.now(timezone.utc),
        emit_log=emit_log,
    )
)
AVATAR_TUNING_UNDO = AvatarTuningUndoStore(
    DASHBOARD_RUNTIME.manual_undo_stack,
    BLENDSHAPE_UNDO_LOCK,
)
AVATAR_TUNING_PREPARED = AvatarTuningPreparedService(
    stores=AVATAR_TUNING_STORES,
    undo=AVATAR_TUNING_UNDO,
    ports=AvatarTuningPreparedPorts(
        parse_manual_arguments=lambda arguments: ManualBlendshapeApplyRequest(
            **arguments
        ).model_dump(),
        parse_mock_execute=lambda arguments: build_agent_dashboard_request(
            arguments
        ).mock_execute,
        make_prepare_error=lambda detail, status_code: AgentGatewayError(
            detail,
            status_code=status_code,
        ),
        resolve_write_settings=lambda arguments: load_dashboard_settings(
            UndoBlendshapeRequest(**arguments)
        ),
        resolve_live_context=_resolve_avatar_tuning_live_context,
        invoke_unity=invoke_unity_mcp,
        serialize_result=serialize_result,
        serialize_avatar=lambda context: serialize_selected_avatar(
            context.selected_avatar
        ),
        verify_live_changes=lambda context, changes: verify_live_blendshape_changes(
            context.settings,
            context.selected_avatar,
            changes,
        ),
        remember_avatar=remember_loaded_avatar,
        prepare_face_state=_prepare_face_tuning_state,
        face_adjustments_from_plan=_avatar_tuning_face_adjustments,
        render_face_summary=_render_avatar_tuning_face_summary,
        save_face_artifacts=_save_avatar_tuning_face_artifacts,
        save_face_history=_save_avatar_tuning_face_history,
    ),
)
AVATAR_TUNING_WORKFLOWS = AvatarTuningWorkflowService(
    AvatarTuningWorkflowPorts(
        scan_scene_avatars=_scan_scene_avatars_tuning_adapter,
        read_avatars=_read_avatars_tuning_adapter,
        read_avatar_blendshapes=_read_avatar_blendshapes_tuning_adapter,
        run_face_tuning=_run_face_tuning_adapter,
        preview_manual_blendshapes=_preview_manual_blendshapes_adapter,
        preview_agent_blendshape_apply=_preview_agent_blendshape_adapter,
        request_supervised_write=request_supervised_unity_write,
        load_history=AVATAR_TUNING_STORES.load_history,
        load_presets=AVATAR_TUNING_STORES.load_presets,
        load_locked_blendshapes=AVATAR_TUNING_STORES.load_locked_blendshapes,
        current_avatar_path=lambda: (
            DASHBOARD_RUNTIME.current_avatar_path if DASHBOARD_RUNTIME else ""
        ),
        create_preset=AVATAR_TUNING_STORES.create_preset,
        rename_preset=AVATAR_TUNING_STORES.rename_preset,
        duplicate_preset=AVATAR_TUNING_STORES.duplicate_preset,
        delete_preset=AVATAR_TUNING_STORES.delete_preset,
        update_locks=AVATAR_TUNING_STORES.update_locks,
        ai_select_locks=_ai_select_tuning_locks_adapter,
        preview_saved_history=_preview_saved_tuning_history_adapter,
        preview_saved_preset=_preview_saved_tuning_preset_adapter,
    )
)
AVATAR_TUNING_APPROVED_WRITES = AvatarTuningApprovedWriteHandlers(
    prepare_manual_apply=AVATAR_TUNING_PREPARED.prepare_manual_apply,
    execute_manual_apply=AVATAR_TUNING_PREPARED.execute_manual_apply,
    prepare_manual_undo=AVATAR_TUNING_PREPARED.prepare_manual_undo,
    execute_manual_undo=AVATAR_TUNING_PREPARED.execute_manual_undo,
    prepare_face_tuning=AVATAR_TUNING_PREPARED.prepare_face_tuning,
    execute_face_tuning=AVATAR_TUNING_PREPARED.execute_face_tuning,
    prepare_reapply_history=AVATAR_TUNING_PREPARED.prepare_reapply_history,
    execute_reapply_history=AVATAR_TUNING_PREPARED.execute_reapply_history,
    prepare_apply_preset=AVATAR_TUNING_PREPARED.prepare_apply_preset,
    execute_apply_preset=AVATAR_TUNING_PREPARED.execute_apply_preset,
)


def _preview_optimizer_parameter_bit_packing(
    params: dict[str, Any],
) -> dict[str, Any]:
    try:
        return preview_parameter_bit_packing_sync(params)
    except (AgentGatewayError, AuthoritativeUnityWriteError) as exc:
        raise OptimizationApplyPreviewError(str(exc)) from exc


OPTIMIZATION_APPLY_PREVIEWS = OptimizationApplyPreviewService(
    OptimizationApplyPreviewPorts(
        resolve_project_path=lambda params: resolve_project_path(
            params,
            DASHBOARD_STATE.selected_project_path if DASHBOARD_STATE else "",
        ),
        package_install_plan=PACKAGE_INSTALL_WORKFLOWS.plan_install,
        build_parameter_bit_packing_arguments=(
            build_parameter_bit_packing_wrapper_arguments
        ),
        preview_parameter_bit_packing=_preview_optimizer_parameter_bit_packing,
    )
)
OPTIMIZER_PROOFS = OptimizerProofStore(
    OptimizerProofStorePorts(
        artifact_root=ARTIFACTS_DIR,
        to_artifact_url=to_artifact_url,
        to_runtime_artifact_url=to_runtime_artifact_url,
    )
)
OPTIMIZATION_WORKFLOWS = OptimizationWorkflowService(
    OptimizationWorkflowPorts(
        selected_project_path=lambda: (
            DASHBOARD_STATE.selected_project_path if DASHBOARD_STATE else ""
        ),
        build_validation_report=build_validation_report_sync,
        build_report=build_optimization_report,
        normalize_tool_name=normalize_tool_name,
        build_tool_result=build_optimization_tool_result,
        build_apply_preview=OPTIMIZATION_APPLY_PREVIEWS.build,
        build_validation_delta=build_optimization_validation_delta,
        create_apply_request=AGENT_GATEWAY.create_apply_request,
        proofs=OPTIMIZER_PROOFS,
        parameter_bit_packing_tool=PARAMETER_BIT_PACKING_TOOL,
    )
)
SHADER_VISION_PROTECTION = ShaderVisionProtectionService(
    ShaderWorkflowPorts(
        scan=scan_shader_materials_sync,
        plan=generate_shader_material_plan_sync,
        preview_apply=preview_agent_shader_apply,
        preview_material_assignment=preview_material_shader_assignment_sync,
        request_supervised_write=request_supervised_unity_write,
        load_history_store=load_shader_tuning_history_store,
        load_preset_store=load_shader_tuning_preset_store,
        create_preset=create_shader_tuning_preset_sync,
        rename_preset=rename_shader_tuning_preset_sync,
        duplicate_preset=duplicate_shader_tuning_preset_sync,
        delete_preset=delete_shader_tuning_preset_sync,
        current_avatar_path=lambda: DASHBOARD_RUNTIME.current_avatar_path,
        load_locks=load_shader_tuning_locks,
        update_locks=update_shader_tuning_locks_sync,
        review_vision=review_shader_material_vision_sync,
    ),
    VisionAuditWorkflowPorts(
        request_supervised_capture=request_supervised_vision_capture,
        read_capture_status=read_vision_capture_status_sync,
        request_supervised_multi_capture=request_supervised_vision_capture,
        audit_capture=audit_avatar_screenshot_sync,
        audit_multi_capture=audit_avatar_multi_screenshot_sync,
    ),
    ProtectionWorkflowPorts(
        research_report=build_avatar_encryption_research_report_sync,
        scan=scan_avatar_encryption_sync,
        plan=plan_avatar_encryption_sync,
        preview=preview_avatar_encryption_sync,
        addon_status=avatar_encryption_addon_status_sync,
        request_supervised_apply=request_avatar_encryption_apply_sync,
        request_supervised_remove=request_avatar_encryption_remove_sync,
    ),
)

AGENT_GATEWAY.checkpoint_project_root_resolver = lambda: DASHBOARD_STATE.selected_project_path if DASHBOARD_STATE else ""
AGENT_GATEWAY.checkpoint_prepare_handler = prepare_unity_checkpoint_sync
AGENT_GATEWAY.checkpoint_restore_prepare_handler = prepare_unity_checkpoint_restore_sync
AGENT_GATEWAY.checkpoint_restore_handler = reload_unity_checkpoint_sync
AGENT_GATEWAY.scoped_approval_reviewer_fn = _review_saved_project_category_approval

register_agent_gateway_tools()


def create_primitive_basis_live_runtime(
    session: PrimitiveBasisLiveSession,
    connection: PrimitiveBasisLiveUnityConnection,
) -> ModelPartCompositionLiveRuntime:
    return ModelPartCompositionLiveRuntime(
        session,
        LiveRuntimeCallbacks(
            bind_connection=connection.bind,
            validate_connection=connection.validate,
            inspect_fixture=connection.inspect_fixture,
            reload_fixture=connection.reload_fixture,
            inspect_component=connection.inspect_component,
            preview_component=connection.preview_component,
            create_apply_request=lambda params: AGENT_GATEWAY.create_apply_request(
                params,
                include_arguments_digest=True,
            ),
            read_compile_status=connection.read_compile_status,
            create_restore_request=create_primitive_basis_restore_request_sync,
            preview_checkpoint=lambda checkpoint_id: AGENT_GATEWAY.preview_restore_checkpoint(
                {"checkpointId": checkpoint_id}
            ),
        ),
    )


class PrimitiveBasisLiveUnityConnection:
    def __init__(self) -> None:
        self._lock = RLock()
        self._settings: Settings | None = None
        self._project_root = ""
        self._project_path_digest = ""
        self._binding_digest = ""
        self._settings_path = ""
        self._core_port = 0
        self._core_instance_id = ""
        self._core_process_id = 0
        self._core_project_hash = ""

    def is_frozen(self) -> bool:
        with self._lock:
            return self._settings is not None

    def bind(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._settings is not None:
                raise PrimitiveBasisLiveRuntimeError("The fixed Unity connection is already frozen.")
            project_root = normalize_path_string(str(params.get("projectPath") or ""))
            selected = normalize_path_string(str(DASHBOARD_STATE.selected_project_path or ""))
            if not project_root or selected != project_root:
                raise PrimitiveBasisLiveRuntimeError("The fixed Unity project selection changed.")
            request = ConnectionRequest(settings_path=str(DASHBOARD_STATE.settings_path))
            settings = load_dashboard_settings(request)
            try:
                core = load_unity_mcp_core_connection(Path(project_root))
            except UnityMcpCoreError as exc:
                raise PrimitiveBasisLiveRuntimeError("The project-scoped VRCForge MCP Core is not ready.") from exc
            settings.unity_mcp_command = []
            settings.unity_mcp_host = "127.0.0.1"
            settings.unity_mcp_port = 0
            settings.unity_mcp_instance = ""
            settings.unity_mcp_timeout_seconds = max(
                int(settings.unity_mcp_timeout_seconds or 30), 180
            )
            project_path_digest = hashlib.sha256(
                project_root.replace("\\", "/").rstrip("/").lower().encode("utf-8")
            ).hexdigest()
            binding_digest = hashlib.sha256(
                json.dumps(
                    {
                        "projectPathDigest": project_path_digest,
                        "transport": "vrcforge-mcp-core",
                        "corePort": core.port,
                        "coreInstanceId": core.instance_id,
                        "coreProcessId": core.process_id,
                        "coreProjectHash": core.project_hash,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            self._settings = settings
            self._project_root = project_root
            self._project_path_digest = project_path_digest
            self._binding_digest = binding_digest
            self._settings_path = str(DASHBOARD_STATE.settings_path)
            self._core_port = core.port
            self._core_instance_id = core.instance_id
            self._core_process_id = core.process_id
            self._core_project_hash = core.project_hash
            return self._public_binding()

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._settings is None:
                raise PrimitiveBasisLiveRuntimeError("The fixed Unity connection is not frozen.")
            expected = str(params.get("connectionBindingDigest") or "")
            if expected and expected != self._binding_digest:
                raise PrimitiveBasisLiveRuntimeError("The fixed Unity connection binding changed.")
            self._validate_current_locked()
            return self._public_binding()

    def state_update_allowed(self, request: DashboardStateRequest) -> bool:
        with self._lock:
            if self._settings is None:
                return True
            project = (
                normalize_path_string(request.project_path)
                if request.project_path is not None
                else self._project_root
            )
            return (
                project == self._project_root
                and str(resolve_local_path(request.settings_path)) == self._settings_path
            )

    def inspect_fixture(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._invoke_payload(
            "vrc_inspect_primitive_basis_fixture",
            {"expectedRunIdDigest": str(params.get("expectedRunIdDigest") or "")},
            "primitive-basis fixture inspection",
        )

    def reload_fixture(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._invoke_payload(
            "vrc_reload_primitive_basis_fixture",
            dict(params),
            "primitive-basis fixture reload",
        )

    def inspect_component(self, params: dict[str, Any]) -> dict[str, Any]:
        request = build_inspect_modular_avatar_component_request(params)
        request.update(_primitive_live_guard_fields(params))
        return self._invoke_payload(
            "vrc_inspect_modular_avatar_component",
            request,
            "primitive-basis component inspection",
        )

    def preview_component(self, params: dict[str, Any]) -> dict[str, Any]:
        request = build_owned_add_modular_avatar_component_request(params, True)
        request.update(_primitive_live_guard_fields(params))
        return self._invoke_payload(
            "vrc_add_modular_avatar_component",
            request,
            "primitive-basis component preview",
        )

    def apply_component(self, params: dict[str, Any]) -> dict[str, Any]:
        request = build_owned_add_modular_avatar_component_request(params, False)
        request.update(_primitive_live_guard_fields(params))
        return self._invoke_payload(
            "vrc_add_modular_avatar_component",
            request,
            "primitive-basis component apply",
        )

    def read_compile_status(self, params: dict[str, Any]) -> dict[str, Any]:
        arguments = {"maxErrors": int(params.get("maxErrors") or 20)}
        arguments.update(_primitive_live_guard_fields(params))
        result = self._invoke_result("vrc_get_compile_errors", arguments)
        payload = ensure_dict_payload(
            extract_tool_result_payload(result),
            "primitive-basis compile status",
        )
        return {
            **payload,
            "ok": result.exit_code == 0 and payload.get("ok") is not False,
            "exitCode": result.exit_code,
        }

    def prepare_checkpoint(self, project_root: Path) -> dict[str, Any]:
        return self._checkpoint_call("vrc_prepare_checkpoint", project_root)

    def prepare_restore_checkpoint(self, project_root: Path) -> dict[str, Any]:
        return self._checkpoint_call(
            "vrc_reload_after_checkpoint_restore",
            project_root,
            {"phase": "prepare_restore"},
        )

    def reload_checkpoint(
        self,
        project_root: Path,
        restore_prepare: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prepared = ensure_dict(restore_prepare)
        return self._checkpoint_call(
            "vrc_reload_after_checkpoint_restore",
            project_root,
            {
                "phase": "reload",
                "scenePaths": normalize_string_list(
                    prepared.get("scenes") if isinstance(prepared.get("scenes"), list) else []
                ),
                "activeScenePath": str(prepared.get("activeScenePath") or "").strip(),
            },
        )

    def _checkpoint_call(
        self,
        tool_name: str,
        project_root: Path,
        extra_arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if normalize_path_string(str(project_root)) != self._project_root:
            raise PrimitiveBasisLiveRuntimeError("The fixed checkpoint project changed.")
        result = self._invoke_result(
            tool_name,
            {"projectPath": str(project_root), **ensure_dict(extra_arguments), **self._guard_fields()},
            preserve_tool_error=True,
        )
        return normalize_unity_checkpoint_result(result, project_root)

    def _guard_fields(self) -> dict[str, Any]:
        runtime = PRIMITIVE_BASIS_LIVE_RUNTIME
        if runtime is None:
            return {}
        return _primitive_live_guard_fields(runtime._component_arguments(preview=False))

    def _invoke_payload(
        self, tool_name: str, arguments: dict[str, Any], label: str
    ) -> dict[str, Any]:
        result = self._invoke_result(tool_name, arguments)
        payload = ensure_dict_payload(
            extract_tool_result_payload(result),
            label,
        )
        call_audit = _primitive_live_call_audit(result, tool_name)
        if call_audit:
            # Keep only the existing non-sensitive Core audit projection.  In
            # particular, this does not copy guarded arguments or their hash.
            payload = {**payload, "_meta": {"io.vrcforge/callAudit": call_audit}}
        payload.setdefault("ok", True)
        return payload

    def _invoke_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        preserve_tool_error: bool = False,
    ) -> McpResult:
        with self._lock:
            settings = self._settings
            if settings is None:
                raise PrimitiveBasisLiveRuntimeError("The fixed Unity connection is not frozen.")
            self._validate_current_locked()
            result = invoke_unity_mcp(
                settings,
                tool_name,
                arguments,
                preserve_tool_error=preserve_tool_error,
            )
            self._validate_current_locked()
            return result

    def _validate_current_locked(self) -> None:
        current_project = normalize_path_string(str(DASHBOARD_STATE.selected_project_path or ""))
        try:
            core = load_unity_mcp_core_connection(Path(self._project_root))
        except UnityMcpCoreError as exc:
            raise PrimitiveBasisLiveRuntimeError("The fixed project-scoped MCP Core is unavailable.") from exc
        if (
            current_project != self._project_root
            or str(DASHBOARD_STATE.settings_path) != self._settings_path
            or core.port != self._core_port
            or core.instance_id != self._core_instance_id
            or core.process_id != self._core_process_id
            or core.project_hash != self._core_project_hash
        ):
            raise PrimitiveBasisLiveRuntimeError("The fixed Unity connection changed.")

    def _public_binding(self) -> dict[str, Any]:
        return {
            "ok": True,
            "schema": "vrcforge.primitive_basis_connection_binding.v1",
            "frozen": True,
            "projectPathDigest": self._project_path_digest,
            "connectionBindingDigest": self._binding_digest,
        }


def _primitive_live_guard_fields(params: dict[str, Any]) -> dict[str, Any]:
    names = (
        "expectedRunIdDigest",
        "expectedProjectPathDigest",
        "expectedUnityProcessId",
        "expectedUnityProcessStartedAtUtc",
        "expectedUnityExecutableDigest",
    )
    return {name: params[name] for name in names if name in params}


def _primitive_live_call_audit(result: McpResult, expected_tool: str) -> dict[str, Any]:
    """Project the standard Core audit without retaining tool arguments."""
    raw = result.payload if isinstance(result.payload, Mapping) else {}
    metadata = raw.get("_meta") if isinstance(raw, Mapping) else None
    audit = metadata.get("io.vrcforge/callAudit") if isinstance(metadata, Mapping) else None
    if not isinstance(audit, Mapping) or audit.get("toolName") != expected_tool:
        return {}
    request_id = audit.get("requestId")
    duration_ms = audit.get("durationMs")
    result_summary = audit.get("resultSummary")
    if (
        type(request_id) is not int
        or request_id <= 0
        or not isinstance(duration_ms, (int, float))
        or duration_ms < 0
        or result_summary not in {"complete", "error", "pending"}
    ):
        return {}
    return {
        "requestId": request_id,
        "toolName": expected_tool,
        "resultSummary": result_summary,
        "durationMs": duration_ms,
    }


PRIMITIVE_BASIS_LIVE_CONNECTION: PrimitiveBasisLiveUnityConnection | None = None


def install_primitive_basis_live_runtime(
    session: PrimitiveBasisLiveSession | None,
) -> ModelPartCompositionLiveRuntime | None:
    global PRIMITIVE_BASIS_LIVE_CONNECTION
    global PRIMITIVE_BASIS_LIVE_RUNTIME
    global PRIMITIVE_BASIS_LIVE_SESSION

    if session is None:
        PRIMITIVE_BASIS_LIVE_SESSION = None
        PRIMITIVE_BASIS_LIVE_CONNECTION = None
        PRIMITIVE_BASIS_LIVE_RUNTIME = None
        AGENT_GATEWAY.apply_lifecycle_observer_fn = None
        return None

    connection = PrimitiveBasisLiveUnityConnection()
    runtime = create_primitive_basis_live_runtime(session, connection)
    PRIMITIVE_BASIS_LIVE_SESSION = session
    PRIMITIVE_BASIS_LIVE_CONNECTION = connection
    PRIMITIVE_BASIS_LIVE_RUNTIME = runtime
    AGENT_GATEWAY.apply_lifecycle_observer_fn = runtime.observe_apply_lifecycle
    return runtime


install_primitive_basis_live_runtime(PRIMITIVE_BASIS_LIVE_SESSION)
app.mount("/", AGENT_MCP_MOUNT, name="agent_mcp")


def to_http_exception(exc: Exception) -> HTTPException:
    detail = str(exc)
    lowered = detail.lower()
    status_code = 503 if "unity mcp server is not ready yet" in lowered or "cannot connect to unity mcp server" in lowered else 400
    return HTTPException(status_code=status_code, detail=detail)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if "--cli" in raw_args:
        cli_index = raw_args.index("--cli")
        return argparse.Namespace(
            host="127.0.0.1",
            port=8757,
            agent_mcp_stdio=False,
            preflight=False,
            json=False,
            cli=True,
            no_start=False,
            start_runtime=False,
            cleanup_user_data=False,
            cleanup_user_data_root="",
            cli_args=raw_args[cli_index + 1 :],
        )
    parser = argparse.ArgumentParser(description="Launch the VRChat Blendshape control dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard bind host.")
    parser.add_argument("--port", default=8757, type=int, help="Dashboard bind port.")
    parser.add_argument("--agent-mcp-stdio", action="store_true", help="Run the external-agent stdio MCP bridge instead of the HTTP backend.")
    parser.add_argument("--start-runtime", action="store_true", help="With --agent-mcp-stdio, launch VRCForge Desktop when the runtime is offline.")
    parser.add_argument("--no-start", action="store_true", help="Compatibility flag; stdio runtime auto-launch is disabled by default.")
    parser.add_argument("--preflight", action="store_true", help="With --agent-mcp-stdio, print a bridge preflight report and exit.")
    parser.add_argument("--json", action="store_true", help="Compatibility flag for preflight JSON output.")
    parser.add_argument("--cli", action="store_true", help="Run the VRCForge CLI against the local desktop runtime.")
    parser.add_argument("--cleanup-user-data", action="store_true", help="Installer helper: remove VRCForge user data and known project chat transcripts.")
    parser.add_argument("--cleanup-user-data-root", default="", help="Installer helper override for the VRCForge user data root.")
    return parser.parse_args(raw_args)


def cleanup_user_data_root(user_data_root: Path) -> dict[str, Any]:
    root = user_data_root.expanduser().resolve()
    if root.name.casefold() != "agentic-app" or root.parent.name.casefold() != "vrcforge":
        raise RuntimeError("Refusing to clean a path outside the VRCForge agentic-app data directory.")
    projects: set[Path] = set()

    def add_project(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        try:
            path = Path(text).expanduser()
            if path.is_absolute():
                projects.add(path.resolve())
        except OSError:
            return

    def read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - uninstall cleanup is best-effort.
            return None

    index_payload = read_json(root / "chat-projects.json")
    if isinstance(index_payload, dict):
        for item in index_payload.get("projectPaths") or []:
            add_project(item)

    custom_payload = read_json(root / "custom-projects.json")
    if isinstance(custom_payload, dict):
        for item in list(custom_payload.get("customPaths") or []) + list(custom_payload.get("hiddenPaths") or []):
            add_project(item)

    legacy_payload = read_json(root / "chat-transcripts.json")
    if isinstance(legacy_payload, dict):
        for chat in legacy_payload.get("chats") or []:
            if isinstance(chat, dict):
                add_project(chat.get("projectPath"))

    removed_project_transcripts: list[str] = []
    for project in sorted(projects, key=lambda path: str(path).casefold()):
        transcript = project / ".vrcforge" / "chat-transcripts.json"
        try:
            if transcript.exists():
                transcript.unlink()
                removed_project_transcripts.append(str(transcript))
            metadata_dir = transcript.parent
            if metadata_dir.exists() and not any(metadata_dir.iterdir()):
                metadata_dir.rmdir()
        except OSError:
            continue

    root_removed = False
    try:
        if root.exists():
            shutil.rmtree(root)
            root_removed = True
    except OSError:
        root_removed = False

    return {
        "ok": True,
        "schema": "vrcforge.installer_cleanup.v1",
        "userDataRoot": str(root),
        "rootRemoved": root_removed,
        "projectTranscriptCount": len(removed_project_transcripts),
    }


def backend_bind_target_occupied(host: str, port: int) -> bool:
    """Fail closed when the requested HTTP bind target cannot be reserved."""

    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    except OSError:
        return True
    if not addresses:
        return True
    seen: set[tuple[int, tuple[Any, ...]]] = set()
    for family, socket_type, protocol, _canonical_name, address in addresses:
        key = (family, tuple(address))
        if key in seen:
            continue
        seen.add(key)
        probe: socket.socket | None = None
        try:
            probe = socket.socket(family, socket_type, protocol)
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(address)
        except OSError:
            return True
        finally:
            if probe is not None:
                probe.close()
    return False


def main() -> int:
    args = parse_args()
    if args.cli:
        from tools.vrcforge_cli import main as cli_main

        return cli_main(args.cli_args)
    if args.cleanup_user_data:
        root = Path(args.cleanup_user_data_root).expanduser() if str(args.cleanup_user_data_root or "").strip() else USER_DATA_DIR
        print(json.dumps(cleanup_user_data_root(root), ensure_ascii=False, sort_keys=True))
        return 0
    if args.agent_mcp_stdio:
        from tools.vrcforge_agent_mcp_stdio import VRCForgeBridge, run_stdio_server

        no_start_env = str(os.environ.get("VRCFORGE_AGENT_NO_START") or "").strip().lower()
        start_runtime_env = str(os.environ.get("VRCFORGE_AGENT_START_RUNTIME") or "").strip().lower()
        no_start = bool(args.no_start or no_start_env in {"1", "true", "yes", "on"})
        start_runtime = bool(args.start_runtime or start_runtime_env in {"1", "true", "yes", "on"}) and not no_start

        bridge = VRCForgeBridge(
            base_url=os.environ.get("VRCFORGE_AGENT_BASE_URL", "http://127.0.0.1:8757").rstrip("/"),
            config_path=Path(os.environ["VRCFORGE_AGENT_GATEWAY_CONFIG"]).expanduser().resolve()
            if os.environ.get("VRCFORGE_AGENT_GATEWAY_CONFIG")
            else None,
            timeout_seconds=float(os.environ.get("VRCFORGE_AGENT_TIMEOUT", "30")),
            start_runtime=start_runtime,
        )
        if args.preflight:
            print(json.dumps(bridge.preflight(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        run_stdio_server(bridge)
        return 0
    adoption_requested = backend_listener_adoption_requested()
    if not adoption_requested and backend_bind_target_occupied(args.host, args.port):
        print(
            f"VRCForge backend refused to start because {args.host}:{args.port} is already occupied.",
            file=sys.stderr,
        )
        return 1
    if not BACKEND_OWNER_LEASE.acquire():
        print(
            f"VRCForge backend refused to start because another runtime owns {BACKEND_OWNER_LEASE.path}.",
            file=sys.stderr,
        )
        return 1
    adopted = None
    if adoption_requested:
        try:
            adopted = load_backend_listener_adoption()
            if adopted is None:
                raise BackendListenerAdoptionError("backend_adoption_missing")
            install_primitive_basis_live_runtime(adopted.live_session)
            adopted.acknowledge()
        except Exception:
            install_primitive_basis_live_runtime(None)
            if adopted is not None:
                adopted.close()
            BACKEND_OWNER_LEASE.release()
            print(
                "VRCForge backend refused the protected listener adoption.",
                file=sys.stderr,
            )
            return 1
    if getattr(sys, "frozen", False):
        install_standard_stream_capture(DIAGNOSTIC_LOGGER)
    try:
        if adopted is None:
            run_owned_uvicorn_server(args.host, args.port)
        else:
            run_owned_uvicorn_server(
                args.host,
                args.port,
                sockets=[adopted.listener_socket],
            )
    finally:
        if adopted is not None:
            try:
                adopted.close()
            finally:
                install_primitive_basis_live_runtime(None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

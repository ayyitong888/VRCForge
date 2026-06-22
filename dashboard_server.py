from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hmac
import json
import math
import mimetypes
import os
import subprocess
import re
import secrets
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_gateway import AgentGateway, AgentGatewayError, create_agent_mcp_app, ensure_dict, redact_sensitive
from external_agent_connector_installer import (
    ConnectorInstallError,
    connector_client_statuses,
    install_connector,
    resolve_stdio_bridge,
    uninstall_connector,
)
from external_agent_connectors import ExternalAgentConnectorOptions, build_connector_bundle
from optimization_service import (
    OPTIMIZATION_APPLY_REQUEST_BY_EXTERNAL,
    OPTIMIZATION_APPLY_REQUEST_BY_GATEWAY,
    OPTIMIZATION_APPLY_REQUEST_DEFINITIONS,
    OPTIMIZATION_GATEWAY_TOOL_NAMES,
    OPTIMIZER_DEPENDENCIES,
    OPTIMIZATION_TOOL_DEFINITIONS,
    STABLE_OPTIMIZATION_APPLY_REQUEST_DEFINITIONS,
    build_optimization_report,
    build_optimization_tool_result,
    normalize_tool_name,
)
from outfit_import_planner import build_outfit_import_plan
from outfit_package_inspector import inspect_outfit_package, is_safe_archive_path, normalize_archive_name
from project_memory_index import scan_project_memory
from skill_packages import SkillPackageError, SkillPackageService
from sub_agent_tasks import CancelledError, SubAgentRole, SubAgentTaskRegistry
from vrchat_blendshape_agent import (
    DEFAULT_LLM_PROVIDER,
    DEFAULT_MVP_EXPORT_PATH,
    DEFAULT_SETTINGS_PATH,
    McpResult,
    SelectedAvatar,
    Settings,
    UnityMcpError,
    build_planning_payload,
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
    provider_display_name,
    provider_requires_api_key,
    read_plan_json,
    request_llm_plan,
    request_llm_plan_with_metadata,
    render_apply_payload_json,
    render_preview,
    render_summary,
    run_unity_mcp_passthrough,
    save_plan,
    save_result,
    save_text,
    extract_json_block,
    try_parse_json,
    validate_plan,
    resolve_avatar_selection,
)


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
        "VRCFORGE_LOG_DIR",
        "VRCFORGE_ARTIFACTS_DIR",
        "VRCFORGE_DASHBOARD_DIR",
        "VRCFORGE_SETTINGS_PATH",
    )
)
USER_DATA_DIR = resolve_runtime_path("VRCFORGE_USER_DATA_DIR", default_user_data_root())
DASHBOARD_DIR = resolve_runtime_path("VRCFORGE_DASHBOARD_DIR", ROOT_DIR / "dashboard")
CONFIG_DIR = resolve_runtime_path("VRCFORGE_CONFIG_DIR", USER_DATA_DIR / "config") if PORTABLE_MODE else ROOT_DIR
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
INSTALL_SCRIPT_PATH = TOOLS_DIR / "install-unity-project.ps1"
CONFIG_PATH = CONFIG_DIR / "config.json" if PORTABLE_MODE else ROOT_DIR / "config.json"
RUNTIME_SETTINGS_PATH = resolve_runtime_path(
    "VRCFORGE_SETTINGS_PATH",
    CONFIG_DIR / "settings.json" if PORTABLE_MODE else ROOT_DIR / DEFAULT_SETTINGS_PATH,
)
LOCAL_LOG_PATH = LOG_DIR / "dashboard.log"
LOG_RETENTION = timedelta(hours=24)
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
    if not PORTABLE_MODE:
        return ""
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


APP_SESSION_TOKEN = resolve_app_session_token()
APP_AUTH_REQUIRED = bool(APP_SESSION_TOKEN)
APP_ALLOWED_ORIGINS = {
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://127.0.0.1:1420",
    "http://localhost:1420",
}
REQUIRED_VRCFORGE_UNITY_TOOLS = [
    "vrc_export_blendshapes",
    "vrc_apply_blendshapes",
    "vrc_capture_scene_view",
    "vrc_scan_avatar_materials",
    "vrc_apply_material_tuning",
    "vrc_scan_avatar_items",
    "vrc_scan_fx_animator",
    "vrc_scan_animation_bindings",
    "vrc_create_safe_backup",
    "vrc_restore_safe_backup",
    "vrc_setup_outfit",
    "vrc_scan_avatar_performance",
]

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


class ConnectionRequest(BaseModel):
    settings_path: str = Field(default_factory=runtime_settings_path)
    unity_host: str | None = None
    unity_port: int | None = None
    unity_instance: str | None = None


class DashboardStateRequest(BaseModel):
    settings_path: str = Field(default_factory=runtime_settings_path)
    project_path: str | None = None
    unity_host: str | None = None
    unity_port: int | None = None
    unity_instance: str | None = None


class ProjectActionRequest(BaseModel):
    project_path: str | None = None


class ProjectInstallRequest(BaseModel):
    project_path: str | None = None
    launch_unity: bool = False


class UnityMcpRepairRequest(BaseModel):
    project_path: str = Field(default="", alias="projectPath")
    unity_editor_path: str = Field(default="", alias="unityEditorPath")
    allow_unity_relaunch: bool = Field(default=False, alias="allowUnityRelaunch")
    wait_seconds: int = Field(default=90, alias="waitSeconds", ge=5, le=360)
    close_timeout_seconds: int = Field(default=60, alias="closeTimeoutSeconds", ge=5, le=180)

    model_config = {"populate_by_name": True}


class ApiConfigRequest(BaseModel):
    provider: str = DEFAULT_LLM_PROVIDER
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None


class ApiModelListRequest(ApiConfigRequest):
    pass


class DiagnosticsConfigRequest(BaseModel):
    debug_logging: bool = Field(default=False, alias="debugLogging")


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
    avatar_path: str | None = None


class ShaderMaterialScanRequest(AvatarScopedConnectionRequest):
    category_overrides: dict[str, str] = Field(default_factory=dict)


class ShaderMaterialPlanRequest(DashboardRequest):
    avatar_path: str | None = None
    inventory: dict[str, Any] | None = None
    category_overrides: dict[str, str] = Field(default_factory=dict)
    locked_materials: list[str] = Field(default_factory=list)
    locked_properties: list[str] = Field(default_factory=list)


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


class ClothingToggleRequest(ConnectionRequest):
    object_path: str
    active: bool


class VisionCaptureRequest(ConnectionRequest):
    avatar_path: str | None = None
    width: int = 960
    height: int = 960
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
    width: int = 960
    height: int = 960
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
    message: str
    shell_command: str | None = None
    skill_tool: str | None = None
    skill_params: dict[str, Any] = Field(default_factory=dict)
    cwd: str | None = None
    workspace_root: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class AgentPermissionRequest(BaseModel):
    execution_mode: str = Field(default="approval")
    acknowledge_roslyn_risk: bool = Field(default=False)


class AgentNotesRequest(BaseModel):
    content: str = Field(default="", max_length=262144)


class ChatTranscriptsRequest(BaseModel):
    chats: list[dict[str, Any]] = Field(default_factory=list)


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

    model_config = {"populate_by_name": True}


class ExternalAgentConnectorActionRequest(BaseModel):
    client: Literal["codex", "codexApp", "codexCli", "claudeCode", "claudeCowork"]
    project_path: str | None = Field(default=None, alias="projectPath")

    model_config = {"populate_by_name": True}


class SkillPackagePathRequest(BaseModel):
    package_path: str = Field(alias="packagePath")
    allow_downgrade: bool = Field(default=False, alias="allowDowngrade")
    dev_mode: bool = Field(default=False, alias="devMode")
    project_to_user_skills: bool = Field(default=True, alias="projectToUserSkills")

    model_config = {"populate_by_name": True}


class SkillPackageExportRequest(BaseModel):
    skill_name: str = Field(alias="skillName")
    output_path: str = Field(alias="outputPath")
    release: bool = False
    private_key_path: str | None = Field(default=None, alias="privateKeyPath")
    private_key_pem: str | None = Field(default=None, alias="privateKeyPem")

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

    model_config = {"populate_by_name": True}


class SubAgentCreateRequest(BaseModel):
    role: str = Field(default="project_index_review")
    task: str = Field(default="")
    display_name: str = Field(default="", alias="displayName")
    parent_session_id: str = Field(default="", alias="parentSessionId")
    project_path: str = Field(default="", alias="projectPath")
    params: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class ProviderTestRequest(ApiConfigRequest):
    capability: Literal["text", "structured", "vision"] = "text"


class AgentCompactRequest(BaseModel):
    history: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class DashboardApiConfig:
    provider: str
    api_key: str
    base_url: str
    model: str


@dataclass
class DashboardState:
    settings_path: Path
    project_roots: list[Path] = field(default_factory=list)
    unity_editor_path: str = ""
    status_push_interval_seconds: float = 2.5
    selected_project_path: str = ""
    unity_host: str = "127.0.0.1"
    unity_port: int = 8080
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

EVENT_BUS = DashboardEventBus()
RECENT_LOGS: deque[dict[str, Any]] = deque(maxlen=300)
LOCAL_LOG_LOCK = Lock()
TUNING_STORE_LOCK = Lock()
CURRENT_UNITY_STATUS: dict[str, Any] | None = None
LAST_STATUS_FINGERPRINT = ""
LAST_STATUS_CONNECTED: bool | None = None
STATUS_MONITOR_TASK: asyncio.Task[None] | None = None
DASHBOARD_STATE: DashboardState | None = None
DASHBOARD_API_CONFIG: DashboardApiConfig | None = None
DASHBOARD_RUNTIME = DashboardRuntimeState()
AGENT_GATEWAY = AgentGateway(
    config_path=AGENT_GATEWAY_CONFIG_PATH,
    audit_dir=AGENT_GATEWAY_AUDIT_DIR,
)
SUB_AGENT_REGISTRY = SubAgentTaskRegistry(
    artifact_dir=SUB_AGENT_TASK_DIR,
    roles=[
        SubAgentRole(
            id="project_index_review",
            title="Project index review",
            description="Scan the local Unity project index and summarize changed scanner families.",
            tool_profile="local-index-only",
        ),
        SubAgentRole(
            id="outfit_package_inspection",
            title="Outfit package inspection",
            description="Inspect a UnityPackage, Booth ZIP/folder, or loose prefab folder without reading asset payload bytes.",
            tool_profile="read-only",
        ),
        SubAgentRole(
            id="validation_triage",
            title="Validation triage",
            description="Run the read-only validation report and summarize errors, warnings, and likely next plans.",
            tool_profile="read-only",
        ),
        SubAgentRole(
            id="package_install_diagnosis",
            title="Package install diagnosis",
            description="Classify package install output and Unity compile errors without repairing automatically.",
            tool_profile="read-only",
        ),
        SubAgentRole(
            id="outfit_import_plan_review",
            title="Outfit import plan review",
            description="Inspect a package and build a supervised import plan without writing to Unity.",
            tool_profile="plan-only",
        ),
    ],
    handlers={
        "project_index_review": lambda payload, cancel_event: run_project_index_sub_agent(payload, cancel_event),
        "outfit_package_inspection": lambda payload, cancel_event: run_outfit_package_sub_agent(payload, cancel_event),
        "validation_triage": lambda payload, cancel_event: run_validation_sub_agent(payload, cancel_event),
        "package_install_diagnosis": lambda payload, cancel_event: run_package_install_sub_agent(payload, cancel_event),
        "outfit_import_plan_review": lambda payload, cancel_event: run_outfit_import_plan_sub_agent(payload, cancel_event),
    },
    max_concurrent=3,
)
AGENT_MCP_MOUNT = AgentMcpMount()
AGENT_MCP_APP = None
AGENT_MCP_CONTEXT = None


@app.middleware("http")
async def authorize_local_requests(request: Request, call_next):
    started_at = time.perf_counter()
    status_code = 500
    error_message = ""
    is_preflight = is_cors_preflight_request(request)
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
                }
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
                }
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
                }
            )


@app.on_event("startup")
async def on_startup() -> None:
    global STATUS_MONITOR_TASK
    global AGENT_MCP_APP
    global AGENT_MCP_CONTEXT

    EVENT_BUS.set_loop(asyncio.get_running_loop())
    try:
        AGENT_MCP_APP = create_agent_mcp_app(AGENT_GATEWAY)
        AGENT_MCP_CONTEXT = AGENT_MCP_APP.state.fastmcp_server.session_manager.run()
        await AGENT_MCP_CONTEXT.__aenter__()
        AGENT_MCP_MOUNT.app = AGENT_MCP_APP
    except Exception as exc:  # noqa: BLE001 - external MCP must not block the desktop agent.
        AGENT_MCP_APP = None
        AGENT_MCP_CONTEXT = None
        AGENT_MCP_MOUNT.app = None
        emit_log("warn", "agent", "Agent MCP app failed to initialize; desktop normal-agent mode remains available.", {"error": str(exc)})
    if not CONFIG_PATH.exists():
        save_dashboard_api_config(DASHBOARD_API_CONFIG)
    if STATUS_MONITOR_TASK is None or STATUS_MONITOR_TASK.done():
        STATUS_MONITOR_TASK = asyncio.create_task(status_monitor_loop())

    await emit_log_async(
        "info",
        "dashboard",
        "Dashboard server started.",
        {
            "projectRoots": [str(path) for path in DASHBOARD_STATE.project_roots],
            "unityEditorPath": DASHBOARD_STATE.unity_editor_path,
            "provider": DASHBOARD_API_CONFIG.provider,
            "model": DASHBOARD_API_CONFIG.model,
        },
    )


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global STATUS_MONITOR_TASK
    global AGENT_MCP_APP
    global AGENT_MCP_CONTEXT

    if STATUS_MONITOR_TASK is not None:
        STATUS_MONITOR_TASK.cancel()
        try:
            await STATUS_MONITOR_TASK
        except asyncio.CancelledError:
            pass
        STATUS_MONITOR_TASK = None
    if AGENT_MCP_CONTEXT is not None:
        await AGENT_MCP_CONTEXT.__aexit__(None, None, None)
        AGENT_MCP_CONTEXT = None
        AGENT_MCP_MOUNT.app = None
        AGENT_MCP_APP = None


@app.get("/")
def read_dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/dashboard")
@app.get("/dashboard/")
def read_dashboard_alias() -> FileResponse:
    return read_dashboard()


@app.get("/api/health")
def read_health() -> dict[str, Any]:
    settings = load_settings(
        RUNTIME_SETTINGS_PATH,
        llm_override=serialize_api_config(include_secret=True),
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
        "apiConfig": serialize_api_config(include_secret=False),
        "projects": project_snapshot_payload(),
        "logRetentionHours": int(LOG_RETENTION.total_seconds() // 3600),
        "unityStatus": CURRENT_UNITY_STATUS,
    }


@app.get("/api/app/bootstrap")
def read_agentic_app_bootstrap() -> dict[str, Any]:
    return {
        "ok": True,
        "app": {
            "name": "VRCForge",
            "version": app.version,
            "surface": "tauri-agentic-desktop",
            "browserRequired": False,
            "legacyDashboardDebugOnly": True,
        },
        "health": build_agentic_app_health(),
        "apiConfig": serialize_app_api_config(),
        "agentManifest": safe_agent_manifest(),
        "agentHealth": safe_agent_health(),
        "permission": safe_permission_state(),
        "approvals": safe_approval_list(),
    }


@app.get("/api/app/permission")
def read_agentic_app_permission() -> dict[str, Any]:
    return {"ok": True, "permission": AGENT_GATEWAY.permission_state()}


@app.post("/api/app/permission")
async def update_agentic_app_permission(request: AgentPermissionRequest) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.update_permission_state(
            request.execution_mode,
            acknowledge_roslyn_risk=request.acknowledge_roslyn_risk,
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if payload["permission"].get("roslynFullAuto") and payload["permission"].get("roslynRiskAcknowledged"):
        try:
            payload["unityAcknowledgement"] = acknowledge_unity_roslyn_risk_sync()
        except Exception as exc:  # noqa: BLE001
            payload["unityAcknowledgement"] = {
                "ok": False,
                "warning": f"Unity acknowledgement sync is pending; Unity will show its fallback dialog: {exc}",
            }
    await EVENT_BUS.broadcast("agentPermission", payload["permission"])
    return payload


@app.post("/api/app/agent/message")
async def app_agent_runtime_message(runtime_request: AgentRuntimeMessageRequest) -> dict[str, Any]:
    payload = AGENT_GATEWAY.runtime_message(
        {
            "session_id": runtime_request.session_id,
            "message": runtime_request.message,
            "shell_command": runtime_request.shell_command,
            "skill_tool": runtime_request.skill_tool,
            "skill_params": runtime_request.skill_params,
            "cwd": runtime_request.cwd,
            "workspace_root": runtime_request.workspace_root,
            "history": runtime_request.history,
        },
        agent_name=runtime_request.agent_name,
    )
    await EVENT_BUS.broadcast("agentRuntimeTurn", payload)
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


AGENT_COMPACT_TRANSCRIPT_MAX_CHARS = 60000


@app.post("/api/app/agent/compact")
def app_agent_compact(request: AgentCompactRequest) -> dict[str, Any]:
    entries: list[str] = []
    for item in request.history:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        role = "用户" if str(item.get("role") or "user").strip().lower() == "user" else "助手"
        entries.append(f"{role}: {text}")
    if not entries:
        raise HTTPException(status_code=400, detail="history is empty; nothing to compact.")

    settings = load_dashboard_settings(ConnectionRequest())
    if provider_requires_api_key(settings.llm_provider) and not settings.llm_api_key:
        raise HTTPException(
            status_code=400,
            detail=f"{provider_display_name(settings.llm_provider)} API key is not configured; LLM compaction is unavailable.",
        )

    transcript = "\n".join(entries)
    if len(transcript) > AGENT_COMPACT_TRANSCRIPT_MAX_CHARS:
        transcript = transcript[-AGENT_COMPACT_TRANSCRIPT_MAX_CHARS:]
    prompt = (
        "你是会话压缩助手。请把下面这段用户与助手的对话历史压缩成一份中文摘要，"
        "保留：用户的目标和需求、已经完成的事情和结果、做出的关键决定（含具体文件名/对象名/参数值）、"
        "尚未完成或待确认的事项。省略寒暄与重复内容。摘要控制在 500 字以内。\n"
        '只返回 JSON，格式为 {"summary": "<中文摘要>"}，不要 Markdown，不要其他文字。\n'
        "对话历史：\n"
        f"{transcript}"
    )
    try:
        raw_response = request_llm_plan(settings, prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM compaction failed: {exc}") from exc

    summary = ""
    try:
        raw_json = extract_json_block(raw_response)
        payload = json.loads(raw_json) if raw_json else {}
        if isinstance(payload, dict):
            summary = str(payload.get("summary") or "").strip()
    except Exception:  # noqa: BLE001
        summary = ""
    if not summary:
        summary = str(raw_response or "").strip()
    if not summary:
        raise HTTPException(status_code=502, detail="LLM returned an empty summary.")
    return {
        "ok": True,
        "summary": summary,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "entryCount": len(entries),
    }


@app.get("/api/app/agent/session/{session_id}")
def app_agent_runtime_session(session_id: str) -> dict[str, Any]:
    try:
        return AGENT_GATEWAY.get_runtime_session(session_id)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/app/agent/approvals")
def app_agent_approvals() -> dict[str, Any]:
    approvals = AGENT_GATEWAY.list_approvals()
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
    try:
        payload = AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": "vrcforge_restore_checkpoint",
                "arguments": {"checkpointId": checkpoint_id, "confirmRestore": True},
                "reason": "Restore Unity project files from a VRCForge checkpoint.",
                "preview": preview,
                "agent_name": "desktop-agent",
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/app/agent/approvals/{approval_id}/approve")
async def app_agent_approve_and_execute(approval_id: str) -> dict[str, Any]:
    try:
        approved = AGENT_GATEWAY.approve(approval_id)
        execution = None
        approval = approved.get("approval") if isinstance(approved, dict) else None
        if isinstance(approval, dict) and approved.get("ok"):
            if approval.get("targetTool") == "vrcforge_shell_execute":
                execution = AGENT_GATEWAY.execute_approved_shell({"approval_id": approval_id})
            else:
                execution = AGENT_GATEWAY.apply_approved({"approval_id": approval_id})
        payload = {"ok": bool(approved.get("ok")), "approval": approved.get("approval"), "execution": execution}
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/app/agent/approvals/{approval_id}/reject")
async def app_agent_reject(approval_id: str) -> dict[str, Any]:
    try:
        payload = AGENT_GATEWAY.reject(approval_id)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
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


CHAT_TRANSCRIPTS_MAX_BYTES = 16 * 1024 * 1024
CHAT_TRANSCRIPTS_MAX_CHATS = 100


def chat_transcripts_path() -> Path:
    return AGENT_GATEWAY.user_constraints_path.parent / "chat-transcripts.json"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


@app.get("/api/app/chats")
def read_chat_transcripts() -> dict[str, Any]:
    path = chat_transcripts_path()
    chats: list[dict[str, Any]] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("chats"), list):
                chats = [item for item in payload["chats"] if isinstance(item, dict)]
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"无法读取会话记录: {exc}") from exc
    return {"ok": True, "path": str(path), "exists": path.exists(), "chats": chats, "count": len(chats)}


@app.post("/api/app/chats")
async def write_chat_transcripts(request: ChatTranscriptsRequest) -> dict[str, Any]:
    path = chat_transcripts_path()
    chats = request.chats[:CHAT_TRANSCRIPTS_MAX_CHATS]
    serialized = json.dumps({"version": 1, "chats": chats}, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > CHAT_TRANSCRIPTS_MAX_BYTES:
        raise HTTPException(status_code=413, detail="会话记录超过 16MB 上限，请删除旧会话后重试。")
    try:
        atomic_write_text(path, serialized)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法写入会话记录: {exc}") from exc
    return {"ok": True, "path": str(path), "count": len(chats)}


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
    await EVENT_BUS.broadcast("projects", project_snapshot_payload())
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


def should_skip_legacy_checkpoint(request: Any, *, skip_when_mock_execute: bool) -> bool:
    if bool(getattr(request, "dry_run", False)):
        return True
    if skip_when_mock_execute and bool(getattr(request, "mock_execute", False)):
        return True
    return False


def create_legacy_write_checkpoint(target_tool: str, request: Any) -> dict[str, Any]:
    arguments = request_model_to_dict(request)
    if not any(arguments.get(key) for key in ("project_root", "projectRoot", "project_path", "projectPath")):
        selected_project = str(getattr(DASHBOARD_STATE, "selected_project_path", "") or "").strip()
        if selected_project:
            arguments["project_path"] = selected_project

    approval = {
        "id": f"legacy_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
        "targetTool": target_tool,
    }
    try:
        checkpoint = AGENT_GATEWAY._create_pre_write_checkpoint(approval, arguments)  # noqa: SLF001 - legacy REST writes share the gateway checkpoint engine.
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=f"Could not create a pre-write checkpoint for this Unity write: {exc}") from exc
    if not checkpoint or not checkpoint.get("ok"):
        raise HTTPException(
            status_code=409,
            detail=str((checkpoint or {}).get("error") or "Could not create a pre-write checkpoint for this Unity write."),
        )
    return checkpoint


def run_legacy_write_with_checkpoint(
    target_tool: str,
    request: Any,
    callback: Callable[[], dict[str, Any]],
    *,
    skip_when_mock_execute: bool = False,
) -> dict[str, Any]:
    checkpoint: dict[str, Any] | None = None
    if not should_skip_legacy_checkpoint(request, skip_when_mock_execute=skip_when_mock_execute):
        checkpoint = create_legacy_write_checkpoint(target_tool, request)
    result = callback()
    if checkpoint and isinstance(result, dict):
        result.setdefault("checkpoint", checkpoint)
    return result


def skill_package_store_dir() -> Path:
    return AGENT_GATEWAY.user_constraints_path.parent / "skill-packages"


def skill_package_service() -> SkillPackageService:
    return SkillPackageService(skill_package_store_dir(), vrcforge_version=app.version)


def skill_package_error_response(exc: Exception) -> HTTPException:
    status = 400 if isinstance(exc, SkillPackageError) else 500
    return HTTPException(status_code=status, detail=str(exc))


def list_skill_packages_sync(_params: dict[str, Any] | None = None) -> dict[str, Any]:
    service = skill_package_service()
    return {
        "ok": True,
        "store": str(service.skill_store),
        "registry": service.load_registry(),
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


def _project_installed_skill(installed_path: Path, manifest: dict[str, Any]) -> dict[str, Any] | None:
    skill_file = installed_path / "SKILL.md"
    if not skill_file.is_file():
        return None
    target_name = str(manifest.get("skill_name") or manifest.get("skillName") or manifest.get("id") or "").strip()
    target_name = re.sub(r"[^a-z0-9_.-]+", "-", target_name.lower()).strip("-._")
    if not target_name:
        return None
    target_dir = AGENT_GATEWAY.user_skills_dir / target_name
    if target_dir.is_symlink():
        raise RuntimeError(f"Refusing to write through symlinked skill directory: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(skill_file, target_dir / "SKILL.md")
    return {"name": target_name, "path": str(target_dir / "SKILL.md")}


def import_skill_package_sync(params: dict[str, Any]) -> dict[str, Any]:
    service = skill_package_service()
    result = service.install(
        str(params.get("packagePath") or params.get("package_path") or ""),
        source=str(params.get("source") or "local-import"),
        allow_downgrade=bool(params.get("allowDowngrade") or params.get("allow_downgrade") or False),
        dev_mode=bool(params.get("devMode") or params.get("dev_mode") or False),
    )
    projection = None
    if params.get("projectToUserSkills", params.get("project_to_user_skills", True)) is not False:
        projection = _project_installed_skill(result.installed_path, result.preview.manifest)
    return {"ok": True, "imported": result.as_dict(), "projectedSkill": projection}


def _exportable_user_skill(skill_name: str) -> tuple[dict[str, Any], Path]:
    skill = AGENT_GATEWAY._find_user_skill(skill_name)  # noqa: SLF001 - package export is a host-level integration.
    if not skill:
        raise AgentGatewayError(f"User skill was not found: {skill_name}", status_code=404)
    storage_path = Path(str(skill.get("storagePath") or ""))
    if not storage_path.is_file():
        raise AgentGatewayError(f"User skill file was not found: {skill_name}", status_code=404)
    return skill, storage_path


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
            "min_vrcforge_version": app.version,
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


def _sub_agent_cancel_checkpoint(cancel_event: Any) -> None:
    if cancel_event.is_set():
        raise CancelledError("Sub-agent task was cancelled.")


def run_project_index_sub_agent(payload: dict[str, Any], cancel_event: Any) -> dict[str, Any]:
    _sub_agent_cancel_checkpoint(cancel_event)
    project_path = str(payload.get("projectPath") or "").strip()
    result = scan_project_index_sync({"projectPath": project_path, "maxFiles": payload.get("maxFiles") or 100000})
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    changed = bool(summary.get("changed"))
    scanner_families = result.get("summary", {}).get("scannerFamilies") if isinstance(result.get("summary"), dict) else []
    summary_text = (
        f"Project index {'changed' if changed else 'is clean'}: "
        f"+{summary.get('addedFiles', 0)} / ~{summary.get('modifiedFiles', 0)} / -{summary.get('deletedFiles', 0)}; "
        f"scanner families: {', '.join(scanner_families or []) or 'none'}."
    )
    _sub_agent_cancel_checkpoint(cancel_event)
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.project_index_review.v1",
        "role": "project_index_review",
        "readOnly": True,
        "summaryText": summary_text,
        "projectIndex": result,
        "proposedNextAction": "Run targeted scanners for the affected families before planning writes." if changed else "No project-index-triggered scanner rerun is needed.",
    }


def run_outfit_package_sub_agent(payload: dict[str, Any], cancel_event: Any) -> dict[str, Any]:
    _sub_agent_cancel_checkpoint(cancel_event)
    package_path = str(payload.get("packagePath") or payload.get("package_path") or "").strip()
    result = inspect_outfit_package_sync({"packagePath": package_path, "maxEntries": payload.get("maxEntries") or 5000})
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    summary_text = (
        "Outfit package inspected: "
        f"{summary.get('unityPackageCount', 0)} UnityPackage(s), "
        f"{summary.get('prefabCandidateCount', 0)} prefab candidate(s), "
        f"{summary.get('textureCount', 0)} texture(s)."
    )
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.outfit_package_inspection.v1",
        "role": "outfit_package_inspection",
        "readOnly": True,
        "summaryText": summary_text,
        "inspection": result,
        "proposedNextAction": "Create a supervised import plan if the package has a UnityPackage or prefab candidate.",
    }


def run_validation_sub_agent(payload: dict[str, Any], cancel_event: Any) -> dict[str, Any]:
    _sub_agent_cancel_checkpoint(cancel_event)
    result = build_validation_report_sync(
        {
            "avatarPath": payload.get("avatarPath") or payload.get("avatar_path") or "",
            "projectPath": payload.get("projectPath") or payload.get("project_path") or "",
            "includeQuest": payload.get("includeQuest", True),
            "maxErrors": payload.get("maxErrors") or 50,
        }
    )
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    severity_counts = summary.get("severityCounts") if isinstance(summary.get("severityCounts"), dict) else {}
    summary_text = (
        "Validation triage finished: "
        f"{severity_counts.get('Error', 0)} error(s), "
        f"{severity_counts.get('Warning', 0)} warning(s), "
        f"{severity_counts.get('Suggestion', 0)} suggestion(s)."
    )
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.validation_triage.v1",
        "role": "validation_triage",
        "readOnly": True,
        "summaryText": summary_text,
        "validation": result,
        "proposedNextAction": "Convert selected validation findings into separate supervised fix plans.",
    }


def run_package_install_sub_agent(payload: dict[str, Any], cancel_event: Any) -> dict[str, Any]:
    _sub_agent_cancel_checkpoint(cancel_event)
    result = diagnose_package_install_errors_sync(payload)
    symptoms = result.get("symptoms") if isinstance(result.get("symptoms"), list) else []
    titles = [str(item.get("title") or item.get("code") or "") for item in symptoms if isinstance(item, dict)]
    summary_text = f"Package install diagnosis found {len(symptoms)} symptom(s): {', '.join(titles[:4]) or 'none'}."
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.package_install_diagnosis.v1",
        "role": "package_install_diagnosis",
        "readOnly": True,
        "summaryText": summary_text,
        "diagnostics": result,
        "proposedNextAction": "Create a separate supervised repair plan for any selected symptom.",
    }


def run_outfit_import_plan_sub_agent(payload: dict[str, Any], cancel_event: Any) -> dict[str, Any]:
    _sub_agent_cancel_checkpoint(cancel_event)
    result = plan_outfit_import_sync(payload)
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    ready = bool(plan.get("readyToApply"))
    summary_text = (
        f"Outfit import plan {'ready' if ready else 'needs review'}: "
        f"kind={plan.get('kind') or 'unknown'}, writeTarget={plan.get('writeTarget') or 'none'}."
    )
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.outfit_import_plan_review.v1",
        "role": "outfit_import_plan_review",
        "readOnly": True,
        "planOnly": True,
        "summaryText": summary_text,
        "importPlan": result,
        "proposedNextAction": "Queue the normal VRCForge approval from the parent thread if the user accepts this plan." if ready else "Resolve package ambiguity before requesting a write.",
    }


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


def external_agent_status_sync(project_path: str | None = None) -> dict[str, Any]:
    config = AGENT_GATEWAY.ensure_config()
    health = safe_agent_health()
    manifest = safe_agent_manifest()
    selected_project_path = _selected_project_path_or(project_path)
    return {
        **connector_bundle_sync({}),
        "clients": connector_client_statuses(root_dir=ROOT_DIR, project_path=selected_project_path),
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
    if params.get("revokeToken") is True or params.get("revoke_token") is True:
        config.token = secrets.token_urlsafe(32)
        config.approval_token = secrets.token_urlsafe(32)
    AGENT_GATEWAY.save_config(config)
    return external_agent_status_sync()


def install_external_agent_connector_sync(params: dict[str, Any]) -> dict[str, Any]:
    client = str(params.get("client") or "").strip()
    project_path = _selected_project_path_or(params.get("projectPath") or params.get("project_path"))
    try:
        action = install_connector(client, root_dir=ROOT_DIR, project_path=project_path)
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
    return {**external_agent_status_sync(project_path), "lastConnectorAction": action}


def uninstall_external_agent_connector_sync(params: dict[str, Any]) -> dict[str, Any]:
    client = str(params.get("client") or "").strip()
    project_path = _selected_project_path_or(params.get("projectPath") or params.get("project_path"))
    try:
        action = uninstall_connector(client, project_path=project_path)
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
    return {**external_agent_status_sync(project_path), "lastConnectorAction": action}


@app.get("/api/app/external-agent/connectors")
def app_external_agent_connectors(projectPath: str | None = None, project_path: str | None = None) -> dict[str, Any]:
    return external_agent_status_sync(projectPath or project_path)


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


@app.post("/api/app/skill-packages/export")
def app_export_skill_package(request: SkillPackageExportRequest) -> dict[str, Any]:
    try:
        return export_skill_package_sync(request.model_dump(by_alias=True))
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


@app.post("/api/app/optimization/plan")
def app_optimization_plan(request: OptimizationPlanRequest) -> dict[str, Any]:
    return build_optimization_plan_sync(request.model_dump(by_alias=True))


@app.post("/api/app/optimization/tool")
def app_optimization_tool(request: OptimizationToolRequest) -> dict[str, Any]:
    params = request.model_dump(by_alias=True)
    tool_name = str(params.pop("tool", "") or "")
    return build_optimization_tool_sync(tool_name, params)


@app.post("/api/app/optimization/apply-request")
async def app_optimization_apply_request(request: OptimizationApplyRequest) -> dict[str, Any]:
    params = request.model_dump(by_alias=True)
    try:
        payload = request_optimization_apply_sync(params, agent_name="desktop-agent")
    except (AgentGatewayError, ValueError) as exc:
        status_code = exc.status_code if isinstance(exc, AgentGatewayError) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    if not payload.get("ok"):
        raise HTTPException(status_code=400, detail=payload)
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/app/project-index/scan")
def app_project_index_scan(request: ProjectIndexScanRequest) -> dict[str, Any]:
    return scan_project_index_sync(request.model_dump(by_alias=True))


@app.post("/api/app/outfit-packages/inspect")
def app_outfit_package_inspect(request: OutfitPackageInspectRequest) -> dict[str, Any]:
    return inspect_outfit_package_sync(request.model_dump(by_alias=True))


@app.post("/api/app/outfit-imports/plan")
def app_outfit_import_plan(request: OutfitImportPlanRequest) -> dict[str, Any]:
    return plan_outfit_import_sync(request.model_dump(by_alias=True))


@app.post("/api/app/outfit-imports/request")
async def app_request_outfit_import(request: OutfitImportPlanRequest) -> dict[str, Any]:
    params = request.model_dump(by_alias=True)
    preview = plan_outfit_import_sync(params)
    plan_payload = preview.get("plan") if isinstance(preview.get("plan"), dict) else {}
    if not preview.get("ok") or not plan_payload.get("readyToApply"):
        raise HTTPException(status_code=400, detail=preview.get("error") or "Outfit import plan is not ready to apply.")
    try:
        payload = AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": "vrcforge_import_outfit_package",
                "arguments": params,
                "reason": "Import outfit package through VRCForge supervised Golden Path.",
                "preview": preview,
                "agent_name": "desktop-agent",
            }
        )
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.post("/api/app/package-install/diagnose")
def app_package_install_diagnostics(request: PackageInstallDiagnosticsRequest) -> dict[str, Any]:
    return diagnose_package_install_errors_sync(request.model_dump(by_alias=True))


@app.post("/api/app/package-install/plan")
def app_package_install_plan(request: PackageInstallPlanRequest) -> dict[str, Any]:
    return package_install_plan_sync(request.model_dump(by_alias=True))


@app.post("/api/app/package-install/request")
async def app_package_install_request(request: PackageInstallPlanRequest) -> dict[str, Any]:
    payload = request_package_install_sync(request.model_dump(by_alias=True), agent_name="desktop-agent")
    if not payload.get("ok"):
        raise HTTPException(status_code=400, detail=payload)
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


@app.get("/api/app/sub-agents")
def app_list_sub_agents(includeEvents: bool = False, limit: int = 50) -> dict[str, Any]:
    return SUB_AGENT_REGISTRY.list_tasks(include_events=includeEvents, limit=limit)


@app.post("/api/app/sub-agents")
async def app_create_sub_agent(request: SubAgentCreateRequest) -> dict[str, Any]:
    try:
        payload = SUB_AGENT_REGISTRY.create_task(
            role=request.role,
            task=request.task,
            display_name=request.display_name,
            parent_session_id=request.parent_session_id,
            project_path=request.project_path,
            params=request.params,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await EVENT_BUS.broadcast("subAgentTasks", SUB_AGENT_REGISTRY.list_tasks())
    return payload


@app.get("/api/app/sub-agents/{task_id}")
def app_get_sub_agent(task_id: str) -> dict[str, Any]:
    payload = SUB_AGENT_REGISTRY.get_task(task_id, include_events=True)
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Sub-agent task was not found.")
    return payload


@app.post("/api/app/sub-agents/{task_id}/cancel")
async def app_cancel_sub_agent(task_id: str) -> dict[str, Any]:
    payload = SUB_AGENT_REGISTRY.cancel_task(task_id)
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Sub-agent task was not found.")
    await EVENT_BUS.broadcast("subAgentTasks", SUB_AGENT_REGISTRY.list_tasks())
    return payload


@app.post("/api/app/sub-agents/{task_id}/retry")
async def app_retry_sub_agent(task_id: str) -> dict[str, Any]:
    payload = SUB_AGENT_REGISTRY.retry_task(task_id)
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Sub-agent task was not found.")
    await EVENT_BUS.broadcast("subAgentTasks", SUB_AGENT_REGISTRY.list_tasks())
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
        payload = copy.deepcopy(read_health())
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


def safe_approval_list() -> list[dict[str, Any]]:
    try:
        return AGENT_GATEWAY.list_approvals(include_expired=False)
    except Exception:  # noqa: BLE001
        return []


def serialize_app_api_config() -> dict[str, Any]:
    config = serialize_api_config(include_secret=False)
    config.pop("api_key", None)
    return config


def load_diagnostics_config() -> dict[str, Any]:
    try:
        payload = json.loads(DIAGNOSTICS_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    return {"debugLogging": bool(payload.get("debugLogging", payload.get("debug_logging", False)))}


def save_diagnostics_config(payload: dict[str, Any]) -> dict[str, Any]:
    state = {"debugLogging": bool(payload.get("debugLogging", payload.get("debug_logging", False)))}
    DIAGNOSTICS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIAGNOSTICS_CONFIG_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def diagnostics_state() -> dict[str, Any]:
    config = load_diagnostics_config()
    return {
        "ok": True,
        "schema": "vrcforge.diagnostics.v1",
        "debugLogging": bool(config.get("debugLogging")),
        "configPath": str(DIAGNOSTICS_CONFIG_PATH),
        "logsDir": str(LOG_DIR),
        "dashboardLogPath": str(LOCAL_LOG_PATH),
        "interactionLogPath": str(INTERACTION_LOG_PATH),
        "supportBundleDir": str(SUPPORT_BUNDLE_DIR),
        "logRetentionHours": int(LOG_RETENTION.total_seconds() // 3600),
    }


def debug_logging_enabled() -> bool:
    return bool(load_diagnostics_config().get("debugLogging"))


def summarize_debug_payload(value: Any) -> Any:
    value = redact_sensitive(value)
    if isinstance(value, dict):
        return {str(key): summarize_debug_payload(item) for key, item in list(value.items())[:40]}
    if isinstance(value, list):
        return {"type": "list", "count": len(value), "items": [summarize_debug_payload(item) for item in value[:5]]}
    if isinstance(value, str):
        if _looks_like_local_path(value):
            return _redact_local_path(value)
        return value[:500] + ("..." if len(value) > 500 else "")
    return value


def record_debug_interaction(entry: dict[str, Any]) -> None:
    if not debug_logging_enabled():
        return
    safe_entry = summarize_debug_payload({"timestamp": utc_now_iso(), **entry})
    with LOCAL_LOG_LOCK:
        INTERACTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with INTERACTION_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(safe_entry, ensure_ascii=False, sort_keys=True) + "\n")
        prune_jsonl_log_file(INTERACTION_LOG_PATH)


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
    redacted = redact_sensitive(value)
    if include_full_paths:
        return redacted
    return _redact_doctor_detail(redacted)


def write_support_bundle_member(bundle: zipfile.ZipFile, name: str, payload: Any, include_full_paths: bool = False) -> None:
    redacted = redact_support_payload(payload, include_full_paths=include_full_paths)
    bundle.writestr(name, json.dumps(redacted, ensure_ascii=False, indent=2, sort_keys=True))


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
        "debugLogging": debug_logging_enabled(),
        "includeFullPaths": bool(request.include_full_paths),
        "paths": {
            "programDir": str(ROOT_DIR),
            "userDataDir": str(USER_DATA_DIR),
            "configDir": str(CONFIG_DIR),
            "logsDir": str(LOG_DIR),
            "artifactsDir": str(ARTIFACTS_DIR),
        },
        "privacy": {
            "redactsSecrets": True,
            "includesScreenshots": False,
            "includesPaidAssetContents": False,
            "includesFullPaths": bool(request.include_full_paths),
        },
    }
    try:
        bootstrap = read_agentic_app_bootstrap()
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
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        write_support_bundle_member(bundle, "metadata.json", metadata, request.include_full_paths)
        write_support_bundle_member(bundle, "bootstrap.json", bootstrap, request.include_full_paths)
        write_support_bundle_member(bundle, "doctor.json", doctor, request.include_full_paths)
        write_support_bundle_member(bundle, "diagnostics.json", diagnostics_state(), request.include_full_paths)
        write_support_bundle_member(bundle, "dashboard-log.json", read_jsonl_tail(LOCAL_LOG_PATH, log_limit), request.include_full_paths)
        write_support_bundle_member(bundle, "interaction-log.json", read_jsonl_tail(INTERACTION_LOG_PATH, log_limit), request.include_full_paths)
        write_support_bundle_member(bundle, "agent-audit.json", AGENT_GATEWAY.recent_audit_logs(limit=log_limit), request.include_full_paths)
        write_support_bundle_member(bundle, "sub-agent-events.json", SUB_AGENT_REGISTRY.recent_events(limit=log_limit), request.include_full_paths)
        write_support_bundle_member(bundle, "sub-agent-tasks.json", SUB_AGENT_REGISTRY.list_tasks(include_events=False, limit=log_limit), request.include_full_paths)
        write_support_bundle_member(bundle, "checkpoints.json", checkpoints, request.include_full_paths)
        write_support_bundle_member(bundle, "backend-stdout-tail.json", read_text_tail(LOG_DIR / "backend_stdout.log", log_limit), request.include_full_paths)
        write_support_bundle_member(bundle, "backend-stderr-tail.json", read_text_tail(LOG_DIR / "backend_stderr.log", log_limit), request.include_full_paths)
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
        "redacted": not bool(request.include_full_paths),
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
    if check_id.startswith("desktop.") or check_id.startswith("backend.") or check_id.startswith("doctor."):
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
    return "Doctor"


def _doctor_fix_command_for_id(check_id: str) -> str:
    commands = {
        "unity.project_root": "Open Project Picker and select the Unity project root used by the bridge.",
        "unity.plugin": "Run Unity plugin install/repair for the selected project.",
        "unity.mcp.package": "Repair VRCForge Unity plugin install, or add the Unity MCP package through VCC/vrc-get/ALCOM.",
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


def _redact_doctor_detail(value: Any, key_hint: str = "") -> Any:
    key = key_hint.lower()
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for item_key, item_value in value.items():
            lower_key = str(item_key).lower()
            if any(marker in lower_key for marker in ("path", "directory", "folder", "root")) and "url" not in lower_key:
                redacted[str(item_key)] = _redact_local_path(item_value)
            else:
                redacted[str(item_key)] = _redact_doctor_detail(item_value, lower_key)
        return redacted
    if isinstance(value, list):
        return [_redact_doctor_detail(item, key_hint) for item in value]
    if isinstance(value, str) and (_looks_like_local_path(value) or any(marker in key for marker in ("path", "directory", "folder", "root"))):
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
) -> dict[str, Any]:
    if status not in {"ok", "warning", "error", "unknown"}:
        status = "unknown"
    return {
        "id": check_id,
        "section": _doctor_section_for_id(check_id),
        "title": title,
        "status": status,
        "message": message,
        "whatFailed": "" if status == "ok" else message,
        "whyItMatters": why_it_matters,
        "howToFix": how_to_fix,
        "fixCommand": _doctor_fix_command_for_id(check_id),
        "fixable": fixable,
        "actions": actions or ["retry", "open_logs", "copy_diagnostic_summary"],
        "detail": _redact_doctor_detail(detail),
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
    order = ["Runtime", "Unity environment", "SDK / dependencies", "Providers", "External agents", "Skills", "Rollback", "Doctor"]
    names = [name for name in order if name in sections] + sorted(name for name in sections if name not in order)
    return [
        {
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


def build_app_doctor_report() -> dict[str, Any]:
    health = build_agentic_app_health()
    components = health.get("components") if isinstance(health.get("components"), dict) else {}
    api_config = serialize_app_api_config()
    agent_health = safe_agent_health()
    agent_manifest = safe_agent_manifest()
    permission = safe_permission_state()
    selected_project_value = _selected_project_path_from_health(health)
    selected_project = Path(selected_project_value) if selected_project_value else None

    checks: list[dict[str, Any]] = [
        _doctor_check(
            "desktop.runtime",
            "Desktop runtime connection",
            "ok",
            "Desktop can reach the local VRCForge runtime.",
            "The desktop UI needs the loopback runtime for chat, tools, approvals, checkpoints, and diagnostics.",
            "Restart VRCForge or use Retry if this check ever disappears.",
            {"endpoint": "http://127.0.0.1:8757"},
        ),
        _doctor_check_from_component(
            "backend.online",
            "Backend online",
            components.get("backend"),
            "All avatar workflows depend on the local FastAPI runtime.",
            "Restart the backend from the desktop app; if it still fails, open logs and export a support bundle.",
        ),
        _doctor_check_from_component(
            "unity.project_root",
            "Unity environment root",
            components.get("selectedUnityProject"),
            "Unity bridge, plugin, and SDK dependency-version checks need the configured Unity root; Doctor does not inspect avatar assets or scene content.",
            "Select the Unity root folder used by the editor bridge. Project content checks happen later as normal agent tasks.",
        ),
        _doctor_check_from_component(
            "unity.plugin",
            "VRCForge Unity plugin",
            components.get("unityPluginInstalled"),
            "The editor plugin provides the predefined Unity tools used for scans, previews, writes, and rollback validation.",
            "Install or repair the VRCForge Unity plugin for the selected project.",
        ),
        _doctor_check_from_component(
            "unity.mcp.package",
            "Unity MCP package",
            components.get("mcpPackageConfigured"),
            "VRCForge reaches Unity through the MCP bridge, so the project manifest must include the Unity MCP package.",
            "Repair the plugin install or add the Unity MCP package through VCC/vrc-get/ALCOM.",
        ),
        _doctor_check_from_component(
            "unity.mcp.bridge",
            "Unity MCP bridge",
            components.get("unityMcpBridgeReachable"),
            "Live scans and writes require the Unity editor bridge to be reachable.",
            "Open the selected Unity project, confirm the MCP server is running, then Retry.",
            actions=["repair_unity_bridge", "retry", "open_logs", "copy_diagnostic_summary"],
            fixable=True,
        ),
        _doctor_check_from_component(
            "unity.mcp.instance",
            "Unity instance registration",
            components.get("unityMcpInstance"),
            "The runtime must target the correct Unity editor instance before tool calls are reliable.",
            "Focus the Unity project, check MCP instance selection, or restart the bridge.",
            actions=["repair_unity_bridge", "retry", "open_logs", "copy_diagnostic_summary"],
            fixable=True,
        ),
        _doctor_check_from_component(
            "unity.tools",
            "VRCForge Unity tools",
            components.get("vrcForgeUnityTools"),
            "VRCForge uses predefined Unity tools for live editor access; Doctor only checks that the tool surface is registered.",
            "Repair the VRCForge plugin and wait for Unity compile to finish.",
        ),
        _package_doctor_check(
            "package.vrchat_sdk",
            "VRChat SDK",
            selected_project,
            ["com.vrchat.avatars", "com.vrchat.base"],
            "Avatar validation, expression menus, parameters, and VRChat build checks need the SDK packages.",
            "Install the VRChat Avatar SDK through VCC, ALCOM, or vrc-get.",
        ),
        _package_doctor_check(
            "package.modular_avatar",
            "Modular Avatar",
            selected_project,
            ["nadena.dev.modular-avatar"],
            "Outfit and menu workflows prefer Modular Avatar because it keeps edits non-destructive.",
            "Install Modular Avatar if the avatar/outfit workflow needs MA components.",
            optional=True,
        ),
        _package_doctor_check(
            "package.vrcfury",
            "VRCFury",
            selected_project,
            ["com.vrcfury.vrcfury"],
            "VRCFury components can affect generated controllers and conflict analysis.",
            "Install VRCFury only when the avatar uses it; otherwise this warning is informational.",
            optional=True,
        ),
        _doctor_check_from_component(
            "provider.configured",
            "Provider configured",
            components.get("providerConfigPresent"),
            "Model planning needs a configured cloud, local, or fallback provider; manual tools still work without one.",
            "Set a BYOK provider, choose Ollama/local, or continue in manual/read-only mode.",
        ),
    ]

    provider = str(api_config.get("provider") or "")
    provider_requires_key = bool(api_config.get("apiKeyRequired"))
    provider_has_key = bool(api_config.get("apiKeyPresent"))
    provider_status = "warning" if provider_requires_key and not provider_has_key else "unknown"
    if provider == "ollama":
        provider_status = "unknown"
    checks.append(
        _doctor_check(
            "provider.test",
            "Provider test call",
            provider_status,
            "Provider test has not been run automatically.",
            "Automatic first-run diagnostics must not spend API credits or send project data without an explicit action.",
            "Use Settings > Providers to run text, vision, or structured-output tests when needed.",
            {"provider": provider, "model": api_config.get("model"), "apiKeyPresent": provider_has_key},
            ["retry", "open_settings", "copy_diagnostic_summary"],
        )
    )

    checks.append(
        _doctor_check(
            "provider.local_ollama",
            "Ollama local provider",
            "unknown" if provider == "ollama" else "ok",
            "Ollama reachability is checked only when explicitly testing the provider."
            if provider == "ollama"
            else "Ollama is not the selected provider.",
            "Local fallback keeps the app usable when cloud providers are unavailable or privacy mode is required.",
            "Select Ollama in provider settings and run a provider test when using local/offline mode.",
            {"provider": provider, "baseUrl": api_config.get("base_url")},
            ["retry", "open_settings", "copy_diagnostic_summary"],
        )
    )

    gateway_enabled = bool(agent_health.get("enabled"))
    checks.append(
        _doctor_check(
            "agent.gateway",
            "External Agent Gateway",
            "ok" if gateway_enabled else "warning",
            "Agent Gateway is enabled." if gateway_enabled else "Agent Gateway is disabled.",
            "External Codex, Claude Code, and MCP clients can only use VRCForge through this supervised bridge.",
            "Enable the gateway only when connecting an external agent; keep it disabled otherwise.",
            {
                "enabled": gateway_enabled,
                "requiresToken": agent_health.get("requiresToken"),
                "mcpUrl": agent_health.get("mcpUrl"),
                "pendingApprovalCount": agent_health.get("pendingApprovalCount"),
                "allowWriteRequests": agent_health.get("allowWriteRequests"),
            },
            ["retry", "open_settings", "copy_diagnostic_summary"],
        )
    )

    try:
        skill_check = AGENT_GATEWAY.check_skill_registry()
        skill_status = _status_from_counts(
            int(skill_check.get("errorCount") or 0),
            int(skill_check.get("warningCount") or 0),
        )
        checks.append(
            _doctor_check(
                "skills.registry",
                "Skill registry",
                skill_status,
                "Skill registry is healthy." if skill_status == "ok" else "Skill registry has warnings or errors.",
                "Slash commands, community skills, and external-agent skill lists all depend on registry health.",
                "Open Skill Manager, inspect broken skills, disable unsafe packages, or repair manifests.",
                {
                    "schema": skill_check.get("schema"),
                    "count": skill_check.get("count"),
                    "errorCount": skill_check.get("errorCount"),
                    "warningCount": skill_check.get("warningCount"),
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _doctor_check(
                "skills.registry",
                "Skill registry",
                "error",
                f"Skill registry check failed: {exc}",
                "Broken skill registry state can hide capabilities or break startup surfaces.",
                "Open logs, remove the broken skill package, or restart with user skills disabled.",
                {"error": str(exc)},
            )
        )

    try:
        checkpoint_payload = AGENT_GATEWAY.list_checkpoints({"projectRoot": selected_project_value, "limit": 1})
        checks.append(
            _doctor_check(
                "checkpoint.backend",
                "Checkpoint backend",
                "ok" if checkpoint_payload.get("ok") else "warning",
                "Checkpoint timeline is readable." if checkpoint_payload.get("ok") else "Checkpoint timeline could not be read.",
                "Every real write must create a pre-write checkpoint so restore can prove rollback.",
                "Check logs and the checkpoint storage path before approving any write.",
                {
                    "checkpointLogPath": str(AGENT_GATEWAY.checkpoint_log_path),
                    "checkpointStoreDir": str(AGENT_GATEWAY.checkpoint_store_dir),
                    "count": checkpoint_payload.get("count"),
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _doctor_check(
                "checkpoint.backend",
                "Checkpoint backend",
                "error",
                f"Checkpoint backend failed: {exc}",
                "Writes must be blocked when VRCForge cannot create or read rollback checkpoints.",
                "Open logs and repair checkpoint storage before approving writes.",
                {"error": str(exc)},
            )
        )

    try:
        package_manager = package_manager_status_sync({"projectPath": selected_project_value})
        preferred_cli = package_manager.get("preferredCli")
        checks.append(
            _doctor_check(
                "package.manager",
                "vrc-get / ALCOM / VPM",
                "ok" if preferred_cli else "warning",
                f"Preferred package CLI detected: {preferred_cli.get('name')}."
                if isinstance(preferred_cli, dict)
                else "No vrc-get or VCC vpm CLI was detected.",
                "Dependency diagnostics and repair flows are clearer when VPM tooling is installed.",
                "Install vrc-get or use VCC/ALCOM UI for dependency changes.",
                {
                    "managers": package_manager.get("managers"),
                    "preferredCli": preferred_cli,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            _doctor_check(
                "package.manager",
                "vrc-get / ALCOM / VPM",
                "warning",
                f"Package manager diagnostics failed: {exc}",
                "Dependency diagnostics help explain missing MA/VRCFury/VRC SDK packages.",
                "Open logs or verify vrc-get/VCC/ALCOM manually.",
                {"error": str(exc)},
            )
        )

    external_writes_blocked = not bool(permission.get("allowWriteRequests", True))
    if external_writes_blocked:
        checks.append(
            _doctor_check(
                "external.security_contract",
                "External agent write contract",
                "warning",
                "External write requests are disabled by permission state.",
                "External agents should request writes; VRCForge must own approval, checkpoint, apply, validation, and restore.",
                "Enable write requests only when a trusted local agent needs supervised writes.",
                {"permission": permission},
            )
        )
    else:
        checks.append(
            _doctor_check(
                "external.security_contract",
                "External agent write contract",
                "ok",
                "External agents can request supervised writes; direct approval still belongs to VRCForge.",
                "This prevents Codex, Claude Code, and other MCP clients from bypassing approval/checkpoint policy.",
                "Keep gateway tokens private and revoke the gateway when external work is finished.",
                {"permission": permission, "writeTargets": len(agent_manifest.get("writeTargets") or [])},
            )
        )

    summary = _doctor_summary(checks)
    return {
        "ok": summary["errorCount"] == 0,
        "schema": "vrcforge.doctor.v1",
        "scope": "vrcforge.environment.v1",
        "projectContentInspected": False,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "version": app.version,
        "summary": summary,
        "sections": _doctor_sections(checks),
        "selectedUnityEnvironment": {
            "configured": bool(selected_project_value),
            "label": _redact_local_path(selected_project_value) if selected_project_value else "",
        },
        "checks": checks,
    }


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


@app.get("/api/app/diagnostics")
def read_app_diagnostics() -> dict[str, Any]:
    return diagnostics_state()


@app.post("/api/app/diagnostics")
async def update_app_diagnostics(request: DiagnosticsConfigRequest) -> dict[str, Any]:
    save_diagnostics_config(request.model_dump(by_alias=True))
    payload = diagnostics_state()
    await emit_log_async(
        "success",
        "diagnostics",
        "Diagnostics settings updated.",
        {"debugLogging": payload["debugLogging"], "interactionLogPath": payload["interactionLogPath"]},
    )
    return payload


@app.post("/api/app/support-bundle")
def create_app_support_bundle(request: SupportBundleRequest) -> dict[str, Any]:
    return build_support_bundle(request)


@app.get("/api/app/tools/registry")
def read_app_tool_registry() -> dict[str, Any]:
    return AGENT_GATEWAY.build_tool_registry()


@app.get("/api/agent/manifest")
def read_agent_manifest(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.build_manifest()


@app.get("/api/agent/tools/registry")
def read_agent_tool_registry(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.build_tool_registry()


@app.get("/api/agent/health")
def read_agent_health(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.build_health()


@app.get("/api/agent/skills")
def read_agent_skills(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.build_skill_registry()


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
        "manifest": AGENT_GATEWAY.build_manifest(),
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
def call_agent_tool(tool_name: str, request: Request, tool_request: AgentToolRequest) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=False)
    try:
        return AGENT_GATEWAY.call_tool(tool_name, tool_request.params, agent_name=tool_request.agent_name)
    except AgentGatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


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
    supplied = extract_bearer_token_from_values(websocket.headers, websocket.query_params)
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
    return project_snapshot_payload()


@app.post("/api/projects/refresh")
async def refresh_projects() -> dict[str, Any]:
    payload = await asyncio.to_thread(project_snapshot_payload)
    await EVENT_BUS.broadcast("projects", payload)
    await emit_log_async("info", "project", "Project list refreshed.", {"count": len(payload["projects"])})
    return payload


@app.post("/api/state")
async def update_state(request: DashboardStateRequest) -> dict[str, Any]:
    if request.project_path is not None:
        DASHBOARD_STATE.selected_project_path = normalize_path_string(request.project_path)
        if request.unity_instance is None or not request.unity_instance.strip():
            DASHBOARD_STATE.unity_instance = Path(DASHBOARD_STATE.selected_project_path).name

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
        "apiConfig": serialize_api_config(include_secret=True),
        "effective": build_effective_model_summary(),
    }


@app.post("/api/config")
async def update_api_config(request: ApiConfigRequest) -> dict[str, Any]:
    global DASHBOARD_API_CONFIG

    config = normalize_api_config_request(request)
    if not config.api_key.strip():
        saved = DASHBOARD_API_CONFIG or load_initial_dashboard_api_config()
        if saved and saved.provider == config.provider and saved.api_key.strip():
            config = DashboardApiConfig(
                provider=config.provider,
                api_key=saved.api_key,
                base_url=config.base_url,
                model=config.model,
            )
    DASHBOARD_API_CONFIG = config
    save_dashboard_api_config(DASHBOARD_API_CONFIG)
    payload = {
        "configPath": str(CONFIG_PATH),
        "apiConfig": serialize_api_config(include_secret=True),
        "effective": build_effective_model_summary(),
    }
    await EVENT_BUS.broadcast("config", payload)
    await emit_log_async(
        "success",
        "config",
        "Dashboard API config saved and applied.",
        {
            "provider": DASHBOARD_API_CONFIG.provider,
            "model": DASHBOARD_API_CONFIG.model,
            "baseUrl": DASHBOARD_API_CONFIG.base_url or "(official endpoint)",
        },
    )
    return payload


@app.post("/api/models")
async def read_api_models(request: ApiModelListRequest) -> dict[str, Any]:
    config = normalize_api_config_request(request)
    if not config.api_key.strip():
        saved = DASHBOARD_API_CONFIG or load_initial_dashboard_api_config()
        if saved and saved.provider == config.provider and saved.api_key.strip():
            config = DashboardApiConfig(
                provider=config.provider,
                api_key=saved.api_key,
                base_url=config.base_url,
                model=config.model,
            )
    provider_label = provider_display_name(config.provider)

    try:
        models = await asyncio.to_thread(fetch_provider_models, config)
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
        "models": models,
        "modelCount": len(models),
        "selectedModel": config.model,
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
    if not request.api_key.strip():
        saved = DASHBOARD_API_CONFIG or load_initial_dashboard_api_config()
        if saved and saved.provider == normalize_provider_name(request.provider) and saved.api_key.strip():
            request = ProviderTestRequest(
                provider=request.provider,
                api_key=saved.api_key,
                base_url=request.base_url,
                model=request.model,
                capability=request.capability,
            )
    return await asyncio.to_thread(run_provider_test_sync, request)


@app.post("/api/projects/install")
async def install_project(request: ProjectInstallRequest) -> dict[str, Any]:
    project_path = resolve_target_project(request.project_path)
    command = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSTALL_SCRIPT_PATH),
        "-ProjectPath",
        project_path,
    ]

    if request.launch_unity and DASHBOARD_STATE.unity_editor_path:
        command.extend(["-UnityEditorPath", DASHBOARD_STATE.unity_editor_path, "-LaunchUnity"])

    await emit_log_async("info", "project", "Installing VRCForge into Unity project.", {"projectPath": project_path})
    completed = await asyncio.to_thread(
        subprocess.run,
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=str(ROOT_DIR),
    )

    output = (completed.stdout or "").strip()
    error = (completed.stderr or "").strip()
    if completed.returncode != 0:
        await emit_log_async("error", "project", "Project installation failed.", {"projectPath": project_path, "error": error or output})
        raise HTTPException(status_code=500, detail=error or output or f"Installer exited with code {completed.returncode}")

    payload = {
        "ok": True,
        "projectPath": project_path,
        "output": output,
    }
    await EVENT_BUS.broadcast("projects", await asyncio.to_thread(project_snapshot_payload))
    await emit_log_async("success", "project", "VRCForge installed into Unity project.", {"projectPath": project_path})
    return payload


@app.post("/api/projects/open")
async def open_project(request: ProjectActionRequest) -> dict[str, Any]:
    project_path = resolve_target_project(request.project_path)
    editor_path = DASHBOARD_STATE.unity_editor_path
    if not editor_path or not Path(editor_path).exists():
        raise HTTPException(
            status_code=400,
            detail="Unity editor path is empty or does not exist. Update dashboard settings before opening a project.",
        )

    subprocess.Popen([editor_path, "-projectPath", project_path], cwd=str(ROOT_DIR))
    DASHBOARD_STATE.selected_project_path = project_path
    DASHBOARD_STATE.unity_instance = Path(project_path).name
    payload = serialize_dashboard_state()
    await EVENT_BUS.broadcast("state", payload)
    await emit_log_async("info", "project", "Opened Unity project.", {"projectPath": project_path, "unityEditorPath": editor_path})
    return {"ok": True, "projectPath": project_path, "unityEditorPath": editor_path}


@app.post("/api/unity/status")
async def read_unity_status(request: ConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(build_unity_status_snapshot, load_dashboard_settings(request))


@app.post("/api/unity/instances")
async def read_unity_instances(request: ConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(build_unity_instances_diagnostics, load_dashboard_settings(request))


@app.post("/api/unity/tools")
async def read_unity_tools(request: ConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(build_unity_tools_diagnostics, load_dashboard_settings(request))


@app.post("/api/scene/avatars")
async def read_scene_avatars(request: AvatarSceneScanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(scan_scene_avatars_sync, request)


@app.post("/api/avatars")
async def read_avatars(request: DashboardRequest) -> dict[str, Any]:
    return await asyncio.to_thread(read_avatars_sync, request)


@app.post("/api/avatar/blendshapes")
async def read_avatar_blendshapes(request: AvatarBlendshapeListRequest) -> dict[str, Any]:
    return await asyncio.to_thread(read_avatar_blendshapes_sync, request)


@app.post("/api/pipeline/plan")
async def build_pipeline_plan(request: DashboardRequest) -> dict[str, Any]:
    return await asyncio.to_thread(run_dashboard_pipeline_sync, request, False)


@app.post("/api/pipeline/run")
async def run_pipeline(request: DashboardRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_run_face_tuning",
        request,
        lambda: run_dashboard_pipeline_sync(request, True),
        skip_when_mock_execute=True,
    )


@app.post("/api/blendshapes/apply")
async def apply_manual_blendshapes(request: ManualBlendshapeApplyRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_apply_blendshapes",
        request,
        lambda: apply_manual_blendshapes_sync(request),
        skip_when_mock_execute=True,
    )


@app.post("/api/blendshapes/undo")
async def undo_manual_blendshapes(request: UndoBlendshapeRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_undo_blendshapes",
        request,
        lambda: undo_manual_blendshapes_sync(request),
    )


@app.get("/api/tuning/history")
def read_tuning_history(avatar_path: str | None = None) -> dict[str, Any]:
    store = load_tuning_history_store()
    records = list(store.get("records") or [])
    if avatar_path:
        records = [
            record for record in records
            if record.get("avatar_path") == avatar_path or record.get("avatar_name") == avatar_path
        ]
    return {"ok": True, "records": records, "count": len(records)}


@app.post("/api/tuning/history/{history_id}/reapply")
async def reapply_tuning_history(history_id: str, request: DashboardRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_run_face_tuning",
        request,
        lambda: apply_saved_tuning_history_sync(history_id, request),
        skip_when_mock_execute=True,
    )


@app.get("/api/tuning/presets")
def read_tuning_presets(avatar_path: str | None = None) -> dict[str, Any]:
    store = load_tuning_preset_store()
    presets = list(store.get("presets") or [])
    if avatar_path:
        presets = [
            preset for preset in presets
            if preset.get("avatar_path") == avatar_path or preset.get("avatar_name") == avatar_path
        ]
    return {"ok": True, "presets": presets, "count": len(presets)}


@app.post("/api/tuning/presets")
async def create_tuning_preset(request: TuningPresetCreateRequest) -> dict[str, Any]:
    return await asyncio.to_thread(create_tuning_preset_sync, request)


@app.post("/api/tuning/presets/{preset_id}/apply")
async def apply_tuning_preset(preset_id: str, request: DashboardRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_run_face_tuning",
        request,
        lambda: apply_saved_tuning_preset_sync(preset_id, request),
        skip_when_mock_execute=True,
    )


@app.post("/api/tuning/presets/{preset_id}/rename")
async def rename_tuning_preset(preset_id: str, request: TuningPresetRenameRequest) -> dict[str, Any]:
    return await asyncio.to_thread(rename_tuning_preset_sync, preset_id, request)


@app.post("/api/tuning/presets/{preset_id}/duplicate")
async def duplicate_tuning_preset(preset_id: str, request: TuningPresetDuplicateRequest) -> dict[str, Any]:
    return await asyncio.to_thread(duplicate_tuning_preset_sync, preset_id, request)


@app.post("/api/tuning/presets/{preset_id}/delete")
async def delete_tuning_preset(preset_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(delete_tuning_preset_sync, preset_id)


@app.get("/api/tuning/locks")
def read_tuning_locks(avatar_path: str | None = None) -> dict[str, Any]:
    resolved_avatar = avatar_path or DASHBOARD_RUNTIME.current_avatar_path
    locked = load_locked_blendshapes(resolved_avatar)
    return {"ok": True, "avatarPath": resolved_avatar, "lockedBlendshapes": locked, "count": len(locked)}


@app.post("/api/tuning/locks")
async def update_tuning_locks(request: TuningLocksUpdateRequest) -> dict[str, Any]:
    return await asyncio.to_thread(update_tuning_locks_sync, request)


@app.post("/api/tuning/locks/ai-select")
async def ai_select_tuning_locks(request: TuningLocksAiSelectRequest) -> dict[str, Any]:
    return await asyncio.to_thread(ai_select_tuning_locks_sync, request)


@app.post("/api/clothes/scan")
async def scan_clothes(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(scan_clothes_sync, request)


@app.post("/api/clothes/toggle")
async def toggle_clothing(request: ClothingToggleRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_toggle_clothing",
        request,
        lambda: toggle_clothing_sync(request),
    )


@app.post("/api/clothes/generate-fx")
async def generate_clothing_fx(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(generate_clothing_fx_sync, request)


@app.post("/api/clothes/apply-fx")
async def apply_clothing_fx(request: ClothingApplyFxRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_apply_clothing_fx",
        request,
        lambda: apply_clothing_fx_sync(request),
    )


@app.post("/api/parameters/scan")
async def scan_avatar_parameters(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(scan_avatar_parameters_sync, request)


@app.post("/api/parameters/optimize")
async def optimize_avatar_parameters(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(optimize_avatar_parameters_sync, request)


@app.post("/api/parameters/apply-optimization")
async def apply_parameter_optimization(request: ParameterApplyOptimizationRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_apply_parameter_optimization",
        request,
        lambda: apply_parameter_optimization_sync(request),
    )


@app.post("/api/parameters/rollback")
async def rollback_parameter_optimization(request: ParameterRollbackRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_rollback_parameters",
        request,
        lambda: rollback_parameter_optimization_sync(request),
    )


@app.post("/api/shader/materials/scan")
async def scan_shader_materials(request: ShaderMaterialScanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(scan_shader_materials_sync, request)


@app.post("/api/shader/plan")
async def generate_shader_material_plan(request: ShaderMaterialPlanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(generate_shader_material_plan_sync, request)


@app.post("/api/shader/apply")
async def apply_shader_material_plan(request: ShaderMaterialApplyRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_apply_shader_tuning",
        request,
        lambda: apply_shader_material_plan_sync(request),
    )


@app.post("/api/shader/restore")
async def restore_shader_material_plan(request: ShaderMaterialRestoreRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_restore_shader_tuning",
        request,
        lambda: restore_shader_material_plan_sync(request),
    )


@app.get("/api/shader/history")
def read_shader_tuning_history(avatar_path: str | None = None) -> dict[str, Any]:
    store = load_shader_tuning_history_store()
    records = list(store.get("records") or [])
    if avatar_path:
        records = [
            record for record in records
            if record.get("avatar_path") == avatar_path or record.get("avatar_name") == avatar_path
        ]
    return {"ok": True, "records": records, "count": len(records)}


@app.post("/api/shader/history/{history_id}/reapply")
async def reapply_shader_tuning_history(history_id: str, request: ShaderMaterialPlanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_apply_shader_tuning",
        request,
        lambda: apply_saved_shader_history_sync(history_id, request),
    )


@app.get("/api/shader/presets")
def read_shader_tuning_presets(avatar_path: str | None = None) -> dict[str, Any]:
    store = load_shader_tuning_preset_store()
    presets = list(store.get("presets") or [])
    if avatar_path:
        presets = [
            preset for preset in presets
            if preset.get("avatar_path") == avatar_path or preset.get("avatar_name") == avatar_path
        ]
    return {"ok": True, "presets": presets, "count": len(presets)}


@app.post("/api/shader/presets")
async def create_shader_tuning_preset(request: ShaderTuningPresetCreateRequest) -> dict[str, Any]:
    return await asyncio.to_thread(create_shader_tuning_preset_sync, request)


@app.post("/api/shader/presets/{preset_id}/apply")
async def apply_shader_tuning_preset(preset_id: str, request: ShaderMaterialPlanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        run_legacy_write_with_checkpoint,
        "vrcforge_apply_shader_tuning",
        request,
        lambda: apply_saved_shader_preset_sync(preset_id, request),
    )


@app.post("/api/shader/presets/{preset_id}/rename")
async def rename_shader_tuning_preset(preset_id: str, request: ShaderTuningPresetRenameRequest) -> dict[str, Any]:
    return await asyncio.to_thread(rename_shader_tuning_preset_sync, preset_id, request)


@app.post("/api/shader/presets/{preset_id}/duplicate")
async def duplicate_shader_tuning_preset(preset_id: str, request: ShaderTuningPresetDuplicateRequest) -> dict[str, Any]:
    return await asyncio.to_thread(duplicate_shader_tuning_preset_sync, preset_id, request)


@app.post("/api/shader/presets/{preset_id}/delete")
async def delete_shader_tuning_preset(preset_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(delete_shader_tuning_preset_sync, preset_id)


@app.get("/api/shader/locks")
def read_shader_tuning_locks(avatar_path: str | None = None) -> dict[str, Any]:
    resolved_avatar = avatar_path or DASHBOARD_RUNTIME.current_avatar_path
    locks = load_shader_tuning_locks(resolved_avatar)
    return {"ok": True, "avatarPath": resolved_avatar, **locks}


@app.post("/api/shader/locks")
async def update_shader_tuning_locks(request: ShaderTuningLocksUpdateRequest) -> dict[str, Any]:
    return await asyncio.to_thread(update_shader_tuning_locks_sync, request)


@app.post("/api/shader/vision-review")
async def review_shader_material_vision(request: ShaderVisionReviewRequest) -> dict[str, Any]:
    return await asyncio.to_thread(review_shader_material_vision_sync, request)


@app.post("/api/vision/capture")
async def capture_avatar_screenshot(request: VisionCaptureRequest) -> dict[str, Any]:
    return await asyncio.to_thread(capture_avatar_screenshot_sync, request)


@app.post("/api/vision/capture-status")
async def read_vision_capture_status(request: VisionCaptureStatusRequest) -> dict[str, Any]:
    return await asyncio.to_thread(read_vision_capture_status_sync, request)


@app.post("/api/vision/capture-multi")
async def capture_avatar_multi_screenshot(request: VisionCaptureMultiRequest) -> dict[str, Any]:
    return await asyncio.to_thread(capture_avatar_multi_screenshot_sync, request)


@app.post("/api/vision/audit")
async def audit_avatar_screenshot(request: VisionAuditRequest) -> dict[str, Any]:
    return await asyncio.to_thread(audit_avatar_screenshot_sync, request)


@app.post("/api/vision/audit-multi")
async def audit_avatar_multi_screenshot(request: VisionAuditMultiRequest) -> dict[str, Any]:
    return await asyncio.to_thread(audit_avatar_multi_screenshot_sync, request)


def read_avatars_sync(request: DashboardRequest) -> dict[str, Any]:
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


def scan_scene_avatars_sync(request: AvatarSceneScanRequest) -> dict[str, Any]:
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


def read_avatar_blendshapes_sync(request: AvatarBlendshapeListRequest) -> dict[str, Any]:
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
            "selectedAvatar": serialize_selected_avatar(selected_avatar),
            "blendshapes": blendshapes,
            "filterScope": "face",
            "filterNote": "Only face-related blendshapes are shown for the face editor.",
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "blendshape", "Failed to load blendshape list.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def apply_manual_blendshapes_sync(request: ManualBlendshapeApplyRequest) -> dict[str, Any]:
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
        locked_targets = build_locked_blendshape_set(load_locked_blendshapes(selected_avatar.avatar_path))
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
                "undoDepth": len(DASHBOARD_RUNTIME.manual_undo_stack.get(selected_avatar.avatar_path, [])),
            }

        if using_mock_execute:
            apply_payload = render_manual_blendshape_payload_json(selected_avatar.avatar_path, validated_adjustments)
            result = mock_execute_payload(apply_payload, selected_avatar, export_source)
        else:
            result = apply_blendshapes_direct(settings, selected_avatar.avatar_path, validated_adjustments)

        push_manual_undo_snapshot(selected_avatar.avatar_path, undo_items)
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
            "undoDepth": len(DASHBOARD_RUNTIME.manual_undo_stack.get(selected_avatar.avatar_path, [])),
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "blendshape", "Failed to apply manual blendshape adjustments.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def undo_manual_blendshapes_sync(request: UndoBlendshapeRequest) -> dict[str, Any]:
    try:
        avatar_path = request.avatar_path.strip()
        if not avatar_path:
            raise RuntimeError("avatar_path is required for undo.")

        stack = DASHBOARD_RUNTIME.manual_undo_stack.get(avatar_path) or []
        if not stack:
            raise RuntimeError("There is no manual blendshape action to undo for the selected avatar.")

        settings = load_dashboard_settings(request)
        undo_items = stack.pop()
        result = apply_blendshapes_direct(settings, avatar_path, undo_items)
        emit_log("success", "blendshape", "Manual blendshape undo applied.", {"avatarPath": avatar_path, "count": len(undo_items)})
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "result": serialize_result(result),
            "undoDepth": len(stack),
            "restoredAdjustments": undo_items,
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "blendshape", "Failed to undo manual blendshape adjustments.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def load_tuning_history_store() -> dict[str, Any]:
    return load_tuning_store(
        TUNING_HISTORY_PATH,
        {
            "type": "blendshape_tuning_history",
            "version": "0.1",
            "records": [],
        },
    )


def load_tuning_preset_store() -> dict[str, Any]:
    return load_tuning_store(
        TUNING_PRESETS_PATH,
        {
            "type": "blendshape_tuning_presets",
            "version": "0.1",
            "presets": [],
        },
    )


def load_tuning_locks_store() -> dict[str, Any]:
    return load_tuning_store(
        TUNING_LOCKS_PATH,
        {
            "type": "blendshape_tuning_locks",
            "version": "0.1",
            "avatars": {},
        },
    )


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


def load_locked_blendshapes(avatar_path: str | None) -> list[dict[str, str]]:
    if not avatar_path:
        return []
    store = load_tuning_locks_store()
    avatars = store.get("avatars") if isinstance(store.get("avatars"), dict) else {}
    return normalize_locked_blendshape_list(avatars.get(avatar_path) or [])


def update_tuning_locks_sync(request: TuningLocksUpdateRequest) -> dict[str, Any]:
    avatar_path = (request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path or "").strip()
    if not avatar_path:
        raise to_http_exception(RuntimeError("avatar_path is required before updating locked Blendshapes."))

    locked = normalize_locked_blendshape_list(request.locked_blendshapes)
    store = load_tuning_locks_store()
    avatars = store.get("avatars") if isinstance(store.get("avatars"), dict) else {}
    avatars[avatar_path] = locked
    store["avatars"] = avatars
    save_tuning_store(TUNING_LOCKS_PATH, store)
    emit_log("info", "blendshape", "Locked Blendshape list updated.", {"avatarPath": avatar_path, "count": len(locked)})
    return {"ok": True, "avatarPath": avatar_path, "lockedBlendshapes": locked, "count": len(locked)}


def ai_select_tuning_locks_sync(request: TuningLocksAiSelectRequest) -> dict[str, Any]:
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


def save_tuning_history_record(record: dict[str, Any]) -> dict[str, Any]:
    store = load_tuning_history_store()
    records = list(store.get("records") or [])
    records.append(record)
    store["records"] = records[-200:]
    save_tuning_store(TUNING_HISTORY_PATH, store)
    return record


def find_tuning_history_record(history_id: str) -> dict[str, Any]:
    for record in load_tuning_history_store().get("records") or []:
        if record.get("id") == history_id:
            return record
    raise RuntimeError(f"Tuning history record was not found: {history_id}")


def find_tuning_preset(preset_id: str) -> dict[str, Any]:
    for preset in load_tuning_preset_store().get("presets") or []:
        if preset.get("id") == preset_id:
            return preset
    raise RuntimeError(f"Tuning preset was not found: {preset_id}")


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


def create_tuning_preset_sync(request: TuningPresetCreateRequest) -> dict[str, Any]:
    try:
        history = find_tuning_history_record(request.history_id)
        name = request.name.strip()
        if not name:
            raise RuntimeError("Preset name is required.")

        preset = {
            "id": make_tuning_id("preset"),
            "name": name,
            "created_at": tuning_timestamp(),
            "avatar_name": history.get("avatar_name", ""),
            "avatar_path": history.get("avatar_path", ""),
            "source_history_id": history.get("id", ""),
            "user_prompt": history.get("user_prompt", ""),
            "provider": history.get("provider", ""),
            "provider_id": history.get("provider_id", ""),
            "model": history.get("model", ""),
            "tags": [str(tag).strip() for tag in request.tags if str(tag).strip()],
            "description": request.description.strip(),
            "apply_mode": "after_values",
            "changes": list(history.get("changes") or []),
        }
        store = load_tuning_preset_store()
        presets = list(store.get("presets") or [])
        presets.append(preset)
        presets = trim_presets_for_avatar(presets, request.max_presets)
        store["presets"] = presets
        save_tuning_store(TUNING_PRESETS_PATH, store)
        emit_log("success", "preset", "Tuning preset saved.", {"presetId": preset["id"], "name": preset["name"]})
        return {"ok": True, "preset": preset, "presets": presets}
    except RuntimeError as exc:
        raise to_http_exception(exc) from exc


def rename_tuning_preset_sync(preset_id: str, request: TuningPresetRenameRequest) -> dict[str, Any]:
    try:
        name = request.name.strip()
        if not name:
            raise RuntimeError("Preset name is required.")
        store = load_tuning_preset_store()
        presets = list(store.get("presets") or [])
        for preset in presets:
            if preset.get("id") == preset_id:
                preset["name"] = name
                preset["updated_at"] = tuning_timestamp()
                save_tuning_store(TUNING_PRESETS_PATH, {**store, "presets": presets})
                return {"ok": True, "preset": preset, "presets": presets}
        raise RuntimeError(f"Tuning preset was not found: {preset_id}")
    except RuntimeError as exc:
        raise to_http_exception(exc) from exc


def duplicate_tuning_preset_sync(preset_id: str, request: TuningPresetDuplicateRequest) -> dict[str, Any]:
    try:
        source = find_tuning_preset(preset_id)
        duplicate = json.loads(json.dumps(source))
        duplicate["id"] = make_tuning_id("preset")
        duplicate["name"] = (request.name or f"{source.get('name', 'preset')}_copy").strip()
        duplicate["created_at"] = tuning_timestamp()
        duplicate["source_preset_id"] = source.get("id", "")
        store = load_tuning_preset_store()
        presets = list(store.get("presets") or [])
        presets.append(duplicate)
        presets = trim_presets_for_avatar(presets, request.max_presets)
        store["presets"] = presets
        save_tuning_store(TUNING_PRESETS_PATH, store)
        return {"ok": True, "preset": duplicate, "presets": presets}
    except RuntimeError as exc:
        raise to_http_exception(exc) from exc


def delete_tuning_preset_sync(preset_id: str) -> dict[str, Any]:
    store = load_tuning_preset_store()
    presets = list(store.get("presets") or [])
    remaining = [preset for preset in presets if preset.get("id") != preset_id]
    if len(remaining) == len(presets):
        raise to_http_exception(RuntimeError(f"Tuning preset was not found: {preset_id}"))
    store["presets"] = remaining
    save_tuning_store(TUNING_PRESETS_PATH, store)
    return {"ok": True, "deletedPresetId": preset_id, "presets": remaining}


def apply_saved_tuning_history_sync(history_id: str, request: DashboardRequest) -> dict[str, Any]:
    try:
        record = find_tuning_history_record(history_id)
        payload = apply_saved_tuning_payload(record, request, source_type="history")
        mark_tuning_history_applied(history_id)
        payload["historyRecord"] = find_tuning_history_record(history_id)
        return payload
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "preset", "Failed to reapply tuning history.", {"historyId": history_id, "error": str(exc)})
        raise to_http_exception(exc) from exc


def apply_saved_tuning_preset_sync(preset_id: str, request: DashboardRequest) -> dict[str, Any]:
    try:
        preset = find_tuning_preset(preset_id)
        payload = apply_saved_tuning_payload(preset, request, source_type="preset")
        mark_tuning_preset_applied(preset_id)
        payload["preset"] = find_tuning_preset(preset_id)
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
    locked_blendshapes = load_locked_blendshapes(selected_avatar.avatar_path)
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
        push_manual_undo_snapshot(selected_avatar.avatar_path, undo_items)

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
        "undoDepth": len(DASHBOARD_RUNTIME.manual_undo_stack.get(selected_avatar.avatar_path, [])),
    }


def mark_tuning_history_applied(history_id: str) -> None:
    store = load_tuning_history_store()
    records = list(store.get("records") or [])
    for record in records:
        if record.get("id") == history_id:
            record["applied"] = True
            record["last_applied_at"] = tuning_timestamp()
            break
    store["records"] = records
    save_tuning_store(TUNING_HISTORY_PATH, store)


def mark_tuning_preset_applied(preset_id: str) -> None:
    store = load_tuning_preset_store()
    presets = list(store.get("presets") or [])
    for preset in presets:
        if preset.get("id") == preset_id:
            preset["last_applied_at"] = tuning_timestamp()
            preset["apply_count"] = int(preset.get("apply_count") or 0) + 1
            break
    store["presets"] = presets
    save_tuning_store(TUNING_PRESETS_PATH, store)


def scan_clothes_sync(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        payload = scan_avatar_controls_direct(settings, avatar_path)
        clothes = ensure_list_payload(payload.get("items") or payload.get("clothes") or [], "avatar menu/parameter scan")
        emit_log("info", "fx", "Avatar menu/parameter scan completed.", {"avatarPath": avatar_path, "count": len(clothes)})
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "clothes": clothes,
            "count": len(clothes),
            "jsonPath": payload.get("jsonPath"),
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "fx", "Failed to scan clothing objects.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


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


def generate_clothing_fx_sync(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        payload = build_clothing_fx_blueprint_from_controls(settings, avatar_path)
        emit_log("success", "fx", "Clothing FX blueprint generated.", {"avatarPath": avatar_path, "itemCount": len(payload.get("items") or [])})
        return {"ok": True, "avatarPath": avatar_path, "fxBlueprint": payload}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "fx", "Failed to generate clothing FX blueprint.", {"error": str(exc)})
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
            for material in materials:
                if not isinstance(material, dict):
                    continue
                material_id = str(material.get("material_id") or "")
                override = overrides.get(material_id)
                if override in {"skin", "eyes", "hair", "clothes", "accessory", "unknown"}:
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


def apply_shader_material_plan_sync(request: ShaderMaterialApplyRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or request.avatar or DASHBOARD_RUNTIME.current_avatar_path
        if not request.changes:
            raise RuntimeError("No shader material changes were provided.")

        inventory = copy.deepcopy(request.inventory) if request.inventory else scan_shader_materials_direct(settings, avatar_path)
        inventory = apply_shader_category_overrides(inventory, request.category_overrides)
        locks = load_shader_tuning_locks(avatar_path)
        locked_materials = set(locks.get("lockedMaterials") or []) | set(request.locked_materials or [])
        locked_properties = set(locks.get("lockedProperties") or []) | set(request.locked_properties or [])
        validation = validate_shader_material_tuning_plan(
            plan={"type": "material_tuning_plan", "version": "0.2", "changes": request.changes, "warnings": []},
            inventory=inventory,
            locked_materials=locked_materials,
            locked_properties=locked_properties,
        )
        changes = validation["validatedChanges"]
        if not changes:
            raise RuntimeError("No valid shader material changes remained after validation.")

        result = apply_shader_material_tuning_direct(settings, avatar_path, changes)
        applied = normalize_shader_applied_changes(result, changes)
        skipped = [*validation["skippedChanges"], *list(result.get("skipped") or [])]
        backup_changes = build_shader_restore_changes(applied)
        if backup_changes:
            undo_stack = DASHBOARD_RUNTIME.shader_undo_stack.setdefault(avatar_path or "", [])
            undo_stack.append(backup_changes)
        if request.history_id:
            mark_shader_tuning_history_applied(request.history_id)

        emit_log(
            "success",
            "shader",
            "Shader material tuning applied.",
            {"avatarPath": avatar_path, "appliedCount": len(applied), "skippedCount": len(skipped)},
        )
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "result": result,
            "appliedChanges": applied,
            "skippedChanges": skipped,
            "warnings": validation["warnings"],
            "undoDepth": len(DASHBOARD_RUNTIME.shader_undo_stack.get(avatar_path or "", [])),
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "shader", "Failed to apply shader material tuning.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def restore_shader_material_plan_sync(request: ShaderMaterialRestoreRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        undo_stack = DASHBOARD_RUNTIME.shader_undo_stack.setdefault(avatar_path or "", [])
        if not undo_stack:
            raise RuntimeError("No shader material restore point is available.")

        restore_changes = undo_stack.pop()
        result = apply_shader_material_tuning_direct(settings, avatar_path, restore_changes)
        applied = normalize_shader_applied_changes(result, restore_changes)
        skipped = list(result.get("skipped") or [])
        emit_log(
            "success",
            "shader",
            "Shader material tuning restored.",
            {"avatarPath": avatar_path, "restoredCount": len(applied), "skippedCount": len(skipped)},
        )
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "result": result,
            "restoredChanges": applied,
            "skippedChanges": skipped,
            "undoDepth": len(undo_stack),
        }
    except (RuntimeError, UnityMcpError) as exc:
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
    if isinstance(raw_applied, list) and raw_applied:
        return [item for item in raw_applied if isinstance(item, dict)]

    try:
        applied_count = int(result.get("appliedCount") or result.get("applied_count") or 0)
    except (TypeError, ValueError):
        applied_count = 0

    if applied_count <= 0:
        return []
    if applied_count != len(requested_changes):
        return []

    # Some unity-mcp custom-tool calls flatten list payloads as "[N items]" in
    # stdout. The validation layer already expanded before/after values, so when
    # Unity reports that every requested change applied we can preserve rollback
    # state from the validated request instead of losing the undo point.
    return [dict(change) for change in requested_changes if isinstance(change, dict)]


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


def apply_saved_shader_history_sync(history_id: str, request: ShaderMaterialPlanRequest) -> dict[str, Any]:
    store = load_shader_tuning_history_store()
    record = next((item for item in store.get("records") or [] if item.get("id") == history_id), None)
    if not record:
        raise to_http_exception(RuntimeError(f"Shader tuning history record was not found: {history_id}"))
    response = apply_saved_shader_payload(record, request, source_type="history")
    mark_shader_tuning_history_applied(history_id)
    return response


def apply_saved_shader_preset_sync(preset_id: str, request: ShaderMaterialPlanRequest) -> dict[str, Any]:
    store = load_shader_tuning_preset_store()
    preset = next((item for item in store.get("presets") or [] if item.get("id") == preset_id), None)
    if not preset:
        raise to_http_exception(RuntimeError(f"Shader tuning preset was not found: {preset_id}"))
    response = apply_saved_shader_payload(preset, request, source_type="preset")
    mark_shader_tuning_preset_applied(preset_id)
    return response


def apply_saved_shader_payload(saved_payload: dict[str, Any], request: ShaderMaterialPlanRequest, source_type: str) -> dict[str, Any]:
    changes = list(saved_payload.get("changes") or [])
    replay_changes = [
        {
            **change,
            "after": change.get("after"),
            "reason": change.get("reason") or f"Reapply saved shader {source_type}.",
        }
        for change in changes
        if isinstance(change, dict) and "after" in change
    ]
    request_data = request.model_dump()
    request_data["avatar_path"] = request.avatar_path or saved_payload.get("avatar_path") or request.avatar
    request_data["changes"] = replay_changes
    request_data["history_id"] = saved_payload.get("source_history_id") if source_type == "preset" else saved_payload.get("id")
    apply_request = ShaderMaterialApplyRequest(**request_data)
    response = apply_shader_material_plan_sync(apply_request)
    response["sourceType"] = source_type
    response["sourceRecord"] = saved_payload
    return response


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
    for material in inventory.get("materials") or []:
        if not isinstance(material, dict):
            continue
        material_id = str(material.get("material_id") or "")
        override = (overrides or {}).get(material_id)
        if override in valid_categories:
            material["category"] = override
    return inventory


def build_shader_material_index(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for material in inventory.get("materials") or []:
        if not isinstance(material, dict):
            continue
        material_id = str(material.get("material_id") or "")
        if material_id:
            index[material_id] = material
    return index


def validate_shader_material_tuning_plan(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    locked_materials: set[str] | None = None,
    locked_properties: set[str] | None = None,
) -> dict[str, Any]:
    material_index = build_shader_material_index(inventory)
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


def capture_avatar_screenshot_sync(request: VisionCaptureRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        output_path = (DASHBOARD_ARTIFACTS_DIR / "latest" / "vision_capture.png").resolve()
        payload = capture_scene_view_direct(
            settings=settings,
            output_path=output_path,
            width=request.width,
            height=request.height,
            avatar_path=request.avatar_path,
            set_rotation=False,
            require_play_mode=request.require_play_mode,
        )
        image_path = payload.get("imagePath") or str(output_path)
        image_url = to_artifact_url(image_path)
        DASHBOARD_RUNTIME.latest_screenshot_path = image_path
        DASHBOARD_RUNTIME.latest_screenshot_url = image_url
        emit_log("success", "vision", "Screenshot captured for visual audit.", {"imagePath": image_path})
        return {"ok": True, "imagePath": image_path, "imageUrl": image_url, "capture": payload}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "vision", "Failed to capture screenshot.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


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

        api_config = serialize_api_config(include_secret=True)
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


def apply_clothing_fx_sync(request: ClothingApplyFxRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        items = request.items
        if not items:
            raise RuntimeError("No clothing items provided. Run /api/clothes/scan or /api/clothes/generate-fx first.")

        apply_payload = build_clothes_fx_apply_preview(avatar_path, items)

        if request.dry_run:
            emit_log("info", "fx", "Clothing FX apply payload generated (dry-run).", {"avatarPath": avatar_path, "itemCount": len(items)})
            return {"ok": True, "avatarPath": avatar_path, "dryRun": True, "applyPayload": apply_payload, "itemCount": len(items)}

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


def rollback_parameter_optimization_sync(request: ParameterRollbackRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        snapshot_path = resolve_parameter_snapshot_path(request.snapshot_path)
        snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if not isinstance(snapshot_payload, dict):
            raise RuntimeError(f"Parameter snapshot is not a JSON object: {snapshot_path}")

        avatar_path = request.avatar_path or snapshot_payload.get("avatarPath") or DASHBOARD_RUNTIME.current_avatar_path
        apply_payload = build_parameter_rollback_preview(avatar_path, snapshot_payload)
        payload = rollback_parameters_direct(settings, avatar_path, snapshot_payload)
        restored_count = payload.get("restoredCount", snapshot_payload.get("parameterCount", 0))
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
    except (RuntimeError, UnityMcpError, json.JSONDecodeError) as exc:
        emit_log("error", "parameter", "Failed to rollback parameter optimization.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


_ANGLE_CAMERA_ROTATIONS: dict[str, tuple[float, float, float]] = {
    "front":      (15.0,   0.0,  0.0),
    "side_left":  (10.0,  90.0,  0.0),
    "side_right": (10.0, -90.0,  0.0),
    "back":       (10.0, 180.0,  0.0),
}


def capture_avatar_multi_screenshot_sync(request: VisionCaptureMultiRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        angles = [a.strip().lower() for a in (request.angles or list(_ANGLE_CAMERA_ROTATIONS.keys()))]
        output_dir = (DASHBOARD_ARTIFACTS_DIR / "latest").resolve()
        captures: list[dict[str, Any]] = []

        for angle in angles:
            out_path = output_dir / f"vision_{angle}.png"
            pitch, yaw, roll = _ANGLE_CAMERA_ROTATIONS.get(angle, (10.0, 0.0, 0.0))
            payload = capture_scene_view_direct(
                settings=settings,
                output_path=out_path,
                width=request.width,
                height=request.height,
                pitch=pitch,
                yaw=yaw,
                roll=roll,
                set_rotation=True,
                avatar_path=request.avatar_path,
                capture_scope="face",
                require_play_mode=request.require_play_mode,
            )
            image_path = payload.get("imagePath") or str(out_path)
            image_url = to_artifact_url(image_path)
            captures.append(
                {
                    "angle": angle,
                    "imagePath": image_path,
                    "imageUrl": image_url,
                    "rotation": {"pitch": pitch, "yaw": yaw, "roll": roll},
                    "capture": payload,
                }
            )

        if captures:
            DASHBOARD_RUNTIME.latest_screenshot_path = captures[0]["imagePath"]
            DASHBOARD_RUNTIME.latest_screenshot_url = captures[0]["imageUrl"]

        emit_log("success", "vision", "Multi-angle screenshots captured.", {"angles": angles, "count": len(captures)})
        return {"ok": True, "captures": captures}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "vision", "Failed to capture multi-angle screenshots.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def audit_avatar_multi_screenshot_sync(request: VisionAuditMultiRequest) -> dict[str, Any]:
    try:
        image_paths = request.image_paths
        if not image_paths:
            raise RuntimeError("No image paths provided for multi-image audit.")

        api_config = serialize_api_config(include_secret=True)
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


def run_dashboard_pipeline_sync(request: DashboardRequest, execute: bool) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        export_payload, export_source, using_mock_execute = load_dashboard_export_payload(settings, request)
        selected_avatar = resolve_avatar_selection(export_payload, request.avatar)
        remember_loaded_avatar(selected_avatar.avatar_name, selected_avatar.avatar_path)
        locked_blendshapes = load_locked_blendshapes(selected_avatar.avatar_path)
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
                visual_proof = capture_blendshape_visual_proof(
                    settings=settings,
                    selected_avatar=selected_avatar,
                    stage="before",
                    current_proof=visual_proof,
                )
                direct_adjustments = build_direct_blendshape_adjustments_from_plan(plan)
                undo_items = build_undo_items_from_change_preview(change_preview)
                result = apply_blendshapes_direct(settings, selected_avatar.avatar_path, direct_adjustments)
                push_manual_undo_snapshot(selected_avatar.avatar_path, undo_items)
                time.sleep(0.15)
                visual_proof = capture_blendshape_visual_proof(
                    settings=settings,
                    selected_avatar=selected_avatar,
                    stage="after",
                    current_proof=visual_proof,
                )
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

        history_record = save_tuning_history_record(
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
    settings = load_settings(
        settings_path,
        getattr(request, "model", None),
        llm_override=serialize_api_config(include_secret=True),
    )

    settings.unity_mcp_host = request.unity_host or DASHBOARD_STATE.unity_host or settings.unity_mcp_host
    settings.unity_mcp_port = int(request.unity_port if request.unity_port is not None else DASHBOARD_STATE.unity_port or settings.unity_mcp_port)

    if request.unity_instance is not None:
        settings.unity_mcp_instance = request.unity_instance.strip()
    elif DASHBOARD_STATE.unity_instance:
        settings.unity_mcp_instance = DASHBOARD_STATE.unity_instance

    resolve_unity_cli_instance_selector(settings)
    return settings


def _agent_gateway_llm_plan(prompt: str) -> str:
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
    response = request_llm_plan_with_metadata(settings, prompt)
    reasoning = dict(response.reasoning or {})
    if int(reasoning.get("itemCount") or 0) > 0:
        AGENT_GATEWAY.llm_reasoning_trace = reasoning
    return response.text


AGENT_GATEWAY.llm_plan_fn = _agent_gateway_llm_plan


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

    stdout_payload = parse_flat_unity_stdout_payload(result.stdout)
    if stdout_payload:
        return stdout_payload

    if isinstance(candidate, str):
        parsed = try_parse_json(candidate)
        return parsed if parsed is not None else candidate

    return candidate


def parse_flat_unity_stdout_payload(stdout: str) -> dict[str, Any]:
    """Parse unity-mcp's flattened custom-tool stdout into a small dict.

    Some project-scoped custom tools are returned by the CLI as:
    ``key: value`` lines instead of a JSON object. ``try_parse_json`` can also
    accidentally pick up fragments like ``[0 items]`` as a list, so dashboard
    endpoints that expect objects need this stdout fallback.
    """
    payload: dict[str, Any] = {}
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("[") or line.startswith("✅") or line.startswith("Executed custom tool"):
            continue
        if ": " not in line:
            continue

        key, value = line.split(": ", 1)
        key = key.strip()
        value = value.strip()
        if not key or not all(ch.isalnum() or ch == "_" for ch in key):
            continue

        payload[key] = parse_flat_unity_stdout_value(value)

    return payload


def parse_flat_unity_stdout_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.startswith("[") and value.endswith("items]"):
        return []

    parsed = try_parse_json(value)
    if parsed is not None:
        return parsed

    try:
        if all(ch not in value for ch in ".eE"):
            return int(value)
        return float(value)
    except ValueError:
        return value


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


def run_unity_cli_json(settings: Settings, cli_args: list[str]) -> dict[str, Any]:
    try:
        output = run_unity_mcp_passthrough(settings, cli_args)
        parsed = try_parse_json(output)
        emit_log("info", "unity", "Unity CLI command completed.", {"command": cli_args[-1], "instance": settings.unity_mcp_instance})
        return {
            "ok": True,
            "output": output,
            "parsed": parsed,
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "unity", "Unity CLI command failed.", {"command": " ".join(cli_args), "error": str(exc)})
        raise to_http_exception(exc) from exc


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
    result = invoke_unity_mcp(
        settings,
        "vrc_scan_avatar_controls",
        {
            "avatarPath": avatar_path or "",
            "outputPath": str(output_path),
        },
    )
    if output_path.exists():
        payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        payload.setdefault("jsonPath", str(output_path))
        return ensure_dict_payload(payload, "avatar menu/parameter scan")

    payload = extract_tool_result_payload(result)
    return ensure_dict_payload(payload, "avatar menu/parameter scan")


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
    result = invoke_unity_mcp(
        settings,
        "vrc_scan_avatar_parameters",
        {
            "avatarPath": avatar_path or "",
            "outputPath": str(output_path),
        },
    )
    if output_path.exists():
        payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        payload.setdefault("jsonPath", str(output_path))
        return ensure_dict_payload(payload, "parameter scan")

    payload = extract_tool_result_payload(result)
    return ensure_dict_payload(payload, "parameter scan")


def capture_scene_view_direct(
    settings: Settings,
    output_path: Path,
    width: int,
    height: int,
    avatar_path: str | None = None,
    pitch: float = 0.0,
    yaw: float = 0.0,
    roll: float = 0.0,
    set_rotation: bool = False,
    capture_scope: str = "avatar",
    require_play_mode: bool = False,
) -> dict[str, Any]:
    result = invoke_unity_mcp(
        settings,
        "vrc_capture_scene_view",
        {
            "outputPath": str(output_path),
            "width": width,
            "height": height,
            "pitch": pitch,
            "yaw": yaw,
            "roll": roll,
            "setRotation": set_rotation,
            "restoreView": True,
            "avatarPath": avatar_path or "",
            "captureScope": capture_scope,
            "requirePlayMode": require_play_mode,
        },
    )
    payload = extract_tool_result_payload(result)
    if isinstance(payload, dict):
        return payload

    if output_path.exists():
        return {
            "imagePath": str(output_path),
            "width": width,
            "height": height,
            "pitch": pitch,
            "yaw": yaw,
            "roll": roll,
            "setRotation": set_rotation,
            "avatarPath": avatar_path or "",
            "captureScope": capture_scope,
            "requirePlayMode": require_play_mode,
        }

    return ensure_dict_payload(payload, "vision capture")


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
    result = invoke_unity_mcp(
        settings,
        "vrc_scan_avatar_materials",
        {
            "avatarPath": avatar_path or "",
            "outputPath": str(output_path),
            "refreshAssets": False,
        },
    )
    if output_path.exists():
        payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        payload.setdefault("jsonPath", str(output_path))
        return ensure_dict_payload(payload, "shader material scan")

    payload = extract_tool_result_payload(result)
    return ensure_dict_payload(payload, "shader material scan")


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


def capture_blendshape_visual_proof(
    settings: Settings,
    selected_avatar: SelectedAvatar,
    stage: str,
    current_proof: dict[str, Any] | None,
) -> dict[str, Any]:
    proof = dict(current_proof or {})
    output_dir = (DASHBOARD_ARTIFACTS_DIR / "latest").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"blendshape_{stage}.png"

    try:
        payload = capture_scene_view_direct(
            settings=settings,
            output_path=output_path,
            width=960,
            height=960,
            avatar_path=selected_avatar.avatar_path,
            set_rotation=False,
        )
        image_path = payload.get("imagePath") or str(output_path)
        proof[stage] = {
            "imagePath": image_path,
            "imageUrl": to_artifact_url(image_path),
            "capture": payload,
        }
        emit_log("info", "pipeline", f"Captured blendshape {stage} proof image.", {"imagePath": image_path})
    except Exception as exc:
        proof.setdefault("errors", []).append({"stage": stage, "error": str(exc)})
        emit_log("warning", "pipeline", f"Failed to capture blendshape {stage} proof image.", {"error": str(exc)})

    return proof


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


def push_manual_undo_snapshot(avatar_path: str, adjustments: list[dict[str, Any]]) -> None:
    stack = DASHBOARD_RUNTIME.manual_undo_stack.setdefault(avatar_path, [])
    stack.append(adjustments)
    if len(stack) > 12:
        del stack[0]


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


def to_artifact_url(path_value: str) -> str:
    try:
        path = resolve_local_path(path_value)
        relative = path.relative_to(DASHBOARD_ARTIFACTS_DIR).as_posix()
        return f"/artifacts/{relative}"
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
        image_path = resolve_under(DASHBOARD_ARTIFACTS_DIR, path_value[len("/artifacts/"):])
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


def emit_log(level: str, scope: str, message: str, data: dict[str, Any] | None = None) -> None:
    entry = build_log_entry(level, scope, message, data)
    record_log_entry(entry)
    EVENT_BUS.broadcast_from_sync("log", entry)


async def emit_log_async(level: str, scope: str, message: str, data: dict[str, Any] | None = None) -> None:
    entry = build_log_entry(level, scope, message, data)
    record_log_entry(entry)
    await EVENT_BUS.broadcast("log", entry)


def build_log_entry(level: str, scope: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": f"{scope}-{datetime.now().strftime('%H%M%S%f')}",
        "timestamp": utc_now_iso(),
        "level": level,
        "scope": scope,
        "message": message,
        "data": data or {},
    }


def record_log_entry(entry: dict[str, Any]) -> None:
    RECENT_LOGS.append(entry)
    prune_recent_logs()
    append_local_log(entry)


def recent_log_snapshot() -> list[dict[str, Any]]:
    prune_recent_logs()
    return list(RECENT_LOGS)


def prune_recent_logs() -> None:
    cutoff = datetime.now(timezone.utc) - LOG_RETENTION
    while RECENT_LOGS:
        timestamp = parse_log_timestamp(RECENT_LOGS[0].get("timestamp"))
        if timestamp is not None and timestamp >= cutoff:
            break
        RECENT_LOGS.popleft()


def append_local_log(entry: dict[str, Any]) -> None:
    with LOCAL_LOG_LOCK:
        LOCAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOCAL_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        prune_local_log_file()
        prune_stale_dashboard_log_files()


def prune_local_log_file() -> None:
    if not LOCAL_LOG_PATH.exists():
        return

    prune_jsonl_log_file(LOCAL_LOG_PATH)


def prune_jsonl_log_file(path: Path) -> None:
    if not path.exists():
        return
    cutoff = datetime.now(timezone.utc) - LOG_RETENTION
    kept_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if should_keep_log_line(line, cutoff):
            kept_lines.append(line)

    path.write_text(("\n".join(kept_lines) + "\n") if kept_lines else "", encoding="utf-8")


def prune_stale_dashboard_log_files() -> None:
    if not DASHBOARD_ARTIFACTS_DIR.exists():
        return

    cutoff = datetime.now(timezone.utc) - LOG_RETENTION
    for log_path in DASHBOARD_ARTIFACTS_DIR.glob("*.log"):
        if log_path == LOCAL_LOG_PATH:
            continue
        try:
            modified_at = datetime.fromtimestamp(log_path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if modified_at < cutoff:
            log_path.unlink(missing_ok=True)


def should_keep_log_line(line: str, cutoff: datetime) -> bool:
    if not line.strip():
        return False
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return False

    timestamp = parse_log_timestamp(payload.get("timestamp"))
    return timestamp is not None and timestamp >= cutoff


def parse_log_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def unity_http_base(settings: Settings) -> str:
    host = settings.unity_mcp_host or "127.0.0.1"
    port = int(settings.unity_mcp_port or 8080)
    return f"http://{host}:{port}"


def fetch_unity_http_json(settings: Settings, path: str) -> tuple[bool, Any, str, int | None]:
    url = f"{unity_http_base(settings)}{path}"
    timeout = max(1.0, min(float(settings.unity_mcp_timeout_seconds or 5), 10.0))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - loopback diagnostic URL from local settings.
            status_code = int(getattr(response, "status", 200))
            raw = response.read().decode("utf-8", errors="replace")
            parsed = try_parse_json(raw)
            return 200 <= status_code < 300, parsed if parsed is not None else raw, "", status_code
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        return False, try_parse_json(body) or body, f"HTTP {exc.code}", int(exc.code)
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc), None


def post_unity_http_json(settings: Settings, path: str, payload: dict[str, Any]) -> tuple[bool, Any, str, int | None]:
    url = f"{unity_http_base(settings)}{path}"
    timeout = max(1.0, min(float(settings.unity_mcp_timeout_seconds or 5), 20.0))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback diagnostic URL from local settings.
            status_code = int(getattr(response, "status", 200))
            raw = response.read().decode("utf-8", errors="replace")
            parsed = try_parse_json(raw)
            return 200 <= status_code < 300, parsed if parsed is not None else raw, "", status_code
    except urllib.error.HTTPError as exc:
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body_text = ""
        return False, try_parse_json(body_text) or body_text, f"HTTP {exc.code}", int(exc.code)
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc), None


def normalize_unity_instance(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    session_id = str(raw.get("session_id") or raw.get("sessionId") or raw.get("id") or "").strip()
    project = str(raw.get("project") or raw.get("project_name") or raw.get("projectName") or raw.get("name") or "").strip()
    unity_version = str(raw.get("unity_version") or raw.get("unityVersion") or raw.get("version") or "").strip()
    project_path = normalize_path_string(str(raw.get("project_path") or raw.get("projectPath") or raw.get("path") or "").strip())
    if project_path in {".", "./"}:
        project_path = ""
    instance_hash = str(raw.get("hash") or raw.get("project_id") or raw.get("projectId") or "").strip()
    cli_instance_id = instance_hash or project or session_id
    return {
        "sessionId": session_id,
        "cliInstanceId": cli_instance_id,
        "project": project,
        "projectName": project,
        "projectPath": project_path,
        "hash": instance_hash,
        "unityVersion": unity_version,
        "connectedAt": str(raw.get("connected_at") or raw.get("connectedAt") or "").strip(),
        "raw": raw,
    }


def normalize_unity_instances_payload(payload: Any) -> list[dict[str, Any]]:
    raw_instances: Any = []
    if isinstance(payload, dict):
        raw_instances = payload.get("instances") or payload.get("data") or payload.get("result") or []
        if isinstance(raw_instances, dict):
            raw_instances = raw_instances.get("instances") or raw_instances.get("items") or []
    elif isinstance(payload, list):
        raw_instances = payload

    if not isinstance(raw_instances, list):
        return []
    return [instance for item in raw_instances if (instance := normalize_unity_instance(item))]


def instance_matches_selector(instance: dict[str, Any], selector: str) -> bool:
    normalized_selector = selector.strip().casefold()
    if not normalized_selector:
        return False
    candidates = [
        instance.get("sessionId"),
        instance.get("cliInstanceId"),
        instance.get("project"),
        instance.get("projectName"),
        instance.get("hash"),
        Path(str(instance.get("projectPath") or "")).name if instance.get("projectPath") else "",
    ]
    return any(str(candidate or "").strip().casefold() == normalized_selector for candidate in candidates)


def choose_active_unity_instance(instances: list[dict[str, Any]], settings: Settings) -> tuple[dict[str, Any] | None, bool]:
    selector = (settings.unity_mcp_instance or DASHBOARD_STATE.unity_instance or "").strip()
    if selector:
        for instance in instances:
            if instance_matches_selector(instance, selector):
                return instance, True
    if len(instances) == 1:
        return instances[0], not bool(selector)
    return None, False


def resolve_unity_cli_instance_selector(settings: Settings) -> None:
    """Map CoplayDev session ids from /api/instances to CLI-safe project ids."""
    selector = (settings.unity_mcp_instance or DASHBOARD_STATE.unity_instance or "").strip()
    ok, payload, _error, _status_code = fetch_unity_http_json(settings, "/api/instances")
    if not ok:
        return

    instances = normalize_unity_instances_payload(payload)
    active_instance, _selected_match = choose_active_unity_instance(instances, settings)
    if not active_instance:
        return

    cli_selector = str(
        active_instance.get("cliInstanceId")
        or active_instance.get("hash")
        or active_instance.get("project")
        or active_instance.get("sessionId")
        or ""
    ).strip()
    if not cli_selector:
        return

    if selector and selector != cli_selector:
        emit_log(
            "info",
            "unity",
            "Resolved Unity MCP session selector to CLI project selector.",
            {"selector": selector, "cliSelector": cli_selector, "project": active_instance.get("project")},
        )
    settings.unity_mcp_instance = cli_selector
    DASHBOARD_STATE.unity_instance = cli_selector


def build_unity_instances_diagnostics(settings: Settings) -> dict[str, Any]:
    ok, payload, error, status_code = fetch_unity_http_json(settings, "/api/instances")
    instances = normalize_unity_instances_payload(payload) if ok else []
    active_instance, selected_match = choose_active_unity_instance(instances, settings)
    if active_instance:
        cli_selector = active_instance.get("cliInstanceId") or active_instance.get("hash") or active_instance.get("project") or active_instance.get("sessionId") or ""
        if cli_selector:
            DASHBOARD_STATE.unity_instance = cli_selector
            settings.unity_mcp_instance = cli_selector
    return {
        "ok": ok,
        "reachable": ok,
        "statusCode": status_code,
        "host": settings.unity_mcp_host,
        "port": settings.unity_mcp_port,
        "instance": settings.unity_mcp_instance,
        "instances": instances,
        "activeCount": len(instances),
        "activeInstance": active_instance,
        "selectedInstanceMatched": selected_match,
        "raw": payload,
        "error": error,
    }


def collect_tool_names_from_payload(payload: Any) -> list[str]:
    names: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and all(ch.isalnum() or ch in "_-." for ch in stripped):
                names.append(stripped)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return

        for key in ("name", "toolName", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                names.append(candidate.strip())
        function_payload = value.get("function")
        if isinstance(function_payload, dict):
            candidate = function_payload.get("name")
            if isinstance(candidate, str) and candidate.strip():
                names.append(candidate.strip())
        for key in ("tools", "items", "functions", "commands"):
            if key in value:
                visit(value[key])
        for key in ("result", "data", "payload"):
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                visit(nested)

    visit(payload)
    unique: dict[str, None] = {}
    for name in names:
        unique.setdefault(name, None)
    return sorted(unique)


def build_unity_tools_diagnostics(settings: Settings) -> dict[str, Any]:
    resolve_unity_cli_instance_selector(settings)
    output = ""
    parsed: Any = None
    error = ""
    try:
        cli_payload = run_unity_cli_json(settings, ["-f", "json", "tool", "list"])
        output = str(cli_payload.get("output") or "")
        parsed = cli_payload.get("parsed")
        cli_ok = bool(cli_payload.get("ok"))
    except Exception as exc:  # noqa: BLE001
        cli_ok = False
        error = str(exc)

    names = collect_tool_names_from_payload(parsed)
    if output:
        import re

        for name in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)?\b", output):
            if name.startswith("vrc_") or name.startswith("vrcforge_"):
                names.append(name)
        names = sorted(dict.fromkeys(names))

    vrcforge_tools = sorted(name for name in names if name.startswith("vrc_") or name.startswith("vrcforge_"))
    missing_required = [name for name in REQUIRED_VRCFORGE_UNITY_TOOLS if name not in set(names)]
    return {
        "ok": cli_ok and bool(names),
        "reachable": cli_ok,
        "connected": cli_ok,
        "host": settings.unity_mcp_host,
        "port": settings.unity_mcp_port,
        "instance": settings.unity_mcp_instance,
        "totalTools": len(names),
        "defaultToolsCount": max(0, len(names) - len(vrcforge_tools)),
        "vrcForgeToolsCount": len(vrcforge_tools),
        "toolNames": names,
        "vrcForgeToolNames": vrcforge_tools,
        "missingRequiredVrcForgeTools": missing_required,
        "onlyDefaultTools": cli_ok and bool(names) and not vrcforge_tools,
        "output": output,
        "parsed": parsed,
        "error": error,
    }


def build_unity_status_snapshot(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_dashboard_settings(ConnectionRequest(settings_path=str(DASHBOARD_STATE.settings_path)))
    settings.unity_mcp_timeout_seconds = min(settings.unity_mcp_timeout_seconds, 10)
    instances = build_unity_instances_diagnostics(settings)
    tools = build_unity_tools_diagnostics(settings)
    mcp_health = fetch_mcp_server_health(settings)
    unity_mcp_package_version = ""
    selected_project = normalize_path_string(DASHBOARD_STATE.selected_project_path)
    if selected_project:
        try:
            selected_project_path = Path(selected_project)
            if is_unity_project_path(selected_project_path):
                unity_mcp_package_version = read_unity_mcp_package_version(selected_project_path)
        except Exception:  # noqa: BLE001 - status diagnostics should stay best effort.
            unity_mcp_package_version = ""

    try:
        output = run_unity_mcp_passthrough(settings, ["-f", "json", "status"])
        parsed = try_parse_json(output)
        status_error = ""
        status_reachable = True
    except Exception as exc:  # noqa: BLE001
        output = ""
        parsed = None
        status_error = str(exc)
        status_reachable = False

    connected = bool(
        instances.get("reachable")
        and (instances.get("activeCount") or tools.get("reachable") or status_reachable)
    ) or bool(tools.get("reachable") and (tools.get("totalTools") or tools.get("vrcForgeToolsCount")))
    errors = [item for item in [instances.get("error"), tools.get("error"), status_error] if item]
    active_instance = instances.get("activeInstance")

    return {
        "connected": connected,
        "mcpServerReachable": bool(instances.get("reachable") or tools.get("reachable") or status_reachable),
        "unityInstanceRegistered": bool(instances.get("activeCount")),
        "selectedInstanceMatched": bool(instances.get("selectedInstanceMatched")),
        "host": settings.unity_mcp_host,
        "port": settings.unity_mcp_port,
        "instance": settings.unity_mcp_instance,
        "projectPath": DASHBOARD_STATE.selected_project_path,
        "activeInstance": active_instance,
        "instances": instances.get("instances") or [],
        "activeInstanceCount": instances.get("activeCount") or 0,
        "tools": tools,
        "mcpHealth": mcp_health,
        "unityMcpPackageVersion": unity_mcp_package_version,
        "vrcForgeToolsRegistered": bool(tools.get("vrcForgeToolsCount")),
        "missingRequiredVrcForgeTools": tools.get("missingRequiredVrcForgeTools") or [],
        "output": output,
        "parsed": parsed,
        "error": "\n".join(errors),
    }


def _repair_phase(phase_id: str, status: str, message: str, detail: Any = None) -> dict[str, Any]:
    if status not in {"ok", "warning", "error", "skipped"}:
        status = "warning"
    return {
        "id": phase_id,
        "status": status,
        "message": message,
        "detail": _redact_doctor_detail(detail),
    }


def _unity_repair_status_summary(status: dict[str, Any]) -> dict[str, Any]:
    tools = status.get("tools") if isinstance(status.get("tools"), dict) else {}
    mcp_health = status.get("mcpHealth") if isinstance(status.get("mcpHealth"), dict) else {}
    return {
        "connected": bool(status.get("connected")),
        "mcpServerReachable": bool(status.get("mcpServerReachable")),
        "mcpServerVersion": str(mcp_health.get("version") or mcp_health.get("serverVersion") or ""),
        "unityMcpPackageVersion": str(status.get("unityMcpPackageVersion") or ""),
        "unityInstanceRegistered": bool(status.get("unityInstanceRegistered")),
        "selectedInstanceMatched": bool(status.get("selectedInstanceMatched")),
        "activeInstanceCount": int(status.get("activeInstanceCount") or 0),
        "vrcForgeToolsRegistered": bool(status.get("vrcForgeToolsRegistered")),
        "totalTools": int(tools.get("totalTools") or 0),
        "vrcForgeToolsCount": int(tools.get("vrcForgeToolsCount") or 0),
        "missingRequiredVrcForgeTools": status.get("missingRequiredVrcForgeTools") or [],
        "toolsError": str(tools.get("error") or ""),
        "error": str(status.get("error") or ""),
    }


def read_unity_mcp_package_version(project_root: Path) -> str:
    candidates = [
        project_root / "Packages" / "com.coplaydev.unity-mcp" / "package.json",
        project_root / "Packages" / "com.gamelovers.mcp-unity" / "package.json",
    ]
    package_cache = project_root / "Library" / "PackageCache"
    if package_cache.is_dir():
        candidates.extend(sorted(package_cache.glob("com.coplaydev.unity-mcp*/package.json")))
        candidates.extend(sorted(package_cache.glob("com.gamelovers.mcp-unity*/package.json")))
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            version = str(payload.get("version") or "").strip()
            if version:
                return version
        except Exception:  # noqa: BLE001 - Doctor version read is best effort.
            continue
    return ""


def fetch_mcp_server_health(settings: Settings) -> dict[str, Any]:
    ok, payload, error, status_code = fetch_unity_http_json(settings, "/health")
    result: dict[str, Any] = {
        "ok": ok,
        "statusCode": status_code,
        "error": error,
    }
    if isinstance(payload, dict):
        result.update(payload)
    elif payload not in (None, ""):
        result["payload"] = payload
    return result


def _decode_csharp_string_literal(value: str) -> str:
    try:
        return bytes(value, "utf-8").decode("unicode_escape")
    except Exception:  # noqa: BLE001
        return value


def discover_vrcforge_unity_tool_definitions(project_root: Path) -> list[dict[str, Any]]:
    editor_root = project_root / "Assets" / "VRCForge" / "Editor"
    if not editor_root.is_dir():
        return []

    attribute_pattern = re.compile(r"\[\s*McpForUnityTool\s*\((?P<body>.*?)\)\s*\]", re.DOTALL)
    name_pattern = re.compile(r"\bname\s*:\s*\"(?P<value>(?:\\.|[^\"\\])*)\"", re.DOTALL)
    first_string_pattern = re.compile(r"\"(?P<value>(?:\\.|[^\"\\])*)\"", re.DOTALL)
    description_pattern = re.compile(r"\bDescription\s*=\s*\"(?P<value>(?:\\.|[^\"\\])*)\"", re.DOTALL)

    definitions: dict[str, dict[str, Any]] = {}
    for source_path in sorted(editor_root.rglob("*.cs")):
        try:
            source = source_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for match in attribute_pattern.finditer(source):
            body = match.group("body") or ""
            name_match = name_pattern.search(body) or first_string_pattern.search(body)
            if not name_match:
                continue
            name = _decode_csharp_string_literal(name_match.group("value")).strip()
            if not (name.startswith("vrc_") or name.startswith("vrcforge_")):
                continue
            description_match = description_pattern.search(body)
            description = (
                _decode_csharp_string_literal(description_match.group("value")).strip()
                if description_match
                else f"VRCForge Unity tool {name}."
            )
            definitions[name] = {
                "name": name,
                "description": description,
                "structured_output": True,
                "requires_polling": False,
                "poll_action": "status",
                "max_poll_seconds": 0,
                "parameters": [],
                "source": str(source_path.relative_to(project_root)).replace("\\", "/"),
            }
    return [definitions[name] for name in sorted(definitions)]


def unity_repair_active_instance_for_registration(settings: Settings, project_root: Path) -> dict[str, Any]:
    status = build_unity_status_snapshot(settings)
    active = status.get("activeInstance") if isinstance(status.get("activeInstance"), dict) else {}
    if active and unity_instance_matches_project(active, project_root):
        return active
    instances = status.get("instances") if isinstance(status.get("instances"), list) else []
    for instance in instances:
        if isinstance(instance, dict) and unity_instance_matches_project(instance, project_root):
            return instance
    return {}


def register_vrcforge_unity_tools_from_project(
    project_root: Path,
    settings: Settings,
    phases: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    tool_definitions = discover_vrcforge_unity_tool_definitions(project_root)
    if not tool_definitions:
        detail = {"editorRoot": str(project_root / "Assets" / "VRCForge" / "Editor")}
        phases.append(
            _repair_phase(
                "unity_tool_registration",
                "warning",
                "VRCForge Unity tool declarations were not found in the selected project.",
                detail,
            )
        )
        return False, detail

    active_instance = unity_repair_active_instance_for_registration(settings, project_root)
    project_id = str(
        active_instance.get("cliInstanceId")
        or active_instance.get("hash")
        or settings.unity_mcp_instance
        or active_instance.get("project")
        or project_root.name
    ).strip()
    if not project_id:
        detail = {"toolCount": len(tool_definitions)}
        phases.append(
            _repair_phase(
                "unity_tool_registration",
                "warning",
                "Unity has no active MCP instance id, so VRCForge could not re-register tools.",
                detail,
            )
        )
        return False, detail

    payload = {
        "project_id": project_id,
        "project_hash": str(active_instance.get("hash") or project_id),
        "tools": [
            {key: value for key, value in definition.items() if key != "source"}
            for definition in tool_definitions
        ],
    }
    ok, response, error, status_code = post_unity_http_json(settings, "/register-tools", payload)
    detail = {
        "toolCount": len(tool_definitions),
        "projectId": project_id,
        "statusCode": status_code,
        "response": response,
        "error": error,
        "sources": [definition.get("source") for definition in tool_definitions[:10]],
    }
    phases.append(
        _repair_phase(
            "unity_tool_registration",
            "ok" if ok else "warning",
            f"Re-registered {len(tool_definitions)} VRCForge Unity tool(s) with the MCP server."
            if ok
            else f"VRCForge Unity tool re-registration failed: {error or response}",
            detail,
        )
    )
    return ok, detail


def _repair_process_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _powershell_json(script: str, timeout_seconds: int = 10) -> tuple[bool, Any, str]:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            **_repair_process_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)

    raw = (completed.stdout or "").strip()
    parsed = try_parse_json(raw) if raw else None
    if completed.returncode != 0:
        return False, parsed, (completed.stderr or completed.stdout or f"PowerShell exited {completed.returncode}").strip()
    return True, parsed, ""


def list_running_unity_processes() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    ok, payload, _error = _powershell_json(
        "@(Get-CimInstance Win32_Process -Filter \"Name = 'Unity.exe'\" "
        "| Select-Object ProcessId,ExecutablePath,CommandLine) | ConvertTo-Json -Depth 4",
        timeout_seconds=10,
    )
    if not ok or payload is None:
        return []
    raw_items = payload if isinstance(payload, list) else [payload]
    processes: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            process_id = int(item.get("ProcessId"))
        except (TypeError, ValueError):
            continue
        processes.append(
            {
                "processId": process_id,
                "executablePath": normalize_path_string(str(item.get("ExecutablePath") or "")),
                "commandLine": str(item.get("CommandLine") or ""),
            }
        )
    return processes


def _project_path_token(path: Path) -> str:
    return normalize_path_string(str(path)).replace("\\", "/").casefold().strip()


def unity_process_matches_project(process: dict[str, Any], project_root: Path) -> bool:
    command_line = str(process.get("commandLine") or "").replace("\\", "/").casefold()
    project_token = _project_path_token(project_root)
    if project_token and project_token in command_line:
        return True
    project_name = project_root.name.casefold()
    return bool(project_name and "-projectpath" in command_line and project_name in command_line)


def unity_instance_matches_project(instance: dict[str, Any], project_root: Path) -> bool:
    instance_path = normalize_path_string(str(instance.get("projectPath") or "")).casefold()
    project_path = normalize_path_string(str(project_root)).casefold()
    if instance_path and instance_path == project_path:
        return True
    project_name = project_root.name.casefold()
    candidates = [
        instance.get("project"),
        instance.get("projectName"),
        instance.get("cliInstanceId"),
        Path(str(instance.get("projectPath") or "")).name if instance.get("projectPath") else "",
    ]
    return any(str(candidate or "").strip().casefold() == project_name for candidate in candidates)


def find_mcp_for_unity_executable() -> Path | None:
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA", "").strip()
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if appdata:
        candidates.extend(sorted(Path(appdata).glob("Python/Python*/Scripts/mcp-for-unity.exe")))
        candidates.append(Path(appdata) / "Python" / "Python314" / "Scripts" / "mcp-for-unity.exe")
    if local_appdata:
        candidates.extend(sorted(Path(local_appdata).glob("Programs/Python/Python*/Scripts/mcp-for-unity.exe")))
    for command_name in ("mcp-for-unity.exe", "mcp-for-unity"):
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


def wait_for_mcp_health(settings: Settings, wait_seconds: int) -> bool:
    deadline = time.monotonic() + max(1, wait_seconds)
    while time.monotonic() < deadline:
        ok, _payload, _error, _status_code = fetch_unity_http_json(settings, "/health")
        if ok:
            return True
        time.sleep(1.0)
    return False


def ensure_unity_mcp_server_running(
    settings: Settings,
    phases: list[dict[str, Any]],
    wait_seconds: int,
    *,
    force_start: bool = False,
    preferred_executable: Path | None = None,
) -> bool:
    ok, _payload, error, _status_code = fetch_unity_http_json(settings, "/health")
    if ok and not force_start:
        phases.append(_repair_phase("mcp_server", "ok", "MCP server is already reachable.", {"url": f"{unity_http_base(settings)}/health"}))
        return True

    mcp_exe = preferred_executable if preferred_executable and preferred_executable.exists() else find_mcp_for_unity_executable()
    if mcp_exe is None:
        phases.append(
            _repair_phase(
                "mcp_server",
                "error",
                "MCP server is not reachable and mcp-for-unity.exe was not found.",
                {"error": error},
            )
        )
        return False

    try:
        subprocess.Popen(
            [
                str(mcp_exe),
                "--transport",
                "http",
                "--http-url",
                unity_http_base(settings),
                "--project-scoped-tools",
            ],
            cwd=str(ROOT_DIR),
            **_repair_process_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001
        phases.append(_repair_phase("mcp_server", "error", f"Failed to start MCP server: {exc}", {"executable": str(mcp_exe)}))
        return False

    if wait_for_mcp_health(settings, min(max(wait_seconds, 5), 45)):
        phases.append(_repair_phase("mcp_server", "ok", "MCP server started and is reachable.", {"executable": str(mcp_exe)}))
        return True

    phases.append(_repair_phase("mcp_server", "error", "MCP server was started but did not become reachable.", {"executable": str(mcp_exe)}))
    return False


def list_running_unity_mcp_processes(settings: Settings) -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    port = int(settings.unity_mcp_port or 8080)
    script = (
        "$port = "
        f"{port}; "
        "@(Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -notlike 'powershell*' -and "
        "$_.CommandLine -like '*mcp-for-unity*' -and "
        "$_.CommandLine -like ('*--http-url*:' + $port + '*') "
        "} | Select-Object ProcessId,Name,ExecutablePath,CommandLine) | ConvertTo-Json -Depth 4"
    )
    ok, payload, _error = _powershell_json(script, timeout_seconds=10)
    if not ok or payload is None:
        return []
    raw_items = payload if isinstance(payload, list) else [payload]
    processes: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            process_id = int(item.get("ProcessId"))
        except (TypeError, ValueError):
            continue
        processes.append(
            {
                "processId": process_id,
                "name": str(item.get("Name") or ""),
                "executablePath": normalize_path_string(str(item.get("ExecutablePath") or "")),
                "commandLine": str(item.get("CommandLine") or ""),
            }
        )
    return processes


def _preferred_mcp_executable_from_processes(processes: list[dict[str, Any]]) -> Path | None:
    for process in processes:
        executable = str(process.get("executablePath") or "").strip()
        if executable and Path(executable).name.casefold() == "mcp-for-unity.exe":
            candidate = Path(executable)
            try:
                if candidate.exists():
                    return candidate.resolve()
            except OSError:
                continue
    return None


def stop_unity_mcp_processes(processes: list[dict[str, Any]]) -> tuple[bool, dict[str, Any], str]:
    ids = sorted({int(process["processId"]) for process in processes if process.get("processId")})
    if not ids:
        return True, {"stopped": [], "stillRunning": []}, ""
    id_literal = ",".join(str(process_id) for process_id in ids)
    script = (
        f"$ids = @({id_literal}); "
        "$stopped = @(); "
        "foreach ($id in $ids) { "
        "  $proc = Get-Process -Id $id -ErrorAction SilentlyContinue; "
        "  if ($null -ne $proc) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue; $stopped += $id } "
        "}; "
        "Start-Sleep -Milliseconds 800; "
        "$still = @(); "
        "foreach ($id in $ids) { if ($null -ne (Get-Process -Id $id -ErrorAction SilentlyContinue)) { $still += $id } }; "
        "[pscustomobject]@{ ok=($still.Count -eq 0); stopped=$stopped; stillRunning=$still } | ConvertTo-Json -Depth 4; "
        "if ($still.Count -ne 0) { exit 2 }"
    )
    ok, payload, error = _powershell_json(script, timeout_seconds=20)
    return ok, payload if isinstance(payload, dict) else {"stopped": ids, "stillRunning": []}, error


def start_project_mcp_terminal_script(project_root: Path, settings: Settings, phases: list[dict[str, Any]], wait_seconds: int) -> bool | None:
    terminal_script = project_root / "Library" / "MCPForUnity" / "TerminalScripts" / "mcp-terminal.cmd"
    if not terminal_script.is_file():
        return None
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", str(terminal_script)],
            cwd=str(terminal_script.parent),
            **_repair_process_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001
        phases.append(
            _repair_phase(
                "mcp_server",
                "error",
                f"Failed to start MCP bridge from the Unity-generated terminal script: {exc}",
                {"script": str(terminal_script)},
            )
        )
        return False
    if wait_for_mcp_health(settings, min(max(wait_seconds, 5), 60)):
        phases.append(
            _repair_phase(
                "mcp_server",
                "ok",
                "MCP server started from the Unity-generated terminal script.",
                {"script": str(terminal_script)},
            )
        )
        return True
    phases.append(
        _repair_phase(
            "mcp_server",
            "error",
            "The Unity-generated MCP terminal script started but did not become reachable.",
            {"script": str(terminal_script)},
        )
    )
    return False


def restart_unity_mcp_server(settings: Settings, phases: list[dict[str, Any]], wait_seconds: int, project_root: Path | None = None) -> bool:
    processes = list_running_unity_mcp_processes(settings)
    preferred_executable = _preferred_mcp_executable_from_processes(processes)
    if processes:
        stopped, stop_detail, stop_error = stop_unity_mcp_processes(processes)
        phases.append(
            _repair_phase(
                "mcp_server_restart",
                "ok" if stopped else "error",
                "Restarted the MCP bridge process." if stopped else "Could not stop the existing MCP bridge process.",
                {
                    "processCount": len(processes),
                    "stopped": stop_detail,
                    "error": stop_error,
                },
            )
        )
        if not stopped:
            return False
    else:
        phases.append(_repair_phase("mcp_server_restart", "warning", "No MCP bridge process was found; VRCForge will try to start one."))
    if project_root is not None:
        started_from_project = start_project_mcp_terminal_script(project_root, settings, phases, wait_seconds)
        if started_from_project is not None:
            return started_from_project
    return ensure_unity_mcp_server_running(
        settings,
        phases,
        max(wait_seconds, 15),
        force_start=True,
        preferred_executable=preferred_executable,
    )


def wait_for_unity_project_registration(settings: Settings, project_root: Path, wait_seconds: int) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + max(1, wait_seconds)
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = build_unity_instances_diagnostics(settings)
        instances = latest.get("instances") if isinstance(latest.get("instances"), list) else []
        matched = next((instance for instance in instances if unity_instance_matches_project(instance, project_root)), None)
        if matched:
            cli_selector = str(matched.get("cliInstanceId") or matched.get("hash") or matched.get("project") or "").strip()
            if cli_selector:
                DASHBOARD_STATE.unity_instance = cli_selector
                settings.unity_mcp_instance = cli_selector
            return True, latest
        time.sleep(2.0)
    return False, latest


def unity_repair_tools_ready(summary: dict[str, Any]) -> bool:
    return bool(
        summary.get("unityInstanceRegistered")
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
    entries = read_jsonl_tail(LOCAL_LOG_PATH, 250)
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
    instances = build_unity_instances_diagnostics(settings)
    active_instance = instances.get("activeInstance") if isinstance(instances.get("activeInstance"), dict) else {}
    matched = bool(active_instance and unity_instance_matches_project(active_instance, project_root))
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


def wait_for_unity_tools_ready(settings: Settings, project_root: Path, wait_seconds: int) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + max(1, wait_seconds)
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = build_unity_status_snapshot(settings)
        latest = _unity_repair_status_summary(status)
        active = status.get("activeInstance") if isinstance(status.get("activeInstance"), dict) else {}
        if active and not unity_instance_matches_project(active, project_root):
            latest["selectedInstanceMatched"] = False
        if unity_repair_tools_ready(latest):
            return True, latest
        time.sleep(2.0)
    if not latest:
        latest = _unity_repair_status_summary(build_unity_status_snapshot(settings))
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
        if executable and unity_process_matches_project(process, project_root):
            candidates.append(("running-unity-project", Path(executable)))
    if len(running_processes) == 1:
        executable = str(running_processes[0].get("executablePath") or "").strip()
        if executable:
            candidates.append(("single-running-unity", Path(executable)))

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
    processes = list_running_unity_processes()
    matching = [process for process in processes if unity_process_matches_project(process, project_root)]
    if not processes:
        return True, "Unity is not currently running; launch can proceed.", {"processCount": 0}
    if not matching:
        return False, "No running Unity process clearly matched the selected project, so VRCForge did not close any editor.", {"processCount": len(processes)}

    results: list[dict[str, Any]] = []
    for process in matching:
        process_id = int(process["processId"])
        ok, payload, error = _powershell_json(
            "$proc = Get-Process -Id "
            f"{process_id} "
            "-ErrorAction SilentlyContinue; "
            "if ($null -eq $proc) { [pscustomobject]@{ ok=$true; exited=$true; reason='not_running' } | ConvertTo-Json -Depth 3; exit 0 }; "
            "$closed = $proc.CloseMainWindow(); "
            f"$exited = $proc.WaitForExit({max(1, int(timeout_seconds)) * 1000}); "
            "[pscustomobject]@{ ok=$exited; closeRequested=$closed; exited=$exited; pid=$proc.Id } | ConvertTo-Json -Depth 3; "
            "if (-not $exited) { exit 2 }",
            timeout_seconds=max(10, int(timeout_seconds) + 5),
        )
        result = payload if isinstance(payload, dict) else {"pid": process_id, "ok": ok, "error": error}
        results.append(result)
        if not ok or not bool(result.get("exited")):
            return False, "Unity did not exit after a normal close request. Save or close Unity manually, then Retry.", {"processes": results, "error": error}

    return True, "Unity closed cleanly.", {"processes": results}


def launch_unity_project(editor_path: Path, project_root: Path) -> tuple[bool, str]:
    try:
        subprocess.Popen([str(editor_path), "-projectPath", str(project_root)], cwd=str(ROOT_DIR))
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, ""


def resolve_unity_mcp_repair_project(project_path: str) -> Path:
    candidate_text = project_path.strip() or DASHBOARD_STATE.selected_project_path.strip()
    if not candidate_text:
        raise RuntimeError("No Unity project is selected. Select a Unity project first, then run Repair bridge.")
    candidate = Path(normalize_path_string(candidate_text))
    if not is_unity_project_path(candidate):
        raise RuntimeError("Selected path is not a Unity project root. Select the project root containing Assets, Packages, and ProjectSettings.")
    return candidate


def repair_unity_mcp_bridge_sync(request: UnityMcpRepairRequest) -> dict[str, Any]:
    phases: list[dict[str, Any]] = []
    generated_at = utc_now_iso()
    try:
        project_root = resolve_unity_mcp_repair_project(request.project_path)
        settings = load_dashboard_settings(ConnectionRequest(settings_path=str(DASHBOARD_STATE.settings_path)))
        settings.unity_mcp_timeout_seconds = min(settings.unity_mcp_timeout_seconds, 3)
        settings.unity_mcp_retries = 1
        settings.unity_mcp_retry_backoff_seconds = 0.0
        registration_wait = min(request.wait_seconds, 30 if request.allow_unity_relaunch else 10)
        tools_wait = min(request.wait_seconds, 60 if request.allow_unity_relaunch else 10)
        short_tools_wait = min(request.wait_seconds, 30 if request.allow_unity_relaunch else 6)
        restart_wait = min(request.wait_seconds, 90 if request.allow_unity_relaunch else 20)
        recent_execution_error = recent_unity_mcp_execution_error()
        if recent_execution_error and not request.allow_unity_relaunch:
            before = build_unity_repair_quick_summary(settings, project_root)
            phases.append(
                _repair_phase(
                    "unity_recent_execution_disconnect",
                    "warning",
                    "Recent Unity MCP execution disconnect was found; VRCForge will not claim the bridge is healthy.",
                    recent_execution_error,
                )
            )
            phases.append(
                _repair_phase(
                    "unity_relaunch",
                    "skipped",
                    "Allow a graceful Unity relaunch or restart the MCP bridge from Unity, then retry.",
                    {"unityEditorPathResolved": bool(resolve_unity_editor_path_for_repair(project_root, request.unity_editor_path)[0])},
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
        before_status = build_unity_status_snapshot(settings)
        before = _unity_repair_status_summary(before_status)

        if unity_repair_tools_ready(before) and unity_repair_execution_ready(settings, before, phases, "unity_execution_probe_initial"):
            phases.append(_repair_phase("already_healthy", "ok", "Unity bridge is already registered and VRCForge tools are available."))
            return {
                "ok": True,
                "schema": "vrcforge.unity_mcp_repair.v1",
                "status": "healthy",
                "generatedAt": generated_at,
                "projectPath": str(project_root),
                "phases": phases,
                "before": before,
                "after": before,
            }

        if not ensure_unity_mcp_server_running(settings, phases, request.wait_seconds):
            after_status = build_unity_status_snapshot(settings)
            return {
                "ok": False,
                "schema": "vrcforge.unity_mcp_repair.v1",
                "status": "failed",
                "generatedAt": generated_at,
                "projectPath": str(project_root),
                "phases": phases,
                "before": before,
                "after": _unity_repair_status_summary(after_status),
            }

        registered, instance_payload = wait_for_unity_project_registration(settings, project_root, registration_wait)
        if registered:
            phases.append(_repair_phase("unity_registration", "ok", "Unity registered with the MCP server.", instance_payload))
            tools_ready, tools_after = wait_for_unity_tools_ready(settings, project_root, tools_wait)
            phases.append(
                _repair_phase(
                    "unity_tools",
                    "ok" if tools_ready else "warning",
                    "Unity MCP tools are ready." if tools_ready else unity_repair_tools_message(tools_after),
                    tools_after,
                )
            )
            if tools_ready and unity_repair_execution_ready(settings, tools_after, phases, "unity_execution_probe"):
                return {
                    "ok": True,
                    "schema": "vrcforge.unity_mcp_repair.v1",
                    "status": "recovered",
                    "generatedAt": generated_at,
                    "projectPath": str(project_root),
                    "phases": phases,
                    "before": before,
                    "after": tools_after,
                }
            registered_tools, _registration_detail = register_vrcforge_unity_tools_from_project(project_root, settings, phases)
            if registered_tools:
                tools_ready_after_registration, registration_after = wait_for_unity_tools_ready(settings, project_root, short_tools_wait)
                phases.append(
                    _repair_phase(
                        "unity_tools_after_registration",
                        "ok" if tools_ready_after_registration else "warning",
                        "Unity MCP tools are ready after VRCForge tool re-registration."
                        if tools_ready_after_registration
                        else unity_repair_tools_message(registration_after),
                        registration_after,
                    )
                )
                if tools_ready_after_registration and unity_repair_execution_ready(
                    settings,
                    registration_after,
                    phases,
                    "unity_execution_probe_after_registration",
                ):
                    return {
                        "ok": True,
                        "schema": "vrcforge.unity_mcp_repair.v1",
                        "status": "recovered",
                        "generatedAt": generated_at,
                        "projectPath": str(project_root),
                        "phases": phases,
                        "before": before,
                        "after": registration_after,
                    }
            if restart_unity_mcp_server(settings, phases, restart_wait, project_root):
                registered_after_restart, restart_instances = wait_for_unity_project_registration(settings, project_root, registration_wait)
                phases.append(
                    _repair_phase(
                        "unity_registration_after_mcp_restart",
                        "ok" if registered_after_restart else "warning",
                        "Unity registered after MCP bridge restart."
                        if registered_after_restart
                        else "MCP bridge restarted, but Unity did not register before the timeout.",
                        restart_instances,
                    )
                )
                if registered_after_restart:
                    tools_ready_after_restart, restart_after = wait_for_unity_tools_ready(settings, project_root, tools_wait)
                    phases.append(
                        _repair_phase(
                            "unity_tools_after_mcp_restart",
                            "ok" if tools_ready_after_restart else "warning",
                            "Unity MCP tools are ready after MCP bridge restart."
                            if tools_ready_after_restart
                            else unity_repair_tools_message(restart_after),
                            restart_after,
                        )
                    )
                    if tools_ready_after_restart and unity_repair_execution_ready(
                        settings,
                        restart_after,
                        phases,
                        "unity_execution_probe_after_mcp_restart",
                    ):
                        return {
                            "ok": True,
                            "schema": "vrcforge.unity_mcp_repair.v1",
                            "status": "recovered",
                            "generatedAt": generated_at,
                            "projectPath": str(project_root),
                            "phases": phases,
                            "before": before,
                            "after": restart_after,
                        }
                    registered_tools_after_restart, _restart_registration_detail = register_vrcforge_unity_tools_from_project(project_root, settings, phases)
                    if registered_tools_after_restart:
                        tools_ready_after_restart_registration, restart_registration_after = wait_for_unity_tools_ready(
                            settings,
                            project_root,
                            short_tools_wait,
                        )
                        phases.append(
                            _repair_phase(
                                "unity_tools_after_mcp_restart_registration",
                                "ok" if tools_ready_after_restart_registration else "warning",
                                "Unity MCP tools are ready after bridge restart and VRCForge tool re-registration."
                                if tools_ready_after_restart_registration
                                else unity_repair_tools_message(restart_registration_after),
                                restart_registration_after,
                            )
                        )
                        if tools_ready_after_restart_registration and unity_repair_execution_ready(
                            settings,
                            restart_registration_after,
                            phases,
                            "unity_execution_probe_after_mcp_restart_registration",
                        ):
                            return {
                                "ok": True,
                                "schema": "vrcforge.unity_mcp_repair.v1",
                                "status": "recovered",
                                "generatedAt": generated_at,
                                "projectPath": str(project_root),
                                "phases": phases,
                                "before": before,
                                "after": restart_registration_after,
                            }
        else:
            phases.append(_repair_phase("unity_registration", "warning", "MCP server is reachable, but Unity did not register yet.", instance_payload))

        editor_path, editor_source = resolve_unity_editor_path_for_repair(project_root, request.unity_editor_path)
        if not request.allow_unity_relaunch:
            phases.append(
                _repair_phase(
                    "unity_relaunch",
                    "skipped",
                    "Unity relaunch was not requested. Run Repair bridge from Doctor to allow a graceful relaunch.",
                    {"unityEditorPathResolved": bool(editor_path), "source": editor_source},
                )
            )
            after_status = build_unity_status_snapshot(settings)
            after = _unity_repair_status_summary(after_status)
            return {
                "ok": False,
                "schema": "vrcforge.unity_mcp_repair.v1",
                "status": "needs_user_action",
                "generatedAt": generated_at,
                "projectPath": str(project_root),
                "phases": phases,
                "before": before,
                "after": after,
            }

        if editor_path is None:
            phases.append(
                _repair_phase(
                    "unity_editor",
                    "error",
                    "Unity editor path could not be resolved. Configure the Unity editor path or open this project once, then retry.",
                    {"source": editor_source},
                )
            )
            after_status = build_unity_status_snapshot(settings)
            return {
                "ok": False,
                "schema": "vrcforge.unity_mcp_repair.v1",
                "status": "needs_user_action",
                "generatedAt": generated_at,
                "projectPath": str(project_root),
                "phases": phases,
                "before": before,
                "after": _unity_repair_status_summary(after_status),
            }

        closed, close_message, close_detail = close_unity_project_gracefully(project_root, request.close_timeout_seconds)
        phases.append(_repair_phase("unity_close", "ok" if closed else "warning", close_message, close_detail))
        if not closed:
            after_status = build_unity_status_snapshot(settings)
            return {
                "ok": False,
                "schema": "vrcforge.unity_mcp_repair.v1",
                "status": "needs_user_action",
                "generatedAt": generated_at,
                "projectPath": str(project_root),
                "phases": phases,
                "before": before,
                "after": _unity_repair_status_summary(after_status),
            }

        launched, launch_error = launch_unity_project(editor_path, project_root)
        if not launched:
            phases.append(_repair_phase("unity_launch", "error", f"Unity launch failed: {launch_error}", {"unityEditorPath": str(editor_path)}))
            after_status = build_unity_status_snapshot(settings)
            return {
                "ok": False,
                "schema": "vrcforge.unity_mcp_repair.v1",
                "status": "failed",
                "generatedAt": generated_at,
                "projectPath": str(project_root),
                "phases": phases,
                "before": before,
                "after": _unity_repair_status_summary(after_status),
            }

        phases.append(_repair_phase("unity_launch", "ok", "Unity launch requested for the selected project.", {"unityEditorPath": str(editor_path), "source": editor_source}))
        registered_after_launch, launch_instances = wait_for_unity_project_registration(settings, project_root, request.wait_seconds)
        phases.append(
            _repair_phase(
                "unity_registration_after_launch",
                "ok" if registered_after_launch else "error",
                "Unity registered after relaunch." if registered_after_launch else "Unity did not register before the timeout.",
                launch_instances,
            )
        )
        tools_ready_after_launch = False
        after: dict[str, Any] = {}
        if registered_after_launch:
            tools_ready_after_launch, after = wait_for_unity_tools_ready(settings, project_root, min(request.wait_seconds, 90))
            phases.append(
                _repair_phase(
                    "unity_tools_after_launch",
                    "ok" if tools_ready_after_launch else "error",
                    "Unity MCP tools are ready after relaunch." if tools_ready_after_launch else unity_repair_tools_message(after),
                    after,
                )
            )
            if not tools_ready_after_launch:
                registered_tools_after_launch, _launch_registration_detail = register_vrcforge_unity_tools_from_project(project_root, settings, phases)
                if registered_tools_after_launch:
                    tools_ready_after_launch_registration, after = wait_for_unity_tools_ready(settings, project_root, min(request.wait_seconds, 30))
                    phases.append(
                        _repair_phase(
                            "unity_tools_after_launch_registration",
                            "ok" if tools_ready_after_launch_registration else "error",
                            "Unity MCP tools are ready after relaunch and VRCForge tool re-registration."
                            if tools_ready_after_launch_registration
                            else unity_repair_tools_message(after),
                            after,
                        )
                    )
                    tools_ready_after_launch = tools_ready_after_launch_registration
        else:
            after = _unity_repair_status_summary(build_unity_status_snapshot(settings))
        recovered = unity_repair_execution_ready(settings, after, phases, "unity_execution_probe_after_launch") if unity_repair_tools_ready(after) else False
        return {
            "ok": bool(recovered),
            "schema": "vrcforge.unity_mcp_repair.v1",
            "status": "recovered" if recovered else ("needs_user_action" if registered_after_launch or tools_ready_after_launch else "failed"),
            "generatedAt": generated_at,
            "projectPath": str(project_root),
            "phases": phases,
            "before": before,
            "after": after,
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
        components["mcpPackageConfigured"] = health_component("unknown", "Unity MCP package status is unknown until a project is selected.", "")
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

        mcp_value = dependencies.get("com.coplaydev.unity-mcp")
        components["mcpPackageConfigured"] = health_component(
            "ok" if mcp_value else "error",
            "Unity MCP package dependency is configured." if mcp_value else "Unity MCP package dependency is missing.",
            {"manifestPath": str(manifest_path), "dependency": mcp_value or ""},
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
    health = read_health()
    api_config = serialize_api_config(include_secret=include_secret)
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
            "effective": build_effective_model_summary(),
        },
        "projects": project_snapshot_payload(),
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


def project_snapshot_payload() -> dict[str, Any]:
    projects = discover_projects(DASHBOARD_STATE.project_roots, include_external=True)
    return {
        "selectedProjectPath": DASHBOARD_STATE.selected_project_path,
        "unityEditorPath": DASHBOARD_STATE.unity_editor_path,
        "projects": projects,
    }


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
            project["cliInstanceId"] = active_instance.get("cliInstanceId") or active_instance.get("hash") or active_instance.get("project") or ""
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
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "VRChatCreatorCompanion" / "settings.json",
        Path(os.environ.get("LOCALAPPDATA", "")) / "VRChatCreatorCompanion" / "vrc-get-settings.json",
        Path(os.environ.get("APPDATA", "")) / "VRChatCreatorCompanion" / "settings.json",
        Path(os.environ.get("APPDATA", "")) / "VRChatCreatorCompanion" / "vrc-get-settings.json",
    ]
    return discover_projects_from_settings_files(candidates)


def discover_alcom_projects() -> list[str]:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "VRChatCreatorCompanion" / "vrc-get-settings.json",
        Path(os.environ.get("APPDATA", "")) / "VRChatCreatorCompanion" / "vrc-get-settings.json",
        Path(os.environ.get("LOCALAPPDATA", "")) / "ALCOM" / "settings.json",
        Path(os.environ.get("APPDATA", "")) / "ALCOM" / "settings.json",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Alcom" / "settings.json",
        Path(os.environ.get("APPDATA", "")) / "Alcom" / "settings.json",
        Path(os.environ.get("LOCALAPPDATA", "")) / "vrc-get" / "settings.json",
        Path(os.environ.get("APPDATA", "")) / "vrc-get" / "settings.json",
    ]
    return discover_projects_from_settings_files(candidates)


def discover_projects_from_settings_files(candidates: list[Path]) -> list[str]:
    projects: list[str] = []
    for settings_path in candidates:
        if not settings_path.exists():
            continue
        raw_text = ""
        try:
            raw_text = settings_path.read_text(encoding="utf-8-sig")
            payload = json.loads(raw_text)
        except Exception:  # noqa: BLE001
            projects.extend(extract_windows_paths_from_text(raw_text or settings_path.read_text(errors="ignore")))
            continue
        projects.extend(extract_project_paths_from_json(payload))
    return sorted(
        {
            normalize_path_string(project)
            for project in projects
            if project and is_unity_project_path(Path(normalize_path_string(project)))
        },
        key=str.casefold,
    )


def extract_project_paths_from_json(payload: Any) -> list[str]:
    paths: list[str] = []

    def visit(value: Any, key_hint: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = str(key).casefold()
                if lowered in {"userprojects", "projects", "recentprojects", "knownprojects"}:
                    visit(item, lowered)
                elif lowered in {"path", "projectpath", "project", "directorypath"}:
                    if isinstance(item, str) and item.strip():
                        paths.append(normalize_path_string(item))
                elif key_hint in {"userprojects", "projects", "recentprojects", "knownprojects"}:
                    visit(item, key_hint)
        elif isinstance(value, list):
            for item in value:
                visit(item, key_hint)
        elif isinstance(value, str) and key_hint in {"userprojects", "projects", "recentprojects", "knownprojects"}:
            paths.append(normalize_path_string(value))

    visit(payload)
    return paths


def extract_windows_paths_from_text(value: str) -> list[str]:
    import re

    paths: list[str] = []
    for match in re.finditer(r"[A-Za-z]:\\\\[^\"\\r\\n,]+(?:\\\\[^\"\\r\\n,]+)*", value):
        candidate = match.group(0).replace("\\\\", "\\").strip()
        if "\\unity" in candidate.casefold() or "\\projects" in candidate.casefold():
            paths.append(normalize_path_string(candidate))
    return paths


def discover_unity_hub_projects() -> list[dict[str, str]]:
    projects: list[dict[str, str]] = []
    seen: set[str] = set()
    for hub_projects in [
        Path(os.environ.get("APPDATA", "")) / "UnityHub" / "projects-v1.json",
        Path(os.environ.get("LOCALAPPDATA", "")) / "UnityHub" / "projects-v1.json",
    ]:
        if not hub_projects.exists():
            continue
        try:
            payload = json.loads(hub_projects.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            continue
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            path = normalize_path_string(str(value.get("path") or key or "").strip())
            if not path or not is_unity_project_path(Path(path)):
                continue
            key_text = path.casefold()
            if key_text in seen:
                continue
            seen.add(key_text)
            projects.append(
                {
                    "name": str(value.get("title") or value.get("name") or Path(path).name),
                    "path": path,
                    "editorVersion": str(value.get("version") or value.get("unityVersion") or "Unknown"),
                }
            )

    for project_root in discover_unity_hub_project_roots():
        if not project_root.exists():
            continue
        for child in sorted(project_root.iterdir(), key=lambda item: item.name.casefold()):
            if not child.is_dir() or not is_unity_project_path(child):
                continue
            path = normalize_path_string(str(child))
            key_text = path.casefold()
            if key_text in seen:
                continue
            seen.add(key_text)
            projects.append(
                {
                    "name": child.name,
                    "path": path,
                    "editorVersion": parse_editor_version(child / "ProjectSettings" / "ProjectVersion.txt"),
                }
            )
    return projects


def discover_unity_hub_project_roots() -> list[Path]:
    roots: list[Path] = []
    for project_dir in [
        Path(os.environ.get("APPDATA", "")) / "UnityHub" / "projectDir.json",
        Path(os.environ.get("LOCALAPPDATA", "")) / "UnityHub" / "projectDir.json",
    ]:
        if not project_dir.exists():
            continue
        try:
            payload = json.loads(project_dir.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            continue
        directory = payload.get("directoryPath") if isinstance(payload, dict) else ""
        if isinstance(directory, str) and directory.strip():
            roots.append(Path(normalize_path_string(directory)))
    return roots


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
    if not manifest_path.exists():
        return False

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return False

    dependencies = manifest.get("dependencies") or {}
    return "com.coplaydev.unity-mcp" in dependencies


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


def load_initial_dashboard_api_config() -> DashboardApiConfig:
    settings_path = RUNTIME_SETTINGS_PATH
    settings = load_settings(settings_path)
    config_document = load_config_document()
    api_section = config_document.get("api") or {}

    provider = normalize_provider_name(api_section.get("provider") or settings.llm_provider or DEFAULT_LLM_PROVIDER)
    defaults = get_provider_defaults(provider)
    api_key = str(api_section.get("api_key") or settings.llm_api_key).strip()
    base_url = normalize_base_url(api_section.get("base_url"), provider, defaults["base_url"])
    model = str(api_section.get("model") or settings.llm_model or defaults["model"]).strip() or defaults["model"]

    return DashboardApiConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )


def load_config_document() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}

    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    return payload if isinstance(payload, dict) else {}


def normalize_api_config_request(request: ApiConfigRequest) -> DashboardApiConfig:
    provider = normalize_provider_name(request.provider)
    defaults = get_provider_defaults(provider)
    model = str(request.model or defaults["model"]).strip() or defaults["model"]
    base_url = normalize_base_url(request.base_url, provider, defaults["base_url"])

    return DashboardApiConfig(
        provider=provider,
        api_key=request.api_key.strip(),
        base_url=base_url,
        model=model,
    )


def fetch_provider_models(config: DashboardApiConfig) -> list[dict[str, str]]:
    if provider_requires_api_key(config.provider) and not config.api_key.strip():
        raise RuntimeError(f"{provider_display_name(config.provider)} API key is empty. Enter an API key before loading models.")

    if config.provider == "gemini":
        return fetch_google_ai_studio_models(config)
    if config.provider == "vertexai":
        return fetch_vertex_ai_models(config)
    if config.provider == "anthropic":
        return fetch_anthropic_models(config)
    return fetch_openai_compatible_models(config)


def run_provider_test_sync(request: ProviderTestRequest) -> dict[str, Any]:
    config = normalize_api_config_request(request)
    capability = request.capability
    provider_label = provider_display_name(config.provider)
    if provider_requires_api_key(config.provider) and not config.api_key.strip():
        return {
            "ok": False,
            "status": "error",
            "capability": capability,
            "provider": config.provider,
            "providerLabel": provider_label,
            "model": config.model,
            "message": f"{provider_label} API key is empty.",
        }
    if capability == "vision":
        return {
            "ok": True,
            "status": "skipped",
            "skipped": True,
            "capability": capability,
            "provider": config.provider,
            "providerLabel": provider_label,
            "model": config.model,
            "message": "Vision test requires an explicit user-selected image; no Unity screenshot or project asset was sent.",
        }
    prompt = (
        "Return exactly: VRCForge provider test OK"
        if capability == "text"
        else 'Return compact JSON exactly like {"ok":true,"name":"vrcforge"}.'
    )
    try:
        text = _run_provider_text_probe(config, prompt, structured=capability == "structured")
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": "error",
            "capability": capability,
            "provider": config.provider,
            "providerLabel": provider_label,
            "model": config.model,
            "message": str(exc),
        }
    structured_ok = True
    if capability == "structured":
        try:
            parsed = json.loads(extract_json_block(text) or text)
            structured_ok = isinstance(parsed, dict) and bool(parsed.get("ok"))
        except Exception:  # noqa: BLE001
            structured_ok = False
    return {
        "ok": structured_ok,
        "status": "ok" if structured_ok else "warning",
        "capability": capability,
        "provider": config.provider,
        "providerLabel": provider_label,
        "model": config.model,
        "message": "Provider test succeeded." if structured_ok else "Provider responded, but structured JSON did not validate.",
        "responsePreview": text[:240],
    }


def _run_provider_text_probe(config: DashboardApiConfig, prompt: str, structured: bool = False) -> str:
    if config.provider in {"gemini", "vertexai"}:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("The google-genai package is not installed.") from exc
        if config.provider == "vertexai":
            project, location = resolve_vertex_project_location(config.base_url)
            client = genai.Client(vertexai=True, project=project, location=location)
        else:
            client = genai.Client(api_key=config.api_key)
        response = client.models.generate_content(model=config.model, contents=prompt)
        return str(getattr(response, "text", "") or response)
    if config.provider == "anthropic":
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("The anthropic package is not installed.") from exc
        client = anthropic.Anthropic(api_key=config.api_key)
        response = client.messages.create(
            model=config.model,
            max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = getattr(response, "content", []) or []
        texts = [str(getattr(part, "text", "") or "") for part in parts]
        return "\n".join(text for text in texts if text).strip()
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The openai package is not installed.") from exc
    if not config.base_url.strip() and config.provider not in {"openai"}:
        raise RuntimeError("Base URL is empty.")
    kwargs: dict[str, Any] = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 64,
    }
    if structured:
        kwargs["response_format"] = {"type": "json_object"}
    client = OpenAI(api_key=config.api_key or "ollama", base_url=config.base_url or None, timeout=30.0)
    response = client.chat.completions.create(**kwargs)
    choices = getattr(response, "choices", []) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    return str(getattr(message, "content", "") or "")


def fetch_openai_compatible_models(config: DashboardApiConfig) -> list[dict[str, str]]:
    if not config.base_url.strip():
        raise RuntimeError("Base URL is empty. Enter a provider API endpoint before loading models.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The openai package is not installed, so OpenAI-compatible model listing is unavailable.") from exc

    client = OpenAI(api_key=config.api_key or "ollama", base_url=config.base_url, timeout=20.0)
    try:
        response = client.models.list()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{provider_display_name(config.provider)} model list request failed: {exc}") from exc

    return normalize_provider_model_list(response, provider_display_name(config.provider))


def fetch_google_ai_studio_models(config: DashboardApiConfig) -> list[dict[str, str]]:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("The google-genai package is not installed, so Google AI Studio model listing is unavailable.") from exc

    client = genai.Client(api_key=config.api_key)
    try:
        response = client.models.list()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Google AI Studio model list request failed: {exc}") from exc

    return normalize_provider_model_list(response, "Google AI Studio")


def fetch_vertex_ai_models(config: DashboardApiConfig) -> list[dict[str, str]]:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("The google-genai package is not installed, so Google Vertex AI model listing is unavailable.") from exc

    project, location = resolve_vertex_project_location(config.base_url)
    try:
        client = genai.Client(vertexai=True, project=project, location=location)
        response = client.models.list()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Google Vertex AI model list request failed for project '{project}' / location '{location}': {exc}"
        ) from exc

    return normalize_provider_model_list(response, "Google Vertex AI")


def resolve_vertex_project_location(value: str) -> tuple[str, str]:
    settings = load_settings(
        RUNTIME_SETTINGS_PATH,
        llm_override={
            "provider": "vertexai",
            "api_key": "",
            "base_url": value,
            "model": get_provider_defaults("vertexai")["model"],
        },
    )
    from vrchat_blendshape_agent import resolve_vertex_ai_project_location

    return resolve_vertex_ai_project_location(settings.llm_base_url)


def fetch_anthropic_models(config: DashboardApiConfig) -> list[dict[str, str]]:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("The anthropic package is not installed, so Anthropic model listing is unavailable.") from exc

    client = anthropic.Anthropic(api_key=config.api_key)
    models_api = getattr(client, "models", None)
    list_models = getattr(models_api, "list", None)
    if not callable(list_models):
        raise RuntimeError("The installed Anthropic SDK does not expose models.list(). Use manual model input.")

    try:
        try:
            response = list_models(limit=100)
        except TypeError:
            response = list_models()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Anthropic model list request failed: {exc}") from exc

    return normalize_provider_model_list(response, "Anthropic")


def normalize_provider_model_list(response: Any, provider_label: str) -> list[dict[str, str]]:
    raw_items: Any = response
    if isinstance(response, dict):
        raw_items = response.get("data") or response.get("models") or []
    else:
        raw_items = getattr(response, "data", response)

    try:
        items = list(raw_items or [])
    except TypeError:
        items = []

    models_by_id: dict[str, dict[str, str]] = {}
    for item in items:
        if isinstance(item, dict):
            model_id = item.get("id") or item.get("name")
        else:
            model_id = getattr(item, "id", None) or getattr(item, "name", None)

        if not model_id:
            continue

        model_id = str(model_id).strip()
        if model_id:
            models_by_id.setdefault(model_id, {"id": model_id, "label": model_id})

    models = sorted(models_by_id.values(), key=lambda model: model["id"].casefold())
    if not models:
        raise RuntimeError(f"{provider_label} returned no models.")
    return models


def save_dashboard_api_config(config: DashboardApiConfig) -> None:
    payload = {
        "api": {
            "provider": config.provider,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "model": config.model,
        }
    }
    atomic_write_json(CONFIG_PATH, payload)


def serialize_api_config(include_secret: bool) -> dict[str, Any]:
    config = DASHBOARD_API_CONFIG or load_initial_dashboard_api_config()
    return {
        "provider": config.provider,
        "providerLabel": provider_display_name(config.provider),
        "api_key": config.api_key if include_secret else mask_secret(config.api_key),
        "apiKeyPresent": bool(config.api_key),
        "base_url": config.base_url,
        "model": config.model,
        "usesBaseUrl": config.provider not in {"anthropic", "gemini"},
        "authHeader": provider_auth_label(config.provider),
        "apiKeyRequired": provider_requires_api_key(config.provider),
    }


def build_effective_model_summary() -> dict[str, Any]:
    config = DASHBOARD_API_CONFIG or load_initial_dashboard_api_config()
    return {
        "provider": config.provider,
        "providerLabel": provider_display_name(config.provider),
        "model": config.model,
        "baseUrl": config.base_url,
        "authHeader": provider_auth_label(config.provider),
        "apiKeyRequired": provider_requires_api_key(config.provider),
    }


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


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * max(len(value) - 8, 4)}{value[-4:]}"


def load_initial_dashboard_state() -> DashboardState:
    settings_path = RUNTIME_SETTINGS_PATH
    settings = load_settings(
        settings_path,
        llm_override=serialize_api_config(include_secret=True) if DASHBOARD_API_CONFIG is not None else None,
    )
    raw = json.loads(settings_path.read_text(encoding="utf-8-sig")) if settings_path.exists() else {}
    dashboard_settings = raw.get("dashboard") or {}

    project_roots = [Path(path) for path in dashboard_settings.get("project_roots", [])]
    unity_editor_path = str(dashboard_settings.get("unity_editor_path", "")).strip()
    status_push_interval_seconds = float(dashboard_settings.get("status_push_interval_seconds", 2.5))

    return DashboardState(
        settings_path=settings_path,
        project_roots=project_roots,
        unity_editor_path=unity_editor_path,
        status_push_interval_seconds=status_push_interval_seconds,
        selected_project_path="",
        unity_host=settings.unity_mcp_host,
        unity_port=settings.unity_mcp_port,
        unity_instance=settings.unity_mcp_instance,
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


APP_AUTH_PREFIXES = (
    "/api/app",
    "/api/config",
    "/api/models",
)
APP_LOOPBACK_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def app_route_requires_auth(request: Request) -> bool:
    path = request.url.path
    if not any(path == prefix or path.startswith(prefix + "/") for prefix in APP_AUTH_PREFIXES):
        return False
    if not APP_AUTH_REQUIRED and request.method.upper() == "GET":
        return False
    return True


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
    return extract_bearer_token_from_values(request.headers, request.query_params)


def extract_bearer_token_from_values(headers: Any, query_params: Any) -> str:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(query_params.get("app_token") or "")


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


def preview_agent_blendshape_apply(params: dict[str, Any]) -> dict[str, Any]:
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


def request_agent_roslyn_advanced(params: dict[str, Any]) -> dict[str, Any]:
    if not params.get("code"):
        raise RuntimeError("code is required for Roslyn Advanced Power Mode.")
    arguments = dict(params)
    if arguments.get("confirmAdvancedPowerMode") is not True:
        raise RuntimeError("confirmAdvancedPowerMode=true is required for Roslyn Advanced Power Mode requests.")
    return AGENT_GATEWAY.create_apply_request(
        {
            "target_tool": "vrcforge_roslyn_advanced",
            "arguments": arguments,
            "reason": "Roslyn Advanced Power Mode request from external agent.",
            "preview": {
                "codePreview": str(params.get("code") or "")[:240],
                "requiresUnityDialog": True,
                "requiresConfirmAdvancedPowerMode": True,
            },
        }
    )


def read_agent_roslyn_status(params: dict[str, Any]) -> dict[str, Any]:
    settings = load_dashboard_settings(build_agent_connection_request(params))
    result = invoke_unity_mcp(settings, "vrc_check_roslyn_status", {})
    return {"ok": True, "result": serialize_result(result)}


def read_agent_compile_errors(params: dict[str, Any]) -> dict[str, Any]:
    settings = load_dashboard_settings(build_agent_connection_request(params))
    arguments: dict[str, Any] = {}
    if params.get("maxErrors") is not None:
        arguments["maxErrors"] = int(params["maxErrors"])
    if params.get("includeConsoleFallback") is not None:
        arguments["includeConsoleFallback"] = bool(params["includeConsoleFallback"])
    result = invoke_unity_mcp(settings, "vrc_get_compile_errors", arguments)
    return {"ok": True, "result": serialize_result(result)}


def acknowledge_unity_roslyn_risk_sync() -> dict[str, Any]:
    settings = load_dashboard_settings(build_agent_connection_request({}))
    result = invoke_unity_mcp(settings, "vrc_acknowledge_roslyn_risk", {})
    return {
        "ok": result.exit_code == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def prepare_unity_checkpoint_sync(project_root: Path) -> dict[str, Any]:
    settings = load_dashboard_settings(build_agent_connection_request({}))
    settings.unity_mcp_timeout_seconds = max(int(settings.unity_mcp_timeout_seconds or 30), 180)
    result = invoke_unity_mcp(
        settings,
        "vrc_prepare_checkpoint",
        {"projectPath": str(project_root)},
    )
    return {
        "ok": result.exit_code == 0,
        "projectPath": str(project_root),
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def reload_unity_checkpoint_sync(project_root: Path) -> dict[str, Any]:
    settings = load_dashboard_settings(build_agent_connection_request({}))
    settings.unity_mcp_timeout_seconds = max(int(settings.unity_mcp_timeout_seconds or 30), 180)
    result = invoke_unity_mcp(
        settings,
        "vrc_reload_after_checkpoint_restore",
        {"projectPath": str(project_root)},
    )
    return {
        "ok": result.exit_code == 0,
        "projectPath": str(project_root),
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def unity_mcp_write_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    tool_name = str(params.get("tool_name") or params.get("toolName") or "").strip()
    if not tool_name:
        return {"ok": False, "error": "toolName is required."}
    if tool_name in {"vrc_prepare_checkpoint", "vrc_reload_after_checkpoint_restore"}:
        return {"ok": False, "error": f"Internal checkpoint tool cannot be invoked through the generic write wrapper: {tool_name}"}
    if tool_name in {"vrc_execute_roslyn", "execute_code"}:
        return {"ok": False, "error": f"Advanced code execution must use the dedicated Roslyn permission path: {tool_name}"}
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else params.get("params")
    if not isinstance(arguments, dict):
        arguments = {}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    result = invoke_unity_mcp(settings, tool_name, arguments)
    return {"ok": result.exit_code == 0, "toolName": tool_name, "result": serialize_result(result)}


def execute_agent_roslyn_advanced(params: dict[str, Any]) -> dict[str, Any]:
    if params.get("confirmAdvancedPowerMode") is not True:
        raise RuntimeError("confirmAdvancedPowerMode=true is required.")
    settings = load_dashboard_settings(ConnectionRequest(**params))
    snippet_timeout = max(1, min(int(params.get("timeoutSeconds", 10)), 30))
    settings.unity_mcp_timeout_seconds = max(settings.unity_mcp_timeout_seconds, snippet_timeout + 45, 75)
    result = invoke_unity_mcp(
        settings,
        "vrc_execute_roslyn",
        {
            "code": params.get("code") or "",
            "confirmAdvancedPowerMode": True,
            "enforceWriteDefaultsOn": params.get("enforceWriteDefaultsOn", True),
            "targetAvatarPaths": params.get("targetAvatarPaths") or [],
            "timeoutSeconds": snippet_timeout,
        },
    )
    return {"ok": True, "result": serialize_result(result)}


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


def detect_addon_package(project_path: Path | None, package_ids: list[str]) -> dict[str, Any]:
    info: dict[str, Any] = {"installed": False, "packageId": "", "version": "", "source": ""}
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
    for manifest_name, source in (("vpm-manifest.json", "vpm"), ("manifest.json", "upm")):
        manifest_path = packages_dir / manifest_name
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        sections = [manifest.get("locked"), manifest.get("dependencies")] if source == "vpm" else [manifest.get("dependencies")]
        for section in sections:
            if not isinstance(section, dict):
                continue
            for package_id in package_ids:
                entry = section.get(package_id)
                if entry is None:
                    continue
                version = entry.get("version") if isinstance(entry, dict) else entry
                info.update({"installed": True, "packageId": package_id, "version": str(version or ""), "source": source})
                return info
    return info


def scan_addon_framework_sync(framework: str, params: dict[str, Any]) -> dict[str, Any]:
    spec = ADDON_FRAMEWORKS[framework]
    prefixes = [str(prefix).lower() for prefix in spec["componentPrefixes"]]
    project_value = str(params.get("project_path") or params.get("projectPath") or DASHBOARD_STATE.selected_project_path or "").strip()
    project_path = Path(project_value) if project_value else None
    package_info = detect_addon_package(project_path, list(spec["packageIds"]))
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()

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
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
    output_path = build_dashboard_artifact_path(prefix, avatar_path, "json")
    # Never let a previous scan for the same avatar path masquerade as the
    # current Unity response when the tool fails to refresh its output file.
    output_path.unlink(missing_ok=True)
    merged: dict[str, Any] = {"avatarPath": avatar_path, "outputPath": str(output_path)}
    merged.update(unity_params)
    result = invoke_unity_mcp(settings, tool_name, merged)
    if output_path.exists():
        payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
        payload.setdefault("jsonPath", str(output_path))
    else:
        payload = extract_tool_result_payload(result)
    payload = ensure_dict_payload(payload, label)
    payload.setdefault("ok", True)
    return payload


def scan_avatar_items_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    return run_unity_artifact_scan_sync(
        params,
        "vrc_scan_avatar_items",
        "avatar_items",
        {
            "maxItems": int(params.get("max_items") or params.get("maxItems") or 2000),
            "refreshAssets": False,
        },
        "avatar item scan",
    )


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


def scan_avatar_controls_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
    payload = scan_avatar_controls_direct(settings, avatar_path)
    payload.setdefault("ok", True)
    return payload


def scan_wardrobe_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    return run_unity_artifact_scan_sync(
        params,
        "vrc_scan_wardrobe",
        "wardrobe",
        {},
        "wardrobe scan",
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
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_ensure_expression_parameter", request)),
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
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_ensure_expression_menu_control", request)),
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
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_ensure_animator_state", request)),
        "ensure animator state",
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
    settings = load_dashboard_settings(build_agent_connection_request(params))
    asset_paths = params.get("asset_paths") or params.get("assetPaths") or []
    request: dict[str, Any] = {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "assetPaths": [str(item) for item in asset_paths if str(item).strip()],
        "includeOpenScenes": bool(params.get("include_open_scenes", params.get("includeOpenScenes", True))),
        "refreshAssets": False,
    }
    backup_root = str(params.get("backup_root") or params.get("backupRoot") or "").strip()
    if backup_root:
        request["backupRoot"] = backup_root
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_create_safe_backup", request)),
        "safe backup",
    )
    payload.setdefault("ok", True)
    emit_log("info", "backup", "Safe backup snapshot created.", {"backupPath": payload.get("backup_path")})
    return payload


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
            invoke_unity_mcp(settings, "vrc_restore_safe_backup", build_safe_backup_restore_request(params, False))
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


VPM_PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,100}$")
KNOWN_VPM_CLI_NAMES = ("vpm", "vrc-get")
PACKAGE_UI_MANAGER_NAMES = ("vcc", "alcom")


def _optimizer_package_catalog() -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    for dependency in OPTIMIZER_DEPENDENCIES:
        repository = str(dependency.get("vpmRepository") or "")
        label = str(dependency.get("label") or dependency.get("displayName") or dependency.get("id") or "")
        for package_id in dependency.get("packageIds") or []:
            key = str(package_id or "").strip().lower()
            if key:
                catalog[key] = {
                    "dependencyId": str(dependency.get("id") or ""),
                    "label": label,
                    "repository": repository,
                    "docsLink": str(dependency.get("docsLink") or ""),
                }
    return catalog


def _normalize_manager_path(path: str) -> str:
    return str(path or "").replace("\\", "/")


def _add_package_manager(
    managers: list[dict[str, Any]],
    *,
    name: str,
    path: str,
    kind: str,
    label: str,
    supports_command_install: bool,
    supports_ui_handoff: bool,
    source: str,
) -> None:
    normalized = _normalize_manager_path(path)
    if not normalized:
        return
    key = (name, normalized.lower(), kind)
    if any((item.get("name"), str(item.get("path") or "").lower(), item.get("kind")) == key for item in managers):
        return
    managers.append(
        {
            "name": name,
            "label": label,
            "path": normalized,
            "kind": kind,
            "source": source,
            "supportsCommandInstall": supports_command_install,
            "supportsUiHandoff": supports_ui_handoff,
        }
    )


def _existing_app_paths(candidates: list[Path]) -> list[str]:
    paths: list[str] = []
    for candidate in candidates:
        try:
            if candidate.is_file():
                paths.append(str(candidate))
        except OSError:
            continue
    return paths


def locate_vpm_package_managers() -> list[dict[str, Any]]:
    managers: list[dict[str, Any]] = []
    managed_vrc_get = Path(os.environ.get("VRCFORGE_VRC_GET_PATH") or "")
    managed_candidates = [
        managed_vrc_get,
        Path(os.environ.get("LOCALAPPDATA") or "") / "VRCForge" / "package-tools" / "vrc-get" / "v1.9.1" / "vrc-get.exe",
    ]
    for candidate in managed_candidates:
        try:
            if candidate and candidate.is_file():
                _add_package_manager(
                    managers,
                    name="vrc-get",
                    path=str(candidate),
                    kind="managed-cli",
                    label="VRCForge managed vrc-get CLI",
                    supports_command_install=True,
                    supports_ui_handoff=False,
                    source="vrcforge-managed",
                )
        except OSError:
            continue
    cli_specs = {
        "vpm": ("VCC vpm CLI", True),
        "vrc-get": ("vrc-get CLI", True),
        "alcom": ("ALCOM CLI/UI", False),
    }
    for name, (label, supports_install) in cli_specs.items():
        path = shutil.which(name)
        if path:
            _add_package_manager(
                managers,
                name=name,
                path=path,
                kind="cli",
                label=label,
                supports_command_install=supports_install,
                supports_ui_handoff=name == "alcom",
                source="PATH",
            )

    local_app_data = Path(os.environ.get("LOCALAPPDATA") or "")
    program_files = Path(os.environ.get("ProgramFiles") or "")
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)") or "")
    for path in _existing_app_paths(
        [
            local_app_data / "Programs" / "VRChat Creator Companion" / "CreatorCompanion.exe",
            local_app_data / "VRChat Creator Companion" / "CreatorCompanion.exe",
            program_files / "VRChat Creator Companion" / "CreatorCompanion.exe",
            program_files_x86 / "VRChat Creator Companion" / "CreatorCompanion.exe",
        ]
    ):
        _add_package_manager(
            managers,
            name="vcc",
            path=path,
            kind="app",
            label="VRChat Creator Companion",
            supports_command_install=False,
            supports_ui_handoff=True,
            source="well-known-path",
        )
    for path in _existing_app_paths(
        [
            local_app_data / "Programs" / "ALCOM" / "ALCOM.exe",
            local_app_data / "ALCOM" / "ALCOM.exe",
            program_files / "ALCOM" / "ALCOM.exe",
            program_files_x86 / "ALCOM" / "ALCOM.exe",
        ]
    ):
        _add_package_manager(
            managers,
            name="alcom",
            path=path,
            kind="app",
            label="ALCOM",
            supports_command_install=False,
            supports_ui_handoff=True,
            source="well-known-path",
        )
    return managers


def resolve_addon_project_path(params: dict[str, Any]) -> str:
    return str(
        params.get("project_path") or params.get("projectPath") or DASHBOARD_STATE.selected_project_path or ""
    ).strip()


def package_manager_status_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    project_value = resolve_addon_project_path(params)
    project_path = Path(project_value) if project_value else None
    managers = locate_vpm_package_managers()
    packages = {
            framework: detect_addon_package(project_path, list(spec["packageIds"]))
        for framework, spec in ADDON_FRAMEWORKS.items()
    }
    usable = sorted(
        [manager for manager in managers if manager.get("supportsCommandInstall")],
        key=lambda item: {"vpm": 0, "vrc-get": 1}.get(str(item.get("name") or ""), 9),
    )
    ui_handoff = sorted(
        [manager for manager in managers if manager.get("supportsUiHandoff")],
        key=lambda item: {"vcc": 0, "alcom": 1}.get(str(item.get("name") or ""), 9),
    )
    package_catalog = _optimizer_package_catalog()
    return {
        "ok": True,
        "projectPath": project_value,
        "managers": managers,
        "preferredCli": usable[0] if usable else None,
        "preferredCommandInstaller": usable[0] if usable else None,
        "preferredUiHandoff": ui_handoff[0] if ui_handoff else None,
        "canInstall": bool(usable) and bool(project_value),
        "canRequestInstall": bool(project_value),
        "packages": packages,
        "knownOptimizationPackages": package_catalog,
        "installPolicy": {
            "managerPriority": [
                "ALCOM/VCC UI handoff when a human wants to manage repositories visually",
                "VCC vpm CLI for non-interactive supervised installs",
                "vrc-get CLI for non-interactive supervised installs",
                "agent-managed download/install plan when no package manager is available",
            ],
            "directManifestEditing": False,
            "requiresApprovalCheckpoint": True,
        },
        "hint": (
            "VRCForge detects ALCOM/VCC for user handoff first. Non-interactive installs use the VCC vpm CLI "
            "or vrc-get after approval; if neither exists, VRCForge returns an agent-managed download plan."
        ),
    }


def _select_package_install_strategy(params: dict[str, Any], managers: list[dict[str, Any]]) -> dict[str, Any]:
    package_id = str(params.get("package_id") or params.get("packageId") or "").strip().lower()
    preferred = str(params.get("preferredManager") or params.get("preferred_manager") or "").strip().lower()
    allow_agent = bool(params.get("allowAgentManagedDownload") or params.get("allow_agent_managed_download"))
    catalog = _optimizer_package_catalog()
    package_meta = catalog.get(package_id, {})
    command_installers = sorted(
        [manager for manager in managers if manager.get("supportsCommandInstall")],
        key=lambda item: {"vpm": 0, "vrc-get": 1}.get(str(item.get("name") or ""), 9),
    )
    ui_handoff = sorted(
        [manager for manager in managers if manager.get("supportsUiHandoff")],
        key=lambda item: {"vcc": 0, "alcom": 1}.get(str(item.get("name") or ""), 9),
    )
    if preferred:
        command_installers.sort(key=lambda item: 0 if item.get("name") == preferred else 1)
        ui_handoff.sort(key=lambda item: 0 if item.get("name") == preferred else 1)
    selected_cli = command_installers[0] if command_installers else None
    selected_handoff = ui_handoff[0] if ui_handoff else None
    execution_strategy = "command" if selected_cli else "agent_download" if allow_agent and not selected_handoff else "manual_handoff"
    strategy = "ui_handoff" if selected_handoff else execution_strategy
    return {
        "schema": "vrcforge.package_install_plan.v1",
        "packageId": package_id,
        "repository": str(params.get("repository") or params.get("vpmRepository") or package_meta.get("repository") or ""),
        "package": package_meta,
        "includePrerelease": bool(params.get("includePrerelease") or params.get("include_prerelease") or params.get("prerelease")),
        "strategy": strategy,
        "executionStrategy": execution_strategy,
        "preferredManager": selected_handoff or selected_cli,
        "commandInstaller": selected_cli,
        "uiHandoff": selected_handoff,
        "managers": managers,
        "allowAgentManagedDownload": allow_agent,
        "directManifestEditing": False,
        "requiresApproval": True,
        "requiresCheckpoint": True,
        "message": (
            "Use the selected ALCOM/VCC handoff first; supervised command install is also available after approval."
            if selected_handoff and selected_cli
            else "Use the selected ALCOM/VCC handoff first."
            if selected_handoff
            else "Use the selected VPM CLI after approval."
            if selected_cli
            else "No VPM package manager is available; let an external agent prepare a supervised package-manager download/install plan."
        ),
        "agentManagedDownload": {
            "available": allow_agent and selected_cli is None and selected_handoff is None,
            "allowedTargets": ["install ALCOM or VCC", "install VCC/vpm CLI", "install vrc-get", "download package manager from official source"],
            "disallowedTargets": ["directly edit Packages/manifest.json", "copy optimizer source into VRCForge", "bypass approval/checkpoint"],
            "nextTool": "vrcforge_request_apply",
        },
    }


def package_install_plan_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    package_id = str(params.get("package_id") or params.get("packageId") or "").strip().lower()
    if not VPM_PACKAGE_ID_RE.match(package_id):
        return {"ok": False, "error": "packageId must be a valid VPM package id, for example nadena.dev.modular-avatar."}
    project_value = resolve_addon_project_path(params)
    managers = locate_vpm_package_managers()
    strategy = _select_package_install_strategy(params, managers)
    package_state = None
    if project_value and Path(project_value).is_dir():
        package_state = detect_addon_package(Path(project_value), [package_id])
    return {
        "ok": True,
        **strategy,
        "readOnly": True,
        "planOnly": True,
        "projectPath": project_value,
        "packageState": package_state,
        "canExecuteCommandInstall": bool(strategy.get("commandInstaller")) and bool(project_value),
        "canCreateInstallRequest": bool(project_value),
    }


def request_package_install_sync(params: dict[str, Any], agent_name: str = "external-agent") -> dict[str, Any]:
    params = params or {}
    plan = package_install_plan_sync(params)
    if not plan.get("ok"):
        return plan
    if not plan.get("canExecuteCommandInstall"):
        return {
            "ok": False,
            "status": "blocked",
            "error": "No supported non-interactive VPM CLI is available for package install. Use the UI handoff or prepare an agent-managed package-manager install first.",
            "installPlan": plan,
        }
    return AGENT_GATEWAY.create_apply_request(
        {
            "target_tool": "vrcforge_install_vpm_package",
            "arguments": {
                "projectPath": plan.get("projectPath"),
                "packageId": plan.get("packageId"),
                "repository": plan.get("repository") or "",
                "preferredManager": str(params.get("preferredManager") or params.get("preferred_manager") or ""),
                "includePrerelease": bool(params.get("includePrerelease") or params.get("include_prerelease") or params.get("prerelease")),
            },
            "reason": f"Install VPM package {plan.get('packageId')} through VRCForge supervised package manager flow.",
            "preview": plan,
            "agent_name": agent_name,
        },
        internal_wrapper=True,
    )


def install_vpm_package_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    package_id = str(params.get("package_id") or params.get("packageId") or "").strip().lower()
    if not VPM_PACKAGE_ID_RE.match(package_id):
        return {"ok": False, "error": "packageId must be a valid VPM package id, for example nadena.dev.modular-avatar."}
    project_value = resolve_addon_project_path(params)
    if not project_value or not Path(project_value).is_dir():
        return {"ok": False, "error": "A valid Unity projectPath is required."}

    managers = locate_vpm_package_managers()
    strategy = _select_package_install_strategy(params, managers)
    cli = strategy.get("commandInstaller") if isinstance(strategy.get("commandInstaller"), dict) else None
    if cli is None:
        return {
            "ok": False,
            "error": "No supported non-interactive VPM CLI was found. Use ALCOM/VCC UI or ask the agent to prepare a supervised package-manager download/install request.",
            "managers": managers,
            "installPlan": strategy,
        }

    if cli["name"] == "vrc-get":
        command = [cli["path"], "install", "-p", project_value, "-y"]
        if bool(params.get("includePrerelease") or params.get("include_prerelease") or params.get("prerelease")):
            command.append("--prerelease")
        command.append(package_id)
    else:
        command = [cli["path"], "add", "package", package_id, "-p", project_value]

    try:
        proc = subprocess.run(
            command,
            cwd=project_value,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"Package install command failed to run: {exc}"[:300], "command": command}

    package_state = None
    for spec in ADDON_FRAMEWORKS.values():
        if package_id in [str(item).lower() for item in spec["packageIds"]]:
            package_state = detect_addon_package(Path(project_value), list(spec["packageIds"]))
            break
    if package_state is None:
        package_state = detect_addon_package(Path(project_value), [package_id])

    result = {
        "ok": proc.returncode == 0,
        "manager": cli,
        "command": command,
        "exitCode": proc.returncode,
        "stdoutSummary": (proc.stdout or "")[-1500:],
        "stderrSummary": (proc.stderr or "")[-1500:],
        "projectPath": project_value,
        "packageId": package_id,
        "package": package_state,
        "installPlan": strategy,
        "hint": "Unity must refresh/resolve packages before new components are usable; reopen or focus the Unity project.",
    }
    emit_log(
        "info" if result["ok"] else "error",
        "addon",
        f"VPM package install {'succeeded' if result['ok'] else 'failed'}: {package_id}",
        {"manager": cli["name"], "exitCode": proc.returncode},
    )
    return result


PACKAGE_DIAGNOSTIC_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    ("network", r"\b(timeout|timed out|network|connection|ssl|tls|proxy|dns|unable to resolve)\b", "Package source/network failure", "Retry after checking network/proxy settings, then rerun package status."),
    ("manifest", r"\b(manifest|packages-lock|lock file|json|parse|invalid character|could not parse)\b", "Project manifest or lock-file problem", "Use the package manager UI/CLI to restore packages; any manifest edit must be a supervised repair plan."),
    ("dependency", r"\b(dependency|dependencies|version conflict|conflict|incompatible|resolution|resolve packages)\b", "Package dependency resolution problem", "Inspect Packages/manifest.json and packages-lock.json, then plan a dependency repair with checkpoint."),
    ("permission", r"\b(access denied|permission denied|unauthorized|read-only|being used by another process|locked)\b", "Filesystem permission or lock problem", "Close tools holding the project, check write permissions, then retry."),
    ("compile", r"\b(cs\d{4}|compile error|compilation failed|compiler|assembly)\b", "Unity compile error after package import", "Open the compile errors and generate a separate supervised fix plan."),
    ("unitypackage", r"\b(importpackage|unitypackage|assetdatabase\.importpackage|failed to import)\b", "UnityPackage import problem", "Inspect the UnityPackage/folder first, then import through VRCForge with checkpoint and rollback proof."),
)


def diagnose_package_install_errors_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    project_value = resolve_addon_project_path(params)
    package_id = str(params.get("packageId") or params.get("package_id") or "").strip().lower()
    max_compile_errors = int(params.get("maxCompileErrors") or params.get("max_compile_errors") or 30)
    raw_text = "\n".join(
        str(params.get(key) or "")
        for key in ("stdoutSummary", "stdout_summary", "stderrSummary", "stderr_summary", "logText", "log_text")
    )
    safe_text = str(summarize_debug_payload(raw_text))[:5000]
    warnings: list[str] = []

    try:
        package_status = package_manager_status_sync({"projectPath": project_value})
    except Exception as exc:  # noqa: BLE001 - diagnostics must survive partial failures.
        package_status = {"ok": False, "error": str(exc)}
        warnings.append(f"Package manager status failed: {exc}")

    compile_errors: dict[str, Any]
    try:
        compile_errors = read_agent_compile_errors({"projectPath": project_value, "maxErrors": max_compile_errors})
    except Exception as exc:  # noqa: BLE001
        compile_errors = {"ok": False, "error": str(exc)}
        warnings.append(f"Unity compile-error reader failed: {exc}")

    symptoms = _classify_package_install_symptoms(safe_text, compile_errors, package_status)
    suggested_fix_plans = _build_package_install_fix_suggestions(symptoms, package_status, package_id)
    return {
        "ok": True,
        "schema": "vrcforge.package_install_diagnostics.v1",
        "readOnly": True,
        "projectPath": project_value,
        "packageId": package_id,
        "packageManager": redact_support_payload(package_status),
        "compileErrors": redact_support_payload(compile_errors),
        "symptoms": symptoms,
        "warnings": warnings,
        "suggestedFixPlans": suggested_fix_plans,
        "repairPolicy": {
            "automaticRepair": False,
            "supervisedRepairOnly": True,
            "requiresPreviewApprovalCheckpointValidationRollback": True,
        },
    }


def _classify_package_install_symptoms(
    log_text: str,
    compile_errors: dict[str, Any],
    package_status: dict[str, Any],
) -> list[dict[str, str]]:
    status_error = ""
    if not package_status.get("ok"):
        status_error = json.dumps(
            {
                "error": package_status.get("error"),
                "hint": package_status.get("hint"),
                "output": package_status.get("output"),
            },
            ensure_ascii=False,
        )
    haystack = f"{log_text}\n{json.dumps(compile_errors, ensure_ascii=False)}\n{status_error}".lower()
    symptoms: list[dict[str, str]] = []
    for code, pattern, title, suggestion in PACKAGE_DIAGNOSTIC_PATTERNS:
        if re.search(pattern, haystack, flags=re.IGNORECASE):
            symptoms.append({"code": code, "title": title, "suggestion": suggestion})
    if package_status.get("ok") and not package_status.get("preferredCli"):
        symptoms.append(
            {
                "code": "no_vpm_cli",
                "title": "No command-line VPM installer detected",
                "suggestion": "Use the package manager UI, or install vrc-get/VCC CLI before command-line package installs.",
            }
        )
    if not symptoms:
        symptoms.append(
            {
                "code": "unknown",
                "title": "No known package-install signature matched",
                "suggestion": "Export a support bundle or rerun with debug logging enabled to capture more context.",
            }
        )
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for symptom in symptoms:
        code = symptom["code"]
        if code in seen:
            continue
        seen.add(code)
        unique.append(symptom)
    return unique


def _build_package_install_fix_suggestions(
    symptoms: list[dict[str, str]],
    package_status: dict[str, Any],
    package_id: str,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    codes = {item.get("code") for item in symptoms}
    if "compile" in codes:
        suggestions.append(
            {
                "id": "explain_compile_errors",
                "risk": "read_only",
                "tool": "vrcforge_get_compile_errors",
                "summary": "Read Unity compile errors and create a separate fix plan.",
            }
        )
    if {"manifest", "dependency"} & codes:
        suggestions.append(
            {
                "id": "dependency_repair_plan",
                "risk": "plan_only",
                "tool": "vrcforge_package_manager_status",
                "summary": "Compare package manager status with manifest/lock state before any repair.",
            }
        )
    if "unitypackage" in codes:
        suggestions.append(
            {
                "id": "unitypackage_import_plan",
                "risk": "plan_only",
                "tool": "vrcforge_plan_outfit_import",
                "summary": "Inspect the package and build a supervised import plan with rollback proof.",
            }
        )
    if package_id and package_status.get("preferredCli"):
        suggestions.append(
            {
                "id": "retry_vpm_install_request",
                "risk": "approval_required",
                "tool": "vrcforge_install_vpm_package",
                "summary": f"Retry package install for {package_id} only through the approval/checkpoint path.",
            }
        )
    if not suggestions:
        suggestions.append(
            {
                "id": "support_bundle",
                "risk": "read_only",
                "tool": "vrcforge_support_bundle",
                "summary": "Collect redacted diagnostics before attempting repair.",
            }
        )
    return suggestions


def build_setup_outfit_request(params: dict[str, Any], confirm: bool) -> dict[str, Any]:
    return {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "outfitPath": str(params.get("outfit_path") or params.get("outfitPath") or "").strip(),
        "confirmSetup": confirm,
        "saveScene": bool(params.get("save_scene", params.get("saveScene", True))),
    }


def preview_setup_outfit_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_setup_outfit_request(params, False)
    if not request["outfitPath"]:
        return {"ok": False, "error": "outfitPath is required."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_setup_outfit", request)),
        "setup outfit preview",
    )
    payload.setdefault("ok", True)
    return payload


def setup_outfit_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_setup_outfit_request(params, True)
    if not request["outfitPath"]:
        return {"ok": False, "error": "outfitPath is required."}
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_setup_outfit", request)),
        "setup outfit",
    )
    payload = wait_for_setup_outfit_job(settings, params, payload)
    if str(payload.get("status") or "").lower() == "error":
        payload["ok"] = False
    elif str(payload.get("status") or "").lower() == "timeout":
        payload["ok"] = False
    else:
        payload.setdefault("ok", True)

    emit_log(
        "info" if payload.get("ok") else "error",
        "wardrobe",
        "Modular Avatar Setup Outfit completed." if payload.get("ok") else "Modular Avatar Setup Outfit failed.",
        {"outfitPath": request["outfitPath"], "jobId": payload.get("jobId"), "status": payload.get("status")},
    )
    return payload


def wait_for_setup_outfit_job(settings: Any, params: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(payload.get("jobId") or payload.get("job_id") or "").strip()
    if not job_id or not is_setup_outfit_job_pending(payload):
        return normalize_setup_outfit_terminal_payload(payload)

    timeout_seconds = coerce_float_param(
        params,
        ("setup_outfit_poll_timeout_seconds", "setupOutfitPollTimeoutSeconds"),
        180.0,
        0.0,
        3600.0,
    )
    if timeout_seconds <= 0:
        return setup_outfit_timeout_payload(job_id, payload, None)

    interval_seconds = coerce_float_param(
        params,
        ("setup_outfit_poll_interval_seconds", "setupOutfitPollIntervalSeconds"),
        1.0,
        0.0,
        30.0,
    )
    request_timeout_seconds = int(
        coerce_float_param(
            params,
            ("setup_outfit_poll_request_timeout_seconds", "setupOutfitPollRequestTimeoutSeconds"),
            min(float(getattr(settings, "unity_mcp_timeout_seconds", 30) or 30), 8.0),
            1.0,
            60.0,
        )
    )
    poll_settings = copy.copy(settings)
    try:
        poll_settings.unity_mcp_timeout_seconds = request_timeout_seconds
    except Exception:  # noqa: BLE001 - tests may use a minimal settings object.
        pass

    deadline = time.monotonic() + timeout_seconds
    last_payload = payload
    last_error: str | None = None
    while time.monotonic() < deadline:
        if interval_seconds > 0:
            time.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
        try:
            polled = ensure_dict_payload(
                extract_tool_result_payload(invoke_unity_mcp(poll_settings, "vrc_setup_outfit", {"jobId": job_id})),
                "setup outfit job",
            )
            last_payload = polled
            last_error = None
        except UnityMcpError as exc:
            last_error = str(exc)
            continue

        if not is_setup_outfit_job_pending(polled):
            return normalize_setup_outfit_terminal_payload(polled)

    return setup_outfit_timeout_payload(job_id, last_payload, last_error)


def normalize_setup_outfit_terminal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "").lower()
    if status in {"error", "timeout"}:
        payload["ok"] = False
    elif status in {"completed", ""}:
        payload.setdefault("ok", True)
    return payload


def is_setup_outfit_job_pending(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or "").lower()
    return bool(payload.get("jobId") or payload.get("job_id")) and (
        payload.get("pending") is True or status in {"pending", "running"}
    )


def setup_outfit_timeout_payload(job_id: str, last_payload: dict[str, Any], last_error: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "pending": False,
        "status": "timeout",
        "jobId": job_id,
        "lastStatus": last_payload.get("status"),
        "error": f"Setup Outfit job {job_id} did not finish before the poll timeout.",
        "lastPayload": last_payload,
    }
    if last_error:
        result["lastPollError"] = last_error
    return result


def coerce_float_param(
    params: dict[str, Any],
    names: tuple[str, ...],
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw: Any = None
    for name in names:
        if name in params:
            raw = params.get(name)
            break
    if raw is None:
        value = default
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
    return max(minimum, min(value, maximum))


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


def build_add_wardrobe_outfit_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "parameterName": str(params.get("parameter_name") or params.get("parameterName") or "").strip(),
        "outfitName": str(
            params.get("outfit_name")
            or params.get("outfitName")
            or params.get("display_name")
            or params.get("displayName")
            or ""
        ).strip(),
        "objectPaths": _coerce_path_list(
            params, "object_paths", "objectPaths", "on_object_paths", "onObjectPaths"
        ),
        "preview": preview,
    }
    off_objects = _coerce_path_list(params, "off_object_paths", "offObjectPaths")
    if off_objects:
        request["offObjectPaths"] = off_objects
    if params.get("add_menu_toggle") is not None or params.get("addMenuToggle") is not None:
        request["addMenuToggle"] = bool(params.get("add_menu_toggle", params.get("addMenuToggle")))
    if params.get("set_objects_default_off") is not None or params.get("setObjectsDefaultOff") is not None:
        request["setObjectsDefaultOff"] = bool(
            params.get("set_objects_default_off", params.get("setObjectsDefaultOff"))
        )
    if params.get("sub_menu_overflow") is not None or params.get("subMenuOverflow") is not None:
        request["subMenuOverflow"] = bool(params.get("sub_menu_overflow", params.get("subMenuOverflow")))
    sub_menu_name = str(params.get("sub_menu_name") or params.get("subMenuName") or "").strip()
    if sub_menu_name:
        request["subMenuName"] = sub_menu_name
    clip_dir = str(params.get("clip_output_dir") or params.get("clipOutputDir") or "").strip()
    if clip_dir:
        request["clipOutputDir"] = clip_dir
    if params.get("value") is not None:
        request["value"] = int(params.get("value"))
    if params.get("write_defaults") is not None or params.get("writeDefaults") is not None:
        request["writeDefaults"] = bool(params.get("write_defaults", params.get("writeDefaults")))
    return request


def _validate_add_wardrobe_outfit_request(request: dict[str, Any]) -> dict[str, Any] | None:
    if not request["parameterName"]:
        return {"ok": False, "error": "parameterName is required (the existing int wardrobe parameter)."}
    if not request["outfitName"]:
        return {"ok": False, "error": "outfitName is required (display name for the new outfit)."}
    if not request["objectPaths"]:
        return {"ok": False, "error": "objectPaths is required (the new outfit's scene objects to turn on)."}
    return None


def preview_add_wardrobe_outfit_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_add_wardrobe_outfit_request(params, True)
    invalid = _validate_add_wardrobe_outfit_request(request)
    if invalid is not None:
        return invalid
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_add_wardrobe_outfit", request)),
        "add wardrobe outfit preview",
    )
    payload.setdefault("ok", True)
    return payload


def add_wardrobe_outfit_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_add_wardrobe_outfit_request(params, False)
    invalid = _validate_add_wardrobe_outfit_request(request)
    if invalid is not None:
        return invalid
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_add_wardrobe_outfit", request)),
        "add wardrobe outfit",
    )
    payload.setdefault("ok", True)
    emit_log(
        "info",
        "wardrobe",
        "Wardrobe outfit added.",
        {"parameterName": request["parameterName"], "outfitName": request["outfitName"]},
    )
    return payload


def build_add_outfit_part_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "parameterName": str(params.get("parameter_name") or params.get("parameterName") or "").strip(),
        "partName": str(
            params.get("part_name")
            or params.get("partName")
            or params.get("display_name")
            or params.get("displayName")
            or ""
        ).strip(),
        "objectPaths": _coerce_path_list(
            params, "object_paths", "objectPaths", "on_object_paths", "onObjectPaths"
        ),
        "preview": preview,
    }
    value_raw = params.get("value")
    if value_raw is None:
        value_raw = params.get("outfit_value", params.get("outfitValue"))
    if value_raw is not None:
        request["value"] = int(value_raw)
    part_param = str(
        params.get("part_parameter_name")
        or params.get("partParameterName")
        or params.get("bool_parameter_name")
        or params.get("boolParameterName")
        or ""
    ).strip()
    if part_param:
        request["partParameterName"] = part_param
    if params.get("add_menu_toggle") is not None or params.get("addMenuToggle") is not None:
        request["addMenuToggle"] = bool(params.get("add_menu_toggle", params.get("addMenuToggle")))
    if params.get("set_objects_default_off") is not None or params.get("setObjectsDefaultOff") is not None:
        request["setObjectsDefaultOff"] = bool(
            params.get("set_objects_default_off", params.get("setObjectsDefaultOff"))
        )
    if params.get("default_on") is not None or params.get("defaultOn") is not None:
        request["defaultOn"] = bool(params.get("default_on", params.get("defaultOn")))
    sub_menu_name = str(params.get("sub_menu_name") or params.get("subMenuName") or "").strip()
    if sub_menu_name:
        request["subMenuName"] = sub_menu_name
    clip_dir = str(params.get("clip_output_dir") or params.get("clipOutputDir") or "").strip()
    if clip_dir:
        request["clipOutputDir"] = clip_dir
    if params.get("write_defaults") is not None or params.get("writeDefaults") is not None:
        request["writeDefaults"] = bool(params.get("write_defaults", params.get("writeDefaults")))
    return request


def _validate_add_outfit_part_request(request: dict[str, Any]) -> dict[str, Any] | None:
    if not request["parameterName"]:
        return {"ok": False, "error": "parameterName is required (the existing int wardrobe parameter the part is gated on)."}
    if not request["partName"]:
        return {"ok": False, "error": "partName is required (display name for the new part toggle)."}
    if "value" not in request:
        return {"ok": False, "error": "value is required (the wardrobe int value N this part belongs to)."}
    if not request["objectPaths"]:
        return {"ok": False, "error": "objectPaths is required (the part's scene objects to toggle on/off)."}
    return None


def preview_add_outfit_part_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_add_outfit_part_request(params, True)
    invalid = _validate_add_outfit_part_request(request)
    if invalid is not None:
        return invalid
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_add_outfit_part", request)),
        "add outfit part preview",
    )
    payload.setdefault("ok", True)
    return payload


def add_outfit_part_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_add_outfit_part_request(params, False)
    invalid = _validate_add_outfit_part_request(request)
    if invalid is not None:
        return invalid
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_add_outfit_part", request)),
        "add outfit part",
    )
    payload.setdefault("ok", True)
    emit_log(
        "info",
        "wardrobe",
        "Outfit part added.",
        {"parameterName": request["parameterName"], "partName": request["partName"], "value": request.get("value")},
    )
    return payload


def build_add_modular_avatar_component_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "gameObjectPath": str(
            params.get("game_object_path")
            or params.get("gameObjectPath")
            or params.get("target_path")
            or params.get("targetPath")
            or ""
        ).strip(),
        "componentType": str(params.get("component_type") or params.get("componentType") or "").strip(),
        "preview": preview,
    }
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
    if avatar_path:
        request["avatarPath"] = avatar_path
    if params.get("allow_duplicate") is not None or params.get("allowDuplicate") is not None:
        request["allowDuplicate"] = bool(params.get("allow_duplicate", params.get("allowDuplicate")))
    references = params.get("references")
    if isinstance(references, dict) and references:
        request["references"] = references
    fields = params.get("fields")
    if isinstance(fields, dict) and fields:
        request["fields"] = fields
    return request


def _validate_add_modular_avatar_component_request(request: dict[str, Any]) -> dict[str, Any] | None:
    if not request["gameObjectPath"]:
        return {"ok": False, "error": "gameObjectPath is required (the scene object to add the Modular Avatar component to)."}
    if not request["componentType"]:
        return {"ok": False, "error": "componentType is required (e.g. MergeArmature, BoneProxy, MenuInstaller, MergeAnimator, Parameters)."}
    return None


def preview_add_modular_avatar_component_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_add_modular_avatar_component_request(params, True)
    invalid = _validate_add_modular_avatar_component_request(request)
    if invalid is not None:
        return invalid
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_add_modular_avatar_component", request)),
        "add modular avatar component preview",
    )
    payload.setdefault("ok", True)
    return payload


def add_modular_avatar_component_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_add_modular_avatar_component_request(params, False)
    invalid = _validate_add_modular_avatar_component_request(request)
    if invalid is not None:
        return invalid
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_add_modular_avatar_component", request)),
        "add modular avatar component",
    )
    payload.setdefault("ok", True)
    emit_log(
        "info",
        "modular_avatar",
        "Modular Avatar component added.",
        {"gameObjectPath": request["gameObjectPath"], "componentType": request["componentType"]},
    )
    return payload


def _coerce_int_list(params: dict[str, Any], *keys: str) -> list[int]:
    result: list[int] = []
    for key in keys:
        raw = params.get(key)
        if raw is None:
            continue
        if isinstance(raw, (list, tuple)):
            for item in raw:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value not in result:
                    result.append(value)
            continue
        for part in str(raw).replace(";", ",").replace(" ", ",").split(","):
            if not part.strip():
                continue
            try:
                value = int(part.strip())
            except ValueError:
                continue
            if value not in result:
                result.append(value)
    return result


def build_manage_wardrobe_request(params: dict[str, Any], preview: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "action": str(params.get("action") or "").strip(),
        "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
        "parameterName": str(
            params.get("parameter_name")
            or params.get("parameterName")
            or params.get("wardrobe_parameter")
            or params.get("wardrobeParameter")
            or ""
        ).strip(),
        "preview": preview,
    }
    for source_key, target_key in (
        ("outfit_name", "outfitName"),
        ("outfitName", "outfitName"),
        ("target_name", "targetName"),
        ("targetName", "targetName"),
        ("state_name", "stateName"),
        ("stateName", "stateName"),
        ("control_name", "controlName"),
        ("controlName", "controlName"),
        ("new_name", "newName"),
        ("newName", "newName"),
        ("new_outfit_name", "newOutfitName"),
        ("newOutfitName", "newOutfitName"),
        ("asset_dir", "assetDir"),
        ("assetDir", "assetDir"),
        ("clip_output_dir", "clipOutputDir"),
        ("clipOutputDir", "clipOutputDir"),
    ):
        value = str(params.get(source_key) or "").strip()
        if value:
            request[target_key] = value
    for source_key, target_key in (
        ("target_value", "targetValue"),
        ("targetValue", "targetValue"),
        ("outfit_value", "outfitValue"),
        ("outfitValue", "outfitValue"),
        ("value", "value"),
    ):
        if params.get(source_key) is not None:
            request[target_key] = int(params.get(source_key))
            break
    order_values = _coerce_int_list(params, "order_values", "orderValues")
    if order_values:
        request["orderValues"] = order_values
    target_values = _coerce_int_list(params, "target_values", "targetValues", "values")
    if target_values:
        request["targetValues"] = target_values
    for source_key, target_key, default in (
        ("delete_objects", "deleteObjects", False),
        ("deleteObjects", "deleteObjects", False),
        ("deactivate_objects", "deactivateObjects", True),
        ("deactivateObjects", "deactivateObjects", True),
        ("delete_generated_assets", "deleteGeneratedAssets", False),
        ("deleteGeneratedAssets", "deleteGeneratedAssets", False),
        ("confirm_delete_wardrobe", "confirmDeleteWardrobe", False),
        ("confirmDeleteWardrobe", "confirmDeleteWardrobe", False),
    ):
        if params.get(source_key) is not None:
            request[target_key] = _coerce_gateway_bool(params.get(source_key), default)
    return request


def _validate_manage_wardrobe_request(request: dict[str, Any]) -> dict[str, Any] | None:
    if not request["action"]:
        return {"ok": False, "error": "action is required for wardrobe management."}
    if not request["parameterName"]:
        return {"ok": False, "error": "parameterName is required for wardrobe management."}
    return None


def preview_manage_wardrobe_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_manage_wardrobe_request(params, True)
    invalid = _validate_manage_wardrobe_request(request)
    if invalid is not None:
        return invalid
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_manage_wardrobe", request)),
        "manage wardrobe preview",
    )
    payload.setdefault("ok", True)
    return payload


def manage_wardrobe_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    request = build_manage_wardrobe_request(params, False)
    invalid = _validate_manage_wardrobe_request(request)
    if invalid is not None:
        return invalid
    settings = load_dashboard_settings(build_agent_connection_request(params))
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_manage_wardrobe", request)),
        "manage wardrobe",
    )
    payload.setdefault("ok", True)
    emit_log(
        "info",
        "wardrobe",
        "Wardrobe management action executed.",
        {"parameterName": request["parameterName"], "action": request["action"]},
    )
    return payload


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
            "vrchat_sdk": detect_addon_package(project_path, VRCHAT_SDK_PACKAGE_IDS),
            "modular_avatar": detect_addon_package(project_path, list(ADDON_FRAMEWORKS["modular_avatar"]["packageIds"])),
            "vrcfury": detect_addon_package(project_path, list(ADDON_FRAMEWORKS["vrcfury"]["packageIds"])),
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

    for key, title in (("mcpPackageConfigured", "Unity MCP package"), ("unityMcpBridgeReachable", "Unity MCP bridge"), ("unityMcpInstance", "Unity MCP instance"), ("vrcForgeUnityTools", "VRCForge Unity tools")):
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
        "menu": _run_validation_source("menu", lambda: scan_avatar_controls_sync(base_params)),
        "fx": _run_validation_source("fx", lambda: scan_fx_animator_sync(base_params)),
        "animation_bindings": _run_validation_source("animation_bindings", lambda: scan_animation_bindings_sync(base_params)),
        "materials": _run_validation_source("materials", lambda: scan_shader_materials_sync(ShaderMaterialScanRequest(**base_params))),
        "wardrobe": _run_validation_source("wardrobe", lambda: scan_wardrobe_sync(base_params)),
        "performance_pc": _run_validation_source("performance_pc", lambda: scan_avatar_performance_sync({**base_params, "isMobile": False})),
    }
    if include_readiness:
        sources.update(
            {
                "dependencies": _run_validation_source("dependencies", lambda: validation_dependency_status_sync(base_params)),
                "environment": _run_validation_source("environment", lambda: validation_environment_status_sync(base_params)),
                "package_manager": _run_validation_source("package_manager", lambda: package_manager_status_sync(base_params)),
                "avatar_items": _run_validation_source("avatar_items", lambda: scan_avatar_items_sync(base_params)),
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
        package_diagnostics = diagnose_package_install_errors_sync(
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


def build_optimization_validation_context(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    return build_validation_report_sync(
        {
            "avatarPath": str(params.get("avatar_path") or params.get("avatarPath") or "").strip(),
            "projectPath": str(params.get("project_path") or params.get("projectPath") or DASHBOARD_STATE.selected_project_path or "").strip(),
            "includeQuest": bool(params.get("include_quest", params.get("includeQuest", True))),
            "includeSources": True,
            "includeReadiness": True,
            "gateBuild": False,
            "maxErrors": int(params.get("max_errors") or params.get("maxErrors") or 50),
        }
    )


def build_optimization_plan_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    validation = build_optimization_validation_context(params)
    return build_optimization_report(params, validation)


def build_optimization_tool_sync(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    external_name = normalize_tool_name(tool_name)
    if external_name in {"optimization.target.profile", "optimization.dependency.doctor"}:
        validation: dict[str, Any] = {}
    else:
        validation = build_optimization_validation_context(params)
    return build_optimization_tool_result(external_name, params, validation)


def normalize_optimization_apply_request_name(tool_name: str) -> str:
    value = str(tool_name or "").strip()
    if value in OPTIMIZATION_APPLY_REQUEST_BY_EXTERNAL:
        return value
    definition = OPTIMIZATION_APPLY_REQUEST_BY_GATEWAY.get(value)
    if definition:
        return str(definition["externalName"])
    aliases = {
        "lac": "optimization.lac.apply-request",
        "lac_profile": "optimization.lac.apply-request",
        "aao": "optimization.aao.trace-apply-request",
        "aao_trace": "optimization.aao.trace-apply-request",
        "ttt": "optimization.ttt.atlas-apply-request",
        "textrans": "optimization.ttt.atlas-apply-request",
        "textrans_tool": "optimization.ttt.atlas-apply-request",
        "ma2bt": "optimization.ma2bt.convert-apply-request",
        "ma2bt_pro": "optimization.ma2bt.convert-apply-request",
        "meshia": "optimization.meshia.simplify-apply-request",
        "vrcfury_parameter": "optimization.vrcfury.parameter-compressor-apply-request",
        "vrcfury_parameter_compressor": "optimization.vrcfury.parameter-compressor-apply-request",
        "vrcfury_direct_tree": "optimization.vrcfury.direct-tree-apply-request",
    }
    key = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if key in aliases:
        return aliases[key]
    raise ValueError(f"Unknown optimization apply-request tool: {tool_name}")


def _normalize_optimizer_profile_id(value: Any) -> str:
    raw = str(value or "pc_conservative").strip().lower()
    key = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    aliases = {
        "conservative": "pc_conservative",
        "conservative_pc": "pc_conservative",
        "pc_conservative": "pc_conservative",
        "medium": "pc_medium",
        "balanced": "balanced",
        "balanced_pc": "balanced_pc",
        "pc_medium": "pc_medium",
        "high_quality": "high_quality",
        "quality": "high_quality",
        "custom": "custom",
    }
    return aliases.get(key, key or "pc_conservative")


def _option_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _confirmed_ttt_material_paths(params: dict[str, Any], options: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "atlasTargetMaterials",
        "materialPaths",
        "materials",
        "targetMaterialPaths",
        "confirmedMaterialPaths",
        "userConfirmedMaterialPaths",
    ):
        values.extend(_option_string_list(options.get(key)))
        values.extend(_option_string_list(params.get(key)))
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.replace("\\", "/").strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def _meshia_renderer_path(params: dict[str, Any], options: dict[str, Any]) -> str:
    return str(
        options.get("rendererPath")
        or options.get("targetRendererPath")
        or params.get("rendererPath")
        or params.get("targetRendererPath")
        or params.get("targetPath")
        or ""
    ).strip()


def _meshia_relative_vertex_count(profile: str, options: dict[str, Any]) -> tuple[float, str]:
    raw = (
        options.get("relativeVertexCount")
        or options.get("targetRatio")
        or options.get("ratio")
        or options.get("vertexRatio")
        or ""
    )
    if raw == "":
        return (0.9 if profile in {"pc_conservative", "conservative_pc"} else 0.85), ""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0, "Meshia relativeVertexCount must be a number between 0.75 and 1.0 for the stable request path."
    if value < 0.75 or value > 1.0:
        return value, "Meshia stable request path only allows relativeVertexCount between 0.75 and 1.0. Lower ratios remain experimental."
    return value, ""


def _find_optimizer_dependency(dependency_doctor: dict[str, Any], optimizer_id: str) -> dict[str, Any]:
    for dependency in dependency_doctor.get("dependencies") or []:
        if str(dependency.get("id") or "") == optimizer_id:
            return dependency
    return {}


def build_optimization_apply_request_preview_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    tool = normalize_optimization_apply_request_name(str(params.get("tool") or params.get("externalName") or params.get("gatewayName") or ""))
    definition = OPTIMIZATION_APPLY_REQUEST_BY_EXTERNAL[tool]
    project_value = resolve_addon_project_path(params)
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
    profile = _normalize_optimizer_profile_id(
        params.get("profile") or params.get("targetProfile") or params.get("target_profile") or "pc_conservative"
    )
    options = ensure_dict(params.get("options") or {})
    target_path = avatar_path
    dependency_doctor = build_optimization_tool_result(
        "optimization.dependency.doctor",
        {"projectPath": project_value},
        {},
    ).get("result") or {}
    dependency = _find_optimizer_dependency(dependency_doctor, str(definition["optimizerId"]))
    package_ids = [str(item) for item in dependency.get("packageIds") or [] if str(item or "").strip()]
    dependency_status = str(dependency.get("status") or "unknown")
    install_plan = None
    wants_install_plan = bool(
        params.get("installMissingDependencies")
        or params.get("install_missing_dependencies")
        or params.get("allowAgentManagedDownload")
        or dependency_status != "installed"
    )
    if package_ids and wants_install_plan:
        install_plan = package_install_plan_sync(
            {
                "projectPath": project_value,
                "packageId": package_ids[0],
                "repository": dependency.get("vpmRepository") or "",
                "allowAgentManagedDownload": bool(params.get("installMissingDependencies") or params.get("allowAgentManagedDownload")),
            }
        )
    supported_write = bool(definition.get("writeSupported"))
    stable_callable = bool(definition.get("stableCallable"))
    supported_profiles = [str(item) for item in definition.get("supportedProfiles") or []]
    blocked_reasons: list[str] = []
    if not project_value:
        blocked_reasons.append("Unity projectPath is required.")
    if not avatar_path and supported_write:
        blocked_reasons.append("avatarPath is required for supervised optimizer context and rollback proof.")
    if dependency_status != "installed":
        blocked_reasons.append(f"{dependency.get('label') or definition['optimizerId']} is {dependency_status}; install or repair it first.")
    if not supported_write:
        if str(definition.get("optimizerId")) == "vrcfury":
            blocked_reasons.append("VRCFury optimizer writes use internal feature models/menu settings in the inspected package; VRCForge exposes a stable request surface but blocks the write until a public validated writer path exists.")
        else:
            blocked_reasons.append("This optimizer apply path is still plan-only/experimental; VRCForge will not configure it automatically yet.")
    if not stable_callable:
        blocked_reasons.append("This optimizer is not yet part of the stable avatar optimization skill set.")
    if supported_profiles and profile not in supported_profiles:
        blocked_reasons.append(f"Profile '{profile}' is not enabled for stable delegated apply yet.")
    mode = str(definition.get("mode") or "")
    if mode == "ttt_atlas":
        material_paths = _confirmed_ttt_material_paths(params, options)
        if not material_paths:
            blocked_reasons.append("TexTransTool atlas setup requires user-confirmed material asset paths in options.atlasTargetMaterials.")
        invalid_material_paths = [item for item in material_paths if not item.replace("\\", "/").startswith("Assets/")]
        if invalid_material_paths:
            blocked_reasons.append("TexTransTool material references must be Unity asset paths under Assets/.")
        options = {**options, "atlasTargetMaterials": material_paths}
    elif mode == "meshia_simplify":
        renderer_path = _meshia_renderer_path(params, options)
        if not renderer_path:
            blocked_reasons.append("Meshia stable setup requires options.rendererPath for one user-selected low-risk Renderer object.")
        target_path = renderer_path or avatar_path
        ratio, ratio_error = _meshia_relative_vertex_count(profile, options)
        if ratio_error:
            blocked_reasons.append(ratio_error)
        options = {**options, "rendererPath": renderer_path, "relativeVertexCount": ratio}
    apply_arguments = {
        "projectPath": project_value,
        "avatarPath": avatar_path,
        "targetPath": target_path,
        "optimizerId": definition["optimizerId"],
        "mode": definition["mode"],
        "componentType": definition.get("componentType") or "",
        "profile": profile,
        "options": options,
        "sourceApplyRequestTool": definition["externalName"],
    }
    return {
        "ok": True,
        "schema": "vrcforge.optimization.apply_request.v1",
        "externalName": definition["externalName"],
        "gatewayName": definition["gatewayName"],
        "targetTool": definition["targetTool"],
        "versionStage": definition["versionStage"],
        "directApplyExposed": False,
        "requestOnly": True,
        "requiresApproval": True,
        "requiresCheckpoint": True,
        "requiresValidation": True,
        "requiresRollbackProof": True,
        "writeSupported": supported_write,
        "stableCallable": stable_callable,
        "supportedProfiles": supported_profiles,
        "readyToRequest": not blocked_reasons,
        "blockedReasons": blocked_reasons,
        "dependency": dependency,
        "dependencyInstallPlan": install_plan,
        "plan": build_optimization_tool_result(str(definition["planTool"]), params, {}),
        "applyArguments": apply_arguments,
        "policy": {
            "oneOptimizerStepAtATime": True,
            "noDirectExternalApply": True,
            "noOneClickAllOptimizers": True,
            "checkpointValidationRollbackRequired": True,
        },
    }


def request_optimization_apply_sync(params: dict[str, Any], agent_name: str = "external-agent") -> dict[str, Any]:
    params = params or {}
    preview = build_optimization_apply_request_preview_sync(params)
    install_missing = bool(params.get("installMissingDependencies") or params.get("install_missing_dependencies"))
    dependency = ensure_dict(preview.get("dependency"))
    package_ids = [str(item) for item in dependency.get("packageIds") or [] if str(item or "").strip()]
    if preview.get("blockedReasons") and install_missing and package_ids:
        install_plan = ensure_dict(preview.get("dependencyInstallPlan"))
        if not install_plan.get("canExecuteCommandInstall"):
            return {
                "ok": False,
                "status": "blocked",
                "error": "Dependency is missing and no supported package-manager CLI is available for a supervised install request.",
                "preview": preview,
                "installPlan": install_plan,
            }
        return AGENT_GATEWAY.create_apply_request(
            {
                "target_tool": "vrcforge_install_vpm_package",
                "arguments": {
                    "projectPath": preview["applyArguments"].get("projectPath"),
                    "packageId": package_ids[0],
                    "repository": install_plan.get("repository") or dependency.get("vpmRepository") or "",
                    "includePrerelease": bool(params.get("includePrerelease") or params.get("include_prerelease") or params.get("prerelease")),
                },
                "reason": f"Install dependency for {preview['externalName']} before optimizer configuration.",
                "preview": install_plan,
                "agent_name": agent_name,
            },
            internal_wrapper=True,
        )
    if not preview.get("readyToRequest"):
        return {"ok": False, "status": "blocked", "preview": preview, "error": "; ".join(preview.get("blockedReasons") or [])}
    return AGENT_GATEWAY.create_apply_request(
        {
            "target_tool": str(preview["targetTool"]),
            "arguments": preview["applyArguments"],
            "reason": f"Request supervised optimizer configuration for {preview['externalName']}.",
            "preview": preview,
            "agent_name": agent_name,
        },
        internal_wrapper=True,
    )


def _lac_component_properties(profile: str) -> dict[str, Any]:
    profile_id = _normalize_optimizer_profile_id(profile)
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
        material_paths = _confirmed_ttt_material_paths({}, options)
        properties: dict[str, Any] = {}
        if material_paths:
            properties["AtlasTargetMaterials"] = material_paths
        reference = str(options.get("allMaterialMergeReference") or options.get("mergeReference") or "").strip()
        if reference:
            properties["AllMaterialMergeReference"] = reference.replace("\\", "/")
        return properties
    if optimizer_id == "meshia":
        ratio, _ratio_error = _meshia_relative_vertex_count(_normalize_optimizer_profile_id(profile), options)
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


def configure_optimizer_component_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    optimizer_id = str(params.get("optimizerId") or params.get("optimizer_id") or "").strip()
    mode = str(params.get("mode") or "").strip()
    avatar_path = str(params.get("avatarPath") or params.get("avatar_path") or "").strip()
    target_path = str(params.get("targetPath") or params.get("target_path") or "").strip() or avatar_path
    component_type = str(params.get("componentType") or params.get("component_type") or "").strip()
    profile = _normalize_optimizer_profile_id(params.get("profile") or "pc_conservative")
    options = ensure_dict(params.get("options") or {})
    project_path = resolve_addon_project_path(params)
    if not optimizer_id or not mode:
        return {"ok": False, "error": "optimizerId and mode are required."}
    if not avatar_path:
        return {"ok": False, "error": "avatarPath is required."}
    if not target_path:
        return {"ok": False, "error": "targetPath is required."}
    if not component_type:
        return {"ok": False, "error": "This optimizer does not yet have a validated component writer in VRCForge."}
    steps: list[dict[str, Any]] = []
    already_present, inspect_payload = _component_already_present(project_path, target_path, component_type)
    if already_present:
        added = {
            "ok": True,
            "action": "reuse_component",
            "gameObjectPath": target_path,
            "componentType": component_type,
            "componentIndex": int(inspect_payload.get("componentIndex") or 0),
        }
    else:
        request = {
        "projectPath": project_path,
        "gameObjectPath": target_path,
        "componentType": component_type,
        "preview": bool(params.get("preview", False)),
        }
        added = add_component_sync(request)
    if not added.get("ok"):
        return {
            "ok": False,
            "optimizerId": optimizer_id,
            "mode": mode,
            "componentType": component_type,
            "error": added.get("error") or "Optimizer component setup failed.",
            "addComponent": added,
        }
    steps.append(
        {
            "id": "add_or_reuse_component",
            "status": "done",
            "tool": "vrcforge_add_component" if not already_present else "vrcforge_get_gameobject",
            "result": redact_support_payload(added),
        }
    )
    properties = _optimizer_component_properties(optimizer_id, profile, options)
    for property_path, value in properties.items():
        result = set_component_property_sync(
            {
                "projectPath": project_path,
                "gameObjectPath": target_path,
                "componentType": component_type,
                "componentIndex": 0,
                "propertyPath": property_path,
                "value": value,
                "preview": bool(params.get("preview", False)),
            }
        )
        steps.append(
            {
                "id": f"set_{property_path}",
                "status": "done" if result.get("ok") else "failed",
                "tool": "vrcforge_set_property",
                "propertyPath": property_path,
                "result": redact_support_payload(result),
            }
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "optimizerId": optimizer_id,
                "mode": mode,
                "profile": profile,
                "avatarPath": avatar_path,
                "targetPath": target_path,
                "componentType": component_type,
                "error": result.get("error") or f"Failed to configure {property_path}.",
                "steps": steps,
            }
    emit_log("info", "optimization", "Optimizer component configured.", {"optimizerId": optimizer_id, "mode": mode})
    return {
        "ok": True,
        "schema": "vrcforge.optimization.configure_component.v1",
        "optimizerId": optimizer_id,
        "mode": mode,
        "profile": profile,
        "avatarPath": avatar_path,
        "targetPath": target_path,
        "componentType": component_type,
        "steps": steps,
        "validationRequired": True,
        "rollbackProofRequired": True,
        "note": "VRCForge delegates the optimizer algorithm to the installed package; this handler only adds and configures validated public component fields through the supervised write path.",
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


def import_outfit_package_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    plan = plan_outfit_import_sync(params)
    plan_payload = ensure_dict_payload(plan.get("plan"), "outfit import plan")
    if not plan_payload.get("readyToApply"):
        return {"ok": False, "plan": plan_payload, "error": "Outfit import plan is not ready to apply."}
    project_root = _resolve_unity_project_root_for_import(params, plan_payload)
    kind = str(plan_payload.get("kind") or "")
    if kind in {"unitypackage_import", "unitypackage_import_sequence"}:
        import_results: list[dict[str, Any]] = []
        dependency = plan_payload.get("dependencyPreflight") if isinstance(plan_payload.get("dependencyPreflight"), dict) else {}
        package_order = dependency.get("packageOrder") if isinstance(dependency.get("packageOrder"), dict) else {}
        skipped_imports = package_order.get("skippedInstalledSupportPackages") if isinstance(package_order.get("skippedInstalledSupportPackages"), list) else []
        with tempfile.TemporaryDirectory(prefix="vrcforge-outfit-import-", dir=str(_outfit_import_temp_dir())) as temp_dir:
            queue = _resolve_outfit_import_queue(plan_payload, Path(temp_dir))
            if not queue:
                source = ensure_dict_payload(plan_payload.get("source"), "outfit import source")
                queue = [{"path": source.get("actualPackagePath"), "role": "target", "order": 1}]
            for item in queue:
                package_path = str(item.get("resolvedPackagePath") or item.get("path") or "").strip()
                if not package_path:
                    return {"ok": False, "kind": kind, "plan": plan_payload, "unityImports": import_results, "error": "Import queue contains an empty UnityPackage path."}
                result = import_unitypackage_sync({**params, "projectPath": str(project_root), "unityPackagePath": package_path})
                import_results.append(
                    {
                        "ok": bool(result.get("ok")),
                        "order": item.get("order"),
                        "role": item.get("role"),
                        "path": item.get("path"),
                        "sourceType": item.get("sourceType"),
                        "unityImport": result,
                    }
                )
                if not result.get("ok"):
                    return {"ok": False, "kind": kind, "plan": plan_payload, "unityImports": import_results, "error": result.get("error") or "UnityPackage import failed."}
        return {
            "ok": all(bool(item.get("ok")) for item in import_results),
            "kind": kind,
            "plan": plan_payload,
            "unityImports": import_results,
            "skippedUnityImports": skipped_imports,
            "unityImport": import_results[-1]["unityImport"] if import_results else {},
            "importedPrefabCandidates": _expected_prefab_assets(plan_payload),
            "nextTool": "vrcforge_add_outfit",
        }
    if kind == "loose_prefab_copy":
        copied = _copy_loose_outfit_assets(Path(str(plan_payload["source"]["path"])), project_root, str(plan_payload.get("targetFolder") or "Assets/VRCForge/ImportedOutfits"))
        refresh = refresh_asset_database_sync({**params, "projectPath": str(project_root)})
        return {
            "ok": True,
            "kind": kind,
            "plan": plan_payload,
            "copiedFiles": copied["copiedFiles"],
            "copiedFileCount": copied["copiedFileCount"],
            "importedPrefabCandidates": copied["prefabAssets"],
            "assetDatabaseRefresh": refresh,
            "nextTool": "vrcforge_add_outfit",
        }
    return {"ok": False, "plan": plan_payload, "error": f"Unsupported outfit import plan kind: {kind}"}


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
        safe_name = sanitize_artifact_name(Path(normalized_entry).stem) or "package"
        target = (temp_root / f"{safe_name}_{int(time.time() * 1000)}.unitypackage").resolve()
        with archive.open(info) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
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
    settings = load_dashboard_settings(build_agent_connection_request(params or {}))
    settings.unity_mcp_timeout_seconds = max(int(settings.unity_mcp_timeout_seconds or 30), 120)
    payload = ensure_dict_payload(
        extract_tool_result_payload(invoke_unity_mcp(settings, "vrc_refresh_asset_database", {
            "projectPath": str((params or {}).get("projectPath") or (params or {}).get("project_path") or ""),
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
    if not normalized.startswith("Assets/"):
        raise AgentGatewayError("targetFolder must be under Assets/.", status_code=400)
    target = (project_root / normalized).resolve()
    _ensure_path_inside_project(project_root, target)
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


def preview_add_outfit_workflow_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    avatar_path = str(params.get("avatar_path") or params.get("avatarPath") or "").strip()
    outfit_name = str(params.get("outfit_name") or params.get("outfitName") or params.get("name") or "").strip()
    parent_path = str(params.get("parent_path") or params.get("parentPath") or avatar_path).strip()
    manage_wardrobe = _workflow_bool(params, ("manage_wardrobe", "manageWardrobe"), True)
    create_wardrobe_if_missing = _workflow_bool(
        params,
        ("create_wardrobe_if_missing", "createWardrobeIfMissing"),
        True,
    )
    parameter_name, parameter_explicit = _workflow_parameter_name(params)
    asset, error = _resolve_workflow_asset(params)
    if error:
        return error
    assert asset is not None
    if not outfit_name:
        outfit_name = str(asset.get("name") or "Outfit")
    steps = [
        {"tool": "vrc_find_assets" if asset.get("source") == "query" else "vrc_get_asset_info", "write": False},
    ]
    if manage_wardrobe:
        steps.append({"tool": "vrc_scan_wardrobe", "write": False, "avatarPath": avatar_path})
        if create_wardrobe_if_missing:
            steps.append({"tool": "vrc_create_wardrobe", "write": True, "parameterName": parameter_name, "ifMissing": True})
    steps.append({"tool": "vrc_instantiate_prefab", "write": True, "parentPath": parent_path, "name": outfit_name})
    if params.get("unpack_prefab") is True or params.get("unpackPrefab") is True:
        steps.append({"tool": "vrc_unpack_prefab", "write": True, "mode": str(params.get("unpack_mode") or params.get("unpackMode") or "outermost")})
    if params.get("setup_outfit", params.get("setupOutfit", True)) is not False:
        steps.append({"tool": "vrc_setup_outfit", "write": True, "avatarPath": avatar_path})
    if manage_wardrobe:
        steps.append({"tool": "vrc_add_wardrobe_outfit", "write": True, "parameterName": parameter_name})
    return {
        "ok": True,
        "preview": True,
        "plan": {
            "action": "add_outfit_workflow",
            "avatarPath": avatar_path,
            "parentPath": parent_path,
            "outfitName": outfit_name,
            "asset": asset,
            "manageWardrobe": manage_wardrobe,
            "createWardrobeIfMissing": create_wardrobe_if_missing,
            "parameterName": parameter_name if manage_wardrobe else None,
            "parameterExplicit": parameter_explicit,
            "steps": steps,
        },
    }


def add_outfit_workflow_sync(params: dict[str, Any]) -> dict[str, Any]:
    params = params or {}
    plan = preview_add_outfit_workflow_sync(params)
    if not plan.get("ok"):
        return plan
    plan_payload = ensure_dict_payload(plan.get("plan"), "add outfit workflow plan")
    asset = ensure_dict_payload(plan_payload.get("asset"), "add outfit workflow asset")
    avatar_path = str(plan_payload.get("avatarPath") or "")
    parent_path = str(plan_payload.get("parentPath") or "")
    outfit_name = str(plan_payload.get("outfitName") or "Outfit")
    parameter_name = str(plan_payload.get("parameterName") or "")
    manage_wardrobe = bool(plan_payload.get("manageWardrobe"))
    create_wardrobe_if_missing = bool(plan_payload.get("createWardrobeIfMissing"))
    parameter_explicit = bool(plan_payload.get("parameterExplicit"))
    project_params = _workflow_project_params(params)
    steps: list[dict[str, Any]] = []

    if manage_wardrobe:
        scan = scan_wardrobe_sync({**project_params, "avatarPath": avatar_path})
        steps.append({"tool": "vrc_scan_wardrobe", "ok": bool(scan.get("ok")), "result": scan})
        if not scan.get("ok"):
            return {"ok": False, "preview": False, "plan": plan_payload, "steps": steps, "error": scan.get("error") or "Wardrobe scan failed."}
        wardrobe_names = _wardrobe_parameter_names(scan)
        candidate_names = _wardrobe_candidate_parameter_names(scan)
        if not parameter_explicit and wardrobe_names:
            parameter_name = wardrobe_names[0]
            plan_payload["parameterName"] = parameter_name
        if parameter_name not in wardrobe_names:
            if parameter_explicit and parameter_name in candidate_names:
                steps.append({
                    "tool": "vrc_scan_wardrobe",
                    "ok": True,
                    "candidateSelected": True,
                    "parameterName": parameter_name,
                    "warning": "Selected an explicit wardrobe candidate; automatic selection only uses high-confidence wardrobes.",
                })
            elif not parameter_explicit and candidate_names:
                return {
                    "ok": False,
                    "preview": False,
                    "plan": plan_payload,
                    "steps": steps,
                    "wardrobeCandidates": candidate_names,
                    "error": (
                        "No high-confidence wardrobe was found. Candidate control groups exist: "
                        + ", ".join(candidate_names)
                        + ". Specify parameterName to use one, or choose a new wardrobe parameter name."
                    ),
                }
            elif parameter_explicit and parameter_name not in candidate_names:
                pass
            else:
                # No existing wardrobe-like structure was found; fall through to creation.
                pass

        if parameter_name not in wardrobe_names and not (parameter_explicit and parameter_name in candidate_names):
            if not create_wardrobe_if_missing:
                return {
                    "ok": False,
                    "preview": False,
                    "plan": plan_payload,
                    "steps": steps,
                    "error": f"Wardrobe '{parameter_name}' was not found and createWardrobeIfMissing is false.",
                }
            created = create_wardrobe_sync(_workflow_wardrobe_create_args(params, avatar_path, parameter_name))
            steps.append({"tool": "vrc_create_wardrobe", "ok": bool(created.get("ok")), "result": created})
            if not created.get("ok"):
                return {"ok": False, "preview": False, "plan": plan_payload, "steps": steps, "error": created.get("error") or "Create wardrobe failed."}

    instantiate = instantiate_prefab_sync({
        **project_params,
        "assetPath": asset.get("assetPath"),
        "guid": asset.get("guid"),
        "parentPath": parent_path,
        "name": outfit_name,
        "preview": False,
    })
    steps.append({"tool": "vrc_instantiate_prefab", "ok": bool(instantiate.get("ok")), "result": instantiate})
    if not instantiate.get("ok"):
        return {"ok": False, "preview": False, "plan": plan_payload, "steps": steps, "error": instantiate.get("error") or "Prefab instantiate failed."}

    outfit_path = str(
        instantiate.get("gameObjectPath")
        or instantiate.get("outfitPath")
        or (f"{parent_path.rstrip('/')}/{outfit_name}" if parent_path else outfit_name)
    )
    if params.get("unpack_prefab") is True or params.get("unpackPrefab") is True:
        unpack = unpack_prefab_sync({
            **project_params,
            "gameObjectPath": outfit_path,
            "mode": str(params.get("unpack_mode") or params.get("unpackMode") or "outermost"),
            "preview": False,
        })
        steps.append({"tool": "vrc_unpack_prefab", "ok": bool(unpack.get("ok")), "result": unpack})
        if not unpack.get("ok"):
            return {"ok": False, "preview": False, "plan": plan_payload, "steps": steps, "outfitPath": outfit_path, "error": unpack.get("error") or "Prefab unpack failed."}

    if params.get("setup_outfit", params.get("setupOutfit", True)) is not False:
        setup = setup_outfit_sync({**project_params, "avatarPath": avatar_path, "outfitPath": outfit_path, "saveScene": params.get("saveScene", params.get("save_scene", True))})
        steps.append({"tool": "vrc_setup_outfit", "ok": bool(setup.get("ok")), "result": setup})
        if not setup.get("ok"):
            return {"ok": False, "preview": False, "plan": plan_payload, "steps": steps, "outfitPath": outfit_path, "error": setup.get("error") or "Setup Outfit failed."}

    if manage_wardrobe and parameter_name:
        wardrobe = add_wardrobe_outfit_sync({
            **project_params,
            "avatarPath": avatar_path,
            "parameterName": parameter_name,
            "outfitName": outfit_name,
            "objectPaths": [outfit_path],
            "offObjectPaths": _coerce_path_list(params, "off_object_paths", "offObjectPaths"),
        })
        steps.append({"tool": "vrc_add_wardrobe_outfit", "ok": bool(wardrobe.get("ok")), "result": wardrobe})
        if not wardrobe.get("ok"):
            return {"ok": False, "preview": False, "plan": plan_payload, "steps": steps, "outfitPath": outfit_path, "error": wardrobe.get("error") or "Add wardrobe outfit failed."}

    emit_log("info", "wardrobe", "Add outfit workflow executed.", {"outfitPath": outfit_path, "parameterName": parameter_name})
    return {"ok": True, "preview": False, "plan": plan_payload, "outfitPath": outfit_path, "steps": steps}


def register_agent_gateway_tools() -> None:
    AGENT_GATEWAY.register_tool("vrcforge_agent_observe", "Observe VRCForge agent runtime state.", "read/debug", lambda params: AGENT_GATEWAY.runtime_observe(str(params.get("session_id") or params.get("sessionId") or "")))
    AGENT_GATEWAY.register_tool("vrcforge_agent_message", "Run one VRCForge agent runtime turn.", "plan/preview", lambda params: AGENT_GATEWAY.runtime_message(params, agent_name=str(params.get("agent_name") or params.get("agentName") or "external-agent")))
    AGENT_GATEWAY.register_tool("vrcforge_classify_shell", "Classify a shell command before execution.", "read/debug", AGENT_GATEWAY.classify_shell)
    AGENT_GATEWAY.register_tool("vrcforge_execute_shell", "Execute low-risk shell commands or request approval for high-risk commands.", "supervised-write", lambda params: AGENT_GATEWAY.execute_shell(params, agent_name=str(params.get("agent_name") or params.get("agentName") or "external-agent")), write=True)
    AGENT_GATEWAY.register_tool("vrcforge_execute_approved_shell", "Execute a previously approved shell command payload.", "supervised-write", AGENT_GATEWAY.execute_approved_shell, write=True)
    AGENT_GATEWAY.register_tool("vrcforge_skill_manifest", "List VRCForge Agent Gateway skills.", "read/debug", lambda _params: AGENT_GATEWAY.build_manifest())
    AGENT_GATEWAY.register_tool("vrcforge_skill_check", "Validate VRCForge Agent Gateway skill packages.", "read/debug", lambda _params: AGENT_GATEWAY.check_skill_registry())
    AGENT_GATEWAY.register_tool("vrcforge_tool_registry", "List standardized VRCForge tool metadata for Desktop, MCP, and CLI surfaces.", "read/debug", lambda _params: AGENT_GATEWAY.build_tool_registry())
    AGENT_GATEWAY.register_tool("vrcforge_external_agent_connectors", "Generate loopback MCP connector templates for external coding agents without exposing plaintext tokens.", "read/debug", connector_bundle_sync)
    AGENT_GATEWAY.register_tool("vrcforge_list_skill_packages", "List installed community .vsk skill packages.", "read/debug", list_skill_packages_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preflight_skill_package", "Inspect and verify a local .vsk skill package before import.", "plan/preview", preflight_skill_package_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_project_index", "Scan and update the local project index, returning only structural file deltas and scanner-family hints.", "read/debug", scan_project_index_sync)
    AGENT_GATEWAY.register_tool("vrcforge_inspect_outfit_package", "Inspect a UnityPackage, Booth ZIP/folder, or loose prefab/texture folder without reading paid asset binary contents.", "read/debug", inspect_outfit_package_sync)
    AGENT_GATEWAY.register_tool("vrcforge_plan_outfit_import", "Build a supervised import plan for a UnityPackage, Booth folder, or loose prefab/texture folder without writing Unity project files.", "plan/preview", plan_outfit_import_sync)
    AGENT_GATEWAY.register_tool("vrcforge_health", "Read VRCForge backend and component health.", "read/debug", lambda _params: read_health())
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
        lambda params: build_unity_tools_diagnostics(load_dashboard_settings(build_agent_connection_request(params))),
    )
    AGENT_GATEWAY.register_tool("vrcforge_list_avatars", "List avatars from the current Unity project.", "read/debug", lambda params: read_avatars_sync(build_agent_dashboard_request(params)))
    AGENT_GATEWAY.register_tool("vrcforge_scan_blendshapes", "Scan face-related Blendshapes for an avatar.", "read/debug", lambda params: read_avatar_blendshapes_sync(AvatarBlendshapeListRequest(**build_agent_dashboard_request(params).model_dump())))
    AGENT_GATEWAY.register_tool("vrcforge_scan_materials", "Scan shader/material inventory for an avatar.", "read/debug", lambda params: scan_shader_materials_sync(ShaderMaterialScanRequest(**params)))
    AGENT_GATEWAY.register_tool("vrcforge_scan_modular_avatar", "Detect the Modular Avatar package and scan avatars for Modular Avatar components.", "read/debug", lambda params: scan_addon_framework_sync("modular_avatar", params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_scan_vrcfury", "Detect the VRCFury package and scan avatars for VRCFury components.", "read/debug", lambda params: scan_addon_framework_sync("vrcfury", params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_scan_avatar_items", "Scan avatar hierarchy items including wardrobe-related objects and component types.", "read/debug", scan_avatar_items_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_fx_animator", "Scan FX animator layers, states, and parameters for an avatar.", "read/debug", scan_fx_animator_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_animation_bindings", "Scan animation clip bindings for an avatar or animator controller.", "read/debug", scan_animation_bindings_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_avatar_controls", "Scan expression menu controls and linked parameters for an avatar.", "read/debug", scan_avatar_controls_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_wardrobe", "Detect int-exclusive wardrobe(s) by reconciling an expression Int parameter, menu toggle values, FX Any-State Equals transitions, per-clip object on/off toggles, and Write Defaults.", "read/debug", scan_wardrobe_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_parameters", "Scan expression parameter usage for an avatar.", "read/debug", scan_avatar_parameters_gateway_sync)
    AGENT_GATEWAY.register_tool("vrcforge_run_validation_report", "Run the read-only vrcforge.validation.v1 report across compile, SDK, avatar, hierarchy, parameters, menu, FX, bindings, materials, performance, plugin, MCP, package, and residue checks.", "read/debug", build_validation_report_sync)
    AGENT_GATEWAY.register_tool("vrcforge_build_test_readiness", "Run the read-only Build & Test readiness gate without building, publishing, or repairing automatically.", "read/debug", build_test_readiness_sync)
    AGENT_GATEWAY.register_tool("vrcforge_optimization_plan", "Build the read-only vrcforge.optimization.v1 model optimization dashboard plan and recommended step order without modifying the Unity project.", "plan/preview", build_optimization_plan_sync)
    for definition in OPTIMIZATION_TOOL_DEFINITIONS:
        gateway_tool = definition["gatewayName"]
        external_tool = definition["externalName"]
        AGENT_GATEWAY.register_tool(
            gateway_tool,
            definition["description"],
            definition["category"],
            lambda params, _tool=external_tool: build_optimization_tool_sync(_tool, params or {}),
        )
    for definition in STABLE_OPTIMIZATION_APPLY_REQUEST_DEFINITIONS:
        gateway_tool = str(definition["gatewayName"])
        external_tool = str(definition["externalName"])
        AGENT_GATEWAY.register_tool(
            gateway_tool,
            str(definition["description"]),
            "supervised-write",
            lambda params, _tool=external_tool: request_optimization_apply_sync(
                {**ensure_dict(params or {}), "tool": _tool},
                agent_name=str(ensure_dict(params or {}).get("agent_name") or ensure_dict(params or {}).get("agentName") or "external-agent"),
            ),
            write=True,
        )
    AGENT_GATEWAY.register_tool("vrcforge_preview_ensure_expression_parameter", "Preview creating or updating an avatar expression parameter without writing.", "plan/preview", lambda params: ensure_expression_parameter_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_preview_ensure_expression_menu_control", "Preview creating or updating an expression menu control without writing.", "plan/preview", lambda params: ensure_expression_menu_control_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_preview_ensure_animator_state", "Preview creating or updating an FX animator layer/state/transition without writing.", "plan/preview", lambda params: ensure_animator_state_sync(params, preview=True))
    AGENT_GATEWAY.register_tool("vrcforge_create_safe_backup", "Create a safe backup snapshot of avatar assets and open scenes.", "plan/preview", create_safe_backup_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preview_restore_backup", "Preview which files a safe backup restore would overwrite, without writing.", "plan/preview", preview_safe_backup_restore_sync)
    AGENT_GATEWAY.register_tool("vrcforge_scan_avatar_performance", "Calculate VRChat SDK performance statistics and rank for an avatar.", "read/debug", scan_avatar_performance_sync)
    AGENT_GATEWAY.register_tool("vrcforge_package_manager_status", "Detect vrc-get/ALCOM/vpm CLIs and addon package install state.", "read/debug", package_manager_status_sync)
    AGENT_GATEWAY.register_tool("vrcforge_package_install_plan", "Plan a VPM package install using ALCOM/VCC UI handoff, VCC vpm CLI, vrc-get CLI, or agent-managed download fallback without writing.", "plan/preview", package_install_plan_sync)
    AGENT_GATEWAY.register_tool("vrcforge_package_install_request", "Request supervised VPM package installation through the selected package manager; creates an approval request only.", "supervised-write", lambda params: request_package_install_sync(params or {}, agent_name=str((params or {}).get("agent_name") or (params or {}).get("agentName") or "external-agent")), write=True)
    AGENT_GATEWAY.register_tool("vrcforge_diagnose_package_install_errors", "Read package-manager output and Unity compile errors to explain plugin/package install failures without repairing automatically.", "read/debug", diagnose_package_install_errors_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preview_setup_outfit", "Check Modular Avatar Setup Outfit readiness for an outfit object, without writing.", "plan/preview", preview_setup_outfit_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preview_add_wardrobe_outfit", "Preview adding one outfit to an existing int-exclusive wardrobe (assigned int value, FX state, on/off objects, menu placement), without writing.", "plan/preview", preview_add_wardrobe_outfit_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preview_add_outfit_part", "Preview adding an int-gated part toggle (e.g. a hat) to one outfit value of an int-exclusive wardrobe: Bool parameter, dedicated FX layer (int Equals N AND bool gating), on/off clips, and menu toggle, without writing.", "plan/preview", preview_add_outfit_part_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preview_add_modular_avatar_component", "Preview adding a common Modular Avatar component (MergeArmature, BoneProxy, MenuInstaller, MergeAnimator, Parameters) to a scene object, resolving references and fields, without writing.", "plan/preview", preview_add_modular_avatar_component_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preview_manage_wardrobe", "Preview destructive or structural wardrobe management actions (remove/rename/reorder outfits, set default value, delete wardrobe) without writing.", "plan/preview", preview_manage_wardrobe_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preview_create_wardrobe", "Preview creating an empty int-exclusive wardrobe skeleton (Int parameter, FX layer/default state, and menu), without writing.", "plan/preview", preview_create_wardrobe_sync)
    AGENT_GATEWAY.register_tool("vrcforge_preview_add_outfit", "Preview the full add-outfit workflow: resolve prefab, instantiate under avatar, run Setup Outfit, scan/create wardrobe if needed, and add the outfit to it.", "plan/preview", preview_add_outfit_workflow_sync)
    AGENT_GATEWAY.register_tool("vrcforge_list_checkpoints", "List pre-write git checkpoints created by VRCForge.", "read/debug", lambda params: AGENT_GATEWAY.list_checkpoints(params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_preview_restore_checkpoint", "Preview restoring Assets/Packages/ProjectSettings from a VRCForge checkpoint.", "plan/preview", lambda params: AGENT_GATEWAY.preview_restore_checkpoint(params or {}))
    AGENT_GATEWAY.register_tool("vrcforge_capture_status", "Read current Play Mode / Gesture Manager capture status.", "read/debug", lambda params: read_vision_capture_status_sync(VisionCaptureStatusRequest(**params)))
    AGENT_GATEWAY.register_tool("vrcforge_capture_screenshot", "Capture a Unity screenshot for real-scene debugging.", "read/debug", lambda params: capture_avatar_screenshot_sync(VisionCaptureRequest(**params)))
    AGENT_GATEWAY.register_tool("vrcforge_vision_audit", "Run advisory Vision audit on a captured screenshot.", "read/debug", lambda params: audit_avatar_screenshot_sync(VisionAuditRequest(**params)))
    AGENT_GATEWAY.register_tool("vrcforge_scan_thry_avatar_performance", "Call VRC Avatar Performance Tools / Thry read-only VRAM and mesh memory calculator for an avatar.", "read/debug", scan_thry_avatar_performance_sync)
    AGENT_GATEWAY.register_tool("vrcforge_read_recent_logs", "Read recent VRCForge dashboard logs.", "read/debug", lambda params: {"ok": True, "logs": recent_log_snapshot()[-int(params.get("limit", 80)):], "agentLogs": AGENT_GATEWAY.recent_audit_logs(limit=int(params.get("limit", 80)))})
    AGENT_GATEWAY.register_tool("vrcforge_roslyn_status", "Read Roslyn Advanced Power Mode diagnostics from Unity.", "read/debug", read_agent_roslyn_status)
    AGENT_GATEWAY.register_tool("vrcforge_get_compile_errors", "Read C# compile errors from the last Unity compilation pass.", "read/debug", read_agent_compile_errors)
    AGENT_GATEWAY.register_tool("vrcforge_get_property", "Read a single field/property value from a component on a scene GameObject.", "read/debug", read_component_property_sync)
    AGENT_GATEWAY.register_tool("vrcforge_get_gameobject", "Describe a scene GameObject: path, active state, tag/layer, parent, children, and components.", "read/debug", get_gameobject_sync)
    AGENT_GATEWAY.register_tool("vrcforge_find_assets", "Search the project for assets by query/type/folder.", "read/debug", find_assets_sync)
    AGENT_GATEWAY.register_tool("vrcforge_get_asset_info", "Describe a project asset: path, GUID, type, importer, and prefab details.", "read/debug", get_asset_info_sync)
    AGENT_GATEWAY.register_tool("vrcforge_plan_face_tuning", "Generate a face tuning plan without applying it.", "plan/preview", lambda params: run_dashboard_pipeline_sync(build_agent_dashboard_request(params), False))
    AGENT_GATEWAY.register_tool("vrcforge_plan_shader_tuning", "Generate a shader/material tuning plan without applying it.", "plan/preview", lambda params: generate_shader_material_plan_sync(build_agent_shader_request(params)))
    AGENT_GATEWAY.register_tool("vrcforge_preview_blendshape_apply", "Preview blendshape apply payload without writing to Unity.", "plan/preview", preview_agent_blendshape_apply)
    AGENT_GATEWAY.register_tool("vrcforge_preview_shader_apply", "Preview shader/material apply payload without writing to Unity.", "plan/preview", preview_agent_shader_apply)
    AGENT_GATEWAY.register_write_handler("vrcforge_import_skill_package", "Import a verified .vsk skill package into the user skill store.", "medium", import_skill_package_sync)
    AGENT_GATEWAY.register_write_handler("vrcforge_export_skill_package", "Export a user skill as a shareable .vsk package.", "medium", export_skill_package_sync)
    AGENT_GATEWAY.register_tool("vrcforge_request_apply", "Request user approval for a write operation.", "supervised-write", AGENT_GATEWAY.create_apply_request, write=True)
    AGENT_GATEWAY.register_tool("vrcforge_apply_approved", "Apply a previously approved write operation.", "supervised-write", AGENT_GATEWAY.apply_approved, write=True)
    AGENT_GATEWAY.register_tool("vrcforge_restore_last_backup", "Request approval to restore the last face or shader backup.", "supervised-write", request_agent_restore_last_backup, write=True)
    AGENT_GATEWAY.register_tool("vrcforge_request_roslyn_advanced", "Request Roslyn Advanced Power Mode execution with user approval.", "advanced", request_agent_roslyn_advanced, write=True, advanced=True)

    AGENT_GATEWAY.register_write_handler(
        "vrcforge_apply_blendshapes",
        "Apply validated Blendshape adjustments through VRCForge.",
        "medium",
        lambda params: apply_manual_blendshapes_sync(ManualBlendshapeApplyRequest(**params)),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_run_face_tuning",
        "Run and apply a generated face tuning plan through VRCForge.",
        "high",
        lambda params: run_dashboard_pipeline_sync(build_agent_dashboard_request(params), True),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_apply_shader_tuning",
        "Apply validated shader/material tuning changes through VRCForge.",
        "high",
        lambda params: apply_shader_material_plan_sync(ShaderMaterialApplyRequest(**params)),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_restore_shader_tuning",
        "Restore the last shader/material tuning undo point.",
        "medium",
        lambda params: restore_shader_material_plan_sync(ShaderMaterialRestoreRequest(**params)),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_undo_blendshapes",
        "Undo the last Blendshape apply snapshot for an avatar.",
        "medium",
        lambda params: undo_manual_blendshapes_sync(UndoBlendshapeRequest(**params)),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_apply_clothing_fx",
        "Apply generated clothing FX assets through VRCForge.",
        "high",
        lambda params: apply_clothing_fx_sync(ClothingApplyFxRequest(**params)),
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
        lambda params: rollback_parameter_optimization_sync(ParameterRollbackRequest(**params)),
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_setup_outfit",
        "Run Modular Avatar Setup Outfit on an outfit object through VRCForge.",
        "high",
        setup_outfit_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_add_wardrobe_outfit",
        "Add one outfit to an existing int-exclusive wardrobe (assign next int value, set new objects scene-default off, author an on/off clip, add an FX Any-State Equals state, and a menu toggle) through VRCForge.",
        "high",
        add_wardrobe_outfit_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_manage_wardrobe",
        "Manage an existing int-exclusive wardrobe: remove/rename/reorder outfits, set default value, or delete wardrobe bindings through VRCForge.",
        "high",
        manage_wardrobe_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_add_outfit_part",
        "Add an int-gated part toggle (e.g. a hat) to one outfit value of an existing int-exclusive wardrobe: create a Bool parameter, author a dedicated FX layer gated on (int Equals N AND bool), set the part scene-default off, and add a menu toggle through VRCForge.",
        "high",
        add_outfit_part_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_add_modular_avatar_component",
        "Add a common Modular Avatar component (MergeArmature, BoneProxy, MenuInstaller, MergeAnimator, Parameters) to a scene object, resolving AvatarObjectReference/asset references and scalar fields, through VRCForge.",
        "high",
        add_modular_avatar_component_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_create_wardrobe",
        "Create an empty int-exclusive wardrobe skeleton (expression Int parameter, FX layer/default state, and wardrobe menu) through VRCForge.",
        "high",
        create_wardrobe_sync,
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
        "vrcforge_add_outfit",
        "Run the semantic add-outfit workflow: instantiate a prefab under the avatar, run Modular Avatar Setup Outfit, scan/create an int-exclusive wardrobe if needed, and add the outfit to it.",
        "high",
        add_outfit_workflow_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_import_outfit_package",
        "Import a direct UnityPackage or copy loose outfit prefab/material/texture assets into the Unity project through VRCForge.",
        "high",
        import_outfit_package_sync,
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
        install_vpm_package_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_configure_optimizer_component",
        "Configure one delegated optimizer component on an avatar after approval; no external agent direct apply is exposed.",
        "high",
        configure_optimizer_component_sync,
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
        "vrcforge_unity_mcp_write",
        "Run a Unity MCP write tool through the VRCForge approval and rollback checkpoint boundary.",
        "high",
        unity_mcp_write_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_toggle_scene_object",
        "Toggle a scene object's active state (for example wardrobe items) through VRCForge.",
        "medium",
        toggle_scene_object_sync,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_roslyn_advanced",
        "Execute Roslyn Advanced Power Mode through Unity's warning dialog.",
        "critical",
        execute_agent_roslyn_advanced,
        advanced=True,
    )
    AGENT_GATEWAY.register_write_handler(
        "vrcforge_shell_execute",
        "Execute an approved high-risk shell command.",
        "high",
        AGENT_GATEWAY.execute_shell_payload,
    )


if DASHBOARD_API_CONFIG is None:
    DASHBOARD_API_CONFIG = load_initial_dashboard_api_config()


if DASHBOARD_STATE is None:
    DASHBOARD_STATE = load_initial_dashboard_state()

AGENT_GATEWAY.checkpoint_project_root_resolver = lambda: DASHBOARD_STATE.selected_project_path if DASHBOARD_STATE else ""
AGENT_GATEWAY.checkpoint_prepare_handler = prepare_unity_checkpoint_sync
AGENT_GATEWAY.checkpoint_restore_handler = reload_unity_checkpoint_sync

register_agent_gateway_tools()
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
            cli_args=raw_args[cli_index + 1 :],
        )
    parser = argparse.ArgumentParser(description="Launch the VRChat Blendshape control dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard bind host.")
    parser.add_argument("--port", default=8757, type=int, help="Dashboard bind port.")
    parser.add_argument("--agent-mcp-stdio", action="store_true", help="Run the external-agent stdio MCP bridge instead of the HTTP backend.")
    parser.add_argument("--preflight", action="store_true", help="With --agent-mcp-stdio, print a bridge preflight report and exit.")
    parser.add_argument("--json", action="store_true", help="Compatibility flag for preflight JSON output.")
    parser.add_argument("--cli", action="store_true", help="Run the VRCForge CLI against the local desktop runtime.")
    return parser.parse_args(raw_args)


def main() -> int:
    args = parse_args()
    if args.cli:
        from tools.vrcforge_cli import main as cli_main

        return cli_main(args.cli_args)
    if args.agent_mcp_stdio:
        from tools.vrcforge_agent_mcp_stdio import VRCForgeBridge, run_stdio_server

        bridge = VRCForgeBridge(
            base_url=os.environ.get("VRCFORGE_AGENT_BASE_URL", "http://127.0.0.1:8757").rstrip("/"),
            config_path=Path(os.environ["VRCFORGE_AGENT_GATEWAY_CONFIG"]).expanduser().resolve()
            if os.environ.get("VRCFORGE_AGENT_GATEWAY_CONFIG")
            else None,
            timeout_seconds=float(os.environ.get("VRCFORGE_AGENT_TIMEOUT", "30")),
            start_runtime=True,
        )
        if args.preflight:
            print(json.dumps(bridge.preflight(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        run_stdio_server(bridge)
        return 0
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

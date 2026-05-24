from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import json
import math
import mimetypes
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_gateway import AgentGateway, AgentGatewayError, create_agent_mcp_app
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


ROOT_DIR = resolve_runtime_path("VRCFORGE_APP_DIR", Path(__file__).resolve().parent)
PORTABLE_MODE = any(
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
USER_DATA_DIR = resolve_runtime_path("VRCFORGE_USER_DATA_DIR", ROOT_DIR)
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


class ApiConfigRequest(BaseModel):
    provider: str = DEFAULT_LLM_PROVIDER
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None


class ApiModelListRequest(ApiConfigRequest):
    pass


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
    cwd: str | None = None
    workspace_root: str | None = None


class AgentPermissionRequest(BaseModel):
    execution_mode: str = Field(default="approval")
    acknowledge_roslyn_risk: bool = Field(default=False)


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
                await websocket.send_json(message)
            except (WebSocketDisconnect, RuntimeError):
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


app = FastAPI(title="VRCForge Dashboard", version="0.3.1-alpha")
app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")

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
AGENT_MCP_MOUNT = AgentMcpMount()
AGENT_MCP_APP = None
AGENT_MCP_CONTEXT = None


@app.middleware("http")
async def authorize_agent_mcp(request: Request, call_next):
    if request.url.path == "/mcp" or request.url.path.startswith("/mcp/"):
        try:
            authenticate_agent_request(request, allow_disabled=False)
        except HTTPException as exc:
            return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@app.on_event("startup")
async def on_startup() -> None:
    global STATUS_MONITOR_TASK
    global AGENT_MCP_APP
    global AGENT_MCP_CONTEXT

    EVENT_BUS.set_loop(asyncio.get_running_loop())
    AGENT_MCP_APP = create_agent_mcp_app(AGENT_GATEWAY)
    AGENT_MCP_CONTEXT = AGENT_MCP_APP.state.fastmcp_server.session_manager.run()
    await AGENT_MCP_CONTEXT.__aenter__()
    AGENT_MCP_MOUNT.app = AGENT_MCP_APP
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
            "surface": "tauri-agentic-desktop",
            "browserRequired": False,
            "legacyDashboardDebugOnly": True,
        },
        "health": build_agentic_app_health(),
        "agentManifest": AGENT_GATEWAY.build_manifest(),
        "agentHealth": AGENT_GATEWAY.build_health(),
        "permission": AGENT_GATEWAY.permission_state(),
        "approvals": AGENT_GATEWAY.list_approvals(include_expired=False),
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
    await EVENT_BUS.broadcast("agentPermission", payload["permission"])
    return payload


@app.post("/api/app/agent/message")
async def app_agent_runtime_message(runtime_request: AgentRuntimeMessageRequest) -> dict[str, Any]:
    payload = AGENT_GATEWAY.runtime_message(
        {
            "session_id": runtime_request.session_id,
            "message": runtime_request.message,
            "shell_command": runtime_request.shell_command,
            "cwd": runtime_request.cwd,
            "workspace_root": runtime_request.workspace_root,
        },
        agent_name=runtime_request.agent_name,
    )
    await EVENT_BUS.broadcast("agentRuntimeTurn", payload)
    await EVENT_BUS.broadcast("agentApprovals", {"approvals": AGENT_GATEWAY.list_approvals()})
    return payload


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


@app.post("/api/app/agent/approvals/{approval_id}/approve")
async def app_agent_approve_and_execute(approval_id: str) -> dict[str, Any]:
    try:
        approved = AGENT_GATEWAY.approve(approval_id)
        execution = None
        approval = approved.get("approval") if isinstance(approved, dict) else None
        if isinstance(approval, dict) and approval.get("targetTool") == "vrcforge_shell_execute" and approved.get("ok"):
            execution = AGENT_GATEWAY.execute_approved_shell({"approval_id": approval_id})
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


def build_agentic_app_health() -> dict[str, Any]:
    payload = copy.deepcopy(read_health())
    payload.pop("apiConfig", None)
    return payload


@app.get("/api/agent/manifest")
def read_agent_manifest(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.build_manifest()


@app.get("/api/agent/health")
def read_agent_health(request: Request) -> dict[str, Any]:
    authenticate_agent_request(request, allow_disabled=True)
    return AGENT_GATEWAY.build_health()


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
    await EVENT_BUS.connect(websocket)
    try:
        await EVENT_BUS.send_to_client(websocket, "hello", await asyncio.to_thread(build_bootstrap_payload))
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

    DASHBOARD_API_CONFIG = normalize_api_config_request(request)
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
    return await asyncio.to_thread(run_dashboard_pipeline_sync, request, True)


@app.post("/api/blendshapes/apply")
async def apply_manual_blendshapes(request: ManualBlendshapeApplyRequest) -> dict[str, Any]:
    return await asyncio.to_thread(apply_manual_blendshapes_sync, request)


@app.post("/api/blendshapes/undo")
async def undo_manual_blendshapes(request: UndoBlendshapeRequest) -> dict[str, Any]:
    return await asyncio.to_thread(undo_manual_blendshapes_sync, request)


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
    return await asyncio.to_thread(apply_saved_tuning_history_sync, history_id, request)


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
    return await asyncio.to_thread(apply_saved_tuning_preset_sync, preset_id, request)


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
    return await asyncio.to_thread(toggle_clothing_sync, request)


@app.post("/api/clothes/generate-fx")
async def generate_clothing_fx(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(generate_clothing_fx_sync, request)


@app.post("/api/clothes/apply-fx")
async def apply_clothing_fx(request: ClothingApplyFxRequest) -> dict[str, Any]:
    return await asyncio.to_thread(apply_clothing_fx_sync, request)


@app.post("/api/parameters/scan")
async def scan_avatar_parameters(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(scan_avatar_parameters_sync, request)


@app.post("/api/parameters/optimize")
async def optimize_avatar_parameters(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(optimize_avatar_parameters_sync, request)


@app.post("/api/parameters/apply-optimization")
async def apply_parameter_optimization(request: ParameterApplyOptimizationRequest) -> dict[str, Any]:
    return await asyncio.to_thread(apply_parameter_optimization_sync, request)


@app.post("/api/parameters/rollback")
async def rollback_parameter_optimization(request: ParameterRollbackRequest) -> dict[str, Any]:
    return await asyncio.to_thread(rollback_parameter_optimization_sync, request)


@app.post("/api/shader/materials/scan")
async def scan_shader_materials(request: ShaderMaterialScanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(scan_shader_materials_sync, request)


@app.post("/api/shader/plan")
async def generate_shader_material_plan(request: ShaderMaterialPlanRequest) -> dict[str, Any]:
    return await asyncio.to_thread(generate_shader_material_plan_sync, request)


@app.post("/api/shader/apply")
async def apply_shader_material_plan(request: ShaderMaterialApplyRequest) -> dict[str, Any]:
    return await asyncio.to_thread(apply_shader_material_plan_sync, request)


@app.post("/api/shader/restore")
async def restore_shader_material_plan(request: ShaderMaterialRestoreRequest) -> dict[str, Any]:
    return await asyncio.to_thread(restore_shader_material_plan_sync, request)


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
    return await asyncio.to_thread(apply_saved_shader_history_sync, history_id, request)


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
    return await asyncio.to_thread(apply_saved_shader_preset_sync, preset_id, request)


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
            "executed": execute,
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
        applied = list(result.get("applied") or [])
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
        applied = list(result.get("applied") or [])
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
            if shader_family not in {"lilToon", "Poiyomi"}:
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
    latest_dir = ARTIFACTS_DIR / "latest"
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
        relative = path.relative_to(ARTIFACTS_DIR).as_posix()
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
        image_path = (ARTIFACTS_DIR / path_value[len("/artifacts/"):]).resolve()
    else:
        image_path = resolve_local_path(path_value)

    if not image_path.exists() or not image_path.is_file():
        raise RuntimeError(f"Reference image file does not exist: {image_path}")
    return image_path


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

    cutoff = datetime.now(timezone.utc) - LOG_RETENTION
    kept_lines: list[str] = []
    for line in LOCAL_LOG_PATH.read_text(encoding="utf-8").splitlines():
        if should_keep_log_line(line, cutoff):
            kept_lines.append(line)

    LOCAL_LOG_PATH.write_text(("\n".join(kept_lines) + "\n") if kept_lines else "", encoding="utf-8")


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
        "vrcForgeToolsRegistered": bool(tools.get("vrcForgeToolsCount")),
        "missingRequiredVrcForgeTools": tools.get("missingRequiredVrcForgeTools") or [],
        "output": output,
        "parsed": parsed,
        "error": "\n".join(errors),
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
    if CURRENT_UNITY_STATUS is None:
        status = build_unity_status_snapshot()
    else:
        status = CURRENT_UNITY_STATUS

    return {
        "health": read_health(),
        "state": serialize_dashboard_state(),
        "config": {
            "configPath": str(CONFIG_PATH),
            "apiConfig": serialize_api_config(include_secret=True),
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
        Path(os.environ.get("APPDATA", "")) / "VRChatCreatorCompanion" / "settings.json",
    ]
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
        raw_projects = payload.get("userProjects") or payload.get("projects") or []
        if isinstance(raw_projects, dict):
            raw_projects = raw_projects.values()
        for item in raw_projects if isinstance(raw_projects, list) else []:
            path = item.get("path") if isinstance(item, dict) else item
            if isinstance(path, str) and path.strip():
                projects.append(normalize_path_string(path))
    return sorted(dict.fromkeys(projects), key=str.casefold)


def extract_windows_paths_from_text(value: str) -> list[str]:
    import re

    paths: list[str] = []
    for match in re.finditer(r"[A-Za-z]:\\\\[^\"\\r\\n,]+(?:\\\\[^\"\\r\\n,]+)*", value):
        candidate = match.group(0).replace("\\\\", "\\").strip()
        if "\\unity" in candidate.casefold() or "\\projects" in candidate.casefold():
            paths.append(normalize_path_string(candidate))
    return paths


def discover_unity_hub_projects() -> list[dict[str, str]]:
    hub_projects = Path(os.environ.get("APPDATA", "")) / "UnityHub" / "projects-v1.json"
    if not hub_projects.exists():
        return []
    try:
        payload = json.loads(hub_projects.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return []
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return []
    projects: list[dict[str, str]] = []
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        path = normalize_path_string(str(value.get("path") or key or "").strip())
        if not path:
            continue
        projects.append(
            {
                "name": str(value.get("title") or value.get("name") or Path(path).name),
                "path": path,
                "editorVersion": str(value.get("version") or value.get("unityVersion") or "Unknown"),
            }
        )
    return projects


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
    CONFIG_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


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


def execute_agent_roslyn_advanced(params: dict[str, Any]) -> dict[str, Any]:
    if params.get("confirmAdvancedPowerMode") is not True:
        raise RuntimeError("confirmAdvancedPowerMode=true is required.")
    settings = load_dashboard_settings(ConnectionRequest(**params))
    result = invoke_unity_mcp(
        settings,
        "vrc_execute_roslyn",
        {
            "code": params.get("code") or "",
            "confirmAdvancedPowerMode": True,
            "enforceWriteDefaultsOn": params.get("enforceWriteDefaultsOn", True),
            "targetAvatarPaths": params.get("targetAvatarPaths") or [],
        },
    )
    return {"ok": True, "result": serialize_result(result)}


def register_agent_gateway_tools() -> None:
    AGENT_GATEWAY.register_tool("vrcforge_agent_observe", "Observe VRCForge agent runtime state.", "read/debug", lambda params: AGENT_GATEWAY.runtime_observe(str(params.get("session_id") or params.get("sessionId") or "")))
    AGENT_GATEWAY.register_tool("vrcforge_agent_message", "Run one VRCForge agent runtime turn.", "plan/preview", lambda params: AGENT_GATEWAY.runtime_message(params, agent_name=str(params.get("agent_name") or params.get("agentName") or "external-agent")))
    AGENT_GATEWAY.register_tool("vrcforge_classify_shell", "Classify a shell command before execution.", "read/debug", AGENT_GATEWAY.classify_shell)
    AGENT_GATEWAY.register_tool("vrcforge_execute_shell", "Execute low-risk shell commands or request approval for high-risk commands.", "supervised-write", lambda params: AGENT_GATEWAY.execute_shell(params, agent_name=str(params.get("agent_name") or params.get("agentName") or "external-agent")), write=True)
    AGENT_GATEWAY.register_tool("vrcforge_execute_approved_shell", "Execute a previously approved shell command payload.", "supervised-write", AGENT_GATEWAY.execute_approved_shell, write=True)
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
    AGENT_GATEWAY.register_tool("vrcforge_capture_status", "Read current Play Mode / Gesture Manager capture status.", "read/debug", lambda params: read_vision_capture_status_sync(VisionCaptureStatusRequest(**params)))
    AGENT_GATEWAY.register_tool("vrcforge_capture_screenshot", "Capture a Unity screenshot for real-scene debugging.", "read/debug", lambda params: capture_avatar_screenshot_sync(VisionCaptureRequest(**params)))
    AGENT_GATEWAY.register_tool("vrcforge_vision_audit", "Run advisory Vision audit on a captured screenshot.", "read/debug", lambda params: audit_avatar_screenshot_sync(VisionAuditRequest(**params)))
    AGENT_GATEWAY.register_tool("vrcforge_read_recent_logs", "Read recent VRCForge dashboard logs.", "read/debug", lambda params: {"ok": True, "logs": recent_log_snapshot()[-int(params.get("limit", 80)):], "agentLogs": AGENT_GATEWAY.recent_audit_logs(limit=int(params.get("limit", 80)))})
    AGENT_GATEWAY.register_tool("vrcforge_plan_face_tuning", "Generate a face tuning plan without applying it.", "plan/preview", lambda params: run_dashboard_pipeline_sync(build_agent_dashboard_request(params), False))
    AGENT_GATEWAY.register_tool("vrcforge_plan_shader_tuning", "Generate a shader/material tuning plan without applying it.", "plan/preview", lambda params: generate_shader_material_plan_sync(build_agent_shader_request(params)))
    AGENT_GATEWAY.register_tool("vrcforge_preview_blendshape_apply", "Preview blendshape apply payload without writing to Unity.", "plan/preview", preview_agent_blendshape_apply)
    AGENT_GATEWAY.register_tool("vrcforge_preview_shader_apply", "Preview shader/material apply payload without writing to Unity.", "plan/preview", preview_agent_shader_apply)
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


register_agent_gateway_tools()
app.mount("/", AGENT_MCP_MOUNT, name="agent_mcp")


def to_http_exception(exc: Exception) -> HTTPException:
    detail = str(exc)
    lowered = detail.lower()
    status_code = 503 if "unity mcp server is not ready yet" in lowered or "cannot connect to unity mcp server" in lowered else 400
    return HTTPException(status_code=status_code, detail=detail)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the VRChat Blendshape control dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard bind host.")
    parser.add_argument("--port", default=8757, type=int, help="Dashboard bind port.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

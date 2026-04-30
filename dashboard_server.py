from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import subprocess
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
    execute_csharp,
    get_provider_defaults,
    invoke_unity_mcp,
    load_export_payload,
    load_settings,
    mock_execute_csharp,
    normalize_base_url,
    normalize_provider_name,
    provider_display_name,
    read_plan_json,
    render_csharp,
    render_preview,
    render_summary,
    run_unity_mcp_passthrough,
    save_plan,
    save_result,
    save_text,
    try_parse_json,
    validate_plan,
    resolve_avatar_selection,
)


ROOT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = ROOT_DIR / "dashboard"
DASHBOARD_ARTIFACTS_DIR = ROOT_DIR / "artifacts" / "dashboard"
ARTIFACTS_DIR = ROOT_DIR / "artifacts"
TOOLS_DIR = ROOT_DIR / "tools"
INSTALL_SCRIPT_PATH = TOOLS_DIR / "install-unity-project.ps1"
CONFIG_PATH = ROOT_DIR / "config.json"

ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


class DashboardRequest(BaseModel):
    instruction: str | None = Field(default=None, description="Natural language instruction for LLM planning.")
    avatar: str | None = Field(default=None, description="Exact or partial avatar path/name.")
    model: str | None = Field(default=None, description="Optional model override.")
    source_mode: Literal["unity_live_export", "configured_export", "custom_export", "mvp_sample"] = "mvp_sample"
    export_json: str | None = Field(default=None, description="Optional local export JSON path.")
    plan_json: str | None = Field(default=None, description="Optional local plan JSON path.")
    settings_path: str = str(DEFAULT_SETTINGS_PATH)
    mock_execute: bool = True
    min_confidence: float | None = None
    allow_low_confidence: bool = False
    save_artifacts: bool = True
    unity_host: str | None = None
    unity_port: int | None = None
    unity_instance: str | None = None


class ConnectionRequest(BaseModel):
    settings_path: str = str(DEFAULT_SETTINGS_PATH)
    unity_host: str | None = None
    unity_port: int | None = None
    unity_instance: str | None = None


class DashboardStateRequest(BaseModel):
    settings_path: str = str(DEFAULT_SETTINGS_PATH)
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


class AvatarScopedConnectionRequest(ConnectionRequest):
    avatar_path: str | None = None


class ClothingToggleRequest(ConnectionRequest):
    object_path: str
    active: bool


class VisionCaptureRequest(ConnectionRequest):
    avatar_path: str | None = None
    width: int = 960
    height: int = 960


class VisionAuditRequest(ConnectionRequest):
    image_path: str | None = None


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
        await websocket.send_json(build_event_message(event_type, payload))

    async def broadcast(self, event_type: str, payload: Any) -> None:
        if not self._clients:
            return

        message = build_event_message(event_type, payload)
        stale_clients: list[WebSocket] = []
        for websocket in list(self._clients):
            try:
                await websocket.send_json(message)
            except Exception:
                stale_clients.append(websocket)

        for websocket in stale_clients:
            self._clients.discard(websocket)

    def broadcast_from_sync(self, event_type: str, payload: Any) -> None:
        if self._loop is None:
            return

        asyncio.run_coroutine_threadsafe(self.broadcast(event_type, payload), self._loop)


app = FastAPI(title="VRCAutoRig Dashboard", version="0.2.0")
app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR)), name="dashboard")
app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")

EVENT_BUS = DashboardEventBus()
RECENT_LOGS: deque[dict[str, Any]] = deque(maxlen=300)
CURRENT_UNITY_STATUS: dict[str, Any] | None = None
LAST_STATUS_FINGERPRINT = ""
LAST_STATUS_CONNECTED: bool | None = None
STATUS_MONITOR_TASK: asyncio.Task[None] | None = None
DASHBOARD_STATE: DashboardState | None = None
DASHBOARD_API_CONFIG: DashboardApiConfig | None = None
DASHBOARD_RUNTIME = DashboardRuntimeState()


@app.on_event("startup")
async def on_startup() -> None:
    global STATUS_MONITOR_TASK

    EVENT_BUS.set_loop(asyncio.get_running_loop())
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

    if STATUS_MONITOR_TASK is not None:
        STATUS_MONITOR_TASK.cancel()
        try:
            await STATUS_MONITOR_TASK
        except asyncio.CancelledError:
            pass
        STATUS_MONITOR_TASK = None


@app.get("/")
def read_dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/api/health")
def read_health() -> dict[str, Any]:
    settings = load_settings(
        resolve_local_path(DEFAULT_SETTINGS_PATH),
        llm_override=serialize_api_config(include_secret=True),
    )
    return {
        "ok": True,
        "projectRoot": str(ROOT_DIR),
        "settingsPath": str(resolve_local_path(DEFAULT_SETTINGS_PATH)),
        "configPath": str(CONFIG_PATH),
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
        "apiConfig": serialize_api_config(include_secret=True),
        "projects": project_snapshot_payload(),
        "recentLogs": list(RECENT_LOGS),
        "unityStatus": CURRENT_UNITY_STATUS,
    }


@app.websocket("/ws")
async def dashboard_socket(websocket: WebSocket) -> None:
    await EVENT_BUS.connect(websocket)
    try:
        await EVENT_BUS.send_to_client(websocket, "hello", await asyncio.to_thread(build_bootstrap_payload))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await EVENT_BUS.disconnect(websocket)


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

    await emit_log_async("info", "project", "Installing VRCAutoRig into Unity project.", {"projectPath": project_path})
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
    await emit_log_async("success", "project", "VRCAutoRig installed into Unity project.", {"projectPath": project_path})
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
    return await asyncio.to_thread(run_unity_cli_json, load_dashboard_settings(request), ["-f", "json", "status"])


@app.post("/api/unity/instances")
async def read_unity_instances(request: ConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(run_unity_cli_json, load_dashboard_settings(request), ["-f", "json", "instances"])


@app.post("/api/unity/tools")
async def read_unity_tools(request: ConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(run_unity_cli_json, load_dashboard_settings(request), ["-f", "json", "tool", "list"])


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


@app.post("/api/clothes/scan")
async def scan_clothes(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(scan_clothes_sync, request)


@app.post("/api/clothes/toggle")
async def toggle_clothing(request: ClothingToggleRequest) -> dict[str, Any]:
    return await asyncio.to_thread(toggle_clothing_sync, request)


@app.post("/api/clothes/generate-fx")
async def generate_clothing_fx(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(generate_clothing_fx_sync, request)


@app.post("/api/parameters/scan")
async def scan_avatar_parameters(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(scan_avatar_parameters_sync, request)


@app.post("/api/parameters/optimize")
async def optimize_avatar_parameters(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    return await asyncio.to_thread(optimize_avatar_parameters_sync, request)


@app.post("/api/vision/capture")
async def capture_avatar_screenshot(request: VisionCaptureRequest) -> dict[str, Any]:
    return await asyncio.to_thread(capture_avatar_screenshot_sync, request)


@app.post("/api/vision/audit")
async def audit_avatar_screenshot(request: VisionAuditRequest) -> dict[str, Any]:
    return await asyncio.to_thread(audit_avatar_screenshot_sync, request)


def read_avatars_sync(request: DashboardRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        export_payload, export_source, using_mock_execute = load_dashboard_export_payload(settings, request)
        avatars = serialize_avatar_list(export_payload)
        emit_log("info", "avatar", "Blendshape avatar export loaded.", {"count": len(avatars), "source": export_source})
        return {
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
        result = execute_dashboard_code(settings, build_avatar_descriptor_scan_code())
        avatars = ensure_list_payload(result, "scene avatar scan")
        DASHBOARD_RUNTIME.scene_avatars = avatars
        emit_log("info", "avatar", "Scene avatar scan completed.", {"count": len(avatars)})
        return {
            "ok": True,
            "avatars": avatars,
            "avatarCount": len(avatars),
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
        undo_items: list[dict[str, Any]] = []
        allowed_targets = build_allowed_blendshape_index(export_payload, selected_avatar.avatar_path)
        for item in request.adjustments:
            key = (item.renderer_path, item.blendshape_name)
            if key not in allowed_targets:
                raise RuntimeError(
                    f"Blendshape target does not exist on selected avatar: {item.renderer_path} :: {item.blendshape_name}"
                )

            current_weight = allowed_targets[key]["currentWeight"]
            previous_weight = current_weight if item.previous_weight is None else item.previous_weight
            validated_adjustments.append(
                {
                    "rendererPath": item.renderer_path,
                    "blendshapeName": item.blendshape_name,
                    "targetWeight": item.target_weight,
                }
            )
            undo_items.append(
                {
                    "rendererPath": item.renderer_path,
                    "blendshapeName": item.blendshape_name,
                    "targetWeight": previous_weight,
                }
            )

        code = render_manual_blendshape_csharp(selected_avatar.avatar_path, validated_adjustments)
        if using_mock_execute:
            result = mock_execute_csharp(code, selected_avatar, export_source)
        else:
            result = execute_csharp(settings, code, [selected_avatar.avatar_path])

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
        code = render_manual_blendshape_csharp(avatar_path, undo_items)
        result = execute_csharp(settings, code, [avatar_path])
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


def scan_clothes_sync(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        result = execute_dashboard_code(settings, build_clothes_scan_code(avatar_path), [avatar_path] if avatar_path else None)
        clothes = ensure_list_payload(result, "clothes scan")
        emit_log("info", "fx", "Clothing scan completed.", {"avatarPath": avatar_path, "count": len(clothes)})
        return {
            "ok": True,
            "avatarPath": avatar_path,
            "clothes": clothes,
            "count": len(clothes),
        }
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "fx", "Failed to scan clothing objects.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def toggle_clothing_sync(request: ClothingToggleRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        result = execute_dashboard_code(settings, build_clothing_toggle_code(request.object_path, request.active))
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
        result = execute_dashboard_code(settings, build_clothes_fx_blueprint_code(avatar_path), [avatar_path] if avatar_path else None)
        payload = ensure_dict_payload(result, "clothing fx blueprint")
        emit_log("success", "fx", "Clothing FX blueprint generated.", {"avatarPath": avatar_path, "itemCount": len(payload.get("items") or [])})
        return {"ok": True, "avatarPath": avatar_path, "fxBlueprint": payload}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "fx", "Failed to generate clothing FX blueprint.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def scan_avatar_parameters_sync(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        result = execute_dashboard_code(settings, build_parameter_scan_code(avatar_path), [avatar_path] if avatar_path else None)
        payload = ensure_dict_payload(result, "parameter scan")
        emit_log("info", "parameter", "Avatar parameter scan completed.", {"avatarPath": avatar_path})
        return {"ok": True, "avatarPath": avatar_path, "stats": payload}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "parameter", "Failed to scan avatar parameters.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def optimize_avatar_parameters_sync(request: AvatarScopedConnectionRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        avatar_path = request.avatar_path or DASHBOARD_RUNTIME.current_avatar_path
        result = execute_dashboard_code(settings, build_parameter_optimization_code(avatar_path), [avatar_path] if avatar_path else None)
        payload = ensure_dict_payload(result, "parameter optimization")
        emit_log("success", "parameter", "Avatar parameter optimization suggestions generated.", {"avatarPath": avatar_path})
        return {"ok": True, "avatarPath": avatar_path, "optimization": payload}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "parameter", "Failed to build parameter optimization suggestions.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def capture_avatar_screenshot_sync(request: VisionCaptureRequest) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        output_path = (DASHBOARD_ARTIFACTS_DIR / "latest" / "vision_capture.png").resolve()
        result = execute_dashboard_code(
            settings,
            build_screenshot_capture_code(output_path, request.width, request.height),
            [request.avatar_path] if request.avatar_path else None,
        )
        payload = ensure_dict_payload(result, "vision capture")
        image_path = payload.get("imagePath") or str(output_path)
        image_url = to_artifact_url(image_path)
        DASHBOARD_RUNTIME.latest_screenshot_path = image_path
        DASHBOARD_RUNTIME.latest_screenshot_url = image_url
        emit_log("success", "vision", "Screenshot captured for visual audit.", {"imagePath": image_path})
        return {"ok": True, "imagePath": image_path, "imageUrl": image_url, "capture": payload}
    except (RuntimeError, UnityMcpError) as exc:
        emit_log("error", "vision", "Failed to capture screenshot.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def audit_avatar_screenshot_sync(request: VisionAuditRequest) -> dict[str, Any]:
    try:
        image_path = request.image_path or DASHBOARD_RUNTIME.latest_screenshot_path
        if not image_path:
            raise RuntimeError("No screenshot is available yet. Capture a screenshot before running Gemini Vision audit.")

        image_file = resolve_local_path(image_path)
        if not image_file.exists():
            raise RuntimeError(f"Screenshot file does not exist: {image_file}")

        api_config = serialize_api_config(include_secret=True)
        if api_config.get("provider") != "gemini":
            raise RuntimeError("Gemini Vision audit currently requires the dashboard provider to be set to Gemini.")

        result = run_gemini_vision_audit(api_config, image_file)
        emit_log("success", "vision", "Gemini Vision audit completed.", {"status": result.get("status")})
        return {
            "ok": True,
            "imagePath": str(image_file),
            "imageUrl": to_artifact_url(str(image_file)),
            "audit": result,
        }
    except RuntimeError as exc:
        emit_log("error", "vision", "Failed to run Gemini Vision audit.", {"error": str(exc)})
        raise to_http_exception(exc) from exc


def run_dashboard_pipeline_sync(request: DashboardRequest, execute: bool) -> dict[str, Any]:
    try:
        settings = load_dashboard_settings(request)
        export_payload, export_source, using_mock_execute = load_dashboard_export_payload(settings, request)
        selected_avatar = resolve_avatar_selection(export_payload, request.avatar)
        remember_loaded_avatar(selected_avatar.avatar_name, selected_avatar.avatar_path)
        planning_payload = build_planning_payload(export_payload, selected_avatar)

        emit_log(
            "info",
            "pipeline",
            "Pipeline started.",
            {
                "avatarPath": selected_avatar.avatar_path,
                "mode": "execute" if execute else "plan",
                "executionMode": "mock" if using_mock_execute else "live-unity",
                "source": export_source,
            },
        )

        if request.plan_json:
            plan = read_plan_json(resolve_local_path(request.plan_json))
            emit_log("info", "pipeline", "Loaded local plan JSON.", {"planJson": request.plan_json})
        else:
            if not request.instruction:
                raise RuntimeError("instruction is required unless a local plan_json path is provided.")
            plan = create_blendshape_plan(settings, planning_payload, request.instruction)
            emit_log(
                "info",
                "pipeline",
                "LLM plan generated.",
                {
                    "instruction": request.instruction,
                    "provider": settings.llm_provider,
                    "model": settings.llm_model,
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
        csharp_code = render_csharp(plan)

        result: McpResult | None = None
        summary: str | None = None
        if execute:
            emit_log("info", "pipeline", "Executing generated C# snippet.", {"executionMode": "mock" if using_mock_execute else "live-unity"})
            if using_mock_execute:
                result = mock_execute_csharp(csharp_code, selected_avatar, export_source)
            else:
                result = execute_csharp(settings, csharp_code, [selected_avatar.avatar_path])
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
            artifacts = save_dashboard_artifacts(plan, csharp_code, preview, result, summary)
            emit_log("info", "artifact", "Dashboard artifacts saved.", {"runDirectory": artifacts["runDirectory"]})

        return {
            "exportSource": export_source,
            "executionMode": "mock" if using_mock_execute else "live-unity",
            "selectedAvatar": serialize_selected_avatar(selected_avatar),
            "availableAvatars": serialize_avatar_list(export_payload),
            "plan": plan.model_dump(),
            "preview": preview,
            "csharp": csharp_code,
            "result": serialize_result(result),
            "summary": summary,
            "artifacts": artifacts,
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


def execute_dashboard_code(
    settings: Settings,
    code: str,
    target_avatar_paths: list[str] | None = None,
) -> Any:
    result = invoke_unity_mcp(
        settings,
        settings.execute_tool_name,
        {
            "code": code,
            "enforceWriteDefaultsOn": False,
            "targetAvatarPaths": target_avatar_paths or [],
        },
    )
    payload = extract_tool_result_payload(result)
    if payload is None:
        raise RuntimeError("Unity MCP returned no structured result payload for the requested operation.")
    return payload


def extract_tool_result_payload(result: McpResult) -> Any:
    candidate: Any = result.payload
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


def ensure_list_payload(payload: Any, scope: str) -> list[Any]:
    if not isinstance(payload, list):
        raise RuntimeError(f"Expected {scope} to return a JSON array, got: {type(payload).__name__}")
    return payload


def ensure_dict_payload(payload: Any, scope: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected {scope} to return a JSON object, got: {type(payload).__name__}")
    return payload


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


def render_manual_blendshape_csharp(avatar_path: str, adjustments: list[dict[str, Any]]) -> str:
    lines = [
        "// Generated by dashboard manual blendshape apply",
        f"RoslynExecutor.Log({json.dumps(f'Applying {len(adjustments)} manual blendshape adjustments for {avatar_path}', ensure_ascii=False)});",
    ]
    for item in adjustments:
        lines.append(
            "RoslynExecutor.SetBlendshapeWeight("
            f"{json.dumps(avatar_path, ensure_ascii=False)}, "
            f"{json.dumps(item['rendererPath'], ensure_ascii=False)}, "
            f"{json.dumps(item['blendshapeName'], ensure_ascii=False)}, "
            f"{float(item['targetWeight']):.2f}f);"
        )
    lines.append("RoslynExecutor.SaveProjectAssets();")
    return "\n".join(lines)


def push_manual_undo_snapshot(avatar_path: str, adjustments: list[dict[str, Any]]) -> None:
    stack = DASHBOARD_RUNTIME.manual_undo_stack.setdefault(avatar_path, [])
    stack.append(adjustments)
    if len(stack) > 12:
        del stack[0]


def remember_loaded_avatar(avatar_name: str, avatar_path: str) -> None:
    DASHBOARD_RUNTIME.current_avatar_name = avatar_name
    DASHBOARD_RUNTIME.current_avatar_path = avatar_path


def save_dashboard_artifacts(
    plan: Any,
    csharp_code: str,
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
    run_csharp_path = run_dir / "apply.cs"
    run_preview_path = run_dir / "preview.txt"
    run_summary_path = run_dir / "summary.txt"
    run_result_path = run_dir / "result.json"

    latest_plan_path = latest_dir / "plan.json"
    latest_csharp_path = latest_dir / "apply.cs"
    latest_preview_path = latest_dir / "preview.txt"
    latest_summary_path = latest_dir / "summary.txt"
    latest_result_path = latest_dir / "result.json"

    save_plan(run_plan_path, plan)
    save_plan(latest_plan_path, plan)
    save_text(run_csharp_path, csharp_code)
    save_text(latest_csharp_path, csharp_code)
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
            "csharp": str(run_csharp_path),
            "preview": str(run_preview_path),
            "summary": str(run_summary_path) if summary else None,
            "result": str(run_result_path) if result else None,
        },
    }


def build_avatar_descriptor_scan_code() -> str:
    return """
var avatars = Resources.FindObjectsOfTypeAll<VRCAvatarDescriptor>()
    .Where(descriptor => descriptor != null
        && descriptor.gameObject.scene.IsValid()
        && descriptor.gameObject.scene.isLoaded
        && !EditorUtility.IsPersistent(descriptor))
    .OrderBy(descriptor => descriptor.name)
    .Select(descriptor => new
    {
        avatarName = descriptor.name,
        avatarPath = string.Join("/", descriptor.transform.GetComponentsInParent<Transform>(true).Reverse().Select(item => item.name)),
        sceneName = descriptor.gameObject.scene.name
    })
    .ToList();
return Newtonsoft.Json.JsonConvert.SerializeObject(avatars);
""".strip()


def build_clothes_scan_code(avatar_path: str | None) -> str:
    avatar_path_literal = json.dumps(avatar_path or "", ensure_ascii=False)
    return f"""
string avatarPath = {avatar_path_literal};
string Normalize(string value) => (value ?? string.Empty).Replace("\\\\", "/");
string GetPath(Transform transform) => string.Join("/", transform.GetComponentsInParent<Transform>(true).Reverse().Select(item => item.name));
var keywords = new[] {{ "cloth", "outfit", "dress", "shirt", "skirt", "pants", "shoe", "jacket", "hood", "hat", "accessory", "wear", "top", "bottom", "衣", "服", "裙", "裤", "鞋", "帽" }};
var root = Resources.FindObjectsOfTypeAll<Transform>()
    .Where(item => item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded && !EditorUtility.IsPersistent(item))
    .FirstOrDefault(item => Normalize(GetPath(item)) == Normalize(avatarPath));
if (root == null)
{{
    throw new InvalidOperationException($"Avatar root not found: {{avatarPath}}");
}}
var items = root
    .GetComponentsInChildren<Transform>(true)
    .Where(item => item != null && item != root)
    .Where(item => item.GetComponent<Renderer>() != null || item.GetComponentInChildren<Renderer>(true) != null)
    .Where(item => keywords.Any(keyword => item.name.IndexOf(keyword, StringComparison.OrdinalIgnoreCase) >= 0))
    .Select(item => new
    {{
        name = item.name,
        objectPath = GetPath(item),
        active = item.gameObject.activeSelf
    }})
    .Distinct()
    .OrderBy(item => item.name)
    .ToList();
if (items.Count == 0)
{{
    items = root
        .GetComponentsInChildren<Transform>(true)
        .Where(item => item.parent == root)
        .Where(item => item != null && (item.GetComponent<Renderer>() != null || item.GetComponentInChildren<Renderer>(true) != null))
        .Select(item => new
        {{
            name = item.name,
            objectPath = GetPath(item),
            active = item.gameObject.activeSelf
        }})
        .OrderBy(item => item.name)
        .ToList();
}}
return Newtonsoft.Json.JsonConvert.SerializeObject(items);
""".strip()


def build_clothing_toggle_code(object_path: str, active: bool) -> str:
    object_path_literal = json.dumps(object_path, ensure_ascii=False)
    active_literal = "true" if active else "false"
    return f"""
string targetPath = {object_path_literal};
bool targetActive = {active_literal};
string Normalize(string value) => (value ?? string.Empty).Replace("\\\\", "/");
string GetPath(Transform transform) => string.Join("/", transform.GetComponentsInParent<Transform>(true).Reverse().Select(item => item.name));
var target = Resources.FindObjectsOfTypeAll<Transform>()
    .Where(item => item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded && !EditorUtility.IsPersistent(item))
    .FirstOrDefault(item => Normalize(GetPath(item)) == Normalize(targetPath));
if (target == null)
{{
    throw new InvalidOperationException($"Clothing object not found: {{targetPath}}");
}}
target.gameObject.SetActive(targetActive);
EditorUtility.SetDirty(target.gameObject);
EditorSceneManager.MarkSceneDirty(target.gameObject.scene);
RoslynExecutor.SaveProjectAssets();
return Newtonsoft.Json.JsonConvert.SerializeObject(new
{{
    objectPath = targetPath,
    active = target.gameObject.activeSelf
}});
""".strip()


def build_clothes_fx_blueprint_code(avatar_path: str | None) -> str:
    avatar_path_literal = json.dumps(avatar_path or "", ensure_ascii=False)
    return f"""
string avatarPath = {avatar_path_literal};
string Normalize(string value) => (value ?? string.Empty).Replace("\\\\", "/");
string GetPath(Transform transform) => string.Join("/", transform.GetComponentsInParent<Transform>(true).Reverse().Select(item => item.name));
var root = Resources.FindObjectsOfTypeAll<Transform>()
    .Where(item => item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded && !EditorUtility.IsPersistent(item))
    .FirstOrDefault(item => Normalize(GetPath(item)) == Normalize(avatarPath));
if (root == null)
{{
    throw new InvalidOperationException($"Avatar root not found: {{avatarPath}}");
}}
var clothes = root
    .GetComponentsInChildren<Transform>(true)
    .Where(item => item != null && item.GetComponent<Renderer>() != null)
    .GroupBy(item => item.name)
    .Select(group => new
    {{
        displayName = group.Key,
        parameterName = $"Cloth_{{group.Key.Replace(" ", string.Empty)}}",
        animationClipName = $"FX_{{group.Key.Replace(" ", string.Empty)}}_Toggle",
        bindingCount = group.Count(),
        sampleObjectPath = GetPath(group.First())
    }})
    .OrderBy(item => item.displayName)
    .ToList();
return Newtonsoft.Json.JsonConvert.SerializeObject(new
{{
    mode = "blueprint",
    note = "MVP currently generates an FX blueprint and parameter naming suggestion. It does not yet author AnimatorController assets automatically.",
    items = clothes
}});
""".strip()


def build_parameter_scan_code(avatar_path: str | None) -> str:
    avatar_path_literal = json.dumps(avatar_path or "", ensure_ascii=False)
    return f"""
string avatarPath = {avatar_path_literal};
string Normalize(string value) => (value ?? string.Empty).Replace("\\\\", "/");
string GetPath(Transform transform) => string.Join("/", transform.GetComponentsInParent<Transform>(true).Reverse().Select(item => item.name));
var descriptor = Resources.FindObjectsOfTypeAll<VRCAvatarDescriptor>()
    .Where(item => item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded && !EditorUtility.IsPersistent(item))
    .FirstOrDefault(item => string.IsNullOrWhiteSpace(avatarPath) || Normalize(GetPath(item.transform)) == Normalize(avatarPath));
if (descriptor == null)
{{
    throw new InvalidOperationException("Could not locate a VRCAvatarDescriptor for parameter scan.");
}}
var parametersAsset = descriptor.expressionParameters;
var parameters = parametersAsset != null && parametersAsset.parameters != null
    ? parametersAsset.parameters.Where(parameter => parameter != null).ToArray()
    : Array.Empty<VRCExpressionParameters.Parameter>();
var boolCount = parameters.Count(parameter => parameter.valueType == VRCExpressionParameters.ValueType.Bool);
var intCount = parameters.Count(parameter => parameter.valueType == VRCExpressionParameters.ValueType.Int);
var floatCount = parameters.Count(parameter => parameter.valueType == VRCExpressionParameters.ValueType.Float);
var totalCost = parameters.Sum(parameter =>
    parameter.valueType == VRCExpressionParameters.ValueType.Bool ? 1 :
    parameter.valueType == VRCExpressionParameters.ValueType.Int ? 8 : 8);
return Newtonsoft.Json.JsonConvert.SerializeObject(new
{{
    boolCount,
    intCount,
    floatCount,
    totalParameters = parameters.Length,
    totalEstimatedCost = totalCost,
    parameterNames = parameters.Select(parameter => new
    {{
        name = parameter.name,
        valueType = parameter.valueType.ToString(),
        saved = parameter.saved,
        networkSynced = parameter.networkSynced,
        defaultValue = parameter.defaultValue
    }})
}});
""".strip()


def build_parameter_optimization_code(avatar_path: str | None) -> str:
    avatar_path_literal = json.dumps(avatar_path or "", ensure_ascii=False)
    return f"""
string avatarPath = {avatar_path_literal};
string Normalize(string value) => (value ?? string.Empty).Replace("\\\\", "/");
string GetPath(Transform transform) => string.Join("/", transform.GetComponentsInParent<Transform>(true).Reverse().Select(item => item.name));
var descriptor = Resources.FindObjectsOfTypeAll<VRCAvatarDescriptor>()
    .Where(item => item != null && item.gameObject.scene.IsValid() && item.gameObject.scene.isLoaded && !EditorUtility.IsPersistent(item))
    .FirstOrDefault(item => string.IsNullOrWhiteSpace(avatarPath) || Normalize(GetPath(item.transform)) == Normalize(avatarPath));
if (descriptor == null)
{{
    throw new InvalidOperationException("Could not locate a VRCAvatarDescriptor for parameter optimization.");
}}
var parametersAsset = descriptor.expressionParameters;
var parameters = parametersAsset != null && parametersAsset.parameters != null
    ? parametersAsset.parameters.Where(parameter => parameter != null).ToArray()
    : Array.Empty<VRCExpressionParameters.Parameter>();
var suggestions = parameters
    .Where(parameter => parameter.valueType == VRCExpressionParameters.ValueType.Int)
    .Where(parameter =>
        Math.Abs(parameter.defaultValue) <= 1.0f
        || parameter.name.IndexOf("toggle", StringComparison.OrdinalIgnoreCase) >= 0
        || parameter.name.IndexOf("enable", StringComparison.OrdinalIgnoreCase) >= 0
        || parameter.name.IndexOf("show", StringComparison.OrdinalIgnoreCase) >= 0
        || parameter.name.IndexOf("hide", StringComparison.OrdinalIgnoreCase) >= 0
        || parameter.name.IndexOf("is", StringComparison.OrdinalIgnoreCase) == 0)
    .Select(parameter => new
    {{
        name = parameter.name,
        currentType = parameter.valueType.ToString(),
        suggestedType = "Bool",
        reason = "Heuristic match: this Int parameter looks binary and may be reducible to Bool."
    }})
    .ToList();
return Newtonsoft.Json.JsonConvert.SerializeObject(new
{{
    suggestionCount = suggestions.Count,
    suggestions,
    note = "Suggestions are heuristic only. Review animator conditions and menu bindings before changing parameter types."
}});
""".strip()


def build_screenshot_capture_code(output_path: Path, width: int, height: int) -> str:
    output_path_literal = json.dumps(str(output_path), ensure_ascii=False)
    safe_width = max(256, min(width, 2048))
    safe_height = max(256, min(height, 2048))
    return f"""
string outputPath = {output_path_literal};
int width = {safe_width};
int height = {safe_height};
var sceneView = SceneView.lastActiveSceneView;
if (sceneView == null || sceneView.camera == null)
{{
    throw new InvalidOperationException("No active SceneView is available for screenshot capture.");
}}
Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
var camera = sceneView.camera;
var renderTexture = new RenderTexture(width, height, 24);
var texture = new Texture2D(width, height, TextureFormat.RGB24, false);
var previousTarget = camera.targetTexture;
var previousActive = RenderTexture.active;
camera.targetTexture = renderTexture;
RenderTexture.active = renderTexture;
camera.Render();
texture.ReadPixels(new Rect(0, 0, width, height), 0, 0);
texture.Apply();
camera.targetTexture = previousTarget;
RenderTexture.active = previousActive;
var bytes = texture.EncodeToPNG();
File.WriteAllBytes(outputPath, bytes);
UnityEngine.Object.DestroyImmediate(renderTexture);
UnityEngine.Object.DestroyImmediate(texture);
return Newtonsoft.Json.JsonConvert.SerializeObject(new
{{
    imagePath = outputPath,
    width,
    height
}});
""".strip()


def to_artifact_url(path_value: str) -> str:
    try:
        path = resolve_local_path(path_value)
        relative = path.relative_to(ARTIFACTS_DIR).as_posix()
        return f"/artifacts/{relative}"
    except Exception:
        return ""


def run_gemini_vision_audit(api_config: dict[str, Any], image_path: Path) -> dict[str, Any]:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("The google-genai package is not installed.") from exc

    api_key = str(api_config.get("api_key") or "").strip()
    model = str(api_config.get("model") or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    if not api_key:
        raise RuntimeError("Gemini API key is empty. Save a Gemini provider config before running vision audit.")

    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    client = genai.Client(api_key=api_key)
    image_bytes = image_path.read_bytes()
    prompt = (
        "你是 VRChat Avatar 视觉质检助手。检查这张 Avatar 截图是否存在明显穿模、衣物穿插、头发穿插或严重视觉问题。"
        "只输出 JSON，不要 Markdown。格式为："
        '{"status":"pass|clipping","summary":"一句话结论","issues":["问题1","问题2"]}'
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
        raise RuntimeError("Gemini Vision did not return valid JSON.")
    return payload


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
    RECENT_LOGS.append(entry)
    EVENT_BUS.broadcast_from_sync("log", entry)


async def emit_log_async(level: str, scope: str, message: str, data: dict[str, Any] | None = None) -> None:
    entry = build_log_entry(level, scope, message, data)
    RECENT_LOGS.append(entry)
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


def build_unity_status_snapshot() -> dict[str, Any]:
    settings = load_dashboard_settings(ConnectionRequest(settings_path=str(DASHBOARD_STATE.settings_path)))
    settings.unity_mcp_timeout_seconds = min(settings.unity_mcp_timeout_seconds, 5)

    try:
        output = run_unity_mcp_passthrough(settings, ["-f", "json", "status"])
        parsed = try_parse_json(output)
        return {
            "connected": True,
            "host": settings.unity_mcp_host,
            "port": settings.unity_mcp_port,
            "instance": settings.unity_mcp_instance,
            "projectPath": DASHBOARD_STATE.selected_project_path,
            "output": output,
            "parsed": parsed,
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "connected": False,
            "host": settings.unity_mcp_host,
            "port": settings.unity_mcp_port,
            "instance": settings.unity_mcp_instance,
            "projectPath": DASHBOARD_STATE.selected_project_path,
            "output": "",
            "parsed": None,
            "error": str(exc),
        }


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
        "recentLogs": list(RECENT_LOGS),
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
    projects = discover_projects(DASHBOARD_STATE.project_roots)
    return {
        "selectedProjectPath": DASHBOARD_STATE.selected_project_path,
        "unityEditorPath": DASHBOARD_STATE.unity_editor_path,
        "projects": projects,
    }


def discover_projects(project_roots: list[Path]) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    seen: set[str] = set()

    for root in project_roots:
        if not root.exists():
            continue

        for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if not child.is_dir():
                continue

            version_file = child / "ProjectSettings" / "ProjectVersion.txt"
            if not version_file.exists():
                continue

            normalized_path = normalize_path_string(str(child))
            if normalized_path in seen:
                continue
            seen.add(normalized_path)

            version = parse_editor_version(version_file)
            has_vrc_auto_rig = (child / "Assets" / "VRCAutoRig" / "Editor" / "BlendshapeExporter.cs").exists()
            has_unity_mcp = has_unity_mcp_dependency(child / "Packages" / "manifest.json")

            projects.append(
                {
                    "name": child.name,
                    "path": normalized_path,
                    "editorVersion": version,
                    "hasVrcAutoRig": has_vrc_auto_rig,
                    "hasUnityMcpPackage": has_unity_mcp,
                    "selected": normalized_path == normalize_path_string(DASHBOARD_STATE.selected_project_path),
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
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
    return str(Path(value)).replace("\\", "/")


def load_initial_dashboard_api_config() -> DashboardApiConfig:
    settings_path = resolve_local_path(DEFAULT_SETTINGS_PATH)
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
        "usesBaseUrl": config.provider != "anthropic",
        "authHeader": "x-api-key" if config.provider == "anthropic" else "Authorization: Bearer",
    }


def build_effective_model_summary() -> dict[str, Any]:
    config = DASHBOARD_API_CONFIG or load_initial_dashboard_api_config()
    return {
        "provider": config.provider,
        "providerLabel": provider_display_name(config.provider),
        "model": config.model,
        "baseUrl": config.base_url,
        "authHeader": "x-api-key" if config.provider == "anthropic" else "Authorization: Bearer",
    }


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * max(len(value) - 8, 4)}{value[-4:]}"


def load_initial_dashboard_state() -> DashboardState:
    settings_path = resolve_local_path(DEFAULT_SETTINGS_PATH)
    settings = load_settings(
        settings_path,
        llm_override=serialize_api_config(include_secret=True) if DASHBOARD_API_CONFIG is not None else None,
    )
    raw = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    dashboard_settings = raw.get("dashboard") or {}

    project_roots = [Path(path) for path in dashboard_settings.get("project_roots", ["E:/unity/Projects"])]
    unity_editor_path = str(dashboard_settings.get("unity_editor_path", "E:/unity/Unity 2022.3.22f1/Editor/Unity.exe")).strip()
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


if DASHBOARD_API_CONFIG is None:
    DASHBOARD_API_CONFIG = load_initial_dashboard_api_config()


if DASHBOARD_STATE is None:
    DASHBOARD_STATE = load_initial_dashboard_state()


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

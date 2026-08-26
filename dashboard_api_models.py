from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

from chat_attachment_vault import ARCHIVE_MAX_BYTES
from dashboard_foundation import runtime_settings_path


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
    # Face remains the scanner's compatibility default; `all` is for mesh work
    # that must inspect clothing and accessory blendshapes too.
    scope: str | None = None
    filter_scope: str | None = Field(default=None, alias="filterScope")

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
    provider: str = "gemini"
    api_key: str = ""
    base_url: str | None = None
    model: str | None = None
    # ``None`` means an on-disk/request legacy configuration: preserve its
    # provider's historical transport instead of treating it as new ``auto``.
    api_type: str | None = None
    # Model-aware reasoning variant; empty means provider default/no override.
    thinking_level: str = ""
    # 0 keeps provider/model auto-detection. A positive value is a user cap;
    # it never expands a smaller provider-advertised window.
    context_window: int = Field(default=0, ge=0, le=10_000_000)


class ApiModelListRequest(ApiConfigRequest):
    pass


class ReasoningVariantsRequest(BaseModel):
    provider: str = "gemini"
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
        default_factory=lambda: ["liltoon", "poiyomi"],
        alias="targetShaderFamilies",
    )
    material_ids: list[str] = Field(default_factory=list, alias="materialIds")
    renderer_paths: list[str] = Field(default_factory=list, alias="rendererPaths")
    targets: list[dict[str, Any]] = Field(default_factory=list)
    profile: str = "standard"
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


class CameraVector3(BaseModel):
    """Finite Unity camera vector; unknown fields are deliberately rejected."""

    x: float
    y: float
    z: float

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def finite(self) -> "CameraVector3":
        if not all(math.isfinite(value) for value in (self.x, self.y, self.z)):
            raise ValueError("camera vectors must contain finite values")
        return self


class VisionCaptureRequest(ConnectionRequest):
    camera_mode: Literal["framed", "free"] = Field(default="framed", alias="cameraMode")
    camera_position: CameraVector3 | None = Field(default=None, alias="cameraPosition")
    target_position: CameraVector3 | None = Field(default=None, alias="targetPosition")
    up_vector: CameraVector3 | None = Field(default=None, alias="upVector")
    projection: Literal["perspective", "orthographic"] | None = None
    orthographic_size: float | None = Field(default=None, alias="orthographicSize", gt=0)
    field_of_view: float | None = Field(default=None, alias="fieldOfView", gt=0, le=179)
    avatar_path: str | None = Field(default=None, alias="avatarPath")
    angle: str | None = None
    framing: Literal["face", "avatar"] | None = None
    capture_scope: Literal["face", "avatar"] | None = Field(default=None, alias="captureScope")
    pitch: float | None = Field(default=None, ge=-180.0, le=180.0)
    yaw: float | None = Field(default=None, ge=-180.0, le=180.0)
    roll: float | None = Field(default=None, ge=-180.0, le=180.0)
    width: int = Field(default=960, ge=256, le=2048)
    height: int = Field(default=960, ge=256, le=2048)
    require_play_mode: bool = Field(default=False, alias="requirePlayMode")
    capture_mode: Literal["auto", "scene_view", "game_view"] = Field(default="auto", alias="captureMode")

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_camera_contract(self) -> "VisionCaptureRequest":
        camera_fields = (self.camera_position, self.target_position, self.up_vector)
        if self.camera_mode == "free":
            if self.angle or any(value is not None for value in (self.pitch, self.yaw, self.roll)):
                raise ValueError("free cameraMode excludes named angles and framed pitch/yaw/roll")
            if self.framing not in (None, "avatar") or self.capture_scope not in (None, "avatar"):
                raise ValueError("free cameraMode requires avatar framing/captureScope")
            if any(value is None for value in camera_fields):
                raise ValueError("free cameraMode requires cameraPosition, targetPosition, and upVector")
            if self.projection is None:
                raise ValueError("free cameraMode requires projection")
            if self.projection == "orthographic":
                if self.orthographic_size is None or self.field_of_view is not None:
                    raise ValueError("orthographic free camera requires orthographicSize and excludes fieldOfView")
            elif self.field_of_view is None or self.orthographic_size is not None:
                raise ValueError("perspective free camera requires fieldOfView and excludes orthographicSize")
        elif any(value is not None for value in (*camera_fields, self.projection, self.orthographic_size, self.field_of_view)):
            raise ValueError("cameraPosition, targetPosition, upVector, projection, size and fov are free-camera-only")
        return self


class VisionCaptureStatusRequest(ConnectionRequest):
    require_play_mode: bool = Field(default=False, alias="requirePlayMode")
    capture_mode: Literal["auto", "scene_view", "game_view"] = Field(default="auto", alias="captureMode")

    model_config = {"populate_by_name": True}


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
    avatar_path: str | None = Field(default=None, alias="avatarPath")
    angles: list[str] = Field(default_factory=lambda: ["front", "side_left", "side_right", "back", "bottom"])
    framing: Literal["face", "avatar"] | None = None
    width: int = Field(default=960, ge=256, le=2048)
    height: int = Field(default=960, ge=256, le=2048)
    require_play_mode: bool = Field(default=False, alias="requirePlayMode")
    capture_mode: Literal["auto", "scene_view", "game_view"] = Field(default="auto", alias="captureMode")

    model_config = {"populate_by_name": True}


class VisionAuditMultiRequest(BaseModel):
    image_paths: list[str] = Field(default_factory=list, alias="imagePaths")
    angles: list[str] = Field(default_factory=lambda: ["front", "side_left", "side_right", "back", "bottom"])

    model_config = {"populate_by_name": True, "extra": "forbid"}


class AgentVisionAuditRequest(BaseModel):
    model_config = {"extra": "forbid"}


class ManagedVisionAuditMultiRequest(BaseModel):
    capture_receipt: str = Field(alias="captureReceipt", min_length=1, max_length=256)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class AgentToolRequest(BaseModel):
    agent_name: str = "external-agent"
    params: dict[str, Any] = Field(default_factory=dict)


class AgentSessionRequest(BaseModel):
    agent_name: str = "external-agent"


class AgentRuntimeMessageRequest(BaseModel):
    agent_name: str = "desktop-agent"
    session_id: str | None = None
    chat_id: str | None = Field(default=None, alias="chatId")
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
    project_type: Literal["general", "unity"] | None = Field(default=None, alias="projectType")
    provider: str | None = None
    provider_label: str | None = Field(default=None, alias="providerLabel")
    model: str | None = None
    context_limit: int | None = Field(default=None, alias="contextLimit", gt=0, le=10_000_000)
    max_agentic_turns: int | None = Field(default=None, alias="maxAgenticTurns", ge=1, le=4096)
    history: list[dict[str, Any]] = Field(default_factory=list)
    computer_use_requested: bool = Field(default=False, alias="computerUseRequested")
    computer_use_grant_id: str | None = Field(default=None, alias="computerUseGrantId")
    computer_use_visual_theme: str | None = Field(default=None, alias="computerUseVisualTheme")
    computer_use_visual_accent: str | None = Field(default=None, alias="computerUseVisualAccent")
    followup_queue_id: str | None = Field(default=None, alias="followupQueueId", max_length=180)
    followup_lane_id: str | None = Field(default=None, alias="followupLaneId", max_length=180)

    model_config = {"populate_by_name": True}


class AgentHarnessJourneyReceiptRequest(BaseModel):
    receipt: dict[str, Any]


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
    session_id: str | None = Field(default=None, alias="sessionId", min_length=1, max_length=180)
    lane_id: str | None = Field(default=None, alias="laneId", min_length=1, max_length=180)
    client_turn_id: str = Field(alias="clientTurnId", min_length=1, max_length=180)
    target_client_turn_id: str | None = Field(default=None, alias="targetClientTurnId", min_length=1, max_length=180)
    message: str = Field(default="", max_length=4000)
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    provider: str | None = None
    provider_label: str | None = Field(default=None, alias="providerLabel")
    model: str | None = None
    project_path: str | None = Field(default=None, alias="projectPath")
    project_root: str | None = Field(default=None, alias="projectRoot")
    project_type: Literal["general", "unity"] | None = Field(default=None, alias="projectType")


class AgentRuntimeQueueClaimRequest(BaseModel):
    session_id: str = Field(alias="sessionId", min_length=1, max_length=180)
    owner_id: str = Field(alias="ownerId", min_length=1, max_length=180)
    limit: int = Field(default=8, ge=1, le=64)
    queue_id: str | None = Field(default=None, alias="queueId", max_length=180)


class AgentRuntimeQueueAckRequest(BaseModel):
    session_id: str = Field(alias="sessionId", min_length=1, max_length=180)
    claim_token: str = Field(alias="claimToken", min_length=1, max_length=256)


class AgentRuntimeQueueCancelRequest(BaseModel):
    session_id: str = Field(alias="sessionId", min_length=1, max_length=180)
    claim_token: str | None = Field(default=None, alias="claimToken", max_length=256)

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
    reason: str = Field(default="", max_length=1000)
    deny_reason_code: str = Field(default="", alias="denyReasonCode")
    note: str = Field(default="", max_length=2000)
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
    max_agentic_turns: int | None = Field(default=None, alias="maxAgenticTurns", ge=1, le=4096)

    model_config = {"populate_by_name": True}


class AgentNotesRequest(BaseModel):
    content: str = Field(default="", max_length=262144)


class ChatTranscriptsRequest(BaseModel):
    chats: list[dict[str, Any]] = Field(default_factory=list)
    source_revisions: list[dict[str, Any]] = Field(default_factory=list, alias="sourceRevisions")

    model_config = {"populate_by_name": True}


class SessionHandoffSendRequest(BaseModel):
    source_chat_id: str = Field(alias="sourceChatId", min_length=1, max_length=180)
    target_chat_id: str = Field(alias="targetChatId", min_length=1, max_length=180)
    payload: dict[str, Any] = Field(default_factory=dict)
    kind: str = Field(default="handoff", max_length=32)
    reply_to: str | None = Field(default=None, alias="replyTo")
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
    custom_projects: list[dict[str, Any]] = Field(default_factory=list, alias="customProjects")
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
    client: Literal["codex", "codexApp", "codexCli", "claudeCode", "claudeCowork", "generic", "deepseekHarness"]
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


class OfficialSkillSigningKeyExportRequest(BaseModel):
    output_path: str = Field(alias="outputPath")
    passphrase: SecretStr = Field(min_length=8)

    model_config = {"populate_by_name": True}


class OfficialSkillSigningKeyImportRequest(BaseModel):
    backup_path: str = Field(alias="backupPath")
    passphrase: SecretStr = Field(min_length=8)
    replace: bool = False

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
        max_length=256,
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

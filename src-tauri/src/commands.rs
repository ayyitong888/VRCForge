#![allow(unused_imports)]

use crate::backend::*;
use crate::event_bridge::*;
use crate::sanitize::*;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::{
    env, fs,
    net::{TcpStream, ToSocketAddrs},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, State,
};
use tungstenite::client::IntoClientRequest;
use tungstenite::http::HeaderValue;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopRuntimeSnapshotRequest {
    session_id: Option<String>,
    project_root: Option<String>,
    include_patch: Option<bool>,
    global_only: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
pub(crate) struct DesktopProviderConfigRequest {
    provider: String,
    #[serde(default, alias = "apiKey")]
    api_key: Option<String>,
    #[serde(default, alias = "baseUrl")]
    base_url: Option<String>,
    model: Option<String>,
    #[serde(default, alias = "timeoutMs")]
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
pub(crate) struct DesktopVisionConfigRequest {
    provider: String,
    #[serde(default, alias = "apiKey")]
    api_key: Option<String>,
    #[serde(default, alias = "baseUrl")]
    base_url: Option<String>,
    model: Option<String>,
    enabled: bool,
    #[serde(default, alias = "timeoutMs")]
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
pub(crate) struct DesktopProviderTestRequest {
    provider: String,
    #[serde(default, alias = "apiKey")]
    api_key: Option<String>,
    #[serde(default, alias = "baseUrl")]
    base_url: Option<String>,
    model: Option<String>,
    capability: String,
    #[serde(default, alias = "timeoutMs")]
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
pub(crate) struct DesktopPermissionRequest {
    #[serde(alias = "executionMode")]
    execution_mode: String,
    #[serde(default, alias = "acknowledgeRoslynRisk")]
    acknowledge_roslyn_risk: bool,
    #[serde(default, alias = "timeoutMs")]
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopAdvancedSettingsUpdateRequest {
    developer_options_enabled: bool,
    computer_use_enabled: bool,
    developer_challenge_id: Option<String>,
    #[serde(default)]
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopDeveloperOptionsChallengeRequest {
    challenge_id: String,
    #[serde(default)]
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopAgentMessageRequest {
    message: String,
    session_id: Option<String>,
    history: Option<Vec<serde_json::Value>>,
    agent_name: Option<String>,
    attachments: Option<Vec<serde_json::Value>>,
    project_path: Option<String>,
    provider: Option<String>,
    provider_label: Option<String>,
    model: Option<String>,
    context_limit: Option<u64>,
    client_turn_id: Option<String>,
    goal_delivery_id: Option<String>,
    #[serde(default)]
    computer_use_requested: bool,
    computer_use_grant_id: Option<String>,
    computer_use_visual_theme: Option<String>,
    computer_use_visual_accent: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopComputerUseTurnGrantRequest {
    session_id: Option<String>,
    client_turn_id: String,
    project_root: Option<String>,
    #[serde(default)]
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopAgentRunCancelRequest {
    session_id: Option<String>,
    turn_id: Option<String>,
    client_turn_id: Option<String>,
    reason: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopAgentRunQueuedRequest {
    session_id: Option<String>,
    client_turn_id: String,
    message: Option<String>,
    attachments: Option<Vec<serde_json::Value>>,
    provider: Option<String>,
    provider_label: Option<String>,
    model: Option<String>,
    project_path: Option<String>,
    project_root: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopApprovalScopeRequest {
    approval_id: String,
    expected_project_root: Option<String>,
    global_only: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopApprovalRevisionRequest {
    approval_id: String,
    reason: Option<String>,
    note: Option<String>,
    expected_project_root: Option<String>,
    global_only: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopCheckpointsRequest {
    project_root: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopCheckpointIdRequest {
    checkpoint_id: String,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopRecoveriesRequest {
    project_root: Option<String>,
    include_resolved: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopRecoveryIdRequest {
    recovery_id: String,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopResolveRecoveryRequest {
    recovery_id: String,
    confirm_resolved: bool,
    note: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopExternalAgentConnectorsRequest {
    project_path: Option<String>,
    config_path: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopExternalAgentGatewayRequest {
    enabled: Option<bool>,
    allow_write_requests: Option<bool>,
    revoke_token: Option<bool>,
    checkpoint_archive_max_size_mb: Option<i64>,
    delete_checkpoint_archive_ids: Option<Vec<String>>,
    checkpoint_archive_directory: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopExternalAgentConnectorActionRequest {
    client: String,
    project_path: Option<String>,
    config_path: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopBootstrapRequest {
    refresh_projects: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopTimeoutRequest {
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopWorkspaceDiffRequest {
    root: Option<String>,
    include_patch: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopUnityMcpRepairRequest {
    project_path: Option<String>,
    allow_unity_relaunch: Option<bool>,
    wait_seconds: Option<u64>,
    close_timeout_seconds: Option<u64>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopDiagnosticsUpdateRequest {
    log_level: Option<String>,
    debug_logging: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopSupportBundleRequest {
    include_full_paths: Option<bool>,
    log_limit: Option<u64>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopProjectPrefsRequest {
    custom_paths: Option<Vec<String>>,
    hidden_paths: Option<Vec<String>>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopProjectIndexScanRequest {
    project_path: String,
    max_files: Option<u64>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopAdjustmentCheckpointsRequest {
    kind: Option<String>,
    project_root: Option<String>,
    avatar_path: Option<String>,
    include_deleted: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopAdjustmentCheckpointBodyRequest {
    body: serde_json::Value,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopAdjustmentCheckpointIdRequest {
    checkpoint_id: String,
    body: Option<serde_json::Value>,
    hard_delete: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopJsonBodyRequest {
    body: serde_json::Value,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopIdJsonBodyRequest {
    id: String,
    body: serde_json::Value,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopAgentListRequest {
    limit: Option<u64>,
    session_id: Option<String>,
    project_root: Option<String>,
    client_turn_id: Option<String>,
    chat_id: Option<String>,
    scope: Option<String>,
    include_events: Option<bool>,
    global_only: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopChatListRequest {
    project_paths: Option<Vec<String>>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopOptimizationProofsRequest {
    limit: Option<u64>,
    timeout_ms: Option<u64>,
}

#[tauri::command]
pub async fn desktop_runtime_snapshot(
    request: DesktopRuntimeSnapshotRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        let mut query = Vec::new();
        if let Some(value) = request
            .session_id
            .as_deref()
            .filter(|value| !value.is_empty())
        {
            query.push(format!(
                "sessionId={}",
                percent_encode_query_component(value)
            ));
        }
        if let Some(value) = request
            .project_root
            .as_deref()
            .filter(|value| !value.is_empty())
        {
            query.push(format!(
                "projectRoot={}",
                percent_encode_query_component(value)
            ));
        }
        if request.include_patch.unwrap_or(false) {
            query.push("includePatch=true".to_string());
        }
        if request.global_only.unwrap_or(false) {
            query.push("globalOnly=true".to_string());
        }
        let suffix = if query.is_empty() {
            String::new()
        } else {
            format!("?{}", query.join("&"))
        };
        backend_json_request(
            "GET",
            format!("/api/app/runtime/snapshot{suffix}"),
            None,
            request.timeout_ms,
        )
    })
    .await
}

#[tauri::command]
pub fn update_api_config(
    request: DesktopProviderConfigRequest,
) -> Result<serde_json::Value, String> {
    let secret = request.api_key.clone().unwrap_or_default();
    sanitize_provider_result(
        backend_json_request(
            "POST",
            "/api/config".to_string(),
            Some(provider_config_body(
                request.provider,
                request.api_key,
                request.base_url,
                request.model,
            )),
            request.timeout_ms,
        ),
        &[secret.as_str()],
    )
}

#[tauri::command]
pub fn update_vision_config(
    request: DesktopVisionConfigRequest,
) -> Result<serde_json::Value, String> {
    let secret = request.api_key.clone().unwrap_or_default();
    let mut body = provider_config_body(
        request.provider,
        request.api_key,
        request.base_url,
        request.model,
    );
    if let serde_json::Value::Object(object) = &mut body {
        object.insert(
            "enabled".to_string(),
            serde_json::Value::Bool(request.enabled),
        );
    }
    sanitize_provider_result(
        backend_json_request(
            "POST",
            "/api/config/vision".to_string(),
            Some(body),
            request.timeout_ms,
        ),
        &[secret.as_str()],
    )
}

#[tauri::command]
pub fn fetch_provider_models(
    request: DesktopProviderConfigRequest,
) -> Result<serde_json::Value, String> {
    let secret = request.api_key.clone().unwrap_or_default();
    sanitize_provider_result(
        backend_json_request(
            "POST",
            "/api/models".to_string(),
            Some(provider_config_body(
                request.provider,
                request.api_key,
                request.base_url,
                request.model,
            )),
            request.timeout_ms,
        ),
        &[secret.as_str()],
    )
}

#[tauri::command]
pub fn test_provider_capability(
    request: DesktopProviderTestRequest,
) -> Result<serde_json::Value, String> {
    let secret = request.api_key.clone().unwrap_or_default();
    let mut body = provider_config_body(
        request.provider,
        request.api_key,
        request.base_url,
        request.model,
    );
    if let serde_json::Value::Object(object) = &mut body {
        object.insert(
            "capability".to_string(),
            serde_json::Value::String(request.capability),
        );
    }
    sanitize_provider_result(
        backend_json_request(
            "POST",
            "/api/app/provider/test".to_string(),
            Some(body),
            request.timeout_ms,
        ),
        &[secret.as_str()],
    )
}

#[tauri::command]
pub fn update_permission_mode(
    request: DesktopPermissionRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/permission".to_string(),
        Some(serde_json::json!({
            "execution_mode": request.execution_mode,
            "acknowledge_roslyn_risk": request.acknowledge_roslyn_risk,
        })),
        request.timeout_ms,
    )
}

#[tauri::command]
pub async fn fetch_advanced_settings(
    request: DesktopTimeoutRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            "/api/app/advanced-settings".to_string(),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

pub(crate) fn advanced_settings_update_body(
    developer_options_enabled: bool,
    computer_use_enabled: bool,
    developer_challenge_id: Option<String>,
) -> serde_json::Value {
    let mut body = serde_json::Map::from_iter([
        (
            "developerOptionsEnabled".to_string(),
            serde_json::Value::Bool(developer_options_enabled),
        ),
        (
            "computerUseEnabled".to_string(),
            serde_json::Value::Bool(computer_use_enabled),
        ),
    ]);
    if let Some(challenge_id) = developer_challenge_id {
        body.insert(
            "developerChallengeId".to_string(),
            serde_json::Value::String(challenge_id),
        );
    }
    serde_json::Value::Object(body)
}

#[tauri::command]
pub async fn update_advanced_settings(
    request: DesktopAdvancedSettingsUpdateRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            "/api/app/advanced-settings".to_string(),
            Some(advanced_settings_update_body(
                request.developer_options_enabled,
                request.computer_use_enabled,
                request.developer_challenge_id,
            )),
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn begin_developer_options_challenge(
    request: DesktopTimeoutRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            "/api/app/advanced-settings/developer-challenge".to_string(),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

pub(crate) fn developer_options_challenge_path(challenge_id: &str) -> Result<String, String> {
    if challenge_id.is_empty() || challenge_id.len() > 512 {
        return Err("Developer Options challenge is invalid.".to_string());
    }
    Ok(format!(
        "/api/app/advanced-settings/developer-challenge/{}",
        percent_encode_query_component(challenge_id)
    ))
}

#[tauri::command]
pub async fn cancel_developer_options_challenge(
    request: DesktopDeveloperOptionsChallengeRequest,
) -> Result<serde_json::Value, String> {
    let path = developer_options_challenge_path(&request.challenge_id)?;
    blocking_backend_json_request(move || {
        backend_json_request("DELETE", path, None, request.timeout_ms)
            .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn send_agent_message(
    request: DesktopAgentMessageRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            "/api/app/agent/message".to_string(),
            Some(serde_json::json!({
                "agent_name": request.agent_name.unwrap_or_else(|| "desktop-agent".to_string()),
                "session_id": request.session_id,
                "clientTurnId": request.client_turn_id,
                "goalDeliveryId": request.goal_delivery_id,
                "message": request.message,
                "history": request.history.unwrap_or_default(),
                "attachments": request.attachments.unwrap_or_default(),
                "projectPath": request.project_path,
                "provider": request.provider,
                "providerLabel": request.provider_label,
                "model": request.model,
                "contextLimit": request.context_limit,
                "computerUseRequested": request.computer_use_requested,
                "computerUseGrantId": request.computer_use_grant_id,
                "computerUseVisualTheme": request.computer_use_visual_theme,
                "computerUseVisualAccent": request.computer_use_visual_accent,
            })),
            request
                .timeout_ms
                .or(Some(DESKTOP_AGENT_MESSAGE_TIMEOUT_MS)),
        )
    })
    .await
}

#[tauri::command]
pub async fn issue_computer_use_turn_grant(
    request: DesktopComputerUseTurnGrantRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            "/api/app/agent/computer-use/grants".to_string(),
            Some(serde_json::json!({
                "sessionId": request.session_id,
                "clientTurnId": request.client_turn_id,
                "projectRoot": request.project_root,
            })),
            request.timeout_ms.or(Some(30_000)),
        )
    })
    .await
}

#[tauri::command]
pub fn request_agent_run_cancel(
    request: DesktopAgentRunCancelRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/agent/runs/cancel".to_string(),
        Some(serde_json::json!({
            "sessionId": request.session_id,
            "turnId": request.turn_id,
            "clientTurnId": request.client_turn_id,
            "reason": request.reason,
        })),
        request.timeout_ms.or(Some(30_000)),
    )
}

#[tauri::command]
pub fn record_agent_run_queued(
    request: DesktopAgentRunQueuedRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/agent/runs/queue".to_string(),
        Some(serde_json::json!({
            "sessionId": request.session_id,
            "clientTurnId": request.client_turn_id,
            "message": request.message,
            "attachments": request.attachments.unwrap_or_default(),
            "provider": request.provider,
            "providerLabel": request.provider_label,
            "model": request.model,
            "projectPath": request.project_path,
            "projectRoot": request.project_root,
        })),
        request.timeout_ms.or(Some(30_000)),
    )
}

pub(crate) fn approval_scope_body(request: &DesktopApprovalScopeRequest) -> serde_json::Value {
    serde_json::json!({
        "expectedProjectRoot": request.expected_project_root,
        "globalOnly": request.global_only,
    })
}

#[tauri::command]
pub fn approve_agent_approval(
    request: DesktopApprovalScopeRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/agent/approvals/{}/approve",
            percent_encode_query_component(&request.approval_id)
        ),
        Some(approval_scope_body(&request)),
        request.timeout_ms.or(Some(180_000)),
    )
}

#[tauri::command]
pub fn reject_agent_approval(
    request: DesktopApprovalScopeRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/agent/approvals/{}/reject",
            percent_encode_query_component(&request.approval_id)
        ),
        Some(approval_scope_body(&request)),
        request.timeout_ms.or(Some(60_000)),
    )
}

#[tauri::command]
pub fn request_approval_revision(
    request: DesktopApprovalRevisionRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/agent/approvals/{}/revision",
            percent_encode_query_component(&request.approval_id)
        ),
        Some(serde_json::json!({
            "reason": request.reason,
            "note": request.note,
            "expectedProjectRoot": request.expected_project_root,
            "globalOnly": request.global_only,
        })),
        request.timeout_ms.or(Some(60_000)),
    )
}

#[tauri::command]
pub fn fetch_checkpoints(request: DesktopCheckpointsRequest) -> Result<serde_json::Value, String> {
    let mut query = Vec::new();
    if let Some(value) = request
        .project_root
        .as_deref()
        .filter(|value| !value.is_empty())
    {
        query.push(format!(
            "projectRoot={}",
            percent_encode_query_component(value)
        ));
    }
    let suffix = if query.is_empty() {
        String::new()
    } else {
        format!("?{}", query.join("&"))
    };
    backend_json_request(
        "GET",
        format!("/api/app/checkpoints{suffix}"),
        None,
        request.timeout_ms,
    )
}

#[tauri::command]
pub fn preview_restore_checkpoint(
    request: DesktopCheckpointIdRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/checkpoints/{}/preview",
            percent_encode_query_component(&request.checkpoint_id)
        ),
        None,
        request.timeout_ms,
    )
}

#[tauri::command]
pub fn request_restore_checkpoint(
    request: DesktopCheckpointIdRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/checkpoints/{}/restore",
            percent_encode_query_component(&request.checkpoint_id)
        ),
        None,
        request.timeout_ms.or(Some(180_000)),
    )
}

#[tauri::command]
pub fn fetch_interrupted_apply_recoveries(
    request: DesktopRecoveriesRequest,
) -> Result<serde_json::Value, String> {
    let mut query = Vec::new();
    if let Some(value) = request
        .project_root
        .as_deref()
        .filter(|value| !value.is_empty())
    {
        query.push(format!(
            "projectRoot={}",
            percent_encode_query_component(value)
        ));
    }
    if request.include_resolved.unwrap_or(false) {
        query.push("includeResolved=true".to_string());
    }
    let suffix = if query.is_empty() {
        String::new()
    } else {
        format!("?{}", query.join("&"))
    };
    backend_json_request(
        "GET",
        format!("/api/app/recoveries{suffix}"),
        None,
        request.timeout_ms,
    )
}

#[tauri::command]
pub fn preview_interrupted_apply_recovery(
    request: DesktopRecoveryIdRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/recoveries/{}/preview",
            percent_encode_query_component(&request.recovery_id)
        ),
        None,
        request.timeout_ms,
    )
}

#[tauri::command]
pub fn request_restore_interrupted_apply_recovery(
    request: DesktopRecoveryIdRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/recoveries/{}/restore",
            percent_encode_query_component(&request.recovery_id)
        ),
        None,
        request.timeout_ms.or(Some(180_000)),
    )
}

#[tauri::command]
pub fn resolve_interrupted_apply_recovery(
    request: DesktopResolveRecoveryRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/recoveries/{}/resolve",
            percent_encode_query_component(&request.recovery_id)
        ),
        Some(serde_json::json!({
            "confirmResolved": request.confirm_resolved,
            "note": request.note,
        })),
        request.timeout_ms,
    )
}

#[tauri::command]
pub fn export_interrupted_apply_incident_bundle(
    request: DesktopRecoveryIdRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/recoveries/{}/incident-bundle",
            percent_encode_query_component(&request.recovery_id)
        ),
        None,
        request.timeout_ms,
    )
}

#[tauri::command]
pub async fn fetch_app_bootstrap(
    request: DesktopBootstrapRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        let suffix = if request.refresh_projects.unwrap_or(false) {
            "?refreshProjects=true"
        } else {
            ""
        };
        backend_json_request(
            "GET",
            format!("/api/app/bootstrap{suffix}"),
            None,
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn fetch_app_health(request: DesktopTimeoutRequest) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request("GET", "/api/health".to_string(), None, request.timeout_ms)
            .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn refresh_projects(request: DesktopTimeoutRequest) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            "/api/projects/refresh".to_string(),
            None,
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn refresh_unity_readiness(
    request: DesktopTimeoutRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            "/api/app/unity/readiness/refresh".to_string(),
            None,
            request.timeout_ms.or(Some(20_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn fetch_workspace_diff(
    request: DesktopWorkspaceDiffRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        let mut query = Vec::new();
        if let Some(value) = request.root.as_deref().filter(|value| !value.is_empty()) {
            query.push(format!("root={}", percent_encode_query_component(value)));
        }
        if request.include_patch.unwrap_or(false) {
            query.push("includePatch=true".to_string());
        }
        let suffix = if query.is_empty() {
            String::new()
        } else {
            format!("?{}", query.join("&"))
        };
        backend_json_request(
            "GET",
            format!("/api/app/workspace/diff{suffix}"),
            None,
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub fn fetch_doctor(request: DesktopTimeoutRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "GET",
        "/api/app/doctor".to_string(),
        None,
        request.timeout_ms.or(Some(30_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn repair_unity_mcp_bridge(
    request: DesktopUnityMcpRepairRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/doctor/unity-mcp/repair".to_string(),
        Some(serde_json::json!({
            "projectPath": request.project_path,
            "allowUnityRelaunch": request.allow_unity_relaunch,
            "waitSeconds": request.wait_seconds,
            "closeTimeoutSeconds": request.close_timeout_seconds,
        })),
        request.timeout_ms.or(Some(120_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub async fn fetch_diagnostics(
    request: DesktopTimeoutRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            "/api/app/diagnostics".to_string(),
            None,
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

pub(crate) fn diagnostics_update_body(
    log_level: Option<String>,
    debug_logging: Option<bool>,
) -> serde_json::Value {
    let mut body = serde_json::Map::new();
    if let Some(log_level) = log_level {
        body.insert("logLevel".to_string(), serde_json::Value::String(log_level));
    }
    if let Some(debug_logging) = debug_logging {
        body.insert(
            "debugLogging".to_string(),
            serde_json::Value::Bool(debug_logging),
        );
    }
    serde_json::Value::Object(body)
}

#[tauri::command]
pub async fn update_diagnostics(
    request: DesktopDiagnosticsUpdateRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            "/api/app/diagnostics".to_string(),
            Some(diagnostics_update_body(
                request.log_level,
                request.debug_logging,
            )),
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn export_support_bundle(
    request: DesktopSupportBundleRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            "/api/app/support-bundle".to_string(),
            Some(serde_json::json!({
                "includeFullPaths": request.include_full_paths,
                "logLimit": request.log_limit,
            })),
            request.timeout_ms.or(Some(120_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn fetch_project_prefs(
    request: DesktopTimeoutRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            "/api/app/projects/prefs".to_string(),
            None,
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub fn save_project_prefs(
    request: DesktopProjectPrefsRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/projects/prefs".to_string(),
        Some(serde_json::json!({
            "customPaths": request.custom_paths.unwrap_or_default(),
            "hiddenPaths": request.hidden_paths.unwrap_or_default(),
        })),
        request.timeout_ms.or(Some(30_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn scan_project_index(
    request: DesktopProjectIndexScanRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/project-index/scan".to_string(),
        Some(serde_json::json!({
            "projectPath": request.project_path,
            "maxFiles": request.max_files,
        })),
        request.timeout_ms.or(Some(120_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn fetch_adjustment_checkpoints(
    request: DesktopAdjustmentCheckpointsRequest,
) -> Result<serde_json::Value, String> {
    let mut query = Vec::new();
    if let Some(value) = request.kind.as_deref().filter(|value| !value.is_empty()) {
        query.push(format!("kind={}", percent_encode_query_component(value)));
    }
    if let Some(value) = request
        .project_root
        .as_deref()
        .filter(|value| !value.is_empty())
    {
        query.push(format!(
            "projectRoot={}",
            percent_encode_query_component(value)
        ));
    }
    if let Some(value) = request
        .avatar_path
        .as_deref()
        .filter(|value| !value.is_empty())
    {
        query.push(format!(
            "avatarPath={}",
            percent_encode_query_component(value)
        ));
    }
    if request.include_deleted.unwrap_or(false) {
        query.push("includeDeleted=true".to_string());
    }
    let suffix = if query.is_empty() {
        String::new()
    } else {
        format!("?{}", query.join("&"))
    };
    backend_json_request(
        "GET",
        format!("/api/app/adjustment-checkpoints{suffix}"),
        None,
        request.timeout_ms.or(Some(30_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn create_adjustment_checkpoint(
    request: DesktopAdjustmentCheckpointBodyRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/adjustment-checkpoints".to_string(),
        Some(request.body),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn update_adjustment_checkpoint(
    request: DesktopAdjustmentCheckpointIdRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "PUT",
        format!(
            "/api/app/adjustment-checkpoints/{}",
            percent_encode_query_component(&request.checkpoint_id)
        ),
        Some(request.body.unwrap_or_else(|| serde_json::json!({}))),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn delete_adjustment_checkpoint(
    request: DesktopAdjustmentCheckpointIdRequest,
) -> Result<serde_json::Value, String> {
    let suffix = if request.hard_delete.unwrap_or(false) {
        "?hardDelete=true"
    } else {
        ""
    };
    backend_json_request(
        "DELETE",
        format!(
            "/api/app/adjustment-checkpoints/{}{suffix}",
            percent_encode_query_component(&request.checkpoint_id)
        ),
        None,
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn overwrite_adjustment_checkpoint(
    request: DesktopAdjustmentCheckpointIdRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/adjustment-checkpoints/{}/overwrite",
            percent_encode_query_component(&request.checkpoint_id)
        ),
        Some(request.body.unwrap_or_else(|| serde_json::json!({}))),
        request.timeout_ms.or(Some(120_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn select_adjustment_checkpoint(
    request: DesktopAdjustmentCheckpointIdRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/adjustment-checkpoints/{}/select",
            percent_encode_query_component(&request.checkpoint_id)
        ),
        Some(request.body.unwrap_or_else(|| serde_json::json!({}))),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn apply_adjustment_checkpoint(
    request: DesktopAdjustmentCheckpointIdRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/adjustment-checkpoints/{}/apply",
            percent_encode_query_component(&request.checkpoint_id)
        ),
        None,
        request.timeout_ms.or(Some(120_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn preview_adjustment_checkpoint(
    request: DesktopAdjustmentCheckpointIdRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/adjustment-checkpoints/{}/preview",
            percent_encode_query_component(&request.checkpoint_id)
        ),
        None,
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

pub(crate) fn post_json_body_command(
    path: &str,
    request: DesktopJsonBodyRequest,
    default_timeout_ms: u64,
) -> Result<serde_json::Value, String> {
    json_body_command("POST", path, request, default_timeout_ms)
}

pub(crate) fn json_body_command(
    method: &str,
    path: &str,
    request: DesktopJsonBodyRequest,
    default_timeout_ms: u64,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        method,
        path.to_string(),
        Some(request.body),
        request.timeout_ms.or(Some(default_timeout_ms)),
    )
    .map(sanitize_webview_response)
}

pub(crate) fn append_query_param(query: &mut Vec<String>, key: &str, value: &Option<String>) {
    if let Some(value) = value.as_deref().filter(|value| !value.is_empty()) {
        query.push(format!("{key}={}", percent_encode_query_component(value)));
    }
}

pub(crate) fn agent_list_query(request: &DesktopAgentListRequest) -> String {
    let mut query = Vec::new();
    if let Some(value) = request.limit {
        query.push(format!("limit={value}"));
    }
    append_query_param(&mut query, "sessionId", &request.session_id);
    append_query_param(&mut query, "projectRoot", &request.project_root);
    append_query_param(&mut query, "clientTurnId", &request.client_turn_id);
    append_query_param(&mut query, "chatId", &request.chat_id);
    append_query_param(&mut query, "scope", &request.scope);
    if request.include_events.unwrap_or(false) {
        query.push("includeEvents=true".to_string());
    }
    if request.global_only.unwrap_or(false) {
        query.push("globalOnly=1".to_string());
    }
    if query.is_empty() {
        String::new()
    } else {
        format!("?{}", query.join("&"))
    }
}

#[tauri::command]
pub fn fetch_avatars(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/avatars", request, 60_000)
}

#[tauri::command]
pub fn fetch_optimization_plan(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/optimization/plan", request, 120_000)
}

#[tauri::command]
pub fn request_optimization_apply(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/optimization/apply-request", request, 120_000)
}

#[tauri::command]
pub fn plan_outfit_import(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/outfit-imports/plan", request, 120_000)
}

#[tauri::command]
pub fn request_outfit_import(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/outfit-imports/request", request, 120_000)
}

#[tauri::command]
pub fn request_package_install(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/package-install/request", request, 120_000)
}

#[tauri::command]
pub fn plan_avatar_encryption(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/avatar-encryption/plan", request, 120_000)
}

#[tauri::command]
pub fn request_avatar_encryption_apply(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/avatar-encryption/apply-request", request, 120_000)
}

#[tauri::command]
pub fn fetch_skill_packages() -> Result<serde_json::Value, String> {
    backend_json_request("GET", "/api/app/skill-packages".to_string(), None, None)
        .map(sanitize_webview_response)
}

#[tauri::command]
pub fn preflight_skill_package(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/preflight", request, 120_000)
}

#[tauri::command]
pub fn import_skill_package(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/import", request, 120_000)
}

#[tauri::command]
pub fn set_skill_package_safe_mode(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/safe-mode", request, 60_000)
}

#[tauri::command]
pub fn trust_skill_package_signer(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/trust-signer", request, 60_000)
}

#[tauri::command]
pub fn revoke_skill_package_signer(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/revoke-signer", request, 60_000)
}

#[tauri::command]
pub fn block_skill_package(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/block-package", request, 60_000)
}

#[tauri::command]
pub fn export_skill_package(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/export", request, 120_000)
}

#[tauri::command]
pub fn set_skill_package_enabled(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "PUT",
        format!(
            "/api/app/skill-packages/{}",
            percent_encode_query_component(&request.id)
        ),
        Some(request.body),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn uninstall_skill_package(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "DELETE",
        format!(
            "/api/app/skill-packages/{}",
            percent_encode_query_component(&request.id)
        ),
        Some(request.body),
        request.timeout_ms.or(Some(120_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn preview_path_to_skill(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/path-to-skill/preview", request, 120_000)
}

#[tauri::command]
pub fn write_path_to_skill(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/path-to-skill/write", request, 120_000)
}

#[tauri::command]
pub fn fetch_skills() -> Result<serde_json::Value, String> {
    backend_json_request("GET", "/api/app/skills".to_string(), None, None)
        .map(sanitize_webview_response)
}

#[tauri::command]
pub fn check_skills() -> Result<serde_json::Value, String> {
    backend_json_request("GET", "/api/app/skills/check".to_string(), None, None)
        .map(sanitize_webview_response)
}

#[tauri::command]
pub fn create_skill(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skills", request, 60_000)
}

#[tauri::command]
pub fn update_skill(request: DesktopIdJsonBodyRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "PUT",
        format!(
            "/api/app/skills/{}",
            percent_encode_query_component(&request.id)
        ),
        Some(request.body),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn delete_skill(request: DesktopIdJsonBodyRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "DELETE",
        format!(
            "/api/app/skills/{}",
            percent_encode_query_component(&request.id)
        ),
        Some(request.body),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub async fn fetch_sub_agents(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!("/api/app/sub-agents{}", agent_list_query(&request)),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn create_sub_agent(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        post_json_body_command("/api/app/sub-agents", request, 60_000)
    })
    .await
}

#[tauri::command]
pub async fn fetch_sub_agent(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!(
                "/api/app/sub-agents/{}",
                percent_encode_query_component(&request.id)
            ),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn cancel_sub_agent(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            format!(
                "/api/app/sub-agents/{}/cancel",
                percent_encode_query_component(&request.id)
            ),
            None,
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn retry_sub_agent(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            format!(
                "/api/app/sub-agents/{}/retry",
                percent_encode_query_component(&request.id)
            ),
            None,
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn merge_sub_agent(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            format!(
                "/api/app/sub-agents/{}/merge",
                percent_encode_query_component(&request.id)
            ),
            Some(request.body),
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn acknowledge_sub_agent_handoff(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            format!(
                "/api/app/sub-agents/{}/handoff-ack",
                percent_encode_query_component(&request.id)
            ),
            Some(request.body),
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn fetch_agent_runs(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!("/api/app/agent/runs{}", agent_list_query(&request)),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn fetch_agent_approvals(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!("/api/app/agent/approvals{}", agent_list_query(&request)),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn fetch_agent_desktop_actions(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!(
                "/api/app/agent/desktop-actions{}",
                agent_list_query(&request)
            ),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn request_agent_desktop_action(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        post_json_body_command("/api/app/agent/desktop-actions", request, 60_000)
    })
    .await
}

#[tauri::command]
pub async fn cancel_agent_desktop_action(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            format!(
                "/api/app/agent/desktop-actions/{}/cancel",
                percent_encode_query_component(&request.id)
            ),
            Some(request.body),
            request.timeout_ms.or(Some(30_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn fetch_agent_goals(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!("/api/app/agent/goals{}", agent_list_query(&request)),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn create_agent_goal(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        post_json_body_command("/api/app/agent/goals", request, 60_000)
    })
    .await
}

#[tauri::command]
pub async fn update_agent_goal(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            format!(
                "/api/app/agent/goals/{}",
                percent_encode_query_component(&request.id)
            ),
            Some(request.body),
            request.timeout_ms.or(Some(60_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn fetch_due_agent_goals(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!("/api/app/agent/goals/due{}", agent_list_query(&request)),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn wake_agent_goal(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            format!(
                "/api/app/agent/goals/{}/wake",
                percent_encode_query_component(&request.id)
            ),
            Some(request.body),
            request.timeout_ms.or(Some(60_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn bind_agent_goal_owner(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            format!(
                "/api/app/agent/goals/{}/bind-owner",
                percent_encode_query_component(&request.id)
            ),
            Some(request.body),
            request.timeout_ms.or(Some(60_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn fetch_recoverable_agent_goal_deliveries(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!(
                "/api/app/agent/goals/deliveries/recoverable{}",
                agent_list_query(&request)
            ),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn materialize_agent_goal_delivery(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            format!(
                "/api/app/agent/goals/deliveries/{}/materialized",
                percent_encode_query_component(&request.id)
            ),
            Some(request.body),
            request.timeout_ms.or(Some(60_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn fetch_agent_progress(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!("/api/app/agent/progress{}", agent_list_query(&request)),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub fn replace_agent_progress(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/agent/progress/replace", request, 60_000)
}

#[tauri::command]
pub fn create_agent_progress(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/agent/progress", request, 60_000)
}

#[tauri::command]
pub fn update_agent_progress(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/agent/progress/{}",
            percent_encode_query_component(&request.id)
        ),
        Some(request.body),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn delete_agent_progress(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "DELETE",
        format!(
            "/api/app/agent/progress/{}",
            percent_encode_query_component(&request.id)
        ),
        Some(request.body),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub async fn fetch_agent_questions(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!("/api/app/agent/questions{}", agent_list_query(&request)),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub fn create_agent_question(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/agent/questions", request, 60_000)
}

#[tauri::command]
pub fn answer_agent_question(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        format!(
            "/api/app/agent/questions/{}/answer",
            percent_encode_query_component(&request.id)
        ),
        Some(request.body),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub async fn fetch_agent_memory(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "GET",
            format!("/api/app/agent/memory{}", agent_list_query(&request)),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn create_agent_memory(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        post_json_body_command("/api/app/agent/memory", request, 60_000)
    })
    .await
}

#[tauri::command]
pub async fn delete_agent_memory(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "DELETE",
            format!(
                "/api/app/agent/memory/{}",
                percent_encode_query_component(&request.id)
            ),
            Some(request.body),
            request.timeout_ms.or(Some(60_000)),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn clear_agent_memory(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        post_json_body_command("/api/app/agent/memory/clear", request, 60_000)
    })
    .await
}

#[tauri::command]
pub async fn compact_agent_history(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        post_json_body_command("/api/app/agent/compact", request, 120_000)
    })
    .await
}

#[tauri::command]
pub async fn fetch_agent_notes() -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        backend_json_request("GET", "/api/app/agent-notes".to_string(), None, None)
            .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub fn save_agent_notes(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/agent-notes", request, 60_000)
}

#[tauri::command]
pub async fn fetch_chats(request: DesktopChatListRequest) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        let mut query = Vec::new();
        for project_path in request.project_paths.unwrap_or_default() {
            if !project_path.trim().is_empty() {
                query.push(format!(
                    "projectPath={}",
                    percent_encode_query_component(project_path.trim())
                ));
            }
        }
        let suffix = if query.is_empty() {
            String::new()
        } else {
            format!("?{}", query.join("&"))
        };
        backend_json_request(
            "GET",
            format!("/api/app/chats{suffix}"),
            None,
            request.timeout_ms,
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub fn save_chats(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/chats", request, 60_000)
}

/// Hard shell-side ceiling for chat attachment uploads. The backend vault
/// enforces the real per-kind caps (512MB archives / 64MB images); this only
/// bounds what the IPC bridge is willing to carry.
const CHAT_ATTACHMENT_UPLOAD_CHUNK_MAX_BYTES: usize = 8 * 1024 * 1024;

#[tauri::command]
pub async fn begin_chat_attachment_upload(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        post_json_body_command("/api/app/chat-attachments/uploads", request, 30_000)
    })
    .await
}

#[tauri::command]
pub async fn append_chat_attachment_upload(
    request: tauri::ipc::Request<'_>,
) -> Result<serde_json::Value, String> {
    let tauri::ipc::InvokeBody::Raw(bytes) = request.body() else {
        return Err("Chat attachment upload chunk requires a raw binary body.".to_string());
    };
    if bytes.is_empty() {
        return Err("Chat attachment upload chunk is empty.".to_string());
    }
    if bytes.len() > CHAT_ATTACHMENT_UPLOAD_CHUNK_MAX_BYTES {
        return Err("Chat attachment upload chunk exceeds the transport size limit.".to_string());
    }
    let header_text = |name: &str| -> Result<String, String> {
        request
            .headers()
            .get(name)
            .ok_or_else(|| format!("Chat attachment header {name} is required."))?
            .to_str()
            .map(str::to_string)
            .map_err(|_| format!("Chat attachment header {name} is not ASCII."))
    };
    let upload_id = header_text("x-vrcforge-upload-id")?;
    if !upload_id
        .chars()
        .all(|character| character.is_ascii_alphanumeric() || character == '_' || character == '-')
    {
        return Err("Chat attachment upload id is invalid.".to_string());
    }
    let offset = header_text("x-vrcforge-upload-offset")?
        .parse::<u64>()
        .map_err(|_| "Chat attachment upload offset is invalid.".to_string())?;
    let bytes = bytes.to_vec();
    let path = format!("/api/app/chat-attachments/uploads/{upload_id}/chunks?offset={offset}");
    blocking_backend_json_request(move || {
        backend_bytes_request(
            "POST",
            path,
            &bytes,
            "application/octet-stream",
            Some(120_000),
        )
        .map(sanitize_webview_response)
    })
    .await
}

#[tauri::command]
pub async fn finish_chat_attachment_upload(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        post_json_body_command("/api/app/chat-attachments/uploads/finish", request, 300_000)
    })
    .await
}

#[tauri::command]
pub async fn abort_chat_attachment_upload(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        post_json_body_command("/api/app/chat-attachments/uploads/abort", request, 30_000)
    })
    .await
}

#[tauri::command]
pub async fn request_chat_attachment_import(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    blocking_backend_json_request(move || {
        post_json_body_command("/api/app/chat-attachments/import", request, 120_000)
    })
    .await
}

#[tauri::command]
pub fn fetch_optimization_proofs(
    request: DesktopOptimizationProofsRequest,
) -> Result<serde_json::Value, String> {
    let limit = request.limit.unwrap_or(8);
    backend_json_request(
        "GET",
        format!("/api/app/optimization/proofs?limit={limit}"),
        None,
        request.timeout_ms,
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn fetch_optimization_proof(
    request: DesktopIdJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "GET",
        format!(
            "/api/app/optimization/proofs/{}",
            percent_encode_query_component(&request.id)
        ),
        None,
        request.timeout_ms,
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn fetch_external_agent_connectors(
    request: DesktopExternalAgentConnectorsRequest,
) -> Result<serde_json::Value, String> {
    let mut query = Vec::new();
    append_query_param(&mut query, "projectPath", &request.project_path);
    append_query_param(&mut query, "configPath", &request.config_path);
    let suffix = if query.is_empty() {
        String::new()
    } else {
        format!("?{}", query.join("&"))
    };
    backend_json_request(
        "GET",
        format!("/api/app/external-agent/connectors{suffix}"),
        None,
        request.timeout_ms.or(Some(30_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn update_external_agent_gateway(
    request: DesktopExternalAgentGatewayRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/external-agent/gateway".to_string(),
        Some(serde_json::json!({
            "enabled": request.enabled,
            "allowWriteRequests": request.allow_write_requests,
            "revokeToken": request.revoke_token.unwrap_or(false),
            "checkpointArchiveMaxSizeMb": request.checkpoint_archive_max_size_mb,
            "deleteCheckpointArchiveIds": request.delete_checkpoint_archive_ids,
            "checkpointArchiveDirectory": request.checkpoint_archive_directory,
        })),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn install_external_agent_connector(
    request: DesktopExternalAgentConnectorActionRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/external-agent/connectors/install".to_string(),
        Some(serde_json::json!({
            "client": request.client,
            "projectPath": request.project_path,
            "configPath": request.config_path,
        })),
        request.timeout_ms.or(Some(120_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn uninstall_external_agent_connector(
    request: DesktopExternalAgentConnectorActionRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/external-agent/connectors/uninstall".to_string(),
        Some(serde_json::json!({
            "client": request.client,
            "projectPath": request.project_path,
            "configPath": request.config_path,
        })),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
pub fn ensure_agent_notes_file() -> String {
    let Ok(user_data) = user_data_dir() else {
        eprintln!("Optional AGENTS.md path could not be resolved");
        return String::new();
    };
    let path = user_data.join("AGENTS.md");
    if let Err(error) = try_ensure_agent_notes_file(&user_data) {
        eprintln!("Optional AGENTS.md is unavailable: {error}");
    }
    path.display().to_string()
}

#[tauri::command]
pub fn open_folder(path: String) -> Result<(), String> {
    let folder = validate_project_folder_to_open(&path)?;
    open_folder_in_shell(folder)
}

#[tauri::command]
pub fn open_local_folder(path: String) -> Result<(), String> {
    let folder = validate_local_folder_to_open(&path)?;
    open_folder_in_shell(folder)
}

#[tauri::command]
pub fn open_logs_folder() -> Result<(), String> {
    let user_data =
        user_data_dir().map_err(|_| "Unable to resolve the logs folder.".to_string())?;
    let folder = resolve_logs_folder(&user_data)?;
    open_folder_in_shell(folder).map_err(|_| "Unable to open the logs folder.".to_string())
}

#[tauri::command]
pub fn select_folder(initial_path: Option<String>) -> Result<Option<String>, String> {
    select_folder_dialog(initial_path.as_deref())
}

pub(crate) fn open_folder_in_shell(folder: PathBuf) -> Result<(), String> {
    #[cfg(windows)]
    {
        let mut command = Command::new("explorer.exe");
        command.arg(folder);
        command.creation_flags(CREATE_NO_WINDOW);
        command
            .spawn()
            .map_err(|error| format!("unable to open folder: {error}"))?;
        return Ok(());
    }

    #[cfg(not(windows))]
    {
        let opener = if cfg!(target_os = "macos") {
            "open"
        } else {
            "xdg-open"
        };
        Command::new(opener)
            .arg(folder)
            .spawn()
            .map_err(|error| format!("unable to open folder: {error}"))?;
        Ok(())
    }
}

pub(crate) fn resolve_logs_folder(user_data: &Path) -> Result<PathBuf, String> {
    const ERROR: &str = "Unable to prepare the logs folder.";

    let logs = user_data.join("logs");
    fs::create_dir_all(&logs).map_err(|_| ERROR.to_string())?;
    let user_data = fs::canonicalize(user_data).map_err(|_| ERROR.to_string())?;
    let logs = fs::canonicalize(logs).map_err(|_| ERROR.to_string())?;
    if !logs.is_dir() || !logs.starts_with(&user_data) {
        return Err(ERROR.to_string());
    }
    Ok(logs)
}

pub(crate) fn validate_local_folder_to_open(path: &str) -> Result<PathBuf, String> {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return Err("Folder path is empty.".to_string());
    }
    let folder = PathBuf::from(trimmed);
    if !folder.is_absolute() {
        return Err(format!("Folder must be an absolute path: {trimmed}"));
    }
    let folder = fs::canonicalize(&folder)
        .map_err(|error| format!("Folder does not exist: {} ({error})", folder.display()))?;
    if !folder.is_dir() {
        return Err(format!("Folder does not exist: {}", folder.display()));
    }
    Ok(folder)
}

pub(crate) fn select_folder_dialog(initial_path: Option<&str>) -> Result<Option<String>, String> {
    #[cfg(windows)]
    {
        // Native folder picker via `rfd` (IFileDialog on Windows): no child
        // process, no script injection surface, and non-ASCII paths survive
        // without encoding tricks.
        let mut dialog = rfd::FileDialog::new().set_title("Select checkpoint archive directory");
        if let Some(path) = initial_path {
            let trimmed = path.trim();
            if !trimmed.is_empty() {
                let candidate = PathBuf::from(trimmed);
                if candidate.is_dir() {
                    dialog = dialog.set_directory(candidate);
                }
            }
        }
        Ok(dialog
            .pick_folder()
            .map(|folder| folder.display().to_string()))
    }

    #[cfg(not(windows))]
    {
        let _ = initial_path;
        Err("Folder picker is only available on Windows desktop builds.".to_string())
    }
}

pub(crate) fn validate_project_folder_to_open(path: &str) -> Result<PathBuf, String> {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return Err("Project folder path is empty.".to_string());
    }
    let folder = PathBuf::from(trimmed);
    if !folder.is_absolute() {
        return Err(format!(
            "Project folder must be an absolute path: {trimmed}"
        ));
    }
    let folder = fs::canonicalize(&folder)
        .map_err(|error| format!("Folder does not exist: {} ({error})", folder.display()))?;
    if !folder.is_dir() {
        return Err(format!("Folder does not exist: {}", folder.display()));
    }
    if !(folder.join("Assets").is_dir()
        && folder.join("Packages").is_dir()
        && folder.join("ProjectSettings").is_dir())
    {
        return Err(format!("Not a Unity project root: {}", folder.display()));
    }
    Ok(folder)
}

pub(crate) fn try_ensure_agent_notes_file(user_data: &Path) -> Result<PathBuf, String> {
    fs::create_dir_all(user_data).map_err(|error| {
        format!(
            "unable to create AGENTS.md parent directory {}: {error}",
            user_data.display()
        )
    })?;
    let path = user_data.join("AGENTS.md");
    if path.exists() {
        if path.is_file() {
            return Ok(path);
        }
        return Err(format!("{} is not a file", path.display()));
    }
    fs::write(&path, "")
        .map_err(|error| format!("unable to create {}: {error}", path.display()))?;
    Ok(path)
}

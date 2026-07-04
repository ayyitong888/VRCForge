#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

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
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, State,
};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8757;
const BACKEND_ENDPOINT: &str = "http://127.0.0.1:8757";
const DESKTOP_AGENT_MESSAGE_TIMEOUT_MS: u64 = 600_000;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

struct BackendState {
    child: Mutex<Option<Child>>,
    event_bridge_started: Mutex<bool>,
}

impl Drop for BackendState {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

#[derive(Serialize)]
struct BackendStartResult {
    endpoint: String,
    started: bool,
    already_running: bool,
    mode: String,
    message: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct AppApiRequest {
    method: Option<String>,
    path: String,
    body: Option<serde_json::Value>,
    timeout_ms: Option<u64>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct AppApiResponse {
    status: u16,
    ok: bool,
    body: serde_json::Value,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopRuntimeSnapshotRequest {
    session_id: Option<String>,
    project_root: Option<String>,
    include_patch: Option<bool>,
    global_only: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
struct DesktopProviderConfigRequest {
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
struct DesktopVisionConfigRequest {
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
struct DesktopProviderTestRequest {
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
struct DesktopPermissionRequest {
    #[serde(alias = "executionMode")]
    execution_mode: String,
    #[serde(default, alias = "acknowledgeRoslynRisk")]
    acknowledge_roslyn_risk: bool,
    #[serde(default, alias = "timeoutMs")]
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopAgentMessageRequest {
    message: String,
    session_id: Option<String>,
    history: Option<Vec<serde_json::Value>>,
    agent_name: Option<String>,
    attachments: Option<Vec<serde_json::Value>>,
    project_path: Option<String>,
    provider: Option<String>,
    provider_label: Option<String>,
    model: Option<String>,
    client_turn_id: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopAgentRunCancelRequest {
    session_id: Option<String>,
    turn_id: Option<String>,
    client_turn_id: Option<String>,
    reason: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopAgentRunQueuedRequest {
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
struct DesktopApprovalScopeRequest {
    approval_id: String,
    expected_project_root: Option<String>,
    global_only: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopApprovalRevisionRequest {
    approval_id: String,
    reason: Option<String>,
    note: Option<String>,
    expected_project_root: Option<String>,
    global_only: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopCheckpointsRequest {
    project_root: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopCheckpointIdRequest {
    checkpoint_id: String,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopRecoveriesRequest {
    project_root: Option<String>,
    include_resolved: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopRecoveryIdRequest {
    recovery_id: String,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopResolveRecoveryRequest {
    recovery_id: String,
    confirm_resolved: bool,
    note: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopExternalAgentConnectorsRequest {
    project_path: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopExternalAgentGatewayRequest {
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
struct DesktopExternalAgentConnectorActionRequest {
    client: String,
    project_path: Option<String>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopBootstrapRequest {
    refresh_projects: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopTimeoutRequest {
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopWorkspaceDiffRequest {
    root: Option<String>,
    include_patch: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopUnityMcpRepairRequest {
    project_path: Option<String>,
    allow_unity_relaunch: Option<bool>,
    wait_seconds: Option<u64>,
    close_timeout_seconds: Option<u64>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopDiagnosticsUpdateRequest {
    debug_logging: bool,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopSupportBundleRequest {
    include_full_paths: Option<bool>,
    log_limit: Option<u64>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopProjectPrefsRequest {
    custom_paths: Option<Vec<String>>,
    hidden_paths: Option<Vec<String>>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopProjectIndexScanRequest {
    project_path: String,
    max_files: Option<u64>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopAdjustmentCheckpointsRequest {
    kind: Option<String>,
    project_root: Option<String>,
    avatar_path: Option<String>,
    include_deleted: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopAdjustmentCheckpointBodyRequest {
    body: serde_json::Value,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopAdjustmentCheckpointIdRequest {
    checkpoint_id: String,
    body: Option<serde_json::Value>,
    hard_delete: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopJsonBodyRequest {
    body: serde_json::Value,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopIdJsonBodyRequest {
    id: String,
    body: serde_json::Value,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopAgentListRequest {
    limit: Option<u64>,
    session_id: Option<String>,
    project_root: Option<String>,
    client_turn_id: Option<String>,
    scope: Option<String>,
    include_events: Option<bool>,
    global_only: Option<bool>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopChatListRequest {
    project_paths: Option<Vec<String>>,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopOptimizationProofsRequest {
    limit: Option<u64>,
    timeout_ms: Option<u64>,
}

#[tauri::command]
fn backend_endpoint() -> String {
    BACKEND_ENDPOINT.to_string()
}

fn backend_json_request(
    method: &str,
    path: String,
    body: Option<serde_json::Value>,
    timeout_ms: Option<u64>,
) -> Result<serde_json::Value, String> {
    let user_data = user_data_dir()?;
    let app_session_token = ensure_app_session_token(&user_data)?;
    ensure_backend_session_verified(&app_session_token)?;
    let timeout = Duration::from_millis(timeout_ms.unwrap_or(30_000).clamp(1_000, 600_000));
    let agent = ureq::builder()
        .timeout_connect(Duration::from_secs(2))
        .timeout(timeout)
        .redirects(0)
        .build();
    let url = format!("{BACKEND_ENDPOINT}{path}");
    let http_request = agent
        .request(method, &url)
        .set("Accept", "application/json")
        .set("Origin", "tauri://localhost")
        .set("Authorization", &format!("Bearer {app_session_token}"));
    let response = if let Some(body) = body {
        http_request
            .set("Content-Type", "application/json")
            .send_string(&body.to_string())
    } else {
        http_request.call()
    };
    let response = app_api_response_from_ureq(response)?;
    if response.ok {
        Ok(response.body)
    } else {
        Err(webview_error_message(&response.body))
    }
}

fn remove_secret_response_fields(value: &mut serde_json::Value) {
    match value {
        serde_json::Value::Object(object) => {
            for key in [
                "api_key",
                "apiKey",
                "authorization",
                "Authorization",
                "configPath",
                "backupPath",
            ] {
                object.remove(key);
            }
            for child in object.values_mut() {
                remove_secret_response_fields(child);
            }
        }
        serde_json::Value::String(text) => {
            *text = sanitize_text_for_webview(text);
        }
        serde_json::Value::Array(items) => {
            for item in items {
                remove_secret_response_fields(item);
            }
        }
        _ => {}
    }
}

fn sanitize_webview_response(mut value: serde_json::Value) -> serde_json::Value {
    remove_secret_response_fields(&mut value);
    value
}

fn sanitize_text_for_webview(value: &str) -> String {
    let lower = value.to_ascii_lowercase();
    if lower.contains("api_key")
        || lower.contains("apikey")
        || lower.contains("configpath")
        || lower.contains("backuppath")
        || (lower.contains("bearer ") && !is_env_placeholder_template(value))
        || (lower.contains("authorization") && !is_env_placeholder_template(value))
    {
        return "[redacted]".to_string();
    }
    value.to_string()
}

fn webview_error_message(body: &serde_json::Value) -> String {
    let Some(detail) = body.get("detail") else {
        return "VRCForge runtime request failed.".to_string();
    };
    match detail {
        serde_json::Value::String(text) => sanitize_text_for_webview(text),
        serde_json::Value::Object(_) => {
            let sanitized = sanitize_webview_response(detail.clone());
            for key in ["error", "message", "detail", "reason"] {
                if let Some(text) = sanitized.get(key).and_then(|value| value.as_str()) {
                    return sanitize_text_for_webview(text);
                }
            }
            if let Some(status) = sanitized.get("status") {
                if status.is_string() || status.is_number() || status.is_boolean() {
                    return format!("VRCForge runtime request failed: {status}");
                }
            }
            "VRCForge runtime request failed.".to_string()
        }
        _ => "VRCForge runtime request failed.".to_string(),
    }
}

fn is_env_placeholder_template(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    lower.contains("${")
        && lower.contains('}')
        && (lower.contains("token") || lower.contains("api_key") || lower.contains("apikey"))
}

fn sanitize_error_message(message: String, secrets: &[&str]) -> String {
    let mut sanitized = sanitize_text_for_webview(&message);
    for secret in secrets {
        let secret = secret.trim();
        if !secret.is_empty() {
            sanitized = sanitized.replace(secret, "[redacted]");
        }
    }
    sanitized
}

fn sanitize_provider_result(
    result: Result<serde_json::Value, String>,
    secrets: &[&str],
) -> Result<serde_json::Value, String> {
    result
        .map(sanitize_webview_response)
        .map_err(|message| sanitize_error_message(message, secrets))
}

fn provider_config_body(
    provider: String,
    api_key: Option<String>,
    base_url: Option<String>,
    model: Option<String>,
) -> serde_json::Value {
    let mut body = serde_json::Map::new();
    body.insert("provider".to_string(), serde_json::Value::String(provider));
    if let Some(api_key) = api_key {
        body.insert("api_key".to_string(), serde_json::Value::String(api_key));
    }
    body.insert(
        "base_url".to_string(),
        base_url.map_or(serde_json::Value::Null, serde_json::Value::String),
    );
    body.insert(
        "model".to_string(),
        model.map_or(serde_json::Value::Null, serde_json::Value::String),
    );
    serde_json::Value::Object(body)
}

#[tauri::command]
fn desktop_runtime_snapshot(
    request: DesktopRuntimeSnapshotRequest,
) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn update_api_config(request: DesktopProviderConfigRequest) -> Result<serde_json::Value, String> {
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
fn update_vision_config(request: DesktopVisionConfigRequest) -> Result<serde_json::Value, String> {
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
fn fetch_provider_models(
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
fn test_provider_capability(
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
fn update_permission_mode(request: DesktopPermissionRequest) -> Result<serde_json::Value, String> {
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
fn send_agent_message(request: DesktopAgentMessageRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/agent/message".to_string(),
        Some(serde_json::json!({
            "agent_name": request.agent_name.unwrap_or_else(|| "desktop-agent".to_string()),
            "session_id": request.session_id,
            "clientTurnId": request.client_turn_id,
            "message": request.message,
            "history": request.history.unwrap_or_default(),
            "attachments": request.attachments.unwrap_or_default(),
            "projectPath": request.project_path,
            "provider": request.provider,
            "providerLabel": request.provider_label,
            "model": request.model,
        })),
        request
            .timeout_ms
            .or(Some(DESKTOP_AGENT_MESSAGE_TIMEOUT_MS)),
    )
}

#[tauri::command]
fn request_agent_run_cancel(
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
fn record_agent_run_queued(
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

fn approval_scope_body(request: &DesktopApprovalScopeRequest) -> serde_json::Value {
    serde_json::json!({
        "expectedProjectRoot": request.expected_project_root,
        "globalOnly": request.global_only,
    })
}

#[tauri::command]
fn approve_agent_approval(
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
fn reject_agent_approval(
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
fn request_approval_revision(
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
fn fetch_checkpoints(request: DesktopCheckpointsRequest) -> Result<serde_json::Value, String> {
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
fn preview_restore_checkpoint(
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
fn request_restore_checkpoint(
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
fn fetch_interrupted_apply_recoveries(
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
fn preview_interrupted_apply_recovery(
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
fn request_restore_interrupted_apply_recovery(
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
fn resolve_interrupted_apply_recovery(
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
fn export_interrupted_apply_incident_bundle(
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
fn fetch_app_bootstrap(request: DesktopBootstrapRequest) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn fetch_app_health(request: DesktopTimeoutRequest) -> Result<serde_json::Value, String> {
    backend_json_request("GET", "/api/health".to_string(), None, request.timeout_ms)
        .map(sanitize_webview_response)
}

#[tauri::command]
fn refresh_projects(request: DesktopTimeoutRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/projects/refresh".to_string(),
        None,
        request.timeout_ms.or(Some(30_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn refresh_unity_readiness(request: DesktopTimeoutRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/unity/readiness/refresh".to_string(),
        None,
        request.timeout_ms.or(Some(20_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn fetch_workspace_diff(request: DesktopWorkspaceDiffRequest) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn fetch_doctor(request: DesktopTimeoutRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "GET",
        "/api/app/doctor".to_string(),
        None,
        request.timeout_ms.or(Some(30_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn repair_unity_mcp_bridge(
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
fn fetch_diagnostics(request: DesktopTimeoutRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "GET",
        "/api/app/diagnostics".to_string(),
        None,
        request.timeout_ms.or(Some(30_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn update_diagnostics(
    request: DesktopDiagnosticsUpdateRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/diagnostics".to_string(),
        Some(serde_json::json!({
            "debugLogging": request.debug_logging,
        })),
        request.timeout_ms.or(Some(30_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn export_support_bundle(
    request: DesktopSupportBundleRequest,
) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn fetch_project_prefs(request: DesktopTimeoutRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "GET",
        "/api/app/projects/prefs".to_string(),
        None,
        request.timeout_ms.or(Some(30_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn save_project_prefs(request: DesktopProjectPrefsRequest) -> Result<serde_json::Value, String> {
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
fn scan_project_index(
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
fn fetch_adjustment_checkpoints(
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
fn create_adjustment_checkpoint(
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
fn update_adjustment_checkpoint(
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
fn delete_adjustment_checkpoint(
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
fn overwrite_adjustment_checkpoint(
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
fn select_adjustment_checkpoint(
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
fn apply_adjustment_checkpoint(
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
fn preview_adjustment_checkpoint(
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

fn post_json_body_command(
    path: &str,
    request: DesktopJsonBodyRequest,
    default_timeout_ms: u64,
) -> Result<serde_json::Value, String> {
    json_body_command("POST", path, request, default_timeout_ms)
}

fn json_body_command(
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

fn append_query_param(query: &mut Vec<String>, key: &str, value: &Option<String>) {
    if let Some(value) = value.as_deref().filter(|value| !value.is_empty()) {
        query.push(format!("{key}={}", percent_encode_query_component(value)));
    }
}

fn agent_list_query(request: &DesktopAgentListRequest) -> String {
    let mut query = Vec::new();
    if let Some(value) = request.limit {
        query.push(format!("limit={value}"));
    }
    append_query_param(&mut query, "sessionId", &request.session_id);
    append_query_param(&mut query, "projectRoot", &request.project_root);
    append_query_param(&mut query, "clientTurnId", &request.client_turn_id);
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
fn fetch_avatars(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/avatars", request, 60_000)
}

#[tauri::command]
fn fetch_optimization_plan(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/optimization/plan", request, 120_000)
}

#[tauri::command]
fn request_optimization_apply(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/optimization/apply-request", request, 120_000)
}

#[tauri::command]
fn plan_outfit_import(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/outfit-imports/plan", request, 120_000)
}

#[tauri::command]
fn request_outfit_import(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/outfit-imports/request", request, 120_000)
}

#[tauri::command]
fn request_package_install(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/package-install/request", request, 120_000)
}

#[tauri::command]
fn plan_avatar_encryption(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/avatar-encryption/plan", request, 120_000)
}

#[tauri::command]
fn request_avatar_encryption_apply(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/avatar-encryption/apply-request", request, 120_000)
}

#[tauri::command]
fn fetch_skill_packages() -> Result<serde_json::Value, String> {
    backend_json_request("GET", "/api/app/skill-packages".to_string(), None, None)
        .map(sanitize_webview_response)
}

#[tauri::command]
fn preflight_skill_package(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/preflight", request, 120_000)
}

#[tauri::command]
fn import_skill_package(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/import", request, 120_000)
}

#[tauri::command]
fn set_skill_package_safe_mode(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/safe-mode", request, 60_000)
}

#[tauri::command]
fn trust_skill_package_signer(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/trust-signer", request, 60_000)
}

#[tauri::command]
fn revoke_skill_package_signer(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/revoke-signer", request, 60_000)
}

#[tauri::command]
fn block_skill_package(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/block-package", request, 60_000)
}

#[tauri::command]
fn export_skill_package(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skill-packages/export", request, 120_000)
}

#[tauri::command]
fn set_skill_package_enabled(
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
fn uninstall_skill_package(request: DesktopIdJsonBodyRequest) -> Result<serde_json::Value, String> {
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
fn preview_path_to_skill(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/path-to-skill/preview", request, 120_000)
}

#[tauri::command]
fn write_path_to_skill(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/path-to-skill/write", request, 120_000)
}

#[tauri::command]
fn fetch_skills() -> Result<serde_json::Value, String> {
    backend_json_request("GET", "/api/app/skills".to_string(), None, None)
        .map(sanitize_webview_response)
}

#[tauri::command]
fn check_skills() -> Result<serde_json::Value, String> {
    backend_json_request("GET", "/api/app/skills/check".to_string(), None, None)
        .map(sanitize_webview_response)
}

#[tauri::command]
fn create_skill(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/skills", request, 60_000)
}

#[tauri::command]
fn update_skill(request: DesktopIdJsonBodyRequest) -> Result<serde_json::Value, String> {
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
fn delete_skill(request: DesktopIdJsonBodyRequest) -> Result<serde_json::Value, String> {
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
fn fetch_sub_agents(request: DesktopAgentListRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "GET",
        format!("/api/app/sub-agents{}", agent_list_query(&request)),
        None,
        request.timeout_ms,
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn create_sub_agent(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/sub-agents", request, 60_000)
}

#[tauri::command]
fn fetch_sub_agent(request: DesktopIdJsonBodyRequest) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn cancel_sub_agent(request: DesktopIdJsonBodyRequest) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn retry_sub_agent(request: DesktopIdJsonBodyRequest) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn fetch_agent_runs(request: DesktopAgentListRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "GET",
        format!("/api/app/agent/runs{}", agent_list_query(&request)),
        None,
        request.timeout_ms,
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn fetch_agent_approvals(request: DesktopAgentListRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "GET",
        format!("/api/app/agent/approvals{}", agent_list_query(&request)),
        None,
        request.timeout_ms,
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn fetch_agent_desktop_actions(
    request: DesktopAgentListRequest,
) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn request_agent_desktop_action(
    request: DesktopJsonBodyRequest,
) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/agent/desktop-actions", request, 60_000)
}

#[tauri::command]
fn fetch_agent_goals(request: DesktopAgentListRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "GET",
        format!("/api/app/agent/goals{}", agent_list_query(&request)),
        None,
        request.timeout_ms,
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn create_agent_goal(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/agent/goals", request, 60_000)
}

#[tauri::command]
fn update_agent_goal(request: DesktopIdJsonBodyRequest) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn fetch_agent_memory(request: DesktopAgentListRequest) -> Result<serde_json::Value, String> {
    backend_json_request(
        "GET",
        format!("/api/app/agent/memory{}", agent_list_query(&request)),
        None,
        request.timeout_ms,
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn create_agent_memory(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/agent/memory", request, 60_000)
}

#[tauri::command]
fn delete_agent_memory(request: DesktopIdJsonBodyRequest) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn clear_agent_memory(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/agent/memory/clear", request, 60_000)
}

#[tauri::command]
fn compact_agent_history(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/agent/compact", request, 120_000)
}

#[tauri::command]
fn fetch_agent_notes() -> Result<serde_json::Value, String> {
    backend_json_request("GET", "/api/app/agent-notes".to_string(), None, None)
        .map(sanitize_webview_response)
}

#[tauri::command]
fn save_agent_notes(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/agent-notes", request, 60_000)
}

#[tauri::command]
fn fetch_chats(request: DesktopChatListRequest) -> Result<serde_json::Value, String> {
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
}

#[tauri::command]
fn save_chats(request: DesktopJsonBodyRequest) -> Result<serde_json::Value, String> {
    post_json_body_command("/api/app/chats", request, 60_000)
}

#[tauri::command]
fn fetch_optimization_proofs(
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
fn fetch_optimization_proof(
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
fn fetch_external_agent_connectors(
    request: DesktopExternalAgentConnectorsRequest,
) -> Result<serde_json::Value, String> {
    let suffix = request
        .project_path
        .as_deref()
        .filter(|value| !value.is_empty())
        .map(|value| format!("?projectPath={}", percent_encode_query_component(value)))
        .unwrap_or_default();
    backend_json_request(
        "GET",
        format!("/api/app/external-agent/connectors{suffix}"),
        None,
        request.timeout_ms.or(Some(30_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn update_external_agent_gateway(
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
fn install_external_agent_connector(
    request: DesktopExternalAgentConnectorActionRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/external-agent/connectors/install".to_string(),
        Some(serde_json::json!({
            "client": request.client,
            "projectPath": request.project_path,
        })),
        request.timeout_ms.or(Some(120_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn uninstall_external_agent_connector(
    request: DesktopExternalAgentConnectorActionRequest,
) -> Result<serde_json::Value, String> {
    backend_json_request(
        "POST",
        "/api/app/external-agent/connectors/uninstall".to_string(),
        Some(serde_json::json!({
            "client": request.client,
            "projectPath": request.project_path,
        })),
        request.timeout_ms.or(Some(60_000)),
    )
    .map(sanitize_webview_response)
}

#[tauri::command]
fn app_api_request(request: AppApiRequest) -> Result<AppApiResponse, String> {
    let method = request
        .method
        .unwrap_or_else(|| "GET".to_string())
        .to_ascii_uppercase();
    if !matches!(method.as_str(), "GET" | "POST" | "PUT" | "PATCH" | "DELETE") {
        return Err("desktop IPC bridge rejected this HTTP method".to_string());
    }
    let path = normalize_app_api_path(&method, &request.path)?;
    let user_data = user_data_dir()?;
    let app_session_token = ensure_app_session_token(&user_data)?;
    ensure_backend_session_verified(&app_session_token)?;
    let timeout = Duration::from_millis(request.timeout_ms.unwrap_or(30_000).clamp(1_000, 600_000));
    let agent = ureq::builder()
        .timeout_connect(Duration::from_secs(2))
        .timeout(timeout)
        .redirects(0)
        .build();
    let url = format!("{BACKEND_ENDPOINT}{path}");
    let http_request = agent
        .request(&method, &url)
        .set("Accept", "application/json")
        .set("Origin", "tauri://localhost")
        .set("Authorization", &format!("Bearer {app_session_token}"));
    let response = if let Some(body) = request.body {
        http_request
            .set("Content-Type", "application/json")
            .send_string(&body.to_string())
    } else {
        http_request.call()
    };
    app_api_response_from_ureq(response).map(|mut response| {
        response.body = sanitize_webview_response(response.body);
        response
    })
}

fn start_backend_event_bridge_once(
    app_handle: tauri::AppHandle,
    state: &BackendState,
    app_session_token: String,
) -> Result<(), String> {
    let mut started = state
        .event_bridge_started
        .lock()
        .map_err(|_| "backend event bridge state lock poisoned".to_string())?;
    if *started {
        return Ok(());
    }
    *started = true;
    thread::spawn(move || backend_event_bridge_loop(app_handle, app_session_token));
    Ok(())
}

fn backend_event_bridge_loop(app_handle: tauri::AppHandle, app_session_token: String) {
    loop {
        match issue_backend_event_ticket(&app_session_token).and_then(connect_backend_event_socket)
        {
            Ok(mut socket) => {
                let _ = app_handle.emit(
                    "vrcforge-backend-event-status",
                    serde_json::json!({"ok": true, "status": "connected"}),
                );
                loop {
                    match socket.read() {
                        Ok(message) if message.is_text() => match message.into_text() {
                            Ok(text) => match serde_json::from_str::<serde_json::Value>(&text) {
                                Ok(payload) => {
                                    if let Some(event) = sanitize_backend_event(payload) {
                                        let _ = app_handle.emit("vrcforge-backend-event", event);
                                    }
                                }
                                Err(error) => {
                                    let _ = app_handle.emit(
                                        "vrcforge-backend-event-status",
                                        serde_json::json!({
                                            "ok": false,
                                            "status": "invalid_event",
                                            "error": error.to_string()
                                        }),
                                    );
                                }
                            },
                            Err(error) => {
                                let _ = app_handle.emit(
                                    "vrcforge-backend-event-status",
                                    serde_json::json!({
                                        "ok": false,
                                        "status": "invalid_text",
                                        "error": error.to_string()
                                    }),
                                );
                            }
                        },
                        Ok(message) if message.is_close() => break,
                        Ok(_) => {}
                        Err(error) => {
                            let _ = app_handle.emit(
                                "vrcforge-backend-event-status",
                                serde_json::json!({
                                    "ok": false,
                                    "status": "disconnected",
                                    "error": error.to_string()
                                }),
                            );
                            break;
                        }
                    }
                }
            }
            Err(error) => {
                let _ = app_handle.emit(
                    "vrcforge-backend-event-status",
                    serde_json::json!({
                        "ok": false,
                        "status": "connect_failed",
                        "error": error
                    }),
                );
            }
        }
        thread::sleep(Duration::from_millis(1500));
    }
}

fn issue_backend_event_ticket(app_session_token: &str) -> Result<String, String> {
    let agent = ureq::builder()
        .timeout_connect(Duration::from_secs(2))
        .timeout(Duration::from_secs(8))
        .redirects(0)
        .build();
    let response = app_api_response_from_ureq(
        agent
            .post(&format!("{BACKEND_ENDPOINT}/api/app/ws-ticket"))
            .set("Accept", "application/json")
            .set("Origin", "tauri://localhost")
            .set("Authorization", &format!("Bearer {app_session_token}"))
            .call(),
    )?;
    if !response.ok {
        return Err(response
            .body
            .get("detail")
            .and_then(|value| value.as_str())
            .unwrap_or("Backend event ticket request failed.")
            .to_string());
    }
    response
        .body
        .get("ticket")
        .and_then(|value| value.as_str())
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .ok_or_else(|| "Backend event ticket response did not include a ticket.".to_string())
}

fn connect_backend_event_socket(
    ticket: String,
) -> Result<tungstenite::WebSocket<tungstenite::stream::MaybeTlsStream<TcpStream>>, String> {
    let url = format!(
        "ws://{BACKEND_HOST}:{BACKEND_PORT}/ws?ws_ticket={}",
        percent_encode_query_component(&ticket)
    );
    let (socket, _) = tungstenite::connect(url.as_str())
        .map_err(|error| format!("unable to connect backend event socket: {error}"))?;
    Ok(socket)
}

fn sanitize_backend_event(payload: serde_json::Value) -> Option<serde_json::Value> {
    let event_type = payload.get("type")?.as_str()?;
    if !desktop_backend_event_allowed(event_type) {
        return None;
    }
    let mut event = serde_json::json!({ "type": event_type });
    if let Some(timestamp) = payload.get("timestamp") {
        event["timestamp"] = timestamp.clone();
    }
    Some(event)
}

fn desktop_backend_event_allowed(event_type: &str) -> bool {
    matches!(
        event_type,
        "agentApprovals"
            | "agentDesktopActions"
            | "agentGoals"
            | "agentMemory"
            | "agentPermission"
            | "agentRuntimeCancel"
            | "agentRuntimeQueue"
            | "agentRuntimeRuns"
            | "agentRuntimeTurn"
            | "hello"
            | "projects"
            | "subAgentTasks"
            | "unity_status"
    )
}

#[tauri::command]
fn start_backend(
    app_handle: tauri::AppHandle,
    state: State<'_, BackendState>,
) -> Result<BackendStartResult, String> {
    let user_data = user_data_dir()?;
    let app_session_token = ensure_app_session_token(&user_data)?;
    if backend_port_open() {
        if !existing_backend_accepts_session(&app_session_token) {
            return Err(
                "Port 8757 is already used by a VRCForge runtime that does not accept this desktop session. Close all VRCForge.exe processes in Task Manager and launch VRCForge again.".to_string()
            );
        }
        start_backend_event_bridge_once(app_handle, &state, app_session_token.clone())?;
        return Ok(BackendStartResult {
            endpoint: BACKEND_ENDPOINT.to_string(),
            started: false,
            already_running: true,
            mode: "existing".to_string(),
            message: "已连接本机 VRCForge runtime".to_string(),
        });
    }

    let root = repo_root()?;
    prepare_runtime_files(&root, &user_data)?;
    let log_dir = user_data.join("logs");
    fs::create_dir_all(&log_dir).map_err(|error| {
        format!(
            "unable to create runtime log directory {}: {error}",
            log_dir.display()
        )
    })?;
    let stdout_log = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_dir.join("backend_stdout.log"))
        .map_err(|error| format!("unable to open backend stdout log: {error}"))?;
    let stderr_log = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_dir.join("backend_stderr.log"))
        .map_err(|error| format!("unable to open backend stderr log: {error}"))?;

    let mut command = backend_command(&root)?;
    command
        .current_dir(&root)
        .env("VRCFORGE_APP_DIR", &root)
        .env("VRCFORGE_USER_DATA_DIR", &user_data)
        .env("VRCFORGE_CONFIG_DIR", user_data.join("config"))
        .env("VRCFORGE_LOG_DIR", user_data.join("logs"))
        .env("VRCFORGE_ARTIFACTS_DIR", user_data.join("artifacts"))
        .env("VRCFORGE_DASHBOARD_DIR", root.join("dashboard"))
        .env(
            "VRCFORGE_SETTINGS_PATH",
            user_data.join("config").join("settings.json"),
        )
        .env("VRCFORGE_APP_SESSION_TOKEN", &app_session_token)
        .arg("--host")
        .arg(BACKEND_HOST)
        .arg("--port")
        .arg(BACKEND_PORT.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout_log))
        .stderr(Stdio::from(stderr_log));
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let child = command
        .spawn()
        .map_err(|error| format!("无法启动本地 runtime: {error}"))?;

    {
        let mut guard = state
            .child
            .lock()
            .map_err(|_| "backend state lock poisoned".to_string())?;
        *guard = Some(child);
    }

    if !wait_for_backend(Duration::from_secs(18)) {
        return Err(format!(
            "本地 runtime 未在 18 秒内就绪。日志目录: {}",
            user_data.join("logs").display()
        ));
    }

    start_backend_event_bridge_once(app_handle, &state, app_session_token.clone())?;
    Ok(BackendStartResult {
        endpoint: BACKEND_ENDPOINT.to_string(),
        started: true,
        already_running: false,
        mode: "managed".to_string(),
        message: "已启动桌面 App 管理的 VRCForge runtime".to_string(),
    })
}

#[tauri::command]
fn stop_backend(state: State<'_, BackendState>) -> Result<(), String> {
    let mut guard = state
        .child
        .lock()
        .map_err(|_| "backend state lock poisoned".to_string())?;
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    Ok(())
}

#[tauri::command]
fn ensure_agent_notes_file() -> String {
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
fn open_folder(path: String) -> Result<(), String> {
    let folder = validate_project_folder_to_open(&path)?;
    open_folder_in_shell(folder)
}

#[tauri::command]
fn open_local_folder(path: String) -> Result<(), String> {
    let folder = validate_local_folder_to_open(&path)?;
    open_folder_in_shell(folder)
}

#[tauri::command]
fn select_folder(initial_path: Option<String>) -> Result<Option<String>, String> {
    select_folder_dialog(initial_path.as_deref())
}

fn open_folder_in_shell(folder: PathBuf) -> Result<(), String> {
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

fn validate_local_folder_to_open(path: &str) -> Result<PathBuf, String> {
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

fn select_folder_dialog(initial_path: Option<&str>) -> Result<Option<String>, String> {
    #[cfg(windows)]
    {
        // Native folder picker via `rfd` (IFileDialog on Windows). Replaces the
        // old PowerShell FolderBrowserDialog subprocess: no child process, no
        // script injection surface, and non-ASCII paths survive without
        // encoding tricks.
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

fn validate_project_folder_to_open(path: &str) -> Result<PathBuf, String> {
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

fn try_ensure_agent_notes_file(user_data: &Path) -> Result<PathBuf, String> {
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

fn backend_command(root: &Path) -> Result<Command, String> {
    let packaged = root.join("backend").join("vrcforge_backend.exe");
    if packaged.exists() {
        return Ok(Command::new(packaged));
    }

    let script = root.join("dashboard_server.py");
    if !script.exists() {
        return Err(format!("找不到 dashboard_server.py: {}", script.display()));
    }
    let python = env::var("PYTHON").unwrap_or_else(|_| "python".to_string());
    let mut command = Command::new(python);
    command.arg(script);
    Ok(command)
}

fn prepare_runtime_files(root: &Path, user_data: &Path) -> Result<(), String> {
    for dir in ["config", "logs", "artifacts", "backups", "skills"] {
        let path = user_data.join(dir);
        fs::create_dir_all(&path)
            .map_err(|error| format!("无法创建必要的运行目录 {}: {error}", path.display()))?;
    }
    let settings_path = user_data.join("config").join("settings.json");
    if !settings_path.exists() {
        let settings = serde_json::json!({
            "gemini": {
                "api_key_env": "GEMINI_API_KEY",
                "model": "gemini-2.5-flash",
                "thinking_level": ""
            },
            "unity_mcp": {
                "command": ["powershell", "-ExecutionPolicy", "Bypass", "-File", "tools/unity-mcp-cli.ps1"],
                "host": "127.0.0.1",
                "port": 8080,
                "instance": "",
                "retries": 3,
                "retry_backoff_seconds": 2.0,
                "timeout_seconds": 30,
                "export_tool_name": "vrc_export_blendshapes",
                "execute_tool_name": "vrc_apply_blendshapes"
            },
            "paths": {
                "blendshape_export": "Assets/VRCForge/blendshapes_export.json"
            },
            "planning": {
                "min_confidence": 0.65
            },
            "dashboard": {
                "project_roots": [],
                "unity_editor_path": "",
                "status_push_interval_seconds": 2.5
            }
        });
        fs::write(
            settings_path,
            serde_json::to_string_pretty(&settings).map_err(|error| error.to_string())?,
        )
        .map_err(|error| format!("无法写入默认 settings.json: {error}"))?;
    }
    if let Err(error) = try_ensure_agent_notes_file(user_data) {
        eprintln!("Optional AGENTS.md is unavailable: {error}");
    }
    if !root.join("dashboard").join("index.html").exists() {
        return Err("缺少 dashboard 静态资源，无法启动 runtime。".to_string());
    }
    Ok(())
}

fn repo_root() -> Result<PathBuf, String> {
    if let Ok(exe_path) = env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            let packaged_markers = [
                exe_dir.join("backend").join("vrcforge_backend.exe"),
                exe_dir.join("dashboard").join("index.html"),
                exe_dir.join("unity_plugin"),
            ];
            if packaged_markers.iter().any(|marker| marker.exists()) {
                return Ok(exe_dir.to_path_buf());
            }
        }
    }

    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| "无法解析 VRCForge 项目根目录".to_string())
}

fn user_data_dir() -> Result<PathBuf, String> {
    if let Ok(value) = env::var("VRCFORGE_USER_DATA_DIR") {
        if !value.trim().is_empty() {
            return Ok(PathBuf::from(value));
        }
    }
    let base = env::var("LOCALAPPDATA")
        .or_else(|_| env::var("APPDATA"))
        .map_err(|_| "无法解析 Windows 用户数据目录".to_string())?;
    Ok(PathBuf::from(base).join("VRCForge").join("agentic-app"))
}

fn ensure_app_session_token(user_data: &Path) -> Result<String, String> {
    let config_dir = user_data.join("config");
    fs::create_dir_all(&config_dir).map_err(|error| format!("无法创建用户配置目录: {error}"))?;
    let token_path = config_dir.join("app-session-token");
    if let Ok(existing) = fs::read_to_string(&token_path) {
        let token = existing.trim().to_string();
        if token.len() >= 32 {
            return Ok(token);
        }
    }
    let token = generate_session_token()?;
    fs::write(&token_path, &token)
        .map_err(|error| format!("无法写入 app session token: {error}"))?;
    Ok(token)
}

fn generate_session_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| format!("Unable to generate app session token: {error}"))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

/// HMAC-SHA256 hex digest backed by the audited RustCrypto `hmac` crate
/// (replaces a hand-rolled ipad/opad implementation with identical output —
/// see the known-vector test below).
#[cfg(test)]
fn hmac_sha256_hex(key: &[u8], message: &[u8]) -> String {
    let mut mac =
        Hmac::<Sha256>::new_from_slice(key).expect("HMAC-SHA256 accepts keys of any length");
    mac.update(message);
    mac.finalize()
        .into_bytes()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Nonce-challenge design (kept intentionally): the shell generates a fresh
/// random nonce per probe, the backend signs "vrcforge.app-session.v1\n{nonce}"
/// with the shared app session token, and the shell recomputes the signature
/// locally to compare. The token itself never travels over the wire, and a
/// captured response cannot be replayed against a different nonce.
#[cfg(test)]
fn app_session_challenge_signature(token: &str, nonce: &str) -> String {
    hmac_sha256_hex(
        token.as_bytes(),
        format!("vrcforge.app-session.v1\n{nonce}").as_bytes(),
    )
}

fn app_session_challenge_signature_matches(token: &str, nonce: &str, signature: &str) -> bool {
    let Some(signature_bytes) = decode_hmac_sha256_hex(signature) else {
        return false;
    };
    let mut mac =
        Hmac::<Sha256>::new_from_slice(token.as_bytes()).expect("HMAC-SHA256 accepts any key");
    mac.update(format!("vrcforge.app-session.v1\n{nonce}").as_bytes());
    mac.verify_slice(&signature_bytes).is_ok()
}

fn normalize_app_api_path(method: &str, path: &str) -> Result<String, String> {
    let trimmed = path.trim();
    if !trimmed.starts_with('/') {
        return Err("desktop IPC bridge requires an app-relative API path".to_string());
    }
    if trimmed.contains('#') {
        return Err("desktop IPC bridge rejected an unsafe API path".to_string());
    }
    let route = trimmed.split('?').next().unwrap_or(trimmed);
    let decoded_route = percent_decode_route(route)?;
    if decoded_route.contains("://") || decoded_route.contains('\\') || decoded_route.contains("..")
    {
        return Err("desktop IPC bridge rejected an unsafe API path".to_string());
    }
    if !decoded_route.starts_with("/api/") {
        return Err(format!(
            "desktop IPC bridge path is not enabled: {decoded_route}"
        ));
    }
    if matches!(
        decoded_route.as_str(),
        "/api/app/session" | "/api/app/session-challenge"
    ) {
        return Err(format!(
            "desktop IPC bridge path is not exposed to WebView: {decoded_route}"
        ));
    }
    if decoded_route == "/api/agent" || decoded_route.starts_with("/api/agent/") {
        return Err(format!(
            "desktop IPC bridge keeps external agent API on its HTTP boundary: {decoded_route}"
        ));
    }
    if !desktop_ipc_route_allowed(method, &decoded_route) {
        return Err(format!(
            "desktop IPC bridge path is not in the desktop allowlist: {decoded_route}"
        ));
    }
    Ok(trimmed.to_string())
}

fn desktop_ipc_route_allowed(method: &str, route: &str) -> bool {
    let normalized_method = method.to_ascii_uppercase();
    let route_path = route.split('?').next().unwrap_or(route);
    if desktop_ipc_route_migrated_to_typed_command(&normalized_method, route_path) {
        return false;
    }
    if normalized_method == "GET" && route == "/api/health" {
        return true;
    }
    if route == "/api/projects/refresh" {
        return normalized_method == "POST";
    }
    let app_prefixes = [
        "/api/app/adjustment-checkpoints",
        "/api/app/agent",
        "/api/app/agent-notes",
        "/api/app/avatars",
        "/api/app/bootstrap",
        "/api/app/chats",
        "/api/app/checkpoints",
        "/api/app/diagnostics",
        "/api/app/doctor",
        "/api/app/external-agent",
        "/api/app/optimization",
        "/api/app/outfit-imports",
        "/api/app/package-install",
        "/api/app/path-to-skill",
        "/api/app/permission",
        "/api/app/project-index",
        "/api/app/projects",
        "/api/app/provider",
        "/api/app/recoveries",
        "/api/app/runtime",
        "/api/app/skill-packages",
        "/api/app/skills",
        "/api/app/sub-agents",
        "/api/app/support-bundle",
        "/api/app/unity",
        "/api/app/workspace",
    ];
    app_prefixes
        .iter()
        .any(|prefix| route == *prefix || route.starts_with(&format!("{prefix}/")))
}

fn desktop_ipc_route_migrated_to_typed_command(method: &str, route_path: &str) -> bool {
    if matches!(
        route_path,
        "/api/app/avatars"
            | "/api/app/optimization/plan"
            | "/api/app/optimization/apply-request"
            | "/api/app/outfit-imports/plan"
            | "/api/app/outfit-imports/request"
            | "/api/app/package-install/request"
            | "/api/avatar-encryption/plan"
            | "/api/avatar-encryption/apply-request"
    ) {
        return true;
    }
    if method == "GET"
        && matches!(
            route_path,
            "/api/health"
                | "/api/app/bootstrap"
                | "/api/app/workspace/diff"
                | "/api/app/doctor"
                | "/api/app/diagnostics"
                | "/api/app/projects/prefs"
        )
    {
        return true;
    }
    if method == "POST"
        && matches!(
            route_path,
            "/api/projects/refresh"
                | "/api/app/unity/readiness/refresh"
                | "/api/app/doctor/unity-mcp/repair"
                | "/api/app/diagnostics"
                | "/api/app/support-bundle"
                | "/api/app/projects/prefs"
                | "/api/app/project-index/scan"
        )
    {
        return true;
    }
    if route_path == "/api/app/adjustment-checkpoints"
        || route_path.starts_with("/api/app/adjustment-checkpoints/")
        || route_path == "/api/app/skill-packages"
        || route_path.starts_with("/api/app/skill-packages/")
        || route_path == "/api/app/path-to-skill"
        || route_path.starts_with("/api/app/path-to-skill/")
        || route_path == "/api/app/skills"
        || route_path.starts_with("/api/app/skills/")
        || route_path == "/api/app/sub-agents"
        || route_path.starts_with("/api/app/sub-agents/")
        || route_path == "/api/app/agent/runs"
        || route_path.starts_with("/api/app/agent/runs/")
        || route_path == "/api/app/agent/approvals"
        || route_path.starts_with("/api/app/agent/approvals/")
        || route_path == "/api/app/agent/desktop-actions"
        || route_path.starts_with("/api/app/agent/desktop-actions/")
        || route_path == "/api/app/agent/goals"
        || route_path.starts_with("/api/app/agent/goals/")
        || route_path == "/api/app/agent/memory"
        || route_path.starts_with("/api/app/agent/memory/")
        || route_path == "/api/app/agent/compact"
        || route_path == "/api/app/agent-notes"
        || route_path.starts_with("/api/app/agent-notes/")
        || route_path == "/api/app/chats"
        || route_path.starts_with("/api/app/chats/")
        || route_path == "/api/app/optimization/proofs"
        || route_path.starts_with("/api/app/optimization/proofs/")
    {
        return true;
    }
    if route_path == "/api/config" && matches!(method, "GET" | "POST") {
        return true;
    }
    if method == "POST"
        && matches!(
            route_path,
            "/api/config/vision" | "/api/models" | "/api/app/provider/test"
        )
    {
        return true;
    }
    if method == "POST" && route_path == "/api/app/permission" {
        return true;
    }
    if route_path == "/api/app/external-agent/connectors" && matches!(method, "GET" | "POST") {
        return true;
    }
    if method == "POST"
        && matches!(
            route_path,
            "/api/app/external-agent/gateway"
                | "/api/app/external-agent/connectors/install"
                | "/api/app/external-agent/connectors/uninstall"
        )
    {
        return true;
    }
    if method == "GET" && route_path == "/api/app/runtime/snapshot" {
        return true;
    }
    if method == "POST"
        && matches!(
            route_path,
            "/api/app/agent/message" | "/api/app/agent/runs/cancel" | "/api/app/agent/runs/queue"
        )
    {
        return true;
    }
    if method == "POST"
        && route_path.starts_with("/api/app/agent/approvals/")
        && (route_path.ends_with("/approve")
            || route_path.ends_with("/reject")
            || route_path.ends_with("/revision"))
    {
        return true;
    }
    if method == "GET" && matches!(route_path, "/api/app/checkpoints" | "/api/app/recoveries") {
        return true;
    }
    if method == "POST"
        && route_path.starts_with("/api/app/checkpoints/")
        && (route_path.ends_with("/preview") || route_path.ends_with("/restore"))
    {
        return true;
    }
    if method == "POST"
        && route_path.starts_with("/api/app/recoveries/")
        && (route_path.ends_with("/preview")
            || route_path.ends_with("/restore")
            || route_path.ends_with("/resolve")
            || route_path.ends_with("/incident-bundle"))
    {
        return true;
    }
    false
}

fn ensure_backend_session_verified(token: &str) -> Result<(), String> {
    if existing_backend_accepts_session(token) {
        Ok(())
    } else {
        Err(
            "VRCForge runtime session verification failed before an internal IPC request. Restart VRCForge if the local runtime was replaced.".to_string(),
        )
    }
}

fn percent_decode_route(route: &str) -> Result<String, String> {
    let bytes = route.as_bytes();
    let mut output = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' {
            if index + 2 >= bytes.len() {
                return Err("desktop IPC bridge rejected an invalid encoded API path".to_string());
            }
            let high = hex_nibble(bytes[index + 1]).ok_or_else(|| {
                "desktop IPC bridge rejected an invalid encoded API path".to_string()
            })?;
            let low = hex_nibble(bytes[index + 2]).ok_or_else(|| {
                "desktop IPC bridge rejected an invalid encoded API path".to_string()
            })?;
            output.push((high << 4) | low);
            index += 3;
        } else {
            output.push(bytes[index]);
            index += 1;
        }
    }
    String::from_utf8(output)
        .map_err(|_| "desktop IPC bridge rejected a non-UTF8 API path".to_string())
}

fn percent_encode_query_component(value: &str) -> String {
    let mut encoded = String::new();
    for byte in value.as_bytes() {
        match *byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                encoded.push(*byte as char)
            }
            _ => encoded.push_str(&format!("%{byte:02X}")),
        }
    }
    encoded
}

fn app_api_response_from_ureq(
    result: Result<ureq::Response, ureq::Error>,
) -> Result<AppApiResponse, String> {
    match result {
        Ok(response) => app_api_response_from_parts(response.status(), response.into_string()),
        Err(ureq::Error::Status(status, response)) => {
            app_api_response_from_parts(status, response.into_string())
        }
        Err(ureq::Error::Transport(error)) => Err(format!(
            "VRCForge runtime is not reachable at {BACKEND_ENDPOINT}: {error}"
        )),
    }
}

fn app_api_response_from_parts(
    status: u16,
    body_result: Result<String, std::io::Error>,
) -> Result<AppApiResponse, String> {
    let text = body_result.map_err(|error| format!("unable to read runtime response: {error}"))?;
    let (body, ok) = if text.trim().is_empty() {
        (serde_json::json!({}), (200..300).contains(&status))
    } else {
        match serde_json::from_str(&text) {
            Ok(payload) => (payload, (200..300).contains(&status)),
            Err(_) => (
                serde_json::json!({
                    "detail": format!("HTTP {status}: response was not JSON"),
                    "excerpt": text.chars().take(300).collect::<String>(),
                }),
                false,
            ),
        }
    };
    Ok(AppApiResponse { status, ok, body })
}

fn decode_hmac_sha256_hex(value: &str) -> Option<[u8; 32]> {
    let bytes = value.as_bytes();
    if bytes.len() != 64 {
        return None;
    }
    let mut out = [0u8; 32];
    for (index, chunk) in bytes.chunks_exact(2).enumerate() {
        let high = hex_nibble(chunk[0])?;
        let low = hex_nibble(chunk[1])?;
        out[index] = (high << 4) | low;
    }
    Some(out)
}

fn hex_nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn backend_port_open() -> bool {
    let addrs = (BACKEND_HOST, BACKEND_PORT).to_socket_addrs();
    let Ok(mut addrs) = addrs else {
        return false;
    };
    if let Some(addr) = addrs.next() {
        TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok()
    } else {
        false
    }
}

fn existing_backend_accepts_session(token: &str) -> bool {
    let Ok(nonce) = generate_session_token() else {
        return false;
    };
    // Plain-HTTP probe via `ureq` (sync, no tokio/reqwest, no startup wait).
    // Fail-fast semantics match the old hand-rolled TcpStream client: short
    // timeouts, no retries, and any non-200 status or malformed body means
    // "this runtime does not accept our session".
    let agent = ureq::builder()
        .timeout_connect(Duration::from_millis(350))
        .timeout(Duration::from_millis(1500))
        .redirects(0)
        .build();
    let Ok(response) = agent
        .get(&format!("{BACKEND_ENDPOINT}/api/app/session-challenge"))
        .set("Origin", "tauri://localhost")
        .query("nonce", &nonce)
        .call()
    else {
        return false;
    };
    let Ok(payload) = response.into_json::<serde_json::Value>() else {
        return false;
    };
    let Some(signature) = extract_challenge_signature(&payload) else {
        return false;
    };
    app_session_challenge_signature_matches(token, &nonce, &signature)
}

fn extract_challenge_signature(payload: &serde_json::Value) -> Option<String> {
    payload
        .get("signature")
        .and_then(|value| value.as_str())
        .map(str::to_string)
}

fn wait_for_backend(timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if backend_port_open() {
            return true;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState {
            child: Mutex::new(None),
            event_bridge_started: Mutex::new(false),
        })
        .setup(|app| {
            let show_item = MenuItem::with_id(app, "show", "显示 VRCForge", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出 VRCForge", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &quit_item])?;
            let mut tray = TrayIconBuilder::new()
                .tooltip("VRCForge")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main_window(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            app_api_request,
            apply_adjustment_checkpoint,
            approve_agent_approval,
            backend_endpoint,
            block_skill_package,
            cancel_sub_agent,
            check_skills,
            clear_agent_memory,
            compact_agent_history,
            create_adjustment_checkpoint,
            create_agent_goal,
            create_agent_memory,
            create_skill,
            create_sub_agent,
            delete_adjustment_checkpoint,
            delete_agent_memory,
            delete_skill,
            desktop_runtime_snapshot,
            export_interrupted_apply_incident_bundle,
            export_skill_package,
            export_support_bundle,
            fetch_agent_approvals,
            fetch_agent_desktop_actions,
            fetch_agent_goals,
            fetch_agent_memory,
            fetch_agent_notes,
            fetch_agent_runs,
            fetch_adjustment_checkpoints,
            fetch_app_bootstrap,
            fetch_app_health,
            fetch_avatars,
            fetch_chats,
            fetch_diagnostics,
            fetch_doctor,
            fetch_external_agent_connectors,
            fetch_optimization_proof,
            fetch_optimization_proofs,
            fetch_optimization_plan,
            fetch_provider_models,
            fetch_checkpoints,
            fetch_interrupted_apply_recoveries,
            fetch_project_prefs,
            fetch_skill_packages,
            fetch_skills,
            fetch_sub_agent,
            fetch_sub_agents,
            fetch_workspace_diff,
            import_skill_package,
            install_external_agent_connector,
            overwrite_adjustment_checkpoint,
            plan_avatar_encryption,
            plan_outfit_import,
            preflight_skill_package,
            preview_path_to_skill,
            preview_interrupted_apply_recovery,
            preview_adjustment_checkpoint,
            preview_restore_checkpoint,
            record_agent_run_queued,
            reject_agent_approval,
            refresh_projects,
            refresh_unity_readiness,
            repair_unity_mcp_bridge,
            request_avatar_encryption_apply,
            request_agent_desktop_action,
            request_agent_run_cancel,
            request_approval_revision,
            request_optimization_apply,
            request_outfit_import,
            request_package_install,
            request_restore_checkpoint,
            request_restore_interrupted_apply_recovery,
            resolve_interrupted_apply_recovery,
            retry_sub_agent,
            revoke_skill_package_signer,
            save_agent_notes,
            save_chats,
            select_adjustment_checkpoint,
            set_skill_package_enabled,
            set_skill_package_safe_mode,
            send_agent_message,
            test_provider_capability,
            trust_skill_package_signer,
            uninstall_skill_package,
            uninstall_external_agent_connector,
            update_adjustment_checkpoint,
            update_agent_goal,
            update_api_config,
            update_diagnostics,
            update_external_agent_gateway,
            update_permission_mode,
            update_vision_config,
            update_skill,
            save_project_prefs,
            scan_project_index,
            start_backend,
            stop_backend,
            write_path_to_skill,
            ensure_agent_notes_file,
            open_folder,
            open_local_folder,
            select_folder
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running VRCForge");
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

#[cfg(test)]
mod tests {
    use super::{
        app_session_challenge_signature, app_session_challenge_signature_matches,
        extract_challenge_signature, hmac_sha256_hex, normalize_app_api_path,
        percent_encode_query_component, prepare_runtime_files, provider_config_body,
        sanitize_backend_event, sanitize_text_for_webview, sanitize_webview_response,
        try_ensure_agent_notes_file, validate_local_folder_to_open,
        validate_project_folder_to_open, webview_error_message, DESKTOP_AGENT_MESSAGE_TIMEOUT_MS,
    };
    use std::{
        env, fs,
        path::{Path, PathBuf},
        process,
        time::{SystemTime, UNIX_EPOCH},
    };

    fn test_dir(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock should be after Unix epoch")
            .as_nanos();
        env::temp_dir().join(format!("vrcforge-{label}-{}-{nonce}", process::id()))
    }

    fn create_dashboard(root: &Path) {
        let dashboard = root.join("dashboard");
        fs::create_dir_all(&dashboard).expect("dashboard directory should be created");
        fs::write(dashboard.join("index.html"), "test").expect("dashboard index should be created");
    }

    #[test]
    fn agent_notes_creation_creates_missing_parent_directories() {
        let base = test_dir("agent-notes-parent");
        let user_data = base.join("missing").join("nested");

        let path = try_ensure_agent_notes_file(&user_data)
            .expect("optional AGENTS.md should be created when its parent is missing");

        assert_eq!(path, user_data.join("AGENTS.md"));
        assert!(path.is_file());
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn runtime_preparation_ignores_optional_agent_notes_failure() {
        let base = test_dir("optional-agent-notes");
        let root = base.join("app");
        let user_data = base.join("user-data");
        create_dashboard(&root);
        fs::create_dir_all(user_data.join("AGENTS.md"))
            .expect("conflicting AGENTS.md directory should be created");

        let result = prepare_runtime_files(&root, &user_data);

        assert!(result.is_ok(), "optional AGENTS.md failure blocked runtime");
        assert!(user_data.join("config").join("settings.json").is_file());
        assert!(user_data.join("logs").is_dir());
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn runtime_preparation_reports_required_directory_failure() {
        let base = test_dir("required-runtime-dir");
        let root = base.join("app");
        let user_data = base.join("user-data");
        create_dashboard(&root);
        fs::create_dir_all(&user_data).expect("user data directory should be created");
        let logs_path = user_data.join("logs");
        fs::write(&logs_path, "not a directory").expect("logs conflict should be created");

        let error = prepare_runtime_files(&root, &user_data)
            .expect_err("required logs directory failure must block runtime");

        assert!(error.contains(&logs_path.display().to_string()));
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn hmac_sha256_matches_known_vector() {
        // RFC-style known vector: proves the RustCrypto-backed implementation
        // is byte-identical to the previous hand-rolled HMAC.
        assert_eq!(
            hmac_sha256_hex(b"key", b"The quick brown fox jumps over the lazy dog"),
            "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8",
        );
        assert_ne!(
            app_session_challenge_signature("session-token", "nonce-value"),
            app_session_challenge_signature("session-token", "other-nonce"),
        );
    }

    #[test]
    fn session_challenge_signature_match_uses_valid_hex_hmac() {
        let signature = app_session_challenge_signature("session-token", "nonce-value");

        assert!(app_session_challenge_signature_matches(
            "session-token",
            "nonce-value",
            &signature,
        ));
        assert!(app_session_challenge_signature_matches(
            "session-token",
            "nonce-value",
            &signature.to_uppercase(),
        ));
        assert!(!app_session_challenge_signature_matches(
            "session-token",
            "other-nonce",
            &signature,
        ));
        assert!(!app_session_challenge_signature_matches(
            "session-token",
            "nonce-value",
            "not-a-valid-hmac",
        ));
    }

    #[test]
    fn session_challenge_signature_extraction_requires_string_field() {
        assert_eq!(
            extract_challenge_signature(&serde_json::json!({"signature": "abc123"})).as_deref(),
            Some("abc123"),
        );
        assert!(extract_challenge_signature(&serde_json::json!({"signature": 42})).is_none());
        assert!(extract_challenge_signature(&serde_json::json!({})).is_none());
        assert!(extract_challenge_signature(&serde_json::json!(null)).is_none());
    }

    #[test]
    fn app_api_bridge_allows_api_paths_without_exposing_session_endpoints() {
        assert!(normalize_app_api_path("GET", "/api/health").is_err());
        assert!(normalize_app_api_path("GET", "http://127.0.0.1:8757/api/app/bootstrap").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/bootstrap?refreshProjects=true").is_err());
        assert!(normalize_app_api_path("POST", "/api/projects/refresh").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/unity/readiness/refresh").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/workspace/diff?root=D%3A%5CProj").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/doctor").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/doctor/unity-mcp/repair").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/diagnostics").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/diagnostics").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/support-bundle").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/projects/prefs").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/projects/prefs").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/project-index/scan").is_err());
        assert!(
            normalize_app_api_path("GET", "/api/app/adjustment-checkpoints?kind=face").is_err()
        );
        assert!(normalize_app_api_path("POST", "/api/app/adjustment-checkpoints").is_err());
        assert!(normalize_app_api_path("PUT", "/api/app/adjustment-checkpoints/a1").is_err());
        assert!(normalize_app_api_path("DELETE", "/api/app/adjustment-checkpoints/a1").is_err());
        assert!(
            normalize_app_api_path("POST", "/api/app/adjustment-checkpoints/a1/overwrite").is_err()
        );
        assert!(
            normalize_app_api_path("POST", "/api/app/adjustment-checkpoints/a1/select").is_err()
        );
        assert!(
            normalize_app_api_path("POST", "/api/app/adjustment-checkpoints/a1/apply").is_err()
        );
        assert!(
            normalize_app_api_path("POST", "/api/app/adjustment-checkpoints/a1/preview").is_err()
        );
        assert!(normalize_app_api_path("GET", "/api/app/skill-packages").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/skill-packages/import").is_err());
        assert!(normalize_app_api_path("PUT", "/api/app/skill-packages/p1").is_err());
        assert!(normalize_app_api_path("DELETE", "/api/app/skill-packages/p1").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/path-to-skill/preview").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/path-to-skill/write").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/skills").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/skills/check").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/skills").is_err());
        assert!(normalize_app_api_path("PUT", "/api/app/skills/s1").is_err());
        assert!(normalize_app_api_path("DELETE", "/api/app/skills/s1").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/avatars").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/optimization/plan").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/optimization/apply-request").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/outfit-imports/plan").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/outfit-imports/request").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/package-install/request").is_err());
        assert!(normalize_app_api_path("POST", "/api/avatar-encryption/plan").is_err());
        assert!(normalize_app_api_path("POST", "/api/avatar-encryption/apply-request").is_err());
        assert!(normalize_app_api_path("GET", "/api/avatar-encryption/plan").is_err());
        assert!(normalize_app_api_path("GET", "/api/avatar-encryption/apply-request").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/optimization/plan").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/optimization/proofs").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/optimization/proofs/r1").is_err());
        assert!(normalize_app_api_path("PUT", "/api/app/outfit-imports/request").is_err());
        assert!(normalize_app_api_path("DELETE", "/api/app/package-install/request").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/sub-agents").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/sub-agents").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/sub-agents/t1").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/sub-agents/t1/cancel").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/sub-agents/t1/retry").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/agent/runs").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/agent/approvals").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/agent/desktop-actions").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/desktop-actions").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/agent/goals").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/goals").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/goals/g1").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/agent/memory").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/memory").is_err());
        assert!(normalize_app_api_path("DELETE", "/api/app/agent/memory/m1").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/memory/clear").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/compact").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/agent-notes").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent-notes").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/chats?projectPath=D%3A%5CProj").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/chats").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/session").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/session-challenge").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/ws-ticket").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/%73ession").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/%2e%2e/health").is_err());
        assert!(normalize_app_api_path("GET", "/api/agent/manifest").is_err());
        assert!(normalize_app_api_path("GET", "/mcp").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/../health").is_err());
        assert!(normalize_app_api_path("GET", "/api/config").is_err());
        assert!(normalize_app_api_path("POST", "/api/config").is_err());
        assert!(normalize_app_api_path("POST", "/api/config/vision").is_err());
        assert!(normalize_app_api_path("POST", "/api/models").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/provider/test").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/permission").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/external-agent/connectors").is_err());
        assert!(normalize_app_api_path(
            "GET",
            "/api/app/external-agent/connectors?projectPath=D%3A%5CProj"
        )
        .is_err());
        assert!(normalize_app_api_path("POST", "/api/app/external-agent/connectors").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/external-agent/gateway").is_err());
        assert!(
            normalize_app_api_path("POST", "/api/app/external-agent/connectors/install").is_err()
        );
        assert!(
            normalize_app_api_path("POST", "/api/app/external-agent/connectors/uninstall").is_err()
        );
        assert!(normalize_app_api_path("GET", "/api/app/runtime/snapshot?sessionId=s1").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/message").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/runs/cancel").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/runs/queue").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/approvals/a1/approve").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/approvals/a1/reject").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/agent/approvals/a1/revision").is_err());
        assert!(
            normalize_app_api_path("GET", "/api/app/checkpoints?projectRoot=D%3A%5CProj").is_err()
        );
        assert!(normalize_app_api_path("POST", "/api/app/checkpoints/c1/preview").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/checkpoints/c1/restore").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/recoveries?includeResolved=true").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/recoveries/r1/preview").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/recoveries/r1/restore").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/recoveries/r1/resolve").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/recoveries/r1/incident-bundle").is_err());
        assert!(normalize_app_api_path("POST", "/api/blendshapes/apply").is_err());
        assert!(normalize_app_api_path("POST", "/api/clothes/apply-fx").is_err());
        assert!(normalize_app_api_path("POST", "/api/shader/apply").is_err());
        assert!(normalize_app_api_path("POST", "/api/projects/install").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/unlisted-future-route").is_err());
    }

    #[test]
    fn backend_event_sanitizer_only_forwards_safe_desktop_events() {
        let safe = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeRuns",
            "payload": {
                "runs": [],
                "secret": "should-not-reach-webview",
                "state": {"path": "C:\\Users\\Example\\AppData\\Local\\VRCForge"},
                "config": {"apiKey": "hidden"},
                "configPath": "C:\\Users\\Example\\AppData\\Local\\VRCForge\\settings.json",
                "logsDir": "C:\\Users\\Example\\AppData\\Local\\VRCForge\\logs"
            },
            "timestamp": 1.0
        }));
        assert_eq!(
            safe,
            Some(serde_json::json!({"type": "agentRuntimeRuns", "timestamp": 1.0}))
        );

        for event_type in ["config", "log", "state", "agentNotesUpdated"] {
            assert!(sanitize_backend_event(serde_json::json!({
                "type": event_type,
                "payload": {"secret": "should-not-reach-webview"}
            }))
            .is_none());
        }

        assert!(sanitize_backend_event(serde_json::json!({"payload": {}})).is_none());
    }

    #[test]
    fn agent_message_timeout_keeps_long_turns_usable() {
        assert!(DESKTOP_AGENT_MESSAGE_TIMEOUT_MS >= 600_000);
    }

    #[test]
    fn webview_response_sanitizer_removes_secrets_before_webview() {
        let sanitized = sanitize_webview_response(serde_json::json!({
            "configPath": "local-config-path",
            "apiConfig": {
                "provider": "gemini",
                "api_key": "main-secret",
                "apiKeyPresent": true,
                "Authorization": "Bearer main-secret"
            },
            "visionConfig": {
                "provider": "openai",
                "apiKey": "vision-secret",
                "apiKeyPresent": true
            },
            "lastConnectorAction": {
                "backupPath": "Q:/private/AppData/Local/VRCForge/backup.json"
            },
            "clientConfigs": {
                "claudeCode": {
                    "text": "{\"headers\":{\"Authorization\":\"Bearer ${CUSTOM_VRCFORGE_TOKEN}\"}}"
                }
            },
            "effective": {
                "model": "test-model",
                "message": "api_key=main-secret",
                "backupMarker": "backupPath=Q:/private/AppData/Local/VRCForge/backup.json",
                "markerText": "configPath=Q:/private/AppData/Local/VRCForge/config.json",
                "projectPath": "Q:/projects/avatar"
            }
        }));

        assert!(sanitized
            .get("apiConfig")
            .and_then(|value| value.get("Authorization"))
            .is_none());
        let text = sanitized.to_string();
        assert!(!text.contains("main-secret"));
        assert!(!text.contains("vision-secret"));
        assert!(!text.contains("configPath"));
        assert!(!text.contains("backupPath"));
        assert!(!text.contains("Q:/private"));
        assert!(text.contains("[redacted]"));
        assert!(text.contains("Q:/projects/avatar"));
        assert!(text.contains("apiKeyPresent"));
        assert!(text.contains("CUSTOM_VRCFORGE_TOKEN"));
        assert!(text.contains("Bearer"));
    }

    #[test]
    fn webview_text_sanitizer_redacts_error_details() {
        assert_eq!(
            sanitize_text_for_webview(
                "request failed with Authorization: Bearer real-secret and api_key=real-secret"
            ),
            "[redacted]"
        );
        assert_eq!(
            sanitize_text_for_webview("request failed with Bearer real-secret"),
            "[redacted]"
        );
        assert_eq!(
            sanitize_text_for_webview(
                "{\"headers\":{\"Authorization\":\"Bearer ${CUSTOM_VRCFORGE_TOKEN}\"}}"
            ),
            "{\"headers\":{\"Authorization\":\"Bearer ${CUSTOM_VRCFORGE_TOKEN}\"}}"
        );
        assert_eq!(
            webview_error_message(&serde_json::json!({
                "detail": {
                    "message": "Unity MCP bridge is offline",
                    "configPath": "Q:/private/AppData/Local/VRCForge/config.json"
                }
            })),
            "Unity MCP bridge is offline"
        );
        assert_eq!(
            webview_error_message(&serde_json::json!({
                "detail": {
                    "message": "request failed with Bearer real-secret"
                }
            })),
            "[redacted]"
        );
    }

    #[test]
    fn provider_config_body_preserves_omitted_key_semantics() {
        let without_key = provider_config_body(
            "gemini".to_string(),
            None,
            Some("https://example.test".to_string()),
            Some("model-a".to_string()),
        );
        assert!(without_key.get("api_key").is_none());

        let with_blank_key =
            provider_config_body("gemini".to_string(), Some(String::new()), None, None);
        assert_eq!(
            with_blank_key
                .get("api_key")
                .and_then(|value| value.as_str()),
            Some("")
        );
    }

    #[test]
    fn query_component_encoding_handles_windows_paths() {
        assert_eq!(
            percent_encode_query_component(r"D:\VR Projects\Karin FT"),
            "D%3A%5CVR%20Projects%5CKarin%20FT",
        );
        assert_eq!(
            percent_encode_query_component("session_123-abc"),
            "session_123-abc",
        );
    }

    #[test]
    fn open_folder_validation_rejects_empty_and_relative_paths() {
        assert!(validate_project_folder_to_open("").is_err());
        assert!(validate_project_folder_to_open("Documents").is_err());
    }

    #[test]
    fn open_folder_validation_requires_unity_project_root() {
        let base = test_dir("open-folder-validation");
        let not_project = base.join("not-project");
        fs::create_dir_all(&not_project).expect("test directory should be created");

        let error = validate_project_folder_to_open(&not_project.display().to_string())
            .expect_err("non-project folder must be rejected");
        assert!(error.contains("Not a Unity project root"));

        let project = base.join("unity-project");
        fs::create_dir_all(project.join("Assets")).expect("Assets directory should be created");
        fs::create_dir_all(project.join("Packages")).expect("Packages directory should be created");
        fs::create_dir_all(project.join("ProjectSettings"))
            .expect("ProjectSettings directory should be created");

        let resolved = validate_project_folder_to_open(&project.display().to_string())
            .expect("Unity project root should be accepted");
        assert!(resolved.ends_with("unity-project"));
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn local_folder_validation_accepts_non_project_directory() {
        let base = test_dir("local-folder-validation");
        let folder = base.join("archives");
        fs::create_dir_all(&folder).expect("archive directory should be created");

        let resolved = validate_local_folder_to_open(&folder.display().to_string())
            .expect("non-project archive directory should be accepted");

        assert_eq!(resolved, folder.canonicalize().unwrap());
        assert!(validate_local_folder_to_open("").is_err());
        assert!(validate_local_folder_to_open("relative\\archives").is_err());
        let _ = fs::remove_dir_all(base);
    }
}

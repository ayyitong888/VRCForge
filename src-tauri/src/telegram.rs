use crate::backend::{backend_json_request, blocking_backend_json_request};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

// User-clicked public help destination only. No credentials or arbitrary URL
// cross this boundary; the OS owns the browser after the existing launcher exits.
#[tauri::command]
pub fn telegram_open_botfather() -> Result<(), String> {
    crate::commands::open_validated_external_url("https://t.me/BotFather", "Telegram BotFather")
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct TelegramRequest {
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct TelegramConfigRequest {
    token: String,
    owner_id: u64,
    projects: Vec<String>,
    #[serde(default = "default_proxy_mode")]
    proxy_mode: String,
    #[serde(default)]
    proxy_url: String,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct TelegramStatus {
    enabled: bool,
    configured: bool,
    owner_id: Option<u64>,
    projects: Vec<String>,
    state: String,
    error_code: Option<String>,
    #[serde(default = "default_proxy_mode")]
    proxy_mode: String,
    #[serde(default)]
    proxy_url: String,
}

fn default_proxy_mode() -> String { "environment".to_string() }

#[tauri::command]
pub async fn telegram_test_connection(request: TelegramRequest) -> Result<Value, String> {
    blocking_backend_json_request(move || backend_json_request("POST",
        "/api/telegram/test-connection".to_string(), None,
        Some(request.timeout_ms.unwrap_or(20000).clamp(1000, 30000)))).await
}

fn parse_status(value: Value) -> Result<TelegramStatus, String> {
    serde_json::from_value(value).map_err(|_| "Telegram returned an invalid status.".to_string())
}

// Fixed routes use the existing app-session-authenticated backend transport.
// Each command owns one bounded request; no URL or auth header comes from the UI.
async fn status_request(
    method: &'static str,
    path: &'static str,
    timeout_ms: Option<u64>,
) -> Result<TelegramStatus, String> {
    let value = blocking_backend_json_request(move || {
        backend_json_request(
            method,
            path.to_string(),
            None,
            Some(timeout_ms.unwrap_or(30000).clamp(1000, 30000)),
        )
    })
    .await?;
    parse_status(value)
}

#[tauri::command]
pub async fn telegram_status(request: TelegramRequest) -> Result<TelegramStatus, String> {
    status_request("GET", "/api/telegram/status", request.timeout_ms).await
}

#[tauri::command]
pub async fn telegram_start(request: TelegramRequest) -> Result<TelegramStatus, String> {
    status_request("POST", "/api/telegram/start", request.timeout_ms).await
}

#[tauri::command]
pub async fn telegram_stop(request: TelegramRequest) -> Result<TelegramStatus, String> {
    status_request("POST", "/api/telegram/stop", request.timeout_ms).await
}

#[tauri::command]
pub async fn telegram_configure(request: TelegramConfigRequest) -> Result<TelegramStatus, String> {
    if request.owner_id == 0 || request.owner_id > 9007199254740991 || request.projects.len() > 32 {
        return Err("A valid Telegram owner and project allowlist are required.".to_string());
    }
    let value = blocking_backend_json_request(move || {
        backend_json_request(
            "PUT",
            "/api/telegram/config".to_string(),
            Some(json!({
                "token": request.token,
                "ownerId": request.owner_id,
                "projects": request.projects,
                "proxyMode": request.proxy_mode,
                "proxyUrl": request.proxy_url,
            })),
            Some(request.timeout_ms.unwrap_or(30000).clamp(1000, 30000)),
        )
    })
    .await?;
    parse_status(value)
}

#![allow(unused_imports)]

use crate::commands::*;
use crate::event_bridge::*;
use crate::sanitize::*;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
#[cfg(windows)]
use std::os::windows::io::AsRawHandle;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::{
    env, fs,
    net::{TcpStream, ToSocketAddrs},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Mutex, OnceLock},
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
#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    },
};

pub(crate) const BACKEND_HOST: &str = "127.0.0.1";
pub(crate) const BACKEND_PORT: u16 = 8757;
pub(crate) const BACKEND_ENDPOINT: &str = "http://127.0.0.1:8757";
pub(crate) const BACKEND_START_BACKGROUND_WAIT_SECONDS: u64 = 18;
pub(crate) const DESKTOP_AGENT_MESSAGE_TIMEOUT_MS: u64 = 600_000;
pub(crate) const BACKEND_REQUEST_MAX_TIMEOUT_MS: u64 = 1_200_000;
pub(crate) const BACKEND_SESSION_VERIFY_WAIT: Duration = Duration::from_secs(5);
pub(crate) const BACKEND_SESSION_VERIFY_CACHE_TTL: Duration = Duration::from_secs(15);
pub(crate) const BACKEND_GRACEFUL_SHUTDOWN_METHOD: &str = "POST";
pub(crate) const BACKEND_GRACEFUL_SHUTDOWN_PATH: &str = "/api/app/runtime/shutdown";
pub(crate) const BACKEND_GRACEFUL_REQUEST_TIMEOUT: Duration = Duration::from_millis(1500);
pub(crate) const BACKEND_GRACEFUL_EXIT_WAIT: Duration = Duration::from_secs(5);
pub(crate) const BACKEND_FORCE_EXIT_WAIT: Duration = Duration::from_secs(2);
#[cfg(windows)]
pub(crate) const CREATE_NO_WINDOW: u32 = 0x08000000;

static BACKEND_SESSION_VERIFIED_UNTIL: OnceLock<Mutex<Option<Instant>>> = OnceLock::new();

pub(crate) fn bounded_backend_request_timeout(
    timeout_ms: Option<u64>,
    default_ms: u64,
) -> Duration {
    Duration::from_millis(
        timeout_ms
            .unwrap_or(default_ms)
            .clamp(1_000, BACKEND_REQUEST_MAX_TIMEOUT_MS),
    )
}

pub(crate) struct BackendState {
    pub(crate) child: Mutex<Option<Child>>,
    pub(crate) app_session_token: Mutex<Option<String>>,
    #[cfg(windows)]
    pub(crate) job: Mutex<Option<BackendJob>>,
    pub(crate) event_bridge_started: Mutex<bool>,
    pub(crate) start_in_progress: Mutex<bool>,
}

impl BackendState {
    pub(crate) fn new() -> Self {
        Self {
            child: Mutex::new(None),
            app_session_token: Mutex::new(None),
            #[cfg(windows)]
            job: Mutex::new(None),
            event_bridge_started: Mutex::new(false),
            start_in_progress: Mutex::new(false),
        }
    }
}

#[cfg(windows)]
pub(crate) struct BackendJob {
    handle: HANDLE,
}

#[cfg(windows)]
unsafe impl Send for BackendJob {}

#[cfg(windows)]
impl BackendJob {
    fn assign(child: &Child) -> Result<Self, String> {
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err(format!(
                    "unable to create backend job object: {}",
                    std::io::Error::last_os_error()
                ));
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags =
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
            if SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            ) == 0
            {
                let error = std::io::Error::last_os_error();
                CloseHandle(handle);
                return Err(format!("unable to configure backend job object: {error}"));
            }
            if AssignProcessToJobObject(handle, child.as_raw_handle() as HANDLE) == 0 {
                let error = std::io::Error::last_os_error();
                CloseHandle(handle);
                return Err(format!("unable to assign backend to job object: {error}"));
            }
            Ok(Self { handle })
        }
    }
}

#[cfg(windows)]
impl Drop for BackendJob {
    fn drop(&mut self) {
        unsafe {
            if !self.handle.is_null() {
                CloseHandle(self.handle);
                self.handle = std::ptr::null_mut();
            }
        }
    }
}

impl Drop for BackendState {
    fn drop(&mut self) {
        stop_managed_backend_child(self);
    }
}

#[derive(Serialize)]
pub(crate) struct BackendStartResult {
    endpoint: String,
    started: bool,
    already_running: bool,
    mode: String,
    message: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct AppApiResponse {
    status: u16,
    ok: bool,
    body: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct BackendJsonErrorEnvelope {
    error_type: &'static str,
    status: u16,
    detail: String,
}

impl BackendJsonErrorEnvelope {
    pub(crate) fn transport(detail: impl AsRef<str>) -> Self {
        Self {
            error_type: "backendJsonError",
            status: 0,
            detail: bounded_backend_error_detail(detail.as_ref()),
        }
    }
}

pub(crate) fn backend_json_error_from_response(
    status: u16,
    body: &serde_json::Value,
) -> BackendJsonErrorEnvelope {
    BackendJsonErrorEnvelope {
        error_type: "backendJsonError",
        status,
        detail: bounded_backend_error_detail(&webview_error_message(body)),
    }
}

pub(crate) fn bounded_backend_error_detail(detail: &str) -> String {
    let sanitized = sanitize_text_for_webview(detail.trim());
    let lower = sanitized.to_ascii_lowercase();
    let bytes = sanitized.as_bytes();
    let has_drive_path = bytes.windows(3).any(|window| {
        window[0].is_ascii_alphabetic() && window[1] == b':' && matches!(window[2], b'\\' | b'/')
    });
    let sensitive_markers = [
        "sk-",
        "client_secret",
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "bearer ",
        "password=",
        "password:",
        "\"password\"",
        "credential=",
        "credential:",
        "\"credential\"",
        "token=",
        "token:",
        "\"token\"",
        "secret=",
        "secret:",
        "\"secret\"",
        "session=",
        "session_id",
        "sessionid",
        "cookie=",
        "\"cookie\"",
    ];
    if sanitized == "[redacted]"
        || has_drive_path
        || sanitized.starts_with("\\\\")
        || lower.contains("\\users\\")
        || lower.contains("/users/")
        || lower.contains("/home/")
        || sensitive_markers
            .iter()
            .any(|marker| lower.contains(marker))
    {
        return "[redacted]".to_string();
    }
    let bounded = sanitized.chars().take(500).collect::<String>();
    if bounded.is_empty() {
        "VRCForge runtime request failed.".to_string()
    } else {
        bounded
    }
}

pub(crate) fn backend_json_request(
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
    let transport_proof = tauri_ipc_bridge_proof(&app_session_token, method, &path);
    let send_once = || {
        let http_request = agent
            .request(method, &url)
            .set("Accept", "application/json")
            .set("Origin", "tauri://localhost")
            .set("X-VRCForge-Transport", "tauri-ipc-bridge")
            .set("X-VRCForge-Transport-Proof", &transport_proof)
            .set("Authorization", &format!("Bearer {app_session_token}"));
        let response = if let Some(body) = body.as_ref() {
            http_request
                .set("Content-Type", "application/json")
                .send_string(&body.to_string())
        } else {
            http_request.call()
        };
        app_api_response_from_ureq(response)
    };
    let mut response = send_once()?;
    if matches!(response.status, 401 | 403) {
        clear_backend_session_verify_cache();
        if wait_for_backend_session(&app_session_token, BACKEND_SESSION_VERIFY_WAIT) {
            mark_backend_session_verified();
            response = send_once()?;
            if matches!(response.status, 401 | 403) {
                clear_backend_session_verify_cache();
            }
        } else {
            return Err(runtime_session_verification_error());
        }
    }
    if response.ok {
        Ok(response.body)
    } else {
        Err(webview_error_message(&response.body))
    }
}

pub(crate) async fn blocking_backend_json_request<F>(
    operation: F,
) -> Result<serde_json::Value, String>
where
    F: FnOnce() -> Result<serde_json::Value, String> + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(operation)
        .await
        .map_err(|error| format!("VRCForge runtime worker failed: {error}"))?
}

/// Status-preserving variant used only by typed IPC surfaces that need to
/// distinguish a stale revision or a missing resource from transport failure.
/// The error envelope contains a bounded, sanitized `detail` string rather
/// than the backend response body.
pub(crate) fn backend_json_request_with_error_envelope(
    method: &str,
    path: String,
    body: Option<serde_json::Value>,
    timeout_ms: Option<u64>,
) -> Result<serde_json::Value, BackendJsonErrorEnvelope> {
    let user_data = user_data_dir().map_err(BackendJsonErrorEnvelope::transport)?;
    let app_session_token =
        ensure_app_session_token(&user_data).map_err(BackendJsonErrorEnvelope::transport)?;
    ensure_backend_session_verified(&app_session_token)
        .map_err(BackendJsonErrorEnvelope::transport)?;
    let timeout = bounded_backend_request_timeout(timeout_ms, 30_000);
    let agent = ureq::builder()
        .timeout_connect(Duration::from_secs(2))
        .timeout(timeout)
        .redirects(0)
        .build();
    let url = format!("{BACKEND_ENDPOINT}{path}");
    let transport_proof = tauri_ipc_bridge_proof(&app_session_token, method, &path);
    let send_once = || {
        let http_request = agent
            .request(method, &url)
            .set("Accept", "application/json")
            .set("Origin", "tauri://localhost")
            .set("X-VRCForge-Transport", "tauri-ipc-bridge")
            .set("X-VRCForge-Transport-Proof", &transport_proof)
            .set("Authorization", &format!("Bearer {app_session_token}"));
        let response = if let Some(body) = body.as_ref() {
            http_request
                .set("Content-Type", "application/json")
                .send_string(&body.to_string())
        } else {
            http_request.call()
        };
        app_api_response_from_ureq(response).map_err(BackendJsonErrorEnvelope::transport)
    };
    let mut response = send_once()?;
    if matches!(response.status, 401 | 403) {
        clear_backend_session_verify_cache();
        if wait_for_backend_session(&app_session_token, BACKEND_SESSION_VERIFY_WAIT) {
            mark_backend_session_verified();
            response = send_once()?;
            if matches!(response.status, 401 | 403) {
                clear_backend_session_verify_cache();
            }
        } else {
            return Err(BackendJsonErrorEnvelope::transport(
                runtime_session_verification_error(),
            ));
        }
    }
    if response.ok {
        Ok(response.body)
    } else {
        Err(backend_json_error_from_response(
            response.status,
            &response.body,
        ))
    }
}

pub(crate) async fn blocking_backend_json_request_with_error_envelope<F>(
    operation: F,
) -> Result<serde_json::Value, BackendJsonErrorEnvelope>
where
    F: FnOnce() -> Result<serde_json::Value, BackendJsonErrorEnvelope> + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(operation)
        .await
        .map_err(|error| {
            BackendJsonErrorEnvelope::transport(format!("VRCForge runtime worker failed: {error}"))
        })?
}

#[cfg(test)]
mod memory_review_backend_error_tests {
    use super::{
        backend_json_error_from_response, bounded_backend_error_detail,
        bounded_backend_request_timeout, BackendJsonErrorEnvelope,
    };
    use std::time::Duration;

    #[test]
    fn typed_backend_transport_preserves_full_review_timeout() {
        assert_eq!(
            bounded_backend_request_timeout(Some(1_200_000), 30_000),
            Duration::from_millis(1_200_000)
        );
        assert_eq!(
            bounded_backend_request_timeout(Some(9_999_999), 30_000),
            Duration::from_millis(1_200_000)
        );
    }

    #[test]
    fn http_error_envelope_keeps_status_and_only_safe_detail() {
        let body = serde_json::json!({
            "detail": "stale revision",
            "response": "must-not-cross-ipc"
        });
        let envelope = backend_json_error_from_response(409, &body);
        let serialized = serde_json::to_value(&envelope).expect("error envelope should serialize");
        assert_eq!(serialized["errorType"], "backendJsonError");
        assert_eq!(serialized["status"], 409);
        assert_eq!(serialized["detail"], "stale revision");
        assert!(!serialized.to_string().contains("must-not-cross-ipc"));
    }

    #[test]
    fn transport_error_is_status_zero_bounded_and_sanitized() {
        let envelope = BackendJsonErrorEnvelope::transport(
            "Authorization: Bearer private-value that must not cross IPC",
        );
        let serialized = serde_json::to_value(&envelope).expect("error envelope should serialize");
        assert_eq!(serialized["status"], 0);
        assert_eq!(serialized["detail"], "[redacted]");

        let bare_secret_value = ["sk", "-test-value"].concat();
        let bare_secret = BackendJsonErrorEnvelope::transport(format!(
            "request rejected for {bare_secret_value}"
        ));
        let bare_secret =
            serde_json::to_value(&bare_secret).expect("secret error envelope should serialize");
        assert_eq!(bare_secret["detail"], "[redacted]");

        for sensitive in [
            r"failed while reading D:\private\project\config.json",
            "request failed at https://example.invalid/path?token=private-value",
            "password=private-value",
            "credential: private-value",
            "session=private-value",
            "cookie=private-value",
        ] {
            assert_eq!(
                bounded_backend_error_detail(sensitive),
                "[redacted]",
                "sensitive error detail crossed the IPC boundary: {sensitive}"
            );
        }
        assert_eq!(
            bounded_backend_error_detail("Memory Review revision changed."),
            "Memory Review revision changed."
        );

        let long = "x".repeat(800);
        assert_eq!(bounded_backend_error_detail(&long).chars().count(), 500);
    }
}

/// Raw-body variant of `backend_json_request` for binary uploads (chat
/// attachment vault ingestion). Keeps the same session-token, transport-proof,
/// and 401/403 re-verify semantics; only the request body encoding differs.
pub(crate) fn backend_bytes_request(
    method: &str,
    path: String,
    body: &[u8],
    content_type: &str,
    timeout_ms: Option<u64>,
) -> Result<serde_json::Value, String> {
    let user_data = user_data_dir()?;
    let app_session_token = ensure_app_session_token(&user_data)?;
    ensure_backend_session_verified(&app_session_token)?;
    let timeout = Duration::from_millis(timeout_ms.unwrap_or(120_000).clamp(1_000, 600_000));
    let agent = ureq::builder()
        .timeout_connect(Duration::from_secs(2))
        .timeout(timeout)
        .redirects(0)
        .build();
    let url = format!("{BACKEND_ENDPOINT}{path}");
    let transport_proof = tauri_ipc_bridge_proof(&app_session_token, method, &path);
    let send_once = || {
        let response = agent
            .request(method, &url)
            .set("Accept", "application/json")
            .set("Origin", "tauri://localhost")
            .set("X-VRCForge-Transport", "tauri-ipc-bridge")
            .set("X-VRCForge-Transport-Proof", &transport_proof)
            .set("Authorization", &format!("Bearer {app_session_token}"))
            .set("Content-Type", content_type)
            .send_bytes(body);
        app_api_response_from_ureq(response)
    };
    let mut response = send_once()?;
    if matches!(response.status, 401 | 403) {
        clear_backend_session_verify_cache();
        if wait_for_backend_session(&app_session_token, BACKEND_SESSION_VERIFY_WAIT) {
            mark_backend_session_verified();
            response = send_once()?;
            if matches!(response.status, 401 | 403) {
                clear_backend_session_verify_cache();
            }
        } else {
            return Err(runtime_session_verification_error());
        }
    }
    if response.ok {
        Ok(response.body)
    } else {
        Err(webview_error_message(&response.body))
    }
}

#[tauri::command]
pub fn start_backend(
    app_handle: tauri::AppHandle,
    state: State<'_, BackendState>,
) -> Result<BackendStartResult, String> {
    let already_running = backend_port_open();
    let started_background = begin_backend_start(&state)?;
    if started_background {
        thread::spawn(move || run_backend_start_worker(app_handle));
    }
    Ok(BackendStartResult {
        endpoint: BACKEND_ENDPOINT.to_string(),
        started: started_background && !already_running,
        already_running,
        mode: "starting".to_string(),
        message: if started_background {
            "VRCForge runtime is starting in the background.".to_string()
        } else {
            "VRCForge runtime startup is already in progress.".to_string()
        },
    })
}

pub(crate) fn begin_backend_start(state: &BackendState) -> Result<bool, String> {
    let mut guard = state
        .start_in_progress
        .lock()
        .map_err(|_| "backend startup state lock poisoned".to_string())?;
    if *guard {
        return Ok(false);
    }
    *guard = true;
    Ok(true)
}

pub(crate) fn clear_backend_start_in_progress(app_handle: &tauri::AppHandle) {
    let state = app_handle.state::<BackendState>();
    if let Ok(mut guard) = state.start_in_progress.lock() {
        *guard = false;
    };
}

pub(crate) fn run_backend_start_worker(app_handle: tauri::AppHandle) {
    let payload = match start_backend_in_background(&app_handle) {
        Ok(payload) => payload,
        Err(error) => serde_json::json!({
            "ok": false,
            "status": "error",
            "error": error
        }),
    };
    let _ = app_handle.emit("vrcforge-backend-start-status", payload);
    clear_backend_start_in_progress(&app_handle);
}

pub(crate) fn start_backend_in_background(
    app_handle: &tauri::AppHandle,
) -> Result<serde_json::Value, String> {
    let user_data = user_data_dir()?;
    let app_session_token = ensure_app_session_token(&user_data)?;
    if backend_port_open() {
        if !existing_backend_accepts_session(&app_session_token) {
            return Err(
                "Port 8757 is already used by a VRCForge runtime that does not accept this desktop session. Close all VRCForge.exe processes in Task Manager and launch VRCForge again.".to_string()
            );
        }
        mark_backend_session_verified();
        let state = app_handle.state::<BackendState>();
        start_backend_event_bridge_once(app_handle.clone(), &state, app_session_token)?;
        return Ok(serde_json::json!({
            "ok": true,
            "status": "ready",
            "mode": "existing"
        }));
    }

    let root = repo_root()?;
    prepare_runtime_files(&root, &user_data)?;
    let log_dir = user_data.join("logs");

    let mut command = backend_command(&root)?;
    let capture_helper = env::current_exe().map_err(|error| {
        format!("unable to resolve the VRCForge capture helper executable: {error}")
    })?;
    command
        .current_dir(&root)
        .env("VRCFORGE_APP_DIR", &root)
        .env("VRCFORGE_USER_DATA_DIR", &user_data)
        .env("VRCFORGE_CONFIG_DIR", user_data.join("config"))
        .env("VRCFORGE_LOG_DIR", user_data.join("logs"))
        .env("VRCFORGE_ARTIFACTS_DIR", user_data.join("artifacts"))
        .env("VRCFORGE_DASHBOARD_DIR", root.join("dashboard"))
        .env("VRCFORGE_DESKTOP_VERSION", env!("CARGO_PKG_VERSION"))
        .env(
            "VRCFORGE_SETTINGS_PATH",
            user_data.join("config").join("settings.json"),
        )
        .env("VRCFORGE_APP_SESSION_TOKEN", &app_session_token)
        .env("VRCFORGE_CAPTURE_HELPER", &capture_helper)
        .arg("--host")
        .arg(BACKEND_HOST)
        .arg("--port")
        .arg(BACKEND_PORT.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动本地 runtime: {error}"))?;

    #[cfg(windows)]
    let backend_job = match BackendJob::assign(&child) {
        Ok(job) => job,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };

    let state = app_handle.state::<BackendState>();
    {
        // Keep the child lock until all managed-only shutdown state is
        // installed. Shutdown takes this lock first, so it cannot observe a
        // child without the matching token and Windows Job Object.
        let mut child_guard = state
            .child
            .lock()
            .map_err(|_| "backend state lock poisoned".to_string())?;
        let mut token_guard = state
            .app_session_token
            .lock()
            .map_err(|_| "backend session-token state lock poisoned".to_string())?;
        #[cfg(windows)]
        let mut job_guard = state
            .job
            .lock()
            .map_err(|_| "backend job state lock poisoned".to_string())?;
        *token_guard = Some(app_session_token.clone());
        #[cfg(windows)]
        {
            *job_guard = Some(backend_job);
        }
        *child_guard = Some(child);
    }

    start_backend_event_bridge_once(app_handle.clone(), &state, app_session_token.clone())?;
    let ready = wait_for_backend(Duration::from_secs(BACKEND_START_BACKGROUND_WAIT_SECONDS));

    if ready && wait_for_backend_session(&app_session_token, BACKEND_SESSION_VERIFY_WAIT) {
        mark_backend_session_verified();
        return Ok(serde_json::json!({
            "ok": true,
            "status": "ready",
            "mode": "managed"
        }));
    }
    Ok(serde_json::json!({
        "ok": false,
        "status": "timeout",
        "logDir": log_dir.display().to_string()
    }))
}

pub(crate) fn backend_command(root: &Path) -> Result<Command, String> {
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

pub(crate) fn prepare_runtime_files(root: &Path, user_data: &Path) -> Result<(), String> {
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
                "command": ["uvx", "--from", "mcpforunityserver", "unity-mcp"],
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

pub(crate) fn repo_root() -> Result<PathBuf, String> {
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

pub(crate) fn user_data_dir() -> Result<PathBuf, String> {
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

pub(crate) fn ensure_app_session_token(user_data: &Path) -> Result<String, String> {
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

pub(crate) fn generate_session_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| format!("Unable to generate app session token: {error}"))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

/// HMAC-SHA256 hex digest backed by the audited RustCrypto `hmac` crate
/// (replaces a hand-rolled ipad/opad implementation with identical output —
/// see the known-vector test below).
pub(crate) fn hmac_sha256_hex(key: &[u8], message: &[u8]) -> String {
    let mut mac =
        Hmac::<Sha256>::new_from_slice(key).expect("HMAC-SHA256 accepts keys of any length");
    mac.update(message);
    mac.finalize()
        .into_bytes()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

pub(crate) fn tauri_ipc_bridge_proof(token: &str, method: &str, path_and_query: &str) -> String {
    hmac_sha256_hex(
        token.as_bytes(),
        format!(
            "vrcforge.tauri-ipc-bridge.v1\n{}\n{}",
            method.to_ascii_uppercase(),
            path_and_query
        )
        .as_bytes(),
    )
}

/// Nonce-challenge design (kept intentionally): the shell generates a fresh
/// random nonce per probe, the backend signs "vrcforge.app-session.v1\n{nonce}"
/// with the shared app session token, and the shell recomputes the signature
/// locally to compare. The token itself never travels over the wire, and a
/// captured response cannot be replayed against a different nonce.
#[cfg(test)]
pub(crate) fn app_session_challenge_signature(token: &str, nonce: &str) -> String {
    hmac_sha256_hex(
        token.as_bytes(),
        format!("vrcforge.app-session.v1\n{nonce}").as_bytes(),
    )
}

pub(crate) fn app_session_challenge_signature_matches(
    token: &str,
    nonce: &str,
    signature: &str,
) -> bool {
    let Some(signature_bytes) = decode_hmac_sha256_hex(signature) else {
        return false;
    };
    let mut mac =
        Hmac::<Sha256>::new_from_slice(token.as_bytes()).expect("HMAC-SHA256 accepts any key");
    mac.update(format!("vrcforge.app-session.v1\n{nonce}").as_bytes());
    mac.verify_slice(&signature_bytes).is_ok()
}

pub(crate) fn ensure_backend_session_verified(token: &str) -> Result<(), String> {
    if !backend_port_open() {
        clear_backend_session_verify_cache();
        return Err("VRCForge runtime is still starting.".to_string());
    }
    if backend_session_verify_cache_valid() {
        return Ok(());
    }
    if wait_for_backend_session(token, BACKEND_SESSION_VERIFY_WAIT) {
        mark_backend_session_verified();
        Ok(())
    } else {
        clear_backend_session_verify_cache();
        Err(runtime_session_verification_error())
    }
}

pub(crate) fn runtime_session_verification_error() -> String {
    "VRCForge runtime session verification failed before an internal IPC request. Restart VRCForge if the local runtime was replaced.".to_string()
}

pub(crate) fn backend_session_verify_cache() -> &'static Mutex<Option<Instant>> {
    BACKEND_SESSION_VERIFIED_UNTIL.get_or_init(|| Mutex::new(None))
}

pub(crate) fn backend_session_verify_cache_valid() -> bool {
    let Ok(guard) = backend_session_verify_cache().lock() else {
        return false;
    };
    guard
        .as_ref()
        .is_some_and(|deadline| Instant::now() <= *deadline)
}

pub(crate) fn mark_backend_session_verified() {
    if let Ok(mut guard) = backend_session_verify_cache().lock() {
        *guard = Some(Instant::now() + BACKEND_SESSION_VERIFY_CACHE_TTL);
    }
}

pub(crate) fn clear_backend_session_verify_cache() {
    if let Ok(mut guard) = backend_session_verify_cache().lock() {
        *guard = None;
    }
}

/// Inverse of `percent_encode_query_component` for metadata smuggled through
/// ASCII-only invoke headers (chat attachment uploads). Returns `None` when the
/// escape sequences are malformed or the decoded bytes are not valid UTF-8.
pub(crate) fn percent_decode_utf8(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    let mut decoded: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if byte == b'%' {
            let hex = bytes.get(index + 1..index + 3)?;
            let text = std::str::from_utf8(hex).ok()?;
            decoded.push(u8::from_str_radix(text, 16).ok()?);
            index += 3;
        } else {
            decoded.push(byte);
            index += 1;
        }
    }
    String::from_utf8(decoded).ok()
}

pub(crate) fn percent_encode_query_component(value: &str) -> String {
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

pub(crate) fn app_api_response_from_ureq(
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

pub(crate) fn app_api_response_from_parts(
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

pub(crate) fn decode_hmac_sha256_hex(value: &str) -> Option<[u8; 32]> {
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

pub(crate) fn hex_nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

pub(crate) fn backend_port_open() -> bool {
    let addrs = (BACKEND_HOST, BACKEND_PORT).to_socket_addrs();
    let Ok(mut addrs) = addrs else {
        return false;
    };
    if let Some(addr) = addrs.next() {
        TcpStream::connect_timeout(&addr, Duration::from_millis(25)).is_ok()
    } else {
        false
    }
}

pub(crate) fn existing_backend_accepts_session(token: &str) -> bool {
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
    let challenge_path = format!(
        "/api/app/session-challenge?nonce={}",
        percent_encode_query_component(&nonce)
    );
    let transport_proof = tauri_ipc_bridge_proof(token, "GET", &challenge_path);
    let Ok(response) = agent
        .get(&format!("{BACKEND_ENDPOINT}{challenge_path}"))
        .set("Origin", "tauri://localhost")
        .set("X-VRCForge-Transport", "tauri-ipc-bridge")
        .set("X-VRCForge-Transport-Proof", &transport_proof)
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

pub(crate) fn wait_for_backend_session(token: &str, timeout: Duration) -> bool {
    let start = Instant::now();
    loop {
        if existing_backend_accepts_session(token) {
            return true;
        }
        if start.elapsed() >= timeout {
            return false;
        }
        thread::sleep(Duration::from_millis(150));
    }
}

pub(crate) fn extract_challenge_signature(payload: &serde_json::Value) -> Option<String> {
    payload
        .get("signature")
        .and_then(|value| value.as_str())
        .map(str::to_string)
}

pub(crate) fn wait_for_backend(timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if backend_port_open() {
            return true;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

pub(crate) fn send_backend_graceful_shutdown_request_to(endpoint: &str, token: &str) -> bool {
    let transport_proof = tauri_ipc_bridge_proof(
        token,
        BACKEND_GRACEFUL_SHUTDOWN_METHOD,
        BACKEND_GRACEFUL_SHUTDOWN_PATH,
    );
    let agent = ureq::builder()
        .timeout_connect(Duration::from_millis(350))
        .timeout(BACKEND_GRACEFUL_REQUEST_TIMEOUT)
        .redirects(0)
        .build();
    let response = agent
        .post(&format!(
            "{}{BACKEND_GRACEFUL_SHUTDOWN_PATH}",
            endpoint.trim_end_matches('/')
        ))
        .set("Accept", "application/json")
        .set("Origin", "tauri://localhost")
        .set("X-VRCForge-Transport", "tauri-ipc-bridge")
        .set("X-VRCForge-Transport-Proof", &transport_proof)
        .set("Authorization", &format!("Bearer {token}"))
        .call();
    match response {
        Ok(response) if (200..300).contains(&response.status()) => response.into_string().is_ok(),
        _ => false,
    }
}

pub(crate) fn wait_for_child_exit(child: &mut Child, timeout: Duration) -> bool {
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return true,
            Ok(None) => {}
            Err(_) => return false,
        }
        if started.elapsed() >= timeout {
            return false;
        }
        thread::sleep(Duration::from_millis(25));
    }
}

pub(crate) fn force_child_exit(child: &mut Child, timeout: Duration) -> bool {
    if child.kill().is_err() {
        return matches!(child.try_wait(), Ok(Some(_)));
    }
    wait_for_child_exit(child, timeout)
}

pub(crate) fn stop_managed_backend_child(state: &BackendState) {
    let Ok(mut guard) = state.child.lock() else {
        return;
    };
    let child = guard.take();
    drop(guard);
    let app_session_token = state
        .app_session_token
        .lock()
        .ok()
        .and_then(|mut token_guard| token_guard.take());
    #[cfg(windows)]
    let _backend_job = state
        .job
        .lock()
        .ok()
        .and_then(|mut job_guard| job_guard.take());
    clear_backend_session_verify_cache();
    let Some(mut child) = child else {
        return;
    };
    if matches!(child.try_wait(), Ok(Some(_))) {
        return;
    }
    let graceful_requested = app_session_token
        .as_deref()
        .is_some_and(|token| send_backend_graceful_shutdown_request_to(BACKEND_ENDPOINT, token));
    if graceful_requested && wait_for_child_exit(&mut child, BACKEND_GRACEFUL_EXIT_WAIT) {
        return;
    }
    let _ = force_child_exit(&mut child, BACKEND_FORCE_EXIT_WAIT);
}

pub(crate) fn shutdown_managed_backend(app: &tauri::AppHandle) {
    let state = app.state::<BackendState>();
    stop_managed_backend_child(&state);
}

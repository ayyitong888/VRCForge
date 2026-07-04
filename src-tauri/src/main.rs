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
    Manager, State,
};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8757;
const BACKEND_ENDPOINT: &str = "http://127.0.0.1:8757";
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

struct BackendState {
    child: Mutex<Option<Child>>,
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

#[tauri::command]
fn backend_endpoint() -> String {
    BACKEND_ENDPOINT.to_string()
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
    let response = app_api_request(AppApiRequest {
        method: Some("GET".to_string()),
        path: format!("/api/app/runtime/snapshot{suffix}"),
        body: None,
        timeout_ms: request.timeout_ms,
    })?;
    if response.ok {
        Ok(response.body)
    } else {
        Err(response
            .body
            .get("detail")
            .and_then(|value| value.as_str())
            .unwrap_or("Desktop runtime snapshot failed.")
            .to_string())
    }
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
    app_api_response_from_ureq(response)
}

#[tauri::command]
fn start_backend(state: State<'_, BackendState>) -> Result<BackendStartResult, String> {
    let user_data = user_data_dir()?;
    let app_session_token = ensure_app_session_token(&user_data)?;
    if backend_port_open() {
        if !existing_backend_accepts_session(&app_session_token) {
            return Err(
                "Port 8757 is already used by a VRCForge runtime that does not accept this desktop session. Close all VRCForge.exe processes in Task Manager and launch VRCForge again.".to_string()
            );
        }
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
    if normalized_method == "GET" && route == "/api/health" {
        return true;
    }
    if route == "/api/config" || route == "/api/config/vision" || route == "/api/models" {
        return matches!(normalized_method.as_str(), "GET" | "POST");
    }
    if route == "/api/projects/refresh" {
        return normalized_method == "POST";
    }
    if route == "/api/avatar-encryption/plan" || route == "/api/avatar-encryption/apply-request" {
        return matches!(normalized_method.as_str(), "POST" | "GET");
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
        "/api/app/ws-ticket",
    ];
    app_prefixes
        .iter()
        .any(|prefix| route == *prefix || route.starts_with(&format!("{prefix}/")))
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
            backend_endpoint,
            desktop_runtime_snapshot,
            start_backend,
            stop_backend,
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
        percent_encode_query_component, prepare_runtime_files, try_ensure_agent_notes_file,
        validate_local_folder_to_open, validate_project_folder_to_open,
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
        assert_eq!(
            normalize_app_api_path("GET", "/api/app/bootstrap?refreshProjects=true").as_deref(),
            Ok("/api/app/bootstrap?refreshProjects=true"),
        );
        assert_eq!(
            normalize_app_api_path("POST", "/api/avatar-encryption/plan").as_deref(),
            Ok("/api/avatar-encryption/plan"),
        );
        assert_eq!(
            normalize_app_api_path("POST", "/api/app/ws-ticket").as_deref(),
            Ok("/api/app/ws-ticket"),
        );
        assert_eq!(
            normalize_app_api_path("GET", "/api/health").as_deref(),
            Ok("/api/health")
        );

        assert!(normalize_app_api_path("GET", "http://127.0.0.1:8757/api/app/bootstrap").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/session").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/session-challenge").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/%73ession").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/%2e%2e/health").is_err());
        assert!(normalize_app_api_path("GET", "/api/agent/manifest").is_err());
        assert!(normalize_app_api_path("GET", "/mcp").is_err());
        assert!(normalize_app_api_path("GET", "/api/app/../health").is_err());
        assert!(normalize_app_api_path("POST", "/api/blendshapes/apply").is_err());
        assert!(normalize_app_api_path("POST", "/api/clothes/apply-fx").is_err());
        assert!(normalize_app_api_path("POST", "/api/shader/apply").is_err());
        assert!(normalize_app_api_path("POST", "/api/projects/install").is_err());
        assert!(normalize_app_api_path("POST", "/api/app/unlisted-future-route").is_err());
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

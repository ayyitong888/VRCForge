#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::{
    env, fs,
    io::{Read, Write},
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
    app_session_token: String,
    started: bool,
    already_running: bool,
    mode: String,
    message: String,
}

#[tauri::command]
fn backend_endpoint() -> String {
    BACKEND_ENDPOINT.to_string()
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
            app_session_token,
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
        app_session_token,
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
    let addrs = (BACKEND_HOST, BACKEND_PORT).to_socket_addrs();
    let Ok(mut addrs) = addrs else {
        return false;
    };
    let Some(addr) = addrs.next() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(350)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(900)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(900)));
    let request = format!(
        "GET /api/app/bootstrap HTTP/1.1\r\nHost: {BACKEND_HOST}:{BACKEND_PORT}\r\nOrigin: tauri://localhost\r\nAuthorization: Bearer {token}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = [0u8; 64];
    let Ok(size) = stream.read(&mut response) else {
        return false;
    };
    parse_http_status_ok(&response[..size])
}

fn parse_http_status_ok(response: &[u8]) -> bool {
    response.starts_with(b"HTTP/1.1 200 ") || response.starts_with(b"HTTP/1.0 200 ")
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
            backend_endpoint,
            start_backend,
            stop_backend,
            ensure_agent_notes_file
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
    use super::{parse_http_status_ok, prepare_runtime_files, try_ensure_agent_notes_file};
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
    fn existing_backend_probe_only_accepts_success_status() {
        assert!(parse_http_status_ok(b"HTTP/1.1 200 OK\r\n"));
        assert!(parse_http_status_ok(b"HTTP/1.0 200 OK\r\n"));
        assert!(!parse_http_status_ok(b"HTTP/1.1 401 Unauthorized\r\n"));
        assert!(!parse_http_status_ok(b"HTTP/1.1 404 Not Found\r\n"));
        assert!(!parse_http_status_ok(b"not http"));
    }
}

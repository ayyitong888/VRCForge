#![windows_subsystem = "windows"]
mod engine;
mod models;
mod platform;
mod payload;
mod window_outline;

use std::{path::Path, sync::{Arc, atomic::{AtomicBool, Ordering}}};
use serde::Serialize;
use tauri::Manager;

struct Runtime {
    user: Result<platform::UserContext, String>,
    uninstall: bool,
    busy: AtomicBool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SetupInfo {
    version: &'static str,
    uninstall: bool,
    install_dir: String,
    data_dir: String,
    installed_version: Option<String>,
    required_bytes: u64,
    available_bytes: u64,
    running: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct LocationStatus { valid: bool, available_bytes: u64, reason: String }

fn user(runtime: &Runtime) -> Result<&platform::UserContext, String> {
    runtime.user.as_ref().map_err(Clone::clone)
}

#[tauri::command]
fn load_state(runtime: tauri::State<'_, Arc<Runtime>>) -> Result<SetupInfo, String> {
    let user = user(&runtime)?;
    let installed = platform::existing_install()?;
    let install_dir = installed.as_ref().map(|old| old.install_dir.clone()).unwrap_or_else(platform::default_install_dir);
    if runtime.uninstall && installed.is_none() {
        return Err("VRCForge is not registered as installed. There is nothing to uninstall.".into());
    }
    let required_bytes = if runtime.uninstall { 0 } else { engine::inspect_payload()?.required_bytes };
    Ok(SetupInfo {
        version: env!("CARGO_PKG_VERSION"),
        uninstall: runtime.uninstall,
        install_dir: install_dir.to_string_lossy().into_owned(),
        data_dir: platform::data_dir(user)?.to_string_lossy().into_owned(),
        installed_version: installed.map(|old| old.version),
        required_bytes,
        available_bytes: platform::disk_free(&install_dir)?,
        running: !platform::running_processes(&install_dir)?.is_empty(),
    })
}

fn destination(path: &Path, runtime: &Runtime) -> Result<u64, String> {
    engine::validate_install_directory(user(runtime)?, path)?;
    platform::check_writable(&path)?;
    platform::disk_free(&path)
}

#[tauri::command]
fn check_install_path(path: String, runtime: tauri::State<'_, Arc<Runtime>>) -> LocationStatus {
    match destination(Path::new(&path), &runtime).and_then(|free| {
        let required = engine::inspect_payload()?.required_bytes;
        if free < required { return Err(format!("Not enough free space: {} bytes are required, {} are available.", required, free)); }
        Ok(free)
    }) {
        Ok(free) => LocationStatus { valid: true, available_bytes: free, reason: String::new() },
        Err(reason) => LocationStatus { valid: false, available_bytes: 0, reason },
    }
}

#[tauri::command]
async fn choose_install_dir() -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(|| platform::choose_folder().map(|folder| folder.map(|path| path.to_string_lossy().into_owned())))
        .await.map_err(|error| error.to_string())?
}

#[tauri::command]
fn is_app_running(path: String) -> Result<bool, String> {
    platform::running_processes(Path::new(&path)).map(|pids| !pids.is_empty())
}

#[tauri::command]
async fn close_running_app(path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || platform::close_running(Path::new(&path))).await.map_err(|error| error.to_string())?
}

#[tauri::command]
async fn begin_install(app: tauri::AppHandle, runtime: tauri::State<'_, Arc<Runtime>>, request: models::InstallRequest) -> Result<models::SetupResult, String> {
    let runtime = Arc::clone(&runtime);
    tauri::async_runtime::spawn_blocking(move || {
        if runtime.busy.swap(true, Ordering::SeqCst) { return Err("An installation operation is already running.".into()); }
        let result = user(&runtime).and_then(|user| engine::install(&app, user, &request));
        runtime.busy.store(false, Ordering::SeqCst);
        result
    }).await.map_err(|error| error.to_string())?
}

#[tauri::command]
async fn begin_uninstall(app: tauri::AppHandle, runtime: tauri::State<'_, Arc<Runtime>>, request: models::UninstallRequest) -> Result<models::SetupResult, String> {
    let runtime = Arc::clone(&runtime);
    tauri::async_runtime::spawn_blocking(move || {
        if runtime.busy.swap(true, Ordering::SeqCst) { return Err("An installation operation is already running.".into()); }
        let result = user(&runtime).and_then(|user| engine::uninstall(&app, user, &request));
        runtime.busy.store(false, Ordering::SeqCst);
        result
    }).await.map_err(|error| error.to_string())?
}

#[tauri::command]
fn launch_installed_app(runtime: tauri::State<'_, Arc<Runtime>>) -> Result<(), String> {
    let installed = platform::existing_install()?.ok_or("The installed VRCForge application could not be found.")?;
    platform::launch_app(user(&runtime)?, &installed.install_dir)
}

#[tauri::command]
fn read_setup_log(runtime: tauri::State<'_, Arc<Runtime>>) -> Result<String, String> {
    std::fs::read_to_string(engine::setup_log_path(user(&runtime)?)).map_err(|error| format!("Could not read the setup log: {error}"))
}

// The installed uninstaller moves itself to a private temporary directory before
// removing its installation. The child inherits elevation; no service or shell
// command is created. Windows removes this one temporary executable on reboot.
fn relocate_uninstaller(uninstall: bool) -> Result<bool, String> {
    use std::os::windows::{ffi::OsStrExt, process::CommandExt};
    use windows::Win32::{Storage::FileSystem::{MoveFileExW, MOVEFILE_DELAY_UNTIL_REBOOT}, System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE}, Foundation::CloseHandle};
    let args: Vec<String> = std::env::args().collect();
    if let Some(at) = args.iter().position(|arg| arg == "--wait-for-parent") {
        let pid = args.get(at + 1).ok_or("Missing uninstall parent process.")?.parse::<u32>().map_err(|error| error.to_string())?;
        unsafe {
            if let Ok(handle) = OpenProcess(PROCESS_SYNCHRONIZE, false, pid) {
                WaitForSingleObject(handle, 30_000);
                let _ = CloseHandle(handle);
            }
        }
        let exe = std::env::current_exe().map_err(|error| error.to_string())?;
        let encoded: Vec<u16> = exe.as_os_str().encode_wide().chain(Some(0)).collect();
        unsafe {
            let _ = MoveFileExW(windows::core::PCWSTR(encoded.as_ptr()), None, MOVEFILE_DELAY_UNTIL_REBOOT);
            if let Some(directory) = exe.parent() {
                let parent: Vec<u16> = directory.as_os_str().encode_wide().chain(Some(0)).collect();
                let _ = MoveFileExW(windows::core::PCWSTR(parent.as_ptr()), None, MOVEFILE_DELAY_UNTIL_REBOOT);
            }
        }
        return Ok(false);
    }
    if !uninstall { return Ok(false); }
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let Some(installed) = platform::existing_install()? else { return Ok(false); };
    if !executable.parent().is_some_and(|parent| parent.as_os_str().to_string_lossy().eq_ignore_ascii_case(&installed.install_dir.as_os_str().to_string_lossy())) { return Ok(false); }
    let temp = tempfile::Builder::new().prefix("VRCForge-uninstall-").tempdir().map_err(|error| error.to_string())?;
    let executable_copy = temp.path().join("Uninstall.exe");
    std::fs::copy(&executable, &executable_copy).map_err(|error| format!("Could not prepare the uninstaller: {error}"))?;
    std::process::Command::new(&executable_copy).args(["--uninstall", "--wait-for-parent", &std::process::id().to_string()])
        .current_dir(temp.path()).creation_flags(0x08000000).spawn().map_err(|error| format!("Could not start the uninstaller: {error}"))?;
    let _retained = temp.keep();
    Ok(true)
}

fn main() {
    let uninstall = std::env::args().any(|arg| arg == "--uninstall") || std::env::current_exe().ok().and_then(|path| path.file_stem().map(|name| name.to_string_lossy().starts_with("Uninstall"))).unwrap_or(false);
    match relocate_uninstaller(uninstall) {
        Ok(true) => return,
        Err(error) => { eprintln!("{error}"); return; }
        Ok(false) => {}
    }
    match platform::require_webview() {
        Ok(true) => {},
        Ok(false) => return,
        Err(error) => { eprintln!("{error}"); return; }
    }
    let runtime = Arc::new(Runtime { user: platform::resolve_user(), uninstall, busy: AtomicBool::new(false) });
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::Builder::new().open_js_links_on_click(false).build())
        .manage(runtime)
        .invoke_handler(tauri::generate_handler![load_state, check_install_path, choose_install_dir, is_app_running, close_running_app, begin_install, begin_uninstall, launch_installed_app, read_setup_log])
        .on_window_event(|window,event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.app_handle().state::<Arc<Runtime>>().busy.load(Ordering::SeqCst) { api.prevent_close(); }
            }
            if matches!(event,tauri::WindowEvent::Resized(size) if size.width>0 && size.height>0) || matches!(event,tauri::WindowEvent::ScaleFactorChanged{..}) {
                if let Err(error)=window_outline::apply(window){eprintln!("Could not update the window outline: {error}");}
            }
        })
        .setup(|app| {
            if let Some(window)=app.get_webview_window("setup") { window_outline::apply(&window.as_ref().window())?; }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("VRCForge setup could not start");
}

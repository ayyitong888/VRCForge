use serde::Deserialize;
use serde_json::Value;
use std::path::{Path, PathBuf};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ChatFileRequest {
    path: String,
    chat_id: String,
    project_path: String,
    session_id: String,
    client_turn_id: String,
}

// User-clicked presentation only: authenticated receipt and canonical project
// containment precede dispatch. No Unity writes or shell command text. The short
// lived Explorer dispatcher is owned here; the document viewer is user-owned.
#[tauri::command]
pub(crate) async fn open_chat_file(request: ChatFileRequest) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let authorized = crate::backend::backend_json_request("POST", "/api/app/agent/resource-file".into(),
            Some(serde_json::json!({"path":request.path,"chatId":request.chat_id,"projectPath":request.project_path,
                "sessionId":request.session_id,"clientTurnId":request.client_turn_id})), Some(10_000))?;
        let path = PathBuf::from(authorized.get("path").and_then(Value::as_str).ok_or("File authorization is unavailable.")?);
        if !path.is_absolute() || !path.is_file() { return Err("The file is no longer available.".into()); }
        open_validated_chat_file(&path)
    }).await.map_err(|error| format!("Unable to open the file: {error}"))?
}

fn is_passive_document(path: &Path) -> bool {
    matches!(path.extension().and_then(|value| value.to_str()).unwrap_or("").to_ascii_lowercase().as_str(),
        "txt" | "md" | "json" | "log" | "csv" | "pdf" | "png" | "jpg" | "jpeg" | "gif" | "webp" | "bmp")
}

fn open_validated_chat_file(path: &Path) -> Result<(), String> {
    #[cfg(windows)]
    {
        if is_passive_document(path) {
            use windows_sys::Win32::UI::Shell::ShellExecuteW;
            let operation: Vec<u16> = "open\0".encode_utf16().collect();
            let target: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
            use std::os::windows::ffi::OsStrExt;
            let status = unsafe { ShellExecuteW(std::ptr::null_mut(), operation.as_ptr(), target.as_ptr(), std::ptr::null(), std::ptr::null(), 1) };
            if status as isize <= 32 { return Err("Unable to open the file viewer.".into()); }
        } else {
            use std::os::windows::process::CommandExt;
            let mut child = std::process::Command::new("explorer.exe").arg(format!("/select,{}", path.display()))
                .creation_flags(0x08000000).spawn().map_err(|_| "Unable to show the file in Explorer.")?;
            std::thread::spawn(move || { let _ = child.wait(); });
        }
        Ok(())
    }
    #[cfg(not(windows))]
    { let _ = path; Err("File opening requires the Windows desktop app.".into()) }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn executable_and_active_documents_are_revealed_only() {
        for name in ["run.exe", "run.cmd", "run.ps1", "file.lnk", "page.html", "image.svg", "macro.docm", "scene.unity"] {
            assert!(!is_passive_document(Path::new(name)), "{name}");
        }
        assert!(is_passive_document(Path::new("picture.PNG")));
    }
}
